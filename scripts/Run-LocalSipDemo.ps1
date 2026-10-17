$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path -Parent $PSScriptRoot
$softphone = Join-Path $demoRoot 'runtime/deployment/demo-softphone/MicroSIP.exe'
$activeRoute = (& docker exec college-parent-portable-caller-1 python -c "import json; from app.config import settings; print(json.dumps({'driver':settings.outbound_channel_driver,'trunk':settings.sip_trunk,'ari':settings.ari_url}))") -join "`n"
if ($LASTEXITCODE -ne 0) { throw 'The local caller container is not running.' }
$route = $activeRoute | ConvertFrom-Json
if ($route.driver -ne 'pjsip' -or $route.trunk -ne 'zoiper1' -or $route.ari -ne 'http://pbx:8088/ari') {
    throw 'The caller is not on the local SIP route. Select local SIP and restart caller first.'
}
$channels = (& docker exec college-parent-portable-pbx-1 asterisk -rx 'core show channels count') -join "`n"
if ($LASTEXITCODE -ne 0 -or $channels -notmatch '(?m)^0 active channels\s*$') {
    throw 'Finish the active local call before starting another demo.'
}
if (-not (Test-Path -LiteralPath $softphone)) { throw 'The project MicroSIP executable is missing.' }
if (-not (Get-Process MicroSIP -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $softphone -WorkingDirectory (Split-Path $softphone)
}
$registered = $false
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    $contacts = (& docker exec college-parent-portable-pbx-1 asterisk -rx 'pjsip show contacts') -join "`n"
    if ($LASTEXITCODE -eq 0 -and $contacts -match '(?m)^\s*Contact:\s+zoiper1/.*\sAvail\s') { $registered = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $registered) { throw 'MicroSIP did not register. Check its local account configuration.' }
Invoke-RestMethod -Uri 'http://127.0.0.1:8113/health' | Out-Null
Write-Host 'Warming the local language model before the call (a cold start can take longer)...'
& docker exec college-parent-portable-caller-1 python -c "import httpx; from app.config import settings; response=httpx.post(settings.ollama_url.rstrip('/')+'/api/generate',json={'model':settings.ollama_model,'prompt':'','stream':False,'keep_alive':settings.ollama_keep_alive,'options':{'num_ctx':settings.ollama_num_ctx}},timeout=240,trust_env=False) if settings.ollama_enabled else None; response.raise_for_status() if response is not None else None"
if ($LASTEXITCODE -ne 0) { throw 'The local language model could not start. No call placed; free system memory and retry.' }
$credentials = Get-Content (Join-Path $demoRoot 'runtime/deployment/credentials.json') -Raw | ConvertFrom-Json
$headers = @{'X-Admin-Token' = $credentials.admin_token}
$body = '{"student_id":"demo-001"}'
Write-Host 'Preparing fictional demo audio for the local MicroSIP extension...'
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8113/calls/prepare' -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 240 | Out-Null
Write-Host 'Answer in MicroSIP on this PC. Press 1, 2 or 3, then confirm your identity and ask a question.'
$result = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8113/calls/originate' -Headers $headers -ContentType 'application/json' -Body $body
$result | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $demoRoot 'runtime/deployment/local-sip/latest-call.json')
$result | Select-Object session_id,@{n='endpoint';e={'PJSIP/zoiper1'}},@{n='channel_id';e={$_.channel.id}}
