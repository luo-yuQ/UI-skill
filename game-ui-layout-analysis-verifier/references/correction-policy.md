# A2 修正策略

每个 finding 必须使用一个稳定 `correction_action`。动作描述 A2 对 A1 结论的处置，不代表引擎操作。

## `confirmed`

A1 内容与截图证据一致，无需修改。用于明确记录重要结论已被复核；通常 severity 为 `info`，不计为已应用变更。

## `modified`

保留同一逻辑对象和 ID，但修改类型、描述、粗略边界、层级、数量、关系、证据级别或置信度。小幅修正应尽量保留原 ID，并同步更新所有依赖描述。

## `added`

截图中存在但 A1 遗漏。为新 region、relationship、component group、layout rule 或 uncertainty 分配稳定新 ID，并补齐所有必需引用。

## `removed`

A1 中的对象缺少截图证据、属于误判或不适合作为布局参考。删除后清理 parent、relationship、visual hierarchy、layout rule 和 uncertainty 中的全部引用。

## `downgraded_to_uncertain`

结论可能合理，但当前截图不能可靠确认。保留必要描述，将证据级别改为 `uncertain`、降低置信度，并在 final 的 `uncertainties` 中记录保守处理方式。

## `unresolved`

A2 无法在现有截图中可靠决定。记录冲突、原因、受影响对象和下游处理；不得选择看似合理但无证据的答案。对应 finding 还必须出现在 `unresolved_findings`。

## ID 稳定原则

- 确认或小幅修改时保留 ID。
- 对象拆分时删除原对象并为新对象分配多个 ID。
- 多个对象合并时删除旧对象并创建一个新 ID。
- 不因排序或措辞调整批量修改 ID。
- final 不得引用已删除 ID。
- `added` finding 可在 review 中提前引用准备加入 final 的 ID。

## 修改优先级

1. 页面类型与主要用途。
2. 大区域遗漏和主操作误判。
3. 区域层级与关系。
4. 组件组与可见数量。
5. 证据等级与置信度。
6. 品牌内容泄漏。
7. 术语和轻微措辞。

`modified`、`added`、`removed` 和 `downgraded_to_uncertain` 计为已应用变更；`confirmed` 与 `unresolved` 不计为已应用变更。
