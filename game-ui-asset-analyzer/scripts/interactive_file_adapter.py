#!/usr/bin/env python3
"""File request/response bridge for interactive Stage2-A visual adapters."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
RESPONSE_SCHEMA_PATH = ROOT / "schemas" / "interactive-adapter-response.schema.json"
ADAPTER_CONTRACTS = {
    "router": "node-router-v0.1",
    "structural_split": "structural-split-v0.1",
    "expand_instances": "expand-instances-v0.1",
    "semantic_decompose": "semantic-decompose-v0.1",
}


@dataclass(frozen=True)
class AdapterRequestContext:
    request_id: str
    node_id: str
    adapter_kind: str
    analysis_image: str
    request_path: str
    response_path: str

    def to_pending_dict(self) -> dict[str, str]:
        return {
            "request_id": self.request_id,
            "node_id": self.node_id,
            "adapter_kind": self.adapter_kind,
            "analysis_image": self.analysis_image,
            "request_path": self.request_path,
            "response_path": self.response_path,
        }


class WaitingForAdapter(Exception):
    """Normal control signal indicating that an interactive response is pending."""

    def __init__(self, pending_request: dict[str, str]) -> None:
        super().__init__("waiting_for_adapter")
        self.pending_request = copy.deepcopy(pending_request)


def load_response_schema() -> dict[str, Any]:
    schema = json.loads(RESPONSE_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def validate_response_envelope(
    response: Any,
    *,
    request_id: str,
    adapter_kind: str,
) -> list[str]:
    """Validate the bridge envelope; frozen result validation remains in Runtime."""

    validator = Draft202012Validator(load_response_schema())
    errors = [
        f"{list(error.absolute_path)}: {error.message}"
        for error in sorted(
            validator.iter_errors(response),
            key=lambda item: (
                tuple(str(part) for part in item.absolute_path),
                item.message,
            ),
        )
    ]
    if not isinstance(response, dict):
        return errors
    if response.get("request_id") != request_id:
        errors.append(
            "request_id mismatch: "
            f"expected {request_id!r}, got {response.get('request_id')!r}"
        )
    if response.get("adapter_kind") != adapter_kind:
        errors.append(
            "adapter_kind mismatch: "
            f"expected {adapter_kind!r}, got {response.get('adapter_kind')!r}"
        )
    return errors


class InteractiveFileAdapter:
    """Synchronous Protocol adapter backed by durable request/response JSON files."""

    adapter_type = "interactive_visual"

    def __init__(self, run_dir: Path, adapter_kind: str) -> None:
        if adapter_kind not in ADAPTER_CONTRACTS:
            raise ValueError(f"unsupported interactive adapter kind: {adapter_kind!r}")
        self.run_dir = Path(run_dir)
        self.adapter_kind = adapter_kind
        self.requests_dir = self.run_dir / "adapter-requests"
        self.responses_dir = self.run_dir / "adapter-responses"
        self._context: AdapterRequestContext | None = None
        self.consumed_response_count = 0

    def bind_request(
        self,
        *,
        request_id: str,
        node_id: str,
        node_role: str | None = None,
        adapter_kind: str | None = None,
        analysis_image: str,
    ) -> None:
        del node_role
        if adapter_kind is not None and adapter_kind != self.adapter_kind:
            raise ValueError("interactive adapter kind context mismatch")
        request_path = self.requests_dir / f"{request_id}.json"
        response_path = self.responses_dir / f"{request_id}.json"
        self._context = AdapterRequestContext(
            request_id=request_id,
            node_id=node_id,
            adapter_kind=self.adapter_kind,
            analysis_image=analysis_image,
            request_path=request_path.relative_to(self.run_dir).as_posix(),
            response_path=response_path.relative_to(self.run_dir).as_posix(),
        )

    def _require_context(self) -> AdapterRequestContext:
        if self._context is None:
            raise RuntimeError("interactive adapter request context was not bound")
        return self._context

    def _write_request_once(self, context: AdapterRequestContext) -> None:
        request_path = self.run_dir / context.request_path
        if request_path.exists():
            return
        request_path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": "0.1",
            "request_id": context.request_id,
            "node_id": context.node_id,
            "adapter_kind": context.adapter_kind,
            "analysis_image": context.analysis_image,
            "contract": ADAPTER_CONTRACTS[context.adapter_kind],
            "status": "waiting",
        }
        request_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _exchange(self, analysis_image: Path) -> dict[str, Any]:
        context = self._require_context()
        expected_image = (self.run_dir / context.analysis_image).resolve()
        if Path(analysis_image).resolve() != expected_image:
            raise ValueError("interactive adapter Analysis Image context mismatch")
        response_path = self.run_dir / context.response_path
        if not response_path.is_file():
            self._write_request_once(context)
            raise WaitingForAdapter(context.to_pending_dict())
        try:
            response = json.loads(response_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"adapter_response_invalid: {exc}") from exc
        errors = validate_response_envelope(
            response,
            request_id=context.request_id,
            adapter_kind=context.adapter_kind,
        )
        if errors:
            raise ValueError("adapter_response_invalid: " + "; ".join(errors))
        return copy.deepcopy(response["result"])

    def mark_consumed(self) -> None:
        context = self._require_context()
        request_path = self.run_dir / context.request_path
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"unable to mark adapter request consumed: {exc}") from exc
        request["status"] = "consumed"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.consumed_response_count += 1

    def route(self, analysis_image: Path) -> dict[str, Any]:
        if self.adapter_kind != "router":
            raise ValueError("route() is valid only for the router adapter kind")
        return self._exchange(analysis_image)

    def run(self, analysis_image: Path) -> dict[str, Any]:
        if self.adapter_kind == "router":
            raise ValueError("run() is not valid for the router adapter kind")
        return self._exchange(analysis_image)
