# A2 布局分析审查工作流

A2 必须先独立观察原始截图，再读取和挑战 A1 draft，避免被初稿锚定。原始截图不可省略；仅对 JSON 做文字审稿无法发现遗漏区域、数量错误、装饰误判和页面层级错误。

## 阶段 1：验证输入完整性

确认同时存在可访问的原始游戏 UI 截图和 A1 draft JSON。用户关注说明与 A1 validation result 可选。先运行 A1 validator 检查 draft；JSON 不可解析或结构非法时停止语义审查，报告结构错误，不生成伪造 final。

截图严重裁切时，只要仍能验证主要结构就继续，并记录限制；截图不可访问时停止语义验证。

## 阶段 2：独立观察截图

在读取 draft 之前建立内部基线：

- 页面大致用途与呈现形态；
- 一级功能区域；
- 最明显的主视觉和主要操作；
- 可见重复结构及可靠数量；
- 遮挡、裁切、水印或设备边框。

基线不必单独写文件，但必须在比较 draft 时使用。不要预先接受 A1 的页面类型、区域边界或交互判断。

## 阶段 3：页面级审查

读取 A1 draft，比较 `page_type`、`page_purpose`、`page_state`、`presentation_mode` 和 confidence。检查页面究竟是全屏、弹窗、HUD 还是混合结构。无法可靠分类时允许 final 使用 A1 契约中的 `unknown` 或 `hybrid`。

## 阶段 4：区域完整性审查

按 `verification-checklist.md` 检查大面积背景、全局状态、导航、主内容、对象列表、详情、角色/对象展示、主要操作、弹窗遮罩、覆盖层和通知区域。

不要因为有视觉边框就机械拆分。对遗漏项使用 `missing_region` 与 `added`；对无证据区域使用 `extra_region` 与 `removed`。

## 阶段 5：结构关系审查

分别检查：

- 视觉包含形成的父子关系；
- z-order 支持的覆盖关系；
- 空间位置支持的相邻关系；
- 有状态证据支持的控制或更新关系；
- 内容归属与重复结构所在区域。

“空间上相邻”“功能上从属”“可能存在交互控制”是不同结论。控制与更新关系通常属于 `inferred`，除非截图提供直接状态证据。

## 阶段 6：组件组与数量审查

检查 group 的类型、所属 region 和粒度。重点识别按钮被过度拆分、独立槽位被错误合并、列表未标为重复、装饰被当控件、背景角色被当交互入口等问题。

重新数当前可靠可见项目。裁切或遮挡时不得猜总量；必要时将数量设为 `null` 或使用不确定的 count certainty。

## 阶段 7：视觉层级审查

比较第一焦点、第二焦点、主操作、支持信息和背景内容。综合页面任务、位置、对比、尺寸和状态；不要把“面积最大”自动等同于“功能最重要”。无法确认主操作时保留不确定性。

## 阶段 8：证据纪律与置信度审查

复用 A1 的 `evidence-vs-inference.md`：直接可见内容使用 `observed`，合理 UI 推断使用 `inferred`，多解或证据不足使用 `uncertain`。删除无证据的点击结果、资源消耗、业务规则或页面跳转。

检查 confidence 是否随证据强度、图像清晰度和替代解释调整。过高置信度使用 `confidence_mismatch`；结论可能合理但不可确认时使用 `downgraded_to_uncertain`。

## 阶段 9：品牌隔离审查

复用 A1 的品牌隔离原则。删除或改写任何复用原游戏名称、Logo、角色与轮廓、具体图标、货币造型、文案、独有纹样、品牌色或具体美术资产的建议。只保留抽象布局、信息层级与空间关系。

## 阶段 10：形成 review

按需读取：

- `analysis-error-taxonomy.md` 选择 `error_type`；
- `correction-policy.md` 选择 correction action；
- `review-output-contract.md` 生成完整 review；
- `verification-checklist.md` 确认所有类别已覆盖。

每个 finding 记录问题、严重程度、受影响对象、截图证据、理由、修正动作、具体改动和置信度。即使没有问题，也生成 verdict 为 `approved` 的 review。

## 阶段 11：生成完整 final

final 必须是可独立使用的完整 A1 analysis，而不是 patch：

1. 保留 A1 正确内容和稳定 ID。
2. 应用已确认的新增、修改、删除和不确定性降级。
3. 清理所有已删除 ID 的引用。
4. 更新关系、视觉层级、layout rule、uncertainty 和置信度。
5. 不加入 review 专用字段。
6. 不创建另一套 final Schema。

## 阶段 12：双重校验

1. 运行 `../scripts/validate_layout_reference_review.py <review-json> --draft <draft-json> --final <final-json>`。
2. 使用 `../../game-ui-layout-reference-analyzer/scripts/validate_layout_reference_analysis.py <final-json>` 验证 final。
3. validator 失败时修正对应文件并重新运行。

若无法解决语义冲突，保留 `unresolved` finding 和下游处理建议。只有 review 与 final 均合法且 final 可信时，才把 `ready_for_downstream` 设为 `true`。
