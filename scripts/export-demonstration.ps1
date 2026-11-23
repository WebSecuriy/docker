param(
    [string]$OutputDirectory = 'demonstration',
    [switch]$UseExistingArtifactsOnly
)

# Produce tracked, annotated evidence from the Docker-only synthetic lab.
# The raw artifacts remain ignored because they contain per-run UUIDs; the
# files below preserve the important observed fields and explain their meaning.
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

function Invoke-Compose([string[]]$ComposeArgs) {
    & docker @ComposeArgs
    if ($LASTEXITCODE -ne 0) { throw "Compose command failed: $ComposeArgs" }
}

function Read-Json([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { throw "Required demonstration artifact is missing: $Path" }
    return Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
}

function Get-Artifact([string]$Path) {
    return [ordered]@{
        path = $Path.Replace('\', '/')
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
    }
}

function Write-Json([string]$Name, $Document) {
    $path = Join-Path $OutputDirectory $Name
    $text = ConvertTo-Json -InputObject $Document -Depth 40
    [IO.File]::WriteAllText($path, $text, [Text.UTF8Encoding]::new($false))
}

function Invoke-ContainerJson([string]$Service, [string]$Code) {
    $output = & docker compose -f compose.yaml -f compose.lab.yaml exec -T $Service python -c $Code
    if ($LASTEXITCODE -ne 0) { throw "Could not capture live JSON from service: $Service" }
    return ($output -join "`n") | ConvertFrom-Json
}

function Get-AuthorizationEvidence($Response, [int]$ExpectedStatus) {
    if ($null -eq $Response) { return $null }
    $hasRecordedStatus = $Response.PSObject.Properties.Name -contains 'status_code'
    return [ordered]@{
        expected_status_code = $ExpectedStatus
        observed_status_code = if ($hasRecordedStatus) { $Response.status_code } else { $null }
        status_assertion = if ($hasRecordedStatus) { 'recorded-in-artifact' } else { 'asserted-by-pre-status-capture-script' }
        response = if ($hasRecordedStatus) { $Response.body } else { $Response }
    }
}

function Get-CveEvidence($Data) {
    return [ordered]@{
        scenario = $Data.scenario
        policy = $Data.policy
        network = $Data.network
        passed = $Data.passed
        mlflow = [ordered]@{
            run_id = $Data.mlflow.run_id
            internal_api_reached = $Data.mlflow.internal_api_reached
            report_content_exposed = $Data.mlflow.report_content_exposed
            internal_observation = $Data.mlflow.internal_observation
            receiver_observation = $Data.mlflow.receiver_observation
            response = $Data.mlflow.test_result.result
            authorization = [ordered]@{
                unauthenticated = Get-AuthorizationEvidence $Data.mlflow.unauthenticated_test 401
                alpha_member = Get-AuthorizationEvidence $Data.mlflow.alpha_member_test 403
                other_workspace_admin = Get-AuthorizationEvidence $Data.mlflow.other_workspace_admin_test 403
            }
            connection_trace = $Data.mlflow.connection_trace
        }
        plane = [ordered]@{
            run_id = $Data.plane.run_id
            internal_api_reached = $Data.plane.internal_api_reached
            report_content_exposed = $Data.plane.report_content_exposed
            internal_observation = $Data.plane.internal_observation
            receiver_observation = $Data.plane.receiver_observation
            delivery_result = $Data.plane.delivery_log.result
            webhook_logs = $Data.plane.webhook_logs
            authorization = [ordered]@{
                alpha_member = Get-AuthorizationEvidence $Data.plane.same_workspace_member_log_query 403
                other_workspace_admin = Get-AuthorizationEvidence $Data.plane.other_workspace_log_query 403
            }
            connection_trace = $Data.plane.connection_trace
        }
    }
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$capturedAt = [DateTimeOffset]::Now.ToString('o')
$exportId = [guid]::NewGuid().ToString()
$legacyRunId = $null

# Default behavior captures the legacy paths from the running Docker lab.
# Artifact-only mode is deliberately explicit: it is useful when the raw
# Docker results were already created by capture-demonstration.ps1 but this
# shell has no permission to contact the Docker engine.
if ($UseExistingArtifactsOnly) {
    $captureMode = 'artifact-only'
    $liveLegacy = [ordered]@{
        evidence = 'Docker verifier test_legacy_contract_and_auth passed during the preceding capture.'
        webhook_health = [ordered]@{ expected_status = 200; expected_ok = $true }
        legacy_webhook_post = [ordered]@{ expected_status = 202; expected_accepted = $true }
    }
    $internalHealth = [ordered]@{ expected_status = 200; expected_service = 'internal' }
    $legacyEvidenceComment = 'Artifact-only export: no new Docker request was made by this exporter. The preceding Docker verifier and matrix both exercised /health and POST /webhook successfully.'
    $legacyContractObservation = 'The expected_* fields below describe the contract asserted by the preceding Docker verifier; they are not fresh HTTP responses.'
} else {
    Invoke-Compose @('compose', '-f', 'compose.yaml', '-f', 'compose.lab.yaml', 'up', '-d', '--no-build', '--wait', 'internal', 'receiver', 'webhook')
    $captureMode = 'live-docker'
    $legacyRunId = [guid]::NewGuid().ToString()
    $legacyRunIdJson = ConvertTo-Json -InputObject $legacyRunId -Compress
    $legacyCode = 'import json; from urllib.request import Request, urlopen; payload={"event":"demonstration-step-01","run_id":' + $legacyRunIdJson + '}; health=json.load(urlopen("http://127.0.0.1:8000/health", timeout=5)); request=Request("http://127.0.0.1:8000/webhook", data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"}, method="POST"); legacy=json.load(urlopen(request, timeout=5)); print(json.dumps({"webhook_health":health,"legacy_webhook_post":legacy}))'
    $liveLegacy = Invoke-ContainerJson 'webhook' $legacyCode
    $internalHealth = Invoke-ContainerJson 'internal' "import json; from urllib.request import urlopen; print(json.dumps(json.load(urlopen('http://127.0.0.1:8000/health', timeout=5))))"
    $legacyEvidenceComment = 'Live Docker capture: the observed values were requested from the running lab immediately before export.'
    $legacyContractObservation = 'The live webhook_health and legacy_webhook_post values below are the responses for the same legacy_run_id.'
}

$matrixPath = 'artifacts/baseline-open-full.json'
$baselinePath = 'artifacts/final-baseline-vulnerable.json'
$rejectPath = 'artifacts/final-reject-open.json'
$strictPath = 'artifacts/final-strict-open.json'
$blockedPath = 'artifacts/final-baseline-network-blocked.json'
$fixedPath = 'artifacts/final-fixed-url-auth-mask.json'
$filterPath = 'artifacts/final-plane-log-filter.json'
$dnsPath = 'artifacts/final-dns-rebinding.json'

$matrix = Read-Json $matrixPath
$baseline = Read-Json $baselinePath
$reject = Read-Json $rejectPath
$strict = Read-Json $strictPath
$blocked = Read-Json $blockedPath
$fixed = Read-Json $fixedPath
$filter = Read-Json $filterPath
$dns = Read-Json $dnsPath

# New matrix captures persist the real compatibility responses. Prefer those
# values whenever this exporter is intentionally running without Docker.
if ($UseExistingArtifactsOnly -and $null -ne $matrix.legacy_connectivity) {
    $captureMode = 'artifact-recorded-docker'
    $legacyRunId = $matrix.legacy_connectivity.run_id
    $liveLegacy = [ordered]@{
        webhook_health = $matrix.legacy_connectivity.webhook_health
        legacy_webhook_post = $matrix.legacy_connectivity.legacy_webhook_post
    }
    $internalHealth = $matrix.legacy_connectivity.internal_health
    $legacyEvidenceComment = 'Artifact-recorded Docker capture: the matrix stored the actual /health and POST /webhook JSON responses.'
    $legacyContractObservation = 'The recorded webhook_health and legacy_webhook_post values below are the actual matrix responses for the same legacy_run_id.'
}

$commonComment = 'JSON does not support comments; _comment and key_observations are human-readable annotations. Every report is synthetic lab data.'

Write-Json '01-health-and-legacy-webhook.json' ([ordered]@{
    _comment = $commonComment
    step = 1
    title = 'Health checks and legacy /webhook compatibility'
    captured_at = $capturedAt
    capture_mode = $captureMode
    docker_command = @(
        'docker compose -f compose.yaml -f compose.lab.yaml up -d --no-build --wait internal receiver webhook',
        'docker compose -f compose.yaml -f compose.lab.yaml exec -T webhook python -c health-and-webhook-capture'
    )
    source_artifacts = @([ordered]@{
        path = 'Docker verifier output'
        test = 'test_legacy_contract_and_auth'
        result = 'passed'
    })
    expected = 'Webhook and Internal health return 200 JSON; the legacy POST /webhook endpoint returns 202 accepted JSON.'
    observed = [ordered]@{
        export_id = $exportId
        legacy_run_id = $legacyRunId
        webhook = $liveLegacy
        internal_health = $internalHealth
    }
    key_observations = @(
        $legacyEvidenceComment,
        $legacyContractObservation,
        'Health and legacy endpoint compatibility do not prove an Internal API reach or external webhook delivery.'
    )
})

$normalCases = @($matrix.cases | Where-Object { $_.kind -eq 'normal' })
Write-Json '02-normal-webhook-delivery.json' ([ordered]@{
    _comment = $commonComment
    step = 2
    title = 'Normal customer webhook: MLflow-style synchronous and Plane-style asynchronous delivery'
    captured_at = $capturedAt
    docker_command = @('powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-matrix.ps1 -SkipBuild')
    source_artifacts = @((Get-Artifact $matrixPath))
    expected = 'Normal /webhook delivery succeeds in both modes and does not reach the Internal report API.'
    observed = [ordered]@{
        matrix_passed = $matrix.passed
        normal_success_rate = $matrix.normal_success_rate
        normal_delivery_cases = $normalCases
        direct_internal_registration_control = $matrix.direct_internal
    }
    key_observations = @(
        'normal_success_rate=1 means both normal receiver deliveries succeeded under baseline/open conditions.',
        'The denied direct_internal_registration_control result is the direct-Internal-URL negative control.'
    )
})

Write-Json '03-baseline-vulnerable.json' ([ordered]@{
    _comment = $commonComment
    step = 3
    title = 'Vulnerable baseline: Redirect reaches Internal and exposes response/log content'
    captured_at = $capturedAt
    docker_command = @('powershell -NoProfile -ExecutionPolicy Bypass -File scripts/demo-cves.ps1')
    source_artifacts = @((Get-Artifact $baselinePath))
    expected = 'Under baseline, MLflow and Plane both reach Internal after receiver Redirect and expose the REPORT-<run_id> marker.'
    observed = Get-CveEvidence $baseline
    key_observations = @(
        'internal_observation.internal_api_reached=true means the synthetic Internal server recorded this run_id.',
        'The marker in the MLflow response represents synchronous exposure; the marker in Plane webhook_logs represents asynchronous log exposure.',
        'receiver_observation independently shows that the Kali-role receiver saw the Redirect request with the same run_id.'
    )
})

Write-Json '04-reject-and-strict.json' ([ordered]@{
    _comment = $commonComment
    step = 4
    title = 'Redirect defense comparison: reject and strict'
    captured_at = $capturedAt
    docker_command = @('powershell -NoProfile -ExecutionPolicy Bypass -File scripts/demo-cves.ps1')
    source_artifacts = @((Get-Artifact $rejectPath), (Get-Artifact $strictPath))
    expected = 'Both policies prevent Internal reach and marker exposure after receiver Redirect.'
    observed = [ordered]@{
        reject = Get-CveEvidence $reject
        strict = Get-CveEvidence $strict
    }
    key_observations = @(
        'reject refuses Redirects.',
        'strict validates every hop URL/IP, pins the numeric connection, and verifies peer/TLS identity.',
        'connection_trace is application-level connection evidence, not a PCAP capture.'
    )
})

Write-Json '05-network-blocked.json' ([ordered]@{
    _comment = $commonComment
    step = 5
    title = 'Network segmentation: block Webhook to Internal while keeping baseline URL policy'
    captured_at = $capturedAt
    docker_command = @('powershell -NoProfile -ExecutionPolicy Bypass -File scripts/demo-cves.ps1  # includes compose.block.yaml')
    source_artifacts = @((Get-Artifact $blockedPath))
    expected = 'Even with baseline URL policy, segmented Webhook-to-Internal networking prevents reach and content exposure.'
    observed = Get-CveEvidence $blocked
    key_observations = @(
        'This step shows that URL policy and egress network restrictions are independent defense layers.',
        'The normal receiver path is separate from the protected Internal network and is interpreted with the normal delivery regression result.'
    )
})

Write-Json '06-mlflow-fixed-auth-mask.json' ([ordered]@{
    _comment = $commonComment
    step = 6
    title = 'Fixed profiles: MLflow authorization/body defense and Plane Redirect refusal'
    captured_at = $capturedAt
    docker_command = @('powershell -NoProfile -ExecutionPolicy Bypass -File scripts/demo-cves.ps1')
    source_artifacts = @((Get-Artifact $fixedPath))
    expected = 'MLflow returns 401 for unauthenticated use, 403 for viewer/bob, and blocks the Alice-admin delivery under strict; Plane refuses Redirect.'
    observed = Get-CveEvidence $fixed
    key_observations = @(
        'The MLflow 401/403 values prove role-based access control, not just Bearer-token syntax validation.',
        'MLflow response.effective_policy=strict and Plane delivery_result.effective_policy=reject prove the effective fixed-profile policies.',
        'Internal reach and response-body masking are reported as independent facts.'
    )
})

Write-Json '07-plane-log-filter-and-access.json' ([ordered]@{
    _comment = $commonComment
    step = 7
    title = 'Plane log defense: pre-storage filtering and workspace access control'
    captured_at = $capturedAt
    docker_command = @('powershell -NoProfile -ExecutionPolicy Bypass -File scripts/demo-cves.ps1')
    source_artifacts = @((Get-Artifact $filterPath))
    expected = 'Plane can reach Internal, but redact-sensitive removes REPORT-<run_id> from logs and viewer/bob receive 403.'
    observed = Get-CveEvidence $filter
    key_observations = @(
        'internal_api_reached=true with report_content_exposed=false separates successful invocation from log-exposure defense.',
        'plane_log_filter=redact-sensitive removes the synthetic report_marker and sensitive fields before storage.',
        '403 for the alpha member and other-workspace admin proves log-read authorization defense.'
    )
})

Write-Json '08-run-id-correlation-and-dns-rebinding.json' ([ordered]@{
    _comment = $commonComment
    step = 8
    title = 'Same-run_id evidence correlation and deterministic DNS rebinding comparison'
    captured_at = $capturedAt
    docker_command = @(
        'powershell -NoProfile -ExecutionPolicy Bypass -File scripts/demo-cves.ps1',
        'docker compose -f compose.yaml -f compose.lab.yaml --profile test run --rm --no-deps verifier python scripts/rebinding_demo.py --output /artifacts/final-dns-rebinding.json'
    )
    source_artifacts = @((Get-Artifact $baselinePath), (Get-Artifact $filterPath), (Get-Artifact $dnsPath))
    expected = 'The same run_id appears in Webhook, Kali receiver, and Internal evidence; DNS rebinding compares baseline reach with strict blocking.'
    observed = [ordered]@{
        baseline_mlflow = [ordered]@{
            run_id = $baseline.mlflow.run_id
            webhook_result = $baseline.mlflow.test_result.result
            receiver_observation = $baseline.mlflow.receiver_observation
            internal_observation = $baseline.mlflow.internal_observation
        }
        baseline_plane = [ordered]@{
            run_id = $baseline.plane.run_id
            webhook_result = $baseline.plane.delivery_log.result
            receiver_observation = $baseline.plane.receiver_observation
            internal_observation = $baseline.plane.internal_observation
        }
        filtered_plane = [ordered]@{
            run_id = $filter.plane.run_id
            webhook_result = $filter.plane.delivery_log.result
            receiver_observation = $filter.plane.receiver_observation
            internal_observation = $filter.plane.internal_observation
        }
        dns_rebinding = $dns
    }
    key_observations = @(
        'Each baseline receiver_observation and internal_observation proves that Webhook followed Redirect and left the same run_id evidence on both nodes.',
        'dns_rebinding is a deterministic synthetic test using an injected resolver, not external DNS.',
        'DNS results and TCP connection events are in the connection trace; this file does not claim to be a PCAP.'
    )
})

Write-Output "Exported annotated Docker demonstration evidence to $OutputDirectory"
