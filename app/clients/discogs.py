import re
from typing import Any

import httpx

from app.core.config import get_settings
from app.models.release import Release, TrackListing

_BASE_URL: str = "https://api.discogs.com"
_USER_AGENT: str = "MusicDownloaderAgent/0.1"
_ARTIST_DISAMBIGUATION_RE: re.Pattern[str] = re.compile(r"\s*\(\d+\)$")


class DiscogsAPIError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Discogs API error {status_code}: {message}")
        self.status_code = status_code


def _parse_duration(duration_string: str) -> float | None:
    if not duration_string.strip():
        return None
    parts = duration_string.split(":")
    try:
        if len(parts) == 2:
            return float(int(parts[0]) * 60 + int(parts[1]))
        if len(parts) == 3:
            return float(int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]))
    except ValueError:
        return None
    return None


class DiscogsClient:
    def __init__(self, api_token: str) -> None:
        self._http_client = httpx.Client(
            base_url=_BASE_URL,
            headers={
                "Authorization": f"Discogs token={api_token}",
                "User-Agent": _USER_AGENT,
            },
        )

    def fetch_release(
        self,
        artist: str,
        title: str,
        edition: str | None = None,
    ) -> Release:
        search_params: dict[str, str] = {
            "artist": artist,
            "release_title": title,
            "type": "release",
        }
        if edition is not None:
            search_params["q"] = edition

        try:
            search_response = self._http_client.get(
                "/database/search", params=search_params
            )
            search_response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DiscogsAPIError(exc.response.status_code, str(exc)) from exc

        results: list[dict[str, Any]] = search_response.json().get("results", [])
        if not results:
            raise DiscogsAPIError(404, "No results found")

        best_result = min(
            results, key=lambda r: int(r.get("year")) if r.get("year") else 9999
        )

        try:
            release_response = self._http_client.get(f"/releases/{best_result['id']}")
            release_response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DiscogsAPIError(exc.response.status_code, str(exc)) from exc

        return self._map_release(release_response.json())

    def _map_release(self, release_data: dict[str, Any]) -> Release:
        artist_names = [
            _ARTIST_DISAMBIGUATION_RE.sub("", artist_entry["name"])
            for artist_entry in release_data.get("artists", [])
        ]
        artist = " & ".join(artist_names)

        images = release_data.get("images", [])
        primary_image = next(
            (image for image in images if image.get("type") == "primary"), None
        )
        artwork_url: str | None
        if primary_image is not None:
            artwork_url = primary_image.get("uri")
        elif images:
            artwork_url = images[0].get("uri")
        else:
            artwork_url = None

        tracklist = [
            TrackListing(
                position=entry.get("position", ""),
                title=entry.get("title", ""),
                duration_seconds=_parse_duration(entry.get("duration", "")),
            )
            for entry in release_data.get("tracklist", [])
            if entry.get("type_") != "heading"
        ]

        return Release(
            discogs_id=release_data["id"],
            artist=artist,
            album_title=release_data.get("title", ""),
            year=release_data.get("year", 0),
            genres=release_data.get("genres", []),
            artwork_url=artwork_url,
            tracklist=tracklist,
        )


def get_discogs_client() -> DiscogsClient:
    return DiscogsClient(api_token=get_settings().discogs_api_token)
