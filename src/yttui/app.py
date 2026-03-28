from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from PIL import Image
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, ListItem, ListView, Static


ASCII_RAMP = " .,:;irsXA253hMHGS#9B&@"


@dataclass(slots=True)
class Track:
    video_id: str
    title: str
    artist: str
    album: str
    duration: int
    thumbnail_url: str = ""
    source_url: str = ""
    views: int = 0
    description: str = ""
    audio_url: str = ""

    @property
    def duration_label(self) -> str:
        total_seconds = int(max(self.duration, 0))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @property
    def display_artist(self) -> str:
        return self.artist or "Unknown Artist"


def run_yt_dlp(*args: str) -> str:
    command = ["yt-dlp", *args]
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


class YTMusicClient:
    def search(self, query: str, limit: int = 10) -> list[Track]:
        raw = run_yt_dlp(
            "--dump-single-json",
            "--flat-playlist",
            f"ytsearch{limit}:{query} music",
        )
        payload = json.loads(raw)
        entries = payload.get("entries") or []
        tracks: list[Track] = []
        for entry in entries:
            video_id = entry.get("id")
            if not video_id:
                continue
            thumbnails = entry.get("thumbnails") or []
            tracks.append(
                Track(
                    video_id=video_id,
                    title=entry.get("title") or "Unknown Title",
                    artist=entry.get("channel") or entry.get("uploader") or "Unknown Artist",
                    album=entry.get("album") or entry.get("playlist_title") or "YouTube Music",
                    duration=int(entry.get("duration") or 0),
                    thumbnail_url=entry.get("thumbnail") or (thumbnails[-1].get("url") if thumbnails else ""),
                    source_url=f"https://www.youtube.com/watch?v={video_id}",
                    views=entry.get("view_count") or 0,
                )
            )
        return tracks

    def enrich(self, track: Track) -> Track:
        raw = run_yt_dlp(
            "--dump-single-json",
            "--no-playlist",
            track.source_url,
        )
        payload = json.loads(raw)
        thumbnails = payload.get("thumbnails") or []
        thumbnail_url = track.thumbnail_url
        if thumbnails:
            thumbnail_url = thumbnails[-1].get("url") or thumbnail_url
        artist = (
            payload.get("artist")
            or payload.get("album_artist")
            or payload.get("channel")
            or payload.get("uploader")
            or track.artist
        )
        album = payload.get("album") or payload.get("track") or track.album
        description = payload.get("description") or ""
        return Track(
            video_id=track.video_id,
            title=payload.get("track") or payload.get("title") or track.title,
            artist=artist,
            album=album,
            duration=int(payload.get("duration") or track.duration),
            thumbnail_url=thumbnail_url,
            source_url=track.source_url,
            views=payload.get("view_count") or track.views,
            description=description,
            audio_url=track.audio_url,
        )

    def resolve_audio_url(self, track: Track) -> str:
        output = run_yt_dlp(
            "--no-playlist",
            "-f",
            "bestaudio/best",
            "--get-url",
            track.source_url,
        )
        return output.strip().splitlines()[0]

    def recommendations_for(self, track: Track, limit: int = 8) -> list[Track]:
        artist = track.artist if track.artist and track.artist != "Unknown Artist" else ""
        query = f"{artist} {track.title}".strip()
        recommendations = self.search(query, limit=limit + 2)
        return [item for item in recommendations if item.video_id != track.video_id][:limit]


def dedupe_tracks(tracks: list[Track], seen_ids: set[str], limit: int) -> list[Track]:
    unique: list[Track] = []
    for track in tracks:
        if track.video_id in seen_ids:
            continue
        seen_ids.add(track.video_id)
        unique.append(track)
        if len(unique) >= limit:
            break
    return unique


class MPVController:
    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.socket_path = Path(tempfile.gettempdir()) / f"yt-tui-mpv-{os.getpid()}.sock"
        self._lock = Lock()

    def start(self, audio_url: str) -> None:
        self.stop()
        if self.socket_path.exists():
            self.socket_path.unlink()
        self.process = subprocess.Popen(
            [
                "mpv",
                "--no-config",
                "--no-video",
                "--force-window=no",
                "--really-quiet",
                "--idle=yes",
                f"--input-ipc-server={self.socket_path}",
                audio_url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._wait_for_socket()

    def _wait_for_socket(self) -> None:
        for _ in range(50):
            if self.socket_path.exists():
                return
            time.sleep(0.1)
        raise RuntimeError("mpv IPC socket did not become ready")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            try:
                self.command("quit")
            except Exception:
                self.process.terminate()
        self.process = None
        if self.socket_path.exists():
            self.socket_path.unlink()

    def command(self, *args: Any) -> Any:
        with self._lock:
            if not self.process or self.process.poll() is not None:
                raise RuntimeError("mpv is not running")
            payload = json.dumps({"command": list(args)}) + "\n"
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(str(self.socket_path))
                    client.sendall(payload.encode())
                    data = client.recv(65536)
            except (FileNotFoundError, ConnectionRefusedError, BrokenPipeError, OSError) as error:
                raise RuntimeError("mpv IPC unavailable") from error
            if not data:
                return None
            try:
                return json.loads(data.decode())
            except json.JSONDecodeError as error:
                raise RuntimeError("invalid mpv IPC response") from error

    def set_pause(self, paused: bool) -> None:
        self.command("set_property", "pause", paused)

    def toggle_pause(self) -> None:
        current = self.get_property("pause")
        self.set_pause(not bool(current))

    def seek(self, seconds: int) -> None:
        self.command("seek", seconds, "relative")

    def set_volume(self, volume: int) -> None:
        self.command("set_property", "volume", max(0, min(volume, 130)))

    def get_property(self, name: str) -> Any:
        try:
            response = self.command("get_property", name)
        except RuntimeError:
            return None
        return response.get("data") if isinstance(response, dict) else None

    def status(self) -> dict[str, Any]:
        if not self.process or self.process.poll() is not None:
            return {
                "running": False,
                "pause": True,
                "time_pos": 0,
                "duration": 0,
                "volume": 0,
                "idle": True,
                "eof": False,
            }
        try:
            return {
                "running": True,
                "pause": bool(self.get_property("pause")),
                "time_pos": float(self.get_property("time-pos") or 0),
                "duration": float(self.get_property("duration") or 0),
                "volume": int(self.get_property("volume") or 0),
                "idle": bool(self.get_property("idle-active")),
                "eof": bool(self.get_property("eof-reached")),
            }
        except (TypeError, ValueError, RuntimeError):
            return {
                "running": False,
                "pause": True,
                "time_pos": 0,
                "duration": 0,
                "volume": 0,
                "idle": True,
                "eof": False,
            }


class CavaPanel(Static):
    bars = reactive("", repaint=True)

    def render(self) -> str:
        return self.bars


def render_thumbnail_ascii(url: str, width: int = 32, height: int = 18) -> str:
    if not url:
        return "\n".join([" " * width for _ in range(height)])
    with urllib.request.urlopen(url, timeout=10) as response:
        with Image.open(response) as image:
            image = image.convert("L").resize((width, height))
            pixels = list(image.getdata())
    rows = []
    for row in range(height):
        start = row * width
        chunk = pixels[start : start + width]
        rows.append("".join(ASCII_RAMP[pixel * (len(ASCII_RAMP) - 1) // 255] for pixel in chunk))
    return "\n".join(rows)


class TrackListItem(ListItem):
    def __init__(self, track: Track, index: int) -> None:
        self.track = track
        label = (
            f"[b]{index:02d}[/]  {track.title}\n"
            f"[dim]{track.display_artist}[/]  [dim]{track.duration_label}[/]"
        )
        super().__init__(Static(label))


class ArtPanel(Static):
    art = reactive("", repaint=True)

    def render(self) -> str:
        return self.art or "Nothing playing"


class BlockProgressBar(Static):
    progress = reactive(0.0, repaint=True)

    def render(self) -> str:
        width = max(self.size.width, 10)
        filled = round(width * max(0.0, min(self.progress, 1.0)))
        return ("█" * filled) + ("░" * max(width - filled, 0))


class PlayerApp(App[None]):
    TITLE = "yt-tui"
    CSS = """
    Screen {
        background: transparent;
        color: #d4d4d4;
    }

    Header {
        dock: top;
        background: #0a0a0a;
        color: #8f8f8f;
    }

    Footer {
        dock: bottom;
        background: #0a0a0a;
        color: #8f8f8f;
    }

    #shell {
        height: 1fr;
        padding: 1 2;
        background: transparent;
    }

    #search-panel {
        height: 3;
        margin-bottom: 1;
        border: solid #4a4a4a;
        background: #0c0c0c;
        padding: 0 1;
    }

    #search-panel:focus-within {
        border: solid white;
    }

    #search-box {
        height: 1;
        border: none;
        background: #0c0c0c;
    }

    #content {
        height: 1fr;
    }

    #left-pane {
        width: 36;
        min-width: 28;
        margin-right: 1;
    }

    #art {
        height: 16;
        border: solid #4a4a4a;
        padding: 1;
        background: #090909;
        content-align: center middle;
    }

    #right-pane {
        width: 1fr;
    }

    #now-playing {
        height: 13;
        border: solid #4a4a4a;
        padding: 1 2;
        background: #0c0c0c;
    }

    #now-top {
        height: 1fr;
    }

    #now-main {
        width: 1fr;
        padding-right: 2;
    }

    #now-side {
        width: 28;
        min-width: 20;
        align: right top;
    }

    #track-artist {
        color: #b8b8b8;
    }

    #track-album {
        color: #8d8d8d;
    }

    #track-meta {
        color: #959595;
    }

    #status-block {
        color: #bcbcbc;
        text-align: right;
    }

    #progress-wrap {
        margin-top: 0;
        height: 3;
    }

    #progress-meta {
        width: 1fr;
        color: #bcbcbc;
    }

    #progress-percent {
        width: 8;
        content-align: right middle;
        color: #d8d8d8;
    }

    #lists {
        height: 1fr;
        margin-top: 1;
    }

    #results-panel {
        margin-top: 1;
        height: 1fr;
    }

    #next-panel {
        height: 1fr;
        width: 1fr;
        margin-top: 1;
    }

    #cava-panel {
        height: 6;
        width: 1fr;
        border: solid #4a4a4a;
        background: #0c0c0c;
        padding: 0 1;
        margin-top: 1;
    }

    #cava {
        height: 1fr;
        color: #e6e6e6;
        background: #0c0c0c;
    }

    .panel {
        border: solid #4a4a4a;
        background: #0c0c0c;
    }

    .panel:focus-within {
        border: solid white;
    }

    ListView {
        height: 1fr;
        border: none;
        background: #0c0c0c;
    }

    ListItem {
        height: auto;
        padding: 0 1;
    }

    ListItem.active-item {
        background: #353535;
        color: white;
    }

    ListItem.active-item Static {
        background: #353535;
        color: white;
    }

    ListView > .list-view--highlight {
        background: #353535;
        color: white;
    }

    ListView:focus > .list-view--highlight {
        background: #353535;
        color: white;
    }

    #progress {
        margin-top: 1;
        color: white;
        background: #141414;
        width: 1fr;
        border: none;
        text-style: bold;
    }

    Input {
        background: #0c0c0c;
        color: #f0f0f0;
        border: none;
    }

    Input:focus {
        color: white;
    }

    #playback-line, #progress-label {
        color: #bcbcbc;
    }
    """

    BINDINGS = [
        Binding("/", "focus_search", "Search"),
        Binding("enter", "play_selected", "Play"),
        Binding("space", "toggle_pause", "Pause"),
        Binding("n", "play_next", "Next"),
        Binding("p", "play_previous", "Prev"),
        Binding("left", "seek_back", "-10s"),
        Binding("right", "seek_forward", "+10s"),
        Binding("-", "volume_down", "Vol-"),
        Binding("=", "volume_up", "Vol+"),
        Binding("escape", "focus_results", "Results"),
        Binding("ctrl+c", "quit", "Quit"),
        Binding("q", "quit", "Quit"),
    ]

    current_track: reactive[Track | None] = reactive(None)
    playback_status: reactive[str] = reactive("STOPPED")
    volume: reactive[int] = reactive(70)
    progress_label: reactive[str] = reactive("00:00 / 00:00")

    def __init__(self) -> None:
        super().__init__()
        self.client = YTMusicClient()
        self.player = MPVController()
        self.results: list[Track] = []
        self.next_tracks: list[Track] = []
        self.history: list[Track] = []
        self.history_index = -1
        self.active_items: dict[str, TrackListItem] = {}
        self.loading_playback = False
        self.preloading_next = False
        self.preloaded_next: tuple[Track, str, list[Track]] | None = None
        self.cava_process: subprocess.Popen[str] | None = None
        self.cava_config_path: Path | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="shell"):
            with Container(id="search-panel"):
                yield Input(placeholder="Search songs, artists, albums...", id="search-box")
            with Horizontal(id="content"):
                with Vertical(id="left-pane"):
                    yield ArtPanel(id="art")
                    with Vertical(id="results-panel", classes="panel"):
                        yield ListView(id="results")
                with Vertical(id="right-pane"):
                    with Vertical(id="now-playing"):
                        with Horizontal(id="now-top"):
                            with Vertical(id="now-main"):
                                yield Static("Choose a track from search results", id="track-artist")
                                yield Static("YouTube Music", id="track-album")
                                yield Static("Views: 0  Source: YouTube Music", id="track-meta")
                            with Vertical(id="now-side"):
                                yield Static("STOPPED\nVol: 70%", id="status-block")
                        with Vertical(id="progress-wrap"):
                            yield BlockProgressBar(id="progress")
                            with Horizontal():
                                yield Static("00:00 / 00:00", id="progress-meta")
                                yield Static("0%", id="progress-percent")
                    with Vertical(id="next-panel", classes="panel"):
                        yield ListView(id="next")
                    with Vertical(id="cava-panel"):
                        yield CavaPanel(id="cava")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#search-panel", Container).border_title = " Search "
        self.query_one("#results-panel", Vertical).border_title = " Search Results "
        self.query_one("#next-panel", Vertical).border_title = " Next "
        self.query_one("#cava-panel", Vertical).border_title = " Visualizer "
        self.query_one("#now-playing", Vertical).border_title = "[white] Nothing Playing [/white]"
        self.query_one("#cava", CavaPanel).bars = self.render_cava_bars([0] * 24)
        self.query_one(Input).focus()
        self.set_interval(1.0, self.refresh_playback)
        self.start_cava()

    def on_unmount(self) -> None:
        self.stop_cava()
        self.player.stop()

    def populate_list(self, target: str, tracks: list[Track]) -> None:
        list_view = self.query_one(f"#{target}", ListView)
        list_view.clear()
        self.active_items.pop(target, None)
        for index, track in enumerate(tracks, start=1):
            list_view.append(TrackListItem(track, index))
        if tracks:
            list_view.index = 0

    def set_active_item(self, target: str, item: TrackListItem | None) -> None:
        previous = self.active_items.get(target)
        if previous is not None and not previous.is_mounted:
            previous = None
        if previous is not None:
            previous.remove_class("active-item")
        if item is not None:
            item.add_class("active-item")
            self.active_items[target] = item
        else:
            self.active_items.pop(target, None)

    @on(Input.Submitted, "#search-box")
    def handle_search(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return
        self.playback_status = "SEARCHING"
        self.query_one("#now-playing", Vertical).border_title = f"[white] Searching: {query} [/white]"
        self.search_tracks(query)

    @work(thread=True, exclusive=True)
    def search_tracks(self, query: str) -> None:
        try:
            tracks = self.client.search(query, limit=12)
        except Exception as error:
            self.call_from_thread(self.notify, f"Search failed: {error}", severity="error")
            self.call_from_thread(self.set_status_message, "SEARCH FAILED")
            return
        self.call_from_thread(self.update_search_results, tracks, query)

    def update_search_results(self, tracks: list[Track], query: str) -> None:
        self.results = tracks
        self.populate_list("results", tracks)
        self.set_status_message(f"{len(tracks)} RESULTS FOR {query.upper()}")
        if tracks:
            self.query_one("#results", ListView).focus()

    @on(ListView.Highlighted, "#results")
    def highlight_result(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, TrackListItem):
            self.set_active_item("results", event.item)

    @on(ListView.Highlighted, "#next")
    def highlight_next(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, TrackListItem):
            self.set_active_item("next", event.item)

    @on(ListView.Selected, "#results")
    def select_result(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TrackListItem):
            self.start_playback(event.item.track)

    @on(ListView.Selected, "#next")
    def select_next(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TrackListItem):
            self.start_playback(event.item.track)

    def action_focus_search(self) -> None:
        self.query_one(Input).focus()

    def action_focus_results(self) -> None:
        self.query_one("#results", ListView).focus()

    def action_play_selected(self) -> None:
        focused = self.focused
        if isinstance(focused, ListView):
            item = focused.highlighted_child
            if isinstance(item, TrackListItem):
                self.start_playback(item.track)
                return
        item = self.query_one("#results", ListView).highlighted_child
        if isinstance(item, TrackListItem):
            self.start_playback(item.track)

    def action_toggle_pause(self) -> None:
        try:
            self.player.toggle_pause()
        except Exception:
            return
        self.refresh_playback()

    def action_seek_back(self) -> None:
        self._safe_player_action(lambda: self.player.seek(-10))

    def action_seek_forward(self) -> None:
        self._safe_player_action(lambda: self.player.seek(10))

    def action_volume_up(self) -> None:
        self.volume = min(self.volume + 5, 130)
        self._safe_player_action(lambda: self.player.set_volume(self.volume))

    def action_volume_down(self) -> None:
        self.volume = max(self.volume - 5, 0)
        self._safe_player_action(lambda: self.player.set_volume(self.volume))

    def action_play_next(self) -> None:
        if self.next_tracks:
            self.play_next_track()

    def action_play_previous(self) -> None:
        if self.history_index > 0:
            self.history_index -= 1
            self.start_playback(self.history[self.history_index], remember=False)

    def _safe_player_action(self, callback: Any) -> None:
        try:
            callback()
        except Exception:
            pass
        self.refresh_playback()

    def set_status_message(self, message: str) -> None:
        self.query_one("#status-block", Static).update(f"{message}\nVol: {self.volume}%")

    def render_cava_bars(self, values: list[int], max_height: int = 4) -> str:
        if not values:
            values = [0] * 24
        glyphs = " ▁▂▃▄▅▆▇█"
        rows: list[str] = []
        scaled = [max(0, min(max_height * 8, round((value / 1000) * max_height * 8))) for value in values]
        for row in range(max_height, 0, -1):
            parts: list[str] = []
            row_base = (row - 1) * 8
            for value in scaled:
                level = max(0, min(8, value - row_base))
                parts.append(glyphs[level])
            rows.append("".join(parts))
        return "\n".join(rows)

    def update_cava(self, values: list[int]) -> None:
        self.query_one("#cava", CavaPanel).bars = self.render_cava_bars(values)

    def stop_cava(self) -> None:
        if self.cava_process and self.cava_process.poll() is None:
            self.cava_process.terminate()
            try:
                self.cava_process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.cava_process.kill()
        self.cava_process = None
        if self.cava_config_path and self.cava_config_path.exists():
            self.cava_config_path.unlink()
        self.cava_config_path = None

    def build_cava_config(self, method: str) -> Path:
        config = "\n".join(
            [
                "[general]",
                "framerate = 30",
                "bars = 24",
                "bar_width = 2",
                "bar_spacing = 1",
                "autosens = 1",
                "",
                "[input]",
                f"method = {method}",
                "source = auto",
                "",
                "[output]",
                "method = raw",
                "data_format = ascii",
                "ascii_max_range = 1000",
                "bar_delimiter = 59",
                "frame_delimiter = 10",
                "channels = mono",
                "",
                "[color]",
                "foreground = white",
                "background = black",
            ]
        )
        path = Path(tempfile.gettempdir()) / f"yt-tui-cava-{os.getpid()}.conf"
        path.write_text(config)
        return path

    @work(thread=True)
    def start_cava(self) -> None:
        try:
            for method in ("pipewire", "pulse"):
                self.cava_config_path = self.build_cava_config(method)
                self.cava_process = subprocess.Popen(
                    ["cava", "-p", str(self.cava_config_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
                if self.cava_process.stdout is None:
                    continue
                for line in self.cava_process.stdout:
                    frame = line.strip()
                    if not frame:
                        continue
                    try:
                        values = [int(part) for part in frame.split(";") if part]
                    except ValueError:
                        continue
                    self.call_from_thread(self.update_cava, values)
                if self.cava_process.poll() is None:
                    break
        except Exception:
            self.call_from_thread(self.update_cava, [0] * 24)

    def show_now_playing(self, track: Track, art: str) -> None:
        self.current_track = track
        self.query_one("#now-playing", Vertical).border_title = f"[white] {track.title} [/white]"
        self.query_one("#track-artist", Static).update(track.display_artist)
        self.query_one("#track-album", Static).update(track.album or "YouTube Music")
        self.query_one("#track-meta", Static).update(
            f"Views: {track.views or 0}"
        )
        art_panel = self.query_one("#art", ArtPanel)
        art_panel.art = art
        self.set_status_message("READY")

    def build_next_tracks(self, track: Track, queued_remainder: list[Track] | None = None) -> list[Track]:
        recommendations = self.client.recommendations_for(track, limit=10)
        seen_ids = {track.video_id, *(entry.video_id for entry in self.history)}
        base_next = queued_remainder or []
        return dedupe_tracks(base_next + recommendations, seen_ids, limit=8)

    @work(thread=True, exclusive=True)
    def start_playback(
        self,
        track: Track,
        remember: bool = True,
        queued_remainder: list[Track] | None = None,
    ) -> None:
        self.loading_playback = True
        try:
            detailed = self.client.enrich(track)
            detailed.audio_url = self.client.resolve_audio_url(detailed)
            art = render_thumbnail_ascii(detailed.thumbnail_url)
            next_tracks = self.build_next_tracks(detailed, queued_remainder)
            self.player.start(detailed.audio_url)
            self.player.set_volume(self.volume)
        except Exception as error:
            self.loading_playback = False
            self.call_from_thread(self.notify, f"Playback failed: {error}", severity="error")
            return
        self.call_from_thread(
            self.finish_playback_start,
            detailed,
            art,
            next_tracks,
            remember,
        )

    def finish_playback_start(
        self,
        track: Track,
        art: str,
        next_tracks: list[Track],
        remember: bool,
    ) -> None:
        if remember:
            if self.history_index < len(self.history) - 1:
                self.history = self.history[: self.history_index + 1]
            self.history.append(track)
            self.history_index = len(self.history) - 1
        self.current_track = track
        self.next_tracks = next_tracks
        self.preloading_next = False
        self.preloaded_next = None
        self.show_now_playing(track, art)
        self.populate_list("next", next_tracks)
        self.playback_status = "PLAYING"
        self.loading_playback = False
        self.refresh_playback()

    @work(thread=True)
    def preload_next_track(self, track: Track, queued_remainder: list[Track], current_video_id: str) -> None:
        try:
            detailed = self.client.enrich(track)
            detailed.audio_url = self.client.resolve_audio_url(detailed)
        except Exception:
            self.call_from_thread(self.finish_preload_next, None, current_video_id)
            return
        self.call_from_thread(
            self.finish_preload_next,
            (detailed, detailed.audio_url, queued_remainder),
            current_video_id,
        )

    def finish_preload_next(
        self,
        payload: tuple[Track, str, list[Track]] | None,
        current_video_id: str,
    ) -> None:
        self.preloading_next = False
        if not self.current_track or self.current_track.video_id != current_video_id:
            return
        if payload is None or not self.next_tracks:
            return
        if self.next_tracks[0].video_id != payload[0].video_id:
            return
        self.preloaded_next = payload

    def play_next_track(self) -> None:
        next_track = self.next_tracks[0]
        remaining = self.next_tracks[1:]
        if self.preloaded_next and self.preloaded_next[0].video_id == next_track.video_id:
            detailed, audio_url, queued_remainder = self.preloaded_next
            self.start_playback_from_preload(detailed, audio_url, queued_remainder)
            return
        self.start_playback(next_track, queued_remainder=remaining)

    @work(thread=True, exclusive=True)
    def start_playback_from_preload(
        self,
        track: Track,
        audio_url: str,
        queued_remainder: list[Track],
    ) -> None:
        self.loading_playback = True
        try:
            art = render_thumbnail_ascii(track.thumbnail_url)
            next_tracks = self.build_next_tracks(track, queued_remainder)
            self.player.start(audio_url)
            self.player.set_volume(self.volume)
        except Exception as error:
            self.loading_playback = False
            self.call_from_thread(self.notify, f"Playback failed: {error}", severity="error")
            return
        self.call_from_thread(
            self.finish_playback_start,
            track,
            art,
            next_tracks,
            True,
        )

    def refresh_playback(self) -> None:
        try:
            status = self.player.status()
        except Exception:
            return
        track_finished = (not status["running"]) or bool(status.get("eof")) or bool(status.get("idle"))
        if track_finished:
            if self.loading_playback:
                return
            if self.playback_status in {"PLAYING", "PAUSED"}:
                if self.next_tracks:
                    self.set_status_message("LOADING NEXT")
                    self.play_next_track()
                else:
                    self.playback_status = "STOPPED"
                    self.set_status_message("NEXT ENDED")
            return
        paused = status["pause"]
        self.volume = int(status["volume"])
        now = int(status["time_pos"])
        total = int(status["duration"])
        remaining = max(total - now, 0)
        self.playback_status = "PAUSED" if paused else "PLAYING"
        progress = self.query_one("#progress", BlockProgressBar)
        progress.progress = (now / total) if total > 0 else 0.0
        self.query_one("#progress-meta", Static).update(f"{self._format_time(now)} / {self._format_time(total)}")
        percent = int((now / total) * 100) if total > 0 else 0
        self.query_one("#progress-percent", Static).update(f"{percent}%")
        self.set_status_message(self.playback_status)
        if (
            not paused
            and total > 0
            and remaining <= 15
            and self.next_tracks
            and not self.preloading_next
            and (
                self.preloaded_next is None
                or self.preloaded_next[0].video_id != self.next_tracks[0].video_id
            )
            and self.current_track is not None
        ):
            self.preloading_next = True
            self.preload_next_track(self.next_tracks[0], self.next_tracks[1:], self.current_track.video_id)

    def _format_time(self, seconds: int) -> str:
        minutes, seconds = divmod(max(seconds, 0), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"


def main() -> None:
    PlayerApp().run()
