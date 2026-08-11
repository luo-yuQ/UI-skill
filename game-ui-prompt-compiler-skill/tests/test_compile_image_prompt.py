import subprocess
import sys
import tempfile
import unittest
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "game-ui-prompt-compiler-skill" / "scripts" / "compile_image_prompt.py"
STYLE = REPO_ROOT / "game-ui-style-reference-analyzer" / "examples" / "b2-style-profile.json"
RUN_PLAN = (
    REPO_ROOT
    / "runs"
    / "stage-composer"
    / "20260810_v2.1-test_001"
    / "ui-compose-plan.json"
)


class PromptCompilerTests(unittest.TestCase):
    def test_repository_run_preserves_structure_and_style(self):
        if not RUN_PLAN.exists():
            self.skipTest("Ignored repository run is not present in this checkout")
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "image-prompt.txt"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--compose-plan",
                    str(RUN_PLAN),
                    "--style-profile",
                    str(STYLE),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            prompt = output.read_text(encoding="utf-8")

        expected_headings = [
            "GOAL",
            "CANVAS AND PAGE TYPE",
            "COMPOSITION",
            "VISUAL STYLE",
            "HARD REQUIREMENTS",
            "PRODUCTION CONSTRAINTS",
        ]
        self.assertEqual([line for line in prompt.splitlines() if line in expected_headings], expected_headings)
        self.assertIn("Exactly 3 category tabs.", prompt)
        self.assertIn("Exactly 6 product cards.", prompt)
        self.assertIn("exactly 2 columns and 3 rows", prompt)
        self.assertIn("Place the category navigation on the left side.", prompt)
        self.assertIn("Use cool blue-gray and near-black globally.", prompt)
        self.assertIn("Use silver-black frames and matte panel bases.", prompt)
        self.assertNotIn("localized red content accents", prompt)
        self.assertNotIn("semi-realistic versus", prompt)
        for internal_name in ("component_id", "trait_id", "source_ref", "confidence"):
            self.assertNotIn(internal_name, prompt)

    def test_invalid_json_fails_without_creating_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            invalid_plan = temp / "invalid-plan.json"
            output = temp / "image-prompt.txt"
            invalid_plan.write_text("{not valid json", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--compose-plan",
                    str(invalid_plan),
                    "--style-profile",
                    str(STYLE),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Cannot parse compose plan", result.stderr)
        self.assertFalse(output.exists())

    def test_missing_style_description_fails(self):
        if not RUN_PLAN.exists():
            self.skipTest("Ignored repository run is not present in this checkout")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            empty_style = temp / "empty-style.json"
            output = temp / "image-prompt.txt"
            empty_style.write_text(json.dumps({"schema_version": "0.1"}), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--compose-plan",
                    str(RUN_PLAN),
                    "--style-profile",
                    str(empty_style),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("No usable visual style description", result.stderr)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
