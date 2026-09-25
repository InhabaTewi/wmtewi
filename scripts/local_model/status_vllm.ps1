[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$containerName = "inaba-local-vllm"

if (-not (Test-Path $docker)) { throw "Docker Desktop CLI was not found" }
if (-not (& $docker ps -aq --filter "name=^/$containerName$")) {
    Write-Output "Local vLLM is not running"
    exit 0
}
& $docker inspect --format "name={{.Name}} running={{.State.Running}} image={{.Config.Image}} ports={{json .NetworkSettings.Ports}}" $containerName
& nvidia-smi --query-gpu=name,memory.used,memory.free --format=csv,noheader,nounits