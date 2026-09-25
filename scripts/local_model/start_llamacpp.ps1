[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot "..\..\configs\local_models\qwen3.5-9b-llamacpp.yaml")
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) { throw "Project Python environment was not found" }
if (-not (Test-Path $ConfigPath)) { throw "Local model configuration was not found" }

$env:PYTHONPATH = "$projectRoot;$env:PYTHONPATH"
$configJson = & $python -c "import json, sys; from apps.gpu_worker.local_model_config import load_local_model_runtime_config; print(json.dumps(load_local_model_runtime_config(__import__('pathlib').Path(sys.argv[1])).model_dump()))" $ConfigPath
if ($LASTEXITCODE -ne 0) { throw "Local model configuration is invalid" }
$config = $configJson | ConvertFrom-Json

if ($config.engine -ne "llama_cpp") { throw "The configuration must use engine=llama_cpp" }
if (-not (Test-Path $config.llama_server_path)) { throw "llama-server.exe was not found" }
if (-not (Test-Path $config.gguf_path)) { throw "Pinned GGUF model was not found" }

$keyLine = Get-Content (Join-Path $projectRoot ".env.worker") | Where-Object { $_ -match "^INABA_LOCAL_LLM_API_KEY=" } | Select-Object -First 1
if (-not $keyLine) { throw "INABA_LOCAL_LLM_API_KEY is required in .env.worker" }
$apiKey = $keyLine.Substring("INABA_LOCAL_LLM_API_KEY=".Length).Trim()
if (-not $apiKey -or $apiKey -eq "CHANGE_ME") { throw "INABA_LOCAL_LLM_API_KEY must be configured" }

if (Test-Path $config.state_file) {
    $state = Get-Content -Raw $config.state_file | ConvertFrom-Json
    $existingProcess = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
    if ($existingProcess) {
        Write-Output "Local llama.cpp is already running"
        exit 0
    }
    Remove-Item -Force $config.state_file
}

$listener = Get-NetTCPConnection -LocalPort $config.port -State Listen -ErrorAction SilentlyContinue
if ($listener) { throw "Port $($config.port) is already in use" }

[System.IO.File]::WriteAllText($config.api_key_file, "$apiKey`n", [System.Text.UTF8Encoding]::new($false))
$logPath = [System.IO.Path]::ChangeExtension($config.state_file, "log")
$errorLogPath = [System.IO.Path]::ChangeExtension($config.state_file, "stderr.log")
$arguments = @(
    "--model", $config.gguf_path,
    "--host", $config.host,
    "--port", "$($config.port)",
    "--ctx-size", "$($config.context_size)",
    "--gpu-layers", $config.gpu_layers,
    "--alias", $config.served_model_name,
    "--api-key-file", $config.api_key_file
)

try {
    $process = Start-Process -FilePath $config.llama_server_path -ArgumentList $arguments -PassThru -RedirectStandardOutput $logPath -RedirectStandardError $errorLogPath
    [pscustomobject]@{
        pid = $process.Id
        executable = $config.llama_server_path
        model = $config.gguf_path
        port = $config.port
    } | ConvertTo-Json | Set-Content -Encoding utf8 $config.state_file

    $headers = @{ Authorization = "Bearer $apiKey" }
    for ($attempt = 1; $attempt -le 180; $attempt++) {
        try {
            $models = Invoke-RestMethod -Headers $headers -Uri "http://127.0.0.1:$($config.port)/v1/models" -TimeoutSec 5
            if ($models.data.id -contains $config.served_model_name) {
                Write-Output "Local llama.cpp is ready on 127.0.0.1:$($config.port)"
                exit 0
            }
        } catch {}
        Start-Sleep -Seconds 2
    }
} catch {
    if ($process) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item -Force $config.state_file -ErrorAction SilentlyContinue
    throw
}

throw "Local llama.cpp did not become ready; inspect $logPath and $errorLogPath"