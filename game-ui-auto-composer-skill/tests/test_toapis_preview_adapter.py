from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import toapis_preview_adapter as adapter


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_REQUEST = ROOT / "references" / "examples" / "example-preview-request.json"


class FakeResponse:
    def __init__(self, data=None, *, status_code=200, chunks=None, json_error=None):
        self.data = data
        self.status_code = status_code
        self.chunks = chunks if chunks is not None else [b"image-bytes"]
        self.json_error = json_error

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.data

    def iter_content(self, chunk_size):
        del chunk_size
        yield from self.chunks


class FakeSession:
    def __init__(self, *, posts=None, gets=None):
        self.posts = list(posts or [])
        self.gets = list(gets or [])
        self.post_calls = []
        self.get_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if not self.posts:
            raise AssertionError("Unexpected POST")
        response = self.posts.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if not self.gets:
            raise AssertionError("Unexpected GET")
        response = self.gets.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def example_request():
    return json.loads(EXAMPLE_REQUEST.read_text(encoding="utf-8"))


def args_for(request_path, output_path, result_path, **overrides):
    values = {
        "request": str(request_path),
        "asset_root": None,
        "output": str(output_path),
        "result_json": str(result_path),
        "poll_interval": 3.0,
        "max_wait": 300.0,
        "upload_timeout": 120.0,
        "request_timeout": 120.0,
        "download_timeout": 120.0,
        "dry_run": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class PreviewRequestTests(unittest.TestCase):
    def test_loads_normal_request(self):
        data = adapter.load_preview_request(EXAMPLE_REQUEST)
        references = adapter.validate_preview_request(data)
        self.assertEqual("login", data["source"]["page_id"])
        self.assertEqual([1, 2], [item["order"] for item in references])

    def test_missing_request_file(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(adapter.ToApisAdapterError) as caught:
                adapter.load_preview_request(Path(raw) / "missing.json")
        self.assertEqual("REQUEST_FILE_NOT_FOUND", caught.exception.error_code)
        self.assertEqual(adapter.EXIT_INPUT, caught.exception.exit_code)

    def test_invalid_json(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "bad.json"
            path.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(adapter.ToApisAdapterError) as caught:
                adapter.load_preview_request(path)
        self.assertEqual("REQUEST_JSON_INVALID", caught.exception.error_code)

    def test_missing_prompt_fields(self):
        data = example_request()
        data["composition_requirements"] = []
        with self.assertRaises(adapter.ToApisAdapterError) as caught:
            adapter.validate_preview_request(data)
        self.assertEqual("PURPOSE_MISSING", caught.exception.error_code)

    def test_reference_order_is_sorted_and_must_be_consecutive(self):
        data = example_request()
        data["reference_assets"].reverse()
        references = adapter.validate_preview_request(data)
        self.assertEqual([1, 2], [item["order"] for item in references])
        data["reference_assets"][0]["order"] = 5
        with self.assertRaises(adapter.ToApisAdapterError) as caught:
            adapter.validate_preview_request(data)
        self.assertEqual("REFERENCE_ORDER_INVALID", caught.exception.error_code)


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.data = example_request()
        self.references = adapter.validate_preview_request(self.data)
        self.prompt = adapter.build_image_prompt(self.data, self.references)

    def test_prompt_is_english_semistructured(self):
        for heading in ("Project:", "Page:", "Purpose:", "Reference assets:", "Layout:", "Constraints:"):
            self.assertIn(heading, self.prompt)
        self.assertIn("Sky Vanguard", self.prompt)
        self.assertIn("Login", self.prompt)
        self.assertNotIn("为游戏", self.prompt)

    def test_prompt_is_not_serialized_json(self):
        self.assertNotIn('"schema_version"', self.prompt)
        self.assertNotIn('"reference_assets"', self.prompt)
        self.assertLess(len(self.prompt), len(json.dumps(self.data, ensure_ascii=False)))

    def test_prompt_numbers_match_sorted_references(self):
        first = self.prompt.index("1. login_bg_castle")
        second = self.prompt.index("2. login_button_yellow")
        self.assertLess(first, second)

    def test_prompt_has_roles_preservation_and_layout(self):
        self.assertIn("Role: Full-page background", self.prompt)
        self.assertIn("Role: Primary login button", self.prompt)
        self.assertIn("upper-center castle silhouette", self.prompt)
        self.assertIn("irregular decorative outer edges", self.prompt)
        self.assertIn("Background fills the entire canvas.", self.prompt)
        self.assertIn("bottom center", self.prompt)
        self.assertIn("58%", self.prompt)

    def test_prompt_removes_invisible_behavior_and_forbids_extra_ui(self):
        self.assertNotIn("begin_loading_flow", self.prompt)
        self.assertNotIn("navigation_id", self.prompt)
        self.assertIn("Do not add account forms.", self.prompt)
        self.assertIn("Do not add social login buttons.", self.prompt)
        self.assertIn("Do not add menus.", self.prompt)
        self.assertIn("Do not add extra UI panels.", self.prompt)
        self.assertIn("concept preview, not an engineering screenshot", self.prompt)


class SourceResolutionTests(unittest.TestCase):
    def test_public_url_is_not_uploaded(self):
        references = [
            {
                "order": 1,
                "asset_id": "remote",
                "source_ref": {"ref_type": "opaque_id", "value": "https://cdn.example/a.png"},
            }
        ]
        session = FakeSession()
        result = adapter.resolve_reference_assets(
            references,
            asset_root=None,
            base_url=adapter.DEFAULT_BASE_URL,
            api_key="secret",
            upload_timeout=10,
            dry_run=False,
            session=session,
        )
        self.assertEqual("public_url", result[0]["source_kind"])
        self.assertEqual("https://cdn.example/a.png", result[0]["resolved_url"])
        self.assertEqual([], session.post_calls)

    def test_relative_local_path_uses_asset_root(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = root / "images" / "a.png"
            image.parent.mkdir()
            image.write_bytes(b"x")
            resolved = adapter.resolve_local_path("images/a.png", root)
            self.assertEqual(image.resolve(), resolved)

    def test_relative_local_path_requires_asset_root(self):
        with self.assertRaises(adapter.ToApisAdapterError) as caught:
            adapter.resolve_local_path("images/a.png", None)
        self.assertEqual("ASSET_ROOT_REQUIRED", caught.exception.error_code)

    def test_missing_local_file_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(adapter.ToApisAdapterError) as caught:
                adapter.resolve_local_path("missing.png", Path(raw))
        self.assertEqual("LOCAL_ASSET_NOT_FOUND", caught.exception.error_code)

    def test_unsupported_reference_type_fails_unless_value_is_url(self):
        references = [
            {"order": 1, "asset_id": "x", "source_ref": {"ref_type": "attachment_id", "value": "att_1"}}
        ]
        with self.assertRaises(adapter.ToApisAdapterError) as caught:
            adapter.resolve_reference_assets(
                references,
                asset_root=None,
                base_url=adapter.DEFAULT_BASE_URL,
                api_key="secret",
                upload_timeout=10,
                dry_run=False,
                session=FakeSession(),
            )
        self.assertEqual("UNSUPPORTED_SOURCE_REF", caught.exception.error_code)

    def test_upload_uses_confirmed_multipart_contract(self):
        with tempfile.TemporaryDirectory() as raw:
            image = Path(raw) / "a.png"
            image.write_bytes(b"image")
            session = FakeSession(posts=[FakeResponse({"url": "https://transfer.example/a.png"})])
            url = adapter.upload_image(
                image,
                base_url=adapter.DEFAULT_BASE_URL,
                api_key="secret",
                timeout=120,
                session=session,
            )
        self.assertEqual("https://transfer.example/a.png", url)
        request_url, kwargs = session.post_calls[0]
        self.assertEqual("https://ai-api.youchu.work/api/upload", request_url)
        self.assertEqual({"Authorization": "Bearer secret"}, kwargs["headers"])
        self.assertNotIn("Content-Type", kwargs["headers"])
        self.assertEqual("a.png", kwargs["files"]["file"][0])
        self.assertEqual(120, kwargs["timeout"])

    def test_absolute_upload_url_is_not_prefixed(self):
        self.assertEqual(
            "https://transfer-hk.youchu.xyz/d/a.jpg",
            adapter.provider_url(adapter.DEFAULT_BASE_URL, "https://transfer-hk.youchu.xyz/d/a.jpg"),
        )

    def test_upload_response_without_url_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            image = Path(raw) / "a.png"
            image.write_bytes(b"image")
            session = FakeSession(posts=[FakeResponse({"success": True})])
            with self.assertRaises(adapter.ToApisAdapterError) as caught:
                adapter.upload_image(
                    image,
                    base_url=adapter.DEFAULT_BASE_URL,
                    api_key="secret",
                    timeout=120,
                    session=session,
                )
        self.assertEqual("UPLOAD_URL_MISSING", caught.exception.error_code)
        self.assertEqual(adapter.EXIT_UPLOAD, caught.exception.exit_code)

    def test_duplicate_source_ref_uploads_once_but_keeps_two_images(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = root / "a.png"
            image.write_bytes(b"image")
            source_ref = {"ref_type": "workspace_path", "value": "a.png"}
            refs = [
                {"order": 1, "asset_id": "a", "source_ref": dict(source_ref)},
                {"order": 2, "asset_id": "b", "source_ref": dict(source_ref)},
            ]
            session = FakeSession(posts=[FakeResponse({"url": "https://transfer.example/a.png"})])
            result = adapter.resolve_reference_assets(
                refs,
                asset_root=root,
                base_url=adapter.DEFAULT_BASE_URL,
                api_key="secret",
                upload_timeout=120,
                dry_run=False,
                session=session,
            )
        self.assertEqual(1, len(session.post_calls))
        self.assertEqual(2, len(result))
        self.assertEqual(result[0]["resolved_url"], result[1]["resolved_url"])


class ProviderProtocolTests(unittest.TestCase):
    def test_prompt_numbers_and_images_share_reference_order(self):
        data = example_request()
        data["reference_assets"].reverse()
        for index, item in enumerate(data["reference_assets"], 1):
            item["source_ref"] = {
                "ref_type": "asset_uri",
                "value": f"https://cdn.example/by-array-position-{index}.png",
            }
        references = adapter.validate_preview_request(data)
        prompt = adapter.build_image_prompt(data, references)
        resolved = adapter.resolve_reference_assets(
            references,
            asset_root=None,
            base_url=adapter.DEFAULT_BASE_URL,
            api_key="secret",
            upload_timeout=10,
            dry_run=False,
            session=FakeSession(),
        )
        payload = adapter.build_generation_payload(prompt, resolved)
        self.assertIn("1. login_bg_castle", prompt)
        self.assertIn("2. login_button_yellow", prompt)
        self.assertEqual(
            [
                "https://cdn.example/by-array-position-2.png",
                "https://cdn.example/by-array-position-1.png",
            ],
            payload["images"],
        )

    def test_generation_payload_matches_confirmed_protocol(self):
        payload = adapter.build_generation_payload(
            "Prompt", [{"resolved_url": "https://a"}, {"resolved_url": "https://b"}]
        )
        self.assertEqual(
            {
                "model": "gpt-image-2",
                "prompt": "Prompt",
                "type": "image",
                "images": ["https://a", "https://b"],
                "size": "1024x1536",
                "n": 1,
                "response_format": "url",
            },
            payload,
        )

    def test_submit_uses_generation_endpoint_and_returns_auxiliary_fields(self):
        response = {
            "success": True,
            "task_id": "tsk_1",
            "task_status_url": "/custom/status",
            "poll_interval": 7,
            "max_wait": 99,
        }
        session = FakeSession(posts=[FakeResponse(response)])
        returned = adapter.submit_generation(
            adapter.build_generation_payload("P", []),
            base_url=adapter.DEFAULT_BASE_URL,
            api_key="secret",
            timeout=20,
            session=session,
        )
        self.assertEqual(response, returned)
        url, kwargs = session.post_calls[0]
        self.assertEqual("https://ai-api.youchu.work/v1/images/generations", url)
        self.assertEqual("gpt-image-2", kwargs["json"]["model"])
        self.assertNotIn("secret", json.dumps(kwargs["json"]))

    def test_poll_prefers_response_url_interval_and_wait(self):
        session = FakeSession(gets=[FakeResponse({"task_status": "pending"}), FakeResponse({"task_status": "completed"})])
        sleeps = []
        times = iter([0.0, 0.0, 1.0])
        result = adapter.poll_task_status(
            "tsk_1",
            {"task_status_url": "/preferred/status", "poll_interval": 7, "max_wait": 99},
            base_url=adapter.DEFAULT_BASE_URL,
            api_key="secret",
            poll_interval=3,
            max_wait=10,
            timeout=20,
            session=session,
            sleep_fn=sleeps.append,
            monotonic_fn=lambda: next(times),
        )
        self.assertEqual("completed", result["task_status"])
        self.assertEqual([7.0], sleeps)
        self.assertTrue(all(call[0].endswith("/preferred/status") for call in session.get_calls))

    def test_pending_and_in_progress_continue_until_completed(self):
        session = FakeSession(
            gets=[
                FakeResponse({"task_status": "pending"}),
                FakeResponse({"task_status": "in_progress"}),
                FakeResponse({"task_status": "completed"}),
            ]
        )
        times = iter([0.0, 0.0, 1.0, 2.0, 3.0])
        result = adapter.poll_task_status(
            "tsk_1",
            {},
            base_url=adapter.DEFAULT_BASE_URL,
            api_key="secret",
            poll_interval=1,
            max_wait=10,
            timeout=20,
            session=session,
            sleep_fn=lambda _: None,
            monotonic_fn=lambda: next(times),
        )
        self.assertEqual("completed", result["task_status"])
        self.assertEqual(3, len(session.get_calls))

    def test_failed_task_stops(self):
        session = FakeSession(gets=[FakeResponse({"task_status": "failed"})])
        with self.assertRaises(adapter.ToApisAdapterError) as caught:
            adapter.poll_task_status(
                "tsk_1", {}, base_url=adapter.DEFAULT_BASE_URL, api_key="secret",
                poll_interval=1, max_wait=10, timeout=20, session=session,
            )
        self.assertEqual("TASK_FAILED", caught.exception.error_code)
        self.assertEqual(adapter.EXIT_POLL, caught.exception.exit_code)

    def test_poll_timeout(self):
        session = FakeSession(gets=[FakeResponse({"task_status": "pending"})])
        times = iter([0.0, 2.0])
        with self.assertRaises(adapter.ToApisAdapterError) as caught:
            adapter.poll_task_status(
                "tsk_1", {}, base_url=adapter.DEFAULT_BASE_URL, api_key="secret",
                poll_interval=1, max_wait=1, timeout=20, session=session,
                sleep_fn=lambda _: None, monotonic_fn=lambda: next(times),
            )
        self.assertEqual("POLL_TIMEOUT", caught.exception.error_code)

    def test_extract_image_url_prefers_items(self):
        data = {
            "items": [{"url": "https://preferred/image.png"}],
            "data": {"result": {"data": [{"url": "https://compatible/image.png"}]}},
        }
        self.assertEqual("https://preferred/image.png", adapter.extract_image_url(data))

    def test_extract_image_url_uses_compatible_path(self):
        data = {"data": {"result": {"data": [{"url": "https://compatible/image.png"}]}}}
        self.assertEqual("https://compatible/image.png", adapter.extract_image_url(data))

    def test_completed_result_without_image_url_fails(self):
        session = FakeSession(gets=[FakeResponse({"items": []})])
        with self.assertRaises(adapter.ToApisAdapterError) as caught:
            adapter.fetch_task_result(
                "tsk_1", base_url=adapter.DEFAULT_BASE_URL, api_key="secret", timeout=20, session=session
            )
        self.assertEqual("fetch_result", caught.exception.stage)
        self.assertEqual("RESULT_IMAGE_URL_MISSING", caught.exception.error_code)


class OutputAndCliTests(unittest.TestCase):
    def test_download_success_is_atomic(self):
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "nested" / "image.png"
            session = FakeSession(gets=[FakeResponse(chunks=[b"abc", b"def"])])
            adapter.download_image("https://files.example/image.png", output, timeout=30, session=session)
            self.assertEqual(b"abcdef", output.read_bytes())
            self.assertEqual([], list(output.parent.glob("*.part")))

    def test_download_failure_leaves_no_partial_target(self):
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "image.png"
            session = FakeSession(gets=[FakeResponse(status_code=500)])
            with self.assertRaises(adapter.ToApisAdapterError) as caught:
                adapter.download_image("https://files.example/image.png", output, timeout=30, session=session)
            self.assertFalse(output.exists())
            self.assertEqual([], list(Path(raw).glob("*.part")))
        self.assertEqual(adapter.EXIT_OUTPUT, caught.exception.exit_code)

    def test_path_conflicts_are_rejected(self):
        path = Path("same.json")
        with self.assertRaises(adapter.ToApisAdapterError) as caught:
            adapter.validate_path_conflicts(path, path, Path("result.json"))
        self.assertEqual("PATH_CONFLICT", caught.exception.error_code)

    def test_output_cannot_overwrite_local_reference_asset(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            request_path = root / "request.json"
            data = example_request()
            data["reference_assets"] = data["reference_assets"][:1]
            local = root / data["reference_assets"][0]["source_ref"]["value"]
            local.parent.mkdir(parents=True)
            local.write_bytes(b"original-image")
            request_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            code, summary = adapter.run(
                args_for(
                    request_path,
                    local,
                    root / "result.json",
                    asset_root=str(root),
                    dry_run=True,
                )
            )
            self.assertEqual(adapter.EXIT_INPUT, code)
            self.assertEqual("PATH_CONFLICT", summary["error_code"])
            self.assertEqual(b"original-image", local.read_bytes())

    def test_dry_run_resolves_local_files_and_never_calls_network(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            request_path = root / "request.json"
            data = example_request()
            for item in data["reference_assets"]:
                local = root / item["source_ref"]["value"]
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(b"image")
            request_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            output = root / "out" / "image.png"
            result = root / "out" / "result.json"
            session = FakeSession()
            code, summary = adapter.run(
                args_for(request_path, output, result, asset_root=str(root), dry_run=True), session=session
            )
            self.assertEqual(0, code)
            self.assertTrue(summary["dry_run"])
            self.assertFalse(output.exists())
            self.assertTrue(result.exists())
            self.assertEqual([], session.post_calls)
            self.assertEqual([], session.get_calls)
            self.assertTrue(all("<local-upload-required:" in value for value in summary["payload_summary"]["images"]))

    def test_success_result_json_never_contains_api_key_and_stdout_is_json(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            request_path = root / "request.json"
            data = example_request()
            for index, item in enumerate(data["reference_assets"], 1):
                item["source_ref"] = {"ref_type": "asset_uri", "value": f"https://cdn.example/{index}.png"}
            request_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            output = root / "image.png"
            result = root / "result.json"
            session = FakeSession(
                posts=[FakeResponse({"success": True, "task_id": "tsk_1", "task_status": "pending"})],
                gets=[
                    FakeResponse({"task_status": "completed"}),
                    FakeResponse({"items": [{"url": "https://files.example/result.png"}]}),
                    FakeResponse(chunks=[b"image"]),
                ],
            )
            secret = "super-secret-key"
            with patch.dict(os.environ, {"TOAPIS_API_KEY": secret, "TOAPIS_BASE_URL": adapter.DEFAULT_BASE_URL}, clear=False):
                code, summary = adapter.run(args_for(request_path, output, result), session=session)
            self.assertEqual(0, code)
            self.assertTrue(output.exists())
            serialized = result.read_text(encoding="utf-8")
            self.assertNotIn(secret, serialized)
            self.assertNotIn("Authorization", serialized)
            self.assertEqual("completed", summary["task_status"])

            stream = io.StringIO()
            with redirect_stdout(stream):
                adapter.emit_json_summary(summary)
            parsed = json.loads(stream.getvalue())
            self.assertTrue(parsed["success"])

    def test_failure_writes_structured_result_and_expected_exit_code(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            missing = root / "missing.json"
            result = root / "failure.json"
            code, summary = adapter.run(args_for(missing, root / "image.png", result, dry_run=True))
            self.assertEqual(adapter.EXIT_INPUT, code)
            self.assertFalse(summary["success"])
            self.assertEqual("REQUEST_FILE_NOT_FOUND", summary["error_code"])
            self.assertEqual(summary, json.loads(result.read_text(encoding="utf-8")))

    def test_cli_has_no_api_key_option(self):
        with self.assertRaises(adapter.ToApisAdapterError) as caught:
            adapter.parse_args(
                [
                    "--request", "request.json",
                    "--output", "image.png",
                    "--result-json", "result.json",
                    "--api-key", "secret",
                ]
            )
        self.assertEqual("CLI_ARGUMENT_ERROR", caught.exception.error_code)
        self.assertNotIn("secret", caught.exception.message)
        self.assertIn("[REDACTED]", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
