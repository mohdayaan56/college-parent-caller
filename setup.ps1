# Windows entry point. Docker supplies Python, Ollama and all inference dependencies.
[CmdletBinding()]
param(
    [ValidateSet('Check','Prerequisites','Install','Start','Verify','Stop','Logs')]
    [string]$Action = 'Check',
    [ValidateSet('auto','cpu','gpu')][string]$Profile = 'auto',
    [string]$SipAddress = '127.0.0.1',
    [switch]$Online,
    [switch]$AllowLowVram,
    [string]$TrustedCertificate
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$compose = Join-Path $root 'runtime/deployment/compose.json'
$bootstrapImage = 'python:3.11-slim-bookworm'
if ($TrustedCertificate) {
    $certificatePath = (Resolve-Path -LiteralPath $TrustedCertificate).Path
    if ([IO.Path]::GetExtension($certificatePath) -ne '.crt') { throw 'Provide a PEM-encoded trusted CA certificate with .crt extension.' }
    Copy-Item -LiteralPath $certificatePath -Destination (Join-Path $root 'deploy/ca/local-trusted-root.crt') -Force
}

function Invoke-Docker {
    param([string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed (exit $LASTEXITCODE). See output above." }
}
function Invoke-Bootstrap {
    param([string[]]$Arguments)
    Invoke-Docker -Arguments (@('run','--rm','--mount',"type=bind,source=$root,target=/workspace",'-w','/workspace',
        $bootstrapImage,'python','scripts/portable_setup.py') + $Arguments)
}
function Invoke-Compose {
    param([string[]]$Arguments)
    Invoke-Docker -Arguments (@('compose','-f',$compose) + $Arguments)
}

if ($Action -eq 'Prerequisites') {
    if (!(Get-Command docker -ErrorAction SilentlyContinue)) {
        if (!(Get-Command winget -ErrorAction SilentlyContinue)) { throw 'Install Docker Desktop from https://docs.docker.com/desktop/setup/install/windows-install/' }
        & winget install --exact --id Docker.DockerDesktop --source winget
        if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop installation did not complete.' }
    }
    Write-Host 'Open Docker Desktop, enable its WSL 2 / Linux containers backend, and complete any Windows restart it requests.'
    Write-Host 'For GPU: install the NVIDIA Windows driver. Then rerun setup.ps1 -Action Check -Profile gpu -Online.'
    exit 0
}

$computer = Get-CimInstance Win32_ComputerSystem
$operatingSystem = Get-CimInstance Win32_OperatingSystem
$ramGiB = [Math]::Round($computer.TotalPhysicalMemory / 1GB, 1)
$freeRamGiB = [Math]::Round($operatingSystem.FreePhysicalMemory * 1KB / 1GB, 1)
$driveRoot = [IO.Path]::GetPathRoot($root)
$disk = New-Object IO.DriveInfo($driveRoot)
$freeDiskGiB = [Math]::Round($disk.AvailableFreeSpace / 1GB, 1)
Write-Host "Host RAM: $ramGiB GiB total, $freeRamGiB GiB currently free; project disk free: $freeDiskGiB GiB"
if (![Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'This release requires Windows x86-64 and Linux x86-64 containers.' }
if ($ramGiB -lt 15) { throw 'At least 16 GB nominal RAM is required; 32 GB recommended for GPU deployment.' }
if ($freeRamGiB -lt 6) { Write-Warning 'Less than 6 GiB free RAM. Close other applications before installing/starting.' }
$gpuName = ''
$gpuMiB = 0
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $rows = @(& nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>$null)
    if ($LASTEXITCODE -eq 0 -and $rows.Count -gt 0) {
        $fields = $rows[0] -split ','
        $gpuName = $fields[0].Trim()
        $gpuMiB = [int]$fields[1].Trim()
        Write-Host "NVIDIA GPU: $gpuName; VRAM $([Math]::Round($gpuMiB/1024,1)) GiB; driver $($fields[2].Trim())"
    }
}
if ($Profile -eq 'auto') {
    if ($Action -in @('Start','Verify','Stop','Logs') -and (Test-Path (Join-Path $root 'runtime/deployment/profile.json'))) {
        $Profile = (Get-Content -Raw (Join-Path $root 'runtime/deployment/profile.json') | ConvertFrom-Json).profile
    } else { $Profile = if ($gpuMiB -ge 10000) { 'gpu' } else { 'cpu' } }
}
Write-Host "Selected profile: $Profile"
if ($Profile -eq 'gpu' -and $gpuMiB -lt 10000) {
    if (!$AllowLowVram -or $gpuMiB -lt 8000) { throw 'GPU profile requires at least 10 GiB VRAM by default. An 8 GB GPU can be trialled explicitly with -Profile gpu -AllowLowVram; CUDA verification must still pass.' }
    Write-Warning 'Experimental 8 GB GPU trial: model fit and latency are unverified. CUDA checks remain mandatory; watch for OOM or partial LLM offload.'
}
if (!(Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker is missing. Run: powershell -File .\setup.ps1 -Action Prerequisites' }
& docker info --format '{{.OSType}}' 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Docker daemon is unavailable. Start Docker Desktop and select Linux containers.' }
$engineType = & docker info --format '{{.OSType}}'
if ($engineType.Trim() -ne 'linux') { throw 'Switch Docker Desktop to Linux containers.' }
Invoke-Docker -Arguments @('compose','version')
$dockerMemory = [long](& docker info --format '{{.MemTotal}}')
if ($dockerMemory -lt 7GB) { throw 'Docker needs at least 8 GB nominal memory. Increase Docker/WSL memory (10-12 GB recommended on a 16 GB host).' }
if ($Action -eq 'Check') {
    $arguments = @('check','--profile',$Profile,'--host-root',$root)
    if ($Online) { $arguments += '--online' }
    Invoke-Bootstrap -Arguments $arguments
    Write-Host 'Preflight passed. GPU kernel checks are performed by Install/Verify; model downloads can still fail if network/access changes.'
    exit 0
}
if ($Action -eq 'Install') {
    if ($freeRamGiB -lt 6) { throw 'Installation/start needs at least 6 GiB currently free RAM. Close other applications and rerun Check.' }
    Invoke-Bootstrap -Arguments @('check','--profile',$Profile,'--online','--hashes')
    Invoke-Bootstrap -Arguments @('download')
    Invoke-Bootstrap -Arguments @('configure','--profile',$Profile,'--host-root',$root,'--sip-address',$SipAddress)
    Invoke-Compose -Arguments @('config','--quiet')
    Invoke-Compose -Arguments @('build')
    if ($Profile -eq 'gpu') {
        Invoke-Docker -Arguments @('run','--rm','--gpus','all','college-parent-speech:gpu','python','-c',
            'import torch; assert torch.cuda.is_available(), "CUDA unavailable"; print(torch.cuda.get_device_name(0), torch.cuda.get_device_properties(0).total_memory)')
    }
    # CPU frontend export is intentional; waveform decoder is later selected by profile.
    Invoke-Compose -Arguments @('run','--rm','--no-deps','voice-export')
    Invoke-Compose -Arguments @('up','-d','ollama')
    Start-Sleep -Seconds 3
    Invoke-Compose -Arguments @('exec','-T','ollama','ollama','create','gemma4-e2b-college:qat4','-f',
        '/workspace/models/gemma-4-E2B-text-qat/Modelfile.docker')
    Invoke-Compose -Arguments @('up','-d')
    Invoke-Compose -Arguments @('run','--rm','--no-deps','caller','python','scripts/portable_setup.py','verify','--internal','--profile',$Profile)
    Write-Host 'Installed. Open http://127.0.0.1:8113/docs. See SETUP_GUIDE.md before replacing demo data or placing a call.'
    exit 0
}
if (!(Test-Path -LiteralPath $compose)) { throw 'Run setup.ps1 -Action Install first.' }
switch ($Action) {
    'Start' { Invoke-Compose -Arguments @('up','-d') }
    'Verify' { Invoke-Compose -Arguments @('run','--rm','--no-deps','caller','python','scripts/portable_setup.py','verify','--internal','--profile',$Profile) }
    'Stop' { Invoke-Compose -Arguments @('stop') }
    'Logs' { Invoke-Compose -Arguments @('logs','--tail','100') }
}
