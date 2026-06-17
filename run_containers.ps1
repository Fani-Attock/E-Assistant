param(
  [switch]$Build
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw "Docker is not installed or not in PATH. Install Docker Desktop first."
}

$args = @("compose", "up", "-d")
if ($Build) {
  $args = @("compose", "up", "--build", "-d")
}

Write-Host "[container] Starting E-Assistant stack..." -ForegroundColor Cyan
docker @args

if ($LASTEXITCODE -ne 0) {
  throw "docker compose up failed."
}

Write-Host ""
Write-Host "Open these URLs:" -ForegroundColor Green
Write-Host "Web UI:   http://localhost:3000"
Write-Host "API:      http://localhost:8000"
Write-Host "API Docs: http://localhost:8000/docs"
