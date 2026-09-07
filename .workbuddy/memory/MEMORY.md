# 项目长期记忆（UI-skill）

## 路径约定（重要）
- Stage2-C 实验输出基础目录：`D:/Third_Test_1/UI-skill/runs/<run-name>/stage2c/repair/<asset_id>/`
- 每轮 PoC 在其下新建 `<poc-name>/` 子目录（如 `smooth-plate-repair-poc-001/`），内含输出图 + result.json + run_poc.py
- authoritative input：`stage2c/repair/<asset_id>/repair-input.json`
- asset_027 当前 run：`runs/20260902_direct-asset-discovery-007-production-client/`
  - target: `runs/20260904_sam_box_only_batch_001/asset_027/filtered-rgba.png`
- 实验总日志：`D:/Third_Test_1/UI-skill/EXPERIMENT_LOG.md`（追加式）

## 环境
- managed python 无 PIL/scipy；实际使用 `C:/Users/Administrator/anaconda3/python.exe`（含 numpy/PIL/scipy）

## 项目约定
- mask 外必须逐字节不变（final[~mask]=original[~mask]），硬验证写入 result.json
- Stage2-C 禁用 cv2.inpaint TELEA/NS（会扩散暗斑）、Image2、VLM、SAM rerun
- 每轮实验只做单 case，不接生产链、不批量、不改 C1.5，除非用户明说

## Stage2-C 进展
- smooth-plate-repair-poc-001（2026-09-07）：asset_027 surface-fit 修复完成，linear 优于 quadratic（quad 过拟合出现浅粉色斑）
- **Traditional CV Repair v0.1 已冻结（2026-09-07，production）**：
  - 正式模块：`game-ui-asset-repairer/repair_asset_cv.py` + `schemas/cv-repair-result.schema.json`（cv-repair-result-v0.1）+ `tests/test_repair_asset_cv.py`（17 tests）
  - 冻结算法：linear surface fit（a+bx+cy, IRLS-Huber）+ 5% 比例 dilation（radius=clamp(round(min(occ_w,occ_h)*0.05),1,6)，每 occluder 单独算）+ ring consensus alpha
  - TELEA/NS 明确禁入 production；quadratic 不进 production
  - CLI：--repair-input / --output-dir / --dilation-ratio / --dilation-px（override, dilation_mode=fixed_override）/ --disable-dilation
  - 冻结回归输出在各 asset 下 `cv-repair-v0.1-freeze/`；asset_027 冻结输出与 PoC r2 逐像素一致
  - 注意：dilation-policy-poc 曾推荐方案 C（clamp 2..3），冻结以 5% 比例规则为准，差异已记录在 EXPERIMENT_LOG

## Image2 中转站输入合约（2026-09-07 验证）
- images 字段禁止 data URI/base64（400 "base64 image is not allowed"），必须传公网 HTTPS URL
- 上传合约：POST `https://ai-api.youchu.work/api/upload`，multipart `files={"file": (name, file, <mime>)}`，Bearer 认证，响应 `{url: https://transfer-hk.youchu.xyz/d/xxx.png}`（HTTP 201）
- **必须显式传 part MIME**，否则 requests 默认 application/octet-stream 会被 400 拒绝
- `/v1/uploads/images` endpoint 已废（502）；参考 image2_cli.py 与 toapis_preview_adapter.py
