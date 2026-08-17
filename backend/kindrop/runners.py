import subprocess
from pathlib import Path

from .domain import ConversionPreset
from .kcc import build_kcc_command


class KccRunner:
    def run(
        self, source: Path, output_directory: Path, preset: ConversionPreset, title: str
    ) -> list[Path]:
        output_directory.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            build_kcc_command(source, output_directory, preset, title),
            check=False,
            capture_output=True,
            text=True,
            timeout=2 * 60 * 60,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "KCC exited without details")[-4000:]
            raise RuntimeError(f"KCC conversion failed: {detail}")
        return sorted(output_directory.glob("*.epub"))
