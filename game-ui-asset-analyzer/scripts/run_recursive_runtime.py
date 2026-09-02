#!/usr/bin/env python3
"""Start or resume Recursive Runtime with a selected visual adapter."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from interactive_file_adapter import InteractiveFileAdapter
from production_visual_adapter import (
    ProductionVisualAdapter,
    build_production_runtime_adapters,
)
from recursive_runtime import (
    RecursiveRuntime,
    RuntimeAdapters,
    RuntimeConfig,
)
from vlm_client import VLMClientConfig, create_configured_vlm_client


def build_interactive_adapters(run_dir: Path) -> RuntimeAdapters:
    return RuntimeAdapters(
        router=InteractiveFileAdapter(run_dir, "router"),
        structural_split=InteractiveFileAdapter(run_dir, "structural_split"),
        expand_instances=InteractiveFileAdapter(run_dir, "expand_instances"),
        semantic_decompose=InteractiveFileAdapter(run_dir, "semantic_decompose"),
    )


def build_adapters(
    adapter: str,
    run_dir: Path,
    model: str | None = None,
) -> RuntimeAdapters:
    if adapter == "interactive":
        return build_interactive_adapters(run_dir)
    config = VLMClientConfig.from_env(model_override=model)
    client = create_configured_vlm_client(config)
    return build_production_runtime_adapters(ProductionVisualAdapter(client))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start or resume Stage2-A Recursive Runtime with a visual adapter."
    )
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--root-node-crop", type=Path)
    parser.add_argument("--root-id", default="root")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--adapter",
        choices=("interactive", "production"),
        default="interactive",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override STAGE2A_VLM_MODEL for this run.",
    )
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
    parser.add_argument(
        "--max-node-retries",
        type=int,
        default=2,
        help="maximum requeues after the initial node attempt (default: 2)",
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
    try:
        adapters = build_adapters(args.adapter, args.run_dir, model=args.model)
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
                    max_node_retries=args.max_node_retries,
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
