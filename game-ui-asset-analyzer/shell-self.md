# 配置 VLM的环境变量
$env:STAGE2A_VLM_BASE_URL = "https://ai-api.youchu.work"
$env:STAGE2A_VLM_API_KEY = "lk-gn9sjZGxknj_0Cy3j1xI-AfmKKW__DZ_EIhER_0UNjY"

# 此时可以不设置 STAGE2A_VLM_MODEL

python game-ui-asset-analyzer/scripts/run_recursive_runtime.py `
  --run-dir "<RUN_DIR>" `
  --root-node-crop "<IMAGE_PATH>" `
  --adapter production `
  --model "glm-5.3-flash"

# 对于 Stage0 输出的 clean UI，是否可以不经过递归组件树，直接让 VLM 一次性发现主要 terminal assets，并给出稳定 bbox。
$env:STAGE2A_VLM_BASE_URL = "..."
$env:STAGE2A_VLM_API_KEY = "..."

python game-ui-asset-analyzer/experiments/direct_asset_discovery_probe.py `
  --image "D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\size-ab\square_03\clean.png" `
  --output-dir "runs\20260902_direct-asset-discovery-001" `
  --model "glm-5.3-flash" `
  --runs 3

# 关闭thinking
python game-ui-asset-analyzer/experiments/direct_asset_discovery_probe.py `
  --image "D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\exp-001\alpha-sanitized-image2-01\clean.png" `
  --output-dir "runs\20260902_direct-asset-discovery-007-production-client" `
  --model "glm-5.3-flash" `
  --runs 1

Stage 2-A2
  python D:\Third_Test_1\UI-skill\game-ui-asset-analyzer\experiments\asset_admission_probe.py `
    --image "D:\Third_Test_1\UI-skill\runs\20260902_direct-asset-discovery-005-production-client\analysis-image.png" `
    --candidates-json "D:\Third_Test_1\UI-skill\runs\20260902_direct-asset-discovery-005-production-client\direct-assets.json" `
    --output-dir "runs\20260902_asset-admission-001-spin-wheel-clean-001" `
    --model glm-5.3-flash `
    --runs 1
# 九月三号的最新的clean图
D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\exp-001\alpha-sanitized-image2-01\clean.png

# 双图测试 原图+clean
  python game-ui-asset-analyzer/experiments/direct_asset_discovery_dual_image_probe.py `
    --original-image "D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\inputs\analysis-image.png" `
    --clean-image "D:\Third_Test_1\UI-skill\runs\20260902_direct-asset-discovery-007-production-client\source.png" `
    --output-dir "runs\20260903_direct-asset-discovery-dual-image-002" `
    --model glm-5.3-flash
# 压缩原图
  python -c "from PIL import Image; img=Image.open(r'D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\inputs\analysis-image.png').convert('RGB'); img.resize((1024,1536), Image.Resampling.LANCZOS).save(r'D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\inputs\sheiji.png', quality=95)"

# 前端的人工手动模式
  python game-ui-asset-analyzer/experiments/direct_asset_review_ui.py `
    --image "runs\20260902_direct-asset-discovery-007-production-client\overlay-source.png" `
    --assets-json "runs\20260902_direct-asset-discovery-007-production-client\direct-assets.json" `
    --overrides-json "runs\20260902_direct-asset-discovery-007-production-client\review-overrides.json"