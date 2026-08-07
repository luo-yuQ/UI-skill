# A1 输出契约

本文解释 `layout-reference-analysis.draft.json` 的字段语义。机器可校验约束以 `../schemas/layout-reference-analysis.schema.json` 为准。输出只描述粗粒度布局参考，不是切图、像素检测或引擎节点定义。

## 顶层结构

- `schema_version`：固定为 `0.1`。
- `analysis_id`：单次分析的稳定逻辑标识；使用英文小写、数字、下划线或连字符，不包含绝对路径。
- `input_kind`：固定为 `layout_reference_screenshot`。
- `source`：输入截图的声明信息。validator 不打开或检查真实图片。
- `user_focus`：用户可选的关注说明；未提供时为 `null`。
- `page`：整页用途、状态和呈现方式。
- `regions`：页面一级功能区域，至少一个。
- `region_relationships`：区域之间的空间、从属或控制关系。
- `component_groups`：位于 region 与单个控件之间的逻辑组件组。
- `visual_hierarchy`：页面焦点、主操作与支持信息的层级。
- `layout_rules`：可用于原创设计的抽象布局规律，至少一条。
- `excluded_content`：不应继承的品牌、美术或具体内容。
- `uncertainties`：截图无法支持可靠结论的事项。
- `overall_confidence`：整体置信度，范围为 `0` 到 `1`。
- `notes`：无法归入其他字段的必要补充字符串列表；不要作为随意倾倒信息的字段。

## `source`

- `source_ref`：调用方提供的不透明来源标识；不得据此猜测画面内容。
- `file_name`：原始文件名，不包含绝对路径。
- `width`、`height`：调用方或视觉系统已知的像素尺寸，均为正整数。
- `orientation`：`landscape`、`portrait` 或 `square`。
- `capture_limitations`：裁切、遮挡、水印、设备边框、状态栏等限制；没有限制时为空数组。

## `page`

- `page_type`：优先使用页面分类表中的稳定 ID；无法可靠分类时使用 `unknown`，多个同等重要职能并存时使用 `hybrid`。
- `page_purpose`：页面帮助玩家完成的主要目标。
- `page_state`：当前可见状态，例如默认、选中对象或弹窗覆盖状态；只写有证据支持的内容。
- `presentation_mode`：`fullscreen`、`modal`、`hud`、`hybrid` 或 `unknown`。
- `confidence`：页面判断置信度，范围为 `0` 到 `1`。

## `regions`

每个 region 包含：

- `region_id`：文档内唯一 ID。
- `region_type`：稳定 region 类型或以 `_region` 结尾的自定义英文类型。
- `label`：简短中文标签。
- `description`：区域的可见内容与布局职责。
- `parent_region_id`：父区域 ID；顶层区域为 `null`。
- `approximate_bounds`：归一化 `x`、`y`、`width`、`height`，各值范围为 `0` 到 `1`。
- `z_order`：粗略堆叠顺序；数值越大表示越靠前。
- `evidence_level`：`observed`、`inferred` 或 `uncertain`。
- `confidence`：局部判断置信度。

归一化坐标只用于粗略布局参考，不代表切图边界或像素级检测结果。

## `region_relationships`

每条关系包含唯一的 `relationship_id`、来源 `source_region_id`、目标 `target_region_id`、`relationship_type`、说明、证据级别和置信度。关系类型为：

- `parent-child`
- `controls`
- `updates`
- `belongs-to`
- `overlays`
- `adjacent-to`
- `repeated-within`

关系两端必须引用现有 region。不要把视觉相邻直接断言为交互控制。

## `component_groups`

每个组件组包含唯一的 `group_id`、`group_type`、标签、所属 `region_id`、描述、`visible_item_count`、`repeat_pattern`、证据级别和置信度。

- `visible_item_count`：当前截图中可靠可见的项目数；看不清时为 `null`，不得猜测列表总量。
- `repeat_pattern.is_repeated`：是否观察到重复结构。
- `repeat_pattern.direction`：`none`、`row`、`column`、`grid`、`carousel`、`list` 或 `unknown`。
- `repeat_pattern.count_certainty`：`exact`、`at_least`、`uncertain` 或 `not_applicable`。
- `repeat_pattern.notes`：解释遮挡、裁切或重复判断；无补充时为 `null`。

## `visual_hierarchy`

层级条目使用统一的实体引用结构：`entity_id`、`description`、`evidence_level`、`confidence`。`entity_id` 必须引用现有 region 或 component group。

- `primary_focal_point`：一个主要视觉焦点。
- `secondary_focal_points`：零个或多个次级焦点。
- `primary_action`：主要操作；无法可靠判断时为 `null`。
- `supporting_information`：支持主任务的信息。
- `background_content`：背景或装饰内容，不应被误认成控件。

## `layout_rules`

每条规则包含唯一 `rule_id`、抽象 `description`、`source_evidence` 实体 ID 列表、`reuse_value` 和 `confidence`。规则应描述分区、比例、排列、层级或空间节奏，不得指示复制具体角色、图标、文案或品牌风格。

## `excluded_content`

每项包含 `category`、`description` 和 `reason`。类别覆盖游戏名称、Logo、角色身份与轮廓、具体图标、货币造型、原始文案、独有装饰纹样、品牌色组合、受保护视觉识别元素及其他具体内容。这里只做设计隔离，不给出法律结论。

## `uncertainties`

每项包含唯一 `uncertainty_id`、`description`、受影响的实体 ID 列表 `affected_entities`、`reason` 和 `recommended_handling`。实体引用必须存在；处理建议应倾向保留不确定性、请求更清晰截图或避免依赖该判断。
