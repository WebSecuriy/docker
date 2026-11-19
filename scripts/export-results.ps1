param([string]$Output = 'docs/verification/docker-matrix.json')
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$expected = @('baseline-open-full','baseline-blocked-full','reject-open-full','reject-blocked-full',
    'strict-open-full','strict-blocked-full','baseline-return-mask','baseline-store-mask','baseline-read-mask')
$rows = @()
foreach ($name in $expected) {
    $source = Join-Path 'artifacts' "$name.json"
    $data = Get-Content -Raw -Encoding UTF8 -LiteralPath $source | ConvertFrom-Json
    if ($data.passed -ne $true -or $data.cases.Count -ne 4 -or $data.normal_success_rate -ne 1) {
        throw "Invalid or failing result: $name"
    }
    $cases = @($data.cases | ForEach-Object {
        [ordered]@{ mode=$_.mode; kind=$_.kind; run_id=$_.run_id; status=$_.result.status;
            outcome=$_.result.outcome; internal_api_reached=$_.internal_api_reached;
            report_content_exposed=$_.report_content_exposed; error=$_.result.error;
            trace=$_.result.trace }
    })
    $rows += [ordered]@{ name=$name; policy=$data.policy; network=$data.network; body=$data.body;
        normal_success_rate=$data.normal_success_rate; passed=$data.passed; cases=$cases;
        direct_internal=$data.direct_internal; raw_result_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash }
}
$hashes = [ordered]@{}
Get-ChildItem webhook,internal,fixtures,scripts,tests -File -Recurse | Where-Object {
    $_.Extension -in '.py','.ps1' -and $_.FullName -notmatch '__pycache__'
} | Sort-Object FullName | ForEach-Object {
    $relative = $_.FullName.Substring((Get-Location).Path.Length + 1).Replace('\','/')
    $hashes[$relative] = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
}
$report = [ordered]@{ exported_at=[DateTimeOffset]::Now.ToString('o'); environment='Windows Docker Desktop, Linux containers, python:3.12-slim';
    test_command='powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-matrix.ps1 -SkipBuild';
    matrix_count=$rows.Count; delivery_cases=36; normal_successes=18; rows=$rows; source_sha256=$hashes }
$destination = Join-Path (Get-Location) $Output
[IO.Directory]::CreateDirectory((Split-Path $destination)) | Out-Null
[IO.File]::WriteAllText($destination, (ConvertTo-Json -InputObject $report -Depth 40), [Text.UTF8Encoding]::new($false))
Write-Output "Exported $($rows.Count) passing matrix cases to $Output"
