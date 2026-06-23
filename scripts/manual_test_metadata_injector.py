"""Manual test for the Metadata Injector slice.

Runs the metadata half of the pipeline end-to-end against the real LLM and
Discogs API: parse_request -> search_candidates (native-script harvesting
only, no download) -> fetch_release -> fetch_artwork -> inject_metadata, then
reads the tags back off disk with mutagen so you can eyeball what was written.

Files are written under the configured local_files_directory, mirroring the
downloader layout. Any track that isn't already on disk is created as a
1-second silent (but valid) .mp3 via ffmpeg so the OS / Spotify will read the
tags; existing audio is tagged in place and never overwritten. Needs ffmpeg on
PATH plus ANTHROPIC_API_KEY and DISCOGS_API_TOKEN.
"""

import re
import subprocess
import sys
from pathlib import Path

from mutagen.id3 import ID3
from mutagen.mp4 import MP4

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.clients.discogs import DiscogsAPIError, DiscogsClient
from app.core.config import get_settings
from app.core.llm import get_llm
from app.core.native_script import gather_native_variants
from app.models.download_intent import DownloadIntent
from app.models.download_result import DownloadResult, TrackResult
from app.models.release import Release
from app.pipeline.candidate_search import search_candidates
from app.pipeline.metadata_injector import fetch_artwork, inject_metadata
from app.pipeline.request_parser import parse_request

# ---------------------------------------------------------------------------
# Inputs — edit these to try other releases.
# ---------------------------------------------------------------------------

REQUEST = (
    "Download the album Ten no Mikaku by Yuu, create a playlist for it, "
    "set the image to the album art, and add the correct tracks in album order."
)

_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|]')


def _sanitize(name: str) -> str:
    return _UNSAFE_CHARS.sub("_", name).strip()


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _format_duration(duration_seconds: float | None) -> str:
    if duration_seconds is None:
        return "?:??"
    minutes, seconds = divmod(int(duration_seconds), 60)
    return f"{minutes}:{seconds:02d}"


def _section(title: str) -> None:
    print("=" * 60)
    print(title)
    print("=" * 60)


def print_download_intent(intent: DownloadIntent) -> None:
    _section("Download intent")
    print(f"  Target:        {intent.target_type}")
    print(f"  Artist:        {intent.artist}")
    print(f"  Title:         {intent.title}")
    print(f"  Edition:       {intent.edition or '(none)'}")
    print(f"  Artist native: {intent.artist_native_variants or '(none)'}")
    print(f"  Title native:  {intent.title_native_variants or '(none)'}")
    print()


def print_native_variants(
    candidate_titles: list[str],
    artist_variants: list[str],
    title_variants: list[str],
) -> None:
    _section("Native-script variants (harvest + kana + LLM)")
    print(f"  From {len(candidate_titles)} search candidates:")
    for title in candidate_titles:
        print(f"    {title!r}")
    print(f"  Artist variants: {artist_variants or '(none)'}")
    print(f"  Title variants:  {title_variants or '(none)'}")
    print()


def print_release(release: Release) -> None:
    _section("Resolved Discogs release")
    print(f"  Discogs ID:  {release.discogs_id}")
    print(f"  Artist:      {release.artist}")
    print(f"  Album:       {release.album_title}")
    print(f"  Year:        {release.year}")
    print(f"  Genres:      {', '.join(release.genres) or '(none)'}")
    print(f"  Artwork URL: {release.artwork_url or '(none)'}")
    print(f"  Tracklist ({len(release.tracklist)} tracks):")
    for track in release.tracklist:
        position = track.position or "?"
        duration = _format_duration(track.duration_seconds)
        print(f"    {position:>4}  {track.title}  [{duration}]")
    print()


def _read_mp3_tags(path: Path) -> dict[str, str]:
    # translate=False keeps the raw v2.3 TYER frame (mutagen upgrades it to
    # TDRC on read otherwise).
    tags = ID3(path, translate=False)

    def field(frame: str) -> str:
        return str(tags[frame].text[0]) if frame in tags else "(missing)"

    artwork = tags.getall("APIC")
    return {
        "title": field("TIT2"),
        "artist": field("TPE1"),
        "album": field("TALB"),
        "genre": field("TCON"),
        "track": field("TRCK"),
        "disc": field("TPOS"),
        "year": field("TYER"),
        "version": ".".join(str(part) for part in tags.version),
        "artwork": f"{len(artwork[0].data)} bytes" if artwork else "(none)",
    }


def _read_mp4_tags(path: Path) -> dict[str, str]:
    audio = MP4(path)

    def field(atom: str) -> str:
        return str(audio[atom][0]) if atom in audio else "(missing)"

    covers = audio.get("covr")
    return {
        "title": field("\xa9nam"),
        "artist": field("\xa9ART"),
        "album": field("\xa9alb"),
        "genre": field("\xa9gen"),
        "track": field("trkn"),
        "disc": field("disk"),
        "year": field("\xa9day"),
        "version": "mp4",
        "artwork": f"{len(bytes(covers[0]))} bytes" if covers else "(none)",
    }


def print_injected_tags(result: DownloadResult) -> None:
    _section("Injected tags (read back from disk)")
    for track in result.tracks:
        reader = (
            _read_mp4_tags if track.path.suffix.lower() == ".m4a" else _read_mp3_tags
        )
        tags = reader(track.path)
        print(f"  [{track.track_number}] {track.path.name}")
        print(f"      title={tags['title']!r}  artist={tags['artist']!r}")
        print(f"      album={tags['album']!r}  genre={tags['genre']!r}")
        print(
            f"      track={tags['track']}  disc={tags['disc']}  "
            f"year={tags['year']}  id3={tags['version']}"
        )
        print(f"      artwork={tags['artwork']}")
    print()


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _write_silent_mp3(path: Path) -> None:
    """Write a 1-second silent but VALID mp3 via ffmpeg.

    An empty/tag-only file is not a real mp3, so Windows Explorer and Spotify
    refuse to read its tags. A genuine (if silent) mp3 is indexed normally.
    """
    subprocess.run(
        [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            "1",
            "-c:a",
            "libmp3lame",
            "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _build_download_result(
    release: Release, intent: DownloadIntent, base_dir: Path
) -> DownloadResult:
    """Map the release tracklist onto files in the real downloads directory.

    Uses the downloader's <Artist>/<Album>/<NN> - <title>.mp3 layout. Tracks
    already on disk are tagged in place; missing ones are created as 1-second
    silent (but valid) mp3s so the OS / Spotify will actually read the tags.
    """
    album_dir = base_dir / _sanitize(intent.artist) / _sanitize(intent.title)
    album_dir.mkdir(parents=True, exist_ok=True)

    tracks: list[TrackResult] = []
    for index, listing in enumerate(release.tracklist, start=1):
        safe_title = _sanitize(listing.title) or f"track-{index}"
        path = album_dir / f"{index:02d} - {safe_title}.mp3"
        if not path.exists():
            _write_silent_mp3(path)
        tracks.append(
            TrackResult(
                track_number=index,
                title=listing.title,
                path=path,
                skipped=False,
            )
        )
    return DownloadResult(
        url="(manual-test)",
        use_m4a=False,
        tracks=tracks,
        downloaded_count=len(tracks),
        skipped_count=0,
    )


def main() -> None:
    settings = get_settings()

    intent = parse_request(REQUEST, get_llm())
    print_download_intent(intent)

    # Lightweight search just to harvest native-script titles (no download). A
    # romanized query on YouTube still surfaces original-script titles, which
    # are the most reliable native-variant source for the Discogs lookup.
    candidate_titles = [candidate.title for candidate in search_candidates(intent)]
    artist_variants = gather_native_variants(
        intent.artist, candidate_titles, intent.artist_native_variants
    )
    title_variants = gather_native_variants(
        intent.title, candidate_titles, intent.title_native_variants
    )
    print_native_variants(candidate_titles, artist_variants, title_variants)

    client = DiscogsClient(api_token=settings.discogs_api_token)
    try:
        release = client.fetch_release(
            artist=intent.artist,
            title=intent.title,
            edition=intent.edition,
            artist_native_variants=artist_variants,
            title_native_variants=title_variants,
        )
    except DiscogsAPIError as error:
        _section("No Discogs match")
        print(f"  {error}")
        print("  -> pipeline would suspend for manual review (Slice 15).")
        return

    print_release(release)

    artwork_bytes = fetch_artwork(release.artwork_url)
    _section("Artwork")
    if artwork_bytes is None:
        print("  (no artwork fetched)")
    else:
        print(f"  Downloaded {len(artwork_bytes)} bytes from {release.artwork_url}")
    print()

    result = _build_download_result(release, intent, settings.local_files_directory)
    inject_metadata(result, release, artwork_bytes)
    print_injected_tags(result)


if __name__ == "__main__":
    main()
