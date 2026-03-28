# yt-tui Handoff Log

## What this app is

`yt-tui` is a terminal-first YouTube music player built as a real TUI.

Core product intent:

- launch with `yt-tui`
- stay terminal-native
- search and play YouTube music-like results
- show grayscale ASCII thumbnail art
- keep playback audio-only
- keep the interface clean, monochrome, and keyboard-driven

Current stack:

- Python
- Textual
- `yt-dlp`
- `mpv` via IPC socket
- Pillow
- `cava`

Project root:

- [pyproject.toml](/home/avi/Code/Project/yt-tui/pyproject.toml)
- [README.md](/home/avi/Code/Project/yt-tui/README.md)
- [app.py](/home/avi/Code/Project/yt-tui/src/yttui/app.py)

## Current UX

Layout:

- top: compact `Search` bar
- left top: ASCII art panel
- left bottom: `Search Results`
- right top: now-playing info panel with border title, metadata, playback state, and progress bar
- right middle: single `Next` list
- right bottom: embedded `Visualizer`
- bottom: keybinding footer

Visual direction:

- grayscale only
- black / dark gray panels
- white active accents
- border titles instead of separate header rows
- white block progress bar
- no GUI wrapper styling

Important current behavior:

- search results come from `yt-dlp ytsearch`
- tracks are only loaded when explicitly selected
- moving the highlight through results or `Next` does not fetch previews
- selecting a result starts playback and updates the now-playing panel
- ASCII art is rendered for the current track
- `Next` is built from search heuristics around the current track
- `n` plays the first item from `Next`
- when a song ends, playback advances to the top item in `Next`
- the top `Next` track is pre-resolved shortly before the current track ends
- the embedded visualizer uses `cava` and is intended to listen to the current sink monitor

## Code structure

Main file:

- [app.py](/home/avi/Code/Project/yt-tui/src/yttui/app.py)

Key pieces:

- `Track`
  - song/video model with metadata and resolved audio URL

- `YTMusicClient`
  - `search(query, limit)`
  - `enrich(track)`
  - `resolve_audio_url(track)`
  - `recommendations_for(track, limit)`
  - recommendation logic is still heuristic search, not an official YouTube Music API

- `MPVController`
  - launches `mpv`
  - talks to it over IPC
  - reports pause, position, duration, volume, idle, and eof state

- `render_thumbnail_ascii(url, width, height)`
  - downloads thumbnail
  - converts it to grayscale ASCII

- `TrackListItem`
  - list row widget used for search results and `Next`

- `ArtPanel`
  - displays ASCII art

- `BlockProgressBar`
  - thicker custom progress bar

- `CavaPanel`
  - displays the embedded visualizer output inside Textual

- `PlayerApp`
  - owns layout, CSS, search flow, playback flow, history, next-track behavior, preload flow, and embedded `cava`

## Important implementation decisions

### 1. Real TUI, not fake terminal

This app intentionally remains a true terminal UI.

Consequence:

- no fake terminal chrome
- no per-widget custom fonts
- styling comes from terminal-safe layout, contrast, and borders

### 2. Audio-only playback

The app is intentionally closer to a terminal music player than a terminal YouTube video player.

Reason:

- inline terminal video is fragile
- audio + ASCII art is more reliable and fits the product better

### 3. `Next` replaces recommendations + queue

The earlier split between recommendations and queue was removed.

Current intended logic:

- keep one list: `Next`
- auto-next and manual `n` both use its top item
- `Next` is regenerated around the current track while preserving carried remainder when appropriate

If behavior drifts, check:

- `build_next_tracks`
- `start_playback`
- `preload_next_track`
- `play_next_track`
- `refresh_playback`

### 4. Explicit loading only

There used to be preview loading on highlight. That created unnecessary network work and felt twitchy.

Current intended logic:

- highlighting rows only changes selection
- metadata / art / audio resolution happen on explicit play

### 5. Auto-next and preload

`mpv` runs with `--idle=yes`, so end-of-track detection depends on status, not just process lifetime.

Current logic:

- `MPVController.status()` exposes `idle-active` and `eof-reached`
- `refresh_playback()` uses those fields to detect end-of-track
- the app starts preloading the top `Next` item near the end of the current track

### 6. Embedded visualizer

The visualizer is an embedded `cava` reader rendered inside Textual.

Current logic:

- `cava` runs in raw ASCII mode
- output is rendered as mirrored bars
- app prefers the default sink monitor source instead of generic input auto-detection

Potential caveat:

- if the visualizer behaves strangely on a different machine, source selection will likely need adjustment first

## Current keybindings

- `/` focus search
- `Esc` focus results
- `Enter` play selected
- `Space` pause/resume
- `Left` / `Right` seek
- `-` / `=` volume
- `n` next
- `p` previous from history
- `q` or `Ctrl+C` quit

## Known rough edges / likely next work

### Next-track quality

The biggest product issue is still heuristic quality.

Good next improvements:

- filter karaoke more aggressively
- filter slowed / reverb / nightcore variants
- prefer official uploads
- avoid long loops and mixes unless explicitly searched
- dedupe better against history and current `Next`

### Visualizer quality

The visualizer works, but it is still an approximation of standalone `cava`.

Likely polish tasks:

- smoother decay
- better bar interpolation
- tighter relation to actual `cava` stereo feel
- cleaner idle behavior

### Playback verification

Still worth testing over longer runs:

- auto-next reliability across several tracks
- preload handoff timing
- manual `n` after several transitions
- behavior when `Next` empties

## Launch / install

Editable install:

```bash
python3 -m pip install --user -e /home/avi/Code/Project/yt-tui
```

Observed launcher path:

- `/home/avi/.local/bin/yt-tui`

## Short mental model for next session

If continuing later, assume:

1. user wants this to stay a polished grayscale terminal music app
2. UX feel matters as much as raw functionality
3. recommendation / `Next` quality is the main product problem
4. avoid turning this into a GUI or browser app unless explicitly asked
5. most logic still lives in [`app.py`](/home/avi/Code/Project/yt-tui/src/yttui/app.py)
