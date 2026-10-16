# Video Download

A small desktop app for downloading a YouTube video as MP4 or MP3, and converting a video file on your Mac to MP3. The compact, card-based interface sizes itself to your screen and uses the Kawaii Pixel Cat icon. Use **Download** for a link, or choose **Convert a local video to MP3** from the gear menu. Completed tasks appear in Recent Downloads for the current session.

## Double-click on macOS

Open `dist/Video Download.app` in Finder after building it once:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pyinstaller
.venv/bin/pyinstaller --noconfirm --clean --windowed --name "Video Download" --icon assets/kawaii-cat.icns --add-data "assets/kawaii-cat-icon.png:assets" --collect-all yt_dlp --collect-all yt_dlp_ejs --collect-all imageio_ffmpeg app.py
```

The `.app` includes Python, the interface, yt-dlp, and FFmpeg. You can copy it to Applications. You still need [Deno](https://docs.deno.com/runtime/getting_started/installation/) or [Node.js](https://nodejs.org/en/download) installed for YouTube compatibility; the app looks for it even when launched from Finder. Local file conversion does not need either runtime.

## Run from source

Requires Python 3.10 or newer. On macOS, run:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

FFmpeg is included through the Python dependencies to combine video and audio. The download works only for videos that yt-dlp can access.

Use this only for videos you have permission to download.
