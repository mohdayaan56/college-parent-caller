param([ValidateSet('en','hi','kn','both','all')][string]$Language = 'all', [switch]$Audible = $true)
$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $demoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'The project Windows Python environment is missing.' }
Push-Location $demoRoot
try {
    & $python -c 'import pycaw,comtypes,httpx,websockets'
    if ($LASTEXITCODE -ne 0) { throw 'Install the Windows audio-test dependencies: .venv/Scripts/python.exe -m pip install -r scripts/audio-test-requirements.txt' }
    Invoke-RestMethod -Uri 'http://127.0.0.1:8113/health' | Out-Null
    Write-Host 'Warming the model and preparing English/Hindi/Kannada test speech...'
    & docker exec college-parent-portable-caller-1 python -c "import httpx; from app.config import settings; r=httpx.post(settings.ollama_url.rstrip('/')+'/api/generate',json={'model':settings.ollama_model,'prompt':'','stream':False,'keep_alive':settings.ollama_keep_alive,'options':{'num_ctx':settings.ollama_num_ctx}},timeout=240,trust_env=False); r.raise_for_status()"
    if ($LASTEXITCODE -ne 0) { throw 'Local model warm-up failed; no call placed.' }
    & docker exec college-parent-portable-caller-1 python -m scripts.prepare_native_audio_test
    if ($LASTEXITCODE -ne 0) { throw 'Test speech preparation failed; no call placed.' }
    $testArguments = @('-m','scripts.run_native_audio_test','--language',$Language)
    if ($Audible) { $testArguments += '--audible' }
    & $python @testArguments
    if ($LASTEXITCODE -ne 0) { throw 'Audio test did not complete; inspect runtime/deployment/local-sip/audio-test/result.json.' }
} finally { Pop-Location }
