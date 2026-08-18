from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from fake_runtime_adapters import (  # noqa: E402
    FixtureExpandInstancesAdapter,
    FixtureRouterAdapter,
    FixtureSemanticDecomposeAdapter,
    FixtureStructuralSplitAdapter,
)
from interactive_file_adapter import (  # noqa: E402
    InteractiveFileAdapter,
    load_response_schema,
)
from recursive_runtime import (  # noqa: E402
    RecursiveRuntime,
    RuntimeAdapters,
    RuntimeConfig,
)
from test_recursive_runtime import route, semantic_decompose  # noqa: E402


class InteractiveFileAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory()
        self.addCleanup(self.context.cleanup)
        self.base = Path(self.context.name)
        self.source = self.base / "source.png"
        Image.new("RGB", (400, 200), "navy").save(self.source)

    def interactive_adapters(self, run_dir: Path) -> RuntimeAdapters:
        return RuntimeAdapters(
            router=InteractiveFileAdapter(run_dir, "router"),
            structural_split=InteractiveFileAdapter(run_dir, "structural_split"),
            expand_instances=InteractiveFileAdapter(run_dir, "expand_instances"),
            semantic_decompose=InteractiveFileAdapter(run_dir, "semantic_decompose"),
        )

    def fixture_adapters(
        self,
        *,
        adapter_type: str,
        routes: dict | None = None,
        semantics: dict | None = None,
    ) -> RuntimeAdapters:
        return RuntimeAdapters(
            router=FixtureRouterAdapter(routes or {}, adapter_type=adapter_type),
            structural_split=FixtureStructuralSplitAdapter(
                {}, adapter_type=adapter_type
            ),
            expand_instances=FixtureExpandInstancesAdapter(
                {}, adapter_type=adapter_type
            ),
            semantic_decompose=FixtureSemanticDecomposeAdapter(
                semantics or {}, adapter_type=adapter_type
            ),
        )

    def create_interactive(self, name: str = "interactive") -> RecursiveRuntime:
        run_dir = self.base / name
        return RecursiveRuntime.create(
            run_dir=run_dir,
            root_node_crop=self.source,
            adapters=self.interactive_adapters(run_dir),
            config=RuntimeConfig(validation_mode="real_image"),
        )

    def write_response(
        self,
        runtime: RecursiveRuntime,
        result: dict,
        *,
        request_id: str | None = None,
        adapter_kind: str | None = None,
    ) -> Path:
        pending = runtime.state.pending_adapter_request
        assert pending is not None
        response_path = runtime.run_dir / pending["response_path"]
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "request_id": request_id or pending["request_id"],
                    "adapter_kind": adapter_kind or pending["adapter_kind"],
                    "result": result,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return response_path

    def resume(self, runtime: RecursiveRuntime) -> RecursiveRuntime:
        return RecursiveRuntime.load(
            run_dir=runtime.run_dir,
            adapters=self.interactive_adapters(runtime.run_dir),
        )

    def test_t01_mechanics_fake_adapter_passes(self):
        run_dir = self.base / "mechanics"
        runtime = RecursiveRuntime.create(
            run_dir=run_dir,
            root_node_crop=self.source,
            adapters=self.fixture_adapters(
                adapter_type="fake", routes={"root": route("asset")}
            ),
            config=RuntimeConfig(validation_mode="mechanics"),
        )
        self.assertEqual("complete", runtime.run())

    def test_t02_real_image_rejects_fake_adapters(self):
        with self.assertRaisesRegex(ValueError, "real_image validation rejects"):
            RecursiveRuntime.create(
                run_dir=self.base / "fake-real",
                root_node_crop=self.source,
                adapters=self.fixture_adapters(adapter_type="fake"),
                config=RuntimeConfig(validation_mode="real_image"),
            )

    def test_t03_real_image_rejects_fixture_adapters(self):
        with self.assertRaisesRegex(ValueError, "real_image validation rejects"):
            RecursiveRuntime.create(
                run_dir=self.base / "fixture-real",
                root_node_crop=self.source,
                adapters=self.fixture_adapters(adapter_type="fixture"),
                config=RuntimeConfig(validation_mode="real_image"),
            )

    def test_t04_interactive_first_call_writes_request_and_waits(self):
        runtime = self.create_interactive()
        self.assertEqual("waiting_for_adapter", runtime.run())
        pending = runtime.state.pending_adapter_request
        self.assertEqual("req_000001", pending["request_id"])
        self.assertTrue((runtime.run_dir / pending["request_path"]).is_file())

    def test_t05_waiting_preserves_node_queue_tree_and_nonfailure_status(self):
        runtime = self.create_interactive()
        self.assertEqual("waiting_for_adapter", runtime.run())
        root = runtime.store.get("root")
        self.assertNotIn(root.status, {"failed", "blocked", "done"})
        self.assertEqual(["root"], runtime.state.current_level_queue)
        self.assertEqual([], runtime.state.next_level_queue)
        self.assertTrue((runtime.run_dir / "tree.json").is_file())
        self.assertTrue((runtime.run_dir / "runtime-state.json").is_file())

    def test_t06_resume_without_response_keeps_request_id_and_file(self):
        runtime = self.create_interactive()
        self.assertEqual("waiting_for_adapter", runtime.run())
        pending = copy.deepcopy(runtime.state.pending_adapter_request)
        request_path = runtime.run_dir / pending["request_path"]
        original = request_path.read_bytes()
        resumed = self.resume(runtime)
        self.assertEqual("waiting_for_adapter", resumed.run())
        self.assertEqual(pending, resumed.state.pending_adapter_request)
        self.assertEqual(original, request_path.read_bytes())

    def test_t07_valid_response_is_consumed_and_runtime_continues(self):
        runtime = self.create_interactive()
        self.assertEqual("waiting_for_adapter", runtime.run())
        self.write_response(runtime, route("asset"))
        resumed = self.resume(runtime)
        self.assertEqual("complete", resumed.run())
        self.assertIsNone(resumed.state.pending_adapter_request)
        request = json.loads(
            (resumed.run_dir / "adapter-requests" / "req_000001.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("consumed", request["status"])

    def test_t08_response_request_id_mismatch_is_validation_failure(self):
        runtime = self.create_interactive()
        runtime.run()
        self.write_response(runtime, route("asset"), request_id="req_999999")
        resumed = self.resume(runtime)
        self.assertEqual("failed", resumed.run())
        self.assertIn("adapter_response_invalid", resumed.store.get("root").error)

    def test_t09_response_adapter_kind_mismatch_is_validation_failure(self):
        runtime = self.create_interactive()
        runtime.run()
        self.write_response(runtime, route("asset"), adapter_kind="expand_instances")
        resumed = self.resume(runtime)
        self.assertEqual("failed", resumed.run())
        self.assertIn("adapter_kind mismatch", resumed.store.get("root").error)

    def test_t10_response_result_must_pass_frozen_validator(self):
        runtime = self.create_interactive()
        runtime.run()
        self.write_response(runtime, {})
        resumed = self.resume(runtime)
        self.assertEqual("failed", resumed.run())
        self.assertIn("invalid Router adapter result", resumed.store.get("root").error)

    def test_t11_reload_preserves_final_asset_over_expand_provenance(self):
        run_dir = self.base / "reload-asset"
        runtime = RecursiveRuntime.create(
            run_dir=run_dir,
            root_node_crop=self.source,
            adapters=self.fixture_adapters(
                adapter_type="fixture", routes={"root": route("asset")}
            ),
        )
        root = runtime.store.get("root")
        root.produced_by = "expand_instances"
        root.node_role = "asset"
        root.terminal = True
        root.next_action = "stop"
        root.requires_router = False
        runtime.store.update(root)
        runtime._persist()
        loaded = RecursiveRuntime.load(
            run_dir=run_dir,
            adapters=self.fixture_adapters(adapter_type="fixture"),
            config=RuntimeConfig(),
        )
        loaded._deterministic_resolve(loaded.store.get("root"))
        self.assertEqual(
            ("asset", True, "stop"),
            (
                loaded.store.get("root").node_role,
                loaded.store.get("root").terminal,
                loaded.store.get("root").next_action,
            ),
        )

    def test_t12_unresolved_expand_node_uses_provenance(self):
        runtime = RecursiveRuntime.create(
            run_dir=self.base / "provenance",
            root_node_crop=self.source,
            adapters=self.fixture_adapters(adapter_type="fixture"),
        )
        root = runtime.store.get("root")
        root.produced_by = "expand_instances"
        root.requires_router = False
        runtime._deterministic_resolve(root)
        self.assertEqual("component_instance", root.node_role)
        self.assertEqual("semantic_decompose", root.next_action)

    def test_t13_conflicting_current_state_fails_validation(self):
        runtime = RecursiveRuntime.create(
            run_dir=self.base / "conflict",
            root_node_crop=self.source,
            adapters=self.fixture_adapters(adapter_type="fixture"),
        )
        root = runtime.store.get("root")
        root.node_role = "asset"
        root.terminal = False
        root.next_action = "structural_split"
        root.requires_router = False
        runtime.store.update(root)
        self.assertEqual("failed", runtime.run())

    def test_t14_semantic_warning_does_not_modify_tree(self):
        runtime = RecursiveRuntime.create(
            run_dir=self.base / "warning-tree",
            root_node_crop=self.source,
            adapters=self.fixture_adapters(adapter_type="fixture"),
        )
        before = runtime.store.snapshot()
        runtime.add_semantic_warning(
            node_id="root",
            source="manual_review",
            warning_type="visual_disagreement",
            message="Review requested",
        )
        self.assertEqual(before, runtime.store.snapshot())

    def test_t15_semantic_warning_does_not_call_adapter(self):
        adapters = self.fixture_adapters(
            adapter_type="fixture", routes={"root": route("asset")}
        )
        runtime = RecursiveRuntime.create(
            run_dir=self.base / "warning-retry",
            root_node_crop=self.source,
            adapters=adapters,
        )
        runtime.add_semantic_warning(
            node_id="root",
            source="manual_review",
            warning_type="visual_disagreement",
            message="Review requested",
        )
        self.assertEqual([], adapters.router.calls)

    def test_t16_manifest_records_validation_mode_adapter_types_and_visual_flag(self):
        run_dir = self.base / "manifest"
        runtime = RecursiveRuntime.create(
            run_dir=run_dir,
            root_node_crop=self.source,
            adapters=self.fixture_adapters(
                adapter_type="fake", routes={"root": route("asset")}
            ),
            config=RuntimeConfig(validation_mode="mechanics"),
        )
        runtime.run()
        manifest = json.loads(runtime.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("mechanics", manifest["validation_mode"])
        self.assertEqual("fake", manifest["adapter_types"]["router"])
        self.assertFalse(manifest["real_visual_inference_used"])

    def test_t17_mechanics_fake_run_records_no_real_visual_inference(self):
        run_dir = self.base / "fake-flag"
        runtime = RecursiveRuntime.create(
            run_dir=run_dir,
            root_node_crop=self.source,
            adapters=self.fixture_adapters(
                adapter_type="fake", routes={"root": route("asset")}
            ),
        )
        runtime.run()
        self.assertFalse(runtime.state.real_visual_inference_used)

    def test_t18_consumed_interactive_response_records_real_visual_inference(self):
        runtime = self.create_interactive("interactive-flag")
        runtime.run()
        self.write_response(runtime, route("asset"))
        resumed = self.resume(runtime)
        self.assertEqual("complete", resumed.run())
        manifest = json.loads(resumed.manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(manifest["real_visual_inference_used"])
        self.assertEqual("interactive_visual", manifest["adapter_types"]["router"])

    def test_t19_waiting_is_not_reported_as_terminal_run_result(self):
        runtime = self.create_interactive("waiting-result")
        self.assertEqual("waiting_for_adapter", runtime.run())
        manifest = json.loads(runtime.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("waiting_for_adapter", manifest["result"])
        self.assertFalse(manifest["active_execution_complete"])
        self.assertNotIn(manifest["result"], {"complete", "blocked", "failed"})

    def test_t20_terminal_semantic_asset_has_no_new_image_artifacts(self):
        run_dir = self.base / "terminal-asset"
        adapters = self.fixture_adapters(
            adapter_type="fixture",
            semantics={"root": semantic_decompose("root", height=512)},
        )
        runtime = RecursiveRuntime.create(
            run_dir=run_dir,
            root_node_crop=self.source,
            adapters=adapters,
        )
        root = runtime.store.get("root")
        root.node_role = "component_instance"
        root.next_action = "semantic_decompose"
        root.requires_router = False
        runtime.store.update(root)
        self.assertEqual("complete", runtime.run())
        asset = runtime.store.get("root.asset_001")
        asset_dir = runtime.store.node_directory(asset.node_id)
        self.assertFalse((asset_dir / "node-crop.png").exists())
        self.assertFalse((asset_dir / "analysis-image.png").exists())

    def test_response_schema_is_valid(self):
        self.assertEqual(
            "https://json-schema.org/draft/2020-12/schema",
            load_response_schema()["$schema"],
        )

    def test_cli_starts_waits_and_resumes_without_source_edits(self):
        run_dir = self.base / "cli"
        first = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "run_recursive_runtime.py"),
                "--run-dir",
                str(run_dir),
                "--root-node-crop",
                str(self.source),
                "--validation-mode",
                "real_image",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertIn("WAITING_FOR_ADAPTER", first.stdout)
        state = json.loads(
            (run_dir / "runtime-state.json").read_text(encoding="utf-8")
        )
        pending = state["pending_adapter_request"]
        response_path = run_dir / pending["response_path"]
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "request_id": pending["request_id"],
                    "adapter_kind": pending["adapter_kind"],
                    "result": route("asset"),
                }
            ),
            encoding="utf-8",
        )
        resumed = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "run_recursive_runtime.py"),
                "--run-dir",
                str(run_dir),
                "--resume",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, resumed.returncode, resumed.stderr)
        self.assertIn("RUN_RESULT=complete", resumed.stdout)


if __name__ == "__main__":
    unittest.main()
