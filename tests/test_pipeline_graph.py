from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command, StateSnapshot
from mutagen.id3 import ID3

from app.core.config import Settings
from app.models.candidate import Candidate
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
@patch("app.pipeline.graph.run_download", return_value=_FAKE_DOWNLOAD_RESULT)
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
@patch("app.pipeline.graph.run_download", return_value=_FAKE_DOWNLOAD_RESULT)
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
