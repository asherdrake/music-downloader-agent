from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.llm import get_llm
from app.models.download_intent import DownloadIntent
from app.pipeline.request_parser import parse_request
from main import app

client = TestClient(app)


def _mock_model(intent: DownloadIntent) -> MagicMock:
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = intent
    mock_model = MagicMock()
    mock_model.with_structured_output.return_value = mock_chain
    return mock_model


def test_plain_track_request() -> None:
    expected = DownloadIntent(
        target_type="Track",
        artist="Pink Floyd",
        title="Comfortably Numb",
    )
    result = parse_request("Download Comfortably Numb by Pink Floyd", _mock_model(expected))
    assert result == expected


def test_album_request() -> None:
    expected = DownloadIntent(
        target_type="Album",
        artist="Pink Floyd",
        title="The Dark Side of the Moon",
    )
    result = parse_request(
        "I want the full album The Dark Side of the Moon by Pink Floyd",
        _mock_model(expected),
    )
    assert result == expected
    assert result.target_type == "Album"


def test_request_with_resource_hint() -> None:
    url = "https://www.youtube.com/watch?v=abc123"
    expected = DownloadIntent(
        target_type="Track",
        artist="Radiohead",
        title="Creep",
        resource_hint=url,
    )
    result = parse_request(
        f"Download Creep by Radiohead from {url}",
        _mock_model(expected),
    )
    assert result.resource_hint == url


def test_request_with_playlist_actions() -> None:
    expected = DownloadIntent(
        target_type="Track",
        artist="Joy Division",
        title="Love Will Tear Us Apart",
        playlist_actions=["80s Classics", "Post-Punk"],
    )
    result = parse_request(
        "Add Love Will Tear Us Apart by Joy Division to my 80s Classics and Post-Punk playlists",
        _mock_model(expected),
    )
    assert result.playlist_actions == ["80s Classics", "Post-Punk"]


def test_ambiguous_title() -> None:
    expected = DownloadIntent(
        target_type="Track",
        artist="The Beatles",
        title="Come Together",
    )
    result = parse_request("that Beatles song come together", _mock_model(expected))
    assert result.artist == "The Beatles"
    assert result.title == "Come Together"


def test_download_endpoint_returns_serialised_intent() -> None:
    expected = DownloadIntent(
        target_type="Track",
        artist="The Cure",
        title="Lovesong",
    )
    app.dependency_overrides[get_llm] = lambda: _mock_model(expected)
    try:
        response = client.post("/download", json={"request": "Download Lovesong by The Cure"})
        assert response.status_code == 200
        data = response.json()
        assert data["target_type"] == "Track"
        assert data["artist"] == "The Cure"
        assert data["title"] == "Lovesong"
        assert data["resource_hint"] is None
        assert data["playlist_actions"] == []
    finally:
        app.dependency_overrides.clear()
