#!/usr/bin/env python3
"""Single-process, level-by-level Stage2-A Recursive Runtime v0.1."""

from __future__ import annotations

import copy
import json
import shutil
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import resolve_terminal_state as terminal_resolver
import validate_expand_instances
import validate_node_route
import validate_semantic_decomposition
import validate_structural_split
from prepare_analysis_input import DEFAULT_MAX_WIDTH, prepare_analysis_input
from runtime_geometry import (
    analysis_bbox_to_crop_bbox,
    create_child_node_images,
    read_image_size,
)


RUNTIME_VERSION = "0.1"
NODE_STATUSES = frozenset(
    {"pending", "running", "ready", "done", "deferred", "failed", "blocked"}
)
ACTIONS = frozenset({"structural_split", "expand_instances", "semantic_decompose", "stop"})
DEFAULT_REPEATED_INSTANCE_SEMANTIC_LIMIT = 2


class RouterAdapter(Protocol):
    def route(self, analysis_image: Path) -> dict[str, Any]: ...


class StructuralSplitAdapter(Protocol):
    def run(self, analysis_image: Path) -> dict[str, Any]: ...


class ExpandInstancesAdapter(Protocol):
    def run(self, analysis_image: Path) -> dict[str, Any]: ...


class SemanticDecomposeAdapter(Protocol):
    def run(self, analysis_image: Path) -> dict[str, Any]: ...


@dataclass
class RuntimeAdapters:
    router: RouterAdapter
    structural_split: StructuralSplitAdapter
    expand_instances: ExpandInstancesAdapter
    semantic_decompose: SemanticDecomposeAdapter


@dataclass(frozen=True)
class SemanticWarning:
    """Non-operative semantic quality note attached to a run summary."""

    node_id: str
    source: str
    type: str
    message: str

    def __post_init__(self) -> None:
        for name in ("node_id", "source", "type", "message"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"semantic warning {name} must be a non-empty string")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class RuntimeConfig:
    repeated_instance_semantic_limit: int | None = (
        DEFAULT_REPEATED_INSTANCE_SEMANTIC_LIMIT
    )

    def __post_init__(self) -> None:
        value = self.repeated_instance_semantic_limit
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(
                "repeated_instance_semantic_limit must be an integer >= 0 or None"
            )


@dataclass
class NodeRecord:
    node_id: str
    parent_id: str | None
    depth: int
    produced_by: str | None
    node_role: str | None = None
    terminal: bool = False
    next_action: str | None = None
    requires_router: bool = True
    node_crop: str | None = None
    analysis_image: str | None = None
    bbox_in_parent_analysis: dict[str, int] | None = None
    bbox_in_parent_crop: dict[str, int] | None = None
    status: str = "pending"
    source_instance_id: str | None = None
    instance_type: str | None = None
    partial_instance: bool | None = None
    taxonomy: str | None = None
    label: str | None = None
    confidence: float | None = None
    deferred_reason: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id:
            raise ValueError("node_id must be a non-empty string")
        if type(self.depth) is not int or self.depth < 0:
            raise ValueError("depth must be an integer >= 0")
        if self.status not in NODE_STATUSES:
            raise ValueError(f"unsupported node status: {self.status!r}")
        if self.next_action is not None and self.next_action not in ACTIONS:
            raise ValueError(f"unsupported next_action: {self.next_action!r}")

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "NodeRecord":
        allowed = {field.name for field in fields(cls)}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unsupported Node Record fields: {sorted(unknown)}")
        return cls(**value)


@dataclass
class RuntimeState:
    current_depth: int = 0
    current_level_queue: list[str] | None = None
    next_level_queue: list[str] | None = None
    processed_nodes: list[str] | None = None
    deferred_nodes: list[str] | None = None
    failed_nodes: list[str] | None = None
    semantic_warnings: list[dict[str, str]] | None = None

    def __post_init__(self) -> None:
        self.current_level_queue = list(self.current_level_queue or [])
        self.next_level_queue = list(self.next_level_queue or [])
        self.processed_nodes = list(self.processed_nodes or [])
        self.deferred_nodes = list(self.deferred_nodes or [])
        self.failed_nodes = list(self.failed_nodes or [])
        self.semantic_warnings = copy.deepcopy(self.semantic_warnings or [])
        if type(self.current_depth) is not int or self.current_depth < 0:
            raise ValueError("current_depth must be an integer >= 0")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NodeStore:
    """Minimal in-memory tree store with deterministic JSON persistence."""

    def __init__(self, tree_path: Path, nodes_root: Path) -> None:
        self.tree_path = Path(tree_path)
        self.nodes_root = Path(nodes_root)
        self._nodes: dict[str, NodeRecord] = {}
        self._children: dict[str, list[str]] = {}

    def get(self, node_id: str) -> NodeRecord:
        try:
            return self._nodes[node_id]
        except KeyError as exc:
            raise KeyError(f"unknown node_id: {node_id}") from exc

    def contains(self, node_id: str) -> bool:
        return node_id in self._nodes

    def add(self, node: NodeRecord) -> None:
        if node.node_id in self._nodes:
            raise ValueError(f"duplicate node_id: {node.node_id}")
        if node.parent_id is not None and node.parent_id not in self._nodes:
            raise ValueError(f"unknown parent_id: {node.parent_id}")
        self._nodes[node.node_id] = node
        self._children.setdefault(node.node_id, [])
        if node.parent_id is not None:
            self._children.setdefault(node.parent_id, []).append(node.node_id)

    def update(self, node: NodeRecord) -> None:
        if node.node_id not in self._nodes:
            raise KeyError(f"unknown node_id: {node.node_id}")
        self._nodes[node.node_id] = node

    def children_of(self, node_id: str) -> list[NodeRecord]:
        self.get(node_id)
        return [self.get(child_id) for child_id in self._children[node_id]]

    def restore_deferred(self, node_id: str) -> NodeRecord:
        node = self.get(node_id)
        if node.status != "deferred":
            raise ValueError(f"node {node_id!r} is not deferred")
        node.status = "pending"
        node.deferred_reason = None
        self.update(node)
        return node

    def node_directory(self, node_id: str) -> Path:
        return self.nodes_root / quote(node_id, safe="._-")

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_VERSION,
            "nodes": [node.to_dict() for node in self._nodes.values()],
            "children": copy.deepcopy(self._children),
        }

    def persist(self) -> None:
        self.tree_path.parent.mkdir(parents=True, exist_ok=True)
        self.tree_path.write_text(
            json.dumps(self.snapshot(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for node in self._nodes.values():
            directory = self.node_directory(node.node_id)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "node.json").write_text(
                json.dumps(node.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )


class RecursiveRuntime:
    """Execute frozen Stage2-A actions serially behind a per-level barrier."""

    def __init__(
        self,
        run_dir: Path,
        adapters: RuntimeAdapters,
        config: RuntimeConfig | None = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.adapters = adapters
        self.config = config or RuntimeConfig()
        self.state_path = self.run_dir / "runtime-state.json"
        self.manifest_path = self.run_dir / "run-manifest.json"
        self.store = NodeStore(self.run_dir / "tree.json", self.run_dir / "nodes")
        self.state = RuntimeState()

    @classmethod
    def create(
        cls,
        *,
        run_dir: Path,
        root_node_crop: Path,
        adapters: RuntimeAdapters,
        root_id: str = "root",
        config: RuntimeConfig | None = None,
    ) -> "RecursiveRuntime":
        runtime = cls(run_dir, adapters, config)
        root_dir = runtime.store.node_directory(root_id)
        root_dir.mkdir(parents=True, exist_ok=True)
        crop_path = root_dir / "node-crop.png"
        analysis_path = root_dir / "analysis-image.png"
        metadata_path = root_dir / "analysis-image-meta.json"
        try:
            shutil.copy2(root_node_crop, crop_path)
        except OSError as exc:
            raise ValueError(f"unable to copy root Node Crop: {exc}") from exc
        prepare_analysis_input(
            crop_path,
            analysis_path,
            metadata_path,
            max_width=DEFAULT_MAX_WIDTH,
            force_width=True,
        )
        root = NodeRecord(
            node_id=root_id,
            parent_id=None,
            depth=0,
            produced_by=None,
            node_crop=runtime._relative(crop_path),
            analysis_image=runtime._relative(analysis_path),
            status="pending",
        )
        runtime.store.add(root)
        runtime.state.current_level_queue.append(root_id)
        runtime._persist()
        return runtime

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.run_dir.resolve()).as_posix()

    def _artifact(self, value: str | None, name: str) -> Path:
        if value is None:
            raise ValueError(f"Node Record is missing {name}")
        path = Path(value)
        return path if path.is_absolute() else self.run_dir / path

    def _persist(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.store.persist()
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _save_adapter_result(
        self, node: NodeRecord, result: dict[str, Any], filename: str
    ) -> None:
        path = self.store.node_directory(node.node_id) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _raise_validation_errors(kind: str, errors: list[str]) -> None:
        if errors:
            raise ValueError(f"invalid {kind} adapter result:\n- " + "\n- ".join(errors))

    def _require_adapter(self, name: str) -> Any:
        adapter = getattr(self.adapters, name)
        if adapter is None:
            raise RuntimeError(
                f"adapter_unavailable: {name} adapter was not injected"
            )
        return adapter

    def _deterministic_resolve(self, node: NodeRecord) -> None:
        # Current semantic state outranks creation provenance. This matters when
        # an expand_instances child later becomes an asset via stop_as_asset.
        if node.node_role is not None:
            resolved = terminal_resolver.resolve_terminal_state(
                node_role=node.node_role
            )
            if node.next_action is not None:
                conflicts: list[str] = []
                if node.next_action != resolved["next_action"]:
                    conflicts.append(
                        f"next_action {node.next_action!r} != {resolved['next_action']!r}"
                    )
                if node.terminal is not resolved["terminal"]:
                    conflicts.append(
                        f"terminal {node.terminal!r} != {resolved['terminal']!r}"
                    )
                if node.requires_router is not resolved["requires_router"]:
                    conflicts.append(
                        "requires_router "
                        f"{node.requires_router!r} != {resolved['requires_router']!r}"
                    )
                if conflicts:
                    raise ValueError(
                        "current node state contract conflict: " + "; ".join(conflicts)
                    )
            node.terminal = resolved["terminal"]
            node.requires_router = resolved["requires_router"]
            node.next_action = resolved["next_action"]
            return
        if node.requires_router:
            return
        resolved = terminal_resolver.resolve_terminal_state(
            produced_by=node.produced_by,
            taxonomy=node.taxonomy,
        )
        node.node_role = resolved.get("node_role", node.node_role)
        node.terminal = resolved["terminal"]
        node.requires_router = resolved["requires_router"]
        node.next_action = resolved.get("next_action")

    def _route(self, node: NodeRecord, analysis_image: Path) -> None:
        result = self._require_adapter("router").route(analysis_image)
        self._save_adapter_result(node, result, "router-result.json")
        self._raise_validation_errors(
            "Router", validate_node_route.validate_document(result)
        )
        resolved = terminal_resolver.resolve_terminal_state(
            node_role=result["node_role"]
        )
        node.node_role = resolved["node_role"]
        node.terminal = resolved["terminal"]
        node.next_action = resolved["next_action"]
        node.requires_router = False

    def _new_child_id(self, parent: NodeRecord, source_id: str) -> str:
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("child id must be a non-empty string")
        return f"{parent.node_id}.{source_id}"

    def _create_recursive_child(
        self,
        *,
        parent: NodeRecord,
        source_id: str,
        bbox: dict[str, int],
        produced_by: str,
        label: str | None = None,
        confidence: float | None = None,
        source_instance_id: str | None = None,
        instance_type: str | None = None,
        partial_instance: bool | None = None,
    ) -> NodeRecord:
        node_id = self._new_child_id(parent, source_id)
        if self.store.contains(node_id):
            raise ValueError(f"duplicate node_id: {node_id}")
        child_dir = self.store.node_directory(node_id)
        crop_path = child_dir / "node-crop.png"
        analysis_path = child_dir / "analysis-image.png"
        metadata_path = child_dir / "analysis-image-meta.json"
        bbox_in_crop = create_child_node_images(
            parent_node_crop=self._artifact(parent.node_crop, "node_crop"),
            parent_analysis_image=self._artifact(parent.analysis_image, "analysis_image"),
            bbox_in_parent_analysis=bbox,
            child_node_crop=crop_path,
            child_analysis_image=analysis_path,
            child_analysis_metadata=metadata_path,
        )
        resolved = terminal_resolver.resolve_terminal_state(produced_by=produced_by)
        child = NodeRecord(
            node_id=node_id,
            parent_id=parent.node_id,
            depth=parent.depth + 1,
            produced_by=produced_by,
            node_role=resolved.get("node_role"),
            terminal=resolved["terminal"],
            next_action=resolved.get("next_action"),
            requires_router=resolved["requires_router"],
            node_crop=self._relative(crop_path),
            analysis_image=self._relative(analysis_path),
            bbox_in_parent_analysis=copy.deepcopy(bbox),
            bbox_in_parent_crop=bbox_in_crop,
            status="pending",
            source_instance_id=source_instance_id,
            instance_type=instance_type,
            partial_instance=partial_instance,
            label=label,
            confidence=confidence,
        )
        self.store.add(child)
        return child

    def _create_asset_child(
        self,
        *,
        parent: NodeRecord,
        source: dict[str, Any],
    ) -> NodeRecord:
        node_id = self._new_child_id(parent, source["id"])
        if self.store.contains(node_id):
            raise ValueError(f"duplicate node_id: {node_id}")
        bbox = copy.deepcopy(source["bbox"])
        bbox_in_crop = analysis_bbox_to_crop_bbox(
            bbox,
            read_image_size(self._artifact(parent.analysis_image, "analysis_image")),
            read_image_size(self._artifact(parent.node_crop, "node_crop")),
        )
        resolved = terminal_resolver.resolve_terminal_state(
            produced_by="semantic_decompose",
            taxonomy=source["taxonomy"],
        )
        child = NodeRecord(
            node_id=node_id,
            parent_id=parent.node_id,
            depth=parent.depth + 1,
            produced_by="semantic_decompose",
            node_role=resolved["node_role"],
            terminal=resolved["terminal"],
            next_action=resolved["next_action"],
            requires_router=resolved["requires_router"],
            bbox_in_parent_analysis=bbox,
            bbox_in_parent_crop=bbox_in_crop,
            status="done",
            taxonomy=source["taxonomy"],
            label=source["label"],
            confidence=source["confidence"],
        )
        self.store.add(child)
        return child

    def _run_structural_split(self, node: NodeRecord, analysis_image: Path) -> None:
        result = self._require_adapter("structural_split").run(analysis_image)
        self._save_adapter_result(node, result, "strategy-result.json")
        self._raise_validation_errors(
            "structural_split",
            validate_structural_split.validate_document(result, analysis_image),
        )
        for source in result["children"]:
            child = self._create_recursive_child(
                parent=node,
                source_id=source["id"],
                bbox=source["bbox"],
                produced_by="structural_split",
                label=source["label"],
                confidence=source["confidence"],
            )
            self.state.next_level_queue.append(child.node_id)

    def _run_expand_instances(self, node: NodeRecord, analysis_image: Path) -> None:
        result = self._require_adapter("expand_instances").run(analysis_image)
        self._save_adapter_result(node, result, "strategy-result.json")
        self._raise_validation_errors(
            "expand_instances",
            validate_expand_instances.validate_document(result, analysis_image),
        )
        limit = self.config.repeated_instance_semantic_limit
        for index, source in enumerate(result["instances"]):
            child = self._create_recursive_child(
                parent=node,
                source_id=source["id"],
                bbox=source["bbox"],
                produced_by="expand_instances",
                source_instance_id=source["id"],
                instance_type=result["instance_type"],
                partial_instance=source["partial_instance"],
                confidence=source["confidence"],
            )
            if limit is not None and index >= limit:
                child.status = "deferred"
                child.deferred_reason = "repeated_instance_semantic_limit"
                self.state.deferred_nodes.append(child.node_id)
                self.store.update(child)
            else:
                self.state.next_level_queue.append(child.node_id)

    def _run_semantic_decompose(self, node: NodeRecord, analysis_image: Path) -> None:
        result = self._require_adapter("semantic_decompose").run(analysis_image)
        self._save_adapter_result(node, result, "strategy-result.json")
        self._raise_validation_errors(
            "semantic_decompose",
            validate_semantic_decomposition.validate_document(result, analysis_image),
        )
        if result["decision"] == "stop_as_asset":
            resolved = terminal_resolver.resolve_terminal_state(
                produced_by="semantic_decompose",
                taxonomy=result["asset_taxonomy"],
            )
            node.node_role = resolved["node_role"]
            node.terminal = resolved["terminal"]
            node.next_action = resolved["next_action"]
            node.requires_router = resolved["requires_router"]
            node.taxonomy = result["asset_taxonomy"]
            return
        for source in result["children"]:
            self._create_asset_child(parent=node, source=source)

    def process_node(self, node_id: str) -> None:
        """Process one queued node; newly discovered nodes only enter next-level state."""

        node = self.store.get(node_id)
        if node.status == "done":
            return
        if node.status not in {"pending", "ready"}:
            raise ValueError(
                f"node {node_id!r} cannot run from status {node.status!r}"
            )
        node.status = "running"
        node.error = None
        self.store.update(node)
        self._persist()
        try:
            analysis_image = self._artifact(node.analysis_image, "analysis_image")
            self._deterministic_resolve(node)
            if node.requires_router:
                self._route(node, analysis_image)
            if node.next_action is None:
                raise ValueError("next_action was not resolved")
            node.status = "ready"
            self.store.update(node)
            self._persist()
            if node.next_action == "stop":
                pass
            elif node.next_action == "structural_split":
                self._run_structural_split(node, analysis_image)
            elif node.next_action == "expand_instances":
                self._run_expand_instances(node, analysis_image)
            elif node.next_action == "semantic_decompose":
                self._run_semantic_decompose(node, analysis_image)
            else:
                raise ValueError(f"unsupported action: {node.next_action!r}")
        except Exception as exc:
            node.status = "failed"
            node.error = str(exc)
            if node.node_id not in self.state.failed_nodes:
                self.state.failed_nodes.append(node.node_id)
            self.store.update(node)
            self._persist()
            return

        node.status = "done"
        if node.node_id not in self.state.processed_nodes:
            self.state.processed_nodes.append(node.node_id)
        self.store.update(node)
        self._persist()

    def advance_level(self) -> bool:
        """Advance only after the current per-level queue is fully consumed."""

        if self.state.current_level_queue:
            return False
        if not self.state.next_level_queue:
            return False
        self.state.current_level_queue = self.state.next_level_queue
        self.state.next_level_queue = []
        self.state.current_depth += 1
        self._persist()
        return True

    def restore_deferred(self, node_id: str, *, schedule: bool = True) -> NodeRecord:
        """Restore one deferred node to pending and optionally schedule an idle run."""

        node = self.store.restore_deferred(node_id)
        self.state.deferred_nodes = [
            value for value in self.state.deferred_nodes if value != node_id
        ]
        if schedule:
            if self.state.current_level_queue or self.state.next_level_queue:
                raise ValueError("automatic restore scheduling requires idle active queues")
            self.state.current_depth = node.depth
            self.state.current_level_queue.append(node_id)
        self._persist()
        return node

    def add_semantic_warning(
        self,
        *,
        node_id: str,
        source: str,
        warning_type: str,
        message: str,
    ) -> dict[str, str]:
        """Record semantic review metadata without mutating or rescheduling a node."""

        self.store.get(node_id)
        warning = SemanticWarning(
            node_id=node_id,
            source=source,
            type=warning_type,
            message=message,
        ).to_dict()
        self.state.semantic_warnings.append(warning)
        self._persist()
        return copy.deepcopy(warning)

    def _run_result(self) -> str:
        nodes = self.store.snapshot()["nodes"]
        if any(node["status"] == "blocked" for node in nodes):
            return "blocked"
        if any(node["status"] == "failed" for node in nodes):
            return "failed"
        if any(node["status"] == "deferred" for node in nodes):
            return "complete_with_deferred"
        return "complete"

    def run(self) -> str:
        """Run serially with a strict barrier between each node depth."""

        while self.state.current_level_queue or self.state.next_level_queue:
            while self.state.current_level_queue:
                node_id = self.state.current_level_queue.pop(0)
                self._persist()
                self.process_node(node_id)
            self.advance_level()
        result = self._run_result()
        fully_decomposed = result == "complete"
        runtime_failures = [
            {
                "node_id": node_id,
                "message": self.store.get(node_id).error or "runtime failure",
            }
            for node_id in self.state.failed_nodes
        ]
        manifest = {
            "schema_version": RUNTIME_VERSION,
            "runtime": "recursive-runtime-v0.1",
            "config": asdict(self.config),
            "result": result,
            "active_execution_complete": True,
            "fully_decomposed": fully_decomposed,
            "runtime_failures": runtime_failures,
            "semantic_warnings": copy.deepcopy(self.state.semantic_warnings),
        }
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._persist()
        return result
