param([switch]$SkipBuild)

# One reproducible command for the committed demonstration/ evidence.
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

function Invoke-Step([scriptblock]$Action, [string]$Name) {
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "Demonstration step failed: $Name" }
}

if (-not $SkipBuild) {
    Invoke-Step { docker compose -f compose.yaml -f compose.lab.yaml --profile test build } 'Docker build'
}
Invoke-Step { docker compose -f compose.yaml -f compose.lab.yaml --profile test run --rm --no-deps verifier } 'unit and integration tests'
Invoke-Step { & (Join-Path $PSScriptRoot 'demo-cves.ps1') -SkipBuild } 'CVE comparison'
Invoke-Step { & (Join-Path $PSScriptRoot 'test-matrix.ps1') -SkipBuild } 'normal webhook matrix'
Invoke-Step { & (Join-Path $PSScriptRoot 'export-results.ps1') } 'matrix result export'
Invoke-Step { & (Join-Path $PSScriptRoot 'export-demonstration.ps1') } 'annotated demonstration export'

Write-Output 'Docker demonstration capture completed. Review demonstration/01-08 JSON files.'
