# Game UI Auto Composer v2.1

Composer 把三类权威输入合成为新的、引擎无关的 UI 设计意图：

```text
显式用户需求（最高优先级）
+ immutable A final layout analysis
+ immutable B2 style profile
→ ui-compose-plan v2.1
```

A 只提供布局证据，B2 只提供分级风格证据；Composer 不重新识图，不复制参考业务语义，也不把示例当目标内容。

## V2.1 严格规则

- 优先级：显式用户要求 > 派生用户意图 > A > B > Composer 假设。
- `project_context.hard_requirements` 保存页面语义、数量、网格、位置和动作，并引用用户原文片段。
- 所有 A `source_ids` 与 B `trait_id` 都对真实输入做存在性与类型校验。
- 内嵌 A/B 可与原文件做 JSON deep equality；任何差异按路径报错。
- B local trait 默认忽略；采用时仅限一个匹配组件，除非用户原文明确授权提升。
- `generation_constraints` 只能从最终设计派生，不得二次创作。
- 输出各 section 必须在数量、网格、页面语义、位置和动作上相互一致。

## 输入与输出

输入 schema 是 `schemas/ui-compose-input.schema.json` v2.1，输出 schema 是 `schemas/ui-compose-plan.schema.json` v2.1。v2 主要顶层结构保持不变，仅增加硬需求账本、网格字段和 local promotion 记录。

真实回归 fixture：

- `references/examples/example-ui-compose-input.json`
- `references/examples/example-ui-compose-plan.json`

示例只展示 schema shape，绝不提供其他运行的数量、语义、组件、CTA 或文案。

## 验证

```powershell
python scripts/validate_input.py references/examples/example-ui-compose-input.json `
  --layout-source ../game-ui-layout-analysis-verifier/examples/example-final-analysis.json `
  --style-source ../game-ui-style-reference-analyzer/examples/b2-style-profile.json

python scripts/validate_plan.py references/examples/example-ui-compose-plan.json `
  --input references/examples/example-ui-compose-input.json

python -m unittest discover -s tests -p "test_composer_v2.py"
```

Composer core 不接收原图，不生成图片、GPT Image prompt、HTML/CSS、FairyGUI XML 或引擎实现字段。Preview Adapter 与 GPT Image 逻辑不属于本次 V2.1 core 修正。

## License

MIT
