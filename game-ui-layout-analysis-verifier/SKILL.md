---
name: game-ui-layout-analysis-verifier
description: 审查一张其他游戏完整 UI 截图及其 A1 布局分析初稿，检查页面用途、遗漏区域、结构关系、组件组、重复数量、视觉层级、证据等级、过度推断、置信度和品牌内容混入，并生成结构化 review 与符合 A1 契约的完整 final analysis。用于已经完成 A1 初步分析且原始截图仍可访问的场景；不用于 UI 生成、切图、风格图分析、引擎节点输出、像素级检测或具体 VLM 服务调用。
---

# 游戏 UI 布局分析审查器

同时读取原始游戏 UI 截图和 A1 `layout-reference-analysis.draft.json`，先独立观察截图，再挑战初稿结论。输出 `layout-reference-review.json` 和完整的 `layout-reference-analysis.final.json`。

## 必需输入

- 原始游戏 UI 截图；
- 可解析且通过 A1 validator 的 draft JSON。

可选输入为用户关注说明和 A1 validation result。validation result 只提供辅助信息，不能替代截图。不要只根据 draft 做文本审稿；否则无法发现遗漏区域、数量错误、装饰误判和层级错误。

## 工作流

1. 验证截图与 draft 同时存在。读取 `references/verification-workflow.md`。
2. 在读取 draft 前独立观察原始截图，建立页面结构检查基线。
3. 记录页面用途、呈现形态、一级区域、焦点、主操作、重复结构和图像限制。
4. 读取 A1 draft，并确认其通过 `../game-ui-layout-reference-analyzer/scripts/validate_layout_reference_analysis.py`。
5. 检查页面类型、用途、状态和 presentation mode。
6. 检查主要区域完整性。
7. 检查区域拆分、合并、粗略边界和父子关系。
8. 检查相邻、覆盖、从属、控制和更新关系。
9. 检查组件组粒度、类型、归属与重复数量。
10. 检查视觉焦点、主要操作、支持信息和背景内容。
11. 检查 `observed`、`inferred`、`uncertain` 与功能推断。
12. 检查品牌和具体美术隔离。
13. 检查局部及整体置信度。
14. 使用 `references/verification-checklist.md` 覆盖全部审查类别，按需读取 `references/analysis-error-taxonomy.md` 与 `references/correction-policy.md`，记录 findings。
15. 读取 `references/review-output-contract.md`，应用修正并生成完整 final JSON；保持正确 ID 稳定，清理已删除引用。
16. 运行 `scripts/validate_layout_reference_review.py <review-json> --draft <draft-json> --final <final-json>`。
17. 使用 A1 的 `../game-ui-layout-reference-analyzer/schemas/layout-reference-analysis.schema.json` 和 validator 验证 final。

即使没有明显错误，也必须生成 findings 可为空、verdict 为 `approved` 的 review。A2 不创建自己的 final analysis Schema。

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
