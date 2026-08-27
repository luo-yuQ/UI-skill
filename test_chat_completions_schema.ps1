[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$ImagePath = "E:\game-ui-skill\runs\20260824_stage0_text_extract_003\inputs\beibao_wzry.jpg",

    [Parameter(Mandatory = $false)]
    [string]$Model = "gpt-5.6-terra"
)

$ErrorActionPreference = "Stop"

function Get-ErrorResponseBody {
    param(
        [Parameter(Mandatory = $true)]
        [System.Management.Automation.ErrorRecord]$ErrorRecord
    )

    $response = $ErrorRecord.Exception.Response
    if ($null -eq $response) {
        return $null
    }

    if ($response.PSObject.Properties.Name -contains "Content") {
        $content = $response.Content
        if ($null -ne $content) {
            try {
                return $content.ReadAsStringAsync().GetAwaiter().GetResult()
            }
            catch {
                return [string]$content
            }
        }
    }

    if ($response.PSObject.Methods.Name -contains "GetResponseStream") {
        try {
            $stream = $response.GetResponseStream()
            if ($null -ne $stream) {
                $reader = [System.IO.StreamReader]::new($stream)
                try {
                    return $reader.ReadToEnd()
                }
                finally {
                    $reader.Dispose()
                }
            }
        }
        catch {
            return $null
        }
    }

    return $null
}

function Get-HttpStatus {
    param(
        [Parameter(Mandatory = $true)]
        [System.Management.Automation.ErrorRecord]$ErrorRecord
    )

    $response = $ErrorRecord.Exception.Response
    if ($null -eq $response) {
        return "Unavailable"
    }

    try {
        $statusCode = [int]$response.StatusCode
        $reasonPhrase = $null
        if ($response.PSObject.Properties.Name -contains "ReasonPhrase") {
            $reasonPhrase = $response.ReasonPhrase
        }
        elseif ($response.PSObject.Properties.Name -contains "StatusDescription") {
            $reasonPhrase = $response.StatusDescription
        }

        if ([string]::IsNullOrWhiteSpace([string]$reasonPhrase)) {
            return [string]$statusCode
        }
        return "$statusCode $reasonPhrase"
    }
    catch {
        return [string]$response.StatusCode
    }
}

$baseUrl = $env:OPENAI_BASE_URL
$apiKey = $env:OPENAI_API_KEY

if ([string]::IsNullOrWhiteSpace($baseUrl)) {
    throw "Environment variable OPENAI_BASE_URL is not set."
}
if ([string]::IsNullOrWhiteSpace($apiKey)) {
    throw "Environment variable OPENAI_API_KEY is not set."
}
if (-not (Test-Path -LiteralPath $ImagePath -PathType Leaf)) {
    throw "Image file does not exist: $ImagePath"
}

$endpoint = "$($env:OPENAI_BASE_URL.TrimEnd('/'))/v1/chat/completions"

$extension = [System.IO.Path]::GetExtension($ImagePath).ToLowerInvariant()
$mimeType = switch ($extension) {
    ".jpg"  { "image/jpeg" }
    ".jpeg" { "image/jpeg" }
    ".png"  { "image/png" }
    ".webp" { "image/webp" }
    ".gif"  { "image/gif" }
    default  { throw "Unsupported image extension '$extension'. Use JPEG, PNG, WebP, or GIF." }
}

$imageBytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $ImagePath).Path)
$imageBase64 = [System.Convert]::ToBase64String($imageBytes)
$imageDataUrl = "data:$mimeType;base64,$imageBase64"

$systemPrompt = @"
Analyze all visible text in the supplied game UI screenshot.

Return the final canonical list of visible text regions.

Follow the provided JSON Schema exactly.

The only allowed top-level key is "texts".

Use image pixel coordinates for bbox_analysis.
"@

$schema = @{
    type                 = "object"
    additionalProperties = $false
    required             = @("texts")
    properties           = @{
        texts = @{
            type  = "array"
            items = @{
                type                 = "object"
                additionalProperties = $false
                required             = @(
                    "text",
                    "bbox_analysis",
                    "ownership",
                    "semantic_role",
                    "confidence"
                )
                properties           = @{
                    text          = @{
                        type = "string"
                    }
                    bbox_analysis = @{
                        type                 = "object"
                        additionalProperties = $false
                        required             = @("x", "y", "width", "height")
                        properties           = @{
                            x      = @{ type = "number" }
                            y      = @{ type = "number" }
                            width  = @{ type = "number" }
                            height = @{ type = "number" }
                        }
                    }
                    ownership     = @{
                        type = "string"
                        enum = @("ui_owned", "asset_owned")
                    }
                    semantic_role = @{
                        type = "string"
                        enum = @(
                            "navigation_label",
                            "button_label",
                            "runtime_value",
                            "body_text",
                            "ordinary_title",
                            "status_text",
                            "embedded_in_artwork",
                            "embedded_logo",
                            "decorative_art_text"
                        )
                    }
                    confidence    = @{
                        type = "number"
                    }
                }
            }
        }
    }
}

$requestBody = @{
    model           = $Model
    messages        = @(
        @{
            role    = "system"
            content = $systemPrompt
        },
        @{
            role    = "user"
            content = @(
                @{
                    type = "text"
                    text = "Analyze the supplied screenshot."
                },
                @{
                    type      = "image_url"
                    image_url = @{
                        url = $imageDataUrl
                    }
                }
            )
        }
    )
    response_format = @{
        type        = "json_schema"
        json_schema = @{
            name   = "canonical_text_response"
            schema = $schema
        }
    }
}

$jsonBody = $requestBody | ConvertTo-Json -Depth 20 -Compress
$headers = @{
    Authorization = "Bearer $apiKey"
}

Write-Host "===== ENDPOINT ====="
Write-Host $endpoint
Write-Host ""

try {
    $invokeParams = @{
        Uri         = $endpoint
        Method      = "Post"
        Headers     = $headers
        ContentType = "application/json; charset=utf-8"
        Body        = [System.Text.Encoding]::UTF8.GetBytes($jsonBody)
    }

    $response = Invoke-WebRequest @invokeParams
    $responseText = $response.Content

    Write-Host "===== HTTP RESPONSE ====="
    Write-Host "Status: $([int]$response.StatusCode) $($response.StatusDescription)"
    Write-Host "Content-Type: $($response.Headers['Content-Type'])"
    Write-Host "Content-Length: $($response.RawContentLength)"
    Write-Host ""

    $responseObject = $responseText | ConvertFrom-Json
    $rawContent = $responseObject.choices[0].message.content

    Write-Host "===== RAW CONTENT ====="
    Write-Host $rawContent
    Write-Host ""

    Write-Host "===== PARSED JSON ====="
    $parsedContent = $null
    try {
        $parsedContent = $rawContent | ConvertFrom-Json
        $parsedContent | ConvertTo-Json -Depth 20
    }
    catch {
        Write-Host "Failed to parse choices[0].message.content as JSON."
        Write-Host "Exception message: $($_.Exception.Message)"
    }
    Write-Host ""

    Write-Host "===== SCHEMA CHECK ====="
    if ($null -eq $parsedContent) {
        Write-Host "Top-level 'texts' exists: NOT CHECKED (content is not valid JSON)"
        Write-Host "Unexpected top-level 'text_regions' exists: NOT CHECKED"
        Write-Host "Unexpected top-level 'regions' exists: NOT CHECKED"
        Write-Host "Unexpected top-level 'items' exists: NOT CHECKED"
    }
    else {
        $topLevelProperties = @($parsedContent.PSObject.Properties.Name)
        Write-Host "Top-level 'texts' exists: $($topLevelProperties -contains 'texts')"
        Write-Host "Unexpected top-level 'text_regions' exists: $($topLevelProperties -contains 'text_regions')"
        Write-Host "Unexpected top-level 'regions' exists: $($topLevelProperties -contains 'regions')"
        Write-Host "Unexpected top-level 'items' exists: $($topLevelProperties -contains 'items')"
    }
}
catch {
    Write-Host "===== HTTP RESPONSE ====="
    Write-Host "HTTP status: $(Get-HttpStatus -ErrorRecord $_)"
    Write-Host "Response body:"
    $errorBody = Get-ErrorResponseBody -ErrorRecord $_
    if ([string]::IsNullOrEmpty($errorBody)) {
        Write-Host "<no response body available>"
    }
    else {
        Write-Host $errorBody
    }
    Write-Host "Exception message: $($_.Exception.Message)"
    exit 1
}
