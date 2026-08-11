# Game UI Prompt Compiler v0.1

该 Skill 把 Composer 已确定的 UI 结构和 B2 已归纳的视觉风格编译成一份可直接交给图像生成模型的英文提示词：

```text
ui-compose-plan.json + style-profile.json -> image-prompt.txt
```

它是 compiler，不是 designer。它不会读取原始图片、追随 JSON 中的 `source_ref` 或文件路径、重新分析布局或风格、修改组件树和数量、处理 `missing_assets`，也不会调用 GPT Image 或任何 Provider Adapter。

## 使用方法

```powershell
python game-ui-prompt-compiler-skill/scripts/compile_image_prompt.py `
  --compose-plan path/to/ui-compose-plan.json `
  --style-profile path/to/style-profile.json `
  --output path/to/image-prompt.txt
```

输入文件按 UTF-8 JSON 读取。只有在 JSON 无法解析、没有有效页面、完全没有可用 UI 结构或完全没有可用风格信息时才失败。

输出固定包含六段：

```text
GOAL
CANVAS AND PAGE TYPE
COMPOSITION
VISUAL STYLE
HARD REQUIREMENTS
PRODUCTION CONSTRAINTS
```

其中 Composer 的精确数量、网格行列和位置要求会被显式强化；B2 的 `stable` 特征优先使用，`secondary` 仅在适用时使用，`local` 需要 Composer 明确限定到当前页面，`conflicting` 和 `uncertain` 不会被擅自定案。
