import argparse
import json
import os
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


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"test-image"


class FakeResponse:
    def __init__(self, body=None, *, status_code=200, chunks=None, headers=None):
        self.body = body
        self.status_code = status_code
        self.chunks = chunks or []
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body

    def iter_content(self, chunk_size=65536):
        yield from self.chunks


class FakeSession:
    def __init__(self, *, posts=None, gets=None):
        self.posts = list(posts or [])
        self.gets = list(gets or [])
        self.post_calls = []
        self.get_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.posts.pop(0)

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.gets.pop(0)


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
        self.assertEqual([], payload["images"])
        self.assertNotIn("masterpiece", json.dumps(payload).lower())


class RunTests(unittest.TestCase):
    def test_success_saves_image_and_minimal_redacted_result(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            prompt = root / "image-prompt.txt"
            prompt_text = "GOAL\n- Guild shop.\n\nCANVAS AND PAGE TYPE\n- Compose for a 1920 x 1080 px canvas."
            prompt.write_text(prompt_text, encoding="utf-8")
            output_dir = root / "preview"
            session = FakeSession(
                posts=[FakeResponse({"success": True, "task_id": "task_1"})],
                gets=[
                    FakeResponse({"task_status": "completed"}),
                    FakeResponse({"items": [{"url": "https://files.example/generated.png"}]}),
                    FakeResponse(chunks=[PNG_BYTES], headers={"Content-Type": "image/png"}),
                ],
            )
            secret = "unit-test-secret"
            with patch.dict(os.environ, {"TOAPIS_API_KEY": secret, "TOAPIS_BASE_URL": adapter.DEFAULT_BASE_URL}, clear=False):
                code, result = adapter.run(args_for(prompt, output_dir), session=session)

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
            submitted = session.post_calls[0][1]["json"]
            self.assertEqual(prompt_text, submitted["prompt"])
            self.assertEqual([], submitted["images"])

    def test_missing_api_key_writes_error_result(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            prompt = root / "image-prompt.txt"
            prompt.write_text("Landscape game UI.", encoding="utf-8")
            output_dir = root / "preview"
            with patch.dict(os.environ, {}, clear=True):
                code, result = adapter.run(args_for(prompt, output_dir), session=FakeSession())
            self.assertEqual(adapter.EXIT_CONFIG, code)
            self.assertEqual("provider_config_missing", result["error_type"])
            self.assertEqual(result, json.loads((output_dir / "result.json").read_text(encoding="utf-8")))

    def test_jpeg_response_keeps_real_extension(self):
        with tempfile.TemporaryDirectory() as raw:
            output_dir = Path(raw)
            session = FakeSession(gets=[FakeResponse(chunks=[b"\xff\xd8\xffjpeg"], headers={"Content-Type": "image/jpeg"})])
            path = adapter.download_image("https://files.example/generated", output_dir, timeout=20, session=session)
            self.assertEqual("preview.jpg", path.name)


if __name__ == "__main__":
    unittest.main()
