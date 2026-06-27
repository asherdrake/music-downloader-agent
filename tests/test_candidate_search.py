from unittest.mock import MagicMock, patch

from app.models.candidate import Candidate
from app.models.download_intent import DownloadIntent
from app.pipeline.candidate_search import search_candidates

_FAKE_ENTRIES = [
    {
        "id": "abc123",
        "url": "https://www.youtube.com/watch?v=abc123",
        "title": "Pink Floyd - Comfortably Numb",
        "duration": 382,
        "channel": "Pink Floyd Official",
        "view_count": 50000000,
        "thumbnails": [
            {"url": "https://i.ytimg.com/vi/abc123/default.jpg"},
            {"url": "https://i.ytimg.com/vi/abc123/hqdefault.jpg"},
        ],
    },
    {
        "id": "def456",
        "url": "https://www.youtube.com/watch?v=def456",
        "title": "Comfortably Numb - Pink Floyd (Lyrics)",
        "duration": 380,
        "channel": "Lyrics Channel",
        "view_count": 8000000,
    },
]

_FAKE_VIDEO_INFO = {
    "id": "xyz789",
    "title": "Pink Floyd - Comfortably Numb (Official Audio)",
    "duration": 382,
    "channel": "Pink Floyd Official",
    "view_count": 12000000,
    "thumbnail": "https://i.ytimg.com/vi/xyz789/maxresdefault.jpg",
}


def _make_plain_intent() -> DownloadIntent:
    return DownloadIntent(
        target_type="Track", artist="Pink Floyd", title="Comfortably Numb"
    )


def _make_hint_intent(url: str) -> DownloadIntent:
    return DownloadIntent(
        target_type="Track",
        artist="Pink Floyd",
        title="Comfortably Numb",
        resource_hint=url,
    )


def test_search_returns_candidates_for_plain_intent() -> None:
    intent = _make_plain_intent()
    with patch("app.pipeline.candidate_search.yt_dlp.YoutubeDL") as MockYDL:
        mock_ydl = MagicMock()
        MockYDL.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"entries": _FAKE_ENTRIES}

        candidates = search_candidates(intent)

    assert len(candidates) == 2
    first = candidates[0]
    assert isinstance(first, Candidate)
    assert first.url == "https://www.youtube.com/watch?v=abc123"
    assert first.title == "Pink Floyd - Comfortably Numb"
    assert first.duration_seconds == 382.0
    assert first.channel_name == "Pink Floyd Official"
    assert first.view_count == 50000000
    # Highest-resolution thumbnail (last in the list) is selected.
    assert first.thumbnail_url == "https://i.ytimg.com/vi/abc123/hqdefault.jpg"
    # Entry without thumbnails degrades gracefully to None.
    assert candidates[1].thumbnail_url is None

    call_args = mock_ydl.extract_info.call_args
    assert "ytsearch" in call_args[0][0]


def test_edition_is_appended_to_search_query() -> None:
    intent = DownloadIntent(
        target_type="Track",
        artist="tricot",
        title="The Danger of Not Mixing",
        edition="Zepp DiverCity live version",
    )
    with patch("app.pipeline.candidate_search.yt_dlp.YoutubeDL") as MockYDL:
        mock_ydl = MagicMock()
        MockYDL.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"entries": []}

        search_candidates(intent)

    query = mock_ydl.extract_info.call_args[0][0]
    assert "ytsearch" in query
    assert "tricot The Danger of Not Mixing Zepp DiverCity live version" in query


def test_resource_hint_bypasses_search() -> None:
    hint_url = "https://www.youtube.com/watch?v=xyz789"
    intent = _make_hint_intent(hint_url)

    with patch("app.pipeline.candidate_search.yt_dlp.YoutubeDL") as MockYDL:
        mock_ydl = MagicMock()
        MockYDL.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = _FAKE_VIDEO_INFO

        search_candidates(intent)

    call_args = mock_ydl.extract_info.call_args
    actual_url = call_args[0][0]
    assert actual_url == hint_url
    assert "ytsearch" not in actual_url


def test_resource_hint_returns_sole_candidate() -> None:
    hint_url = "https://www.youtube.com/watch?v=xyz789"
    intent = _make_hint_intent(hint_url)

    with patch("app.pipeline.candidate_search.yt_dlp.YoutubeDL") as MockYDL:
        mock_ydl = MagicMock()
        MockYDL.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = _FAKE_VIDEO_INFO

        candidates = search_candidates(intent)

    assert len(candidates) == 1
    sole = candidates[0]
    assert sole.url == hint_url
    assert sole.title == "Pink Floyd - Comfortably Numb (Official Audio)"
    assert sole.duration_seconds == 382.0
    assert sole.channel_name == "Pink Floyd Official"
    assert sole.view_count == 12000000
    # Singular `thumbnail` key from full extraction is used directly.
    assert sole.thumbnail_url == "https://i.ytimg.com/vi/xyz789/maxresdefault.jpg"


_PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLabc123"

_FAKE_PLAYLIST_SEARCH_ENTRY = {
    "id": "PLabc123",
    "ie_key": "YoutubeTab",
    "url": _PLAYLIST_URL,
    "title": "Riff-Rain - School Food Punishment",
    "channel": "School Food Punishment Topic",
    "view_count": 5000,
}

_FAKE_PLAYLIST_FLAT_ENTRIES = [
    {"id": "v1", "duration": 180.0},
    {"id": "v2", "duration": 240.0},
    {"id": "v3", "duration": 200.0},
]

_FAKE_MIX_ENTRY = {
    "id": "RDabc123",
    "ie_key": "YoutubeTab",
    "url": "https://www.youtube.com/watch?v=abc&list=RDabc123",
    "title": "Riff-Rain Mix",
    "channel": "YouTube Music",
    "view_count": 1000,
}


def _make_album_intent() -> DownloadIntent:
    return DownloadIntent(
        target_type="Album",
        artist="School Food Punishment",
        title="Riff-Rain",
    )


def test_playlist_included_for_album_intent() -> None:
    intent = _make_album_intent()

    def _fake_extract(url, **_kwargs):
        if "ytsearch" in url:
            return {"entries": []}  # ytsearch never returns playlists
        if "results?search_query" in url:
            return {"entries": [_FAKE_PLAYLIST_SEARCH_ENTRY]}
        return {"entries": _FAKE_PLAYLIST_FLAT_ENTRIES}  # duration fetch

    with patch("app.pipeline.candidate_search.yt_dlp.YoutubeDL") as MockYDL:
        mock_ydl = MagicMock()
        MockYDL.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = _fake_extract

        candidates = search_candidates(intent)

    assert len(candidates) == 1
    playlist = candidates[0]
    assert playlist.is_playlist is True
    assert playlist.url == _PLAYLIST_URL
    assert playlist.title == "Riff-Rain - School Food Punishment"
    assert playlist.duration_seconds == 620.0  # 180 + 240 + 200
    assert playlist.channel_name == "School Food Punishment Topic"


def test_playlist_excluded_for_track_intent() -> None:
    intent = _make_plain_intent()

    with patch("app.pipeline.candidate_search.yt_dlp.YoutubeDL") as MockYDL:
        mock_ydl = MagicMock()
        MockYDL.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"entries": [_FAKE_PLAYLIST_SEARCH_ENTRY]}

        candidates = search_candidates(intent)

    assert candidates == []


def test_youtube_mix_excluded_for_album_intent() -> None:
    intent = _make_album_intent()

    with patch("app.pipeline.candidate_search.yt_dlp.YoutubeDL") as MockYDL:
        mock_ydl = MagicMock()
        MockYDL.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"entries": [_FAKE_MIX_ENTRY]}

        candidates = search_candidates(intent)

    assert candidates == []


def test_video_candidate_has_is_playlist_false() -> None:
    intent = _make_plain_intent()

    with patch("app.pipeline.candidate_search.yt_dlp.YoutubeDL") as MockYDL:
        mock_ydl = MagicMock()
        MockYDL.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"entries": _FAKE_ENTRIES}

        candidates = search_candidates(intent)

    assert all(not c.is_playlist for c in candidates)
