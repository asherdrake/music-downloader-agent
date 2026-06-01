from app.clients.discogs import DiscogsClient
from app.core.config import get_settings
from app.models.release import Release


def _format_duration(duration_seconds: float | None) -> str:
    if duration_seconds is None:
        return "?:??"
    total_seconds = int(duration_seconds)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d}"


def print_release(release: Release) -> None:
    print(f"Discogs ID:  {release.discogs_id}")
    print(f"Artist:      {release.artist}")
    print(f"Album:       {release.album_title}")
    print(f"Year:        {release.year}")
    print(f"Genres:      {', '.join(release.genres) or '(none)'}")
    print(f"Artwork URL: {release.artwork_url or '(none)'}")
    print(f"\nTracklist ({len(release.tracklist)} tracks):")
    for track in release.tracklist:
        position = track.position or "?"
        duration = _format_duration(track.duration_seconds)
        print(f"  {position:>4}  {track.title}  [{duration}]")


settings = get_settings()
client = DiscogsClient(api_token=settings.discogs_api_token)
release = client.fetch_release(artist="tricot", title="makkuro")
print_release(release)
