#!/usr/bin/env python3
"""Start or resume Recursive Runtime with interactive file adapters."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from interactive_file_adapter import InteractiveFileAdapter
from recursive_runtime import (
    RecursiveRuntime,
    RuntimeAdapters,
    RuntimeConfig,
)


def build_interactive_adapters(run_dir: Path) -> RuntimeAdapters:
    return RuntimeAdapters(
        router=InteractiveFileAdapter(run_dir, "router"),
        structural_split=InteractiveFileAdapter(run_dir, "structural_split"),
        expand_instances=InteractiveFileAdapter(run_dir, "expand_instances"),
        semantic_decompose=InteractiveFileAdapter(run_dir, "semantic_decompose"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start or resume Stage2-A Recursive Runtime via JSON files."
    )
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--root-node-crop", type=Path)
    parser.add_argument("--root-id", default="root")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--validation-mode",
        choices=("mechanics", "real_image"),
        default="real_image",
    )
    parser.add_argument(
        "--repeated-instance-semantic-limit",
        type=int,
        default=2,
    )
    return parser


def _print_result(runtime: RecursiveRuntime, result: str) -> None:
    if result == "waiting_for_adapter":
        pending = runtime.state.pending_adapter_request or {}
        print("WAITING_FOR_ADAPTER")
        for key in ("request_id", "adapter_kind", "analysis_image"):
            print(f"{key}={pending.get(key, '')}")
        return
    print(f"RUN_RESULT={result}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    adapters = build_interactive_adapters(args.run_dir)
    try:
        if args.resume:
            runtime = RecursiveRuntime.load(
                run_dir=args.run_dir,
                adapters=adapters,
            )
        else:
            if args.root_node_crop is None:
                raise ValueError("--root-node-crop is required unless --resume is used")
            runtime = RecursiveRuntime.create(
                run_dir=args.run_dir,
                root_node_crop=args.root_node_crop,
                root_id=args.root_id,
                adapters=adapters,
                config=RuntimeConfig(
                    repeated_instance_semantic_limit=(
                        args.repeated_instance_semantic_limit
                    ),
                    validation_mode=args.validation_mode,
                ),
            )
        result = runtime.run()
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Runtime failed: {exc}", file=sys.stderr)
        return 1
    _print_result(runtime, result)
    return 1 if result in {"failed", "blocked"} else 0


if __name__ == "__main__":
    raise SystemExit(main())

