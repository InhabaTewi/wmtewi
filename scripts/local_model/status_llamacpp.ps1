[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot "..\..\configs\local_models\qwen3.5-9b-llamacpp.yaml")
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) { throw "Project Python environment was not found" }
$env:PYTHONPATH = "$projectRoot;$env:PYTHONPATH"
$configJson = & $python -c "import json, sys; from apps.gpu_worker.local_model_config import load_local_model_runtime_config; print(json.dumps(load_local_model_runtime_config(__import__('pathlib').Path(sys.argv[1])).model_dump()))" $ConfigPath
if ($LASTEXITCODE -ne 0) { throw "Local model configuration is invalid" }
$config = $configJson | ConvertFrom-Json

if (-not (Test-Path $config.state_file)) {
    Write-Output "Local llama.cpp is not running"
    exit 0
}

$state = Get-Content -Raw $config.state_file | ConvertFrom-Json
$process = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
$listener = Get-NetTCPConnection -LocalPort $config.port -State Listen -ErrorAction SilentlyContinue
Write-Output "pid=$($state.pid) running=$([bool]$process) loopback_listener=$([bool]$listener) model=$($config.served_model_name)"
& nvidia-smi --query-gpu=name,memory.used,memory.free --format=csv,noheader,nounits