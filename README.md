# FeedForge

![FeedForge icon](assets/feedforge.png)

FeedForge is a cross-platform desktop toolkit for converting Rocksmith `.psarc`
CDLC into FeedBack `.feedpak` packages and inspecting or editing existing
FeedPaks.

## Platform support

| Platform | Run from source | Desktop package | WEM decoding |
| --- | --- | --- | --- |
| Windows | Supported | Portable EXE | Codec tools bundled by release CI |
| Linux | Supported | AppImage | Install `vgmstream-cli` on `PATH` |
| macOS | Supported | DMG | Install `vgmstream-cli` on `PATH` |

macOS packages are currently unsigned, so Gatekeeper may require manual
approval. The application, converter, and Python stem launcher use the same
code on every platform; only optional native codec executables differ.

## Download

Download the package for your operating system from the latest GitHub release.
FeedForge also checks GitHub releases for newer versions from inside the app.

## Features

- Convert individual PSARCs or recursively imported folders.
- Inspect metadata, cover art, arrangements, lyrics, tones, and duration.
- Open and edit existing FeedPaks, including metadata, authors, cover art, and
  stem separation.
- Customize batch output layouts and filenames.
- Inspect tone timelines and mapped equipment assets.
- Split stems through the local Demucs setup or a compatible remote server.
- Convert DDS images with Pillow and WAV audio with `soundfile` on every OS.

## Install from source

Requirements:

- Python 3.11 or newer
- Node.js 20 or newer
- `vgmstream-cli` available on `PATH` on Linux/macOS for WEM decoding

```bash
git clone https://github.com/balki97/FeedForge.git
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

Build the converter and desktop package for the current operating system:

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

## Test

```bash
python -m pytest -q
npm test
npm run build
```

## Stem splitting

The local stem setup uses `tools/start-demucs-server.py` on every platform. It
creates an isolated environment under the selected Demucs folder, installs the
stem dependencies, reuses downloaded models, and starts the service at
`http://127.0.0.1:7865`.

A compatible remote Demucs server can be used instead. FeedForge retains the
full mix in `stems/full.ogg` when separated stems are added.

## Diagnostics

Debug logs are stored in Electron's platform-specific user-data directory:

- Windows: `%APPDATA%\FeedForge\logs\feedforge-debug.log`
- Linux: `~/.config/FeedForge/logs/feedforge-debug.log`
- macOS: `~/Library/Application Support/FeedForge/logs/feedforge-debug.log`

## Community

Join the FeedForge Discord server for announcements, support, bug reports, and
feature requests:

https://discord.gg/9cUe6cacQN
