param([Parameter(Mandatory=$true)][ValidatePattern('^\+[1-9][0-9]{7,14}$')][string]$ExpectedNumber)

$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path -Parent $PSScriptRoot
$activeRoute = (& docker exec college-parent-portable-caller-1 python -c "from app.config import settings; print(settings.outbound_channel_driver)") -join "`n"
if ($LASTEXITCODE -ne 0 -or $activeRoute.Trim() -ne 'mobile') {
    throw 'The caller is not using the phone gateway. For local SIP use scripts/Run-LocalSipDemo.ps1.'
}
$demoStudent = Import-Csv (Join-Path $demoRoot 'runtime/deployment/students.csv') |
    Where-Object { $_.student_id -eq 'demo-phone-001' }
if (@($demoStudent).Count -ne 1 -or $demoStudent.parent_phone -ne $ExpectedNumber) {
    throw "Demo record must point to $ExpectedNumber. Check students.csv before calling."
}
$demoDevice = (& wsl -d CollegePhoneGateway -- asterisk -rx 'mobile show devices') -join "`n"
if ($LASTEXITCODE -ne 0 -or $demoDevice -notmatch '(?m)^simphone\s+.*\sYes\s+Free\s') {
    throw 'The paired phone is not connected and Free. Start the gateway/profile or finish the active call first.'
}
Invoke-RestMethod -Uri 'http://127.0.0.1:8113/health' | Out-Null
$demoCredentials = Get-Content (Join-Path $demoRoot 'runtime/deployment/credentials.json') -Raw | ConvertFrom-Json
$demoHeaders = @{'X-Admin-Token' = $demoCredentials.admin_token}
$demoBody = '{"student_id":"demo-phone-001"}'
Write-Host "Preparing fictional demo audio for $ExpectedNumber..."
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8113/calls/prepare' -Headers $demoHeaders -ContentType 'application/json' -Body $demoBody
Write-Host "Placing a real cellular demo call through the paired phone to $ExpectedNumber..."
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8113/calls/originate' -Headers $demoHeaders -ContentType 'application/json' -Body $demoBody
