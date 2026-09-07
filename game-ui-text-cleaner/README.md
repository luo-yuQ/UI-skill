# game-ui-text-cleaner

Stage0 确定性文字清理核心（Phase 1 选择性迁移自 `stage0/ui-generation-foundation`）。

本模块只做**确定性、纯本地**的文字提取与清理，不依赖任何 VLM API、图像生成 API 或网络访问。

## 职责边界

- **属于本模块**：OCR 文字提取（RapidOCR 本地推理）、raw 文字 mask 生成、Telea 局部 inpaint 清理（确定性 OpenCV 本地算子）、alpha 挖洞图生成。
- **不属于本模块**：VLM 区域审计（`ui_vlm_text_auditor`）、修复规划（`ui_text_repair_planner`）、Image2 / ToAPIs 生成链、Stage2-A/B/C 提取与修复。这些留在 Stage0 历史分支或由各自模块负责。

## 模块结构

```
game-ui-text-cleaner/
├── README.md
├── scripts/
│   ├── ui_text_models.py        # Pydantic v2 契约（Rect / TextItem / TextExtractionResult）
│   ├── ui_text_extractor.py     # OCR 提取 + mask + Telea 清理
│   └── ui_text_alpha_hole.py    # 按区域规划图挖 alpha 洞（独立，无内部依赖）
└── tests/
    ├── test_ui_text_extractor.py
    └── test_ui_text_alpha_hole.py
```

依赖闭包：`ui_text_extractor → ui_text_models`（同目录 sibling import）；`ui_text_alpha_hole →`（无）。

## 生产链契约

### 1. 文字提取与清理 — `ui_text_extractor.py`

**输入**：原始 UI 截图（png/jpg）。

**输出**（自动命名，`--output-dir` 控制目录）：
- `{stem}_texts.json` — `TextExtractionResult` 契约（extra="forbid"，count==len(items) 强校验）
- `{stem}_raw_text_mask.png` — 白字 mask（glyph 估计或 coarse 回退，含膨胀）
- `{stem}_cleaned.png` — 对 mask 区域做 OpenCV Telea inpaint（radius 5，**确定性本地算子，非网络服务**）
- `{stem}_debug.png` — 可选调试可视化

**CLI**：`python scripts/ui_text_extractor.py <input-image> [--output-dir DIR] [单文件输出覆盖项]`；支持目录批量（容错逐个处理）。

**依赖**：opencv-python、numpy、pydantic v2、rapidocr_onnxruntime（懒加载；构造器接受 `ocr_engine` 注入以便测试）。

### 2. Alpha 挖洞 — `ui_text_alpha_hole.py`

**输入**：
- `--image`：源图（png/jpg）
- `--regions-json`：区域规划 JSON（`texts`/`items`/`decisions`/`regions` 之一为列表键；每项含 `bbox_source`，可选 `ownership`/`decision`）
- `--output-dir`：输出目录（必填）
- `--padding`：bbox 外扩像素（默认 8）

**输出**：
- `alpha-hole.png` — REMOVE 区域（`decision=remove_for_background_repair` 或 `ownership=ui_owned`）alpha=0，其余 alpha=255；preserve 项不挖
- `alpha-hole-sanitized.png` — 仅含有效移除区域的紧凑版本
- `alpha-mask.png` — 灰度 mask（白=移除）
- alpha=255 处 RGB 逐字节不变；对非 source 的 `coordinate_space` 直接 fail-fast

**依赖**：仅 opencv-python、numpy。

## 测试

```bash
pytest game-ui-text-cleaner/tests -q
```

测试通过 `ocr_engine` 注入假 OCR 输出，不下载模型、不访问网络。当前 28 项全部通过。

## 来源与迁移说明

- 源：`stage0/ui-generation-foundation` 分支 `game-ui-asset-extractor/scripts|tests/`，算法逐字节原样迁移（git show 提取，行数一致），未做任何算法改写。
- 唯一适配：新模块目录布局（tests 的 `parents[1]/scripts` sys.path 模式在新结构下原样成立）。
- 明确未迁移（按 Phase 1 禁迁清单）：`ui_vlm_region_mask_poc.py`、`ui_image_clean_repair_poc.py`、`ui_vlm_text_auditor.py`、`ui_text_repair_planner.py`、`ui_vlm_planner.py`、`ui_audit_models.py`、`ui_plan_models.py`、`image2_clean_pair_poc.py`、`prepare_image2_working_images.py`、`vlm_client.py`。
