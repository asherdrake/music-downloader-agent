import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.core.config import get_settings
from app.models.download_intent import DownloadIntent
from app.models.download_result import DownloadResult, TrackResult
from app.pipeline.downloader import run_download


def _print_track_result(track: TrackResult, index: int) -> None:
    status = "SKIPPED" if track.skipped else "downloaded"
    print(f"    [{index}] {track.track_number:02d} - {track.title}  ({status})")
    print(f"        Path: {track.path}")


def print_download_result(result: DownloadResult) -> None:
    print("=" * 60)
    print("Download result")
    print("=" * 60)
    print(f"URL:      {result.url}")
    print(f"Format:   {'m4a' if result.use_m4a else 'mp3'}")
    print(f"Downloaded: {result.downloaded_count}  |  Skipped: {result.skipped_count}")
    print()

    print(f"Tracks ({len(result.tracks)}):")
    if result.tracks:
        for index, track in enumerate(result.tracks, start=1):
            _print_track_result(track, index)
    else:
        print("  (none)")
    print("=" * 60)


if __name__ == "__main__":
    settings = get_settings()

    track_intent = DownloadIntent(
        target_type="Track",
        artist="tricot",
        title="The Danger of Not Mixing",
    )

    result: DownloadResult = run_download(
        url="https://www.youtube.com/watch?v=WC3VuXS_4xA",
        intent=track_intent,
        local_files_directory=settings.local_files_directory,
    )

    print_download_result(result)

    album_intent = DownloadIntent(
        target_type="Album", artist="Yuu", title="Ten no Mikaku"
    )

    album_result: DownloadResult = run_download(
        url="https://www.youtube.com/playlist?list=PLk39OIjAKezW5bPC24bNqdC7Pqu_FpBBi",
        intent=album_intent,
        local_files_directory=settings.local_files_directory,
    )
