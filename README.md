# Aura Tools

Everyday tools for PDFs, images and videos in a clean, Notion-style web UI.
Everything runs locally on your computer. Nothing is uploaded anywhere.

| Tool | What it does |
|---|---|
| Create PDF | Combine photos (JPG, PNG, WebP, HEIC…) and Word files (DOCX) into one PDF; phone photos stay upright |
| Merge PDFs | Join several PDFs in the order you choose |
| Split PDF | Extract page ranges (`1-3, 5`) or split every N pages |
| Organize pages | Drag thumbnails into a new order, rotate or delete pages |
| Compress PDF | Shrink a PDF for email or upload forms (Light / Balanced / Strong) |
| Protect / unlock | Add an AES-256 password, or remove one you know |
| Watermark & numbers | Diagonal text watermark and page numbers on every page |
| Convert format | Any image ↔ JPG / PNG / WebP (incl. iPhone HEIC), favicon .ico, PDF → images, images/DOCX → PDF |
| Resize & compress | Shrink photos by percent or to an exact size, passport/Instagram presets, "limit to N KB", strip location data |
| Image editor | Rotate, flip, crop, save as PNG or JPG |
| Video & audio tools | Convert (MP4, MKV, WebM, MP3, M4A, WAV), extract audio, trim, shrink, make GIFs, remove sound (uses ffmpeg) |
| YouTube downloader | Video or audio, up to 4K, subtitles, playlists |
| Facebook downloader | Public videos and Reels (`facebook.com`, `fb.watch`) |
| Instagram downloader | Reels, posts and story videos (`instagram.com`) |

Works on **Windows, macOS and Linux**.

## Quick start

You need **Python 3.9 or newer** ([python.org/downloads](https://www.python.org/downloads/)).
On Windows, tick **"Add Python to PATH"** during install.

| OS | Start the app |
|---|---|
| Windows | Double-click **`run.bat`** |
| macOS | Double-click **`run.command`** (or run `./run.sh` in Terminal) |
| Linux | Run **`./run.sh`** in a terminal |

Or, on any OS, from a terminal in this folder:

```bash
python launch.py      # Windows
python3 launch.py     # macOS / Linux
```

The first run sets itself up: it creates a `.venv` folder and installs the requirements, which takes a minute.
After that it starts in a couple of seconds. The app opens in your browser at `http://127.0.0.1:5000`
(or the next free port). Keep the terminal window open while you use it; close it or press **Ctrl+C** to quit.

**Options** (work with `run.sh`, `run.command` and `launch.py`):

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

HEIC (iPhone photo) support is installed separately from `requirements-optional.txt`. If it has no build for your
system the launcher says so and carries on: everything else works.

## ffmpeg (for the video tools and downloaders)

ffmpeg is needed for HD/4K video, MP3 conversion and subtitles. You don't have to install it yourself:

- **In the app:** open any downloader page and click **Install ffmpeg**. It downloads the right build for
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

Sites change often, and an outdated yt-dlp is the main cause of `HTTP Error 403: Forbidden` (YouTube) and of
Facebook/Instagram extraction failures.

- **Playlists are paced on purpose:** requests in quick succession are what get you rate-limited. The **Pace** setting
  adds a random pause between videos (Balanced 3-8 s, Gentle 8-20 s, Fast none). If the site starts refusing
  (HTTP 429 or "not a bot"), the run stops instead of making it worse and keeps what already finished.
  Wait 10-15 minutes and use Gentle.
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
