# STAGE2 AS-IS Audit

审计范围：当前工作树中的实际 Python、JSON Schema、运行时 Prompt、CLI/Runner、测试与已有 run 产物。未以 README 或历史文档作为实现结论来源。除本报告外，本次未修改任何文件。

## 1. Executive Summary

Stage2 当前可确认的主运行链只有 Stage2-A Recursive Runtime。它从一个 root node crop 创建树，按 BFS 分层调度，对需要路由的节点调用 VLM Router 取得 `node_role`，再由工程固定映射得到 `next_action`，执行 `structural_split`、`expand_instances`、`semantic_decompose` 或 `stop`。

```text
root node crop
  -> analysis image (width fixed to 1024)
  -> Router VLM (only when requires_router)
  -> node_role
  -> ROLE_ACTION_MAP in Python
  -> structural_split | expand_instances | semantic_decompose | stop
  -> child crops / child analysis images / BFS next level
  -> terminal assets, deferred instances, failed nodes, or completion
```

关键结论：

- VLM 不输出 `next_action`。VLM Router 只输出 `node_role`；Python `ROLE_ACTION_MAP` 确定最终 route/action。
- 四个 Router role 与四个 action 是一对一硬映射：`structural_group -> structural_split`、`repeated_group -> expand_instances`、`component_instance -> semantic_decompose`、`asset -> stop`。
- `structural_split` child 下一层重新 Router；`expand_instances` child 被工程硬设为 `component_instance -> semantic_decompose`，不重新 Router；`semantic_decompose` child 被工程硬设为 `asset -> stop`。
- `semantic_decompose` 内部还存在第二个 VLM 判断：`decompose` 或 `stop_as_asset`。后者把当前 `component_instance` 节点原地改为 terminal asset。
- 当前 Runtime 是按层屏障的 BFS，可在同层并发计算并按原队列顺序提交。没有显式 `max_depth`、max node 数或循环检测；终止主要依赖模型输出与有限实例 defer 策略。
- Stage2-B1 已有 extraction plan、executor、quality gate 合同，但没有在 Stage2-A Runtime 或 `build_asset_analysis.py` 的主链中被调用。Stage2-C/D 没有统一生产主链，只存在独立 mask/repair PoC。
- Stage2-A production VLM 使用 Responses API，实际 payload 为 `temperature=0`、`top_p=1`，而非 `top_p=0`。传到 client 的 `response_schema` 在 client 内被丢弃，因此 provider 请求未启用 JSON Schema structured output；本地 validator 在响应后校验。

## 2. Repository State

| 项目 | 当前状态 |
| --- | --- |
| repository root | `D:\Third_Test_1\UI-skill` |
| branch | `stage0/ui-generation-foundation` |
| HEAD commit | `85b808bd42c02d302600cc29287c55bf462e1b8f` |
| git status | 空输出，审计开始时工作树 clean |
| 未提交的 Stage2 修改 | 未发现 |

主要实际目录：

```text
game-ui-asset-analyzer/
  scripts/                Stage2-A Runtime、Adapter、Validator、VLM client、旧平面工具
  references/             四个 production prompt 与策略契约
  schemas/                Stage2-A JSON Schema
  tests/                  Stage2-A 测试
  stage2_b/               ExtractionPlan、Executor、QualityGate 与 B1 测试
game-ui-asset-extractor/
  scripts/                独立 region-mask / text-audit / image-repair PoC
runs/                     历史 run 及 Stage2-A/Stage2-B 实验产物
```

证据：`game-ui-asset-analyzer/scripts/run_recursive_runtime.py:23-115`，`game-ui-asset-analyzer/scripts/recursive_runtime.py:405-419`，`game-ui-asset-analyzer/stage2_b/`。

## 3. Stage2 End-to-End Architecture

### 实际串联状态

```text
CLI: scripts/run_recursive_runtime.py::main
  input: --root-node-crop, --run-dir, --adapter
  output: Runtime result + run directory
  |
  v
scripts/recursive_runtime.py::RecursiveRuntime.create/load/run
  |
  +--> Router (ProductionVisualAdapter.route)
  |      input: node analysis-image.png
  |      output: node_role/confidence/reason
  |
  +--> Python mapping + terminal resolver
  |      output: next_action / terminal / requires_router
  |
  +--> structural_split -> recursive structural children -> Router next BFS level
  +--> expand_instances -> instance children -> component_instance semantic_decompose
  +--> semantic_decompose -> terminal asset children OR current node stop_as_asset
  +--> stop -> node complete
```

Stage2-A 到 B 不是自动主链：Stage2-A Runtime 创建 `NodeRecord` 和 terminal asset nodes，但没有构造 `stage2_b.extraction_plan.AssetLeaf`，没有调用 `ExtractionPlanner.plan()`、`ExtractionExecutor.execute()` 或 `ExtractionQualityGate.evaluate()`。

独立但未接入的下游链：

```text
Stage2-B1 (独立 Python API / contract tests)
AssetLeaf -> ExtractionPlanner -> ExtractionPlan
  -> ExtractionExecutor -> ExtractionArtifact
  -> ExtractionQualityGate -> QualityGateResult

独立 PoC（未由 B1 或 A Runtime 调用）
OCR/source -> ui_vlm_region_mask_poc.py -> region mask
OCR/source -> ui_vlm_text_auditor.py -> OpenCV text inpaint
source/mask -> ui_image_clean_repair_poc.py -> remote image generation repair
```

证据：`scripts/run_recursive_runtime.py:82-112`，`scripts/recursive_runtime.py:940-1033`，`scripts/build_asset_analysis.py:64-166`，`stage2_b/extraction_plan.py:90-243`，`stage2_b/extraction_executor.py:55-138`。

## 4. Stage2-A Recursive Tree Pipeline

### 4.1 Root 初始化与图像准备

```text
RecursiveRuntime.create()
  -> create_multi()
  -> copy root input to nodes/<root>/node-crop.png
  -> prepare_analysis_input(..., max_width=1024, force_width=True)
  -> nodes/<root>/analysis-image.png + analysis-image-meta.json
  -> NodeRecord(depth=0, status=pending)
  -> RuntimeState.current_level_queue.append(root)
```

输入是 CLI 的 `--root-node-crop`；输出是 root `NodeRecord` 与图片产物；调用者是 `run_recursive_runtime.py::main`。`create_multi()` 是 Python API 多 root 入口，CLI 仅暴露单 root。

证据：`scripts/run_recursive_runtime.py:40-107`，`scripts/recursive_runtime.py:451-516`，`scripts/prepare_analysis_input.py:15-34,73-122`。

### 4.2 单节点真实执行链

```text
NodeRecord (pending/ready)
  -> RecursiveRuntime.process_node() / _compute_node()
  -> _deterministic_resolve()
  -> requires_router?
       yes -> _route() -> adapter.route(analysis image) -> validate route
       no  -> preserve/derive deterministic state
  -> resolve_terminal_state / ROLE_ACTION_MAP
  -> next_action
       structural_split   -> _run_structural_split -> structural children
       expand_instances   -> _run_expand_instances -> instance children/deferred
       semantic_decompose -> _run_semantic_decompose -> asset children or parent asset
       stop               -> no strategy adapter
  -> node status=done -> processed_nodes
```

`process_node()` 是串行节点入口；同层并发时 `_process_current_level_concurrently()` 调 `_compute_node()`，再在主线程通过 `_commit_node_execution()` 按原队列顺序提交。

证据：`scripts/recursive_runtime.py:664-722,828-939,940-1105,1173-1223,1225-1292`。

### 4.3 Node / runtime state

`NodeRecord` 存储 node identity、parent、depth、provenance、role、terminal/action、crop 路径、analysis image 路径、父级 analysis/crop bbox、实例元数据、taxonomy、状态、retry/error 字段。`RuntimeState` 存储 `current_depth`、两层队列、processed/deferred/failed node 列表、pending interactive request、request counter 与 production inference flag。

证据：`scripts/recursive_runtime.py:207-308`。

### 4.4 Node 路由图

```text
Node
  |
  +-- node_role already set? -> resolve terminal/action consistency
  |
  +-- otherwise requires_router=true
       -> Router VLM
       -> node_role
       |
       +-- structural_group
       |    -> structural_split
       |    -> child Node(produced_by=structural_split, requires_router=true)
       |    -> next_level_queue
       |
       +-- repeated_group
       |    -> expand_instances
       |    -> child Node(produced_by=expand_instances,
       |                  component_instance, semantic_decompose)
       |    -> next_level_queue or deferred
       |
       +-- component_instance
       |    -> semantic_decompose
       |        +-- decompose -> terminal asset child nodes
       |        +-- stop_as_asset -> current node becomes asset/stop
       |
       `-- asset -> stop -> done
```

证据：`scripts/validate_node_route.py:18-23,64-91`，`scripts/resolve_terminal_state.py:103-165`，`scripts/recursive_runtime.py:705-825,845-939`。

## 5. Router Architecture

### 5.1 谁决定 route

结论：是 VLM 语义分类加工程硬映射，不是模型直接输出 action。

```text
analysis-image.png
  -> ProductionVisualAdapter.route()
  -> VLM Router output: node_role, confidence, reason
  -> validate_node_route.validate_document()
  -> resolve_terminal_state(node_role)
  -> Python ROLE_ACTION_MAP
  -> final next_action
```

Router schema 不包含 `next_action`。`_route()` 丢弃模型以外的任何语义推断，只以验证后的 `result["node_role"]` 调 resolver。`confidence` 仅校验 0..1，不参与 action、fallback 或 retry 决策。

证据：`references/node-router-v0.1.md:5-7,68-97`，`schemas/node-route.schema.json:3-29`，`scripts/recursive_runtime.py:705-722`，`scripts/validate_node_route.py:18-23,64-91`。

### 5.2 合法 route 与触发条件

| node_role (VLM) | next_action (工程) | 触发定义 | 后续 |
| --- | --- | --- | --- |
| `structural_group` | `structural_split` | 下层自然 child 是须先保留的结构区域/容器/集合/子组件，另一次 structural split 可降低复杂度 | structural child 下一层重路由 |
| `repeated_group` | `expand_instances` | 节点主体是可枚举、同组件/业务语义、同或近似 schema 的 peer collection | instance child 工程 shortcut 到 semantic |
| `component_instance` | `semantic_decompose` | 一个具体 self-contained component，直接 owned child 是 visual assets，且不跳过有意义的中间 ownership boundary | `decompose` 或 `stop_as_asset` |
| `asset` | `stop` | 已是 coherent visual asset，继续递归无明确工程价值 | 无 adapter / 无 child |

Role 定义及决策顺序来自运行时加载的 Router Prompt，而实际 action 映射来自 Python。

证据：`references/node-router-v0.1.md:24-64`，`scripts/validate_node_route.py:18-23`。

### 5.3 二次校正、fallback、validator、retry

```text
VLM role JSON
  -> local JSON Schema + enum/range/reason validation
  -> deterministic role/action resolver
  -> final action
```

没有根据图像内容重新分类的 validator、规则型 route override、低 confidence fallback、taxonomy fallback 或 route rewrite。未知 role 直接 `ValueError`。validator 只做 JSON shape、枚举、数值、ID、count、真实 image size/bounds 等机械校验，不做视觉语义审查。

重试分两层：Responses client transport 最多 3 attempt（HTTP 429/502/503/504、timeout/connection），Runtime node 默认在 transient error 后最多 requeue 2 次。重试会重新请求模型，未见固定响应缓存或 route 稳定性比较；因此 retry 本身可以得到不同 role。普通 schema error 和未知 action 属于 non-retryable。

证据：`scripts/validate_node_route.py:64-91`，`scripts/validate_structural_split.py:116-128`，`scripts/validate_expand_instances.py:131-144`，`scripts/validate_semantic_decomposition.py:129-155`，`scripts/vlm_client.py:23-38,279-363`，`scripts/recursive_runtime.py:63-101,580-602`。

## 6. Node Role / Route / Taxonomy

### 概念边界

| 概念 | 枚举/值 | producer | consumer | 作用 |
| --- | --- | --- | --- | --- |
| `node_role` | `structural_group`, `repeated_group`, `component_instance`, `asset`；semantic schema 还接受 `component` | Router VLM；provenance resolver；semantic stop 转换 | resolver、Runtime dispatcher | 当前 node 在递归树上的组织角色 |
| `next_action` / route | `structural_split`, `expand_instances`, `semantic_decompose`, `stop` | Python resolver/ROLE_ACTION_MAP | Runtime | 选择当前节点执行的策略 |
| taxonomy | `background`, `panel`, `button`, `icon`, `illustration`, `frame`, `progress_bar`, `decoration`, `text`, `unknown` | semantic VLM；adapter canonicalization | semantic asset nodes / semantic resolver | terminal visual asset 类型 |

`node_role` 与 `taxonomy` 不是同一维度。taxonomy 不参与 Router route 判定；其实际消费点是 semantic 产物变成 terminal asset 时。semantic 子节点的 provenance 强制成为 `asset -> stop`，不由 taxonomy 映射到不同 action。

`component` 仅见于 semantic-decomposition contract 的输入 enum 和 Prompt；Runtime Router 的合法 role 不含 `component`，正常 Runtime 节点由 Router 返回 `component_instance`。

证据：`schemas/node-route.schema.json:8-17`，`schemas/semantic-decomposition.schema.json:18-31,71-84`，`scripts/validate_node_route.py:18-23`，`scripts/resolve_terminal_state.py:103-165`。

### 映射矩阵（当前代码）

| node_role / 来源 | structural_split | expand_instances | semantic_decompose | stop |
| --- | ---: | ---: | ---: | ---: |
| `structural_group` | 是，硬映射 | 否 | 否 | 否 |
| `repeated_group` | 否 | 是，硬映射 | 否 | 否 |
| `component_instance` | 否 | 否 | 是，硬映射 | 否 |
| `asset` | 否 | 否 | 否 | 是，硬映射 |
| `component` | 无 Router/action 映射；仅 semantic 输入 schema 接受 | 无 | semantic schema 接受 | 无 |
| semantic child taxonomy 任一十类 | 否 | 否 | 否 | 是，provenance resolver 硬设 |

因此 Router role 到 route 是一对一；在完整 Runtime 中不允许一 role 多 action。视觉概念仍可能重叠，因为 role 本身由 Prompt 语言判断。

## 7. Runtime Prompts

### 共同运行时拼接

每次 production adapter 调用的真实构成为：

```text
system: fixed SYSTEM_PROMPT
user: "Execute the following frozen Stage2-A contract... Return JSON only."
      + reference Markdown 中从 "## Production prompt" 到下一个 engineering/evidence heading 的内容
image: current analysis-image.png
response_schema: 对应 schema dict（仅传到 client API；client 内 del 掉）
```

固定 system prompt：

```text
You are a Stage2-A game UI visual structure analyzer.
Execute only the currently specified visual analysis contract.
Judge only from the current input image and current contract; never infer answers from historical tests.
Return output that conforms to the specified JSON schema.
```

加载、截取和调用证据：`scripts/production_visual_adapter.py:31-34,58-79,240-284`。Client 丢弃 `response_schema` 的证据：`scripts/vlm_client.py:335-344`。

### Prompt 1: Node Router

用途：分类 Current Node 的 `node_role`，不输出 children 或 `next_action`。

调用位置：`ProductionVisualAdapter.route()` -> `RecursiveRuntime._route()`。

文件：`references/node-router-v0.1.md:9-78`。

输入变量：Current Node Analysis Image；系统 prompt；没有 parent role/depth/bbox-size 注入变量。

输出 schema：`schemas/node-route.schema.json`，字段 `node_role`, `confidence`, `reason`。

生产 Prompt 的完整可执行主体为该文件 `## Production prompt` 段，即 `references/node-router-v0.1.md:11-78`。其结构为：primary organizational role、四 role 定义、mixed-composition boundary、Flattening Guard、Anti-over-splitting Guard、四步 decision check、1024 analysis-image contract、固定 JSON shape 与禁止字段。

### Prompt 2: structural_split

用途：已路由 `structural_group` 的一层 direct structural children。

调用位置：`ProductionVisualAdapter.structural_split()` -> `RecursiveRuntime._run_structural_split()`。

文件：`references/structural-split-v0.1.md:14-84`。

输入变量：Current `structural_group` Analysis Image。

输出 schema：`schemas/structural-split.schema.json`，`no_useful_structural_split`, `children[]`, `reason`。

可执行主体要求 coarse, stable, direct children；不可输出 icons/text/buttons/illustrations/assets；repeated collection 必须整体保留；functional control region 仅在三条件同时满足时可成为 structural child；bbox 为 1024-wide analysis coordinates。

### Prompt 3: expand_instances

用途：已路由 `repeated_group` 的 direct peer instances。

调用位置：`ProductionVisualAdapter.expand_instances()` -> `RecursiveRuntime._run_expand_instances()`。

文件：`references/expand-instances-v0.1.md:14-56`。

输入变量：Current `repeated_group` Analysis Image。

输出 schema：`schemas/expand-instances.schema.json`，`instance_type`, `repeat_count`, `instances[]`, `reason`。

可执行主体要求同 component template 的 peer instance；保留完整 instance bbox；不输出 collection background/title/container/overlay 或 instance 内 assets；partial instance 仅在完整 bbox 可可靠估计时输出。

### Prompt 4: semantic_decompose

用途：已路由 `component`/`component_instance` 的 `decompose` 或 `stop_as_asset` 及 visual asset children。

调用位置：`ProductionVisualAdapter.semantic_decompose()` -> `RecursiveRuntime._run_semantic_decompose()`。

文件：`references/semantic-decompose-v0.1.md:16-228`。

输入变量：Current node Analysis Image；request context 传入 adapter 的 caller-owned `node_id`/`node_role`，但最终这些 metadata 会被 adapter 覆盖。

输出 schema：`schemas/semantic-decomposition.schema.json`。

生产 Prompt 包含：foundational component 定义、functional completeness 无关、direct-child/ownership/text rules、十类 taxonomy、icon vs illustration role boundary、frame rule、`decompose`/`stop_as_asset` boundary、bbox completeness/overlap、coordinate contract、完整字段/JSON shapes。`task`, `node_id`, `node_role`, `bbox_constraint`, `analysis_image_size` 是 Prompt 中要求字段，但 adapter 将其 canonicalize 为 caller/实际 image 权威值。

### 不存在的运行时 Prompt

未发现独立 root classifier prompt、asset leaf prompt、route rewrite prompt、schema correction prompt、validator prompt、语义 fallback prompt 或 retry-specific prompt。Runtime retry 使用同一 adapter/同一 strategy contract 再调用；validator 是 Python，不调用 VLM。

## 8. Prompt Constraint Audit

本节描述当前 Prompt/工程已经存在的规则及其可能产生的语义边界，不提出修改方案。

| 规则 | 来源 | 影响 | 类型 | 语义重叠/冲突证据 |
| --- | --- | --- | --- | --- |
| `component_instance` 必须直接拥有 visual assets，且不能跳过 intermediate ownership boundary | Router `:28,41-48` | component vs structural/repeated | Prompt hard language | “self-contained”既是 component 定义，又被明确声明不足；图像对 ownership boundary 的证据可能不唯一 |
| 有 structural region/container/collection/subcomponent 且再次 split 能 materially reduce complexity 才是 `structural_group` | Router `:29,63` | structural_split | Prompt hard language | “meaningful”, “evidence-backed”, “materially reduce”未有量化 rule；与 anti-over-splitting 的“不要制造 wrapper”相互拉扯 |
| mixed composition 优先于 collection area | Router `:33,35,47` | structural_group vs repeated_group | Prompt priority | 需判断 sibling 是否“important/independent”；小 sibling 又要求忽略 decoration/light effects |
| visual similarity 不足以证明 repeated group | Router `:34`; expand `:18-29` | repeated vs structural | Prompt hard language | “same business semantics/schema”是抽象推理，单 screenshot 通常信息不足 |
| 遇到不确定以“下一层自然 Direct Children”类型决策 | Router `:37` | component vs structural | Prompt tie-breaker | “mainly visual assets”与“mainly structural regions”都可能成立，缺少可机械执行的边界 |
| 不以 bbox、taxonomy、asset count、complexity 作机械 threshold | Router `:48`; semantic `:68,96,100` | 全部 role / semantic | Prompt prohibition | 排除了可重复的几何 tie-breaker，保留语义裁量 |
| 不得将 icons/text/buttons/illustrations 等直接作为 structural child | structural `:20-26,84` | structural_split | Prompt hard prohibition | functional control exception 又允许 small visual area 成为 structural child，需判断“ordinary button”与“independent functional control region” |
| structural child 必须让下一分析 materially more focused，不能 nearly whole parent | structural `:18,25` | structural_split vs no useful split | Prompt hard language | “materially more focused/complexity reduced”无定量阈值，可能与保留完整 functional region 冲突 |
| functional control 必须同时是 peer、独立职责、缺失将使 first-level incomplete | structural `:48-52` | structural_split | 明确三条件 | “peer / independent / incomplete”均为视觉语义判断；与“not every icon/button”相邻 |
| instance 应为同 template peer，完整 bbox，partial 仅可可靠估计 | expand `:18-29` | expand_instances | Prompt hard language | overlay/selection/occlusion 不改变 template identity，但可见证据不足时何谓 reliable 仍由模型判断 |
| 多个 owned visually distinguishable foundational components 必须 decompose，即使构成完整 button/asset | semantic `:20-30,104-109` | semantic_decompose vs stop | Prompt hard language | 与“protect coherent atomic artwork”并列：panel+icon 强拆，coherent icon/illustration 强保留；边界取决于是否认为 panel/base 与 artwork 独立 |
| functional completeness、semantic unity、bbox overlap 均不能作为 stop 理由 | semantic `:22,27-29,106-115` | semantic_decompose | Prompt hard prohibition | 强迫将视觉可区分层拆出；与 root Router 对 self-contained component 的评估属于不同层，但会放大 Router component 判定后果 |
| atomic artwork 不得因 internal shadow/glow/text/texture 而拆 | semantic `:29,36,61-68` | stop / taxonomy | Prompt hard language | “integral lettering”与“distinguishable label/value”需按 layer semantics 判断；同一 raster 图难有唯一证据 |
| 每 child 与 stop leaf 必须是十类 taxonomy 之一 | semantic `:42-59` | semantic output | Schema enum + Prompt | `unknown` 是唯一 escape category；taxonomy 在 semantic step 强制，但不用于前置 Router |
| bbox 必须完整包含 contour/shadow/glow，prefer safety margin，允许 overlap | semantic `:111-117` | semantic child creation | Prompt hard geometry | 完整性与 child ownership并不等价；overlap合法，validator不审语义/分层 |

### 特定边界：component_instance vs structural_group

Prompt 的选择顺序不是基于 object 外观分类，而是 candidate next layer：若直子层主要是 assets，选 `component_instance`；若应先保留 regions/containers/collections/subcomponents，选 `structural_group`。同时：

- Flattening Guard 强制不跳过 intermediate ownership boundary。
- Anti-over-splitting Guard 禁止仅因 ownership 语言制造 wrapper。
- structural definition 又要求 another split “materially reduce visual complexity”。

因此，复杂 self-contained 模块同时具有 component-instance 与 structural-group 可解释性的场景，Prompt 提供的是自然语言 priority/guard，不提供可计算唯一阈值。`test_node_router_role_boundary.py` 测的是 Prompt 中关键文本和 fixture role boundaries，不是同图独立 VLM run 的稳定性测试。

### 特定边界：semantic_decompose vs complete component / stop

在 semantic 阶段，当前 Prompt 明确规定完整功能或语义统一不得成为 stop 理由；只要可见为两个或更多 owned、distinguishable foundational components 就必须 `decompose`。只有“one atomic foundational UI component，cannot reasonably separate”才 `stop_as_asset`。这仍依赖对 panel/base、badge、text、artwork 的 owned/distinguishable/atomic 解释；schema/validator 不会纠正此选择。

证据：`references/node-router-v0.1.md:24-78`，`references/structural-split-v0.1.md:16-84`，`references/expand-instances-v0.1.md:16-56`，`references/semantic-decompose-v0.1.md:18-162`。

## 9. Structural Split

输入：已被 resolver 硬映射为 `structural_group` 的 Analysis Image。

调用：`RecursiveRuntime._run_structural_split()` -> adapter `run()` -> `validate_structural_split.validate_document()` -> `_commit_structural_split_result()`。

输出：`no_useful_structural_split`、`children[id,label,bbox,confidence]`、`reason`。`no_useful_structural_split=true` 需空 children；false 需至少一个 child。

提交：每 child 走 `_create_recursive_child(produced_by="structural_split")`，生成 crop+analysis image，保存 analysis/crop local bbox，resolver 留 `requires_router=true`，加入 `next_level_queue`。

证据：`scripts/recursive_runtime.py:729-779,828-857`，`schemas/structural-split.schema.json:25-89`，`scripts/validate_structural_split.py:116-128`。

## 10. Expand Instances

```text
repeated_group
  -> expand_instances VLM
  -> repeat_count + instances[{id,bbox,partial_instance,confidence}]
  -> create recursive instance child
  -> resolver: component_instance / semantic_decompose / requires_router=false
  -> first N -> next_level_queue
  -> remainder -> deferred (not discarded)
```

识别由 Router Prompt 的 `repeated_group` 语义与 expand Prompt 的 same template direct-child instance 定义共同决定。`repeat_count` 必须等于 `len(instances)`，instance ID 必须唯一，bbox 必须落在实际 analysis image 内。`partial_instance` 被记录在 NodeRecord，不会使 instance 无效或自动删除。

instance 默认不会重新 Router，而是工程 resolver 根据 `produced_by="expand_instances"` 固定其 `component_instance -> semantic_decompose`。作为语义 node，它的 Router role 已经设置，因此不会再次被判为 `repeated_group`。默认 `repeated_instance_semantic_limit=2`；超过 limit 的 node 标为 `deferred`，保存到树，且可由 `restore_deferred()` 恢复。不存在针对 repeated_group 自循环的专用 guard；该 shortcut 避免 instance 立即再走 Router，但 Runtime 无全局 max depth/cycle guard。

证据：`references/expand-instances-v0.1.md:16-88`，`schemas/expand-instances.schema.json:8-69`，`scripts/validate_expand_instances.py:73-144`，`scripts/resolve_terminal_state.py:148-154`，`scripts/recursive_runtime.py:876-897,1307-1320`。

## 11. Semantic Decompose

输入：Runtime 中通常是 `component_instance`；semantic schema 也接受 `component`。

调用：`RecursiveRuntime._run_semantic_decompose()` -> adapter `run()` -> semantic validator -> `_apply_semantic_parent_result()` -> `_commit_semantic_decompose_result()`。

输出分支：

```text
decision=decompose
  -> children[taxonomy,bbox,...]
  -> _create_asset_child()
  -> child role=asset, action=stop, terminal=true, status=done
  -> no next queue

decision=stop_as_asset
  -> asset_taxonomy
  -> mutate current node to asset/stop/terminal
  -> no fake child
```

每个 semantic child 的 model ID 会在 `ProductionVisualAdapter` 通过 taxonomy counter 改写为如 `icon_001`；caller-owned metadata/实际 analysis size 也被覆盖。schema 限制 decision 的字段组合、十类 taxonomy、bbox shape/size；validator 再检查 child IDs、actual image size 和 bbox boundary，但不判断语义分类或分解决策是否正确。

证据：`scripts/recursive_runtime.py:781-826,899-939`，`scripts/production_visual_adapter.py:91-146,291-317`，`schemas/semantic-decomposition.schema.json:18-121`，`scripts/validate_semantic_decomposition.py:101-155`。

## 12. Stop / Asset Leaf

`stop` 无独立 VLM Prompt 或 adapter。

- Router VLM 返回 `asset`：resolver 设 `terminal=true,next_action=stop,requires_router=false`，`process_node()` 不调 strategy adapter，节点完成。
- semantic VLM 返回 `stop_as_asset`：resolver 将当前 node 设为 asset 并写入 `asset_taxonomy`。
- semantic VLM 返回 `decompose`：每个 child 的 provenance `semantic_decompose` 由 resolver 固定为 asset/stop，并直接 `status=done`。

`asset-stop-result.schema.json` 存在，但 schema 本身对可选 `node_role`/`next_action` 仅要求字符串，不冻结 enum；实际 action consistency 由 `resolve_terminal_state.py` 和 `validate_node_route.py` 的 Python mapping 确保。

证据：`scripts/recursive_runtime.py:917-939,1243-1261`，`scripts/resolve_terminal_state.py:64-165`，`schemas/asset-stop-result.schema.json:8-19`。

## 13. Coordinate Contract

### 实际坐标链

```text
source/root Node Crop pixels
  -> prepare_analysis_input(force_width=True, max_width=1024)
  -> Analysis Image pixels (width exactly 1024)
  -> VLM bbox in current Analysis Image pixels
  -> analysis_bbox_to_crop_bbox / map_bbox_to_source
  -> bbox in current Node Crop pixels
  -> create_node_crop(parent Node Crop, local crop bbox)
  -> child Node Crop
  -> child Analysis Image (again width 1024)
```

Analysis 默认尺寸函数可不放大，但 Recursive Runtime root 与 child 明确调用 `force_width=True`，所以当前 recursive path 的 width 固定 1024；height 为按比例 `round(source_height * 1024 / source_width)`。semantic schema 也将 `analysis_image_size.width` 固定为 1024。

adapter bbox 是相对当前 Node 的 Analysis Image local 坐标。`bbox_in_parent_analysis` 与 `bbox_in_parent_crop` 都是相对直接 parent 的 local 坐标；NodeRecord 不存 root screenshot global bbox 或完整 transform chain。根 Node Crop 是否等于原始 screenshot 由 CLI input 决定，Runtime 不持有 screenshot contract。

`map_bbox_to_source()` 按 x/y 各自 scale 进行四边 transform、round、clamp，最小 1 pixel；`create_child_node_images()` 只从 parent Node Crop 裁出 child，而不是把 VLM bbox 直接当作 source pixels，也不是从 analysis image 裁出。

overlay 工具（`render_structural_overlay.py`, `render_instances_overlay.py`）读取 strategy output 的 analysis-space bbox 与 analysis image 生成 review PNG；它们不参与 route/validator/self-review。已有 `runs/20260814...` 中的 `semantic-overlay-analysis.png` 与 `semantic-overlay-node-crop.png` 是单节点实验产物，非 Runtime 对每 node 的必写产物。

当前 Runtime 未发现“将 VLM analysis bbox 直接作为 source pixel bbox”来裁 recursive child 的旧路径。独立旧平面脚本 `build_asset_analysis.py` 接收 source/analysis image 与 model candidates，另行将 analysis bbox 映射到 source image；它不加入 Recursive Runtime tree。

证据：`scripts/prepare_analysis_input.py:15-34,73-122`，`scripts/runtime_geometry.py:14-99`，`scripts/build_asset_analysis.py:26-61`，`scripts/recursive_runtime.py:749-770,790-807`，`schemas/semantic-decomposition.schema.json:61-69`。

## 14. Recursive Runner

### 状态机

```text
pending/ready
  -> running
  -> [Router if required]
  -> ready
  -> action
     -> done: add processed_nodes
     -> WaitingForAdapter: preserve request, restore ready/pending, return waiting
     -> transient error: requeue same current level until retry budget exhausted
     -> non-retryable/exhausted: failed

expand instance over limit
  -> deferred
  -> restore_deferred() -> pending -> current level (only when idle)
```

队列算法为 BFS with strict level barrier：child 永远先进入 `next_level_queue`；只有 `current_level_queue` 清空时 `advance_level()` 才交换队列并增加 `current_depth`。同一层可使用 `ThreadPoolExecutor` 并发计算，完成后按原 queue 顺序 commit，保持 deterministic commit order。interactive adapter 或 pending interactive request 会禁用该并发路径。

Runner/CLI：`scripts/run_recursive_runtime.py::main`。state：`runtime-state.json`。tree：`tree.json`。resume：CLI `--resume` 调 `RecursiveRuntime.load()`，恢复 state/tree；interactive response 以同 request id 继续。单 node debugging entry 是 public `RecursiveRuntime.process_node(node_id)`；全树入口是 `RecursiveRuntime.run()`。

限制：当前 `RuntimeConfig` 有 repeated-instance limit、max node retries、max concurrency 和 validation mode，但没有 `max_depth`、max total node count 或 cycle detector。`depth` 仅记录层级。终止条件是 stop、semantic terminal、无 structural child、queue empty、或 deferred；模型连续产生 structural tree 时 Runtime 自身不按 depth 截断。

结果：含 deferred 为 `complete_with_deferred`；failed 为 `failed`；blocked 为 `blocked`；否则 `complete`。

证据：`scripts/recursive_runtime.py:40-101,160-182,271-308,557-602,1173-1223,1225-1413`，`scripts/run_recursive_runtime.py:40-112`。

## 15. Runtime Artifacts

递归 Runtime 真正写入：

| 文件 | 写入者 | 内容 | 后续读取者 |
| --- | --- | --- | --- |
| `tree.json` | `NodeStore.persist()` | 全部 NodeRecord 与 parent-child map | `NodeStore.load()` / 审阅者 |
| `runtime-state.json` | `RecursiveRuntime._persist()` | queues、depth、processed/deferred/failed、pending request、flags | `RecursiveRuntime.load()` |
| `run-manifest.json` | `_write_manifest()` | config、adapters、result、failures、warnings | 审阅者 / resume metadata |
| `nodes/<node>/node.json` | `NodeStore.persist()` | 单 NodeRecord | 审阅者 |
| `nodes/<node>/node-crop.png` | root create / child image creation | 当前 node 原尺度 crop | child crop construction / semantic terminal crop source |
| `nodes/<node>/analysis-image.png` | root/recursive child image creation | 1024-width VLM image | Router / strategy adapters / validators |
| `nodes/<node>/analysis-image-meta.json` | `prepare_analysis_input()` | resize/source metadata | 人工/debug；Runtime 从实际 image 读取 size |
| `nodes/<node>/router-result.json` | `_save_adapter_result()` | Router VLM/adapter JSON | 审阅者；不作为下一次 action 的解析 source |
| `nodes/<node>/strategy-result.json` | `_save_adapter_result()` | split/instances/semantic JSON | 审阅者；commit 已在内存完成 |
| `adapter-requests/...` | InteractiveFileAdapter | pending contract request | 外部 interactive responder |
| `adapter-responses/...` | 外部 interactive responder | response envelope | InteractiveFileAdapter |

不存在 Runtime 保证写出的 `route.json`、`children.json`、`overlay.png`、`raw-response.json`、asset extraction output、ExtractionPlan 或 quality gate result。各种 overlay 和 `asset-analysis.json` 来自独立工具/实验，而非 Recursive Runtime 的必产物。

证据：`scripts/recursive_runtime.py:372-384,471-516,557-573,705-712,828-908,1353-1385`，`scripts/interactive_file_adapter.py:155-189`。

## 16. Stage2-A to Stage2-B Bridge

没有自动桥接。Stage2-A semantic output 的 child 有 `id/taxonomy/bbox`，Runtime asset node 也有 `node_id/taxonomy/node_crop`，这些与 B1 `AssetLeaf` 的字段重合，但没有代码将其转换或调用 planner。

现有独立平面转换：`scripts/build_asset_analysis.py::build_asset_analysis()` 从独立 model candidates JSON 与 source/analysis image 生成 `asset-analysis.json`，以 source-image coordinate bbox 输出 asset candidates。该 CLI 不读 `tree.json`，不读 Runtime terminal asset nodes，不调用 B1 planner/executor/gate。

因此，对问题“Stage2-A asset leaf 到底通过哪个字段、哪个 JSON、哪个函数进入 Stage2-B？”的当前实现答案是：没有字段/JSON/function 构成自动运行时桥接；只存在尚未接线的数据模型相似性。

证据：`scripts/build_asset_analysis.py:64-166`，`scripts/recursive_runtime.py:781-826`，`stage2_b/extraction_plan.py:90-243`。

## 17. Stage2-B

### B1 plan 与策略选择

`stage2_b/extraction_plan.py` 定义 `AssetLeaf`、`PlanningDecision`、`ExtractionPlan`、`ExtractionPlanner`。有效 extraction plan mode/backend 组合：

```text
direct_crop       + direct
foreground_extract + color_distance | grabcut | unknown
repair_required   + unknown
```

默认 `ConservativePlanningPolicy` 不根据 taxonomy 或 Stage2-A strategy 作决策，固定返回 `repair_required`/`unknown`/confidence 0。因此默认 planner 不是已接入的 strategy selector，而是等待注入 policy 的 contract envelope。

### Executor

`ExtractionExecutor.execute()` 先验证 input leaf 和 plan lineage 一致：asset ID、node ID、taxonomy、bbox、source crop 必须匹配。`direct_crop` 使用 PIL 从 `source_crop` 按 bbox 裁 PNG bytes；`foreground_extract` 只调注入的 `ForegroundBackend` protocol，仓库未发现 B1 的 color-distance/GrabCut production backend；`repair_required` 抛 `ExecutionDeferred`，不进行 repair。

### Quality Gate

`ExtractionQualityGate.evaluate()` 校验 plan，检查 execution failure/error、empty PNG/dimensions、bbox source bounds、foreground/background ratio、empty mask。结果是 `QualityGateResult`；未发现 A Runtime/B1 主入口实际调用 `evaluate()`，其调用证据在 B1 contract test。

输出：Executor 返回 in-memory `ExtractionArtifact`，不直接写 asset file。现有 `runs/*stage2b*` 有实验性的 extraction request/result/assets/masks，但无法从当前 B1 main chain 证明为 Runtime 自动产物。

证据：`stage2_b/extraction_plan.py:16-34,90-243`，`stage2_b/extraction_executor.py:39-138`，`stage2_b/quality_gate.py:26-85`，`stage2_b/tests/test_extraction_contract.py:101-142`。

## 18. Stage2-C / D

未发现名为 Stage2-C 或 Stage2-D 的统一 package、schema、dispatcher、executor 或 verifier 被接入 Stage2-A/B1 主流程。

可确认的独立相关 PoC：

| 工具 | 输入/输出 | 接入状态 |
| --- | --- | --- |
| `game-ui-asset-extractor/scripts/ui_vlm_region_mask_poc.py` | Stage A OCR JSON + source -> VLM text ownership -> `vlm-region-plan.json`, region mask/overlay | 独立 CLI；未读取 A Runtime asset leaf，未调用 B1 |
| `game-ui-asset-extractor/scripts/ui_vlm_text_auditor.py` | OCR/source -> VLM text audit -> rebuilt mask -> OpenCV Telea -> cleaned image/debug JSON/PNGs | 独立 CLI；非 `advanced_required` dispatcher |
| `game-ui-asset-extractor/scripts/ui_image_clean_repair_poc.py` | source + optional overlay + refs -> remote image generation -> result JSON/image | 独立 CLI；不带 B1 plan lineage、不回写 Stage2-A |
| `scripts/bbox_refiner.py` | `asset-analysis` direct-crop icon candidate -> foreground mask/bbox refinement debug artifacts | 独立工具；不修改 original asset-analysis，不回接 Runtime/B1 |

`game-ui-layout-analysis-verifier` 与 asset extraction chain 未见调用关系。

证据：`game-ui-asset-extractor/scripts/ui_vlm_region_mask_poc.py:772-908`，`ui_vlm_text_auditor.py:830-1060,1192-1273`，`ui_image_clean_repair_poc.py:310-436`，`game-ui-asset-analyzer/scripts/bbox_refiner.py:625-716,794-838`。

## 19. Schemas / Contracts

| Contract | 文件 | producer | consumer | 用途 |
| --- | --- | --- | --- | --- |
| NodeRecord | `scripts/recursive_runtime.py:207-268` | Runtime | NodeStore/Runtime | 节点持久状态 |
| RuntimeState | `scripts/recursive_runtime.py:271-308` | Runtime | Runtime load/run | queue、retry、deferred/resume state |
| node route | `schemas/node-route.schema.json` | Router adapter/VLM | validate_node_route/Runtime | role JSON |
| terminal resolution | `scripts/resolve_terminal_state.py` + `schemas/asset-stop-result.schema.json` | Python resolver | Runtime | role/provenance 到 terminal/action |
| structural split | `schemas/structural-split.schema.json` | split adapter/VLM | structural validator/Runtime | direct structural children |
| expand instances | `schemas/expand-instances.schema.json` | instance adapter/VLM | instance validator/Runtime | repeated peer instances |
| semantic decomposition | `schemas/semantic-decomposition.schema.json` | semantic adapter/VLM then canonicalizer | semantic validator/Runtime | decision/taxonomy/asset children |
| interactive envelope | `schemas/interactive-adapter-response.schema.json` | external responder | InteractiveFileAdapter | request-response resume envelope |
| analysis input metadata | `scripts/prepare_analysis_input.py` | image preparer | debug/review | source/analysis resize metadata |
| asset candidates | `schemas/asset-candidates.schema.json` | flat candidate VLM/tool | validate/build_asset_analysis | pre-final Stage2-A extraction candidates |
| asset analysis | `schemas/asset-analysis.schema.json` | build_asset_analysis | bbox refiner/downstream tools | source-coordinate asset analysis |
| bbox refinement | `schemas/bbox-refinement.schema.json` | bbox_refiner | review/downstream manual use | refinement result |
| extraction plan | `stage2_b/schemas/extraction-plan.schema.json` and Python dataclasses | ExtractionPlanner | Executor/QualityGate | B1 action plan |
| extraction artifact | `stage2_b/extraction_executor.py` dataclass | Executor | QualityGate | in-memory crop/mask/execution result |
| quality gate result | `stage2_b/quality_gate.py` dataclass | QualityGate | no discovered production consumer | mechanical extraction evaluation |
| level1 regions | `schemas/level1-regions.schema.json` | independent old flat flow | level1 validator/tool | not recursive Stage2-A route tree |

## 20. Tests

### 已覆盖

| 模块 | 测试 | 实际覆盖 |
| --- | --- | --- |
| route mapping/schema | `tests/test_node_route.py` | role enum、schema、deterministic mapping、invalid route |
| Router role boundary | `tests/test_node_router_role_boundary.py` | fixture hierarchy cases及 Prompt boundary strings |
| structural split | `tests/test_structural_split.py` | schema/validation/bbox/direct-child contract |
| repeated instances | `tests/test_expand_instances.py` | schema、repeat_count、ID、bbox、partial behavior |
| semantic | `tests/test_semantic_decomposition.py` | decision shapes、taxonomy、bbox/metadata validation |
| terminal/provenance | `tests/test_asset_stop_contract.py` | role/action terminal mapping、structural/instance/semantic provenance |
| recursive core | `tests/test_recursive_runtime.py` | node transitions、stop_as_asset、terminal child、不立即入队 |
| integration | `tests/test_recursive_runtime_integration.py` | repeated deferred、structural then semantic tree |
| multi-root | `tests/test_recursive_runtime_multi_root.py` | multiple root BFS/input ordering/shared state |
| concurrency | `tests/test_runtime_concurrency.py` | level barrier、order-preserving commit、failure isolation、serial/concurrent equivalence |
| retry | `tests/test_runtime_node_retry.py` | retry classification/requeue/exhaustion |
| interactive/resume | `tests/test_interactive_file_adapter.py`, `tests/test_interactive_runtime_integration.py` | request envelope、waiting、resume、deferred policy |
| production adapter | `tests/test_production_visual_adapter.py` | contract loading/canonicalization/validation context |
| VLM client | `tests/test_responses_api_vlm_client.py`, `tests/test_vlm_client_config.py` | endpoint/config/payload/parse/retry boundary |
| coordinate/bbox | `tests/test_bbox_boundary_canonicalizer.py`, `tests/test_bbox_boundary_tolerance_integration.py` | tolerance canonicalization + validation; runtime geometry behavior also covered by runtime tests |
| B1 | `stage2_b/tests/test_extraction_contract.py` | plan/executor/gate contract paths |

### 未发现的覆盖

- 同一 image、同一 production prompt 的多次独立 Router 调用与 route stability benchmark。
- `temperature=0` 下 `component_instance` vs `structural_group` 的实际 VLM repeated-run comparison。
- `semantic_decompose` vs `stop_as_asset` 的实际 VLM repeated-run comparison。
- retry 前后 route 是否变化的 production-VLM test；retry 测试关注机制而非 semantic consistency。
- Prompt/schema 端到端 provider strict structured-output test；client 代码实际删除 schema 参数。
- explicit `max_depth`、node-limit、cycle detection test，因为实现中没有这些 controls。
- Stage2-A terminal asset 自动进入 Stage2-B plan/execution/quality gate 的 integration test，因为未发现桥接实现。

证据：上述 test paths；Router Prompt 自身也记录尚未完成 strict N-run reproducibility benchmark：`references/node-router-v0.1.md:133-135`。

## 21. Model/API Configuration

### Stage2-A production VLM

| 项目 | 当前实现 |
| --- | --- |
| 配置环境变量 | `STAGE2A_VLM_BASE_URL`, `STAGE2A_VLM_API_KEY`, `STAGE2A_VLM_MODEL`, `STAGE2A_VLM_TIMEOUT` |
| endpoint | normalized `<base_url>/v1/responses` |
| API style | Responses API |
| model | `STAGE2A_VLM_MODEL` |
| temperature | `0` |
| top_p | `1` |
| timeout | env，默认 60s |
| max output tokens | 4000 |
| image transport | local PNG/JPEG -> base64 data URL -> `input_image` |
| messages | `instructions`=fixed system prompt；`input` 包含 input_text + input_image |
| response_format/json_schema strict | 未发送。`response_schema` 传进 `infer_json()` 后立即 `del response_schema` |
| provider retry | 最多 3 attempts，5s wait，429/502/503/504 + timeout/connection |
| Runtime retry | 默认最多 2 requeue after initial attempt |

实现没有暴露/记录 API key；本报告不输出 key。

证据：`scripts/vlm_client.py:23-38,85-169,239-264,279-363`，`scripts/production_visual_adapter.py:266-284`，`scripts/run_recursive_runtime.py:32-37,64-67`。

### 其他独立 PoC 客户端

`ui_vlm_region_mask_poc.py` 使用 Chat Completions 风格 payload，并显式 `response_format.type=json_schema`、`strict=true`；该工具不属于 Stage2-A Recursive Runtime。`ui_vlm_text_auditor.py` 也使用独立模型调用。image repair PoC 使用 `TOAPIS_API_KEY` / `TOAPIS_BASE_URL` 的 image task API。

## 22. Route Instability Evidence Summary

### 当前 route pipeline

```text
Node Crop
  -> forced 1024-wide Analysis Image
  -> fixed system prompt + Router production prompt + image
  -> VLM chooses one node_role
  -> local schema/enum validation
  -> Python fixed role/action mapping
  -> final action
  -> strategy-specific VLM decision where applicable
```

### route 判定依赖的语义概念

- primary organizational role
- next natural Direct Children
- immediate ownership / intermediate ownership boundary
- stable, independently meaningful component-tree unit
- structural region, section, container, collection, subcomponent
- mixed composition / important independent sibling
- repeated peer / same component schema / same business semantics
- self-contained UI/business component
- visual assets vs structural children
- material complexity reduction / more focused next step
- coherent visual asset / atomic foundational component
- owned, visually distinguishable foundational UI component
- functional completeness（Router用于 component 描述；semantic Prompt 明确说不作为 stop 条件）
- coherent artwork / integral text vs independent text
- taxonomy role，尤其 icon vs illustration

### 已有代码/Prompt 证据支持的语义边界重叠

1. `structural_group` vs `component_instance`：两者都可以描述 self-contained complex UI。Prompt 必须同时满足“不跳过 ownership boundary”和“不制造 wrapper”；前者倾向 structural，后者倾向 component。没有可量化证据门槛。
2. `structural_group` vs `repeated_group`：含 repeated collection 的混合 module，Prompt 要求看 collection 是否是 parent primary identity，还是 sibling 之一；这依赖“important independent sibling”的解释。
3. `component_instance` vs `asset`：Router 把“direct children are visual assets”路由到 semantic，而 semantic 又将 one atomic foundational component stop。两段独立 VLM semantic boundary都包含 coherent/self-contained/atomic 的自然语言判断。
4. `semantic decompose` vs `stop_as_asset`：panel+icon/text 与 coherent artwork 的判定依赖 visually distinguishable + ownership + atomic artwork；schema和validator无法分辨。
5. `icon` vs `illustration`：Prompt 指定 UI role priority、否定 complexity/area threshold，但“localized UI symbol”与“artwork-like content presentation”仍是语义解释。
6. `text` vs integral artwork lettering：Prompt 要求 layer semantics，且 OCR readability 不是充分条件；单张 composited image 可能无法唯一证明层级。

### 最可能提高判定门槛的当前规则

- Router 对 `structural_group` 要求 evidence-backed structure 且 “another structural_split would materially reduce visual complexity”。来源：`node-router-v0.1.md:29,63`。
- Router `component_instance` 除 self-contained 外还要求 direct semantic decomposition preserve every independently meaningful ownership boundary。来源：`:28,41-48`。
- structural functional-control exception 要求 peer、independent responsibility、first-level incomplete 三项同时成立。来源：`structural-split-v0.1.md:48-52`。
- semantic 对 `decompose` 强制要求所有两个以上 owned distinguishable foundational components均拆出，即使视觉或功能上一体。来源：`semantic-decompose-v0.1.md:20-30,104-109`。
- semantic bbox completeness 要求包住 contour/shadow/glow/highlight，可能提高输出 bbox 合法性负担。来源：`:111-117`。

### 最可能让两种 route 都可自洽的当前规则

- Flattening Guard 与 Anti-over-splitting Guard：同一个候选边界既可被看作 meaningful ownership unit，也可被看作无独立价值 wrapper。来源：Router `:41-57`。
- mixed composition priority 与 repeated-primary-identity：collection同样可作为 parent identity 或 structural child，取决于独立 sibling 重要性。来源：Router `:33-35,47`。
- semantic “panel+icon 必拆”与 “coherent artwork 保持完整”：若基底、装饰、标签板、文字是同一 artwork的内部部分还是独立 base/text，Prompt 仍要模型语义判断。来源：semantic `:27-30,34-40,59-68`。
- structural prohibition of ordinary buttons/icons 与 functional-control exception：单独控制区是否有独立功能 ownership 的判断没有工程阈值。来源：structural `:28-56`。

### VLM 自由判断、工程硬规则、validator 修正

| 类别 | 当前行为 |
| --- | --- |
| VLM 自由语义判断 | Router `node_role`；structural children；repeat identity/instances；semantic `decompose`/`stop_as_asset`；taxonomy；label/reason/confidence；视觉 ownership/atomicity |
| 工程硬规则 | role->action 1:1 mapping；provenance-derived action；instance semantic shortcut；semantic child asset/stop；deferred limit；BFS queue/barrier；status/retry transitions；coordinate transform |
| validator/adapter canonicalization | JSON shapes/enums/fields/ranges/IDs/count/bounds；semantic metadata、analysis size、semantic child IDs；bbox tolerance canonicalization。不会重判视觉语义或 route |

### 为什么 temperature=0 / top_p=0 不能消除当前歧义

实际 production code 是 `temperature=0, top_p=1`，不是 `top_p=0`。即使调用方/Provider 接受 `top_p=0`，当前 Prompt 也没有为下列概念提供唯一、可计算的决策边界：meaningful、independent、ownership、primary identity、materially reduce complexity、next natural Direct Children、coherent artwork、visually distinguishable、atomic、localized UI role。模型服务端、视觉编码、浮点/并行/版本实现与 retry 的重复调用也可能产生差异；更根本的是当前文本允许同一视觉节点存在多种可辩护的组件树解释。工程 validator 不会消除这种语义多解，只拒绝结构/几何不合法的 JSON。

这不是“temperature=0 应绝对 deterministic”的前提判断，而是当前 route contract 没有将上述视觉语义关系离散成唯一决策规则的 AS-IS 描述。

## 23. Important Source Files

| 文件 | 关键职责 |
| --- | --- |
| `game-ui-asset-analyzer/scripts/run_recursive_runtime.py` | Stage2-A CLI start/resume、adapter selector |
| `game-ui-asset-analyzer/scripts/recursive_runtime.py` | Node/tree/state、BFS、action dispatch、children、retry、resume、artifacts |
| `game-ui-asset-analyzer/scripts/resolve_terminal_state.py` | role/provenance 到 action/terminal 的确定性解析 |
| `game-ui-asset-analyzer/scripts/validate_node_route.py` | frozen role/action mapping 和 route validation |
| `game-ui-asset-analyzer/scripts/production_visual_adapter.py` | Prompt loading、VLM invocation、canonicalization、validator dispatch |
| `game-ui-asset-analyzer/scripts/vlm_client.py` | Responses API client、env config、payload、transport retry |
| `game-ui-asset-analyzer/scripts/runtime_geometry.py` | analysis-to-crop transform、child crop/image creation |
| `game-ui-asset-analyzer/scripts/prepare_analysis_input.py` | fixed-width Analysis Image resize |
| `game-ui-asset-analyzer/references/node-router-v0.1.md` | Router actual production prompt |
| `game-ui-asset-analyzer/references/structural-split-v0.1.md` | structural actual production prompt |
| `game-ui-asset-analyzer/references/expand-instances-v0.1.md` | instances actual production prompt |
| `game-ui-asset-analyzer/references/semantic-decompose-v0.1.md` | semantic actual production prompt/taxonomy |
| `game-ui-asset-analyzer/schemas/*.schema.json` | Stage2-A mechanical contracts |
| `game-ui-asset-analyzer/stage2_b/extraction_plan.py` | B1 plan contract/policy |
| `game-ui-asset-analyzer/stage2_b/extraction_executor.py` | B1 direct crop/backend dispatch |
| `game-ui-asset-analyzer/stage2_b/quality_gate.py` | B1 mechanical gate |
| `game-ui-asset-analyzer/scripts/build_asset_analysis.py` | separate flat asset-analysis conversion, not Runtime bridge |
| `game-ui-asset-analyzer/tests/test_recursive_runtime*.py` | tree/BFS/multi-root integration coverage |
| `game-ui-asset-analyzer/tests/test_node_router_role_boundary.py` | Router boundary fixture/prompt checks |

## Audit Boundary

本报告描述当前工作树 AS-IS。引用 `references/*.md` 的内容仅限 `ProductionVisualAdapter._load_prompt()` 实际截取并发送的 `## Production prompt` 段；未使用 README 叙述推断未发现的接线。未修改 Stage2 代码、Prompt、Schema、测试或配置。
