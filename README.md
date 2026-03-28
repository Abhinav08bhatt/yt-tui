# yt-tui

`yt-tui` is a grayscale terminal YouTube music player built with Textual, `yt-dlp`, `mpv`, and Pillow.

It is a real TUI, not a prompt-based CLI and not a GUI wrapper. The app is designed around keyboard-driven search, audio-only playback, ASCII thumbnail art, a lightweight up-next flow, and an embedded visualizer.

## Features

- full-screen Textual interface
- in-app YouTube search via `yt-dlp`
- audio-only playback through `mpv`
- ASCII thumbnail art for the current track
- border-title now-playing panel
- single `Next` list instead of separate recommendations and queue
- automatic next-track handoff when playback ends
- lightweight preload of the top `Next` item shortly before the current song ends
- embedded grayscale `cava`-style visualizer
- keyboard-first playback controls

## Layout

The current layout is:

- top: `Search`
- left: ASCII art and `Search Results`
- right top: now-playing info and progress
- right middle: `Next`
- right bottom: `Visualizer`

## Current Behavior

- Search results are fetched from YouTube through `yt-dlp`.
- Tracks only load when you explicitly select one.
- Moving the highlight in search results or `Next` does not prefetch metadata, thumbnails, or audio URLs.
- The `Next` list is derived from the currently playing track using search heuristics.
- When the current track is near the end, the first `Next` track is pre-resolved so the transition is faster.
- When a track ends, playback advances to the top item in `Next`.

## Requirements

Install these on your system:

- `python3` 3.12+
- `yt-dlp`
- `mpv`
- `cava`

Python dependencies are declared in [`pyproject.toml`](/home/avi/Code/Project/yt-tui/pyproject.toml).

## Install

Editable install:

```bash
cd /home/avi/Code/Project/yt-tui
python3 -m pip install --user -e .
```

If `~/.local/bin` is on your `PATH`, you can then launch:

```bash
yt-tui
```

You can also run the local launcher script:

```bash
cd /home/avi/Code/Project/yt-tui
./yt-tui
```

## Controls

- `/` focus search
- `Esc` focus results
- `Enter` play selected track
- `Space` pause/resume
- `Left` / `Right` seek 10 seconds
- `-` / `=` volume down/up
- `n` play next
- `p` play previous from history
- `q` or `Ctrl+C` quit

## Notes

- `yt-tui` is intentionally grayscale and terminal-native.
- The visualizer uses `cava` and prefers the current sink monitor source instead of generic `auto`.
- Recommendation quality is still heuristic, so karaoke, slowed, looped, or otherwise noisy results can still show up in `Next`.

## Development

Main code lives in [`src/yttui/app.py`](/home/avi/Code/Project/yt-tui/src/yttui/app.py).

The project currently keeps most app logic in that single file:

- search
- playback
- `mpv` IPC
- next-track generation
- preload behavior
- UI layout and styling
- embedded visualizer rendering
