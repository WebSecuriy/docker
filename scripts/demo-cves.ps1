param([switch]$SkipBuild)

# Runs only the synthetic Docker fixtures included in this repository.  It does
# not contact a real MLflow/Plane instance, cloud metadata endpoint, or Kali VM.
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

function Invoke-Compose([string[]]$ComposeArgs) {
    & docker @ComposeArgs
    if ($LASTEXITCODE -ne 0) { throw "Compose command failed: $ComposeArgs" }
}

$environmentNames = @(
    'WEBHOOK_BIND_IP', 'POLICY', 'ENABLED_MODES', 'RETURN_BODY', 'STORE_BODY', 'READ_BODY',
    'ALLOW_HTTP', 'REPORT_API_URL', 'CVE_DEMO_MODE', 'CVE_DEMO_ACK', 'CVE_DEMO_PLANE_TARGET'
)
$saved = @{}
foreach ($name in $environmentNames) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name)
}

try {
    # Keep the deliberately vulnerable profiles reachable only through the
    # Docker lab network and the Windows loopback-published webhook port.
    $env:WEBHOOK_BIND_IP = '127.0.0.1'
    $env:ENABLED_MODES = 'mlflow,plane'
    $env:RETURN_BODY = 'full'
    $env:STORE_BODY = 'full'
    $env:READ_BODY = 'full'
    $env:ALLOW_HTTP = 'true'
    $env:REPORT_API_URL = 'http://internal:8000/reports/alpha'
    $env:CVE_DEMO_MODE = 'all'
    $env:CVE_DEMO_ACK = 'local-lab-only'
    $env:CVE_DEMO_PLANE_TARGET = $env:REPORT_API_URL

    if (-not $SkipBuild) {
        Invoke-Compose @('compose', '-f', 'compose.yaml', '-f', 'compose.lab.yaml', '--profile', 'test', 'build')
    }
    & (Join-Path $PSScriptRoot 'prepare-lab.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'prepare-lab.ps1 failed' }

    foreach ($policy in @('baseline', 'strict')) {
        $env:POLICY = $policy
        Invoke-Compose @('compose', '-f', 'compose.yaml', '-f', 'compose.lab.yaml', 'up', '-d', '--no-build', '--wait', 'internal', 'receiver', 'webhook')
        Invoke-Compose @('compose', '-f', 'compose.yaml', '-f', 'compose.lab.yaml', '--profile', 'test', 'run', '--rm', '--no-deps',
            'verifier', 'python', 'scripts/cve_demo.py', '--policy', $policy, '--output', "/artifacts/cve-$policy.json")
    }

    Write-Output 'CVE mock demonstration passed.'
    Write-Output 'Evidence: artifacts/cve-baseline.json and artifacts/cve-strict.json'
}
finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name])
    }
    # Recreate the service with the caller's original settings so an
    # acknowledgement-gated profile is never left enabled by this launcher.
    try {
        & docker compose -f compose.yaml -f compose.lab.yaml up -d --no-build --wait webhook internal receiver
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Could not restore the lab containers with the original environment.' }
    } catch {
        Write-Warning "Could not restore the lab containers: $($_.Exception.Message)"
    }
}
