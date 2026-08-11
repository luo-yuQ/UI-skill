---
name: game-ui-layout-reference-analyzer
description: 分析用户提供的其他游戏完整 UI 截图，提取页面用途、一级功能区域、区域关系、组件组、重复结构、视觉层级和可供原创设计参考的布局规律，并输出 engine-neutral 的结构化初步分析。用于完整游戏页面、弹窗、商城、背包或英雄选择等截图，可结合一条用户关注说明；不用于切图、新 UI 生成、引擎节点输出、具体 VLM 调用或像素级检测。
---

# 游戏 UI 布局参考分析器

读取一张其他游戏的完整或接近完整 UI 截图，生成 `layout-reference-analysis.draft.json`。始终先分析整页和一级区域，再分析小控件。

## 适用输入

- 完整游戏 UI 页面截图；
- 游戏弹窗、商城页、背包页、英雄选择页等可视化界面；
- 可选的一条用户关注说明。

## 不适用输入

- 单个图标、按钮、角色立绘或背景素材；
- 用户自有游戏风格图；
- UI 生成、图片编辑、切图或九宫格任务；
- FairyGUI、Laya、Unity、Cocos 或其他引擎实现；
- 没有可访问图片的纯文字请求。

## Runner-managed Source Metadata

当 A1 在 First Stage Runner 中执行时，以下字段由 Runner 的确定性代码负责：

- `source.source_ref`
- `source.file_name`
- `source.width`
- `source.height`
- `source.orientation`

A1 不需要通过视觉推理估算这些值。A1 仍负责语义分析，以及
`source.capture_limitations` 等观察性内容。

不要删除这些 schema 字段，也不要修改当前 schema。Runner 会在 schema validation
前使用 `runner/scripts/inject-a1-source.py` 覆盖为真实文件 metadata。

## 工作流

1. 验证输入。读取 `references/analysis-workflow.md` 的输入检查和完整流程；不满足完整 UI 条件时说明无法分析的原因。
2. 观察整张截图，记录方向、裁切、遮挡、水印和设备边框等限制。
3. 判断页面用途。按需读取 `references/game-ui-page-taxonomy.md`。
4. 识别一级区域。按需读取 `references/layout-region-taxonomy.md`。
5. 识别区域之间的空间、从属、覆盖、控制和更新关系。
6. 识别区域内的组件组。按需读取 `references/component-group-taxonomy.md`。
7. 识别重复结构，只记录可靠可见数量。
8. 分析主要焦点、次级焦点、主操作、支持信息和背景内容。
9. 区分事实、推断和不确定项。按需读取 `references/evidence-vs-inference.md`。
10. 提取可供原创 UI 设计参考的抽象布局规则。
11. 隔离品牌和具体美术内容。按需读取 `references/brand-isolation-guidelines.md`。
12. 按需读取 `references/output-contract.md`，生成 draft JSON。
13. 使用 `schemas/layout-reference-analysis.schema.json` 作为输出契约，并运行 `scripts/validate_layout_reference_analysis.py <analysis-json>`。

不要调用具体 VLM、图片生成服务或网络 API。不要读取、切分或修改图片文件。不要创建 run、manifest 或 pipeline 产物。

## 失败处理

- 图片不是完整或接近完整 UI：停止并返回无法分析的原因。
- 页面类型无法确定：使用 `unknown`；多个同等重要职能并存时使用 `hybrid`。
- 元素数量看不清：将 `visible_item_count` 设为 `null` 并记录不确定性，不猜精确数量。
- 无法判断交互：使用 `uncertain` 证据级别并降低置信度。
- 用户关注说明与图片冲突：以图片证据为准，记录冲突。
- validator 失败：修正数据并重新校验，不把非法 JSON 交给下游。

## 输出边界

只输出 engine-neutral 的初步布局分析。不要生成新 UI、图片提示词、素材提取方案、像素级切图框、九宫格判断、引擎节点、XML、Prefab、Scene 或 A2 审查结论。
