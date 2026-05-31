from app.models.candidate import Candidate
from app.models.download_intent import DownloadIntent
from app.models.scored_candidate import ScoredCandidate
from app.pipeline.candidate_evaluator import evaluate_candidates

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_OFFICIAL_TRACK_CANDIDATE = Candidate(
    url="https://www.youtube.com/watch?v=abc001",
    title="Pink Floyd - Comfortably Numb",
    duration_seconds=382.0,
    channel_name="Pink Floyd Official",
    view_count=50_000_000,
)

_REUPLOAD_TRACK_CANDIDATE = Candidate(
    url="https://www.youtube.com/watch?v=abc002",
    title="Comfortably Numb - Pink Floyd (Lyrics)",
    duration_seconds=380.0,
    channel_name="Lyrics Channel",
    view_count=8_000_000,
)

_ALBUM_LENGTH_CANDIDATE = Candidate(
    url="https://www.youtube.com/watch?v=abc003",
    title="Pink Floyd - The Wall (Full Album)",
    duration_seconds=5100.0,
    channel_name="Pink Floyd Official",
    view_count=20_000_000,
)

_WRONG_SONG_CANDIDATE = Candidate(
    url="https://www.youtube.com/watch?v=abc004",
    title="Another Brick in the Wall - Pink Floyd",
    duration_seconds=237.0,
    channel_name="Music Archive",
    view_count=3_000_000,
)


def _make_track_intent() -> DownloadIntent:
    return DownloadIntent(
        target_type="Track",
        artist="Pink Floyd",
        title="Comfortably Numb",
    )


def _make_album_intent() -> DownloadIntent:
    return DownloadIntent(
        target_type="Album",
        artist="Pink Floyd",
        title="The Wall",
    )


# ---------------------------------------------------------------------------
# Title match signal
# ---------------------------------------------------------------------------


def test_exact_title_match_scores_near_one() -> None:
    intent = _make_track_intent()
    result = evaluate_candidates(
        [_OFFICIAL_TRACK_CANDIDATE], intent, confidence_threshold=0.0
    )
    assert len(result) == 1
    assert result[0].title_match_score >= 0.90


def test_partial_title_match_scores_lower_than_exact() -> None:
    intent = _make_track_intent()
    result = evaluate_candidates(
        [_OFFICIAL_TRACK_CANDIDATE, _REUPLOAD_TRACK_CANDIDATE],
        intent,
        confidence_threshold=0.0,
    )
    assert len(result) == 2
    official_score = next(
        r.title_match_score for r in result if r.url == _OFFICIAL_TRACK_CANDIDATE.url
    )
    reupload_score = next(
        r.title_match_score for r in result if r.url == _REUPLOAD_TRACK_CANDIDATE.url
    )
    assert official_score > reupload_score


# ---------------------------------------------------------------------------
# Duration match signal
# ---------------------------------------------------------------------------


def test_mismatched_duration_scores_zero() -> None:
    intent = _make_track_intent()
    result = evaluate_candidates(
        [_ALBUM_LENGTH_CANDIDATE], intent, confidence_threshold=0.0
    )
    assert len(result) == 1
    assert result[0].duration_match_score == 0.0


# ---------------------------------------------------------------------------
# Source quality signal
# ---------------------------------------------------------------------------


def test_official_channel_scores_higher_than_reupload() -> None:
    intent = _make_track_intent()
    result = evaluate_candidates(
        [_OFFICIAL_TRACK_CANDIDATE, _REUPLOAD_TRACK_CANDIDATE],
        intent,
        confidence_threshold=0.0,
    )
    official_source_score = next(
        r.source_quality_score for r in result if r.url == _OFFICIAL_TRACK_CANDIDATE.url
    )
    reupload_source_score = next(
        r.source_quality_score for r in result if r.url == _REUPLOAD_TRACK_CANDIDATE.url
    )
    assert official_source_score == 1.0
    assert reupload_source_score == 0.2
    assert official_source_score > reupload_source_score


# ---------------------------------------------------------------------------
# Threshold filtering
# ---------------------------------------------------------------------------


def test_album_length_video_rejected_for_track_intent() -> None:
    intent = _make_track_intent()
    result = evaluate_candidates(
        [_ALBUM_LENGTH_CANDIDATE], intent, confidence_threshold=0.7
    )
    assert result == []


def test_threshold_boundary_exactly_at_score_passes() -> None:
    intent = _make_track_intent()
    # Use threshold=0.0 to capture the actual score, then re-run at exactly that score.
    all_results = evaluate_candidates(
        [_OFFICIAL_TRACK_CANDIDATE], intent, confidence_threshold=0.0
    )
    assert len(all_results) == 1
    actual_score = all_results[0].confidence_score

    at_threshold = evaluate_candidates(
        [_OFFICIAL_TRACK_CANDIDATE], intent, confidence_threshold=actual_score
    )
    assert len(at_threshold) == 1


def test_threshold_boundary_just_above_score_excludes() -> None:
    intent = _make_track_intent()
    all_results = evaluate_candidates(
        [_OFFICIAL_TRACK_CANDIDATE], intent, confidence_threshold=0.0
    )
    assert len(all_results) == 1
    actual_score = all_results[0].confidence_score

    just_above = evaluate_candidates(
        [_OFFICIAL_TRACK_CANDIDATE],
        intent,
        confidence_threshold=actual_score + 0.001,
    )
    assert just_above == []


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_candidates_sorted_descending_by_confidence() -> None:
    intent = _make_track_intent()
    # Pass wrong-song first to confirm sorting overrides input order.
    result = evaluate_candidates(
        [_WRONG_SONG_CANDIDATE, _OFFICIAL_TRACK_CANDIDATE],
        intent,
        confidence_threshold=0.0,
    )
    assert len(result) == 2
    assert result[0].confidence_score >= result[1].confidence_score
    assert result[0].url == _OFFICIAL_TRACK_CANDIDATE.url


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_candidates_returns_empty_list() -> None:
    intent = _make_track_intent()
    result = evaluate_candidates([], intent, confidence_threshold=0.7)
    assert result == []


def test_all_below_threshold_returns_empty_list() -> None:
    intent = _make_track_intent()
    result = evaluate_candidates(
        [_ALBUM_LENGTH_CANDIDATE], intent, confidence_threshold=0.7
    )
    assert result == []


def test_scored_candidate_exposes_all_component_scores() -> None:
    intent = _make_track_intent()
    result = evaluate_candidates(
        [_OFFICIAL_TRACK_CANDIDATE], intent, confidence_threshold=0.0
    )
    assert len(result) == 1
    scored = result[0]
    assert isinstance(scored, ScoredCandidate)
    assert 0.0 <= scored.title_match_score <= 1.0
    assert 0.0 <= scored.duration_match_score <= 1.0
    assert 0.0 <= scored.source_quality_score <= 1.0
    assert 0.0 <= scored.confidence_score <= 1.0
