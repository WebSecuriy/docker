param([switch]$UseInternalIp)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
function Get-LabAddress([string]$Service) {
    $containerId = & docker compose -f compose.yaml -f compose.lab.yaml ps -q $Service
    if ($LASTEXITCODE -ne 0 -or -not $containerId) { throw "No container for $Service" }
    $detail = & docker inspect $containerId | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw "Cannot inspect $Service" }
    $address = @($detail[0].NetworkSettings.Networks.PSObject.Properties.Value.IPAddress)[0]
    $parsedAddress = $null
    if (-not [System.Net.IPAddress]::TryParse($address, [ref]$parsedAddress)) { throw 'Invalid container address' }
    return $address
}
& docker compose -f compose.yaml -f compose.lab.yaml up -d --no-build --wait internal
if ($LASTEXITCODE -ne 0) { throw 'Cannot start internal service' }
if ($UseInternalIp) {
    $internalAddress = Get-LabAddress 'internal'
    $env:REPORT_API_URL = "http://${internalAddress}:8000/reports/alpha"
}
& docker compose -f compose.yaml -f compose.lab.yaml up -d --no-build --wait --no-deps receiver
if ($LASTEXITCODE -ne 0) { throw 'Cannot start receiver' }
$receiverAddress = Get-LabAddress 'receiver'
$origin = if ($env:RECEIVER_URL) { $env:RECEIVER_URL } else { 'https://receiver:8443' }
$rules = @(@{ origin=$origin; cidrs=@("$receiverAddress/32") })
$json = ConvertTo-Json -InputObject $rules -Depth 4
$exceptionPath = Join-Path (Get-Location) 'config/url-exceptions.lab.local.json'
[IO.File]::WriteAllText($exceptionPath, $json, [Text.UTF8Encoding]::new($false))
Write-Output "Receiver exception restricted to $origin and $receiverAddress/32"
