$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path -Parent $PSScriptRoot
$softphone = Join-Path $demoRoot 'runtime/deployment/demo-softphone/MicroSIP.exe'
$checks = @()
foreach ($choice in @('2','3')) {
    $language = if ($choice -eq '2') { 'hi' } else { 'kn' }
    & (Join-Path $PSScriptRoot 'Run-LocalSipDemo.ps1') | Out-Null
    $call = Get-Content (Join-Path $demoRoot 'runtime/deployment/local-sip/latest-call.json') -Raw | ConvertFrom-Json
    try {
        Start-Process -FilePath $softphone -ArgumentList '/answer' -WindowStyle Hidden -Wait
        Start-Sleep -Seconds 2
        Start-Process -FilePath $softphone -ArgumentList "/dtmf:$choice" -WindowStyle Hidden -Wait
        $passed = $false
        for ($attempt = 0; $attempt -lt 10; $attempt++) {
            Start-Sleep -Milliseconds 500
            $logs = (& docker logs --since 1m college-parent-portable-caller-1 2>&1 |ForEach-Object {$_.ToString()}) -join "`n"
            $events = $logs -split "`n" |Where-Object {$_ -match '^\[CALL LATENCY\] ' -and $_ -match $call.session_id} |
                ForEach-Object {($_ -replace '^\[CALL LATENCY\] ', '') | ConvertFrom-Json}
            $latency = $events |Where-Object {$_.phase -eq 'playback_started' -and $_.input_method -eq 'dtmf' -and $_.language -eq $language} |Select-Object -First 1
            if ($logs.Contains("[ARI DTMF] channel=$($call.channel.id) digit=$choice source=ari") -and $latency) {
                $passed = $true
                $checks += [pscustomobject]@{digit=$choice;language=$language;session_id=$call.session_id;source='ARI event from MicroSIP';identity_playback_seconds=$latency.recording_finished_to_playback_seconds}
                break
            }
        }
        if (-not $passed) { throw "Local SIP key $choice did not reach the $language identity playback." }
    } finally {
        Start-Process -FilePath $softphone -ArgumentList '/hangupall' -WindowStyle Hidden -Wait
        Start-Sleep -Seconds 1
    }
}
$checks | ConvertTo-Json -Depth 5 |Set-Content (Join-Path $demoRoot 'runtime/deployment/local-sip/keypad-checks.json')
$checks | Format-Table digit,language,identity_playback_seconds
