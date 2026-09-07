# game-ui-text-cleaner

Stage0 确定性文字清理核心（Phase 1 选择性迁移自 `stage0/ui-generation-foundation`）+ VLM 文字区域二次定位（Phase 2 选择性迁移）+ Image-2 clean repair（Phase 3 选择性迁移）。

## 职责边界

- **属于本模块**：OCR 文字提取（RapidOCR 本地推理）、raw 文字 mask 生成、Telea 局部 inpaint 清理（确定性 OpenCV 本地算子）、VLM 文字区域语义二次定位（region plan + mask + overlay）、alpha 挖洞图生成、Image-2 clean repair（toapis gpt-image-2 生成链客户端）。
- **不属于本模块**：VLM 区域审计（`ui_vlm_text_auditor`，ARCHIVE_ONLY）、修复规划（`ui_text_repair_planner`）、Stage2-A/B/C 提取与修复。这些留在 Stage0 历史分支或由各自模块负责。

## 模块结构

```
game-ui-text-cleaner/
├── README.md
├── scripts/
│   ├── ui_text_models.py          # Pydantic v2 契约（Rect / TextItem / TextExtractionResult）
│   ├── ui_text_extractor.py       # OCR 提取 + mask + Telea 清理
│   ├── ui_vlm_region_mask.py      # VLM 文字区域二次定位（转正自 ui_vlm_region_mask_poc.py）
│   ├── ui_text_alpha_hole.py      # 按区域规划图挖 alpha 洞（独立，无内部依赖）
│   └── ui_image_clean_repair.py   # Image-2 clean repair 生成链客户端（Phase 3）
└── tests/
    ├── test_ui_text_extractor.py
    ├── test_ui_vlm_region_mask.py
    ├── test_ui_text_alpha_hole.py
    └── test_ui_image_clean_repair.py
```

依赖闭包：`ui_text_extractor → ui_text_models`；`ui_vlm_region_mask → ui_text_models + game-ui-asset-analyzer/scripts/{prepare_analysis_input, vlm_client}`（复用 main 现存 VLM 基础设施，`vlm_client.py` 零修改）；`ui_text_alpha_hole →`（无）；`ui_image_clean_repair → game-ui-auto-composer-skill/scripts/toapis_preview_adapter + game-ui-image-provider-adapter/scripts/generate_preview`（两个 adapter **零修改**，经 importlib 按路径加载，同 Stage0 PoC 模式）。

## 生产链契约

```
Raw UI
↓
ui_text_extractor（OCR）
↓
texts.json
↓
ui_vlm_region_mask（VLM 区域二次定位）
↓
vlm-region-plan.json
region-mask.png
region-mask-overlay.png
```

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

### 3. VLM 文字区域二次定位 — `ui_vlm_region_mask.py`（Phase 2）

以 Stage A OCR 结果为**非权威提示**，用一次 canonical VLM 调用产出最终文字区域列表；VLM 矩形是 mask 唯一权威，OCR box 不被直接复用，且全程不调用 `cv2.inpaint`。

**输入**：
- `--image`：源截图（png/jpg/jpeg/webp）
- `--texts-json`：Phase 1 产出的 `texts.json`（list 或含 `items` 的 envelope；`image_width/height` 需与源图一致，fail-fast）
- `--output-dir`：输出目录（必填）
- `--model`：VLM 模型名（默认 `gpt-5.6-terra`）
- `--padding-px`：UI-owned 框外扩像素（默认 0）
- 环境：`OPENAI_BASE_URL` / `OPENAI_API_KEY`（备选 `STAGE2A_VLM_*`），仅真实运行需要；单测全部走 fake client，零网络

**输出**：
- `vlm-region-plan.json` — 每项含 `text / bbox_analysis / bbox_source / ownership / semantic_role / decision / confidence`，以及 `schema_version`、`source_image_size`、`analysis_image_size`、`padding_px`
- `region-mask.png` — 灰度 mask，仅 `ui_owned` 框置 255（含 padding），`asset_owned` 不进 mask
- `region-mask-overlay.png` — 源图上 mask 区域红色半透明叠加（55/45 addWeighted）

**职责分工**：
- **VLM**：文字区域语义二次定位（确认/纠正/补充/剔除 OCR 候选，判定 ui_owned / asset_owned 与 semantic_role）
- **工程代码**：分析图生成（复用 `prepare_analysis_input`，max_width=1024 force_width）、source↔analysis 确定性坐标映射（floor/ceil 边界 + clamp）、mask rasterization、overlay rendering、JSON 输出、schema 严格校验（严格重试 1 次）

**坐标契约（冻结，Step 5）**：VLM bbox 属于**声明的 analysis-image 坐标空间**（1024 宽分析图像素），不是 source 像素；`bbox_analysis → bbox_source` 是确定性工程变换（floor/ceil + clamp），不推断 provider 内部 resize 行为。source→analysis 方向的 OCR hint 映射同样确定性。验收锚点：128×64 源图 → 1024×512 分析图，VLM 框 `(320,80,160,40)` 必须映射为 source `(40,10,20,5)`。

**VLM Client 兼容性（Step 1/2 审计结论）**：本文件只 import main 已有的 `VLMClientConfig`、`VLMResponseParseError`、`encode_image_as_data_url`（来自 `game-ui-asset-analyzer/scripts/vlm_client.py`）与 `prepare_analysis_input`；`ChatCompletionsSchemaVLMClient` 为本文件自带实现（Chat Completions + API 级 JSON Schema，诊断输出 + api_key 脱敏），**不需要** `ChatCompletionsVLMClient`，`vlm_client.py` 零修改。

### 4. Image-2 Clean Repair — `ui_image_clean_repair.py`（Phase 3）

调用 toapis gpt-image-2 生成链，按修复引导图清除文字并回填背景。参考图合约：**IMAGE 1 = 权威源 UI 截图，IMAGE 2 = 对齐的修复引导 overlay（仅用于定位修复区域）**；provider 只允许重绘 overlay 标记区域，其余像素保持不变。

**输入模式**：
- `source_plus_overlay`（**生产路径**）：源图 + overlay + 内置冻结 prompt（`SOURCE_PLUS_OVERLAY_PROMPT`，原样迁移）
- `alpha_hole_only`（实验路径）：透明洞源图 + 内置冻结 prompt（`ALPHA_HOLE_ONLY_PROMPT`）+ alpha probe 诊断
- `source_only`：**不支持 / 非生产路径**，fail-fast（不发明 prompt），除非显式 `--prompt-file`

**协议路由（本模块自含，不依赖 adapter 扩展）**：create 响应含 `data[].b64_json/url` → sync image 协议（`SYNC_RESULT_PROTOCOL = "openai_images_sync"`）；含 `task_id/id` → async task 协议（轮询 `poll_task_status`/`fetch_task_result`）。

**上传合约**：multipart `file` 字段，显式 MIME（png→`image/png`、jpg/jpeg→`image/jpeg`、webp→`image/webp`），非 octet-stream；上传走 `{base_url}/api/upload`，响应 `url` 必须为合法 HTTP URL。

**尺寸契约**：请求 `size` 取自源图实际尺寸；输出记录 `source_size` / `provider_size` / `output_size` / `output_matches_source_size`（provider 输出尺寸可能与源不一致，必须显式记录，不做静默 resize）。

**输出**（`--output-dir`）：
- `clean.{png|jpg|jpeg|webp}` — 清理后图像（扩展名跟随源图）
- `result.json` — `image2-clean-repair-v0.1`：`schema_version / status / mode / provider / model / source_image / mask_overlay / source_size / provider_size / output_size / output_matches_source_size / output_image / task_id / image_url / prompt / result_protocol / create_debug / poll_debug / alpha_probe`

**CLI**：`python scripts/ui_image_clean_repair.py --source-image IMG [--mask-overlay OVL] --output-dir DIR --mode {source_plus_overlay|alpha_hole_only|source_only} --provider-base-url URL --api-key KEY [--model gpt-image-2] [--timeout 120] [--prompt-file F]`

**Adapter 兼容性（Step 1 审计结论）**：
- 使用的 adapter 符号：`toapis_preview_adapter.{is_http_url, provider_url, upload_image?, poll_task_status, fetch_task_result, download_image, write_result_json, requests}` + `generate_preview.{find_curl, sanitized_text, submit_task_id, image_extension, submit_generation}`
- **`toapis_preview_adapter.py` 零修改**；**`generate_preview.py` 零修改**
- 关键适配点：main adapter **没有** `detect_result_protocol`/`extract_sync_image_items`/`SYNC_RESULT_PROTOCOL`/`ASYNC_TASK_PROTOCOL` —— 协议检测自含在本模块（Stage0 PoC 同款实现），不向 adapter 添加任何符号
- 关键适配点：main `generate_preview.submit_generation` 签名是 `(payload, *, base_url, api_key, timeout, curl_path)` —— **无 `debug_sink` 参数**（Stage0 PoC 曾传 `debug_sink`）。迁移版 `submit_generation_for_clean_repair` 去掉 `debug_sink` 透传（该参数仅诊断用途，不影响行为），改为在调用点 `find_curl()` 后直接调用
- 保留 Stage0 的 `provider_helpers.sanitized_text` 2000 字符 error-preview 包装（模块内 monkey patch，仅作用于本模块加载的 adapter 副本，不影响 main 生产链对该 adapter 的引用）
- 已知技术债：Stage0 PoC 的 upload/submit monkey patch 在迁移版收敛为本模块自有 `upload_image_for_clean_repair` / `submit_generation_for_clean_repair`，不再 patch adapter 模块属性（行为等价，调用点改为显式引用本模块函数）

## 测试

```bash
pytest game-ui-text-cleaner/tests -q
```

测试通过 `ocr_engine` 注入假 OCR 输出、fake VLM client / fake HTTP session 注入假响应，**零真实 HTTP、零真实 VLM API、零 API key 依赖**。当前 Phase 1 28 项 + Phase 2 region-mask 14 项 + Phase 3 image-clean-repair 10 项全部通过（合计 52）。

## 来源与迁移说明

- 源：`stage0/ui-generation-foundation` 分支 `game-ui-asset-extractor/scripts|tests/`，算法逐字节原样迁移（git show 提取），未做任何算法改写。
- Phase 1 唯一适配：新模块目录布局（tests 的 `parents[1]/scripts` sys.path 模式在新结构下原样成立）。
- Phase 2 适配：`ui_vlm_region_mask_poc.py` → 转正命名 `ui_vlm_region_mask.py`（类名 `UIVLMRegionMaskPoC` → `UIVLMRegionMask`，模块 docstring 更新，仅名称与说明，VLM 定位算法与 bbox/mask 逻辑零改动）；测试同步转正并仅改 import 与类名。
- Phase 3 适配：`ui_image_clean_repair_poc.py` → 转正命名 `ui_image_clean_repair.py`（docstring 更新；算法、prompt 冻结文本、上传 MIME 合约、协议路由、输出契约逐项原样保留；`debug_sink` 透传按 main adapter 签名去除；monkey patch 收敛为模块内显式调用）；测试同步转正并仅改 import 与类名引用。两个共享 adapter（`toapis_preview_adapter.py` / `generate_preview.py`）**零修改**。
- 明确未迁移（按禁迁清单）：`ui_vlm_text_auditor.py`（ARCHIVE_ONLY）、`ui_audit_models.py`、`ui_text_repair_planner.py`、`ui_plan_models.py`、`ui_vlm_planner.py`、`ui_image_clean_repair_poc.py`、`image2_clean_pair_poc.py`、`prepare_image2_working_images.py`、Stage0 `vlm_client.py`（main 版本继续作为唯一 VLM 基础设施）。
