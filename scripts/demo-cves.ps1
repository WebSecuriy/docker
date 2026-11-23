param([switch]$SkipBuild)

# Runs only the repository's acknowledgement-gated synthetic lab. It never
# targets a real MLflow/Plane service, cloud metadata address, or Kali VM.
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

function Invoke-Compose([string[]]$ComposeArgs) {
    & docker @ComposeArgs
    if ($LASTEXITCODE -ne 0) { throw "Compose command failed: $ComposeArgs" }
}

$environmentNames = @(
    'WEBHOOK_BIND_IP', 'POLICY', 'ENABLED_MODES', 'RETURN_BODY', 'STORE_BODY', 'READ_BODY',
    'ALLOW_HTTP', 'REPORT_API_URL', 'CVE_DEMO_MODE', 'CVE_DEMO_ACK',
    'MLFLOW_VERSION_PROFILE', 'MLFLOW_CVE_AUTH_REQUIRED', 'PLANE_VERSION_PROFILE',
    'CVE_DEMO_PLANE_ORIGIN', 'PLANE_LOG_FILTER'
)
$saved = @{}
foreach ($name in $environmentNames) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name)
}

try {
    # Keep every deliberately vulnerable profile private to the Docker lab.
    $env:WEBHOOK_BIND_IP = '127.0.0.1'
    $env:ENABLED_MODES = 'mlflow,plane'
    $env:ALLOW_HTTP = 'true'
    $env:REPORT_API_URL = 'http://internal:8000/reports/alpha'
    $env:CVE_DEMO_MODE = 'all'
    $env:CVE_DEMO_ACK = 'local-lab-only'
    $env:CVE_DEMO_PLANE_ORIGIN = 'https://receiver:8443/redirect?status=302'

    if (-not $SkipBuild) {
        Invoke-Compose @('compose', '-f', 'compose.yaml', '-f', 'compose.lab.yaml', '--profile', 'test', 'build')
    }
    # The numeric target lets the blocked-network case prove an egress failure
    # instead of relying on an unresolvable Docker DNS name.
    & (Join-Path $PSScriptRoot 'prepare-lab.ps1') -UseInternalIp
    if ($LASTEXITCODE -ne 0) { throw 'prepare-lab.ps1 failed' }

    $cases = @(
        @{ Label='baseline-vulnerable'; Policy='baseline'; Network='open'; Mflow='v3.13.0-vulnerable'; Auth='false'; Plane='v1.3.1-vulnerable'; Filter='none'; Return='full'; Store='full'; Read='full'; MReach='true'; PReach='true'; MExpose='true'; PExpose='true'; MAuth='none' },
        @{ Label='reject-open'; Policy='reject'; Network='open'; Mflow='v3.13.0-vulnerable'; Auth='false'; Plane='v1.3.1-vulnerable'; Filter='none'; Return='full'; Store='full'; Read='full'; MReach='false'; PReach='false'; MExpose='false'; PExpose='false'; MAuth='none' },
        @{ Label='strict-open'; Policy='strict'; Network='open'; Mflow='v3.13.0-vulnerable'; Auth='false'; Plane='v1.3.1-vulnerable'; Filter='none'; Return='full'; Store='full'; Read='full'; MReach='false'; PReach='false'; MExpose='false'; PExpose='false'; MAuth='none' },
        @{ Label='baseline-network-blocked'; Policy='baseline'; Network='blocked'; Mflow='v3.13.0-vulnerable'; Auth='false'; Plane='v1.3.1-vulnerable'; Filter='none'; Return='full'; Store='full'; Read='full'; MReach='false'; PReach='false'; MExpose='false'; PExpose='false'; MAuth='none' },
        @{ Label='fixed-url-auth-mask'; Policy='baseline'; Network='open'; Mflow='v3.15.0-fixed'; Auth='true'; Plane='v1.4.0-fixed'; Filter='metadata-only'; Return='mask'; Store='mask'; Read='mask'; MReach='false'; PReach='false'; MExpose='false'; PExpose='false'; MAuth='required' },
        @{ Label='plane-log-filter'; Policy='baseline'; Network='open'; Mflow='v3.15.0-fixed'; Auth='true'; Plane='v1.3.1-vulnerable'; Filter='redact-sensitive'; Return='mask'; Store='full'; Read='full'; MReach='false'; PReach='true'; MExpose='false'; PExpose='false'; MAuth='required' }
    )

    foreach ($case in $cases) {
        $env:POLICY = $case.Policy
        $env:RETURN_BODY = $case.Return
        $env:STORE_BODY = $case.Store
        $env:READ_BODY = $case.Read
        $env:MLFLOW_VERSION_PROFILE = $case.Mflow
        $env:MLFLOW_CVE_AUTH_REQUIRED = $case.Auth
        $env:PLANE_VERSION_PROFILE = $case.Plane
        $env:PLANE_LOG_FILTER = $case.Filter
        $files = @('compose', '-f', 'compose.yaml', '-f', 'compose.lab.yaml')
        if ($case.Network -eq 'blocked') { $files += @('-f', 'compose.block.yaml') }
        Invoke-Compose ($files + @('up', '-d', '--no-build', '--wait', 'internal', 'receiver', 'webhook'))
        Invoke-Compose ($files + @('--profile', 'test', 'run', '--rm', '--no-deps', 'verifier',
            'python', 'scripts/cve_demo.py', '--scenario', $case.Label, '--policy', $case.Policy,
            '--network', $case.Network, '--mlflow-auth', $case.MAuth,
            '--expect-mlflow-reach', $case.MReach, '--expect-plane-reach', $case.PReach,
            '--expect-mlflow-exposure', $case.MExpose, '--expect-plane-exposure', $case.PExpose,
            '--output', "/artifacts/final-$($case.Label).json"))
    }

    Invoke-Compose @('compose', '-f', 'compose.yaml', '-f', 'compose.lab.yaml', '--profile', 'test', 'run', '--rm', '--no-deps',
        'verifier', 'python', 'scripts/rebinding_demo.py', '--output', '/artifacts/final-dns-rebinding.json')
    & (Join-Path $PSScriptRoot 'export-final-results.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'export-final-results.ps1 failed' }

    Write-Output 'Final synthetic CVE demonstration passed.'
    Write-Output 'Evidence: artifacts/final-*.json and docs/verification/final-scenario-2026-11-23.json'
}
finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name])
    }
    # Never leave an acknowledgement-gated profile running after the demo.
    try {
        & docker compose -f compose.yaml -f compose.lab.yaml up -d --no-build --wait webhook internal receiver
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Could not restore the lab containers with the original environment.' }
    } catch {
        Write-Warning "Could not restore the lab containers: $($_.Exception.Message)"
    }
}
