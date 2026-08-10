# Game UI Auto Composer v2.1.1

Composer 进行一次 UI 设计合成：

```text
immutable A layout analysis
+ immutable B2 style profile
+ explicit user requirement
→ one candidate ui-compose-plan
```

设计由 Composer 完成；A/B ID 与 classification 的事实真假由确定性 Python 验证。

## V2.1.1

- 新增 `scripts/evidence_registry.py`，从真实 A/B JSON 自动收集 ID、类型、路径和 B classification。
- Registry 使用当前环境已有的 Pydantic 冻结模型，不修改上游数据。
- Layout origin：`layout_reference`、`user_requirement`、`composer_derived`。
- Style origin：`style_reference`、`user_requirement`、`composer_derived`。
- 只有 reference origin 必须提供真实引用；其他 origin 不再伪造证据。
- Validator 只 PASS/FAIL 并给出精确路径，不猜替代 ID、不 repair、不 retry。
- V2.1 hard requirements、6 商品 2×3、公会商店语义和 B local scope 规则保持不变。

## 验证

```powershell
python scripts/validate_input.py references/examples/example-ui-compose-input.json `
  --layout-source ../game-ui-layout-analysis-verifier/examples/example-final-analysis.json `
  --style-source ../game-ui-style-reference-analyzer/examples/b2-style-profile.json

python scripts/validate_plan.py references/examples/example-ui-compose-plan.json `
  --input references/examples/example-ui-compose-input.json

python -m unittest discover -s tests -p "test_*.py"
```

Known issue：required-position validator 尚未完整继承 parent-relative 子组件的 left/right 位置。本版本不扩大范围处理该问题。

Composer core 不修改 first-stage-runner，不创建 Runtime，不触碰 Preview Adapter、GPT Image 或 FairyGUI。

## License

MIT
