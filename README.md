# FeedForge

![FeedForge icon](assets/feedforge.png)

FeedForge is a cross-platform desktop tool (supporting Windows, Linux, and macOS) for converting `.psarc` CDLC packages into `.feedpak` packages for FeedBack.

It can convert one file or a full folder of CDLC files in a batch. During import it reads song metadata, cover art, arrangements, lyrics, and duration so the files can be checked before export.

## Download & Execution

- **Windows (Pre-built)**: Download the portable EXE from the [Releases](https://github.com/D0ulos28/FeedForge/releases) page.
- **Running from Source (All Platforms)**:
  1. Clone the repository:
     ```bash
     git clone https://github.com/D0ulos28/FeedForge.git
     cd FeedForge
     ```
  2. Install Python dependencies:
     ```bash
     pip install -e .
     ```
  3. Install Node.js dependencies:
     ```bash
     npm install
     ```
  4. Run in development mode:
     ```bash
     npm run dev
     ```

## Building from Source

To build a standalone packaged version of the application:
- **Python Backend**: Pack the CLI tool into a standalone binary using PyInstaller:
  ```bash
  npm run converter:pack
  ```
- **Electron Frontend**: Package the Electron app (runs `electron-builder`):
  ```bash
  # Packages for Windows
  npm run electron:pack
  # Or build everything
  npm run release
  ```

## Platform Dependencies

FeedForge is fully cross-platform and uses:
- **Pillow**: For pure-Python DDS-to-PNG cover art conversion.
- **soundfile**: For pure-Python WAV-to-OGG Vorbis encoding.
- **vgmstream-cli** & **ww2ogg**: For Wwise WEM audio decoding. These are resolved dynamically based on the OS. On non-Windows platforms (Linux/macOS), they must be installed globally on your machine and available in your system `PATH`.

## Optimizations & Resiliency

FeedForge includes several features to handle large song libraries safely:
- **Lazy PSARC Reading**: CDLC packages are parsed and decompressed on demand, preventing RAM spikes when processing large directories.
- **Audio Streaming**: Multipart uploads and stems are streamed chunk-by-chunk to disk instead of being loaded entirely into memory.
- **PyTorch Thread Bounds**: Stem separation concurrency is automatically limited to prevent core thrashing on CPU runs.
- **UI Error Boundary**: Component rendering exceptions in the Electron frontend are caught by a global React boundary, preventing blank window screens and providing a crash log report with a reload option.

## Community

Join the FeedForge Discord server for announcements, support, bug reports, and feature requests:

https://discord.gg/9cUe6cacQN

## Usage

1. Open FeedForge.
2. Add `.psarc` files by browsing, dragging them in, or choosing a folder.
3. Choose an output folder.
4. Choose an output layout: one folder, preserve source folders, or artist folders.
5. Choose output file names: source filename, artist-song, song-artist, or a custom template.
6. Select the number of conversion workers.
7. Optional: enable stem separation or B-standard remapping.
8. Click `Convert queue`.

The app writes `.feedpak` files that can be added to FeedBack.

## Notes

- Use fewer workers if the PC becomes slow during a large batch.
- `Stop after current` pauses the queue after active conversions finish.
- Existing output files are skipped unless `Overwrite` is enabled.
- Stem separation can use the in-app `Install/start local stem server` button, FeedBack's Demucs server setting, or a custom Demucs server URL.
- The local stem server install folder stores its Python environment, cache, and downloaded Demucs models. Choose a folder on a drive with enough free space.
- Already downloaded Demucs models are detected in the selected install folder and reused on later starts.
- Very large libraries are supported through folder import and a limited queue view.
- If a conversion fails, send the debug log with your bug report. The log is located at:
  - **Windows**: `%APPDATA%\FeedForge\logs\feedforge-debug.log`
  - **Linux / macOS**: `~/.config/FeedForge/logs/feedforge-debug.log`

---

*Note: This repository is a fork of the original [FeedForge](https://github.com/balki97/FeedForge) project by [balki97](https://github.com/balki97). The cross-platform refactoring, memory/IO footprint optimizations, and PyTorch concurrency tuning in this version were implemented with assistance from Google Antigravity (AI).*
