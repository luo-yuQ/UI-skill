# A2 固定审查清单

对每次审查逐组覆盖。清单用于发现语义问题，不替代 review Schema、A2 validator 或 A1 final validator。

## Independent Baseline

- [ ] 在读取 A1 语义结论前完成独立截图观察。
- [ ] baseline 的主要区域摘要非空，且使用独立 `baseline_id`。
- [ ] 页面假设、呈现模式、组件组、重复数量和可见标签已记录。
- [ ] 第一视觉焦点与主要交互焦点分别记录。
- [ ] 主操作候选和明显次级操作候选已记录。
- [ ] 裁切、遮挡、小字不可读等限制和 baseline uncertainties 已记录。
- [ ] baseline confidence 有证据说明，不继承 A1 confidence。

## Draft Comparison

- [ ] baseline 保存后才读取 A1 的页面、区域、数量和交互结论。
- [ ] `comparison_summary` 覆盖页面、区域、组件组、数量和视觉层级。
- [ ] evidence discipline、metadata consistency 和 user-focus coverage 已明确记录。
- [ ] 每个差异都能追溯到 baseline、截图证据或可信元数据。

## 输入完整性

- [ ] 原始截图存在且可供视觉能力访问。
- [ ] A1 draft 存在且可解析。
- [ ] A1 draft 通过 A1 validator。
- [ ] 可选的 A1 validation result 只作为辅助信息。
- [ ] 用户关注说明与图片冲突时，以截图证据为准并记录冲突。
- [ ] `source.width`、`source.height`、`orientation`、`file_name` 和 `source_ref` 已与可信执行环境信息比较。
- [ ] 无可信图片元数据时没有让视觉模型猜像素尺寸，并创建 metadata unverified finding。

## 页面判断

- [ ] `page_type` 与页面主要用途相符。
- [ ] `page_purpose` 有截图证据，不扩展到不可见业务流程。
- [ ] `page_state` 没有把推测写成已发生事实。
- [ ] `presentation_mode` 正确区分全屏、弹窗、HUD 与混合形态。

## 区域完整性

- [ ] 没有遗漏大面积背景或主内容区。
- [ ] 没有遗漏主要操作。
- [ ] 导航、全局状态、对象列表和详情等重要区域均被检查。
- [ ] 弹窗遮罩、覆盖层与底层页面关系得到表达。
- [ ] 重要通知和状态区域没有因面积小而被忽略。

## 区域结构

- [ ] 没有因视觉边框而过度拆分。
- [ ] 没有把多个独立职责错误合并。
- [ ] `parent_region_id` 与视觉包含关系一致。
- [ ] 覆盖、相邻和父子关系没有混淆。
- [ ] 控制、更新和归属关系均有足够证据并使用正确方向。
- [ ] `approximate_bounds` 与区域描述和主要可见内容基本一致。
- [ ] 子区域粗略位于父区域内，列表框与操作框没有明显错位。

## 组件组

- [ ] 一个按钮没有被拆成多个逻辑组件组。
- [ ] 列表、网格或卡片集合被表达为重复结构。
- [ ] 多个独立奖励槽没有被错误合并为单个控件。
- [ ] 装饰没有被识别为控件。
- [ ] 控件没有被误归为背景装饰。
- [ ] group 粒度位于 region 与单控件之间。

## 重复数量

- [ ] `visible_item_count` 与当前截图可见数量一致。
- [ ] 被遮挡或裁切的数量没有被猜测。
- [ ] 当前可见数量与可能的列表总量已区分。
- [ ] 背景重复纹样没有被当作重复组件。
- [ ] `count_certainty` 与可见证据一致。

## 视觉层级

- [ ] 第一视觉焦点符合对比、位置和页面任务的综合证据。
- [ ] 主要交互焦点已独立检查，没有与第一视觉焦点机械合并。
- [ ] 主要操作识别合理；无法确认时允许为 `null` 或不确定。
- [ ] 创建账号、忘记密码、设置、语言、关闭、返回、帮助或其他明显次级入口没有被完全遗漏。
- [ ] 次级焦点、支持信息和背景内容已区分。
- [ ] 没有把面积最大或亮度最高机械等同于功能最重要。

## 证据与推断

- [ ] `observed` 内容能够直接从截图看见。
- [ ] `inferred` 内容有明确可见依据并使用限定语。
- [ ] 多解或证据不足内容使用 `uncertain`。
- [ ] 没有无证据的点击结果、资源消耗、网络行为或页面跳转。
- [ ] 控制和功能关系没有被空间相邻替代。
- [ ] confidence 与证据等级和图像限制相匹配。

## 品牌隔离

- [ ] 没有建议继承原游戏名称或 Logo。
- [ ] 没有建议继承角色形象或轮廓。
- [ ] 没有建议继承具体图标、货币造型或文案。
- [ ] 没有建议继承独有纹样、品牌色组合或具体美术资产。
- [ ] `layout_rules` 只保留抽象分区、层级、密度和空间关系。

## 输出质量

- [ ] review 记录所有重要修改及其截图证据。
- [ ] finding 计数、严重程度和 correction action 一致。
- [ ] unresolved finding 明确记录原因与下游处理。
- [ ] final 是完整分析，不是 patch。
- [ ] final 不残留被删除对象的引用。
- [ ] final 通过 A1 validator。
- [ ] review 通过 A2 validator。
- [ ] finalization 与 summary 的下游就绪状态一致。
- [ ] 只有验证通过且可信的 final 才标记可供 Composer 使用。
- [ ] `entity_assessments` 覆盖 page、所有一级 region 和重要 component group。
- [ ] 重要 relationship、visual hierarchy、layout rule、excluded content、uncertainty 和 overall confidence 有逐项结论。
- [ ] 非 confirmed assessment 均关联现有 finding。
- [ ] verdict 为 `approved` 时 `approval_evidence` 非空并覆盖全部 approval gate。
- [ ] `issue_count: 0` 时仍保留完整 baseline、comparison 和逐对象通过证据。
