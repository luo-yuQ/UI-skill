#!/usr/bin/env python3
"""Single-process, level-by-level Stage2-A Recursive Runtime v0.1."""

from __future__ import annotations

import copy
import json
import shutil
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import resolve_terminal_state as terminal_resolver
import validate_expand_instances
import validate_node_route
import validate_semantic_decomposition
import validate_structural_split
from interactive_file_adapter import WaitingForAdapter
from prepare_analysis_input import DEFAULT_MAX_WIDTH, prepare_analysis_input
from runtime_geometry import (
    analysis_bbox_to_crop_bbox,
    create_child_node_images,
    read_image_size,
)


RUNTIME_VERSION = "0.1"
RUNTIME_CONCURRENCY_VERSION = "0.1"
RUNTIME_CONCURRENCY_NAME = f"Runtime Concurrency v{RUNTIME_CONCURRENCY_VERSION}"
NODE_STATUSES = frozenset(
    {"pending", "running", "ready", "done", "deferred", "failed", "blocked"}
)
ACTIONS = frozenset({"structural_split", "expand_instances", "semantic_decompose", "stop"})
DEFAULT_REPEATED_INSTANCE_SEMANTIC_LIMIT = 2
DEFAULT_MAX_CONCURRENCY = 2
VALIDATION_MODES = frozenset({"mechanics", "real_image"})
REAL_IMAGE_ADAPTER_TYPES = frozenset({"interactive_visual", "production_visual"})


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
class RootInput:
    """Ordered Level-0 input for multi-root Runtime initialization."""

    root_id: str
    root_node_crop: Path

    def __post_init__(self) -> None:
        if not isinstance(self.root_id, str) or not self.root_id.strip():
            raise ValueError("root_id must be a non-empty string")
        object.__setattr__(self, "root_node_crop", Path(self.root_node_crop))


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
    validation_mode: str = "mechanics"
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY

    def __post_init__(self) -> None:
        value = self.repeated_instance_semantic_limit
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(
                "repeated_instance_semantic_limit must be an integer >= 0 or None"
            )
        if self.validation_mode not in VALIDATION_MODES:
            raise ValueError(
                f"validation_mode must be one of {sorted(VALIDATION_MODES)}"
            )
        if type(self.max_concurrency) is not int or self.max_concurrency < 1:
            raise ValueError("max_concurrency must be an integer >= 1")


@dataclass
class AdapterExecutionResult:
    """One adapter response computed without committing Runtime state."""

    adapter_kind: str
    filename: str
    result: dict[str, Any]
    adapter: Any
    validated: bool = False


@dataclass
class NodeExecutionResult:
    """Internal compute result committed later by the Runtime thread."""

    node: "NodeRecord"
    original_status: str
    outcome: str
    adapter_results: list[AdapterExecutionResult]
    pending_request: dict[str, str] | None = None


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
        restored = {"parent_id": None, "produced_by": None, **value}
        return cls(**restored)


@dataclass
class RuntimeState:
    current_depth: int = 0
    current_level_queue: list[str] | None = None
    next_level_queue: list[str] | None = None
    processed_nodes: list[str] | None = None
    deferred_nodes: list[str] | None = None
    failed_nodes: list[str] | None = None
    semantic_warnings: list[dict[str, str]] | None = None
    pending_adapter_request: dict[str, str] | None = None
    next_request_number: int = 1
    real_visual_inference_used: bool = False

    def __post_init__(self) -> None:
        self.current_level_queue = list(self.current_level_queue or [])
        self.next_level_queue = list(self.next_level_queue or [])
        self.processed_nodes = list(self.processed_nodes or [])
        self.deferred_nodes = list(self.deferred_nodes or [])
        self.failed_nodes = list(self.failed_nodes or [])
        self.semantic_warnings = copy.deepcopy(self.semantic_warnings or [])
        self.pending_adapter_request = copy.deepcopy(self.pending_adapter_request)
        if type(self.current_depth) is not int or self.current_depth < 0:
            raise ValueError("current_depth must be an integer >= 0")
        if type(self.next_request_number) is not int or self.next_request_number < 1:
            raise ValueError("next_request_number must be an integer >= 1")
        if type(self.real_visual_inference_used) is not bool:
            raise ValueError("real_visual_inference_used must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RuntimeState":
        allowed = {field.name for field in fields(cls)}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unsupported Runtime State fields: {sorted(unknown)}")
        return cls(**value)


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

    def root_ids(self) -> list[str]:
        return [
            node.node_id for node in self._nodes.values() if node.parent_id is None
        ]

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

    @classmethod
    def load(cls, tree_path: Path, nodes_root: Path) -> "NodeStore":
        try:
            snapshot = json.loads(Path(tree_path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"unable to load tree snapshot {tree_path}: {exc}") from exc
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("nodes"), list):
            raise ValueError("invalid tree snapshot: nodes must be an array")
        store = cls(tree_path, nodes_root)
        for value in snapshot["nodes"]:
            if not isinstance(value, dict):
                raise ValueError("invalid tree snapshot: Node Record must be an object")
            store.add(NodeRecord.from_dict(value))
        persisted_children = snapshot.get("children")
        if persisted_children != store._children:
            raise ValueError("invalid tree snapshot: parent-child relations are inconsistent")
        return store


class RecursiveRuntime:
    """Execute frozen Stage2-A actions with deterministic per-level commits."""

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
        self._validate_adapter_types()

    def _adapter_types(self) -> dict[str, str]:
        return {
            name: (
                "unavailable"
                if getattr(self.adapters, name) is None
                else str(getattr(getattr(self.adapters, name), "adapter_type", "custom"))
            )
            for name in (
                "router",
                "structural_split",
                "expand_instances",
                "semantic_decompose",
            )
        }

    def _validate_adapter_types(self) -> None:
        if self.config.validation_mode != "real_image":
            return
        invalid = {
            name: adapter_type
            for name, adapter_type in self._adapter_types().items()
            if adapter_type in {"fake", "fixture"}
        }
        if invalid:
            raise ValueError(
                "real_image validation rejects non-visual adapters: "
                + ", ".join(f"{name}={kind}" for name, kind in invalid.items())
            )

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
        return cls.create_multi(
            run_dir=run_dir,
            roots=[RootInput(root_id=root_id, root_node_crop=root_node_crop)],
            adapters=adapters,
            config=config,
        )

    @classmethod
    def create_multi(
        cls,
        *,
        run_dir: Path,
        roots: list[RootInput],
        adapters: RuntimeAdapters,
        config: RuntimeConfig | None = None,
    ) -> "RecursiveRuntime":
        ordered_roots = list(roots)
        if not ordered_roots:
            raise ValueError("multi-root Runtime requires at least one root")
        if any(not isinstance(root, RootInput) for root in ordered_roots):
            raise ValueError("roots must contain only RootInput values")
        root_ids = [root.root_id for root in ordered_roots]
        if len(root_ids) != len(set(root_ids)):
            raise ValueError("duplicate root_id in multi-root Runtime input")

        runtime = cls(run_dir, adapters, config)
        for root_input in ordered_roots:
            root_dir = runtime.store.node_directory(root_input.root_id)
            root_dir.mkdir(parents=True, exist_ok=True)
            crop_path = root_dir / "node-crop.png"
            analysis_path = root_dir / "analysis-image.png"
            metadata_path = root_dir / "analysis-image-meta.json"
            try:
                shutil.copy2(root_input.root_node_crop, crop_path)
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
                node_id=root_input.root_id,
                parent_id=None,
                depth=0,
                produced_by=None,
                node_crop=runtime._relative(crop_path),
                analysis_image=runtime._relative(analysis_path),
                status="pending",
            )
            runtime.store.add(root)
            runtime.state.current_level_queue.append(root_input.root_id)
        runtime._persist()
        return runtime

    @classmethod
    def load(
        cls,
        *,
        run_dir: Path,
        adapters: RuntimeAdapters,
        config: RuntimeConfig | None = None,
    ) -> "RecursiveRuntime":
        run_dir = Path(run_dir)
        if config is None:
            try:
                manifest = json.loads(
                    (run_dir / "run-manifest.json").read_text(encoding="utf-8")
                )
                config = RuntimeConfig(**manifest["config"])
            except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ValueError(f"unable to load Runtime config: {exc}") from exc
        runtime = cls(run_dir, adapters, config)
        runtime.store = NodeStore.load(run_dir / "tree.json", run_dir / "nodes")
        try:
            state_data = json.loads(
                (run_dir / "runtime-state.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"unable to load Runtime State: {exc}") from exc
        if not isinstance(state_data, dict):
            raise ValueError("invalid Runtime State: root must be an object")
        runtime.state = RuntimeState.from_dict(state_data)
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
        adapter_type = getattr(adapter, "adapter_type", "custom")
        if (
            self.config.validation_mode == "real_image"
            and adapter_type not in REAL_IMAGE_ADAPTER_TYPES
        ):
            raise RuntimeError(
                "real_image validation requires an interactive_visual or "
                f"production_visual adapter for {name}, got {adapter_type!r}"
            )
        return adapter

    def _bind_adapter_request(
        self,
        adapter: Any,
        *,
        node: NodeRecord,
        adapter_kind: str,
        analysis_image: Path,
        request_id: str | None = None,
    ) -> None:
        bind = getattr(adapter, "bind_request", None)
        if bind is None:
            return
        if request_id is None:
            pending = self.state.pending_adapter_request
            if pending is not None:
                if (
                    pending.get("node_id") != node.node_id
                    or pending.get("adapter_kind") != adapter_kind
                ):
                    raise ValueError(
                        "pending adapter request does not match the current Node/action"
                    )
                request_id = pending["request_id"]
            else:
                request_id = f"req_{self.state.next_request_number:06d}"
        bind(
            request_id=request_id,
            node_id=node.node_id,
            node_role=node.node_role,
            adapter_kind=adapter_kind,
            analysis_image=self._relative(analysis_image),
        )

    def _adapter_result_valid(self, adapter: Any) -> None:
        mark_consumed = getattr(adapter, "mark_consumed", None)
        if mark_consumed is not None:
            mark_consumed()
            self.state.pending_adapter_request = None
            self.state.real_visual_inference_used = True
        elif getattr(adapter, "adapter_type", None) == "production_visual":
            self.state.real_visual_inference_used = True

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
        adapter = self._require_adapter("router")
        self._bind_adapter_request(
            adapter, node=node, adapter_kind="router", analysis_image=analysis_image
        )
        result = adapter.route(analysis_image)
        self._save_adapter_result(node, result, "router-result.json")
        self._raise_validation_errors(
            "Router", validate_node_route.validate_document(result)
        )
        self._adapter_result_valid(adapter)
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
        adapter = self._require_adapter("structural_split")
        self._bind_adapter_request(
            adapter,
            node=node,
            adapter_kind="structural_split",
            analysis_image=analysis_image,
        )
        result = adapter.run(analysis_image)
        self._save_adapter_result(node, result, "strategy-result.json")
        self._raise_validation_errors(
            "structural_split",
            validate_structural_split.validate_document(result, analysis_image),
        )
        self._adapter_result_valid(adapter)
        self._commit_structural_split_result(node, result)

    def _commit_structural_split_result(
        self, node: NodeRecord, result: dict[str, Any]
    ) -> None:
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
        adapter = self._require_adapter("expand_instances")
        self._bind_adapter_request(
            adapter,
            node=node,
            adapter_kind="expand_instances",
            analysis_image=analysis_image,
        )
        result = adapter.run(analysis_image)
        self._save_adapter_result(node, result, "strategy-result.json")
        self._raise_validation_errors(
            "expand_instances",
            validate_expand_instances.validate_document(result, analysis_image),
        )
        self._adapter_result_valid(adapter)
        self._commit_expand_instances_result(node, result)

    def _commit_expand_instances_result(
        self, node: NodeRecord, result: dict[str, Any]
    ) -> None:
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
        adapter = self._require_adapter("semantic_decompose")
        self._bind_adapter_request(
            adapter,
            node=node,
            adapter_kind="semantic_decompose",
            analysis_image=analysis_image,
        )
        result = adapter.run(analysis_image)
        self._save_adapter_result(node, result, "strategy-result.json")
        self._raise_validation_errors(
            "semantic_decompose",
            validate_semantic_decomposition.validate_document(result, analysis_image),
        )
        self._adapter_result_valid(adapter)
        self._apply_semantic_parent_result(node, result)
        self._commit_semantic_decompose_result(node, result)

    @staticmethod
    def _apply_semantic_parent_result(
        node: NodeRecord, result: dict[str, Any]
    ) -> None:
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

    def _commit_semantic_decompose_result(
        self, node: NodeRecord, result: dict[str, Any]
    ) -> None:
        if result["decision"] == "stop_as_asset":
            return
        for source in result["children"]:
            self._create_asset_child(parent=node, source=source)

    def _compute_node(
        self,
        node_snapshot: NodeRecord,
        *,
        request_id: str,
    ) -> NodeExecutionResult:
        """Compute one node from an isolated snapshot without Runtime commits."""

        node = copy.deepcopy(node_snapshot)
        original_status = node.status
        adapter_results: list[AdapterExecutionResult] = []
        if node.status == "done":
            return NodeExecutionResult(node, original_status, "done", adapter_results)

        try:
            if node.status not in {"pending", "ready"}:
                raise ValueError(
                    f"node {node.node_id!r} cannot run from status {node.status!r}"
                )
            node.status = "running"
            node.error = None
            analysis_image = self._artifact(node.analysis_image, "analysis_image")
            self._deterministic_resolve(node)

            if node.requires_router:
                adapter = self._require_adapter("router")
                self._bind_adapter_request(
                    adapter,
                    node=node,
                    adapter_kind="router",
                    analysis_image=analysis_image,
                    request_id=request_id,
                )
                result = adapter.route(analysis_image)
                execution = AdapterExecutionResult(
                    "router", "router-result.json", result, adapter
                )
                adapter_results.append(execution)
                self._raise_validation_errors(
                    "Router", validate_node_route.validate_document(result)
                )
                execution.validated = True
                resolved = terminal_resolver.resolve_terminal_state(
                    node_role=result["node_role"]
                )
                node.node_role = resolved["node_role"]
                node.terminal = resolved["terminal"]
                node.next_action = resolved["next_action"]
                node.requires_router = False

            if node.next_action is None:
                raise ValueError("next_action was not resolved")
            node.status = "ready"

            if node.next_action != "stop":
                adapter = self._require_adapter(node.next_action)
                self._bind_adapter_request(
                    adapter,
                    node=node,
                    adapter_kind=node.next_action,
                    analysis_image=analysis_image,
                    request_id=request_id,
                )
                result = adapter.run(analysis_image)
                execution = AdapterExecutionResult(
                    node.next_action, "strategy-result.json", result, adapter
                )
                adapter_results.append(execution)
                if node.next_action == "structural_split":
                    self._raise_validation_errors(
                        "structural_split",
                        validate_structural_split.validate_document(
                            result, analysis_image
                        ),
                    )
                elif node.next_action == "expand_instances":
                    self._raise_validation_errors(
                        "expand_instances",
                        validate_expand_instances.validate_document(
                            result, analysis_image
                        ),
                    )
                elif node.next_action == "semantic_decompose":
                    self._raise_validation_errors(
                        "semantic_decompose",
                        validate_semantic_decomposition.validate_document(
                            result, analysis_image
                        ),
                    )
                    self._apply_semantic_parent_result(node, result)
                else:
                    raise ValueError(f"unsupported action: {node.next_action!r}")
                execution.validated = True
        except WaitingForAdapter as exc:
            node.status = "ready" if node.next_action is not None else original_status
            node.error = None
            return NodeExecutionResult(
                node,
                original_status,
                "waiting_for_adapter",
                adapter_results,
                copy.deepcopy(exc.pending_request),
            )
        except Exception as exc:
            node.status = "failed"
            node.error = str(exc)
            return NodeExecutionResult(node, original_status, "failed", adapter_results)

        node.status = "done"
        return NodeExecutionResult(node, original_status, "done", adapter_results)

    def _commit_node_execution(self, execution: NodeExecutionResult) -> str:
        """Commit one completed compute result on the Runtime thread."""

        node = execution.node
        if execution.original_status == "done":
            return "done"
        try:
            for adapter_result in execution.adapter_results:
                self._save_adapter_result(
                    node, adapter_result.result, adapter_result.filename
                )
                if not adapter_result.validated:
                    continue
                self._adapter_result_valid(adapter_result.adapter)
                if adapter_result.adapter_kind == "structural_split":
                    self._commit_structural_split_result(
                        node, adapter_result.result
                    )
                elif adapter_result.adapter_kind == "expand_instances":
                    self._commit_expand_instances_result(
                        node, adapter_result.result
                    )
                elif adapter_result.adapter_kind == "semantic_decompose":
                    self._commit_semantic_decompose_result(
                        node, adapter_result.result
                    )

            if execution.outcome == "waiting_for_adapter":
                pending = execution.pending_request
                if pending is None:
                    raise ValueError("waiting result is missing pending adapter request")
                existing = self.state.pending_adapter_request
                if existing is not None and existing != pending:
                    raise ValueError(
                        "interactive adapter changed the pending request identity"
                    )
                if existing is None:
                    self.state.next_request_number += 1
                self.state.pending_adapter_request = copy.deepcopy(pending)
            elif execution.outcome == "failed":
                if node.node_id not in self.state.failed_nodes:
                    self.state.failed_nodes.append(node.node_id)
            elif execution.outcome == "done":
                if node.node_id not in self.state.processed_nodes:
                    self.state.processed_nodes.append(node.node_id)
            else:
                raise ValueError(
                    f"unsupported node execution outcome: {execution.outcome!r}"
                )
        except Exception as exc:
            node.status = "failed"
            node.error = str(exc)
            if node.node_id not in self.state.failed_nodes:
                self.state.failed_nodes.append(node.node_id)
            self.store.update(node)
            self._persist()
            return "failed"

        self.store.update(node)
        self._persist()
        return execution.outcome

    def _node_requires_adapter_compute(self, node: NodeRecord) -> bool:
        if node.status not in {"pending", "ready"}:
            return False
        candidate = copy.deepcopy(node)
        try:
            self._deterministic_resolve(candidate)
        except Exception:
            return False
        return candidate.requires_router or candidate.next_action in {
            "structural_split",
            "expand_instances",
            "semantic_decompose",
        }

    def _current_level_supports_concurrency(self) -> bool:
        if (
            self.config.max_concurrency == 1
            or len(self.state.current_level_queue) < 2
            or self.state.pending_adapter_request is not None
        ):
            return False
        for adapter in (
            self.adapters.router,
            self.adapters.structural_split,
            self.adapters.expand_instances,
            self.adapters.semantic_decompose,
        ):
            if adapter is None:
                continue
            if getattr(adapter, "adapter_type", None) == "interactive_visual":
                return False
            if getattr(adapter, "mark_consumed", None) is not None:
                return False
        active_count = sum(
            self._node_requires_adapter_compute(self.store.get(node_id))
            for node_id in self.state.current_level_queue
        )
        return active_count > 1

    @staticmethod
    def _unexpected_future_failure(
        node_snapshot: NodeRecord, exc: BaseException
    ) -> NodeExecutionResult:
        node = copy.deepcopy(node_snapshot)
        original_status = node.status
        node.status = "failed"
        node.error = str(exc)
        return NodeExecutionResult(node, original_status, "failed", [])

    def _process_current_level_concurrently(self) -> str | None:
        """Compute one complete BFS level, then commit in queue order."""

        node_ids = list(self.state.current_level_queue)
        snapshots = {
            node_id: copy.deepcopy(self.store.get(node_id)) for node_id in node_ids
        }
        request_id = f"req_{self.state.next_request_number:06d}"
        futures: dict[str, Future[NodeExecutionResult]] = {}
        results: dict[str, NodeExecutionResult] = {}
        active_node_ids = [
            node_id
            for node_id in node_ids
            if self._node_requires_adapter_compute(snapshots[node_id])
        ]
        worker_count = min(self.config.max_concurrency, len(active_node_ids))

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for node_id in active_node_ids:
                futures[node_id] = executor.submit(
                    self._compute_node,
                    snapshots[node_id],
                    request_id=request_id,
                )
            for node_id in node_ids:
                if node_id not in futures:
                    results[node_id] = self._compute_node(
                        snapshots[node_id], request_id=request_id
                    )
            for node_id in active_node_ids:
                try:
                    results[node_id] = futures[node_id].result()
                except BaseException as exc:  # defensive scheduler boundary
                    results[node_id] = self._unexpected_future_failure(
                        snapshots[node_id], exc
                    )

        for node_id in node_ids:
            if not self.state.current_level_queue:
                raise ValueError("current level queue changed during concurrent compute")
            queued_node_id = self.state.current_level_queue.pop(0)
            if queued_node_id != node_id:
                raise ValueError(
                    "current level queue order changed during concurrent compute"
                )
            outcome = self._commit_node_execution(results[node_id])
            if outcome == "waiting_for_adapter":
                self.state.current_level_queue.insert(0, node_id)
                self._persist()
                return outcome
        return None

    def process_node(self, node_id: str) -> str:
        """Process one queued node; newly discovered nodes only enter next-level state."""

        node = self.store.get(node_id)
        if node.status == "done":
            return "done"
        if node.status not in {"pending", "ready"}:
            raise ValueError(
                f"node {node_id!r} cannot run from status {node.status!r}"
            )
        original_status = node.status
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
        except WaitingForAdapter as exc:
            existing = self.state.pending_adapter_request
            if existing is not None and existing != exc.pending_request:
                raise ValueError("interactive adapter changed the pending request identity")
            if existing is None:
                self.state.next_request_number += 1
            self.state.pending_adapter_request = copy.deepcopy(exc.pending_request)
            node.status = "ready" if node.next_action is not None else original_status
            node.error = None
            self.store.update(node)
            self._persist()
            return "waiting_for_adapter"
        except Exception as exc:
            node.status = "failed"
            node.error = str(exc)
            if node.node_id not in self.state.failed_nodes:
                self.state.failed_nodes.append(node.node_id)
            self.store.update(node)
            self._persist()
            return "failed"

        node.status = "done"
        if node.node_id not in self.state.processed_nodes:
            self.state.processed_nodes.append(node.node_id)
        self.store.update(node)
        self._persist()
        return "done"

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

    def _write_manifest(self, result: str, *, active_execution_complete: bool) -> None:
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
            "runtime_concurrency": RUNTIME_CONCURRENCY_NAME,
            "root_count": len(self.store.root_ids()),
            "root_ids": self.store.root_ids(),
            "config": asdict(self.config),
            "validation_mode": self.config.validation_mode,
            "adapter_types": self._adapter_types(),
            "real_visual_inference_used": self.state.real_visual_inference_used,
            "result": result,
            "active_execution_complete": active_execution_complete,
            "fully_decomposed": fully_decomposed,
            "pending_adapter_request": copy.deepcopy(
                self.state.pending_adapter_request
            ),
            "runtime_failures": runtime_failures,
            "semantic_warnings": copy.deepcopy(self.state.semantic_warnings),
        }
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._persist()

    def run(self) -> str:
        """Run with same-depth compute concurrency and a strict level barrier."""

        while self.state.current_level_queue or self.state.next_level_queue:
            if self._current_level_supports_concurrency():
                outcome = self._process_current_level_concurrently()
                if outcome == "waiting_for_adapter":
                    self._write_manifest(
                        "waiting_for_adapter", active_execution_complete=False
                    )
                    return "waiting_for_adapter"
            else:
                while self.state.current_level_queue:
                    node_id = self.state.current_level_queue.pop(0)
                    self._persist()
                    outcome = self.process_node(node_id)
                    if outcome == "waiting_for_adapter":
                        self.state.current_level_queue.insert(0, node_id)
                        self._persist()
                        self._write_manifest(
                            "waiting_for_adapter", active_execution_complete=False
                        )
                        return "waiting_for_adapter"
            self.advance_level()
        result = self._run_result()
        self._write_manifest(result, active_execution_complete=True)
        return result
