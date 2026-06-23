from typing import Literal

from rapidfuzz import fuzz

from app.models.candidate import Candidate
from app.models.download_intent import DownloadIntent
from app.models.scored_candidate import ScoredCandidate

_TRACK_EXPECTED_DURATION_SECONDS: float = 210.0
_TRACK_DURATION_TOLERANCE_SECONDS: float = 150.0

_ALBUM_EXPECTED_DURATION_SECONDS: float = 2700.0
_ALBUM_DURATION_TOLERANCE_SECONDS: float = 1500.0

_OFFICIAL_CHANNEL_KEYWORDS: frozenset[str] = frozenset(
    {"official", "vevo", "records", "music", "audio"}
)
_REUPLOAD_CHANNEL_KEYWORDS: frozenset[str] = frozenset(
    {
        "lyrics",
        "lyric",
        "cover",
        "reaction",
        "tribute",
        "karaoke",
        "slowed",
        "reverb",
        "nightcore",
        "compilation",
    }
)

_TITLE_MATCH_WEIGHT: float = 0.45
_DURATION_MATCH_WEIGHT: float = 0.35
_SOURCE_QUALITY_WEIGHT: float = 0.20


def _score_title_match(candidate_title: str, artist: str, track_title: str) -> float:
    if not candidate_title.strip():
        return 0.0
    query_string = f"{artist} {track_title}".lower()
    candidate_lower = candidate_title.lower()
    set_score: int = fuzz.token_set_ratio(query_string, candidate_lower)
    sort_score: int = fuzz.token_sort_ratio(query_string, candidate_lower)
    raw_score: float = (set_score * sort_score) ** 0.5
    return raw_score / 100.0


def _score_duration_match(
    duration_seconds: float,
    target_type: Literal["Track", "Album"],
) -> float:
    if target_type == "Track":
        expected_duration = _TRACK_EXPECTED_DURATION_SECONDS
        tolerance = _TRACK_DURATION_TOLERANCE_SECONDS
    else:
        expected_duration = _ALBUM_EXPECTED_DURATION_SECONDS
        tolerance = _ALBUM_DURATION_TOLERANCE_SECONDS

    deviation = abs(duration_seconds - expected_duration)
    if deviation >= tolerance:
        return 0.0
    return 1.0 - (deviation / tolerance) * 0.9


def _score_source_quality(channel_name: str, artist: str) -> float:
    normalised_channel = channel_name.lower().strip()
    normalised_artist = artist.lower().strip()

    if normalised_artist and normalised_artist in normalised_channel:
        return 1.0

    channel_tokens = set(normalised_channel.split())

    if channel_tokens & _OFFICIAL_CHANNEL_KEYWORDS:
        return 0.8

    if channel_tokens & _REUPLOAD_CHANNEL_KEYWORDS:
        return 0.2

    return 0.5


def evaluate_candidates(
    candidates: list[Candidate], intent: DownloadIntent, top_k: int = 5
) -> list[ScoredCandidate]:
    scored_candidates: list[ScoredCandidate] = []

    for candidate in candidates:
        title_match_score = _score_title_match(
            candidate.title, intent.artist, intent.title
        )
        duration_match_score = _score_duration_match(
            candidate.duration_seconds, intent.target_type
        )
        source_quality_score = _score_source_quality(
            candidate.channel_name, intent.artist
        )

        confidence_score = (
            _TITLE_MATCH_WEIGHT * title_match_score
            + _DURATION_MATCH_WEIGHT * duration_match_score
            + _SOURCE_QUALITY_WEIGHT * source_quality_score
        )
        print(candidate.title, candidate.channel_name, ":", confidence_score)
        scored_candidates.append(
            ScoredCandidate(
                **candidate.model_dump(),
                title_match_score=title_match_score,
                duration_match_score=duration_match_score,
                source_quality_score=source_quality_score,
                confidence_score=confidence_score,
            )
        )

    playlist_scored = [sc for sc in scored_candidates if sc.is_playlist]
    video_scored = [sc for sc in scored_candidates if not sc.is_playlist]

    playlist_scored.sort(key=lambda sc: sc.confidence_score, reverse=True)
    video_scored.sort(key=lambda sc: sc.confidence_score, reverse=True)

    return playlist_scored + video_scored[:top_k]
