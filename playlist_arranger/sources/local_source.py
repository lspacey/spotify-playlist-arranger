"""Local file playlist support: scanning folders, M3U handling, playback."""

import hashlib
import pathlib
import subprocess
import sys
import os

try:
    import mutagen
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3

    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False


def make_track_id(file_path: pathlib.Path) -> str:
    """Compute SHA1 of the first 512 KB of file bytes as stable track ID."""
    sha1 = hashlib.sha1()
    with open(file_path, "rb") as f:
        sha1.update(f.read(512 * 1024))
    return sha1.hexdigest()


def make_playlist_id(folder: pathlib.Path) -> str:
    """SHA1 of the canonical absolute folder path string (UTF-8 encoded)."""
    return hashlib.sha1(
        str(folder.resolve()).encode("utf-8")
    ).hexdigest()


def _read_tags(file_path: pathlib.Path) -> dict:
    """Read ID3/FLAC tags from an audio file using mutagen."""
    name = file_path.stem
    artist = ""
    album = ""
    duration_ms = 0

    if not HAS_MUTAGEN:
        return {
            "name": name,
            "artist": artist,
            "album": album,
            "duration_ms": duration_ms,
        }

    try:
        ext = file_path.suffix.lower()
        audio = mutagen.File(str(file_path))
        if audio is None:
            return {
                "name": name,
                "artist": artist,
                "album": album,
                "duration_ms": duration_ms,
            }

        # Get duration
        if hasattr(audio, "info") and hasattr(audio.info, "length"):
            duration_ms = int(audio.info.length * 1000)

        # Get tags
        if ext == ".mp3":
            tags = audio.tags if hasattr(audio, "tags") and audio.tags else {}
            if isinstance(tags, dict):
                name = str(tags.get("TIT2", name))
                artist = str(tags.get("TPE1", ""))
                album = str(tags.get("TALB", ""))
        elif ext == ".flac":
            name = str(audio.get("title", name))
            artist = str(audio.get("artist", ""))
            album = str(audio.get("album", ""))
    except Exception:
        pass

    return {
        "name": str(name),
        "artist": str(artist),
        "album": str(album),
        "duration_ms": duration_ms,
    }


def scan_folder(folder: pathlib.Path) -> list[dict]:
    """
    Scan a folder for .mp3 and .flac files.
    Returns list of track dicts: {id, name, artist, album, duration_ms, file_path}
    """
    folder = pathlib.Path(folder).resolve()
    if not folder.is_dir():
        return []

    tracks = []
    extensions = {".mp3", ".flac"}
    for file_path in sorted(folder.iterdir()):
        if file_path.suffix.lower() not in extensions:
            continue
        tid = make_track_id(file_path)
        tags = _read_tags(file_path)
        track = {
            "id": tid,
            "name": tags["name"],
            "artist": tags["artist"],
            "album": tags["album"],
            "duration_ms": tags["duration_ms"],
            "file_path": str(file_path),
        }
        tracks.append(track)

    return tracks


def load_m3u(m3u_path: pathlib.Path) -> list[pathlib.Path]:
    """
    Parse M3U/M3U8 file, return list of resolved file paths.
    """
    m3u_path = pathlib.Path(m3u_path).resolve()
    if not m3u_path.exists():
        return []

    base_dir = m3u_path.parent
    paths = []
    with open(m3u_path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = pathlib.Path(line)
            if not p.is_absolute():
                p = base_dir / p
            p = p.resolve()
            if p.exists():
                paths.append(p)

    return paths


def save_m3u(m3u_path: pathlib.Path, file_paths: list[pathlib.Path]) -> None:
    """
    Write M3U8 playlist (UTF-8, #EXTM3U header, #EXTINF lines with
    duration and title from tags).
    """
    m3u_path = pathlib.Path(m3u_path)
    lines = ["#EXTM3U"]

    for fp in file_paths:
        fp = pathlib.Path(fp)
        tags = _read_tags(fp)
        dur_sec = tags["duration_ms"] / 1000 if tags["duration_ms"] else -1
        title = f"{tags['artist']} - {tags['name']}" if tags["artist"] else tags["name"]
        lines.append(f"#EXTINF:{dur_sec:.0f},{title}")
        # Write relative path if in same directory
        try:
            rel = fp.relative_to(m3u_path.parent)
            lines.append(str(rel))
        except ValueError:
            lines.append(str(fp))

    m3u_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def play_local_file(file_path: pathlib.Path) -> None:
    """
    Play a local audio file using the default system player.
    Windows: os.startfile, macOS: open, Linux: xdg-open
    """
    file_path = pathlib.Path(file_path)
    if sys.platform == "win32":
        os.startfile(str(file_path))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(file_path)])
    else:
        subprocess.run(["xdg-open", str(file_path)])