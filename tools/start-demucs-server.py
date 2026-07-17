from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def log(message: str) -> None:
    print(f"FeedForge: {message}", flush=True)


def run(python: Path | str, *args: str) -> None:
    subprocess.run([str(python), *args], check=True)


def venv_python(venv_root: Path) -> Path:
    return venv_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main() -> int:
    script_root = Path(__file__).resolve().parent
    source_root = script_root if (script_root / "pyproject.toml").is_file() else script_root.parent
    install_root = Path(os.environ.get("FEEDFORGE_DEMUCS_HOME") or source_root).resolve()
    model = os.environ.get("FEEDFORGE_DEMUCS_MODEL") or "htdemucs_6s"
    device = os.environ.get("FEEDFORGE_DEMUCS_DEVICE") or "auto"
    concurrency = os.environ.get("FEEDFORGE_DEMUCS_CONCURRENCY") or "1"
    torch_index = os.environ.get("FEEDFORGE_TORCH_INDEX") or ""
    if torch_index == "auto":
        torch_index = "https://download.pytorch.org/whl/cu128" if shutil.which("nvidia-smi") else ""

    cache_root = install_root / "model-cache"
    runtime_root = install_root / "runtime"
    temp_root = runtime_root / "temp"
    storage_root = runtime_root / "jobs"
    for folder in (cache_root, temp_root, storage_root):
        folder.mkdir(parents=True, exist_ok=True)

    os.environ.update({
        "TORCH_HOME": str(cache_root / "torch"),
        "XDG_CACHE_HOME": str(cache_root),
        "PIP_CACHE_DIR": str(install_root / "pip-cache"),
        "HF_HOME": str(cache_root / "huggingface"),
        "TMPDIR": str(temp_root),
        "TEMP": str(temp_root),
        "TMP": str(temp_root),
    })

    venv_root = install_root / ".demucs-venv"
    python = venv_python(venv_root)
    selected_python = os.environ.get("FEEDFORGE_PYTHON_EXE")
    system_python = Path(selected_python).resolve() if selected_python else Path(sys.executable).resolve()
    marker = install_root / ".feedforge-stems-source"
    source_stamp = f"{source_root}|{(source_root / 'pyproject.toml').stat().st_mtime_ns}|torch={torch_index}"

    log("preparing local stem setup")
    log(f"install folder {install_root}")
    log(f"runtime folder {runtime_root}")
    log(f"selected model {model}")
    log(f"selected device {device}")

    if not python.is_file():
        if sys.version_info < (3, 11):
            raise RuntimeError("Python 3.11 or newer is required for local stem splitting.")
        log("creating local Python environment")
        log(f"source Python {system_python}")
        run(system_python, "-m", "venv", str(venv_root))
    else:
        log("reusing local Python environment")

    installed_stamp = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    if installed_stamp != source_stamp:
        log("installing FeedForge stem dependencies")
        run(python, "-m", "pip", "install", "--upgrade", "pip")
        run(python, "-m", "pip", "install", "-e", f"{source_root}[stems]")
        if torch_index:
            probe = subprocess.run(
                [str(python), "-c", "import torch,sys;sys.exit(0 if getattr(torch.version,'cuda',None) else 1)"],
                check=False,
            )
            if probe.returncode:
                log("installing CUDA PyTorch runtime")
                run(
                    python,
                    "-m", "pip", "install", "--upgrade",
                    "torch", "torchvision", "torchaudio", "--index-url", torch_index,
                )
        marker.write_text(source_stamp, encoding="utf-8")
    else:
        log("dependencies already installed")

    log("verifying Demucs runtime")
    probe_args = ("-c", "import demucs, fastapi, soundfile, torch")
    if subprocess.run([str(python), *probe_args], check=False).returncode:
        log("repairing missing stem dependencies")
        run(python, "-m", "pip", "install", "-e", f"{source_root}[stems]")
        run(python, *probe_args)
        marker.write_text(source_stamp, encoding="utf-8")

    log("starting Demucs server")
    run(
        python,
        "-m", "feedback_converter.demucs_server",
        "--host", "127.0.0.1",
        "--port", "7865",
        "--model", model,
        "--device", device,
        "--concurrency", concurrency,
        "--storage-dir", str(storage_root),
        "--preload-model",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
