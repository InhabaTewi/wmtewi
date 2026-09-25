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
& $docker stop $containerName | Out-Null
& $docker rm $containerName | Out-Null
Write-Output "Local vLLM stopped"