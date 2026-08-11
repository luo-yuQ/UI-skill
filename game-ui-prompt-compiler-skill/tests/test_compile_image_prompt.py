import subprocess
import sys
import tempfile
import unittest
import json
import re
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


def compiler_command(
    compose_plan: Path,
    style_profile: Path,
    output: Path,
    *,
    mode: str | None = None,
) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--compose-plan",
        str(compose_plan),
        "--style-profile",
        str(style_profile),
    ]
    if mode is not None:
        command.extend(["--mode", mode])
    command.extend(["--output", str(output)])
    return command


class PromptCompilerTests(unittest.TestCase):
    def test_repository_run_preserves_structure_and_style(self):
        if not RUN_PLAN.exists():
            self.skipTest("Ignored repository run is not present in this checkout")
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "image-prompt.txt"
            result = subprocess.run(
                compiler_command(RUN_PLAN, STYLE, output, mode="text-only"),
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
        self.assertNotIn("Use the provided reference image", prompt)
        self.assertNotIn("REFERENCE USAGE", prompt)
        for internal_name in ("component_id", "trait_id", "source_ref", "confidence"):
            self.assertNotIn(internal_name, prompt)

    def test_text_only_default_is_byte_compatible_with_explicit_mode(self):
        if not RUN_PLAN.exists():
            self.skipTest("Ignored repository run is not present in this checkout")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            default_output = temp / "default.txt"
            explicit_output = temp / "explicit.txt"
            default_result = subprocess.run(
                compiler_command(RUN_PLAN, STYLE, default_output),
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            explicit_result = subprocess.run(
                compiler_command(RUN_PLAN, STYLE, explicit_output, mode="text-only"),
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(default_result.returncode, 0, default_result.stderr)
            self.assertEqual(explicit_result.returncode, 0, explicit_result.stderr)
            self.assertEqual(default_output.read_bytes(), explicit_output.read_bytes())

    def test_reference_guided_uses_reference_authority_and_preserves_hard_requirements(self):
        if not RUN_PLAN.exists():
            self.skipTest("Ignored repository run is not present in this checkout")
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "image-prompt.txt"
            result = subprocess.run(
                compiler_command(RUN_PLAN, STYLE, output, mode="reference-guided"),
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
            "REFERENCE USAGE",
            "HARD REQUIREMENTS",
            "PRODUCTION CONSTRAINTS",
        ]
        self.assertEqual([line for line in prompt.splitlines() if line in expected_headings], expected_headings)
        self.assertIn("Use the provided reference image as the primary visual style reference.", prompt)
        self.assertIn("Exactly 3 category tabs.", prompt)
        self.assertIn("Exactly 6 product cards.", prompt)
        self.assertIn("exactly 2 columns and 3 rows", prompt)
        self.assertIn("The category navigation must remain on the left.", prompt)
        self.assertIn("The refresh button must remain at the bottom.", prompt)
        for forbidden in (
            "cool blue-gray",
            "silver-black",
            "dark-fantasy visual",
            "gothic detail",
            "warm coral-red",
            "orange",
            "cream",
            "soft gold",
        ):
            self.assertNotIn(forbidden, prompt.lower())

    def test_reference_guided_forbids_copying_and_never_leaks_reference_paths(self):
        if not RUN_PLAN.exists():
            self.skipTest("Ignored repository run is not present in this checkout")
        style = json.loads(STYLE.read_text(encoding="utf-8"))
        style["source_ref"] = "C:/private/reference-secret.png"
        style["workspace_path"] = "D:/workspace/hidden-reference.jpg"
        style["reference_url"] = "https://private.example/reference.webp"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            style_path = temp / "style.json"
            output = temp / "image-prompt.txt"
            style_path.write_text(json.dumps(style), encoding="utf-8")
            result = subprocess.run(
                compiler_command(RUN_PLAN, style_path, output, mode="reference-guided"),
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            prompt = output.read_text(encoding="utf-8")

        for forbidden in ("reference-secret.png", "hidden-reference.jpg", "private.example", "C:/", "D:/"):
            self.assertNotIn(forbidden, prompt)
        for required in (
            "Do not copy reference-specific characters.",
            "Do not copy reference-specific scenes or environmental content.",
            "Do not copy the reference layout.",
            "Do not copy its text.",
            "Do not copy its business content.",
            "Do not introduce reference-specific gameplay functions.",
        ):
            self.assertIn(required, prompt)

    def test_both_modes_leave_upstream_json_unchanged(self):
        if not RUN_PLAN.exists():
            self.skipTest("Ignored repository run is not present in this checkout")
        plan_before = RUN_PLAN.read_bytes()
        style_before = STYLE.read_bytes()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            for mode in ("text-only", "reference-guided"):
                result = subprocess.run(
                    compiler_command(RUN_PLAN, STYLE, temp / f"{mode}.txt", mode=mode),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(plan_before, RUN_PLAN.read_bytes())
        self.assertEqual(style_before, STYLE.read_bytes())

    def test_translates_chinese_style_filters_internal_instructions_and_visualizes_labels(self):
        if not RUN_PLAN.exists():
            self.skipTest("Ignored repository run is not present in this checkout")
        plan = json.loads(RUN_PLAN.read_text(encoding="utf-8"))
        style = json.loads(STYLE.read_text(encoding="utf-8"))

        plan["project_context"]["constraints"].append(
            "Use A as layout evidence and B as style evidence without mutating either source."
        )
        plan["visual_direction"]["hierarchy_emphasis"].append(
            "Agent instruction: preserve provenance and source_ref values."
        )
        plan["visual_direction"]["directives"] = []
        for component in plan["component_tree"]:
            if component.get("component_id") == "category_tab_template":
                component["name"] = "Category Tab Template"
            elif component.get("component_id") == "product_card_template":
                component["name"] = "Product Card Template"

        translations = {
            "color_cool_blue_gray": "低饱和冷蓝灰配色",
            "material_silver_black_hard_surfaces": "银黑色硬表面材质",
            "material_heavy_matte_bases": "厚重的哑光基底",
            "shape_sharp_elongated": "尖锐修长的轮廓",
            "lighting_blue_white_local": "局部蓝白色光效",
            "decoration_restrained_gothic": "克制的哥特式装饰",
            "world_dark_fantasy": "暗黑幻想视觉语境",
        }
        for profile in style["visual_profiles"].values():
            for classification in ("stable", "secondary", "local", "conflicting", "uncertain"):
                for trait in profile[classification]:
                    if trait.get("trait_id") in translations:
                        trait["trait"] = translations[trait["trait_id"]]
                        trait["description"] = translations[trait["trait_id"]]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            plan_path = temp / "plan.json"
            style_path = temp / "style.json"
            output = temp / "image-prompt.txt"
            plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            style_path.write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--compose-plan",
                    str(plan_path),
                    "--style-profile",
                    str(style_path),
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

        self.assertIsNone(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", prompt))
        self.assertTrue(prompt.isascii())
        self.assertIn("Use a low-saturation cool blue-gray palette.", prompt)
        self.assertIn("Use silver-black hard-surface materials.", prompt)
        self.assertIn("Exactly 3 category tabs.", prompt)
        self.assertIn("Exactly 6 product cards.", prompt)
        self.assertNotIn("category tab templates", prompt.lower())
        self.assertNotIn("product card templates", prompt.lower())
        self.assertNotIn("layout evidence", prompt.lower())
        self.assertNotIn("style evidence", prompt.lower())
        self.assertNotIn("provenance", prompt.lower())
        self.assertNotIn("source_ref", prompt.lower())

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
