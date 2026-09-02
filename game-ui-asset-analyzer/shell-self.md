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
  --image "D:\Third_Test_1\UI-skill\runs\stage0-text-cleaning-20260831-114721\size-ab\square_03\clean.png" `
  --output-dir "runs\20260902_direct-asset-discovery-002" `
  --model "glm-5.3-flash" `
  --runs 1