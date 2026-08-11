import argparse
import json
import os
import subprocess
import tempfile
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_preview.py"
SPEC = spec_from_file_location("generate_preview", SCRIPT)
assert SPEC and SPEC.loader
adapter = module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


CURL = r"C:\Windows\System32\curl.exe"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"test-image"

SUBMIT_ID_FIXTURE = {
    "created_at": 1786417523,
    "id": "tsk_img_01KZQCGAHER4ZP0PZGTJFWGX7A",
    "metadata": {},
    "model": "gpt-image-2",
    "object": "generation.task",
    "progress": 0,
    "status": "pending",
}

SUBMIT_TASK_ID_FIXTURE = {
    "success": True,
    "task_id": "tsk_img_task_id",
    "task_status": "processing",
}

SUBMIT_DATA_ID_FIXTURE = {
    "success": True,
    "type": "image_text",
    "is_async": True,
    "task_status": "pending",
    "task_status_url": "/v1/tasks/tsk_img_data_id/status",
    "poll_interval": 3,
    "max_wait": 300,
    "data": {
        "id": "tsk_img_data_id",
        "status": "pending",
    },
}

SUBMIT_FIXTURE = SUBMIT_ID_FIXTURE

STATUS_FIXTURE = {
    "success": True,
    "type": "image_text",
    "task_id": "tsk_img_01KZQCGAHER4ZP0PZGTJFWGX7A",
    "task_status": "completed",
    "progress": 100,
    "latency": 0,
    "provider": {
        "id": "51bc13e8-a13e-425c-a80c-527989a524cd",
        "name": "ToAPIs",
    },
}

RESULT_FIXTURE = {
    "success": True,
    "task_status": "completed",
    "data": {
        "result": {
            "type": "image",
            "data": [{"url": "https://files.toapis.com/images/generated.png"}],
        }
    },
    "items": [{"url": "https://files.toapis.com/images/generated.png"}],
}


class CurlSpec:
    def __init__(
        self,
        body=None,
        *,
        raw_body=None,
        status=200,
        content_type="application/json",
        returncode=0,
        stderr="",
        download_bytes=None,
    ):
        self.body = body
        self.raw_body = raw_body
        self.status = status
        self.content_type = content_type
        self.returncode = returncode
        self.stderr = stderr
        self.download_bytes = download_bytes


class FakeCurlRunner:
    def __init__(self, specs):
        self.specs = list(specs)
        self.calls = []
        self.request_paths = []
        self.request_bytes = []

    def __call__(self, arguments, **kwargs):
        self.calls.append((arguments, kwargs))
        self.assert_safe_invocation(arguments, kwargs)
        spec = self.specs.pop(0)
        if "--data-binary" in arguments:
            value = arguments[arguments.index("--data-binary") + 1]
            self.assertTrue(value.startswith("@"))
            request_path = Path(value[1:])
            self.assertTrue(request_path.is_file())
            self.request_paths.append(request_path)
            self.request_bytes.append(request_path.read_bytes())
        if "--output" in arguments and spec.download_bytes is not None:
            output_path = Path(arguments[arguments.index("--output") + 1])
            output_path.write_bytes(spec.download_bytes)
        if spec.download_bytes is not None:
            body = ""
        elif spec.raw_body is not None:
            body = spec.raw_body
        else:
            body = json.dumps(spec.body, ensure_ascii=False)
        stdout = (
            body
            + f"\n{adapter.HTTP_STATUS_MARKER}{spec.status}"
            + f"\n{adapter.CONTENT_TYPE_MARKER}{spec.content_type}"
        ).encode("utf-8")
        return subprocess.CompletedProcess(
            arguments,
            spec.returncode,
            stdout=stdout,
            stderr=spec.stderr.encode("utf-8"),
        )

    def assert_safe_invocation(self, arguments, kwargs):
        if not isinstance(arguments, list):
            raise AssertionError("curl must be invoked with an argument list")
        if kwargs.get("shell") is not False:
            raise AssertionError("curl must use shell=False")
        if kwargs.get("check") is not False:
            raise AssertionError("curl must use check=False")

    @staticmethod
    def assertTrue(value):
        if not value:
            raise AssertionError("expected true value")


def args_for(prompt: Path, output_dir: Path, **overrides):
    values = {
        "prompt": str(prompt),
        "output_dir": str(output_dir),
        "provider": "toapis",
        "model": "gpt-image-2",
        "size": None,
        "poll_interval": 0.01,
        "max_wait": 10.0,
        "request_timeout": 20.0,
        "download_timeout": 20.0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class PromptAndPayloadTests(unittest.TestCase):
    def test_prompt_is_preserved_except_bom_and_trailing_whitespace(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "image-prompt.txt"
            path.write_text("GOAL\n- Exactly 6 product cards.\n  \n", encoding="utf-8-sig")
            self.assertEqual("GOAL\n- Exactly 6 product cards.", adapter.read_prompt(path))

    def test_landscape_canvas_maps_to_provider_landscape_size(self):
        prompt = "Landscape game UI.\nCompose for a 1920 x 1080 px canvas."
        canvas = adapter.requested_canvas(prompt)
        self.assertEqual("1920x1080", canvas)
        self.assertEqual("1536x1024", adapter.closest_provider_size(prompt, canvas))

    def test_payload_uses_exact_prompt_and_no_reference_images(self):
        prompt = "Exactly 6 product cards."
        payload = adapter.build_payload(prompt, model="gpt-image-2", size="1536x1024")
        self.assertEqual(prompt, payload["prompt"])
        self.assertEqual("text", payload["type"])
        self.assertEqual([], payload["images"])
        self.assertNotIn("masterpiece", json.dumps(payload).lower())


class CurlTransportTests(unittest.TestCase):
    def test_find_curl_prefers_curl_exe(self):
        with patch.object(adapter.shutil, "which", side_effect=lambda name: CURL if name == "curl.exe" else None) as which:
            self.assertEqual(CURL, adapter.find_curl())
        which.assert_called_once_with("curl.exe")

    def test_submit_uses_temp_utf8_json_data_binary_and_cleans_file(self):
        secret = "secret"
        runner = FakeCurlRunner([CurlSpec(SUBMIT_FIXTURE)])
        payload = adapter.build_payload("Prompt", model="gpt-image-2", size="1536x1024")
        returned = adapter.submit_generation(
            payload,
            base_url=adapter.DEFAULT_BASE_URL,
            api_key=secret,
            timeout=20,
            curl_path=CURL,
            runner=runner,
        )
        self.assertEqual(SUBMIT_FIXTURE, returned)
        arguments = runner.calls[0][0]
        self.assertIn("https://ai-api.youchu.work/v1/images/generations", arguments)
        self.assertIn("--data-binary", arguments)
        self.assertIn(f"Authorization: Bearer {secret}", arguments)
        self.assertFalse(runner.request_bytes[0].startswith(b"\xef\xbb\xbf"))
        self.assertEqual(payload, json.loads(runner.request_bytes[0].decode("utf-8")))
        self.assertTrue(all(not path.exists() for path in runner.request_paths))

    def test_submit_task_id_compatibility_and_priority(self):
        cases = [
            ({"id": "from_id", "task_id": "from_task_id", "data": {"id": "from_data"}}, "from_id"),
            (SUBMIT_TASK_ID_FIXTURE, "tsk_img_task_id"),
            (SUBMIT_DATA_ID_FIXTURE, "tsk_img_data_id"),
        ]
        for fixture, expected in cases:
            with self.subTest(expected=expected):
                runner = FakeCurlRunner([CurlSpec(fixture)])
                returned = adapter.submit_generation(
                    adapter.build_payload("Prompt", model="gpt-image-2", size="1536x1024"),
                    base_url=adapter.DEFAULT_BASE_URL,
                    api_key="secret",
                    timeout=20,
                    curl_path=CURL,
                    runner=runner,
                )
                self.assertEqual(expected, returned["id"])

    def test_unrecognized_submit_reports_structure_without_values(self):
        fixture = {"success": True, "opaque": {"authorization": "sensitive-value"}, "items": []}
        runner = FakeCurlRunner([CurlSpec(fixture)])
        with self.assertRaises(adapter.AdapterError) as caught:
            adapter.submit_generation(
                adapter.build_payload("Prompt", model="gpt-image-2", size="1536x1024"),
                base_url=adapter.DEFAULT_BASE_URL,
                api_key="secret",
                timeout=20,
                curl_path=CURL,
                runner=runner,
            )
        self.assertEqual("provider_response_invalid", caught.exception.error_type)
        self.assertIn("Generation response did not contain a usable task id", caught.exception.message)
        self.assertIn('"authorization":"string"', caught.exception.message)
        self.assertNotIn("sensitive-value", caught.exception.message)

    def test_status_uses_curl_get_tasks_status_endpoint(self):
        runner = FakeCurlRunner([CurlSpec(STATUS_FIXTURE)])
        adapter.poll_task(
            SUBMIT_FIXTURE["id"],
            SUBMIT_FIXTURE,
            base_url=adapter.DEFAULT_BASE_URL,
            api_key="secret",
            poll_interval=1,
            max_wait=10,
            timeout=20,
            curl_path=CURL,
            runner=runner,
        )
        arguments = runner.calls[0][0]
        self.assertEqual("GET", arguments[arguments.index("--request") + 1])
        self.assertIn(
            "https://ai-api.youchu.work/v1/tasks/tsk_img_01KZQCGAHER4ZP0PZGTJFWGX7A/status",
            arguments,
        )

    def test_result_uses_curl_get_and_prefers_items(self):
        runner = FakeCurlRunner([CurlSpec(RESULT_FIXTURE)])
        url = adapter.fetch_result(
            SUBMIT_FIXTURE["id"],
            base_url=adapter.DEFAULT_BASE_URL,
            api_key="secret",
            timeout=20,
            curl_path=CURL,
            runner=runner,
        )
        self.assertEqual("https://files.toapis.com/images/generated.png", url)
        self.assertIn(
            "https://ai-api.youchu.work/v1/tasks/tsk_img_01KZQCGAHER4ZP0PZGTJFWGX7A/result",
            runner.calls[0][0],
        )

    def test_curl_nonzero_exit_becomes_adapter_error(self):
        runner = FakeCurlRunner([CurlSpec(raw_body="", status=0, returncode=7, stderr="connection failed")])
        with self.assertRaises(adapter.AdapterError) as caught:
            adapter.curl_json_request(
                "GET",
                "https://example.test/status",
                curl_path=CURL,
                api_key="secret",
                timeout=20,
                runner=runner,
            )
        self.assertEqual("provider_request_failed", caught.exception.error_type)
        self.assertIn("exit code 7", caught.exception.message)

    def test_invalid_json_has_safe_body_prefix(self):
        runner = FakeCurlRunner([CurlSpec(raw_body="not-json-response", status=200)])
        with self.assertRaises(adapter.AdapterError) as caught:
            adapter.curl_json_request(
                "GET",
                "https://example.test/status",
                curl_path=CURL,
                api_key="secret",
                timeout=20,
                runner=runner,
            )
        self.assertEqual("provider_response_invalid", caught.exception.error_type)
        self.assertIn("not-json-response", caught.exception.message)


class RunTests(unittest.TestCase):
    def test_success_saves_image_and_minimal_redacted_result(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            prompt = root / "image-prompt.txt"
            prompt_text = "GOAL\n- Guild shop.\n\nCANVAS AND PAGE TYPE\n- Compose for a 1920 x 1080 px canvas."
            prompt.write_text(prompt_text, encoding="utf-8")
            output_dir = root / "preview"
            runner = FakeCurlRunner(
                [
                    CurlSpec(SUBMIT_FIXTURE),
                    CurlSpec(STATUS_FIXTURE),
                    CurlSpec(RESULT_FIXTURE),
                    CurlSpec(status=200, content_type="image/png", download_bytes=PNG_BYTES),
                ]
            )
            secret = "unit-test-secret"
            with patch.dict(os.environ, {"TOAPIS_API_KEY": secret, "TOAPIS_BASE_URL": adapter.DEFAULT_BASE_URL}, clear=False):
                code, result = adapter.run(args_for(prompt, output_dir), runner=runner, curl_path=CURL)

            self.assertEqual(0, code)
            self.assertEqual(PNG_BYTES, (output_dir / "preview.png").read_bytes())
            saved = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result, saved)
            self.assertEqual("success", saved["status"])
            self.assertEqual("1920x1080", saved["requested_canvas"])
            self.assertEqual("1536x1024", saved["provider_size"])
            self.assertEqual("preview.png", saved["output_image"])
            serialized = json.dumps(saved)
            self.assertNotIn(secret, serialized)
            self.assertNotIn("Authorization", serialized)
            submitted = json.loads(runner.request_bytes[0].decode("utf-8"))
            self.assertEqual(prompt_text, submitted["prompt"])
            self.assertEqual("text", submitted["type"])
            self.assertEqual([], submitted["images"])
            self.assertIn("--location", runner.calls[-1][0])

    def test_missing_api_key_writes_error_result_without_curl(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            prompt = root / "image-prompt.txt"
            prompt.write_text("Landscape game UI.", encoding="utf-8")
            output_dir = root / "preview"
            runner = FakeCurlRunner([])
            with patch.dict(os.environ, {}, clear=True):
                code, result = adapter.run(args_for(prompt, output_dir), runner=runner)
            self.assertEqual(adapter.EXIT_CONFIG, code)
            self.assertEqual("provider_config_missing", result["error_type"])
            self.assertEqual([], runner.calls)
            self.assertEqual(result, json.loads((output_dir / "result.json").read_text(encoding="utf-8")))

    def test_missing_system_curl_reports_dependency_error(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            prompt = root / "image-prompt.txt"
            prompt.write_text("Landscape game UI.", encoding="utf-8")
            output_dir = root / "preview"
            with patch.dict(os.environ, {"TOAPIS_API_KEY": "secret"}, clear=True):
                with patch.object(adapter.shutil, "which", return_value=None):
                    code, result = adapter.run(args_for(prompt, output_dir))
            self.assertEqual(adapter.EXIT_CONFIG, code)
            self.assertEqual("provider_dependency_missing", result["error_type"])

    def test_jpeg_download_keeps_real_extension(self):
        with tempfile.TemporaryDirectory() as raw:
            runner = FakeCurlRunner(
                [CurlSpec(status=200, content_type="image/jpeg", download_bytes=b"\xff\xd8\xffjpeg")]
            )
            path = adapter.download_image(
                "https://files.example/generated",
                Path(raw),
                timeout=20,
                curl_path=CURL,
                api_key="secret",
                runner=runner,
            )
            self.assertEqual("preview.jpg", path.name)


if __name__ == "__main__":
    unittest.main()
