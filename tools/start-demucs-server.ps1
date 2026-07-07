$ErrorActionPreference = "Stop"

$SourceRoot = if (Test-Path (Join-Path $PSScriptRoot "pyproject.toml")) {
    $PSScriptRoot
} else {
    Split-Path -Parent $PSScriptRoot
}
$InstallRoot = if ($env:FEEDFORGE_DEMUCS_HOME) {
    $env:FEEDFORGE_DEMUCS_HOME
} else {
    $SourceRoot
}
$Model = if ($env:FEEDFORGE_DEMUCS_MODEL) {
    $env:FEEDFORGE_DEMUCS_MODEL
} else {
    "htdemucs_6s"
}
$Device = if ($env:FEEDFORGE_DEMUCS_DEVICE) {
    $env:FEEDFORGE_DEMUCS_DEVICE
} else {
    "auto"
}
$Concurrency = if ($env:FEEDFORGE_DEMUCS_CONCURRENCY) {
    $env:FEEDFORGE_DEMUCS_CONCURRENCY
} else {
    "1"
}
$CacheRoot = Join-Path $InstallRoot "model-cache"
$env:TORCH_HOME = Join-Path $CacheRoot "torch"
$env:XDG_CACHE_HOME = $CacheRoot
$env:PIP_CACHE_DIR = Join-Path $InstallRoot "pip-cache"
$TorchIndex = if ($env:FEEDFORGE_TORCH_INDEX) {
    $env:FEEDFORGE_TORCH_INDEX
} else {
    ""
}
if ($TorchIndex -eq "auto") {
    $HasNvidiaSmi = $false
    try {
        $null = Get-Command nvidia-smi.exe -ErrorAction Stop
        $HasNvidiaSmi = $true
    } catch {
        $HasNvidiaSmi = $false
    }
    $TorchIndex = if ($HasNvidiaSmi) { "https://download.pytorch.org/whl/cu128" } else { "" }
}
$Venv = Join-Path $InstallRoot ".demucs-venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$SystemPython = $null
if ($env:FEEDFORGE_PYTHON_EXE -and (Test-Path $env:FEEDFORGE_PYTHON_EXE)) {
    $SystemPython = $env:FEEDFORGE_PYTHON_EXE
} else {
    try {
        $SystemPython = (Get-Command python.exe -ErrorAction Stop).Source
    } catch {
        try {
            $SystemPython = (Get-Command py.exe -ErrorAction Stop).Source
        } catch {
            $SystemPython = $null
        }
    }
}
$Marker = Join-Path $InstallRoot ".feedforge-stems-source"
$SourceStamp = "$SourceRoot|$((Get-Item (Join-Path $SourceRoot "pyproject.toml")).LastWriteTimeUtc.Ticks)|torch=$TorchIndex"

if (-not (Test-Path $Python)) {
    if (-not $SystemPython) {
        Write-Error "Python 3.11 or newer was not found. Install Python from https://www.python.org/downloads/windows/ and enable 'Add python.exe to PATH', then start the local stem server again."
        exit 2
    }
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    if ((Split-Path -Leaf $SystemPython) -ieq "py.exe") {
        & $SystemPython -3 -m venv $Venv
    } else {
        & $SystemPython -m venv $Venv
    }
}

if (-not (Test-Path $Marker) -or (Get-Content $Marker -Raw -ErrorAction SilentlyContinue) -ne $SourceStamp) {
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -e "$SourceRoot[stems]"
    if ($TorchIndex) {
        & $Python -m pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url $TorchIndex
    }
    Set-Content -Encoding UTF8 -Path $Marker -Value $SourceStamp
}
& $Python -m feedback_converter.demucs_server --host 127.0.0.1 --port 7865 --model $Model --device $Device --concurrency $Concurrency --preload-model
