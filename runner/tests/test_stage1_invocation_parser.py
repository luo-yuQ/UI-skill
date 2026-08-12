from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


RUNNER_ROOT = Path(__file__).resolve().parents[1]
PARSER = RUNNER_ROOT / "scripts" / "parse-stage1-invocation.py"


class Stage1InvocationParserTests(unittest.TestCase):
    def parse(self, text: str, cwd: Path | None = None) -> tuple[int, dict[str, object]]:
        result = subprocess.run(
            [sys.executable, str(PARSER), "--text", text],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        return result.returncode, json.loads(result.stdout)

    def test_new_run_separates_business_requirement_and_init_control(self):
        code, result = self.parse(
            "/stage1 参考这个充值界面的布局，帮我设计一个新的游戏充值页面。\n"
            "只初始化 run，然后停止。"
        )
        self.assertEqual(0, code)
        self.assertEqual("new", result["mode"])
        self.assertIsNone(result["run_path"])
        self.assertEqual(
            "参考这个充值界面的布局，帮我设计一个新的游戏充值页面。",
            result["user_requirement"],
        )
        self.assertEqual("init", result["stage_control"]["stop_after"])

    def test_explicit_run_path_resumes_and_discards_invocation_requirement(self):
        code, result = self.parse(
            "/stage1\n"
            "继续 runs/20260811-170410_recharge-page_003。\n"
            "执行 Composer，完成后停止。"
        )
        self.assertEqual(0, code)
        self.assertEqual("resume", result["mode"])
        self.assertEqual(
            "runs/20260811-170410_recharge-page_003", result["run_path"]
        )
        self.assertIsNone(result["user_requirement"])
        self.assertEqual("composer", result["stage_control"]["stop_after"])

    def test_resume_intent_without_run_path_fails(self):
        code, result = self.parse("/stage1\n继续跑 B2。")
        self.assertEqual(2, code)
        self.assertEqual("RUN_ID_REQUIRED", result["error"]["code"])

    def test_multiple_distinct_run_paths_fail(self):
        code, result = self.parse("runs/a\nruns/b")
        self.assertEqual(2, code)
        self.assertEqual("AMBIGUOUS_RUN_ID", result["error"]["code"])

    def test_no_explicit_resume_is_new_even_when_runs_exist(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "runs" / "old-a").mkdir(parents=True)
            (root / "runs" / "old-b").mkdir(parents=True)
            code, result = self.parse("/stage1\n帮我设计一个商城页面。", cwd=root)
        self.assertEqual(0, code)
        self.assertEqual("new", result["mode"])
        self.assertIsNone(result["stage_control"]["stop_after"])

    def test_resume_parser_does_not_modify_request(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            request_path = root / "runs" / "existing" / "00-input" / "request.json"
            request_path.parent.mkdir(parents=True)
            request_path.write_text(
                json.dumps(
                    {"user_requirement": "原始且不可变的业务需求。"},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            before = request_path.read_bytes()
            code, result = self.parse(
                "/stage1 继续 runs/existing。执行 Composer，完成后停止。",
                cwd=root,
            )
            after = request_path.read_bytes()
        self.assertEqual(0, code)
        self.assertEqual("resume", result["mode"])
        self.assertIsNone(result["user_requirement"])
        self.assertEqual(before, after)

    def test_unsupported_stage_control_fails_instead_of_guessing(self):
        code, result = self.parse("/stage1 做一个商城页面。\n运行到 B1 后停止。")
        self.assertEqual(2, code)
        self.assertEqual("UNSUPPORTED_STAGE_CONTROL", result["error"]["code"])

    def test_input_file_is_supported(self):
        with tempfile.TemporaryDirectory() as raw:
            invocation = Path(raw) / "invocation.txt"
            invocation.write_text("/stage1\n设计一个背包页面。", encoding="utf-8")
            process = subprocess.run(
                [sys.executable, str(PARSER), "--input-file", str(invocation)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        self.assertEqual(0, process.returncode)
        self.assertEqual("设计一个背包页面。", json.loads(process.stdout)["user_requirement"])


if __name__ == "__main__":
    unittest.main()
