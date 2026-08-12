#!/usr/bin/env python3
"""Parse one Stage1 invocation without consulting workspace or conversation state."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn


SCHEMA_VERSION = "0.1"
RUN_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9_.-])runs/[A-Za-z0-9][A-Za-z0-9._-]*")
COMMAND_PATTERN = re.compile(r"(?i)(?<!\S)/stage1\b\s*")

RESUME_PATTERNS = (
    re.compile(r"(?i)(?<![A-Za-z])resume(?![A-Za-z])"),
    re.compile(r"(?i)(?<![A-Za-z])continue\s+(?:the\s+)?(?:previous|last|current|run|A1|B1|B2|composer)\b"),
    re.compile(r"继续\s*(?:跑|执行|做)?\s*(?:上一个|上一(?:个|次)|当前|这个|A1|B1|B2|Composer)", re.IGNORECASE),
    re.compile(r"接着\s*(?:跑|执行|做)\s*(?:A1|B1|B2|Composer)", re.IGNORECASE),
    re.compile(r"恢复\s*(?:上一个|上一(?:个|次)|当前|这个)?\s*(?:run|运行|A1|B1|B2|Composer)?", re.IGNORECASE),
)


@dataclass(frozen=True)
class StageControlPattern:
    stop_after: str
    expression: re.Pattern[str]


STAGE_CONTROL_PATTERNS = (
    StageControlPattern(
        "init",
        re.compile(
            r"只\s*初始化(?:\s*run)?(?:\s*[，,]?\s*(?:然后)?\s*停止)?[。.!！]?"
            r"|(?:only\s+initialize(?:\s+the)?\s+run|initialize(?:\s+the)?\s+run\s*(?:,?\s*then)?\s+stop|stop\s+after\s+init(?:ialization)?)\s*[.!]?",
            re.IGNORECASE,
        ),
    ),
    StageControlPattern(
        "a1",
        re.compile(
            r"只\s*执行\s*A1(?:\s*[，,]?\s*完成后\s*停止)?[。.!！]?"
            r"|执行\s*A1\s*[，,]?\s*完成后\s*停止[。.!！]?"
            r"|stop\s+after\s+A1\s*[.!]?",
            re.IGNORECASE,
        ),
    ),
    StageControlPattern(
        "b2",
        re.compile(
            r"执行\s*B1\s*[/／、和]\s*B2\s*[，,]?\s*完成后\s*停止[。.!！]?"
            r"|(?:执行|运行到)\s*B2\s*[，,]?\s*(?:完成后)?\s*停止[。.!！]?"
            r"|stop\s+after\s+B2\s*[.!]?",
            re.IGNORECASE,
        ),
    ),
    StageControlPattern(
        "composer",
        re.compile(
            r"执行\s*Composer\s*[，,]?\s*完成后\s*停止[。.!！]?"
            r"|运行到\s*Composer\s*[，,]?\s*(?:完成后)?\s*停止[。.!！]?"
            r"|stop\s+after\s+Composer\s*[.!]?",
            re.IGNORECASE,
        ),
    ),
)

EXPLICIT_CONTROL_PATTERN = re.compile(
    r"只\s*(?:初始化|执行)|不要\s*执行|运行到|完成后\s*停止|然后\s*停止"
    r"|(?i:stop\s+after|only\s+(?:initialize|run|execute)|do\s+not\s+(?:run|execute))"
)


class InvocationError(ValueError):
    """A deterministic invocation parsing failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def fail(code: str, message: str) -> NoReturn:
    raise InvocationError(code, message)


def parse_stage_control(text: str) -> tuple[str | None, list[tuple[int, int]]]:
    matches: list[tuple[str, int, int]] = []
    for control in STAGE_CONTROL_PATTERNS:
        matches.extend(
            (control.stop_after, match.start(), match.end())
            for match in control.expression.finditer(text)
        )

    targets = {target for target, _, _ in matches}
    if len(targets) > 1:
        fail(
            "UNSUPPORTED_STAGE_CONTROL",
            "Invocation contains conflicting stop-after controls.",
        )
    if not matches:
        if EXPLICIT_CONTROL_PATTERN.search(text):
            fail(
                "UNSUPPORTED_STAGE_CONTROL",
                "Stage control cannot be mapped to init, a1, b2, or composer.",
            )
        return None, []

    spans = sorted((start, end) for _, start, end in matches)
    return next(iter(targets)), spans


def remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + text[end:]
    return text


def clean_business_requirement(text: str, control_spans: list[tuple[int, int]]) -> str:
    text = remove_spans(text, control_spans)
    text = COMMAND_PATTERN.sub("", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines).strip()


def parse_invocation(text: str) -> dict[str, object]:
    if not isinstance(text, str) or not text.strip():
        fail("EMPTY_INVOCATION", "Stage1 invocation text must not be empty.")

    run_paths = list(dict.fromkeys(RUN_PATH_PATTERN.findall(text)))
    if len(run_paths) > 1:
        fail("AMBIGUOUS_RUN_ID", "Invocation contains multiple distinct run paths.")

    stop_after, control_spans = parse_stage_control(text)

    if run_paths:
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "resume",
            "run_path": run_paths[0],
            "user_requirement": None,
            "stage_control": {"stop_after": stop_after},
        }

    if any(pattern.search(text) for pattern in RESUME_PATTERNS):
        fail("RUN_ID_REQUIRED", "Resume intent requires an explicit runs/<run-id> path.")

    requirement = clean_business_requirement(text, control_spans)
    if not requirement:
        fail(
            "BUSINESS_REQUIREMENT_REQUIRED",
            "A new run requires a business requirement after Runner control is removed.",
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "new",
        "run_path": None,
        "user_requirement": requirement,
        "stage_control": {"stop_after": stop_after},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="Current Stage1 invocation text")
    source.add_argument("--input-file", type=Path, help="UTF-8 invocation text file")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    try:
        text = (
            args.text
            if args.text is not None
            else args.input_file.read_text(encoding="utf-8-sig")
        )
        result = parse_invocation(text)
    except InvocationError as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "error": {"code": exc.code, "message": str(exc)},
        }
        print(json.dumps(result, ensure_ascii=False))
        return 2
    except (OSError, UnicodeError) as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "error": {"code": "INPUT_READ_FAILED", "message": str(exc)},
        }
        print(json.dumps(result, ensure_ascii=False))
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
