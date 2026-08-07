# A2 布局分析审查工作流

A2 必须先独立观察原始截图，再读取和挑战 A1 draft，避免被初稿锚定。原始截图不可省略；仅对 JSON 做文字审稿无法发现遗漏区域、数量错误、装饰误判和页面层级错误。

## 目录

- [阶段 1：输入门禁](#阶段-1输入门禁)
- [阶段 2：Independent Baseline](#阶段-2independent-baseline)
- [阶段 3：Draft Comparison](#阶段-3draft-comparison)
- [阶段 4：页面、区域与坐标](#阶段-4页面区域与坐标)
- [阶段 5：组件组、数量与次级操作](#阶段-5组件组数量与次级操作)
- [阶段 6：视觉与交互焦点](#阶段-6视觉与交互焦点)
- [阶段 7：证据、置信度与品牌](#阶段-7证据置信度与品牌)
- [阶段 8：逐对象审查](#阶段-8逐对象审查)
- [阶段 9：review、final 与双重校验](#阶段-9reviewfinal-与双重校验)

## 阶段 1：输入门禁

确认同时存在可访问的原始游戏 UI 截图和 A1 draft JSON。用户关注说明与 A1 validation result 可选。此时只检查 draft 文件存在、JSON 可解析及结构校验结果，不采用其中页面分类、区域名称、数量、焦点或交互结论。

截图严重裁切时，只要仍能验证主要结构就继续，并记录限制；截图不可访问时停止语义验证。

## 阶段 2：Independent Baseline

在读取 A1 语义内容之前独立观察截图，并把结果保存到 review 的 `independent_baseline`：

- `page_hypothesis` 与 `presentation_mode`；
- `major_region_summaries`；
- `component_group_summaries`；
- `visible_repeat_counts`；
- `primary_visual_focal_point`；
- `primary_interaction_focal_point`；
- `primary_action_candidate` 与 `secondary_action_candidates`；
- `visible_text_or_labels`；
- `capture_limitations`、`uncertainties` 与 `confidence`。

基线 region/group 使用自身的 `baseline_id`，不要预先复制 A1 ID 或命名。若运行环境已经把 draft 内容放入上下文，主动忽略其结论，重新完成截图观察后才允许比较。`major_region_summaries` 不得为空。

## 阶段 3：Draft Comparison

保存独立基线后才读取 draft，建立 `comparison_summary`，至少比较：页面类型与用途、presentation mode、每个主要 region、每个重要 component group、可见数量、视觉焦点、交互焦点、主/次操作、区域关系、证据等级、confidence、品牌隔离、用户关注说明覆盖和输入元数据一致性。

每项使用 `match`、`partial_match`、`mismatch`、`unverified` 或 `not_applicable`，并给出摘要和置信度。比较结论必须来自 baseline 与 draft 的显式对照，不接受“看起来合理”作为通过理由。

图片的 `width`、`height`、`orientation`、`file_name` 和 `source_ref` 必须与执行环境或可信元数据结果比较，不得由视觉模型猜测像素尺寸。无法获得可信元数据时，将 `metadata_consistency` 标为 `unverified`，创建 `input_metadata_unverified` finding，并降低批准强度。

## 阶段 4：页面、区域与坐标

读取 A1 draft，比较 `page_type`、`page_purpose`、`page_state`、`presentation_mode` 和 confidence。检查页面究竟是全屏、弹窗、HUD 还是混合结构。无法可靠分类时允许 final 使用 A1 契约中的 `unknown` 或 `hybrid`。

按 `verification-checklist.md` 检查大面积背景、全局状态、导航、主内容、对象列表、详情、角色/对象展示、主/次操作、弹窗遮罩、覆盖层和通知区域。

不要因为有视觉边框就机械拆分。对遗漏项使用 `missing_region` 与 `added`；对无证据区域使用 `extra_region` 与 `removed`。

对每个 `approximate_bounds` 检查：区域描述与位置是否基本一致；列表框是否覆盖主要列表；主操作框是否覆盖操作；子区是否大体位于父区；区域之间是否明显错位；边界是否遗漏其描述声称包含的元素。这里只要求粗粒度合理性，不要求像素级精度。明显错位使用 `wrong_region_boundary` + `modified`；只能粗略判断时降低 confidence 或标记不确定。

分别检查：

- 视觉包含形成的父子关系；
- z-order 支持的覆盖关系；
- 空间位置支持的相邻关系；
- 有状态证据支持的控制或更新关系；
- 内容归属与重复结构所在区域。

“空间上相邻”“功能上从属”“可能存在交互控制”是不同结论。控制与更新关系通常属于 `inferred`，除非截图提供直接状态证据。

## 阶段 5：组件组、数量与次级操作

检查 group 的类型、所属 region 和粒度。重点识别按钮被过度拆分、独立槽位被错误合并、列表未标为重复、装饰被当控件、背景角色被当交互入口等问题。

重新数当前可靠可见项目。裁切或遮挡时不得猜总量；必要时将数量设为 `null` 或使用不确定的 count certainty。

检查明显且功能独立的次级操作，例如创建账号、忘记密码、记住账号、设置、语言、关闭、返回、帮助、注册、游客登录或第三方登录。无需把普通小文字升级为一级 region，但应按粒度放入 component group、`secondary_action_region` 或 `visual_hierarchy.supporting_information`，不能完全遗漏。

## 阶段 6：视觉与交互焦点

分别审查三个概念：

- `primary_visual_focal_point`：最先吸引视觉注意的区域或元素。
- `primary_interaction_focal_point`：用户最主要进行输入、选择或操作的区域。
- `primary_action`：当前页面最主要的操作控件。

三者可以引用不同对象。综合页面任务、位置、对比、尺寸和状态；不要把面积最大或最亮自动等同于交互中心。A1 final Schema 没有独立交互焦点字段时，在 review 中保留区分，并在既有 `visual_hierarchy` 描述里采用最不歧义的表达。

## 阶段 7：证据、置信度与品牌

复用 A1 的 `evidence-vs-inference.md`：直接可见内容使用 `observed`，合理 UI 推断使用 `inferred`，多解或证据不足使用 `uncertain`。删除无证据的点击结果、资源消耗、业务规则或页面跳转。

检查 confidence 是否随证据强度、图像清晰度和替代解释调整。`observed` 不自动等于 0.95 以上；`inferred` 通常不应接近 1.0；存在裁切、小文字不可读、坐标不确定或 major finding 时应保守。不要用固定公式计算 confidence，而要记录证据、限制和替代解释。过高置信度使用 `confidence_mismatch`；结论可能合理但不可确认时使用 `downgraded_to_uncertain`。

复用 A1 的品牌隔离原则。删除或改写任何复用原游戏名称、Logo、角色与轮廓、具体图标、货币造型、文案、独有纹样、品牌色或具体美术资产的建议。只保留抽象布局、信息层级与空间关系。

## 阶段 8：逐对象审查

生成 `entity_assessments`，覆盖 page、每个 draft region、每个 region relationship、每个重要 component group、visual hierarchy、每条 layout rule、excluded content、每条 uncertainty 和 overall confidence。对 draft 对象使用 `confirmed`、`modified`、`removed` 或 `unresolved`；baseline 发现的遗漏对象使用 `added`。非 confirmed 状态必须关联 finding。

不得只输出整体 verdict。普通文字或微小装饰无需逐项 assessment，但一级区域、主/次操作、重复组件组、视觉焦点和重要关系必须有可审计记录。

## 阶段 9：review、final 与双重校验

按需读取：

- `analysis-error-taxonomy.md` 选择 `error_type`；
- `correction-policy.md` 选择 correction action；
- `review-output-contract.md` 生成完整 review；
- `verification-checklist.md` 确认所有类别已覆盖。

每个 finding 记录问题、严重程度、受影响对象、截图证据、理由、修正动作、具体改动和置信度。即使没有问题，也生成 verdict 为 `approved` 的 review。

`approved` 只能在所有 approval gate 通过时使用，并提供完整非空 `approval_evidence`。JSON 合法、A1 confidence 高或没有 Schema 错误均不足以批准。metadata 未验证、用户关注说明未覆盖、核心问题 unresolved、主要对象未 assessment 或缺少次级操作检查时不得 `approved`。

final 必须是可独立使用的完整 A1 analysis，而不是 patch：

1. 保留 A1 正确内容和稳定 ID。
2. 应用已确认的新增、修改、删除和不确定性降级。
3. 清理所有已删除 ID 的引用。
4. 更新关系、视觉层级、layout rule、uncertainty 和置信度。
5. 不加入 review 专用字段。
6. 不创建另一套 final Schema。

1. 运行 `../scripts/validate_layout_reference_review.py <review-json> --draft <draft-json> --final <final-json>`。
2. 使用 `../../game-ui-layout-reference-analyzer/scripts/validate_layout_reference_analysis.py <final-json>` 验证 final。
3. validator 失败时修正对应文件并重新运行。

若无法解决语义冲突，保留 `unresolved` finding 和下游处理建议。只有 review 与 final 均合法且 final 可信时，才把 `ready_for_downstream` 设为 `true`。
