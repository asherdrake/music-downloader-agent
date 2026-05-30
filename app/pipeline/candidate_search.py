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

_URL_OPTS: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
}


def _build_candidate_from_entry(entry: dict[str, Any]) -> Candidate:
    url = entry.get("url") or f"https://www.youtube.com/watch?v={entry['id']}"
    return Candidate(
        url=url,
        title=entry.get("title") or "",
        duration_seconds=float(entry.get("duration") or 0.0),
        channel_name=entry.get("channel") or entry.get("uploader") or "",
        view_count=int(entry.get("view_count") or 0),
    )


def _fetch_candidate_for_url(url: str) -> Candidate:
    with yt_dlp.YoutubeDL(_URL_OPTS) as ydl:
        info: dict[str, Any] = ydl.extract_info(url, download=False)
    return Candidate(
        url=url,
        title=info.get("title") or "",
        duration_seconds=float(info.get("duration") or 0.0),
        channel_name=info.get("channel") or info.get("uploader") or "",
        view_count=int(info.get("view_count") or 0),
    )


def search_candidates(intent: DownloadIntent, max_results: int = 10) -> list[Candidate]:
    if intent.resource_hint is not None:
        return [_fetch_candidate_for_url(intent.resource_hint)]

    query = f"{intent.artist} {intent.title}"
    with yt_dlp.YoutubeDL(_SEARCH_OPTS) as ydl:
        results: dict[str, Any] = ydl.extract_info(
            f"ytsearch{max_results}:{query}", download=False
        )

    entries: list[dict[str, Any]] = (results or {}).get("entries") or []
    return [_build_candidate_from_entry(entry) for entry in entries if entry]
