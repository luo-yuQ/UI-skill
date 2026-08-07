---
name: game-ui-layout-analysis-verifier
description: 审查一张其他游戏完整 UI 截图及其 A1 布局分析初稿，检查页面用途、遗漏区域、结构关系、组件组、重复数量、视觉层级、证据等级、过度推断、置信度和品牌内容混入，并生成结构化 review 与符合 A1 契约的完整 final analysis。用于已经完成 A1 初步分析且原始截图仍可访问的场景；不用于 UI 生成、切图、风格图分析、引擎节点输出、像素级检测或具体 VLM 服务调用。
---

# 游戏 UI 布局分析审查器

同时读取原始游戏 UI 截图和 A1 `layout-reference-analysis.draft.json`，先独立观察截图，再挑战初稿结论。输出 `layout-reference-review.json` 和完整的 `layout-reference-analysis.final.json`。

## 强制两阶段顺序

严格分离两个阶段：

1. **Independent Baseline**：先独立观察截图并写下基线。
2. **Draft Comparison**：保存基线后，才读取 A1 的语义结论并逐项对照。

在独立基线完成前，不得采用 A1 的页面分类、区域名称、组件数量、视觉焦点、主要操作或交互推断作为判断基础。可以预先确认 draft 文件存在且 JSON 可解析，但必须忽略其具体语义内容。若运行环境无法技术性阻止先读取 draft，仍要先重新独立记录截图观察，并在 review 的 `independent_baseline` 中留下可审计证据。

## 必需输入

- 原始游戏 UI 截图；
- 可解析且通过 A1 validator 的 draft JSON。

可选输入为用户关注说明和 A1 validation result。validation result 只提供辅助信息，不能替代截图。不要只根据 draft 做文本审稿；否则无法发现遗漏区域、数量错误、装饰误判和层级错误。

## 工作流

1. 验证截图与 draft 同时存在，只检查 draft 的文件存在性和 JSON 结构。读取 `references/verification-workflow.md`。
2. 不读取 draft 语义，独立观察原始截图。
3. 在 `independent_baseline` 中记录页面假设、呈现模式、主要区域、重要组件组、重复数量、视觉焦点、交互焦点、主操作候选、次级操作候选、可见标签、图像限制、不确定项和基线置信度。
4. 保存 Independent Baseline；未完成时不得进入比较阶段。
5. 读取 A1 draft，并确认其通过 `../game-ui-layout-reference-analyzer/scripts/validate_layout_reference_analysis.py`。
6. 建立 `comparison_summary`，逐项比较页面类型、用途、模式、区域、组件组、数量、视觉层级、证据、元数据和用户关注说明。
7. 检查每个主要区域的完整性、拆分、合并、父子关系和粗略边界。
8. 检查每个重要组件组的粒度、类型、归属与可见数量。
9. 检查相邻、覆盖、从属、控制和更新关系。
10. 分别核实第一视觉焦点、主要交互焦点与主要操作，不得把三者强制视为同一对象。
11. 检查明显且功能独立的次级操作入口。
12. 检查 `observed`、`inferred`、`uncertain` 与功能推断。
13. 检查图片元数据声明、品牌隔离、用户关注说明覆盖情况及局部/整体置信度。
14. 为 page、所有一级 region、region relationship、重要 component group、visual hierarchy、layout rule、excluded content、uncertainty 和 overall confidence 生成 `entity_assessments`。
15. 使用 `references/verification-checklist.md` 覆盖全部类别，按需读取 `references/analysis-error-taxonomy.md` 与 `references/correction-policy.md`，记录 findings。
16. 读取 `references/review-output-contract.md`，生成可审计 review；只有全部 approval gate 通过时才使用 `approved`。
17. 应用修正并生成完整 final JSON；保持正确 ID 稳定，清理已删除引用。
18. 运行 `scripts/validate_layout_reference_review.py <review-json> --draft <draft-json> --final <final-json>`。
19. 使用 A1 的 Schema 和 validator 验证 final。

即使没有明显错误，也必须生成 findings 可为空、verdict 为 `approved` 的 review。A2 不创建自己的 final analysis Schema。

## `approved` 门槛

不要因 JSON 合法、措辞合理、A1 confidence 高或没有 Schema 错误而直接批准。`approved` 必须同时满足：独立基线完成；主要 region 与 component group 均逐项审查；视觉焦点、交互焦点、主操作、次级操作和重复数量已核实；证据等级和 confidence 已重新校准；品牌隔离、用户关注说明和输入元数据均已检查；没有未解决核心问题；`approval_evidence` 对所有门槛提供非空、逐项通过记录。

文件名、像素尺寸和方向等确定性信息必须来自执行环境或可信图片元数据工具，不得由视觉模型估算。没有可信元数据时，记录 `input_metadata_unverified` finding，将 metadata comparison 标为 `unverified`，并避免 `approved` 与极高置信度。本 Skill 不新增图片读取程序。

## 输出要求

- review 必须符合 `schemas/layout-reference-review.schema.json`。
- final 必须符合 A1 的 `layout-reference-analysis.schema.json` 并通过 A1 validator。
- final 必须是独立完整分析，不是 patch，且不包含 review 专用字段。
- 下游 Composer 只消费 final，不消费 draft 或 review。

## 失败处理

- 原始截图不可访问：停止语义验证并说明原因。
- draft 不是合法 JSON 或未通过 A1 validator：报告结构错误，不继续生成 final。
- 页面严重裁切：保留可验证内容并记录限制。
- 重复数量无法确认：使用 `uncertain`，不得猜测。
- 页面类型无法确认：允许 final 使用 `unknown` 或 `hybrid`。
- review validator 失败：修正 review 后重新校验。
- final validator 失败：修正 final 后重新运行 A1 validator。
- 语义冲突无法解决：记录 `unresolved` finding 和下游处理，不伪造确定结论。

## 职责边界

只审查和修正从其他游戏 UI 截图提取的布局参考分析。不要分析自有风格图、生成 asset analysis、汇总多图、改写新页面需求、映射自有素材、生成新 UI、调用 Composer 或图片服务、制作示意图、切图、判断九宫格、输出引擎节点、实现真实 VLM API、创建 pipeline runner 或写入具体 run。
