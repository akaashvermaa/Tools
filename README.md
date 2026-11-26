# Aura Tools

Aura is a free local app for preparing files for forms, working with PDFs, and editing photos or videos.
It works on **Windows, macOS, and Linux**, and processes your files on your computer.

## Quick start

1. Download the Aura project folder or ZIP. If you have a ZIP, extract it before launching.
2. Install **Python 3.9 or newer** if you need it. On Windows, select **Add Python to PATH** during installation. [Python downloads](https://www.python.org/downloads/)
3. Open the extracted Aura folder and run the launcher for your operating system.

| OS | Start the app |
|---|---|
| Windows | Double-click **`launch.bat`** for setup. Then double-click **`run.bat`** whenever you want to open Aura. |
| macOS | Double-click **`run.command`** (or run `./run.sh` in Terminal) |
| Linux | Run **`./run.sh`** in a terminal |

The first setup needs internet access and may take a few minutes. On Windows, `launch.bat` installs the Python
packages and ffmpeg, then caches OCR data and the background-removal model where supported. Once setup finishes,
press a key to close the window. Rerun `launch.bat` to update yt-dlp or retry missing components. `run.bat` starts Aura
using the existing installation.

You can also install and open Aura in one step from a terminal:

```bash
python launch.py      # Windows
python3 launch.py     # macOS / Linux
```

Aura opens Home in your browser automatically at `http://127.0.0.1:5000/` or the next free port. Search Home for a task such as
**resize photo**, **combine PDFs**, or **playlist**, or browse by category. Aura requires no account.
Keep the terminal open while you use the app. Press **Ctrl+C** or close the terminal to quit.

**Options** for `run.sh`, `run.command`, and `launch.py`. On Windows, `run.bat` accepts `--no-browser`, `--port`, and `--help`.

| Option | What it does |
|---|---|
| `--no-browser` | Don't open a browser tab (handy over SSH or on a server) |
| `--port 8080` | Use this port instead of 5000 |
| `--update` | Update yt-dlp now, and retry the optional extras |
| `--install-ffmpeg` | Download ffmpeg now |
| `--help` | Show the list |

### macOS

- If double-clicking `run.command` says it can't be opened, right-click it → **Open** once.
- If it isn't executable after a zip download, `run.command` still works when run as `bash run.command`;
  or run `chmod +x run.command run.sh launch.py`.
- If you have no Python 3.9+, `run.sh` says so and shows how to get it (python.org or `brew install python`).

### Linux

- Debian / Ubuntu need Python's venv and pip as separate packages:
  `sudo apt install python3 python3-venv python3-pip`. Fedora: `sudo dnf install python3 python3-pip`.
  Arch: `sudo pacman -S python python-pip`. If one is missing, the launcher says exactly what to install.
- The **Browse…** button on the downloader pages opens your desktop's folder dialog and needs `zenity`
  (GNOME and most others: `sudo apt install zenity`) or `kdialog` (KDE). Without one you can still type the path.
- On a server or over SSH, use `./run.sh --no-browser` and open the printed address (or forward the port).

### Optional extras

The launcher installs HEIC (iPhone photo) support and the CPU runtime for background removal separately. If an
extra is unavailable on your system, Aura starts with the other tools. Rerun `launch.bat` on Windows or
`python3 launch.py --update` on macOS or Linux to retry optional setup.

If the OCR data (about 4 MB) or background-removal model (about 5 MB) is missing, Aura downloads it on first use.
Aura caches both in your user cache folder. Once the files are cached, both tools work offline. OCR skips pages
that already have selectable text.

## Tools

| Tool | What it does |
|---|---|
| Photo & signature upload | Match a form's exact pixels, format and file-size cap, or clean up a signature photographed on light paper |
| Resize & compress | Shrink photos by percent or to an exact size, use presets, set a KB limit, or strip location data |
| Convert format | Convert between image formats, make a favicon, or save PDF pages as images |
| Image editor | Rotate, flip, crop, and save as PNG or JPG |
| Remove background | Cut out the main subject of a photo and save it as a transparent PNG |
| Create PDF | Combine photos and Word documents (DOCX) into one PDF |
| Make PDF searchable | Add selectable English text to scanned PDFs with local OCR |
| Merge PDFs | Join several PDFs in the order you choose |
| Split PDF | Extract page ranges (`1-3, 5`) or split every N pages |
| Organize pages | Drag thumbnails into a new order, rotate or delete pages |
| Compress PDF | Shrink a PDF for email or upload forms (Light / Balanced / Strong) |
| Protect / unlock | Add an AES-256 password or remove one you know |
| Watermark & numbers | Add a diagonal text watermark or page numbers |
| Video & audio tools | Convert MP4, MKV, WebM, MP3, M4A, and WAV, trim or shrink video, extract audio, and make GIFs |
| YouTube downloader | Save video, audio, subtitles, and playlists |
| Facebook downloader | Save videos and Reels (`facebook.com`, `fb.watch`) |
| Instagram downloader | Save Reels, posts, and story videos (`instagram.com`) |

## ffmpeg (for the video tools and downloaders)

ffmpeg is needed for HD/4K video, MP3 conversion and subtitles. You don't have to install it yourself:

- **In the app:** open any downloader page and click **Install ffmpeg**. It downloads the right build for
  your system, checks that it runs, and works right away without a restart.
- **In the terminal:** on Windows, `launch.bat` installs ffmpeg during setup if it's missing. On macOS and Linux,
  the launcher offers to install it, or you can run `python3 launch.py --install-ffmpeg`.

It is saved to the `tools/` folder inside this project. Nothing system-wide changes. To remove it, delete that folder.
If ffmpeg is already installed on your system (on PATH, Homebrew, winget), it's used automatically.

Builds come from [yt-dlp/FFmpeg-Builds](https://github.com/yt-dlp/FFmpeg-Builds), with
[BtbN](https://github.com/BtbN/FFmpeg-Builds) as a backup (Windows, Linux), and from
[martin-riedl.de](https://ffmpeg.martin-riedl.de) for macOS (Apple Silicon and Intel). The download is about 150–200 MB.
A failed or interrupted download never leaves a broken install behind: it retries, and only replaces
the files once the new ffmpeg has been checked.

## Facebook and Instagram

Paste a link on the matching page. Public videos and Reels usually work as they are. For private, friends-only or
group videos, and for Instagram links that ask you to log in, set **Browser cookies** to a browser where you're signed
in to that site. Only download videos you're allowed to watch and save.

## Choosing where downloads are saved

Each downloader page has a **Save to** row. Press **Browse…** to pick a folder with your system's own folder dialog
(or type a full path). Files are then written straight into that folder, with no browser download, which suits
playlists: songs appear there as they finish, numbered in playlist order. The folder is remembered between visits.
Leave the box empty and the files go through your browser's download as before.

## Keeping downloads working

- **YouTube and YouTube Music playlists:** turn on **Playlist** to download the full list. With it off, Aura
  downloads a single item, even from a playlist link. The **Pace** setting controls the delay between videos
  (Balanced 3–8 s, Gentle 8–20 s, Fast none). Aura stops on detected rate limits and access checks, reports
  the incomplete run, and keeps the finished files. Retry only after access is restored.
- **yt-dlp updates:** to update on Windows, run `launch.bat`. On macOS and Linux, the regular launcher checks for
  a new version at most once a day. Run `python3 launch.py --update` to update immediately.
- **YouTube access check or 403:** open the link in YouTube and follow the service's instructions.
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
launch.bat           Windows dependency setup and updates
run.bat              Windows quick launcher
run.command          macOS double-click launcher
run.sh               Linux launcher
build.py             Standalone app builder (PyInstaller)
requirements.in      Required Python packages
requirements-*.in    Optional image-processing packages
assets/logo.ico      App icon for Windows builds
aura/
  server.py          Flask routes (web API + page)
  pdf_tools.py       PDF / image / DOCX processing
  job_progress.py    Smooths download progress, speed and time left
  jobs.py            Shared background-job registry and cleanup
  folder_dialog.py   Native "choose a folder" dialog and save-folder checks
  media_jobs.py      Video/audio tools (ffmpeg jobs)
  image_tools.py     Image resize / compress / convert
  yt_jobs.py         YouTube / Facebook / Instagram downloads as background jobs
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
