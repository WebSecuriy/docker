param([switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$names = @('POLICY','RETURN_BODY','STORE_BODY','READ_BODY','REPORT_API_URL')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name) }
function Invoke-Compose([string[]]$ComposeArgs) {
    & docker @ComposeArgs
    if ($LASTEXITCODE -ne 0) { throw "Compose command failed: $ComposeArgs" }
}
try {
    if (-not $SkipBuild) {
        Invoke-Compose @('compose','-f','compose.yaml','-f','compose.lab.yaml','--profile','test','build')
    }
    # Use a numeric internal address, so blocked cases test routing even without DNS.
    & (Join-Path $PSScriptRoot 'prepare-lab.ps1') -UseInternalIp
    $cases = @()
    foreach ($policy in @('baseline','reject','strict')) {
        foreach ($network in @('open','blocked')) {
            $cases += @{ Policy=$policy; Network=$network; Return='full'; Store='full'; Read='full'; Label="$policy-$network-full" }
        }
    }
    $cases += @{ Policy='baseline'; Network='open'; Return='mask'; Store='full'; Read='full'; Label='baseline-return-mask' }
    $cases += @{ Policy='baseline'; Network='open'; Return='full'; Store='mask'; Read='full'; Label='baseline-store-mask' }
    $cases += @{ Policy='baseline'; Network='open'; Return='full'; Store='full'; Read='mask'; Label='baseline-read-mask' }
    foreach ($case in $cases) {
        $env:POLICY=$case.Policy
        $env:RETURN_BODY=$case.Return
        $env:STORE_BODY=$case.Store
        $env:READ_BODY=$case.Read
        $files = @('compose','-f','compose.yaml','-f','compose.lab.yaml')
        if ($case.Network -eq 'blocked') { $files += @('-f','compose.block.yaml') }
        Invoke-Compose ($files + @('up','-d','--no-build','--wait','webhook','internal','receiver'))
        Invoke-Compose ($files + @('--profile','test','run','--rm','--no-deps','verifier','python','scripts/check_lab.py',
            '--policy',$case.Policy,'--network',$case.Network,'--return-body',$case.Return,
            '--store-body',$case.Store,'--read-body',$case.Read,'--output',"/artifacts/$($case.Label).json"))
    }
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $saved[$name]) }
    # Restore the user's policy and original two-service network layout; preserve all DB volumes.
    & docker compose up -d --no-build --wait webhook internal
}
