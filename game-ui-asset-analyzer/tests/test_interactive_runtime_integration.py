from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from interactive_file_adapter import InteractiveFileAdapter  # noqa: E402
from recursive_runtime import (  # noqa: E402
    RecursiveRuntime,
    RuntimeAdapters,
    RuntimeConfig,
)
from test_recursive_runtime import instances, route, semantic_stop  # noqa: E402


def interactive_adapters(run_dir: Path) -> RuntimeAdapters:
    return RuntimeAdapters(
        router=InteractiveFileAdapter(run_dir, "router"),
        structural_split=InteractiveFileAdapter(run_dir, "structural_split"),
        expand_instances=InteractiveFileAdapter(run_dir, "expand_instances"),
        semantic_decompose=InteractiveFileAdapter(run_dir, "semantic_decompose"),
    )


class InteractiveRuntimeIntegrationTests(unittest.TestCase):
    def test_request_response_resume_preserves_queue_crop_and_deferred_policy(self):
        with tempfile.TemporaryDirectory() as context:
            base = Path(context)
            source = base / "source.png"
            run_dir = base / "run"
            Image.new("RGB", (400, 200), "navy").save(source)
            runtime = RecursiveRuntime.create(
                run_dir=run_dir,
                root_node_crop=source,
                adapters=interactive_adapters(run_dir),
                config=RuntimeConfig(
                    repeated_instance_semantic_limit=2,
                    validation_mode="real_image",
                ),
            )

            result = runtime.run()
            request_ids: list[str] = []
            while result == "waiting_for_adapter":
                pending = runtime.state.pending_adapter_request
                self.assertIsNotNone(pending)
                request_ids.append(pending["request_id"])
                if pending["adapter_kind"] == "router":
                    adapter_result = route("repeated_group")
                elif pending["adapter_kind"] == "expand_instances":
                    adapter_result = instances(3)
                elif pending["adapter_kind"] == "semantic_decompose":
                    adapter_result = semantic_stop(pending["node_id"], height=1024)
                else:
                    self.fail(f"unexpected adapter kind: {pending['adapter_kind']}")
                response_path = run_dir / pending["response_path"]
                response_path.parent.mkdir(parents=True, exist_ok=True)
                response_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "0.1",
                            "request_id": pending["request_id"],
                            "adapter_kind": pending["adapter_kind"],
                            "result": adapter_result,
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                runtime = RecursiveRuntime.load(
                    run_dir=run_dir,
                    adapters=interactive_adapters(run_dir),
                )
                result = runtime.run()

            self.assertEqual("complete_with_deferred", result)
            self.assertEqual(
                ["req_000001", "req_000002", "req_000003", "req_000004"],
                request_ids,
            )
            self.assertEqual("done", runtime.store.get("root.instance_001").status)
            self.assertEqual("done", runtime.store.get("root.instance_002").status)
            deferred = runtime.store.get("root.instance_003")
            self.assertEqual("deferred", deferred.status)
            self.assertEqual(
                "repeated_instance_semantic_limit", deferred.deferred_reason
            )
            self.assertTrue(runtime.state.real_visual_inference_used)
            self.assertEqual([], runtime.state.current_level_queue)
            self.assertEqual([], runtime.state.next_level_queue)


if __name__ == "__main__":
    unittest.main()

