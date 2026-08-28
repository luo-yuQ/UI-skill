# Stage0 文字清除实验索引

本目录记录 Stage0 文字清除的可复现实验。实验 ID 一经分配不因名称变化而重排；每次实验只改变一个主要变量。

当前文档只记录仓库中已经存在的代码和 CLI，不代表实验已经执行。运行产物统一写入被 `.gitignore` 忽略的 `runs/stage0-text-cleaning/`，实验记录本身保留在本目录。

## 实验索引

| ID | 实验 | 核心变量 | 状态 | 结论 |
|----|------|---------|------|------|
| [EXP-001](EXP-001.md) | Red Overlay Baseline | 当前红色区域标记图 + 内置默认 Prompt | planned（待 source/config） | - |
| [EXP-002](EXP-002.md) | Red Overlay + Strong Prompt | Prompt：内置 baseline → 冻结的 strong v2 | blocked（缺 strong v2 文件） | - |
| [EXP-003](EXP-003.md) | Binary Mask Input | 第二张参考图：red overlay → binary mask | blocked（复用 EXP-002 缺失的 prompt） | - |
| [EXP-004](EXP-004.md) | Local Glyph Destruction | 第一张参考图：source → 同 mask 本地破坏 glyph 后的图 | blocked（缺精确的预清除 CLI） | - |

## 本组固定路径

以下路径是本组实验的记录约定，不表示文件当前已经存在：

```text
runs/stage0-text-cleaning/
├── shared/
│   ├── source.png
│   └── strong-clean-repair-v2.txt
├── exp-001/
│   ├── ocr/
│   ├── regions/
│   └── image2/
├── exp-002/image2/
├── exp-003/image2/
└── exp-004/
    ├── local/precleared.png
    └── image2/
```

`source.png` 必须是所有四个实验使用的同一份原始截图。`strong-clean-repair-v2.txt` 必须在 EXP-002 首次运行前冻结，EXP-003 和 EXP-004 原样复用。

## 当前真实调用链

本组 A/B/C 对照采用：

```text
source.png
→ ui_text_extractor.py
→ texts.json + raw_text_mask.png + OCR 本地 cleaned/debug 图
→ ui_vlm_region_mask_poc.py
→ vlm-region-plan.json + region-mask.png + region-mask-overlay.png
→ ui_image_clean_repair_poc.py
→ clean.<真实扩展名> + result.json
```

选择 `ui_vlm_region_mask_poc.py` 的原因是：同一次 VLM 判断会同时生成完全对应的 binary mask 和 red overlay，EXP-002 与 EXP-003 因而能只切换第二张参考图。

Image-2 脚本的 `--mask-overlay` 只是“第二张普通 reference image”的参数名。即使传入 `region-mask.png`，请求也仍是 `POST /v1/images/generations` 的 `images[1]`，不是 `/images/edits`，也不是 API mask 字段。

## 相关真实脚本

| 流程 | 文件 | 真实 CLI / 关键产物 |
|------|------|---------------------|
| OCR 与本地 Telea 输出 | `game-ui-asset-extractor/scripts/ui_text_extractor.py` | `--image`、`--output-json`、`--output-mask`、`--output-cleaned`、`--output-debug` |
| 本组 VLM 区域判断 | `game-ui-asset-extractor/scripts/ui_vlm_region_mask_poc.py` | `vlm-region-plan.json`、`region-mask.png`、`region-mask-overlay.png` |
| Route B repair-mask 规划（本组不混用） | `game-ui-asset-extractor/scripts/ui_text_repair_planner.py` | `coverage-audit.json`、`text-repair-decisions.json`、`union-text-mask.png`、`repair-mask.png`、`repair-mask-overlay.png` |
| VLM audit + 本地 inpaint | `game-ui-asset-extractor/scripts/ui_vlm_text_auditor.py` | `final_inpaint_mask.png`、`pre_inpaint_image.png`、`cleaned_image.png`、`repair_comparison.png` |
| Image-2 clean repair | `game-ui-asset-extractor/scripts/ui_image_clean_repair_poc.py` | `clean.png/.jpg/.webp`、`result.json` |
| Image-2 配对生成 PoC（独立于 Stage0） | `game-ui-asset-extractor/scripts/image2_clean_pair_poc.py` | 不用于本组 A/B/C |
| 局部 smooth-surface/OpenCV 对照 | `game-ui-asset-extractor/experiments/smooth_surface_repair_poc.py` | 按 text ID 输出 crop 级 Telea/曲面拟合结果；不能生成本组需要的整图 `precleared.png` |

`ui_vlm_planner.py` 是原图与 OCR-cleaned 图的视觉资产规划 CLI，并不输出本组所需的删除区域 mask，因此不纳入这组调用链。

## 环境与当前产物状态

- OCR CLI 依赖其现有 PaddleOCR/OpenCV 环境。
- VLM CLI 默认模型为 `gpt-5.6-terra`，需要 `OPENAI_BASE_URL` 与 `OPENAI_API_KEY`（代码也兼容其已有备用环境变量）。
- Image-2 CLI 默认模型为 `gpt-image-2`，需要 `TOAPIS_API_KEY`；`TOAPIS_BASE_URL` 未设置时使用脚本默认地址。
- Image-2 输出尺寸不能通过该 CLI 手工固定；脚本按 source 长宽关系确定 `1024x1024`、`1024x1536` 或 `1536x1024`，并把选择写入 `result.json`。
- 当前仓库的 `runs/` 中没有 Stage0 文字清除输入或产物；因此 EXP-001～EXP-004 均尚未执行，也没有历史结果可回填。
- 当前缺少 `runs/stage0-text-cleaning/shared/source.png`。
- 当前缺少 `runs/stage0-text-cleaning/shared/strong-clean-repair-v2.txt`。
- 当前缺少“读取现成 `region-mask.png`，仅在该 mask 内破坏 glyph，并输出同尺寸整图”的 CLI。`ui_vlm_text_auditor.py` 虽会输出黑色 mask 区域的 `pre_inpaint_image.png`，但它会重新执行自己的 VLM audit 并形成另一套 `final_inpaint_mask.png`，不能在严格变量隔离下替代该缺口。

## 记录规则

1. 不覆盖旧实验输出；若需重复采样，在实验输出目录下增加人工命名的 trial 子目录，并在对应 EXP 文档中记录。
2. `result.json` 中已经保存实际 model、source、第二张图、provider size、输出尺寸和最终 prompt；观察结论仍须回填到 Markdown。
3. 每次必须检查 `999`、`12345/12345`、新伪文字、背景破坏和非目标区域变化。
4. EXP-002 与 EXP-001 比；EXP-003 与 EXP-002 比；EXP-004 与 EXP-003 比。不要跨过中间实验归因。
5. 不把 `clean.<ext>` 预写死为 `clean.png`；脚本根据下载内容保存真实扩展名。
