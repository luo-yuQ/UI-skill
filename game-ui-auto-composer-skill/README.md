# Game UI Auto Composer v2

Composer v2 把三个权威来源合成为一个新的、引擎无关的 UI 设计意图：

```text
A final layout-reference analysis
+ B2 style profile
+ ordinary user requirement
→ ui-compose-plan v2
```

它不是 A/B 拼接器，也不再负责“已有素材应该放在哪里”。用户决定本次要设计什么，A 提供可复用的布局证据，B2 提供分级的视觉风格证据；Composer 可以继承、调整、删除、新增和重组，最后形成新的页面、组件树、布局意图与视觉方向。

## 输入

```json
{
  "schema_version": "2.0",
  "request": {
    "user_requirement": "设计一个公会委托页面……"
  },
  "layout_reference_analysis": {},
  "style_profile": {}
}
```

- `request.user_requirement`：目标业务、内容、数量、交互和显式变化的最高权威。
- `layout_reference_analysis`：直接接收 A 的完整 final，负责结构证据。
- `style_profile`：直接接收 B2 完整 profile，负责视觉语言证据。

输入 schema 直接引用相邻 A/B Skill 的权威 schema，不维护 Composer 私有副本。

## 输出

`ui-compose-plan.json` 使用 `schema_version: 2.0`，包含：

```text
project_context
design_summary
reference_application
visual_direction
pages
component_tree
layout_rules
interactions
navigation
generation_constraints
assumptions
warnings
```

`reference_application` 记录 A 的结构如何 adopted/adapted/ignored/rejected，以及 B2 trait 如何采用、限域、忽略或因冲突拒绝。`generation_constraints` 为后续生图阶段提供结构化约束，但不是 GPT Image prompt。

v2 不再输出 `asset_usages` 和 `missing_assets`。

## 权威与冲突

- 用户要求决定目标页面、业务内容、数量、交互和显式变化。
- A 只负责布局组织，不负责目标业务内容。
- B2 只负责视觉语言，不覆盖 A 的正式布局关系。
- 用户显式要求覆盖 A/B，并记录适配或 style deviation。
- B2 `stable` 优先；`secondary` 按语境；`local` 仅限语义匹配组件；`conflicting` 不静默选边；`uncertain` 不变成事实。
- 参考图中的城堡、战斗、角色、奖励、文案等语义不是用户需求，不得自动泄漏到目标页面。

## 验证

在 `game-ui-auto-composer-skill` 目录运行：

```powershell
python scripts/validate_input.py references/examples/example-ui-compose-input.json
python scripts/validate_plan.py references/examples/example-ui-compose-plan.json
python -m unittest discover -s tests -p "test_*.py"
```

真实 v2 示例：

- `references/examples/example-ui-compose-input.json`
- `references/examples/example-ui-compose-plan.json`

## 边界

Composer core 不接收原图，不重新视觉识别，不生成图片、GPT Image prompt、HTML/CSS、FairyGUI XML 或 Laya/Unity/Cocos/FairyGUI 实现字段。

`asset-analysis.schema.json`、旧 samples、asset taxonomy、templates、engine compatibility、prototype helpers、GPT Image/ToAPIs preview adapter 等仍保留为 legacy 或下游资源，但不属于 v2 core。现有 adapter 尚未迁移到 v2 plan，后续应独立处理。

## License

MIT
