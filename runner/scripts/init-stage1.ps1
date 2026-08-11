param(
    [Parameter(Mandatory = $true)]
    [string]$Scenario,

    [Parameter(Mandatory = $true)]
    [string]$UserRequirement
)

$ErrorActionPreference = "Stop"

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
    user_requirement = $UserRequirement
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