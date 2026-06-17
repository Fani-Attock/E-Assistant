param(
    [string]$OutputDir = "delivery",
    [string]$Version = "",
    [switch]$SkipArtifacts
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$outputRoot = Join-Path $root $OutputDir
$stagingRoot = Join-Path $outputRoot "_staging"
$versionFile = Join-Path $root "CLIENT_RELEASE_VERSION.txt"
if ([string]::IsNullOrWhiteSpace($Version)) {
    if (Test-Path $versionFile) {
        $Version = (Get-Content -LiteralPath $versionFile -Raw).Trim()
    }
}
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = "0.1.0"
}
$safeVersion = ($Version -replace "[^0-9A-Za-z._-]", "-").Trim("-")
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$releaseFolderName = "product-search-client-v$safeVersion"
$zipPath = Join-Path $outputRoot "$releaseFolderName-$timestamp.zip"

$excludeDirNames = @(
    ".git",
    ".pytest_cache",
    "__pycache__",
    ".venv",
    "data",
    "delivery",
    "node_modules",
    "dist",
    "checkpoints",
    "scraper_debug",
    "tmp"
)

if ($SkipArtifacts) {
    $excludeDirNames += "artifacts"
}

$excludeFileNames = @(
    ".env"
)

function Copy-Tree {
    param(
        [string]$Source,
        [string]$Destination
    )

    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        if ($excludeFileNames -contains $_.Name) {
            return
        }
        if ($_.PSIsContainer) {
            if ($excludeDirNames -contains $_.Name) {
                return
            }
            $targetDir = Join-Path $Destination $_.Name
            New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
            Copy-Tree -Source $_.FullName -Destination $targetDir
            return
        }
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Destination $_.Name) -Force
    }
}

if (Test-Path $outputRoot) {
    New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
} else {
    New-Item -ItemType Directory -Path $outputRoot | Out-Null
}

if (Test-Path $stagingRoot) {
    Remove-Item -LiteralPath $stagingRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $stagingRoot | Out-Null
$stagingReleaseRoot = Join-Path $stagingRoot $releaseFolderName
New-Item -ItemType Directory -Path $stagingReleaseRoot | Out-Null

Copy-Tree -Source $root -Destination $stagingReleaseRoot

if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

Compress-Archive -Path $stagingReleaseRoot -DestinationPath $zipPath -Force
Remove-Item -LiteralPath $stagingRoot -Recurse -Force

Write-Host "Created client delivery package:"
Write-Host $zipPath
