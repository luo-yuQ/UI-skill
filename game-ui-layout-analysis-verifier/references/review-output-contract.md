# A2 审查输出契约

本文解释 `layout-reference-review.json` 的语义。机器约束以 `../schemas/layout-reference-review.schema.json` 为准。review 只记录审查过程和修正决策，不嵌入 final analysis；final 继续使用 A1 的正式契约。

## 顶层字段

- `schema_version`：固定为 `0.1`。
- `review_id`：单次审查的逻辑 ID，不包含绝对路径或敏感信息。
- `input_kind`：固定为 `layout_reference_analysis_review`。
- `source`：原始截图的不透明引用和文件名；validator 不打开截图。
- `source_analysis`：被审查的 A1 draft 标识、引用、版本和结构校验状态。
- `independent_baseline`：读取 A1 语义结论前形成的独立截图观察。
- `comparison_summary`：baseline 与 draft 的固定维度对照结果。
- `review_summary`：总体结论、问题计数、可下游使用状态和审查置信度。
- `findings`：逐项问题、证据和修正动作；没有问题时可以为空。
- `category_assessments`：固定审查维度的覆盖结果。
- `entity_assessments`：page、region、group 等重要对象的逐项结论。
- `approval_evidence`：`approved` verdict 的逐门禁通过证据；其他 verdict 可为空。
- `unresolved_findings`：无法可靠解决的问题。
- `finalization`：final analysis 的引用、校验状态和 finding 应用情况。
- `notes`：必要补充字符串列表，不重复 findings。

## `source`

- `screenshot_source_ref`：调用方提供的不透明截图来源标识。
- `file_name`：原始截图文件名，不包含绝对路径。

截图必须供 A2 的视觉能力访问，但 review validator 只校验声明，不读取图片。

## `source_analysis`

- `analysis_id`：A1 draft 的 `analysis_id`。
- `analysis_ref`：draft 的相对引用或不透明引用。
- `schema_version`：固定为与 A1 当前契约一致的 `0.1`。
- `validation_status`：`valid`、`invalid` 或 `not_run`。

只有结构合法的 draft 才能进入正常语义审查。validation result 是辅助信息，不能代替独立查看截图。

## `independent_baseline`

该对象必须在读取 A1 语义内容前形成，并包含：

- `page_hypothesis`、`presentation_mode`；
- 非空的 `major_region_summaries`，每项使用独立 `baseline_id`、标签、描述、粗略位置和 confidence；
- `component_group_summaries` 与 `visible_repeat_counts`；
- 相互独立的 `primary_visual_focal_point`、`primary_interaction_focal_point` 和 `primary_action_candidate`；
- `secondary_action_candidates`；
- `visible_text_or_labels`；
- `capture_limitations`、`uncertainties` 和整体 `confidence`。

baseline 不使用 A1 region/group ID 作为先验。焦点和操作候选采用描述、证据级别和 confidence，不要求映射到 final ID。

## `comparison_summary`

固定包含：

- `page_match`
- `region_coverage`
- `component_group_coverage`
- `repeat_count_match`
- `visual_hierarchy_match`
- `evidence_discipline`
- `metadata_consistency`
- `user_focus_coverage`

每项包含 `status`、`summary` 和 `confidence`。status 为 `match`、`partial_match`、`mismatch`、`unverified` 或 `not_applicable`。metadata 信息必须来自执行环境或可信图片元数据，不得由视觉模型估算；无法验证时使用 `unverified` 并创建 finding。

## `review_summary`

- `verdict`：`approved`、`approved_with_minor_corrections`、`approved_with_major_corrections` 或 `rejected`。
- `issue_count`：`findings` 总数，包括 `info` finding。
- `critical_issue_count`、`major_issue_count`、`minor_issue_count`：分别对应 severity 计数；`info` 不计入三项。
- `changes_applied`：是否存在已应用的 `modified`、`added`、`removed` 或 `downgraded_to_uncertain` 动作。
- `ready_for_downstream`：final 是否适合下游消费。
- `review_confidence`：审查整体置信度，范围 `0` 到 `1`。
- `summary`：简短说明结论与最重要修改。

判定含义：

- `approved`：没有需要修改的问题；仍必须输出 review。
- `approved_with_minor_corrections`：修改不影响整体页面骨架。
- `approved_with_major_corrections`：存在重要遗漏或结构修正，但已形成可信 final。
- `rejected`：无法形成可信 final，`ready_for_downstream` 必须为 `false`。

## `findings`

每项包含：

- `finding_id`：review 内唯一 ID。
- `error_type`：使用 `analysis-error-taxonomy.md` 的稳定错误类型。
- `severity`：`critical`、`major`、`minor` 或 `info`。
- `correction_action`：使用 `correction-policy.md` 的动作。
- `affected_entities`：结构化对象 ID 列表；`added` 可引用准备加入 final 的新 ID。
- `description`：问题是什么。
- `screenshot_evidence`：截图中支持审查判断的客观证据。
- `rationale`：为何需要该修正。
- `proposed_change`：最终采用或建议采用的改动。
- `confidence`：该 finding 的置信度。

不要把字符串中偶然出现的 ID 当作引用；只有 `affected_entities` 等结构化字段用于一致性检查。

## `category_assessments`

使用以下固定类别：

- `page_understanding`
- `region_completeness`
- `region_structure`
- `component_groups`
- `repeat_counts`
- `visual_hierarchy`
- `evidence_discipline`
- `brand_isolation`
- `confidence_calibration`

每项包含 `category`、`status`、`summary` 和 `confidence`。`status` 为 `pass`、`pass_with_notes`、`needs_correction` 或 `unresolved`。同一类别不得重复。

## `entity_assessments`

每项包含：

- `entity_type`：`page`、`region`、`region_relationship`、`component_group`、`visual_hierarchy`、`layout_rule`、`excluded_content`、`uncertainty` 或 `overall_confidence`。
- `entity_id`：A1 对象 ID；集合级对象使用稳定 ID，如 `page`、`visual_hierarchy`、`excluded_content`、`overall_confidence`。
- `status`：`confirmed`、`modified`、`added`、`removed` 或 `unresolved`。
- `summary`：逐对象审查结论。
- `related_finding_ids`：支持非 confirmed 结论的 finding ID。
- `confidence`：assessment 置信度。

提供 draft 时，validator 要求覆盖 page、所有 region、所有 relationship、所有 component group、visual hierarchy、所有 layout rule、excluded content、所有 uncertainty 和 overall confidence。baseline 发现的遗漏对象使用 `added`。

## `approval_evidence`

每项包含固定 `check`、`status` 和 `summary`。`approved` 时必须非空、覆盖所有 gate 且每项为 `pass`：独立基线、页面一致性、region/group 覆盖、主操作、视觉与交互焦点、重复数量、次级操作、证据、confidence、品牌、用户关注、元数据和可审计记录。

`issue_count: 0` 不能替代批准证据。其他 verdict 可将该数组留空。

## `unresolved_findings`

每项包含 `finding_id`、`description`、`affected_entities`、`reason`、`downstream_handling` 和 `confidence`。ID 应对应一个 `correction_action: unresolved` 的 finding。保留冲突和处理限制，不伪造确定结论。

## `finalization`

- `final_analysis_ref`、`final_analysis_id`：final 的引用和 A1 `analysis_id`；`rejected` 且未形成 final 时可为 `null`。
- `final_validation_status`：`valid`、`invalid` 或 `not_run`。
- `applied_finding_ids`：所有已应用变更 finding 的 ID。
- `unresolved_finding_ids`：所有未解决 finding 的 ID。
- `ready_for_downstream`：必须与 `review_summary.ready_for_downstream` 一致。

final 必须是完整、独立的 A1 analysis，不是 patch，也不包含 review 专用字段。下游只消费 final。
