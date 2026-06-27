from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command, StateSnapshot
from mutagen.id3 import ID3

from app.core.config import Settings
from app.models.candidate import Candidate
from app.models.chapter_map import ChapterEntry, TimestampResolution
from app.models.download_intent import DownloadIntent
from app.models.download_result import DownloadResult, TrackResult
from app.models.release import Release, TrackListing
from app.models.scored_candidate import ScoredCandidate
from app.pipeline.graph import _MAX_SEARCH_ITERATIONS, create_graph

_INTENT = DownloadIntent(target_type="Track", artist="Daft Punk", title="One More Time")

_CANDIDATE = Candidate(
    url="https://youtube.com/watch?v=abc",
    title="Daft Punk - One More Time",
    duration_seconds=320.0,
    channel_name="DaftPunkVEVO",
    view_count=1_000_000,
)

_SCORED = ScoredCandidate(
    **_CANDIDATE.model_dump(),
    title_match_score=0.95,
    duration_match_score=0.85,
    source_quality_score=0.80,
    confidence_score=0.88,
)

_SETTINGS = Settings(confidence_threshold=0.7)

_FAKE_DOWNLOAD_RESULT = DownloadResult(
    url=_CANDIDATE.url,
    use_m4a=False,
    tracks=[
        TrackResult(
            track_number=1,
            title="One More Time",
            path=_SETTINGS.local_files_directory
            / "Daft Punk"
            / "One More Time"
            / "01 - One More Time.mp3",
            skipped=False,
        )
    ],
    downloaded_count=1,
    skipped_count=0,
)

_FAKE_RELEASE = Release(
    discogs_id=1,
    artist="Daft Punk",
    album_title="Discovery",
    year=2001,
    genres=["Electronic"],
    artwork_url=None,
    tracklist=[TrackListing(position="1", title="One More Time")],
)

_ARTWORK = b"\xff\xd8\xff\xe0\x00\x10JFIFfakejpegpayload"

_INITIAL_STATE = {
    "request": "Daft Punk One More Time",
    "download_intent": None,
    "candidates": [],
    "scored_candidates": [],
    "search_iteration": 0,
    "selected_candidate_url": None,
    "use_m4a": False,
    "download_result": None,
}


def _make_graph(mem):
    return create_graph(MagicMock(), _SETTINGS, mem)


def _config(thread_id: str = "t1") -> dict:
    return {"configurable": {"thread_id": thread_id}}


@patch("app.pipeline.graph.evaluate_candidates", return_value=[_SCORED])
@patch("app.pipeline.graph.search_candidates", return_value=[_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_INTENT)
def test_graph_suspends_at_candidate_review(mock_parse, mock_search, mock_eval):
    with SqliteSaver.from_conn_string(":memory:") as mem:
        graph = _make_graph(mem)
        config = _config()
        result = graph.invoke(_INITIAL_STATE, config)

        assert "__interrupt__" in result
        snapshot = graph.get_state(config)
        assert snapshot.next == ("candidate_review",)
        interrupt_value = snapshot.tasks[0].interrupts[0].value
        assert interrupt_value["candidates"] == [_SCORED]


@patch("app.pipeline.graph.evaluate_candidates", return_value=[])
@patch("app.pipeline.graph.search_candidates", return_value=[_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_INTENT)
def test_graph_loops_back_when_no_candidates_pass_threshold(
    mock_parse, mock_search, mock_eval
):
    with SqliteSaver.from_conn_string(":memory:") as mem:
        graph = _make_graph(mem)
        config = _config("t2")
        graph.invoke(_INITIAL_STATE, config)

        snapshot = graph.get_state(config)
        assert mock_search.call_count == _MAX_SEARCH_ITERATIONS
        assert not snapshot.next


@patch("app.pipeline.graph.evaluate_candidates", return_value=[])
@patch("app.pipeline.graph.search_candidates", return_value=[])
@patch("app.pipeline.graph.parse_request", return_value=_INTENT)
def test_graph_search_exhausted_after_max_iterations(
    mock_parse, mock_search, mock_eval
):
    with SqliteSaver.from_conn_string(":memory:") as mem:
        graph = _make_graph(mem)
        config = _config("t3")
        result = graph.invoke(_INITIAL_STATE, config)

        assert "__interrupt__" not in result
        snapshot = graph.get_state(config)
        assert not snapshot.next
        assert mock_search.call_count == _MAX_SEARCH_ITERATIONS


@patch("app.pipeline.graph.inject_metadata", return_value=_FAKE_DOWNLOAD_RESULT)
@patch("app.pipeline.graph.fetch_artwork", return_value=None)
@patch("app.pipeline.graph.get_discogs_client")
@patch("app.pipeline.graph.download_track", return_value=_FAKE_DOWNLOAD_RESULT)
@patch("app.pipeline.graph.evaluate_candidates", return_value=[_SCORED])
@patch("app.pipeline.graph.search_candidates", return_value=[_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_INTENT)
def test_resume_sets_selected_candidate_url(
    mock_parse,
    mock_search,
    mock_eval,
    mock_download,
    mock_discogs,
    mock_art,
    mock_inject,
):
    mock_discogs.return_value.fetch_release.return_value = _FAKE_RELEASE
    with SqliteSaver.from_conn_string(":memory:") as mem:
        graph = _make_graph(mem)
        config = _config("t4")
        graph.invoke(_INITIAL_STATE, config)

        snapshot: StateSnapshot = graph.get_state(config)
        assert snapshot.next == ("candidate_review",)

        selected_url = "https://youtube.com/watch?v=abc"
        graph.invoke(Command(resume=selected_url), config)

        snapshot = graph.get_state(config)
        assert not snapshot.next
        assert snapshot.values["selected_candidate_url"] == selected_url
        assert snapshot.values["download_result"] == _FAKE_DOWNLOAD_RESULT


@patch("app.pipeline.graph.fetch_artwork", return_value=_ARTWORK)
@patch("app.pipeline.graph.get_discogs_client")
@patch("app.pipeline.downloader.yt_dlp.YoutubeDL")
@patch("app.pipeline.graph.evaluate_candidates", return_value=[_SCORED])
@patch("app.pipeline.graph.search_candidates", return_value=[_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_INTENT)
def test_track_pipeline_writes_tagged_mp3_end_to_end(
    mock_parse,
    mock_search,
    mock_eval,
    MockYDL,
    mock_discogs,
    mock_art,
    tmp_path: Path,
):
    """Resume runs the REAL downloader + metadata injector through the graph and
    produces a correctly-tagged .mp3 at the expected path (AC #2 and #3)."""
    mock_discogs.return_value.fetch_release.return_value = _FAKE_RELEASE
    settings = Settings(confidence_threshold=0.7, local_files_directory=tmp_path)
    expected_path = tmp_path / "Daft Punk" / "One More Time" / "01 - One More Time.mp3"

    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl
    # yt-dlp would write+transcode the audio; emulate by dropping an empty mp3
    # at the path the downloader expects (mutagen tags an empty file fine).
    mock_ydl.download.side_effect = lambda _urls: expected_path.write_bytes(b"")

    with SqliteSaver.from_conn_string(":memory:") as mem:
        graph = create_graph(MagicMock(), settings, mem)
        config = _config("t5")
        graph.invoke(_INITIAL_STATE, config)
        graph.invoke(Command(resume=_CANDIDATE.url), config)

        snapshot = graph.get_state(config)
        assert not snapshot.next
        result = snapshot.values["download_result"]
        assert result.tracks[0].path == expected_path
        assert result.tracks[0].skipped is False
        assert result.downloaded_count == 1
        assert expected_path.exists()

        # translate=False keeps the raw v2.3 TYER frame (mutagen otherwise
        # auto-upgrades it to the v2.4 TDRC form on read).
        tags = ID3(expected_path, translate=False)
        assert str(tags["TIT2"].text[0]) == "One More Time"
        assert str(tags["TPE1"].text[0]) == "Daft Punk"
        assert str(tags["TALB"].text[0]) == "Discovery"
        assert str(tags["TYER"].text[0]) == "2001"
        assert str(tags["TRCK"].text[0]) == "1/1"
        apic = tags.getall("APIC")
        assert len(apic) == 1
        assert apic[0].data == _ARTWORK
        assert apic[0].type == 3

        # Metadata summary is surfaced in final state (AC: returned by /resume).
        assert snapshot.values["release"] == _FAKE_RELEASE


@patch("app.pipeline.graph.fetch_artwork", return_value=_ARTWORK)
@patch("app.pipeline.graph.get_discogs_client")
@patch("app.pipeline.downloader.yt_dlp.YoutubeDL")
@patch("app.pipeline.graph.evaluate_candidates", return_value=[_SCORED])
@patch("app.pipeline.graph.search_candidates", return_value=[_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_INTENT)
def test_track_pipeline_skips_duplicate_download(
    mock_parse,
    mock_search,
    mock_eval,
    MockYDL,
    mock_discogs,
    mock_art,
    tmp_path: Path,
):
    """A pre-existing file at the expected path is skipped, not re-downloaded.

    Covers AC #4 (duplicate detection) through the graph.
    """
    mock_discogs.return_value.fetch_release.return_value = _FAKE_RELEASE
    settings = Settings(confidence_threshold=0.7, local_files_directory=tmp_path)
    expected_path = tmp_path / "Daft Punk" / "One More Time" / "01 - One More Time.mp3"
    expected_path.parent.mkdir(parents=True, exist_ok=True)
    expected_path.write_bytes(b"")

    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl

    with SqliteSaver.from_conn_string(":memory:") as mem:
        graph = create_graph(MagicMock(), settings, mem)
        config = _config("t6")
        graph.invoke(_INITIAL_STATE, config)
        graph.invoke(Command(resume=_CANDIDATE.url), config)

        snapshot = graph.get_state(config)
        result = snapshot.values["download_result"]
        assert result.skipped_count == 1
        assert result.downloaded_count == 0
        assert result.tracks[0].skipped is True
        mock_ydl.download.assert_not_called()


@pytest.fixture
def api_client():
    from langgraph.checkpoint.sqlite import SqliteSaver

    import main as app_module

    with SqliteSaver.from_conn_string(":memory:") as mem:
        app_module.app.state.checkpointer = mem
        with TestClient(app_module.app) as client:
            yield client


@patch("app.pipeline.graph.evaluate_candidates", return_value=[_SCORED])
@patch("app.pipeline.graph.search_candidates", return_value=[_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_INTENT)
@patch("main.get_llm", return_value=MagicMock())
def test_download_endpoint_returns_candidate_review(
    mock_llm, mock_parse, mock_search, mock_eval, api_client
):
    response = api_client.post("/download", json={"request": "Daft Punk One More Time"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "candidate_review"
    assert "thread_id" in data
    assert len(data["candidates"]) == 1


@patch("app.pipeline.graph.inject_metadata", return_value=_FAKE_DOWNLOAD_RESULT)
@patch("app.pipeline.graph.fetch_artwork", return_value=None)
@patch("app.pipeline.graph.get_discogs_client")
@patch("app.pipeline.graph.download_track", return_value=_FAKE_DOWNLOAD_RESULT)
@patch("app.pipeline.graph.evaluate_candidates", return_value=[_SCORED])
@patch("app.pipeline.graph.search_candidates", return_value=[_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_INTENT)
@patch("main.get_llm", return_value=MagicMock())
def test_resume_endpoint_completes_pipeline(
    mock_llm,
    mock_parse,
    mock_search,
    mock_eval,
    mock_download,
    mock_discogs,
    mock_art,
    mock_inject,
    api_client,
):
    mock_discogs.return_value.fetch_release.return_value = _FAKE_RELEASE
    dl = api_client.post("/download", json={"request": "Daft Punk One More Time"})
    thread_id = dl.json()["thread_id"]

    response = api_client.post(
        "/resume",
        json={"thread_id": thread_id, "selected_candidate_url": _CANDIDATE.url},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["selected_candidate_url"] == _CANDIDATE.url
    assert "download_result" in data
    assert data["release"]["album_title"] == "Discovery"
    assert data["release"]["year"] == 2001


# --- Album pipeline (Slice 11): Playlist + Compilation sources ----------------

_ALBUM_INTENT = DownloadIntent(
    target_type="Album", artist="Pink Floyd", title="The Wall"
)

_PLAYLIST_CANDIDATE = Candidate(
    url="https://youtube.com/playlist?list=PL1",
    title="Pink Floyd - The Wall (Full Album)",
    duration_seconds=2580.0,
    channel_name="PinkFloydVEVO",
    view_count=500_000,
    is_playlist=True,
)
_PLAYLIST_SCORED = ScoredCandidate(
    **_PLAYLIST_CANDIDATE.model_dump(),
    title_match_score=0.95,
    duration_match_score=0.9,
    source_quality_score=0.8,
    confidence_score=0.9,
)

_COMP_CANDIDATE = Candidate(
    url="https://youtube.com/watch?v=comp",
    title="Pink Floyd - The Wall (Full Album)",
    duration_seconds=2580.0,
    channel_name="PinkFloydArchive",
    view_count=500_000,
    is_playlist=False,
)
_COMP_SCORED = ScoredCandidate(
    **_COMP_CANDIDATE.model_dump(),
    title_match_score=0.95,
    duration_match_score=0.9,
    source_quality_score=0.8,
    confidence_score=0.9,
)

_ALBUM_RELEASE = Release(
    discogs_id=2,
    artist="Pink Floyd",
    album_title="The Wall",
    year=1979,
    genres=["Rock"],
    artwork_url=None,
    tracklist=[
        TrackListing(position="1", title="In the Flesh?", duration_seconds=201.0),
        TrackListing(position="2", title="The Thin Ice", duration_seconds=149.0),
    ],
)

_NATIVE_CHAPTERS = [
    ChapterEntry(start_seconds=0.0, track_name="In the Flesh?"),
    ChapterEntry(start_seconds=201.0, track_name="The Thin Ice"),
]

_ALBUM_DOWNLOAD_RESULT = DownloadResult(
    url=_COMP_CANDIDATE.url,
    use_m4a=False,
    tracks=[
        TrackResult(
            track_number=1, title="In the Flesh?", path=Path("a"), skipped=False
        ),
        TrackResult(
            track_number=2, title="The Thin Ice", path=Path("b"), skipped=False
        ),
    ],
    downloaded_count=2,
    skipped_count=0,
)


@patch("app.pipeline.graph.inject_metadata", return_value=_ALBUM_DOWNLOAD_RESULT)
@patch("app.pipeline.graph.fetch_artwork", return_value=None)
@patch("app.pipeline.graph.get_discogs_client")
@patch("app.pipeline.graph.download_playlist", return_value=_ALBUM_DOWNLOAD_RESULT)
@patch("app.pipeline.graph.resolve_timestamps")
@patch("app.pipeline.graph.evaluate_candidates", return_value=[_PLAYLIST_SCORED])
@patch("app.pipeline.graph.search_candidates", return_value=[_PLAYLIST_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_ALBUM_INTENT)
def test_playlist_album_skips_timestamp_resolution(
    mock_parse,
    mock_search,
    mock_eval,
    mock_resolve,
    mock_playlist,
    mock_discogs,
    mock_art,
    mock_inject,
):
    mock_discogs.return_value.fetch_release.return_value = _ALBUM_RELEASE
    with SqliteSaver.from_conn_string(":memory:") as mem:
        graph = _make_graph(mem)
        config = _config("album_pl")
        graph.invoke(_INITIAL_STATE, config)
        graph.invoke(Command(resume=_PLAYLIST_CANDIDATE.url), config)

        snapshot = graph.get_state(config)
        assert not snapshot.next
        mock_resolve.assert_not_called()
        mock_playlist.assert_called_once()


@patch("app.pipeline.graph.inject_metadata", return_value=_ALBUM_DOWNLOAD_RESULT)
@patch("app.pipeline.graph.fetch_artwork", return_value=None)
@patch("app.pipeline.graph.get_discogs_client")
@patch("app.pipeline.graph.download_compilation", return_value=_ALBUM_DOWNLOAD_RESULT)
@patch(
    "app.pipeline.graph.resolve_timestamps",
    return_value=TimestampResolution(method="native", chapters=_NATIVE_CHAPTERS),
)
@patch("app.pipeline.graph.evaluate_candidates", return_value=[_COMP_SCORED])
@patch("app.pipeline.graph.search_candidates", return_value=[_COMP_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_ALBUM_INTENT)
def test_compilation_native_routes_straight_to_download(
    mock_parse,
    mock_search,
    mock_eval,
    mock_resolve,
    mock_compilation,
    mock_discogs,
    mock_art,
    mock_inject,
):
    mock_discogs.return_value.fetch_release.return_value = _ALBUM_RELEASE
    with SqliteSaver.from_conn_string(":memory:") as mem:
        graph = _make_graph(mem)
        config = _config("album_comp_native")
        graph.invoke(_INITIAL_STATE, config)
        graph.invoke(Command(resume=_COMP_CANDIDATE.url), config)

        snapshot = graph.get_state(config)
        assert not snapshot.next  # never suspended at chapter_map_prompt
        mock_resolve.assert_called_once()
        mock_compilation.assert_called_once()
        assert mock_compilation.call_args[0][2] == _NATIVE_CHAPTERS


@patch("app.pipeline.graph.get_discogs_client")
@patch(
    "app.pipeline.graph.resolve_timestamps",
    return_value=TimestampResolution(method="manual_required", chapters=[]),
)
@patch("app.pipeline.graph.evaluate_candidates", return_value=[_COMP_SCORED])
@patch("app.pipeline.graph.search_candidates", return_value=[_COMP_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_ALBUM_INTENT)
def test_all_levels_fail_suspends_at_chapter_map_prompt(
    mock_parse, mock_search, mock_eval, mock_resolve, mock_discogs
):
    mock_discogs.return_value.fetch_release.return_value = _ALBUM_RELEASE
    with SqliteSaver.from_conn_string(":memory:") as mem:
        graph = _make_graph(mem)
        config = _config("album_comp_fail")
        graph.invoke(_INITIAL_STATE, config)
        graph.invoke(Command(resume=_COMP_CANDIDATE.url), config)

        snapshot = graph.get_state(config)
        assert snapshot.next == ("chapter_map_prompt",)
        payload = snapshot.tasks[0].interrupts[0].value
        assert payload["download_intent"] == _ALBUM_INTENT
        assert payload["tracklist"] == ["In the Flesh?", "The Thin Ice"]


@patch("app.pipeline.graph.inject_metadata", return_value=_ALBUM_DOWNLOAD_RESULT)
@patch("app.pipeline.graph.fetch_artwork", return_value=None)
@patch("app.pipeline.graph.get_discogs_client")
@patch("app.pipeline.graph.download_compilation", return_value=_ALBUM_DOWNLOAD_RESULT)
@patch(
    "app.pipeline.graph.resolve_timestamps",
    return_value=TimestampResolution(method="manual_required", chapters=[]),
)
@patch("app.pipeline.graph.evaluate_candidates", return_value=[_COMP_SCORED])
@patch("app.pipeline.graph.search_candidates", return_value=[_COMP_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_ALBUM_INTENT)
def test_manual_chapter_map_resume_splits_and_completes(
    mock_parse,
    mock_search,
    mock_eval,
    mock_resolve,
    mock_compilation,
    mock_discogs,
    mock_art,
    mock_inject,
    tmp_path: Path,
):
    mock_discogs.return_value.fetch_release.return_value = _ALBUM_RELEASE
    map_path = tmp_path / "manual_chapters.txt"
    map_path.write_text(
        "00:00:00 In the Flesh?\n00:03:21 The Thin Ice\n", encoding="utf-8"
    )

    with SqliteSaver.from_conn_string(":memory:") as mem:
        graph = _make_graph(mem)
        config = _config("album_comp_manual")
        graph.invoke(_INITIAL_STATE, config)
        graph.invoke(Command(resume=_COMP_CANDIDATE.url), config)
        assert graph.get_state(config).next == ("chapter_map_prompt",)

        # Real read_chapter_map parses the supplied file into chapters.
        graph.invoke(Command(resume=str(map_path)), config)

        snapshot = graph.get_state(config)
        assert not snapshot.next
        mock_compilation.assert_called_once()
        parsed_chapters = mock_compilation.call_args[0][2]
        assert [c.track_name for c in parsed_chapters] == [
            "In the Flesh?",
            "The Thin Ice",
        ]
        assert parsed_chapters[1].start_seconds == 201.0


@patch("app.pipeline.graph.inject_metadata", return_value=_ALBUM_DOWNLOAD_RESULT)
@patch("app.pipeline.graph.fetch_artwork", return_value=None)
@patch("app.pipeline.graph.get_discogs_client")
@patch("app.pipeline.graph.download_compilation", return_value=_ALBUM_DOWNLOAD_RESULT)
@patch(
    "app.pipeline.graph.resolve_timestamps",
    return_value=TimestampResolution(method="manual_required", chapters=[]),
)
@patch("app.pipeline.graph.evaluate_candidates", return_value=[_COMP_SCORED])
@patch("app.pipeline.graph.search_candidates", return_value=[_COMP_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_ALBUM_INTENT)
@patch("main.get_llm", return_value=MagicMock())
def test_api_chapter_map_prompt_then_manual_resume(
    mock_llm,
    mock_parse,
    mock_search,
    mock_eval,
    mock_resolve,
    mock_compilation,
    mock_discogs,
    mock_art,
    mock_inject,
    api_client,
    tmp_path: Path,
):
    mock_discogs.return_value.fetch_release.return_value = _ALBUM_RELEASE
    dl = api_client.post("/download", json={"request": "Pink Floyd The Wall album"})
    thread_id = dl.json()["thread_id"]
    assert dl.json()["status"] == "candidate_review"

    prompt = api_client.post(
        "/resume",
        json={"thread_id": thread_id, "selected_candidate_url": _COMP_CANDIDATE.url},
    )
    assert prompt.status_code == 200
    prompt_data = prompt.json()
    assert prompt_data["status"] == "chapter_map_prompt"
    assert prompt_data["tracklist"] == ["In the Flesh?", "The Thin Ice"]

    map_path = tmp_path / "api_chapters.txt"
    map_path.write_text(
        "00:00:00 In the Flesh?\n00:03:21 The Thin Ice\n", encoding="utf-8"
    )
    done = api_client.post(
        "/resume",
        json={"thread_id": thread_id, "chapter_map_file_path": str(map_path)},
    )
    assert done.status_code == 200
    assert done.json()["status"] == "completed"


@patch("app.pipeline.graph.fetch_artwork", return_value=_ARTWORK)
@patch("app.pipeline.graph.get_discogs_client")
@patch("app.pipeline.downloader.subprocess.run")
@patch("app.pipeline.downloader.yt_dlp.YoutubeDL")
@patch(
    "app.pipeline.graph.resolve_timestamps",
    return_value=TimestampResolution(method="native", chapters=_NATIVE_CHAPTERS),
)
@patch("app.pipeline.graph.evaluate_candidates", return_value=[_COMP_SCORED])
@patch("app.pipeline.graph.search_candidates", return_value=[_COMP_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_ALBUM_INTENT)
def test_compilation_pipeline_writes_tagged_tracks_end_to_end(
    mock_parse,
    mock_search,
    mock_eval,
    mock_resolve,
    MockYDL,
    mock_run,
    mock_discogs,
    mock_art,
    tmp_path: Path,
):
    """Resume runs the REAL download_compilation + metadata injector through the
    graph and produces correctly-tagged split .mp3s in <Artist>/<Album>/."""
    mock_discogs.return_value.fetch_release.return_value = _ALBUM_RELEASE
    settings = Settings(confidence_threshold=0.7, local_files_directory=tmp_path)
    album = tmp_path / "Pink Floyd" / "The Wall"

    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl
    mock_ydl.download.side_effect = lambda _urls: (
        album / "__compilation_full.mp3"
    ).write_bytes(b"")
    # ffmpeg would carve each segment; emulate by dropping an empty mp3 at dest.
    mock_run.side_effect = lambda cmd, **kwargs: Path(cmd[-1]).write_bytes(b"")

    with SqliteSaver.from_conn_string(":memory:") as mem:
        graph = create_graph(MagicMock(), settings, mem)
        config = _config("album_comp_e2e")
        graph.invoke(_INITIAL_STATE, config)
        graph.invoke(Command(resume=_COMP_CANDIDATE.url), config)

        snapshot = graph.get_state(config)
        assert not snapshot.next
        result = snapshot.values["download_result"]
        assert result.downloaded_count == 2

        track1 = album / "01 - In the Flesh_.mp3"
        track2 = album / "02 - The Thin Ice.mp3"
        assert track1.exists() and track2.exists()

        tags = ID3(track1, translate=False)
        assert str(tags["TIT2"].text[0]) == "In the Flesh?"
        assert str(tags["TPE1"].text[0]) == "Pink Floyd"
        assert str(tags["TALB"].text[0]) == "The Wall"
        assert str(tags["TRCK"].text[0]) == "1/2"
        assert tags.getall("APIC")[0].data == _ARTWORK
