import re
from typing import Any

import httpx
from rapidfuzz import fuzz

from app.core.config import get_settings
from app.core.romanize import romanize
from app.models.release import Release, TrackListing

_BASE_URL: str = "https://api.discogs.com"
_USER_AGENT: str = "MusicDownloaderAgent/0.1"
_ARTIST_DISAMBIGUATION_RE: re.Pattern[str] = re.compile(r"\s*\(\d+\)$")
_NON_ALNUM_RE: re.Pattern[str] = re.compile(r"[^a-z0-9]+")

# Minimum romanized-similarity (0-100) for a search result to be accepted as a
# real match. Below this we treat the query as unresolved (404) rather than
# returning a confidently-wrong release — native-script searches readily return
# unrelated rows, so a floor here is what keeps mis-tagging from happening.
_MATCH_THRESHOLD: float = 70.0

# Upper bound on search calls per fetch_release so a large native-variant pool
# can't exceed Discogs rate limits; the first above-threshold hit short-circuits
# long before this in the common case.
_MAX_SEARCH_ATTEMPTS: int = 24
# Native artist variants paired with each native title in targeted searches.
_MAX_PAIRED_ARTISTS: int = 3


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


def _build_search_attempts(
    artist: str,
    title: str,
    edition: str | None,
    artist_native_variants: list[str],
    title_native_variants: list[str],
) -> list[dict[str, str]]:
    """Ordered, de-duplicated search-parameter sets to try until one hits.

    The romanized (user-supplied) query is always tried first so behaviour is
    unchanged for Latin-script releases. Native-script variants follow, since
    catalogues like Discogs index many works only under their original script.
    """
    attempts: list[dict[str, str]] = []

    first: dict[str, str] = {
        "artist": artist,
        "release_title": title,
        "type": "release",
    }
    if edition is not None:
        first["q"] = edition
    attempts.append(first)

    artist_candidates = [*artist_native_variants[:_MAX_PAIRED_ARTISTS], artist]
    for native_title in title_native_variants:
        for native_artist in artist_candidates:
            attempts.append(
                {
                    "artist": native_artist,
                    "release_title": native_title,
                    "type": "release",
                }
            )
        attempts.append({"q": native_title, "type": "release"})

    unique_attempts: list[dict[str, str]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for params in attempts:
        key = tuple(sorted(params.items()))
        if key not in seen:
            seen.add(key)
            unique_attempts.append(params)
    return unique_attempts[:_MAX_SEARCH_ATTEMPTS]


def _normalize_compact(text: str) -> str:
    return _NON_ALNUM_RE.sub("", romanize(text).lower())


def _romanized_match_score(candidate_title: str, target: str) -> float:
    """Romanized similarity (0-100) between a result title and the request.

    Combines a token-aware score (good for word-separated romaji) with a
    whitespace/punctuation-stripped ratio. The compact form is what lets a pure
    hiragana title — which romanizes without word boundaries (てんのみかく ->
    "tennomikaku") — still match the spaced romaji request ("ten no mikaku").
    """
    spaced = fuzz.token_set_ratio(
        romanize(target).lower(), romanize(candidate_title).lower()
    )
    compact = fuzz.ratio(
        _normalize_compact(target), _normalize_compact(candidate_title)
    )
    return max(float(spaced), float(compact))


def _select_best_result(results: list[dict[str, Any]], target: str) -> dict[str, Any]:
    """Pick the result whose romanized title best matches the request.

    Ranks by romanized similarity to the request, breaking ties by earliest
    release year (original-release preference, per the metadata rules).
    """

    def _rank_key(result: dict[str, Any]) -> tuple[float, int]:
        match_score = _romanized_match_score(result.get("title", ""), target)
        year = int(result["year"]) if result.get("year") else 9999
        return (match_score, -year)

    return max(results, key=_rank_key)


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
        artist_native_variants: list[str] | None = None,
        title_native_variants: list[str] | None = None,
    ) -> Release:
        attempts = _build_search_attempts(
            artist,
            title,
            edition,
            artist_native_variants or [],
            title_native_variants or [],
        )

        target = f"{artist} {title}".strip()
        best_result: dict[str, Any] | None = None
        for search_params in attempts:
            found = self._search(search_params)
            if not found:
                continue
            candidate = _select_best_result(found, target)
            if _romanized_match_score(candidate.get("title", ""), target) >= (
                _MATCH_THRESHOLD
            ):
                best_result = candidate
                break

        if best_result is None:
            raise DiscogsAPIError(404, "No results found")

        try:
            release_response = self._http_client.get(f"/releases/{best_result['id']}")
            release_response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DiscogsAPIError(exc.response.status_code, str(exc)) from exc

        return self._map_release(release_response.json())

    def _search(self, search_params: dict[str, str]) -> list[dict[str, Any]]:
        try:
            search_response = self._http_client.get(
                "/database/search", params=search_params
            )
            search_response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DiscogsAPIError(exc.response.status_code, str(exc)) from exc
        return search_response.json().get("results", [])

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
