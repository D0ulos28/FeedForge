# FeedForge

![FeedForge icon](assets/feedforge.png)

FeedForge is a cross-platform desktop toolkit for converting Rocksmith `.psarc`
CDLC into FeedBack `.feedpak` packages and inspecting or editing existing
FeedPaks. This repository tracks the features and fixes from the original
[FeedForge project](https://github.com/balki97/FeedForge) while maintaining a
shared Windows, Linux, and macOS codebase.

## Platform support

| Platform | Run from source | Desktop package | WEM decoding |
| --- | --- | --- | --- |
| Windows | Supported | Portable EXE | Codec tools bundled by release CI |
| Linux | Supported | AppImage | Install `vgmstream-cli` on `PATH` |
| macOS | Supported | DMG | Install `vgmstream-cli` on `PATH` |

macOS packages are currently unsigned, so Gatekeeper may require manual
approval. Linux and macOS use the same application, converter, and Python stem
launcher as Windows; only optional native codec executables differ by platform.

## Features

- Convert individual PSARCs or recursively imported folders.
- Inspect metadata, cover art, arrangements, lyrics, tones, and duration before
  conversion.
- Open and edit existing FeedPaks, including metadata, authors, cover art, and
  stem separation.
- Preserve or customize batch output layouts and filenames.
- Inspect tone timelines and mapped equipment assets.
- Split stems through the unified local Demucs setup or a compatible remote
  server.
- Stream large uploads and lazily read PSARC contents to reduce memory use.
- Convert DDS images with Pillow and WAV audio with `soundfile` on every OS.

## Install from source

Requirements:

- Python 3.11 or newer
- Node.js 20 or newer
- `vgmstream-cli` available on `PATH` on Linux/macOS for WEM audio decoding

```bash
git clone https://github.com/D0ulos28/FeedForge.git
cd FeedForge
python -m venv .venv
```

Activate the environment:

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1

# Linux/macOS
source .venv/bin/activate
```

Install and run:

```bash
python -m pip install -e ".[dev]"
npm ci
npm run dev
```

## Build

Build the converter and a desktop package for the current operating system:

```bash
npm run release
```

Platform-specific desktop commands are also available:

```bash
npm run electron:pack:win
npm run electron:pack:linux
npm run electron:pack:mac
```

PyInstaller includes native codec tools when they are present in
`src/feedback_converter/tools`; otherwise the converter searches `PATH`.
Release CI supplies the existing Windows codec bundle and produces Windows,
Linux, and macOS artifacts through one build definition.

## Test

```bash
python -m pytest -q
npm test
npm run build
```

## Stem splitting

The in-app local stem setup uses `tools/start-demucs-server.py` on every
platform. It creates an isolated environment under the selected Demucs folder,
installs the `stems` dependencies, reuses downloaded models, and starts the
local service at `http://127.0.0.1:7865`.

A compatible remote Demucs server can be used instead. FeedForge retains the
full mix in `stems/full.ogg` when separated stems are added.

## Keeping current with upstream

The recommended Git remotes are:

```bash
git remote add upstream https://github.com/balki97/FeedForge.git
git fetch upstream
git merge upstream/main
```

Upstream feature merges should be kept separate from portability commits. This
makes platform changes easy to review, test, and propose back to the original
project without fork-specific merge noise.

## Diagnostics

Debug logs are stored in Electron's platform-specific user-data directory:

- Windows: `%APPDATA%\FeedForge\logs\feedforge-debug.log`
- Linux: `~/.config/FeedForge/logs/feedforge-debug.log`
- macOS: `~/Library/Application Support/FeedForge/logs/feedforge-debug.log`

## Community

Join the original FeedForge Discord server for announcements, support, bug
reports, and feature requests:

https://discord.gg/9cUe6cacQN

## Credits

FeedForge was created by [balki97](https://github.com/balki97) and is developed
in the original [balki97/FeedForge](https://github.com/balki97/FeedForge)
repository. This fork preserves that attribution and community information.

The cross-platform conversion refactor, memory and I/O optimizations, PyTorch
concurrency tuning, and subsequent integration work in this fork were developed
with AI assistance, including Google Antigravity and OpenAI Codex.
