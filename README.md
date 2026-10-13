# Aura Tools

Everyday tools for PDFs, images and videos in a clean, Notion-style web UI.
Everything runs locally on your computer. Nothing is uploaded anywhere.

| Tool | What it does |
|---|---|
| Create PDF | Combine images (PNG/JPG) and Word files (DOCX) into one PDF |
| Merge PDFs | Join several PDFs in the order you choose |
| Split PDF | Extract page ranges (`1-3, 5`) or split every N pages |
| Reorder pages | Drag page thumbnails into a new order |
| Convert format | PNG ↔ JPG, PDF → PNG/JPG, images/DOCX → PDF |
| Image editor | Rotate, flip, crop, save as PNG or JPG |
| YouTube downloader | Video or audio, up to 4K, subtitles, playlists |

Works on **Windows, macOS and Linux**.

## Quick start

You need **Python 3.9 or newer** ([python.org/downloads](https://www.python.org/downloads/)).
On Windows, tick **"Add Python to PATH"** during install.

| OS | Start the app |
|---|---|
| Windows | Double-click **`run.bat`** |
| macOS | Double-click **`run.command`** |
| Linux | Run **`./run.sh`** in a terminal |

Or, on any OS, from a terminal in this folder:

```bash
python launch.py      # Windows
python3 launch.py     # macOS / Linux
```

The first run sets itself up: it creates a `.venv` folder and installs the requirements, which takes a minute.
After that it starts straight away. The app opens in your browser at `http://127.0.0.1:5000`
(or the next free port). Keep the terminal window open while you use it; close it or press **Ctrl+C** to quit.

> **macOS:** if double-clicking `run.command` says it can't be opened, right-click it → **Open** once.
> If it isn't executable after copying, run `chmod +x run.command run.sh launch.py`.

## ffmpeg (for the YouTube downloader)

ffmpeg is needed for HD/4K video, MP3 conversion and subtitles. You don't have to install it yourself:

- **In the app:** open *YouTube downloader* and click **Install ffmpeg**. It downloads the right build for
  your system, checks that it runs, and works right away without a restart.
- **In the terminal:** when you launch, the app offers to download it if it's missing. You can also run
  `python3 launch.py --install-ffmpeg`.

It is saved to the `tools/` folder inside this project. Nothing system-wide changes. To remove it, delete that folder.
If ffmpeg is already installed on your system (on PATH, Homebrew, winget), it's used automatically.

Builds come from [yt-dlp/FFmpeg-Builds](https://github.com/yt-dlp/FFmpeg-Builds), with
[BtbN](https://github.com/BtbN/FFmpeg-Builds) as a backup (Windows, Linux), and from
[martin-riedl.de](https://ffmpeg.martin-riedl.de) for macOS (Apple Silicon and Intel). The download is about 150–200 MB.
A failed or interrupted download never leaves a broken install behind: it retries, and only replaces
the files once the new ffmpeg has been checked.

## Keeping YouTube downloads working

YouTube changes often, and an outdated yt-dlp is the main cause of `HTTP Error 403: Forbidden`.

- **yt-dlp updates itself:** the launcher checks for a new version at most once a day. To force an update, run
  `python3 launch.py --update` (`python launch.py --update` on Windows).
- **"Sign in to confirm you're not a bot":** on the YouTube page, set **Browser cookies** to a browser where you're
  signed in to YouTube. Firefox works best. With Chrome or Edge, close the browser completely first.
- **Still getting 403 after an update:** restart the app and try again, or use the Browser cookies option.
- Downloads are stored temporarily in your user cache folder until you save them. They are deleted when you quit,
  or an hour after they finish.

## Build a standalone app (optional)

To make a version that runs without Python installed:

```bash
python build.py       # Windows
python3 build.py      # macOS / Linux
```

The result is in `dist/Aura Tools/`. PyInstaller only builds for the OS it runs on, so build on each system you need.
A built app bundles a fixed yt-dlp version and can't update it. Rebuild it when YouTube downloads start failing.

## Project layout

```
launch.py            Cross-platform launcher (venv, requirements, yt-dlp updates, ffmpeg prompt)
run.bat              Windows double-click launcher
run.command          macOS double-click launcher
run.sh               Linux launcher
build.py             Standalone app builder (PyInstaller)
requirements.txt
assets/logo.ico      App icon for Windows builds
aura/
  server.py          Flask routes (web API + page)
  pdf_tools.py       PDF / image / DOCX processing
  yt_jobs.py         YouTube downloads as background jobs
  deps.py            Finds or downloads ffmpeg
  paths.py           App, tools and cache folders per OS
  templates/index.html
  static/            style.css, app.js, logo.png
```

## Troubleshooting

- **`python: command not found`** (Linux/macOS): use `python3`, or `./launch.py`.
- **Install fails:** check your internet connection, then delete the `.venv` folder and launch again.
- **Port in use:** the app picks the next free port automatically. To choose one, set `AURA_PORT=5050`.
- **Don't open the browser automatically:** set `AURA_NO_BROWSER=1`.
