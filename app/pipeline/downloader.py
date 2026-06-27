import re
import subprocess
from pathlib import Path

import yt_dlp

from app.models.chapter_map import ChapterEntry
from app.models.download_intent import DownloadIntent
from app.models.download_result import DownloadResult, TrackResult

_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|]')


def _sanitize(name: str) -> str:
    return _UNSAFE_CHARS.sub("_", name).strip()


def _album_dir(base: Path, artist: str, album: str) -> Path:
    return base / _sanitize(artist) / _sanitize(album)


def _expected_path(
    base: Path,
    artist: str,
    album: str,
    track_number: int,
    title: str,
    ext: str,
) -> Path:
    filename = f"{track_number:02d} - {_sanitize(title)}.{ext}"
    return _album_dir(base, artist, album) / filename


def _audio_ydl_opts(output_template: str, use_m4a: bool) -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a" if use_m4a else "mp3",
                "preferredquality": "0",
            }
        ],
        "outtmpl": output_template,
    }


def download_track(
    url: str,
    intent: DownloadIntent,
    local_files_directory: Path,
    use_m4a: bool = False,
) -> DownloadResult:
    ext = "m4a" if use_m4a else "mp3"
    expected = _expected_path(
        local_files_directory, intent.artist, intent.title, 1, intent.title, ext
    )

    if expected.exists():
        return DownloadResult(
            url=url,
            use_m4a=use_m4a,
            tracks=[
                TrackResult(
                    track_number=1, title=intent.title, path=expected, skipped=True
                )
            ],
            downloaded_count=0,
            skipped_count=1,
        )

    expected.parent.mkdir(parents=True, exist_ok=True)
    output_template = str(expected.with_suffix("")) + ".%(ext)s"
    opts = _audio_ydl_opts(output_template, use_m4a)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    return DownloadResult(
        url=url,
        use_m4a=use_m4a,
        tracks=[
            TrackResult(
                track_number=1, title=intent.title, path=expected, skipped=False
            )
        ],
        downloaded_count=1,
        skipped_count=0,
    )


def download_playlist(
    url: str,
    intent: DownloadIntent,
    local_files_directory: Path,
    use_m4a: bool = False,
) -> DownloadResult:
    ext = "m4a" if use_m4a else "mp3"

    with yt_dlp.YoutubeDL(
        {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
        }
    ) as ydl:
        info = ydl.extract_info(url, download=False)

    entries = info.get("entries") or []
    tracks: list[TrackResult] = []

    for entry in entries:
        track_number = int(entry.get("playlist_index") or entry.get("index") or 0)
        track_title = entry.get("title") or ""
        track_url = entry.get("url") or f"https://www.youtube.com/watch?v={entry['id']}"

        expected = _expected_path(
            local_files_directory,
            intent.artist,
            intent.title,
            track_number,
            track_title,
            ext,
        )

        if expected.exists():
            tracks.append(
                TrackResult(
                    track_number=track_number,
                    title=track_title,
                    path=expected,
                    skipped=True,
                )
            )
            continue

        expected.parent.mkdir(parents=True, exist_ok=True)
        output_template = str(expected.with_suffix("")) + ".%(ext)s"
        opts = _audio_ydl_opts(output_template, use_m4a)
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([track_url])

        tracks.append(
            TrackResult(
                track_number=track_number,
                title=track_title,
                path=expected,
                skipped=False,
            )
        )

    skipped_count = sum(1 for t in tracks if t.skipped)
    return DownloadResult(
        url=url,
        use_m4a=use_m4a,
        tracks=tracks,
        downloaded_count=len(tracks) - skipped_count,
        skipped_count=skipped_count,
    )


def _split_chapter_with_ffmpeg(
    source_path: Path,
    destination_path: Path,
    start_seconds: float,
    end_seconds: float | None,
) -> None:
    """Cut one track out of the full compilation audio via ffmpeg.

    Uses stream copy (lossless, fast). ``-ss`` is placed before ``-i`` for an
    efficient input seek; the final chapter passes ``end_seconds=None`` so the
    cut runs to the end of the file without needing the total duration.
    """
    command = ["ffmpeg", "-y", "-ss", str(start_seconds)]
    if end_seconds is not None:
        command += ["-to", str(end_seconds)]
    command += ["-i", str(source_path), "-c", "copy", str(destination_path)]
    subprocess.run(command, check=True)


def download_compilation(
    url: str,
    intent: DownloadIntent,
    chapters: list[ChapterEntry],
    local_files_directory: Path,
    use_m4a: bool = False,
) -> DownloadResult:
    """Download a Compilation Source (one back-to-back video) and split it.

    The full audio is fetched once, then each resolved chapter is carved out
    into the standard ``<Artist>/<Album>/<track_number> - <title>`` layout.
    Existing Track files are skipped (duplicate handling), and the intermediate
    full-length file is removed once splitting completes.
    """
    ext = "m4a" if use_m4a else "mp3"
    album_directory = _album_dir(local_files_directory, intent.artist, intent.title)
    album_directory.mkdir(parents=True, exist_ok=True)

    tracks: list[TrackResult] = []
    pending_splits: list[tuple[TrackResult, float, float | None]] = []
    for index, chapter in enumerate(chapters):
        track_number = index + 1
        expected = _expected_path(
            local_files_directory,
            intent.artist,
            intent.title,
            track_number,
            chapter.track_name,
            ext,
        )
        if expected.exists():
            tracks.append(
                TrackResult(
                    track_number=track_number,
                    title=chapter.track_name,
                    path=expected,
                    skipped=True,
                )
            )
            continue

        end_seconds = (
            chapters[index + 1].start_seconds if index + 1 < len(chapters) else None
        )
        track_result = TrackResult(
            track_number=track_number,
            title=chapter.track_name,
            path=expected,
            skipped=False,
        )
        tracks.append(track_result)
        pending_splits.append((track_result, chapter.start_seconds, end_seconds))

    skipped_count = sum(1 for track in tracks if track.skipped)
    if not pending_splits:
        return DownloadResult(
            url=url,
            use_m4a=use_m4a,
            tracks=tracks,
            downloaded_count=0,
            skipped_count=skipped_count,
        )

    full_audio_path = album_directory / f"__compilation_full.{ext}"
    output_template = str(full_audio_path.with_suffix("")) + ".%(ext)s"
    opts = _audio_ydl_opts(output_template, use_m4a)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    try:
        for track_result, start_seconds, end_seconds in pending_splits:
            _split_chapter_with_ffmpeg(
                full_audio_path, track_result.path, start_seconds, end_seconds
            )
    finally:
        full_audio_path.unlink(missing_ok=True)

    return DownloadResult(
        url=url,
        use_m4a=use_m4a,
        tracks=tracks,
        downloaded_count=len(pending_splits),
        skipped_count=skipped_count,
    )


def run_download(
    url: str,
    intent: DownloadIntent,
    local_files_directory: Path,
    use_m4a: bool = False,
) -> DownloadResult:
    if intent.target_type == "Track":
        return download_track(url, intent, local_files_directory, use_m4a)
    return download_playlist(url, intent, local_files_directory, use_m4a)
