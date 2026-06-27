"""Unit tests for Module A: the Pipeline event-stream layer.

Follows the tests/test_pipeline_graph.py pattern: a real compiled graph with the
node functions patched and an in-memory SQLite checkpointer. We assert the
*ordered* sequence of typed events that stream_pipeline_events yields for the two
canonical paths through the run: reaching the Candidate Review interrupt, and
selecting through to completion.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.pipeline.event_stream import (
    DoneEvent,
    InterruptEvent,
    StatusEvent,
    stream_pipeline_events,
)
from app.pipeline.graph import create_graph
from tests.test_pipeline_graph import (
    _CANDIDATE,
    _FAKE_DOWNLOAD_RESULT,
    _FAKE_RELEASE,
    _INITIAL_STATE,
    _INTENT,
    _SCORED,
    _SETTINGS,
)


def _make_graph(mem):
    return create_graph(MagicMock(), _SETTINGS, mem)


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


@patch("app.pipeline.graph.evaluate_candidates", return_value=[_SCORED])
@patch("app.pipeline.graph.search_candidates", return_value=[_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_INTENT)
def test_stream_reaches_candidate_review_interrupt(mock_parse, mock_search, mock_eval):
    with SqliteSaver.from_conn_string(":memory:") as mem:
        graph = _make_graph(mem)
        config = _config("es1")

        events = list(stream_pipeline_events(graph, _INITIAL_STATE, config))

        assert [type(event) for event in events] == [
            StatusEvent,
            StatusEvent,
            StatusEvent,
            InterruptEvent,
        ]
        assert [event.node for event in events[:3]] == [
            "parse_request",
            "search_candidates",
            "evaluate_candidates",
        ]

        interrupt_event = events[-1]
        assert interrupt_event.type == "interrupt"
        assert interrupt_event.interrupt_type == "candidate_review"
        assert interrupt_event.payload["candidates"] == [_SCORED]


@patch("app.pipeline.graph.inject_metadata", return_value=_FAKE_DOWNLOAD_RESULT)
@patch("app.pipeline.graph.fetch_artwork", return_value=None)
@patch("app.pipeline.graph.get_discogs_client")
@patch("app.pipeline.graph.download_track", return_value=_FAKE_DOWNLOAD_RESULT)
@patch("app.pipeline.graph.evaluate_candidates", return_value=[_SCORED])
@patch("app.pipeline.graph.search_candidates", return_value=[_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_INTENT)
def test_stream_resume_runs_through_to_completion(
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
        config = _config("es2")

        # Drive the run up to the interrupt first.
        list(stream_pipeline_events(graph, _INITIAL_STATE, config))

        resume_events = list(
            stream_pipeline_events(graph, Command(resume=_CANDIDATE.url), config)
        )

        assert [type(event) for event in resume_events] == [
            StatusEvent,
            StatusEvent,
            StatusEvent,
            DoneEvent,
        ]
        assert [event.node for event in resume_events[:3]] == [
            "candidate_review",
            "download",
            "inject_metadata",
        ]

        done_event = resume_events[-1]
        assert done_event.type == "done"
        assert done_event.payload["download_result"] == _FAKE_DOWNLOAD_RESULT
        assert done_event.payload["selected_candidate_url"] == _CANDIDATE.url


@patch("app.pipeline.graph.evaluate_candidates", return_value=[])
@patch("app.pipeline.graph.search_candidates", return_value=[])
@patch("app.pipeline.graph.parse_request", return_value=_INTENT)
def test_stream_emits_done_when_search_exhausted(mock_parse, mock_search, mock_eval):
    with SqliteSaver.from_conn_string(":memory:") as mem:
        graph = _make_graph(mem)
        config = _config("es3")

        events = list(stream_pipeline_events(graph, _INITIAL_STATE, config))

        # No viable candidates -> the run ends (no interrupt) -> a single DoneEvent
        # terminates the stream rather than hanging.
        assert isinstance(events[-1], DoneEvent)
        assert not any(isinstance(event, InterruptEvent) for event in events)


@pytest.fixture
def api_client():
    import main as app_module

    with SqliteSaver.from_conn_string(":memory:") as mem:
        app_module.app.state.checkpointer = mem
        with TestClient(app_module.app) as client:
            yield client


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data:") :].strip())
        for frame in body.split("\n\n")
        for line in frame.splitlines()
        if line.startswith("data:")
    ]


@patch("app.pipeline.graph.evaluate_candidates", return_value=[_SCORED])
@patch("app.pipeline.graph.search_candidates", return_value=[_CANDIDATE])
@patch("app.pipeline.graph.parse_request", return_value=_INTENT)
@patch("main.get_llm", return_value=MagicMock())
def test_download_stream_endpoint_emits_candidate_review_frame(
    mock_llm, mock_parse, mock_search, mock_eval, api_client
):
    response = api_client.post(
        "/download/stream", json={"request": "Daft Punk One More Time"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    assert events[-1]["type"] == "interrupt"
    assert events[-1]["interrupt_type"] == "candidate_review"
    assert "thread_id" in events[-1]["payload"]
    assert len(events[-1]["payload"]["candidates"]) == 1
