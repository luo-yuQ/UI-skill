from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


RUNNER_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = RUNNER_ROOT / "scripts" / "sync-stage1-inputs.py"
INJECT_SCRIPT = RUNNER_ROOT / "scripts" / "inject-a1-source.py"


class Stage1InputToolTests(unittest.TestCase):
    def make_run(self, root: Path) -> Path:
        run = root / "existing-run"
        (run / "00-input" / "layout-reference").mkdir(parents=True)
        (run / "00-input" / "style-reference").mkdir(parents=True)
        (run / "10-layout-reference").mkdir(parents=True)
        request = {
            "user_requirement": "保持这段原始需求：充值页面，不要翻译。",
            "layout_references": [],
            "style_references": [],
            "caller_note": {"preserve": True},
        }
        (run / "00-input" / "request.json").write_text(
            json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return run

    def run_script(self, script: Path, run: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), "--run", str(run)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_sync_updates_only_reference_lists_and_writes_real_metadata(self):
        with tempfile.TemporaryDirectory() as raw:
            run = self.make_run(Path(raw))
            layout = run / "00-input" / "layout-reference"
            style = run / "00-input" / "style-reference"
            fixtures = (
                (layout / "B-square.PNG", (31, 31), "PNG"),
                (layout / "a-wide.jpg", (80, 40), "JPEG"),
                (style / "a-tall.jpeg", (25, 75), "JPEG"),
                (style / "b-wide.webp", (72, 24), "WEBP"),
                (style / "c-square.bmp", (18, 18), "BMP"),
            )
            for path, size, image_format in fixtures:
                Image.new("RGB", size, "navy").save(path, format=image_format)
            (layout / "ignored.gif").write_bytes(b"not scanned")

            before = json.loads((run / "00-input" / "request.json").read_text(encoding="utf-8"))
            result = self.run_script(SYNC_SCRIPT, run)
            self.assertEqual(0, result.returncode, result.stderr)

            request = json.loads((run / "00-input" / "request.json").read_text(encoding="utf-8"))
            metadata = json.loads(
                (run / "00-input" / "input-metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(before["user_requirement"], request["user_requirement"])
            self.assertEqual(before["caller_note"], request["caller_note"])
            self.assertEqual(
                [
                    "00-input/layout-reference/a-wide.jpg",
                    "00-input/layout-reference/B-square.PNG",
                ],
                request["layout_references"],
            )
            self.assertEqual(
                [
                    "00-input/style-reference/a-tall.jpeg",
                    "00-input/style-reference/b-wide.webp",
                    "00-input/style-reference/c-square.bmp",
                ],
                request["style_references"],
            )
            self.assertEqual(
                {
                    "reference_id": "layout-001",
                    "path": "00-input/layout-reference/a-wide.jpg",
                    "file_name": "a-wide.jpg",
                    "width": 80,
                    "height": 40,
                    "orientation": "landscape",
                },
                metadata["layout_references"][0],
            )
            self.assertEqual("square", metadata["layout_references"][1]["orientation"])
            self.assertEqual("portrait", metadata["style_references"][0]["orientation"])
            self.assertEqual("landscape", metadata["style_references"][1]["orientation"])
            self.assertEqual("square", metadata["style_references"][2]["orientation"])

    def test_sync_is_deterministic_and_supports_empty_reference_lists(self):
        with tempfile.TemporaryDirectory() as raw:
            run = self.make_run(Path(raw))
            first = self.run_script(SYNC_SCRIPT, run)
            self.assertEqual(0, first.returncode, first.stderr)
            request_path = run / "00-input" / "request.json"
            metadata_path = run / "00-input" / "input-metadata.json"
            first_request = request_path.read_bytes()
            first_metadata = metadata_path.read_bytes()

            second = self.run_script(SYNC_SCRIPT, run)
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual(first_request, request_path.read_bytes())
            self.assertEqual(first_metadata, metadata_path.read_bytes())
            request = json.loads(request_path.read_text(encoding="utf-8"))
            self.assertEqual([], request["layout_references"])
            self.assertEqual([], request["style_references"])

    def test_inject_overwrites_only_deterministic_source_fields(self):
        with tempfile.TemporaryDirectory() as raw:
            run = self.make_run(Path(raw))
            image_path = run / "00-input" / "layout-reference" / "layout.png"
            Image.new("RGB", (123, 45), "green").save(image_path)
            sync = self.run_script(SYNC_SCRIPT, run)
            self.assertEqual(0, sync.returncode, sync.stderr)

            analysis = {
                "schema_version": "0.1",
                "analysis_id": "keep-analysis",
                "source": {
                    "source_ref": "llm:guess",
                    "file_name": "wrong.png",
                    "width": 1,
                    "height": 999,
                    "orientation": "portrait",
                    "capture_limitations": ["保留遮挡观察"],
                },
                "semantic_payload": {"must": "remain unchanged"},
            }
            analysis_path = run / "10-layout-reference" / "layout-analysis.json"
            analysis_path.write_text(
                json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            before = copy.deepcopy(analysis)

            result = self.run_script(INJECT_SCRIPT, run)
            self.assertEqual(0, result.returncode, result.stderr)
            written = json.loads(analysis_path.read_text(encoding="utf-8"))
            self.assertEqual("run-input:layout-001", written["source"]["source_ref"])
            self.assertEqual("layout.png", written["source"]["file_name"])
            self.assertEqual(123, written["source"]["width"])
            self.assertEqual(45, written["source"]["height"])
            self.assertEqual("landscape", written["source"]["orientation"])
            self.assertEqual(
                before["source"]["capture_limitations"],
                written["source"]["capture_limitations"],
            )
            self.assertEqual(before["semantic_payload"], written["semantic_payload"])
            self.assertEqual(before["analysis_id"], written["analysis_id"])

    def test_inject_fails_without_layout_001(self):
        with tempfile.TemporaryDirectory() as raw:
            run = self.make_run(Path(raw))
            sync = self.run_script(SYNC_SCRIPT, run)
            self.assertEqual(0, sync.returncode, sync.stderr)
            analysis_path = run / "10-layout-reference" / "layout-analysis.json"
            analysis_path.write_text('{"source": {}}', encoding="utf-8")

            result = self.run_script(INJECT_SCRIPT, run)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("layout-001", result.stderr)


if __name__ == "__main__":
    unittest.main()
