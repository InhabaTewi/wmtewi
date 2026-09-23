import subprocess
from dataclasses import dataclass
from typing import Callable, Protocol


class GpuProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class GpuMetadata:
    gpu_name: str
    gpu_count: int
    vram_total_mb: int
    vram_used_mb: int
    vram_free_mb: int


class GpuProbe(Protocol):
    def probe(self) -> GpuMetadata: ...


SubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]


class NvidiaSmiGpuProbe:
    def __init__(
        self,
        gpu_index: int,
        command: str = "nvidia-smi",
        timeout_seconds: float = 10.0,
        runner: SubprocessRunner = subprocess.run,
    ) -> None:
        self.gpu_index = gpu_index
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.runner = runner

    def probe(self) -> GpuMetadata:
        command = [
            self.command,
            "--query-gpu=index,name,memory.total,memory.used,memory.free",
            "--format=csv,noheader,nounits",
        ]
        try:
            result = self.runner(
                command,
                capture_output=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise GpuProbeError("nvidia-smi is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise GpuProbeError("nvidia-smi timed out") from exc
        except OSError as exc:
            raise GpuProbeError("nvidia-smi could not be started") from exc
        if result.returncode != 0:
            raise GpuProbeError("nvidia-smi returned an error")

        rows = self._parse_rows(result.stdout)
        if self.gpu_index not in rows:
            raise GpuProbeError(f"configured GPU index {self.gpu_index} is unavailable")
        name, total, used, free = rows[self.gpu_index]
        return GpuMetadata(
            gpu_name=name,
            gpu_count=len(rows),
            vram_total_mb=total,
            vram_used_mb=used,
            vram_free_mb=free,
        )

    @staticmethod
    def _parse_rows(output: str) -> dict[int, tuple[str, int, int, int]]:
        rows: dict[int, tuple[str, int, int, int]] = {}
        try:
            for line in output.splitlines():
                if not line.strip():
                    continue
                index, name, total, used, free = (part.strip() for part in line.split(","))
                rows[int(index)] = (name, int(total), int(used), int(free))
        except (TypeError, ValueError) as exc:
            raise GpuProbeError("nvidia-smi returned malformed GPU metadata") from exc
        if not rows:
            raise GpuProbeError("nvidia-smi returned no GPU metadata")
        return rows