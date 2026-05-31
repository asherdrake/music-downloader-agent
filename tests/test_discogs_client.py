from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from app.clients.discogs import DiscogsAPIError, DiscogsClient, _parse_duration
from app.models.download_intent import DownloadIntent
from app.models.release import Release

# ---------------------------------------------------------------------------
# Fake API response data
# ---------------------------------------------------------------------------

_FAKE_SEARCH_RESULTS: dict[str, Any] = {
    "results": [
        {"id": 101, "year": 1979, "title": "Pink Floyd - The Wall"},
        {"id": 202, "year": 1990, "title": "Pink Floyd - The Wall (Remaster)"},
    ]
}

_FAKE_RELEASE_DETAIL: dict[str, Any] = {
    "id": 101,
    "title": "The Wall",
    "year": 1979,
    "artists": [{"name": "Pink Floyd"}],
    "genres": ["Rock"],
    "images": [
        {"type": "primary", "uri": "https://img.discogs.com/cover.jpg"},
    ],
    "tracklist": [
        {
            "type_": "track",
            "position": "A1",
            "title": "In The Flesh?",
            "duration": "3:16",
        },
        {"type_": "heading", "position": "", "title": "Side B", "duration": ""},
        {
            "type_": "track",
            "position": "B1",
            "title": "Comfortably Numb",
            "duration": "6:22",
        },
        {
            "type_": "track",
            "position": "B2",
            "title": "The Show Must Go On",
            "duration": "1:36",
        },
    ],
}

_FAKE_RELEASE_NO_IMAGES: dict[str, Any] = {
    **_FAKE_RELEASE_DETAIL,
    "images": [],
}

_FAKE_RELEASE_NO_PRIMARY_IMAGE: dict[str, Any] = {
    **_FAKE_RELEASE_DETAIL,
    "images": [
        {"type": "secondary", "uri": "https://img.discogs.com/back.jpg"},
    ],
}

_FAKE_SEARCH_EMPTY: dict[str, Any] = {"results": []}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(json_data: dict[str, Any]) -> MagicMock:
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = json_data
    return mock_response


def _make_client() -> DiscogsClient:
    return DiscogsClient(api_token="test-token")


# ---------------------------------------------------------------------------
# Release selection
# ---------------------------------------------------------------------------


def test_fetch_release_selects_earliest_year() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.side_effect = [
        _make_mock_response(_FAKE_SEARCH_RESULTS),
        _make_mock_response(_FAKE_RELEASE_DETAIL),
    ]

    client.fetch_release(artist="Pink Floyd", title="The Wall")

    second_call_args = client._http_client.get.call_args_list[1]
    assert "/releases/101" in second_call_args[0][0]


# ---------------------------------------------------------------------------
# Edition hint
# ---------------------------------------------------------------------------


def test_fetch_release_includes_edition_in_search_params() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.side_effect = [
        _make_mock_response(_FAKE_SEARCH_RESULTS),
        _make_mock_response(_FAKE_RELEASE_DETAIL),
    ]

    client.fetch_release(artist="Pink Floyd", title="The Wall", edition="2011 remaster")

    search_call_kwargs = client._http_client.get.call_args_list[0][1]
    assert search_call_kwargs["params"]["q"] == "2011 remaster"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_fetch_release_raises_on_empty_results() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.return_value = _make_mock_response(_FAKE_SEARCH_EMPTY)

    with pytest.raises(DiscogsAPIError) as exc_info:
        client.fetch_release(artist="Unknown Artist", title="Unknown Title")

    assert exc_info.value.status_code == 404


def test_fetch_release_raises_on_http_error() -> None:
    client = _make_client()
    mock_response = MagicMock()
    mock_response.status_code = 503
    http_error = httpx.HTTPStatusError(
        "Service Unavailable",
        request=MagicMock(),
        response=mock_response,
    )
    mock_get_response = MagicMock()
    mock_get_response.raise_for_status.side_effect = http_error
    client._http_client = MagicMock()
    client._http_client.get.return_value = mock_get_response

    with pytest.raises(DiscogsAPIError) as exc_info:
        client.fetch_release(artist="Pink Floyd", title="The Wall")

    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------------


def test_fetch_release_maps_all_fields_correctly() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.side_effect = [
        _make_mock_response(_FAKE_SEARCH_RESULTS),
        _make_mock_response(_FAKE_RELEASE_DETAIL),
    ]

    release = client.fetch_release(artist="Pink Floyd", title="The Wall")

    assert isinstance(release, Release)
    assert release.artist == "Pink Floyd"
    assert release.album_title == "The Wall"
    assert release.year == 1979
    assert release.genres == ["Rock"]
    assert release.artwork_url == "https://img.discogs.com/cover.jpg"
    assert len(release.tracklist) == 3
    assert release.tracklist[0].title == "In The Flesh?"
    assert release.tracklist[0].duration_seconds == 196.0
    assert release.tracklist[1].title == "Comfortably Numb"
    assert release.tracklist[1].duration_seconds == 382.0


def test_map_release_skips_heading_entries() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.side_effect = [
        _make_mock_response(_FAKE_SEARCH_RESULTS),
        _make_mock_response(_FAKE_RELEASE_DETAIL),
    ]

    release = client.fetch_release(artist="Pink Floyd", title="The Wall")

    heading_entries = [t for t in release.tracklist if t.title == "Side B"]
    assert heading_entries == []


# ---------------------------------------------------------------------------
# Artwork fallback
# ---------------------------------------------------------------------------


def test_fetch_release_artwork_fallback_to_first_image() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.side_effect = [
        _make_mock_response(_FAKE_SEARCH_RESULTS),
        _make_mock_response(_FAKE_RELEASE_NO_PRIMARY_IMAGE),
    ]

    release = client.fetch_release(artist="Pink Floyd", title="The Wall")

    assert release.artwork_url == "https://img.discogs.com/back.jpg"


def test_fetch_release_artwork_none_when_no_images() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.side_effect = [
        _make_mock_response(_FAKE_SEARCH_RESULTS),
        _make_mock_response(_FAKE_RELEASE_NO_IMAGES),
    ]

    release = client.fetch_release(artist="Pink Floyd", title="The Wall")

    assert release.artwork_url is None


# ---------------------------------------------------------------------------
# _parse_duration unit tests
# ---------------------------------------------------------------------------


def test_parse_duration_standard_mm_ss() -> None:
    assert _parse_duration("3:45") == 225.0


def test_parse_duration_hh_mm_ss() -> None:
    assert _parse_duration("1:02:30") == 3750.0


def test_parse_duration_empty_returns_none() -> None:
    assert _parse_duration("") is None


def test_parse_duration_whitespace_returns_none() -> None:
    assert _parse_duration("   ") is None


def test_parse_duration_malformed_returns_none() -> None:
    assert _parse_duration("-") is None
    assert _parse_duration("?") is None


# ---------------------------------------------------------------------------
# DownloadIntent edition field
# ---------------------------------------------------------------------------


def test_download_intent_accepts_edition_field() -> None:
    intent = DownloadIntent(
        target_type="Album",
        artist="Pink Floyd",
        title="The Wall",
        edition="deluxe edition",
    )
    dumped = intent.model_dump()
    assert dumped["edition"] == "deluxe edition"


def test_download_intent_edition_defaults_to_none() -> None:
    intent = DownloadIntent(
        target_type="Track",
        artist="Radiohead",
        title="Creep",
    )
    assert intent.edition is None
