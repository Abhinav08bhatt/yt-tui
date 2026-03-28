# yt-tui Handoff Log

## What this app is

`yt-tui` is a terminal YouTube Music client built as a real TUI, not a prompt-based CLI.

Core goals:

- launch with `yt-tui`
- clear into a full-screen TUI
- search YouTube / YouTube Music style content
- show ASCII thumbnail art
- play audio in-terminal workflow with no popup GUI player
- maintain a now-playing area, recommendations, and queue

Current stack:

- Python
- Textual for UI
- `yt-dlp` for search, metadata, and audio URL extraction
- `mpv` with IPC socket for playback/control
- Pillow for thumbnail-to-ASCII conversion

Project root:

- [pyproject.toml](/home/avi/Code/Project/yt-tui/pyproject.toml)
- [README.md](/home/avi/Code/Project/yt-tui/README.md)
- [app.py](/home/avi/Code/Project/yt-tui/src/yttui/app.py)

## Current UX

Layout:

- top: compact search bar
- left: ASCII art + search results
- right top: now-playing panel with title, metadata, playback state, progress bar
- right bottom: `Recommendations` and `Queue` in separate columns
- bottom: keybinding footer

Visual direction:

- grayscale only
- black/gray panels
- white focus borders
- gray selected rows instead of blue
- custom white block progress bar

Important current behavior:

- search results populate from `yt-dlp ytsearch`
- selecting a result starts playback
- ASCII art is rendered from the selected/playing track thumbnail
- recommendations are based on the currently playing track
- queue is also built from the currently playing track
- `n` plays the next queue item
- queue should auto-advance when a song ends

## Code structure

Main file:

- [app.py](/home/avi/Code/Project/yt-tui/src/yttui/app.py)

Key classes/functions:

- `Track`
  - simple data model for a song/video
  - stores title, artist, album, duration, thumbnail, source URL, audio URL, etc.

- `YTMusicClient`
  - `search(query, limit)`
  - `enrich(track)`
  - `resolve_audio_url(track)`
  - `recommendations_for(track, limit)`
  - currently uses YouTube search heuristics, not official/private YouTube Music recommendation APIs

- `MPVController`
  - launches `mpv`
  - talks to it through IPC socket
  - handles play/pause/seek/volume/status
  - has extra error handling for race conditions during song switches
  - status now also checks `idle-active` and `eof-reached`

- `render_thumbnail_ascii(url, width, height)`
  - downloads thumbnail
  - converts to grayscale
  - maps pixels to ASCII ramp

- `TrackListItem`
  - list item widget used for results/recommendations/queue

- `ArtPanel`
  - displays ASCII art
  - empty state should say `Nothing playing`

- `BlockProgressBar`
  - custom progress bar using block characters
  - added because Textual’s built-in progress bar looked wrong and too thin

- `PlayerApp`
  - main Textual app
  - contains CSS, layout, bindings, event handlers, search flow, preview flow, playback flow, queue flow

## Important implementation decisions

### 1. Real TUI, not fake terminal

This app intentionally stays a true terminal app using Textual. We discussed custom fonts and decided not to move toward a fake terminal window / GUI wrapper.

Consequence:

- per-widget custom fonts are not realistic
- terminal chooses the font
- app can only use styling like bold/dim/italic

### 2. Audio-only playback

The app is now a YouTube Music style player, not a YouTube video player.

Reason:

- inline video in terminal is fragile and terminal-dependent
- audio + ASCII/image-like art is much more reliable

### 3. Recommendations vs queue

There was an intermediate version where recommendations updated on hover/preview. That was too twitchy.

Current intended logic:

- recommendations should be tied to the currently playing track
- queue should contain similar songs derived from the currently playing track
- queue is what `n` and auto-next should use

If behavior drifts again, check:

- `load_preview`
- `show_preview`
- `start_playback`
- `finish_playback_start`
- `refresh_playback`

### 4. Auto-next

This was tricky because `mpv` is started with `--idle=yes`.

That means:

- when a song ends, `mpv` may still be alive
- process-alive checks alone are not enough

Fix used:

- `MPVController.status()` now reports `idle-active` and `eof-reached`
- `refresh_playback()` uses those signals to trigger queue handoff

If auto-next fails again, likely cause:

- polling race
- status interpretation issue
- queue mutation issue

Possible future upgrade:

- subscribe to mpv events instead of polling every second

## Current keybindings

- `/` focus search
- `Esc` focus results
- `Enter` play selected
- `Space` pause/resume
- `Left` / `Right` seek
- `-` / `=` volume
- `n` next from queue
- `p` previous from history
- `q` or `Ctrl+C` quit

## Known rough edges / next likely tasks

### Queue quality

The queue is usable, but still heuristic.

Good next improvements:

- filter out karaoke
- filter out slowed/reverb versions
- filter out instrumentals when user likely wants original song
- prefer official uploads/audio
- avoid long loops/mixes unless explicitly searched
- dedupe more aggressively against history/results/current queue

### Recommendation quality

The app currently uses search similarity, not official YouTube Music recommendations.

That means:

- results can be decent
- results can also be noisy or repetitive

### Playback flow

Need to verify over time:

- queue auto-next reliability
- queue depletion behavior
- manual `n` behavior after several tracks

### UI polish ideas

- cleaner metadata row
- better empty states
- queue index / up-next indicator
- explicit now-playing marker in queue
- optional repeat / shuffle modes

## Launch / install

The app was installed in editable mode with:

```bash
python3 -m pip install --user -e /home/avi/Code/Project/yt-tui
```

Command path observed:

- `/home/avi/.local/bin/yt-tui`

If edits are made in a future session, reinstall/editable refresh may still be needed.

## Short mental model for next session

If continuing later, assume:

1. user wants to keep this as a polished grayscale terminal music app
2. user cares a lot about feel/UX, not just raw functionality
3. queue/recommendation behavior is the next most important product problem
4. avoid pushing toward a GUI or browser-based solution unless explicitly requested
5. check `app.py` first because nearly all logic is centralized there right now
