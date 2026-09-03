对，当前 Stage 0 基线流程是：

```text
原图
  → OCR：识别文字、生成 OCR JSON
  → VLM：读取原图 + OCR JSON，二次确认文字位置/归属
  → 生成红色区域标记图 region-mask-overlay.png
  → Image-2：原图 + 红色标记图 + Prompt
  → 输出去文字后的 clean 图片
```

有一点需要纠正：传给 VLM 的不是“经过 OCR 的图片”，而是：

- 原始截图
- OCR 生成的 `texts.json`

VLM 返回结构化文字区域，脚本再根据这些区域生成红色 overlay。Image-2 才会同时收到两张图片。

下面命令均从仓库根目录 `E:\game-ui-skill` 执行。

## 0. 准备输入图片和环境变量


Set-Location "E:\game-ui-skill"

$stage0Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stage0RunDir = "runs/stage0-text-cleaning-$stage0Timestamp"

New-Item -ItemType Directory -Force `
  "$stage0RunDir/shared", `
  "$stage0RunDir/exp-001/ocr", `
  "$stage0RunDir/exp-001/regions", `
  "$stage0RunDir/exp-001/image2"

```powershell
Set-Location "E:\game-ui-skill"

New-Item -ItemType Directory `
  -Force `
  "runs/stage0-text-cleaning/shared" | Out-Null

Copy-Item `
  "你的原始截图.png" `
  "runs/stage0-text-cleaning/shared/source.png"
```

配置 VLM：

```powershell
$env:OPENAI_BASE_URL = ""https://ai-api.youchu.work"
$env:OPENAI_API_KEY = "lk-fWhd0c638Fm1xuXj1htnUyaIXUCNnY3t_syDPz25RX4"
```

配置 Image-2：

```powershell
$env:TOAPIS_API_KEY = "lk-TVYnvJfvJb5Iy9lCL-IUw2gl2QVSEO8nQVngOLHleQc"

# 可选；不设置时使用脚本内置地址
$env:TOAPIS_BASE_URL = "https://ai-api.youchu.work"
```

## 1. OCR 识别

```powershell
python game-ui-asset-extractor/scripts/ui_text_extractor.py `
  --image "D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\inputs\shezhi_wzry.jpg" `
  --output-json "D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\exp-002\ocr\texts.json" `
  --output-mask "D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\exp-002\ocr\raw-text-mask.png" `
  --output-cleaned "D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\exp-002\ocr\ocr-telea-cleaned.png" `
  --output-debug "D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\exp-001\ocr\ocr-debug.png"
```

主要输出：

- `texts.json`：文字内容、位置等 OCR 数据，下一步交给 VLM
- `raw-text-mask.png`：OCR 原始文字 mask
- `ocr-telea-cleaned.png`：本地 OpenCV 清理结果，仅用于参考
- `ocr-debug.png`：OCR 框和文字标记调试图

当前主链路下一步实际使用的是 `texts.json`，不是 `ocr-telea-cleaned.png`。

## 2. VLM 二次定位和文字归属判断

```powershell
python game-ui-asset-extractor/scripts/ui_vlm_region_mask_poc.py `
  --image "D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\exp-002\ocr\ocr-debug.png" `
  --texts-json "D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\exp-002\ocr\texts.json" `
  --output-dir "D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\exp-002\regions" `
  --model "gpt-5.6-terra" `
  --padding-px 0
```

VLM 会：

- 参考 OCR 候选，但不完全相信 OCR
- 修正文字内容或位置
- 补充 OCR 漏掉的文字
- 排除 OCR 误检
- 判断文字是普通 UI 文字还是美术资产内嵌文字

输出：

- `vlm-region-plan.json`：VLM 最终文字区域和判断
- `region-mask.png`：二值区域 mask
- `region-mask-overlay.png`：在原图上绘制的红色区域标记图

当前 Image-2 基线使用的是 `region-mask-overlay.png`。

## 3. 原图 + VLM 标记图交给 Image-2

```powershell
python game-ui-asset-extractor/scripts/ui_image_clean_repair_poc.py `
  --image "D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\inputs\shezhi_wzry.jpg" `
  --mask-overlay "D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\exp-002\regions\region-mask.png" `
  --provider-size "1024x1536" `
  --model "gpt-image-2-official" `
  --output-dir "D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\exp-002\image2_02"
```

这一步会向 Image-2 提交：

1. 第一张参考图：原始截图 `source.png`
2. 第二张参考图：VLM 红色区域标记图 `region-mask-overlay.png`
3. Prompt：脚本内置的 `SOURCE_PLUS_OVERLAY_PROMPT`

输出：

```text
runs/stage0-text-cleaning/exp-001/image2/
├── clean.<实际扩展名>
└── result.json
```

`clean.<实际扩展名>` 是最终结果；扩展名由下载内容决定，不保证一定是 PNG。

## 使用自定义 Prompt

如果不想使用脚本内置 Prompt，可以准备 UTF-8 文本文件：

```powershell
python game-ui-asset-extractor/scripts/ui_image_clean_repair_poc.py `
  --image "runs/stage0-text-cleaning/shared/source.png" `
  --mask-overlay "runs/stage0-text-cleaning/exp-001/regions/region-mask-overlay.png" `
  --prompt-file "你的提示词.txt" `
  --model "gpt-image-2" `
  --output-dir "runs/stage0-text-cleaning/exp-001/image2-custom"
```

注意：`--mask-overlay` 虽然叫 mask，但实际上是作为 Image-2 的第二张普通参考图上传，并不是 Image Edit API 的 mask 字段。

完整基线命令也记录在 [EXP-001.md](E:/game-ui-skill/docs/experiments/stage0-text-cleaning/EXP-001.md)，流程说明在 [README.md](E:/game-ui-skill/docs/experiments/stage0-text-cleaning/README.md)。


# 透明图的CLI
python game-ui-asset-extractor/scripts/ui_text_alpha_hole.py `
  --image D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\inputs\analysis-image.png `
  --regions-json D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\exp-001\regions\vlm-region-plan.json `
  --output-dir D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\exp-001\alpha-hole `
  --padding 8