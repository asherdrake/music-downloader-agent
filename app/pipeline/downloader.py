import re
from pathlib import Path

import yt_dlp

from app.models.download_intent import DownloadIntent
from app.models.download_result import DownloadResult, TrackResult

_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|]')


def _sanitize(name: str) -> str:
    return _UNSAFE_CHARS.sub("_", name).strip()


def _expected_path(
    base: Path,
    artist: str,
    album: str,
    track_number: int,
    title: str,
    ext: str,
) -> Path:
    filename = f"{track_number:02d} - {_sanitize(title)}.{ext}"
    return base / _sanitize(artist) / _sanitize(album) / filename


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


def run_download(
    url: str,
    intent: DownloadIntent,
    local_files_directory: Path,
    use_m4a: bool = False,
) -> DownloadResult:
    if intent.target_type == "Track":
        return download_track(url, intent, local_files_directory, use_m4a)
    return download_playlist(url, intent, local_files_directory, use_m4a)
