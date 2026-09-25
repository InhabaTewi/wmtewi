from pathlib import Path


SCRIPTS = Path("scripts/local_model")


def test_lifecycle_scripts_are_scoped_to_loopback_and_named_container() -> None:
    start_script = (SCRIPTS / "start_vllm.ps1").read_text(encoding="utf-8")
    stop_script = (SCRIPTS / "stop_vllm.ps1").read_text(encoding="utf-8")
    status_script = (SCRIPTS / "status_vllm.ps1").read_text(encoding="utf-8")

    assert '"inaba-local-vllm"' in start_script
    assert '-p "127.0.0.1:$($config.port):8000"' in start_script
    assert "inaba-hf-cache" in start_script
    assert "--enforce-eager" in start_script
    assert "-State Listen" in start_script
    assert "docker system prune" not in start_script.lower()
    assert "inaba-local-vllm" in stop_script
    assert "volume rm" not in stop_script.lower()
    assert "inaba-local-vllm" in status_script


def test_benchmark_does_not_embed_a_secret() -> None:
    benchmark = (SCRIPTS / "benchmark_local_llm.py").read_text(encoding="utf-8")
    assert "INABA_LOCAL_LLM_API_KEY" in benchmark
    assert "CHANGE_ME" not in benchmark


def test_llama_cpp_lifecycle_scripts_are_scoped_to_the_managed_process() -> None:
    start_script = (SCRIPTS / "start_llamacpp.ps1").read_text(encoding="utf-8")
    stop_script = (SCRIPTS / "stop_llamacpp.ps1").read_text(encoding="utf-8")
    status_script = (SCRIPTS / "status_llamacpp.ps1").read_text(encoding="utf-8")

    assert 'engine -ne "llama_cpp"' in start_script
    assert '"--host", $config.host' in start_script
    assert '"--gpu-layers", $config.gpu_layers' in start_script
    assert '"--api-key-file", $config.api_key_file' in start_script
    assert '"--api-key", $apiKey' not in start_script
    assert "Get-NetTCPConnection -LocalPort $config.port -State Listen" in start_script
    assert "0.0.0.0" not in start_script
    assert "Get-CimInstance Win32_Process" in stop_script
    assert "Refusing to stop" in stop_script
    assert "Stop-Process -Id $state.pid" in stop_script
    assert "Get-Process -Id $state.pid" in status_script