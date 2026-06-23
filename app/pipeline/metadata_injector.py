from pathlib import Path

import httpx
from mutagen.id3 import APIC, ID3, TALB, TCON, TIT2, TPE1, TPOS, TRCK, TYER
from mutagen.mp4 import MP4, MP4Cover

from app.models.download_result import DownloadResult, TrackResult
from app.models.release import Release

_USER_AGENT: str = "MusicDownloaderAgent/0.1"
_COVER_MIME: str = "image/jpeg"


def fetch_artwork(artwork_url: str | None) -> bytes | None:
    if artwork_url is None:
        return None
    try:
        response = httpx.get(
            artwork_url,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return response.content


def _write_mp3_tags(
    path: Path,
    *,
    title: str,
    artist: str,
    album: str,
    track_number: int,
    total_tracks: int,
    disc_number: int,
    total_discs: int,
    year: int,
    genre: str,
    artwork_bytes: bytes | None,
) -> None:
    try:
        tags = ID3(path)
    except Exception:
        tags = ID3()

    tags.setall("TIT2", [TIT2(encoding=3, text=title)])
    tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
    tags.setall("TALB", [TALB(encoding=3, text=album)])
    tags.setall("TCON", [TCON(encoding=3, text=genre)])
    tags.setall("TRCK", [TRCK(encoding=3, text=f"{track_number}/{total_tracks}")])
    tags.setall("TPOS", [TPOS(encoding=3, text=f"{disc_number}/{total_discs}")])
    tags.setall("TYER", [TYER(encoding=3, text=str(year))])

    tags.delall("APIC")
    if artwork_bytes is not None:
        tags.add(
            APIC(
                encoding=3,
                mime=_COVER_MIME,
                type=3,
                desc="Cover (front)",
                data=artwork_bytes,
            )
        )

    tags.save(path, v2_version=3)


def _write_mp4_tags(
    path: Path,
    *,
    title: str,
    artist: str,
    album: str,
    track_number: int,
    total_tracks: int,
    disc_number: int,
    total_discs: int,
    year: int,
    genre: str,
    artwork_bytes: bytes | None,
) -> None:
    audio = MP4(path)

    audio["\xa9nam"] = [title]
    audio["\xa9ART"] = [artist]
    audio["\xa9alb"] = [album]
    audio["\xa9gen"] = [genre]
    audio["\xa9day"] = [str(year)]
    audio["trkn"] = [(track_number, total_tracks)]
    audio["disk"] = [(disc_number, total_discs)]

    if "covr" in audio:
        del audio["covr"]
    if artwork_bytes is not None:
        audio["covr"] = [MP4Cover(artwork_bytes, imageformat=MP4Cover.FORMAT_JPEG)]

    audio.save()


def inject_track_metadata(
    track: TrackResult,
    release: Release,
    track_number: int,
    total_tracks: int,
    artwork_bytes: bytes | None,
) -> None:
    index = track_number - 1
    if 0 <= index < len(release.tracklist):
        title = release.tracklist[index].title
    else:
        title = track.title
    genre = release.genres[0] if release.genres else ""

    suffix = track.path.suffix.lower()
    if suffix == ".m4a":
        writer = _write_mp4_tags
    else:
        writer = _write_mp3_tags

    writer(
        track.path,
        title=title,
        artist=release.artist,
        album=release.album_title,
        track_number=track_number,
        total_tracks=total_tracks,
        disc_number=1,
        total_discs=1,
        year=release.year,
        genre=genre,
        artwork_bytes=artwork_bytes,
    )


def inject_metadata(
    download_result: DownloadResult,
    release: Release,
    artwork_bytes: bytes | None,
) -> DownloadResult:
    total_tracks = len(release.tracklist) or len(download_result.tracks)
    for index, track in enumerate(download_result.tracks):
        inject_track_metadata(
            track,
            release,
            track_number=index + 1,
            total_tracks=total_tracks,
            artwork_bytes=artwork_bytes,
        )
    return download_result
