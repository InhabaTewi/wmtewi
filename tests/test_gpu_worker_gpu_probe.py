import subprocess

import pytest

from apps.gpu_worker.gpu_probe import GpuProbeError, NvidiaSmiGpuProbe


def completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["nvidia-smi"], returncode, stdout=stdout, stderr="")


def test_nvidia_smi_probe_reads_single_rtx_5090() -> None:
    probe = NvidiaSmiGpuProbe(
        0,
        runner=lambda *_args, **_kwargs: completed("0, NVIDIA GeForce RTX 5090, 32607, 1024, 31583\n"),
    )

    gpu = probe.probe()

    assert gpu.gpu_name == "NVIDIA GeForce RTX 5090"
    assert gpu.gpu_count == 1
    assert gpu.vram_total_mb == 32607
    assert gpu.vram_used_mb == 1024
    assert gpu.vram_free_mb == 31583


def test_nvidia_smi_probe_selects_requested_gpu_from_multiple_cards() -> None:
    probe = NvidiaSmiGpuProbe(
        1,
        runner=lambda *_args, **_kwargs: completed(
            "0, NVIDIA GPU A, 1000, 100, 900\n1, NVIDIA GPU B, 2000, 200, 1800\n"
        ),
    )

    gpu = probe.probe()

    assert gpu.gpu_count == 2
    assert gpu.gpu_name == "NVIDIA GPU B"
    assert gpu.vram_total_mb == 2000


@pytest.mark.parametrize(
    "runner, match",
    [
        (lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()), "not available"),
        (
            lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("nvidia-smi", 1)),
            "timed out",
        ),
        (lambda *_args, **_kwargs: completed("", returncode=1), "returned an error"),
        (lambda *_args, **_kwargs: completed("not,csv"), "malformed"),
        (lambda *_args, **_kwargs: completed("0, NVIDIA GPU, 1, 0, 1"), "index 1"),
    ],
)
def test_nvidia_smi_probe_reports_safe_failures(runner, match: str) -> None:
    with pytest.raises(GpuProbeError, match=match):
        NvidiaSmiGpuProbe(1, runner=runner).probe()