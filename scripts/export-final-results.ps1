param([string]$Output = 'docs/verification/final-scenario-2026-11-24.json')

# Converts ignored, UUID-rich lab artifacts into the compact versioned record
# used by the report. The connection data is application-level trace evidence,
# not a claim that a PCAP was captured in Docker Desktop.
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

$names = @(
    'baseline-vulnerable', 'reject-open', 'strict-open', 'baseline-network-blocked',
    'fixed-url-auth-mask', 'plane-log-filter'
)
$cases = @()
foreach ($name in $names) {
    $source = Join-Path 'artifacts' "final-$name.json"
    $data = Get-Content -Raw -Encoding UTF8 -LiteralPath $source | ConvertFrom-Json
    if ($data.passed -ne $true) { throw "Failing final scenario artifact: $name" }
    $cases += [ordered]@{
        name = $name
        policy = $data.policy
        network = $data.network
        expected = $data.expected
        mlflow = [ordered]@{
            run_id = $data.mlflow.run_id
            internal_api_reached = $data.mlflow.internal_api_reached
            report_content_exposed = $data.mlflow.report_content_exposed
            outcome = $data.mlflow.test_result.result.outcome
            effective_policy = $data.mlflow.test_result.result.effective_policy
            network_event_count = $data.mlflow.test_result.result.network_event_count
            unauthenticated_denied = ($data.mlflow.unauthenticated_test.status_code -eq 401 -and
                                      $data.mlflow.unauthenticated_test.body.error -eq 'Bearer token required')
            alpha_member_denied = ($data.mlflow.alpha_member_test.status_code -eq 403 -and
                                   $data.mlflow.alpha_member_test.body.error -eq 'workspace permission denied')
            other_workspace_admin_denied = ($data.mlflow.other_workspace_admin_test.status_code -eq 403 -and
                                             $data.mlflow.other_workspace_admin_test.body.error -eq 'workspace permission denied')
        }
        plane = [ordered]@{
            run_id = $data.plane.run_id
            internal_api_reached = $data.plane.internal_api_reached
            report_content_exposed = $data.plane.report_content_exposed
            outcome = $data.plane.delivery_log.result.outcome
            effective_policy = $data.plane.delivery_log.result.effective_policy
            plane_log_filter = $data.plane.delivery_log.result.plane_log_filter
            network_event_count = $data.plane.delivery_log.result.network_event_count
            same_workspace_member_denied = ($data.plane.same_workspace_member_log_query.status_code -eq 403 -and
                                             $data.plane.same_workspace_member_log_query.body.error -eq 'workspace permission denied')
            other_workspace_denied = ($data.plane.other_workspace_log_query.status_code -eq 403 -and
                                      $data.plane.other_workspace_log_query.body.error -eq 'workspace permission denied')
        }
        raw_result_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash
    }
}

$rebindingPath = Join-Path 'artifacts' 'final-dns-rebinding.json'
$rebinding = Get-Content -Raw -Encoding UTF8 -LiteralPath $rebindingPath | ConvertFrom-Json
if ($rebinding.passed -ne $true) { throw 'Failing deterministic DNS rebinding artifact' }

$hashes = [ordered]@{}
Get-ChildItem webhook, internal, fixtures, scripts, tests -File -Recurse | Where-Object {
    $_.Extension -in '.py', '.ps1' -and $_.FullName -notmatch '__pycache__'
} | Sort-Object FullName | ForEach-Object {
    $relative = $_.FullName.Substring((Get-Location).Path.Length + 1).Replace('\', '/')
    $hashes[$relative] = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
}

$report = [ordered]@{
    generated_at = [DateTimeOffset]::Now.ToString('o')
    environment = 'Windows Docker Desktop, Linux containers, Python 3.12-slim'
    scope = 'synthetic lab only; application connection traces are not PCAP captures'
    command = 'powershell -NoProfile -ExecutionPolicy Bypass -File scripts/demo-cves.ps1'
    cves = @('CVE-2026-64849', 'CVE-2026-105636')
    case_count = $cases.Count
    cases = $cases
    dns_rebinding = $rebinding
    source_sha256 = $hashes
}
$destination = Join-Path (Get-Location) $Output
[IO.Directory]::CreateDirectory((Split-Path $destination)) | Out-Null
[IO.File]::WriteAllText($destination, (ConvertTo-Json -InputObject $report -Depth 40), [Text.UTF8Encoding]::new($false))
Write-Output "Exported $($cases.Count) final scenario results to $Output"
