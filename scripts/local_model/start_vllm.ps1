[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot "..\..\configs\local_models\qwen3.5-9b.yaml")
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$containerName = "inaba-local-vllm"
$dockerBin = "C:\Program Files\Docker\Docker\resources\bin"
$docker = Join-Path $dockerBin "docker.exe"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $docker)) { throw "Docker Desktop CLI was not found" }
if (-not (Test-Path $python)) { throw "Project Python environment was not found" }
if (-not (Test-Path $ConfigPath)) { throw "Local model configuration was not found" }

$env:PYTHONPATH = "$projectRoot;$env:PYTHONPATH"
$configJson = & $python -c "import json, sys; from apps.gpu_worker.local_model_config import load_local_model_runtime_config; print(json.dumps(load_local_model_runtime_config(__import__('pathlib').Path(sys.argv[1])).model_dump()))" $ConfigPath
if ($LASTEXITCODE -ne 0) { throw "Local model configuration is invalid" }
$config = $configJson | ConvertFrom-Json

$keyLine = Get-Content (Join-Path $projectRoot ".env.worker") | Where-Object { $_ -match "^INABA_LOCAL_LLM_API_KEY=" } | Select-Object -First 1
if (-not $keyLine) { throw "INABA_LOCAL_LLM_API_KEY is required in .env.worker" }
$apiKey = $keyLine.Substring("INABA_LOCAL_LLM_API_KEY=".Length).Trim()
if (-not $apiKey -or $apiKey -eq "CHANGE_ME") { throw "INABA_LOCAL_LLM_API_KEY must be configured" }

$existing = & $docker ps -aq --filter "name=^/$containerName$"
if ($existing) {
    $running = & $docker inspect --format "{{.State.Running}}" $containerName
    if ($running -eq "true") { Write-Output "Local vLLM is already running"; exit 0 }
    & $docker rm $containerName | Out-Null
}
$listener = Get-NetTCPConnection -LocalPort $config.port -State Listen -ErrorAction SilentlyContinue
if ($listener) { throw "Port $($config.port) is already in use" }

& $docker run -d --name $containerName --gpus all --restart unless-stopped `
    -p "127.0.0.1:$($config.port):8000" `
    -v "inaba-hf-cache:/root/.cache/huggingface" `
    $config.vllm_image $config.hf_repo `
    --revision $config.revision `
    --served-model-name $config.served_model_name `
    --api-key $apiKey `
    --max-model-len $config.max_model_len `
    --max-num-seqs $config.max_num_seqs `
    --gpu-memory-utilization $config.gpu_memory_utilization `
    --dtype $config.dtype `
    $(if ($config.enforce_eager) { "--enforce-eager" }) | Out-Null

$headers = @{ Authorization = "Bearer $apiKey" }
for ($attempt = 1; $attempt -le 180; $attempt++) {
    try {
        $models = Invoke-RestMethod -Headers $headers -Uri "http://127.0.0.1:$($config.port)/v1/models" -TimeoutSec 5
        if ($models.data.id -contains $config.served_model_name) {
            Write-Output "Local vLLM is ready on 127.0.0.1:$($config.port)"
            exit 0
        }
    } catch {}
    Start-Sleep -Seconds 2
}

& $docker logs --tail 100 $containerName
throw "Local vLLM did not become ready"