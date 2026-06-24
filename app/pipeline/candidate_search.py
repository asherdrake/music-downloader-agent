import urllib.parse
from typing import Any

import yt_dlp

from app.models.candidate import Candidate
from app.models.download_intent import DownloadIntent

_SEARCH_OPTS: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": True,
    "skip_download": True,
}

# Targets youtube.com/results filtered to Playlists type (sp= is YouTube's
# protobuf filter; EgIQAw== decodes to type=Playlist).
_PLAYLIST_SEARCH_OPTS: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": True,
    "skip_download": True,
    "playlist_items": "1-5",
}
_PLAYLIST_SEARCH_SP: str = "EgIQAw%3D%3D"

_PLAYLIST_FLAT_OPTS: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": True,
    "skip_download": True,
}

_URL_OPTS: dict[str, Any] = {
    "quiet": False,
    "no_warnings": False,
    "skip_download": True,
}

# YouTube auto-generated Mix playlists use these ID prefixes.
_MIX_ID_PREFIXES: tuple[str, ...] = ("RD", "RDCLAK", "UU")


def _is_playlist_entry(entry: dict[str, Any]) -> bool:
    ie_key = entry.get("ie_key") or ""
    url = entry.get("url") or ""
    return ie_key in ("YoutubeTab", "YoutubePlaylist") or "list=" in url


def _is_youtube_mix(entry: dict[str, Any]) -> bool:
    playlist_id = entry.get("id") or ""
    title = (entry.get("title") or "").strip().lower()
    if any(playlist_id.startswith(prefix) for prefix in _MIX_ID_PREFIXES):
        return True
    return title.endswith(" mix") or title.startswith("mix -")


def _fetch_playlist_total_duration(playlist_url: str) -> float:
    with yt_dlp.YoutubeDL(_PLAYLIST_FLAT_OPTS) as ydl:
        info: dict[str, Any] = ydl.extract_info(playlist_url, download=False) or {}
    entries: list[dict[str, Any]] = info.get("entries") or []
    return sum(float(e.get("duration") or 0.0) for e in entries if e)


def _build_candidate_from_entry(entry: dict[str, Any]) -> Candidate:
    url = entry.get("url") or f"https://www.youtube.com/watch?v={entry['id']}"
    return Candidate(
        url=url,
        title=entry.get("title") or "",
        duration_seconds=float(entry.get("duration") or 0.0),
        channel_name=entry.get("channel") or entry.get("uploader") or "",
        view_count=int(entry.get("view_count") or 0),
        is_playlist=False,
    )


def _build_playlist_candidate(entry: dict[str, Any]) -> Candidate:
    playlist_id = entry.get("id") or ""
    url = entry.get("url") or f"https://www.youtube.com/playlist?list={playlist_id}"
    duration_seconds = _fetch_playlist_total_duration(url)
    return Candidate(
        url=url,
        title=entry.get("title") or "",
        duration_seconds=duration_seconds,
        channel_name=entry.get("channel") or entry.get("uploader") or "",
        view_count=int(entry.get("view_count") or 0),
        is_playlist=True,
    )


def _search_youtube_playlists(query: str) -> list[dict[str, Any]]:
    encoded_query = urllib.parse.quote(query)
    url = f"https://www.youtube.com/results?search_query={encoded_query}&sp={_PLAYLIST_SEARCH_SP}"
    with yt_dlp.YoutubeDL(_PLAYLIST_SEARCH_OPTS) as ydl:
        results: dict[str, Any] = ydl.extract_info(url, download=False) or {}
    return [e for e in (results.get("entries") or []) if e]


def _fetch_candidate_for_url(url: str, target_type: str) -> Candidate:
    with yt_dlp.YoutubeDL(_url_opts_for_target_type(target_type)) as ydl:
        info: dict[str, Any] = ydl.extract_info(url, download=False)
    return Candidate(
        url=url,
        title=info.get("title") or "",
        duration_seconds=float(info.get("duration") or 0.0),
        channel_name=info.get("channel") or info.get("uploader") or "",
        view_count=int(info.get("view_count") or 0),
    )


def _url_opts_for_target_type(target_type: str) -> dict[str, Any]:
    opts = dict(_URL_OPTS)
    if target_type == "Track":
        opts["noplaylist"] = True
    return opts


def search_candidates(intent: DownloadIntent, max_results: int = 15) -> list[Candidate]:
    if intent.resource_hint is not None:
        return [_fetch_candidate_for_url(intent.resource_hint, intent.target_type)]

    query = f"{intent.artist} {intent.title}"
    if intent.edition:
        query = f"{query} {intent.edition}"

    with yt_dlp.YoutubeDL(_SEARCH_OPTS) as ydl:
        results: dict[str, Any] = ydl.extract_info(
            f"ytsearch{max_results}:{query}", download=False
        )

    entries: list[dict[str, Any]] = (results or {}).get("entries") or []
    candidates: list[Candidate] = [
        _build_candidate_from_entry(e) for e in entries if e and not _is_playlist_entry(e)
    ]

    if intent.target_type == "Album":
        for entry in _search_youtube_playlists(query):
            if not _is_youtube_mix(entry):
                candidates.append(_build_playlist_candidate(entry))

    return candidates
