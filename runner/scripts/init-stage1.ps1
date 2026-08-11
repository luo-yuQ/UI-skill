param(
    [Parameter(Mandatory = $true)]
    [string]$Scenario,

    [Parameter(Mandatory = $true)]
    [string]$BusinessRequirement
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($BusinessRequirement)) {
    throw "BusinessRequirement must contain the user's business request."
}

$runnerControlPatterns = @(
    '(?i)/stage1\b',
    '(?i)continue\s+runs[/\\]',
    '继续\s*runs[/\\]',
    '只初始化(?:\s*run)?',
    '只执行\s*(?:A1|B1|B2|Composer)',
    '运行到\s*(?:A1|B1|B2|Composer).{0,8}停止',
    '不要执行\s*(?:A1|B1|B2|Composer)',
    '执行\s*Composer'
)

foreach ($pattern in $runnerControlPatterns) {
    if ($BusinessRequirement -match $pattern) {
        throw "BusinessRequirement contains Runner execution control. Separate workflow control before initializing the run."
    }
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$runsRoot = Join-Path $repoRoot "runs"

if (-not (Test-Path $runsRoot)) {
    New-Item -ItemType Directory -Path $runsRoot | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

$existing = Get-ChildItem $runsRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "*_${Scenario}_*" }

$index = "{0:D3}" -f (($existing | Measure-Object).Count + 1)

$runId = "${timestamp}_${Scenario}_${index}"
$runRoot = Join-Path $runsRoot $runId

$dirs = @(
    "00-input",
    "00-input\layout-reference",
    "00-input\style-reference",
    "10-layout-reference",
    "20-style-reference",
    "20-style-reference\asset-analysis",
    "30-composer"
)

foreach ($dir in $dirs) {
    New-Item -ItemType Directory -Force -Path (Join-Path $runRoot $dir) | Out-Null
}

$request = @{
    user_requirement = $BusinessRequirement
    layout_references = @()
    style_references = @()
}

$request |
    ConvertTo-Json -Depth 10 |
    Set-Content -Path (Join-Path $runRoot "00-input\request.json") -Encoding UTF8

$manifest = @{
    run_id = $runId
    status = "running"
    stages = @{
        input = @{
            status = "completed"
        }
        a1 = @{
            status = "pending"
        }
        b1 = @{
            status = "pending"
        }
        b2 = @{
            status = "pending"
        }
        composer_input = @{
            status = "pending"
        }
        composer = @{
            status = "pending"
        }
    }
}

$manifest |
    ConvertTo-Json -Depth 10 |
    Set-Content -Path (Join-Path $runRoot "run-manifest.json") -Encoding UTF8

Write-Output $runRoot
