from pathlib import Path
from unittest.mock import MagicMock, patch

from app.models.release import Release, TrackListing
from app.pipeline.timestamp_resolver import (
    _format_timestamp,
    _LLMChapterMap,
    _LLMRawChapter,
    _parse_timestamp,
    resolve_timestamps,
)

# --- fixtures / builders ------------------------------------------------------

_NATIVE_INFO = {
    "chapters": [
        {"start_time": 0.0, "end_time": 201.0, "title": "In the Flesh?"},
        {"start_time": 201.0, "end_time": 350.0, "title": "The Thin Ice"},
    ],
    "description": "ignored when native chapters exist",
    "comments": [{"text": "00:00 something"}],
}

_NO_NATIVE_INFO = {
    "chapters": [],
    "description": "Tracklist:\n00:00 In the Flesh?\n03:21 The Thin Ice",
    "comments": [{"text": "great album"}, {"text": "love it"}],
}


def _mock_llm(raw_map: _LLMChapterMap) -> MagicMock:
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = raw_map
    mock_model = MagicMock()
    mock_model.with_structured_output.return_value = mock_chain
    return mock_model


def _release_with_durations() -> Release:
    return Release(
        discogs_id=1,
        artist="Pink Floyd",
        album_title="The Wall",
        year=1979,
        tracklist=[
            TrackListing(position="1", title="In the Flesh?", duration_seconds=201.0),
            TrackListing(position="2", title="The Thin Ice", duration_seconds=149.0),
        ],
    )


def _release_missing_duration() -> Release:
    return Release(
        discogs_id=1,
        artist="Pink Floyd",
        album_title="The Wall",
        year=1979,
        tracklist=[
            TrackListing(position="1", title="In the Flesh?", duration_seconds=201.0),
            TrackListing(position="2", title="The Thin Ice", duration_seconds=None),
        ],
    )


# --- helpers ------------------------------------------------------------------


def test_parse_timestamp_mm_ss() -> None:
    assert _parse_timestamp("03:21") == 201.0


def test_parse_timestamp_hh_mm_ss() -> None:
    assert _parse_timestamp("01:02:03") == 3723.0


def test_parse_timestamp_junk_returns_none() -> None:
    assert _parse_timestamp("not a time") is None
    assert _parse_timestamp("") is None
    assert _parse_timestamp("12") is None


def test_format_timestamp_pads_to_hh_mm_ss() -> None:
    assert _format_timestamp(0.0) == "00:00:00"
    assert _format_timestamp(201.0) == "00:03:21"
    assert _format_timestamp(3723.0) == "01:02:03"


def test_format_parse_round_trip() -> None:
    assert _parse_timestamp(_format_timestamp(3723.0)) == 3723.0


# --- Level 1 (native) ---------------------------------------------------------


@patch("app.pipeline.timestamp_resolver.yt_dlp.YoutubeDL")
def test_level1_native_chapters_short_circuit(MockYDL, tmp_path: Path) -> None:
    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = _NATIVE_INFO
    mock_llm = _mock_llm(_LLMChapterMap(chapters=[]))

    result = resolve_timestamps(
        "https://yt.com/v=abc", _release_with_durations(), mock_llm, tmp_path
    )

    assert result.method == "native"
    assert result.chapter_map_path is None
    assert [c.track_name for c in result.chapters] == ["In the Flesh?", "The Thin Ice"]
    assert result.chapters[1].start_seconds == 201.0
    # Levels 2 and 3 never run.
    mock_llm.with_structured_output.assert_not_called()
    assert not list(tmp_path.iterdir())


# --- Level 2 (LLM) ------------------------------------------------------------


@patch("app.pipeline.timestamp_resolver.yt_dlp.YoutubeDL")
def test_level2_llm_when_no_native(MockYDL, tmp_path: Path) -> None:
    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = _NO_NATIVE_INFO
    mock_llm = _mock_llm(
        _LLMChapterMap(
            chapters=[
                _LLMRawChapter(start_time="00:00", track_name="In the Flesh?"),
                _LLMRawChapter(start_time="03:21", track_name="The Thin Ice"),
            ]
        )
    )

    result = resolve_timestamps(
        "https://yt.com/v=abc", _release_with_durations(), mock_llm, tmp_path
    )

    assert result.method == "llm"
    assert result.chapter_map_path == tmp_path / "chapters.txt"
    assert result.chapter_map_path.exists()
    assert result.chapters[0].start_seconds == 0.0
    assert result.chapters[1].start_seconds == 201.0
    mock_llm.with_structured_output.assert_called_once()


@patch("app.pipeline.timestamp_resolver.yt_dlp.YoutubeDL")
def test_level2_drops_unparseable_entries(MockYDL, tmp_path: Path) -> None:
    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = _NO_NATIVE_INFO
    mock_llm = _mock_llm(
        _LLMChapterMap(
            chapters=[
                _LLMRawChapter(start_time="00:00", track_name="Good"),
                _LLMRawChapter(start_time="nonsense", track_name="Bad"),
            ]
        )
    )

    result = resolve_timestamps("https://yt.com/v=abc", None, mock_llm, tmp_path)

    assert result.method == "llm"
    assert [c.track_name for c in result.chapters] == ["Good"]


# --- Level 3 (Discogs) --------------------------------------------------------


@patch("app.pipeline.timestamp_resolver.yt_dlp.YoutubeDL")
def test_level3_discogs_when_llm_empty(MockYDL, tmp_path: Path) -> None:
    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = _NO_NATIVE_INFO
    mock_llm = _mock_llm(_LLMChapterMap(chapters=[]))

    result = resolve_timestamps(
        "https://yt.com/v=abc", _release_with_durations(), mock_llm, tmp_path
    )

    assert result.method == "discogs"
    assert result.chapter_map_path == tmp_path / "chapters.txt"
    # Cumulative starts: 0, then 0 + 201.
    assert result.chapters[0].start_seconds == 0.0
    assert result.chapters[1].start_seconds == 201.0


@patch("app.pipeline.timestamp_resolver.yt_dlp.YoutubeDL")
def test_level3_skipped_when_duration_missing(MockYDL, tmp_path: Path) -> None:
    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = _NO_NATIVE_INFO
    mock_llm = _mock_llm(_LLMChapterMap(chapters=[]))

    result = resolve_timestamps(
        "https://yt.com/v=abc", _release_missing_duration(), mock_llm, tmp_path
    )

    assert result.method == "manual_required"


# --- Chapter Map file format --------------------------------------------------


@patch("app.pipeline.timestamp_resolver.yt_dlp.YoutubeDL")
def test_chapter_map_file_format(MockYDL, tmp_path: Path) -> None:
    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = _NO_NATIVE_INFO
    mock_llm = _mock_llm(
        _LLMChapterMap(
            chapters=[
                _LLMRawChapter(start_time="00:00", track_name="In the Flesh?"),
                _LLMRawChapter(start_time="03:21", track_name="The Thin Ice"),
            ]
        )
    )

    result = resolve_timestamps("https://yt.com/v=abc", None, mock_llm, tmp_path)

    lines = result.chapter_map_path.read_text(encoding="utf-8").splitlines()
    assert lines == ["00:00:00 In the Flesh?", "00:03:21 The Thin Ice"]


# --- All-levels-fail ----------------------------------------------------------


@patch("app.pipeline.timestamp_resolver.yt_dlp.YoutubeDL")
def test_all_levels_fail_signals_manual(MockYDL, tmp_path: Path) -> None:
    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = _NO_NATIVE_INFO
    mock_llm = _mock_llm(_LLMChapterMap(chapters=[]))

    result = resolve_timestamps("https://yt.com/v=abc", None, mock_llm, tmp_path)

    assert result.method == "manual_required"
    assert result.chapters == []
    assert result.chapter_map_path is None
    # No partial download artifact: nothing written.
    assert not list(tmp_path.iterdir())


# --- Full cascade ordering ----------------------------------------------------


@patch("app.pipeline.timestamp_resolver._discogs_chapters")
@patch("app.pipeline.timestamp_resolver._llm_chapters")
@patch("app.pipeline.timestamp_resolver.yt_dlp.YoutubeDL")
def test_cascade_level1_skips_2_and_3(MockYDL, mock_llm_fn, mock_discogs_fn) -> None:
    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = _NATIVE_INFO

    resolve_timestamps(
        "https://yt.com/v=abc", _release_with_durations(), MagicMock(), Path("/tmp/x")
    )

    mock_llm_fn.assert_not_called()
    mock_discogs_fn.assert_not_called()


@patch("app.pipeline.timestamp_resolver._discogs_chapters", return_value=[])
@patch("app.pipeline.timestamp_resolver.yt_dlp.YoutubeDL")
def test_cascade_level2_success_skips_3(MockYDL, mock_discogs_fn, tmp_path) -> None:
    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = _NO_NATIVE_INFO
    mock_llm = _mock_llm(
        _LLMChapterMap(
            chapters=[_LLMRawChapter(start_time="00:00", track_name="Only Track")]
        )
    )

    result = resolve_timestamps(
        "https://yt.com/v=abc", _release_with_durations(), mock_llm, tmp_path
    )

    assert result.method == "llm"
    mock_discogs_fn.assert_not_called()
