# AI Game UI Engineering 项目上下文

> Snapshot Date: 2026-08-31
> Current Branch: `stage0/ui-generation-foundation`
> HEAD: `85b808bd42c02d302600cc29287c55bf462e1b8f` (`shiyanzhuji de tijiao xiugai`)
> Repository Root: `D:\Third_Test_1\UI-skill`
> Working Tree at snapshot: branch is aligned with `origin/stage0/ui-generation-foundation`; existing untracked user files are `game-ui-asset-analyzer/STAGE2_AS_IS_AUDIT.md` and `game-ui-asset-analyzer/experiments/`.

本文是当前仓库的工程事实快照，不是 README、宣传稿或历史设计汇总。结论优先来自当前代码、Schema、Runtime 调用链、测试和现有产物；文档只用于补充边界。不能由当前仓库确认的能力不会标记为已实现。本文件是本次任务唯一新增文件。

## 0. 阅读结论

当前仓库不是已经贯通的“截图到最终资产”的单一生产系统，而是多个阶段性 Skill、独立 CLI、契约测试和实验产物并存的研究型工作区。最准确的定位是：

> 以 Stage1 UI 生成 Workflow 为正式主线，同时集成 Stage0 文字清除 PoC、Stage2-A 递归视觉结构分析 Runtime，以及独立的 Stage2-B1 提取契约；Stage2-C Repair 和 Stage2-D Verification 尚未形成统一生产链。

当前真实主线可表示为：

```text
Stage1: 用户需求 + 参考图 -> A1/B1/B2 -> Composer -> ui-compose-plan.json
Stage2-A: root node crop -> 固定 Analysis Image -> VLM role -> Python action -> BFS Component Tree
Stage2-B1: AssetLeaf -> ExtractionPlan -> Executor -> QualityGate（独立，未接 A Runtime）
Stage0: OCR/VLM mask -> 独立文字清除或 Image-2 修复 PoC（实验，未形成正式闭环）
```

仓库目前不能证明：Stage1 自动生成最终 UI 图片；Stage2-A 自动进入 Stage2-B1；Stage2-C/D 已统一接线；真实 VLM route 已稳定；FairyGUI/XML 已输出。

## 1. 项目定位与目标

项目目标不是单纯生成一张 UI 图片，也不是一个已经完成的 UI 引擎导出器。它正在探索一条分阶段的 AI UI 工程流水线：

```text
需求与参考图
  -> 参考布局/风格理解
  -> 结构化 Composer 计划
  -> UI 生成基础能力（当前未闭合到最终图片）
  -> UI 结构理解与递归 Component Tree
  -> 资产候选分析
  -> Extraction
  -> Repair（补回截图中不存在的像素）
  -> Verification
  -> 未来 UI Framework / FairyGUI / XML
```

实际已形成的是若干相互独立的可验证合同和 Runtime，而非上述全链路产品。

## 2. 当前仓库结构

| 目录 | 当前事实 |
|---|---|
| `runner/` | Stage1 Runner 的流程规则、invocation 解析、输入同步和 manifest 约定 |
| `game-ui-layout-reference-analyzer/` | A1 布局参考分析 Skill |
| `game-ui-layout-analysis-verifier/` | 布局结果 review/final 校验，不是统一主 Runtime |
| `game-ui-style-reference-analyzer/` | B1 单图风格/资产分析与 B2 风格合成 |
| `game-ui-auto-composer-skill/` | A1 + B2 + 原始需求生成 `ui-compose-plan.json` |
| `game-ui-prompt-compiler-skill/` | Compose plan + style profile 编译图像 Prompt，独立于 Provider |
| `game-ui-image-provider-adapter/` | 独立 ToAPIs/Image Provider 预览 CLI |
| `game-ui-asset-analyzer/` | Stage2-A 递归分析、Runtime、坐标、Prompt/Schema、旧 flat 分析工具 |
| `game-ui-asset-analyzer/stage2_b/` | 独立 Stage2-B1 Extraction Plan、Executor、Quality Gate |
| `game-ui-asset-extractor/` | Stage0 OCR、文字审计、mask、Telea、Image-2 修复等 PoC |
| `docs/experiments/` | Stage0 实验记录与变量控制说明 |
| `runs/` | 历史运行/实验产物；多数被 `.gitignore` 忽略 |
| `.trae/` | Stage1 command 与 Runner Skill 入口 |

仓库没有统一顶层 Python orchestrator，也没有统一的 `pyproject.toml`、`requirements.txt` 或全仓库测试入口。

## 3. 整体系统架构

### Stage1 正式 Workflow

```text
A Layout Reference -> A1 layout-analysis.json
B Style References -> B1 per-reference analysis -> B2 style-profile.json
原始 User Requirement + A1 Final + B2 Final
  -> build_compose_input.py
  -> Composer
  -> finalize_hard_requirements.py
  -> ui-compose-plan.json
```

Stage1 Runner 创建单一 `runs/<run-id>/` workspace，保存原始输入，控制依赖 gate、阶段顺序和状态。它不负责 Stage2、Prompt Compiler、Provider、GPT Image、Preview Adapter 或 FairyGUI。

### Stage2-A 到 Stage2-B1 的实际边界

```text
root crop -> RecursiveRuntime -> terminal NodeRecord/tree.json

AssetLeaf -> ExtractionPlanner -> ExtractionPlan
         -> ExtractionExecutor -> ExtractionArtifact
         -> ExtractionQualityGate -> QualityGateResult
```

两条链当前没有自动桥接：Runtime 不创建 `AssetLeaf`，不调用 `stage2_b`，不持久化 B1 extraction artifact 或 quality gate 结果。

## 4. Stage0 — UI Generation Foundation

**状态：🧪 实验/PoC，代码存在但实验结果不足。** 当前 Stage0 更准确地称为“截图文字清除实验”，不是已完成的 UI Generation Foundation，也不是正式资产分析链。

实际调用链：

```text
source.png
  -> ui_text_extractor.py
  -> texts.json + raw_text_mask.png + OCR cleaned/debug 图
  -> ui_vlm_region_mask_poc.py
  -> vlm-region-plan.json + region-mask.png + red overlay
  -> ui_image_clean_repair_poc.py
  -> clean.<真实扩展名> + result.json
```

`ui_vlm_text_auditor.py`、`ui_text_repair_planner.py` 和 `smooth_surface_repair_poc.py` 是独立路线/对照工具，没有被 Stage2-A Runtime 统一调度。当前 Image-2 CLI 的 `--mask-overlay` 只是第二张普通 reference image；真实请求是 `POST /v1/images/generations` 的两张参考图，不是 Image Edit API 的 mask 字段。

Stage0 文档明确表示实验尚未执行：缺少固定 `source.png`、strong prompt 文件和严格变量隔离所需的预清除 CLI；当前 `runs/` 没有 EXP-001～EXP-004 的可回填结果。因此不能写成 text removal 或 inpainting 已验证。

当前最大技术问题是：文字移除后，复杂 UI 背景、边框、渐变和被文字遮挡的像素需要恢复；现有远程 Image-2 PoC 不是稳定的 UI repair runtime。

## 5. Stage1 — Reference Analysis / Composer

**状态：✅ 已实现/已验证的流程合同；真实视觉质量仍受外部模型和输入影响。**

Stage1 的固定顺序是 `A1 -> B1 -> B2 -> Composer Input -> Composer`。Runner 负责 workspace、原始输入、路径、阶段 gate 和 manifest；Skill 负责各阶段内容。`parse-stage1-invocation.py` 将 Business Requirement 与 Runner Control 分开，`sync-stage1-inputs.py` 确定性维护图片路径和尺寸元数据。

Composer 的实际输入是 `request.json`、`layout-analysis.json`、`style-profile.json`，输出 `ui-compose-plan.json`。Prompt Compiler 和 Provider Adapter 仍是后续独立 CLI，不会被 Composer 自动调用。

关于 B1：当前仓库的 B1 是风格参考分析，不应与 Stage2-B1 Extraction 混淆。B1 应做参考图的客观视觉分析；A1/Layout Analysis 提供布局事实；Composer 决定结构化生成计划。当前 Stage1 文档和 Skill 支持“客观观察 -> style profile -> Composer plan”的合同，但本文件不把视觉判断正确性扩大为已验证。

## 6. Stage2 总体架构

| 部分 | 状态 | 事实 |
|---|---|---|
| Level-1 Region Decomposition | ✅/冻结合同 | 递归 Runtime 当前主要从 root crop 开始；区域合同和 Schema 存在 |
| Coordinate Contract v0.1 | ✅/冻结合同 | 固定 Analysis Space 与确定性 bbox 映射已实现并有测试 |
| Node Router v0.1 | ✅/冻结合同 | VLM 输出 role，Python 硬映射 action |
| `structural_split` | ✅/冻结合同 | direct structural children，下一层重新 Router |
| `expand_instances` | ✅/冻结合同 | repeated group 展开为 peer instances |
| `semantic_decompose` | ✅/已实现 | taxonomy/caller identity closure，保留 v0.1 schema/enum |
| Recursive Runtime | ✅/已实现，mechanics validated | BFS、状态、重试、deferred、interactive resume、同层并发计算 |
| Production Visual Adapter | ✅/已实现 | Prompt/Schema 加载、canonicalization、本地 validation |
| Real Responses API smoke test | 🟡 待验证 | client/payload 有测试，真实 API smoke test 未由当前证据证明 |
| Real-image R5 | ⚪ 待完成 | 当前无法确认 |
| Stage2-B1 | 🟡 部分实现 | plan/executor/quality gate 独立存在，未接 A Runtime |
| Stage2-C Repair | 🧪 PoC/架构碎片 | 有 mask、Telea、Image-2 修复工具，无统一 dispatcher |
| Stage2-D Verifier | 🟡 局部验证器 | 有 contract/schema/quality gate validators，无统一视觉完整性 verifier |
| Semantic-first Router | 🧪 实验/分支线索 | 当前主 Runtime 仍使用 taxonomy role Router，不应写成正式稳定能力 |
| FairyGUI/XML | ⚪ 规划中/当前未实现 | 当前仓库无法确认输出 |

## 7. Stage2-A Recursive Component Tree

### 为什么采用递归树

旧 flat workflow 从 full screenshot 一次性发现全部资产，容易受到上下文复杂度、层级污染、小资产漏检、repeated element 和 Prompt 负载影响。当前递归合同采用：

```text
Full UI -> Region -> Structural Group -> Repeated Group -> Component Instance -> Asset
```

每次只处理当前 Node Crop，逐层缩小视觉注意范围。它解决的是分析边界和 semantic ownership 问题，不等于已经解决真实 VLM 识别稳定性。

### Role 与 action

Router 只输出：

```json
{"node_role":"structural_group | repeated_group | component_instance | asset","confidence":0.0,"reason":"..."}
```

Python `ROLE_ACTION_MAP` 固定映射：

| VLM `node_role` | Python `next_action` | 后续 |
|---|---|---|
| `structural_group` | `structural_split` | 生成 direct structural children，下一层重新 Router |
| `repeated_group` | `expand_instances` | 生成 peer component instances，工程 shortcut 到 semantic decomposition |
| `component_instance` | `semantic_decompose` | VLM 决定 `decompose` 或 `stop_as_asset` |
| `asset` | `stop` | 终止，不再调用策略 adapter |

`next_action` 不在 Router Schema 中，也不是 VLM 直接决定的。`confidence` 当前只做范围校验，不参与 fallback、retry 或 route rewrite。

### Tree Contract

当前 Prompt/Runtime 明确支持以下边界：

- structural split 只输出 direct children；
- `Context Visible, Not Owned`：上下文可见不代表归当前节点所有；
- repeated group 保持整体，先展开 peer instances，不能在结构层提前展开实例内部资产；
- structural children 进入下一层，不在创建当层执行；
- `expand_instances` children 不重新 Router，直接进入 semantic decomposition；
- semantic `decompose` children 直接成为 terminal asset nodes；
- `stop_as_asset` 将当前 component instance 原地变为 asset；
- `bbox` 可以重叠，重叠本身不等于重复；semantic ownership 应尽量唯一；
- child 与 parent 等价时继续递归没有工程价值，应停止；
- 当前 Runtime 不在 branch 内重新回整图补漏。

### Node 输出与终止

每个节点保存 `node.json`、`node-crop.png`、`analysis-image.png`、`analysis-image-meta.json`，并按需要保存 `router-result.json`、`strategy-result.json`。Node 状态包含 `pending/running/ready/done/deferred/failed/blocked`。终止来自 Router `asset -> stop`、semantic `stop_as_asset`、semantic terminal children 或有限实例 defer；没有显式 `max_depth`、总节点上限或 cycle detector。

## 8. Coordinate Contract

当前契约是：

```text
Node Crop
  -> 工程主动生成 fixed Analysis Image
  -> VLM bbox in Analysis Space
  -> analysis_bbox_to_crop_bbox()
  -> 当前父 Node Crop 坐标
  -> 从父 Node Crop 裁出 child Node Crop
  -> child 重新生成 Analysis Image
```

关键事实：

1. 不能直接使用 VLM bbox，因为它属于当前 Analysis Image，而不是原始 source pixel space；VLM reported canvas 也不是可信的工程坐标来源。
2. 当前递归 Runtime 强制 Analysis Image 宽度为 `1024`，不裁剪、不 padding；高度按 Node Crop 原始比例计算并取整。旧 flat 工具在源图宽度小于 1024 时不放大，这与递归 `force_width=True` 是不同上下文，不能混写。
3. `scale_x = crop_width / analysis_width`，`scale_y = crop_height / analysis_height`。四条边独立缩放、round、clamp 到目标边界，并保持至少 1 像素。
4. `bbox_in_parent_analysis` 是相对直接父级 Analysis Image 的 bbox；转换后得到 `bbox_in_parent_crop`，再从父 Node Crop 裁 child。Runtime 不保存一条自动拼接到原始 root screenshot 的 global transform chain，且 root crop 不保证等于完整 screenshot。
5. 每个 child 的分析空间重新建立，不能把父级 Analysis Space bbox 当成 child 的 source 坐标。

这项确定性变换已有 `runtime_geometry.py`、`prepare_analysis_input.py` 和 bbox boundary tests 支撑。当前仓库没有足够证据证明所有真实图片层级上的语义 bbox 都正确。

## 9. Stage2-B Asset Extraction

**状态：🟡 已有基础/部分实现，未接入 Stage2-A。**

Stage2-A 回答“它是什么、在哪里、应该拆什么”；Stage2-B1 回答“具体如何从像素得到它”。`bbox != asset`：不规则 icon、character、ornament 可能需要：

```text
coarse bbox -> ROI -> foreground detection -> mask/alpha -> refined bbox -> PNG
```

当前 B1 合同的 extraction mode 是 `direct_crop`、`foreground_extract`、`repair_required`；仓库 Stage2-A flat 策略合同另有 `advanced_required`、`do_not_extract`，两者属于不同层的枚举，不能擅自合并。

`direct_crop` 使用 PIL 输出 PNG bytes。`foreground_extract` 依赖注入的 `ForegroundBackend`；当前没有确认 B1 内置的 color-distance 或 GrabCut 生产 backend。`repair_required` 由 Executor 延后并抛出 `ExecutionDeferred`。

Stage2-A 另有独立 `bbox_refiner.py`：只对符合条件的 `direct_crop` icon 做确定性局部 refinement，输出单独的 `bbox-refinement.json`，不覆盖正式分析结果。当前未发现名为 `coarse_bbox/roi_bbox/refined_bbox/mask/status` 的统一 Foreground Refiner 生产链；不能把 bbox refiner 写成 foreground extraction 已完成。

## 10. Stage2-C Repair

**状态：🧪 独立 PoC/架构碎片，未形成正式 Stage2-C Runtime。**

Repair 不是普通裁剪或 segmentation。若截图中 `Character` 遮挡 `Panel`，Panel 被遮挡区域的原始像素不存在；segmentation 只能分出可见像素，无法凭空恢复完整 Panel。此时需要生成式或其他 repair 才能补回缺失像素。

可确认的独立工具包括 `ui_vlm_region_mask_poc.py`、`ui_vlm_text_auditor.py`、`ui_text_repair_planner.py`、`ui_image_clean_repair_poc.py` 和 `smooth_surface_repair_poc.py`。它们没有被 Stage2-A 或 B1 统一 dispatcher 调用，因此不能写成 Stage2-C 已接链，也不能写成 UI 缺图问题已解决。

## 11. Stage2-D Verification

**状态：🟡 局部 contract validators，统一 verifier 未完成。**

现有 validator 检查 JSON shape、enum、required fields、数值范围、bbox bounds、ID、repeat count、metadata/lineage 一致性和 B1 机械质量门（empty output、empty mask、bbox outside、foreground/background ratio、extraction failure）。

当前没有统一 verifier 可靠检查：漏切、错切、文字残留、视觉 ownership、taxonomy 正确性、遮挡修复完整性或整个 extraction completeness。应明确记录为“Stage2-D architecture/局部验证存在，runtime 未完成”。

## 12. Semantic-first Router 实验

**状态：🧪 实验中/分支线索，不是当前主线正式 Router。**

当前 Stage2-A 正式 Runtime 仍让 VLM 按 `structural_group/repeated_group/component_instance/asset` 输出 role，再由 Python 固定映射 action。Semantic-first 的探索方向是先让 VLM 描述 visually meaningful parts，再由工程逻辑归类，而不是直接把 taxonomy 当作模型分类任务。

仓库当前测试和代码能证明 role/action 合同与 mechanics，不能证明 semantic-first 在真实 UI 上稳定，也不能证明摇摆只是 sampling randomness。Production client 当前固定 `temperature=0`、`top_p=1`；若固定参数后仍摇摆，较合理的待验证假设是 Task Definition/Prompt Semantic Ambiguity，而非单纯随机采样，但当前仓库没有足够重复真实调用证据把它写成定论。

## 13. Runtime / Queue / Multi-root

Runtime 是单进程、按层屏障的 BFS：`current_level_queue` 全部处理完成后，才交换 `next_level_queue`。同一层可用 `ThreadPoolExecutor` 并发 compute，之后按原队列顺序 commit，保持确定性提交顺序。状态包括 `current_depth`、两层 queue、`processed_nodes`、`deferred_nodes`、`failed_nodes`、pending adapter request、request counter 和 real visual inference flag。

`create_multi()` 与测试支持 multi-root API，但当前 CLI 暴露的是单个 `--root-node-crop`。interactive adapter 通过 `adapter-requests/<request-id>.json` 和 `adapter-responses/<request-id>.json` 支持文件式等待/继续；resume 会复用未回答 request ID。实例语义处理有默认 limit 2，超限记录为 `deferred`，可通过 `restore_deferred()` 恢复。

并发已存在于 Runtime 的同层计算基础，不应表述为“已经实现四线程 VLM 调度框架”或完整 async 并发系统。当前没有 max-depth、max-node 或 cycle protection。

## 14. Schema 与 Structured Output

当前 Stage2-A Schema 包括：

```text
level1-regions.schema.json
node-route.schema.json
structural-split.schema.json
expand-instances.schema.json
semantic-decomposition.schema.json
asset-stop-result.schema.json
asset-candidates.schema.json
asset-analysis.schema.json
bbox-refinement.schema.json
interactive-adapter-response.schema.json
```

Stage2-B1 另有 `stage2_b/schemas/extraction-plan.schema.json`。

当前生产适配器的实际链路是：

```text
Prompt + Analysis Image
  -> VLMClient.infer_json()
  -> Responses API output_text
  -> json.loads
  -> adapter canonicalization
  -> 本地 Schema validator
  -> Runtime commit
```

`ProductionVisualAdapter` 按 capability 读取 `references/*.md` 和对应 Schema；它会 canonicalize semantic metadata、child IDs、analysis image size 和 bbox 边界。`VLMClient.infer_json()` 当前显式丢弃 `response_schema`，所以 provider 请求没有启用 structured output；约束来自模型文本 JSON 加本地 validator。实际请求是 `temperature=0`、`top_p=1`、`max_output_tokens=4000`。

Runtime 产物主要是 `run-manifest.json`、`runtime-state.json`、`tree.json`、节点目录及 interactive request/response。它不保证生成 `asset-analysis.json`、ExtractionPlan、最终 extraction PNG、统一 verifier JSON 或 FairyGUI 输出。旧 flat `asset-analysis.json` 与递归 `tree.json` 是两种不同格式。

## 15. Prompt 与真实调用链

Stage1 Prompt/Skill 通过 Runner 顺序执行 A1、B1、B2 和 Composer。Composer 不自动调用 Prompt Compiler 或 Provider Adapter。

Stage2-A 的真实 capability 绑定：

| Runtime 方法 | Prompt | Schema |
|---|---|---|
| `route()` | `references/node-router-v0.1.md` | `schemas/node-route.schema.json` |
| `structural_split()` | `references/structural-split-v0.1.md` | `schemas/structural-split.schema.json` |
| `expand_instances()` | `references/expand-instances-v0.1.md` | `schemas/expand-instances.schema.json` |
| `semantic_decompose()` | `references/semantic-decompose-v0.1.md` | `schemas/semantic-decomposition.schema.json` |

调用链是 `RecursiveRuntime._route/_run_* -> ProductionVisualAdapter -> VLMClient -> POST /v1/responses -> parse/canonicalize/local validate -> Runtime commit`。`interactive` 模式不调用真实 provider，而是等待文件响应；`production` 模式需要环境变量 `STAGE2A_VLM_BASE_URL`、`STAGE2A_VLM_API_KEY`、`STAGE2A_VLM_MODEL`，不会自动 fallback。

## 16. 当前测试与实验结果

已覆盖的主要内容：Router enum/硬映射和边界 fixture；structural split；repeated instances；semantic decomposition contract；terminal/provenance；Runtime 状态、BFS、multi-root、deferred、并发顺序 commit、retry、interactive resume；Production adapter contract loading/canonicalization；Responses API payload/解析/transport retry；坐标转换和 bbox tolerance；B1 planner、executor、quality gate。一次实际执行 `python -m pytest game-ui-asset-analyzer/tests game-ui-asset-analyzer/stage2_b/tests` 收集 338 项，337 通过、1 项失败。

测试证明了：除当前失败用例外，Python contract、Runtime mechanics、Schema/lineage/queue/coordinate 的确定性行为，以及独立 B1 API 的合同行为。当前失败是 `test_t18_invalid_output_text_json_is_parse_error`：响应文本为 fenced JSON（```json...```）时，代码没有按测试预期抛出 `VLMResponseParseError`；这属于现有 VLM response parsing 的工作区问题，本次未修改代码。

测试不能证明：真实 Provider 已成功运行；真实图片 R5 已完成；真实 VLM route/semantic decision 的重复稳定性；provider structured output 已启用；A→B1 自动桥接；foreground backend 生产可用；Stage2-C/D 统一链；所有 UI 类型可工程化；最终 UI 图片已生成。

Stage0 EXP-001～EXP-004 当前只有实验设计与 CLI 记录，没有可回填的正式执行产物。真实 VLM/Image-2 调用可能需要密钥并产生费用，本次没有擅自执行。

## 17. 当前能力成熟度

| 模块 | 状态 | 当前事实 |
|---|---|---|
| 产品总体目标 | 🟡 | 目标是 AI UI 理解、生成基础、递归拆解到资产工程化；闭环未完成 |
| Stage0 Foundation | 🧪 | 文字清除与修复 PoC，实验尚未形成证据 |
| Text Auditor / mask | 🧪 | CLI 存在，未接入统一 Runtime |
| A1 Layout Analysis | ✅/🟡 | Skill、Schema、Runner gate 存在；视觉正确性依赖实际模型运行 |
| B1/B2 Style Analysis | ✅/🟡 | Stage1 Workflow 中有明确输入输出合同 |
| Composer | ✅/🟡 | 可生成并校验 `ui-compose-plan.json`；不负责图片生成 |
| Stage2-A Component Tree | ✅ | 递归数据模型与 BFS mechanics 已验证 |
| `structural_split` | ✅ | frozen contract 与测试存在 |
| `expand_instances` | ✅ | frozen contract 与测试存在 |
| `semantic_decompose` | ✅/🟡 | 代码与 contract 已实现，真实模型语义稳定性未证实 |
| Coordinate Contract | ✅ | fixed analysis space、四边 transform、clamp/round 有代码和测试 |
| Multi-root Runtime | 🟡 | Python API/测试存在，CLI 仍为单 root |
| Queue/concurrency | 🟡 | level queue 与同层并发基础存在，无完整并发产品合同 |
| Stage2-B1 Extraction | 🟡 | plan/executor/quality gate 独立实现，未自动接 A |
| Foreground Refiner | 🧪/🟡 | bbox refiner 存在；统一 mask/alpha foreground backend 未确认 |
| Stage2-C Repair | 🧪 | 独立 mask/inpaint/Image-2 PoC，无统一 Runtime |
| Stage2-D Verifier | 🟡 | 局部机械验证器存在，统一视觉 verifier 未完成 |
| Semantic-first Router | 🧪 | 实验方向，不是主线稳定能力 |
| FairyGUI/XML | ⚪ | 规划中，当前无实现证据 |

## 18. 当前主要技术成果

1. **坐标漂移问题被工程化处理。** 当前 Runtime 主动创建固定 1024 宽 Analysis Space，VLM 只在该空间给 bbox，程序通过四边独立缩放、round、clamp 映射回 Node Crop；这比直接信任 VLM reported canvas 可复现，且有数学/边界测试。
2. **Full-image detection 演进为递归 Component Tree。** Role/action 硬映射、direct-child、repeated group 延迟展开、semantic terminal 和 BFS level barrier 已形成可测试的树合同。
3. **AI 语义与确定性 Runtime 已有清晰分工。** VLM 负责视觉 role、结构/实例/语义候选；Python 负责 resize、坐标、ID、状态、队列、序列化、canonicalization 和机械 validation。
4. **Detection、Extraction、Repair 已在合同层解耦。** AssetLeaf/ExtractionPlan/Executor/QualityGate 明确“知道资产在哪”不等于“已经得到可复用 PNG”；缺失像素需进入 Repair，而不是伪装成 segmentation。
5. **Structured AI engineering 基础已建立。** Prompt、Schema、raw text JSON、canonicalization、本地验证、lineage 和 branch/fixture 测试共同约束概率模型的不稳定性；但 provider structured output 本身尚未启用。

## 19. 当前问题和技术债

按当前风险优先级：

1. 真实 VLM 的 semantic decomposition/router 稳定性仍未被重复真实图片实验覆盖；当前 `confidence` 不会触发 fallback。
2. Stage0 文字清除后的复杂 UI repair 质量没有正式实验结果，且现有 Image-2 路线不是统一 mask/edit runtime。
3. Stage2-A terminal nodes 尚未自动桥接 Stage2-B1，导致 extraction PNG、quality gate 和后续 repair 没有端到端闭环。
4. `foreground_extract` 的生产 backend、遮挡资产 Repair 和统一 Stage2-D verifier 尚未完成。
5. 当前没有完整递归保护（max depth/node/cycle）；并发虽有基础，但没有稳定的完整 async/并发合同。
6. 上游生成尚未证明足够 asset-friendly；Prompt Compiler/Provider 是独立阶段，不能假定 Composer 已生成可拆资产。
7. FairyGUI/XML 尚未开始。

## 20. 当前项目演进路线

合理顺序是：

```text
稳定当前 Stage1/Stage2-A 合同
  -> 用非付费 fixture 验证所有 artifact/状态边界
  -> 做受控真实 VLM smoke/R5 与重复 route 回归
  -> 冻结 semantic Router 的任务定义和回归集
  -> 实现 Stage2-A terminal -> AssetLeaf -> B1 bridge
  -> 落地 direct crop 与 foreground extraction backend
  -> 建立 Stage0 text mask/repair 的受控实验与质量指标
  -> 实现 Stage2-C Repair dispatcher
  -> 实现 Stage2-D completeness/ownership/asset-integrity verifier
  -> 形成资产工程输出
  -> 再评估 FairyGUI/XML 与上游 asset-friendly generation
```

不应先把“优化模型效果”作为唯一路线；当前更紧迫的是接通模块边界、冻结证据格式、建立真实图片回归和明确质量 gate。

## 21. 关键文件索引

- Stage1 流程：[runner/first-stage-runner.md](runner/first-stage-runner.md)、[.trae/commands/stage1.md](.trae/commands/stage1.md)
- Stage1 能力：[game-ui-layout-reference-analyzer/](game-ui-layout-reference-analyzer/)、[game-ui-style-reference-analyzer/](game-ui-style-reference-analyzer/)、[game-ui-auto-composer-skill/](game-ui-auto-composer-skill/)
- Stage2-A 入口：[run_recursive_runtime.py](game-ui-asset-analyzer/scripts/run_recursive_runtime.py)、[recursive_runtime.py](game-ui-asset-analyzer/scripts/recursive_runtime.py)
- Stage2-A 适配器：[production_visual_adapter.py](game-ui-asset-analyzer/scripts/production_visual_adapter.py)、[vlm_client.py](game-ui-asset-analyzer/scripts/vlm_client.py)
- 坐标：[prepare_analysis_input.py](game-ui-asset-analyzer/scripts/prepare_analysis_input.py)、[runtime_geometry.py](game-ui-asset-analyzer/scripts/runtime_geometry.py)、[build_asset_analysis.py](game-ui-asset-analyzer/scripts/build_asset_analysis.py)
- Stage2-A Prompt：[node-router-v0.1.md](game-ui-asset-analyzer/references/node-router-v0.1.md)、[structural-split-v0.1.md](game-ui-asset-analyzer/references/structural-split-v0.1.md)、[expand-instances-v0.1.md](game-ui-asset-analyzer/references/expand-instances-v0.1.md)、[semantic-decompose-v0.1.md](game-ui-asset-analyzer/references/semantic-decompose-v0.1.md)
- Stage2-A Schema：[schemas/](game-ui-asset-analyzer/schemas/)
- Stage2-B1：[extraction_plan.py](game-ui-asset-analyzer/stage2_b/extraction_plan.py)、[extraction_executor.py](game-ui-asset-analyzer/stage2_b/extraction_executor.py)、[quality_gate.py](game-ui-asset-analyzer/stage2_b/quality_gate.py)
- Stage0 CLI：[ui_text_extractor.py](game-ui-asset-extractor/scripts/ui_text_extractor.py)、[ui_vlm_region_mask_poc.py](game-ui-asset-extractor/scripts/ui_vlm_region_mask_poc.py)、[ui_vlm_text_auditor.py](game-ui-asset-extractor/scripts/ui_vlm_text_auditor.py)、[ui_text_repair_planner.py](game-ui-asset-extractor/scripts/ui_text_repair_planner.py)、[ui_image_clean_repair_poc.py](game-ui-asset-extractor/scripts/ui_image_clean_repair_poc.py)
- Stage0 实验边界：[docs/experiments/stage0-text-cleaning/README.md](docs/experiments/stage0-text-cleaning/README.md)
- 当前 Stage2 审计：[game-ui-asset-analyzer/STAGE2_AS_IS_AUDIT.md](game-ui-asset-analyzer/STAGE2_AS_IS_AUDIT.md)
- 历史 flat 产物：[runs/20260812_s2a-coordinate-chain_001/analysis/asset-analysis.json](runs/20260812_s2a-coordinate-chain_001/analysis/asset-analysis.json)

当前本地和远程可确认的分支包括 `main`、`stage0/ui-generation-foundation`、`stage2/asset-extraction`、`stage2/multi-root-runtime-v01`、`stageA/semantic-first-router` 及对应部分 remote refs。分支存在不代表其能力已合并到当前 HEAD。

## 22. 后续 AI 的事实约束

1. bbox 不能默认视为 Source Pixel Coordinate；必须确认它属于当前 Analysis Image、Node Crop 还是 source image。
2. VLM raw bbox 必须按照 Coordinate Contract 转换；不要直接把模型数字当最终 source bbox。
3. Analysis Image 和 Source/Node Crop 是不同 coordinate space；递归 child 必须从父 Node Crop 裁出后重新建立 analysis space。
4. semantic ownership 与 bbox overlap 是两个问题；允许合理重叠，不要仅凭重叠删除候选。
5. Stage2-A 决定“拆什么”；Stage2-B1 决定“怎么提取”。
6. bbox 不是最终 asset；Extraction 需要像素策略、mask/alpha 或 repair 判断。
7. 被遮挡且原像素不存在的资产不能靠 extraction/segmentation 完整恢复，需要 Repair。
8. repeated group 不应在 structural layer 提前展开内部资产；先保持 peer instances，再进入 semantic decomposition。
9. Tree traversal 只能处理当前 semantic owner 的内容；不要在 branch 内回整图补漏造成重复 ownership。
10. `Prompt` 文件存在不代表 Runtime 已调用它；必须追 `RecursiveRuntime -> Adapter -> VLMClient` 的真实调用链。
11. 不要把实验分支、PoC、一次成功或文档计划写成当前主线正式能力。
12. 不要把 B1 Extraction contract 写成 Stage2-A 已自动消费；当前 A→B1 bridge 不存在。
13. 不要把 `response_schema` 传给 Python client 写成 provider structured output 已启用；当前 client 会删除它并依赖本地 validation。
14. 当前 production VLM 参数事实是 `temperature=0`、`top_p=1`，不要引用旧的 `top_p=0` 描述。
15. 不要把 `ThreadPoolExecutor` 的同层 compute 基础写成完整四线程/async VLM 产品调度。
16. 不要把 Stage2-D 局部 validators 写成已经完成漏切、错切、文字残留和视觉完整性验证。
17. 不要把历史 run manifest、旧路径示例或 `.gitignore` 下的约定产物当作当前已存在的结果。
18. 不要把 FairyGUI/XML 规划描述为已实现；当前没有该输出的代码证据。
