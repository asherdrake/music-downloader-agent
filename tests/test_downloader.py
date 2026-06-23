from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models.download_intent import DownloadIntent
from app.pipeline.downloader import (
    _expected_path,
    _sanitize,
    download_playlist,
    download_track,
    run_download,
)

_BASE = Path("/tmp/music")

_FAKE_PLAYLIST_INFO = {
    "entries": [
        {
            "id": "t1",
            "url": "https://www.youtube.com/watch?v=t1",
            "title": "In the Flesh?",
            "playlist_index": 1,
        },
        {
            "id": "t2",
            "url": "https://www.youtube.com/watch?v=t2",
            "title": "The Thin Ice",
            "playlist_index": 2,
        },
    ]
}


def _track_intent() -> DownloadIntent:
    return DownloadIntent(target_type="Track", artist="Pink Floyd", title="Comfortably Numb")


def _album_intent() -> DownloadIntent:
    return DownloadIntent(target_type="Album", artist="Pink Floyd", title="The Wall")


# --- _sanitize ---


def test_sanitize_strips_forbidden_chars() -> None:
    assert _sanitize('AC/DC: "Back in Black"') == "AC_DC_ _Back in Black_"


def test_sanitize_strips_leading_trailing_whitespace() -> None:
    assert _sanitize("  Title  ") == "Title"


# --- _expected_path ---


def test_expected_path_track_layout() -> None:
    path = _expected_path(_BASE, "Pink Floyd", "Comfortably Numb", 1, "Comfortably Numb", "mp3")
    assert path == _BASE / "Pink Floyd" / "Comfortably Numb" / "01 - Comfortably Numb.mp3"


def test_expected_path_album_track_layout() -> None:
    path = _expected_path(_BASE, "Pink Floyd", "The Wall", 12, "Comfortably Numb", "mp3")
    assert path == _BASE / "Pink Floyd" / "The Wall" / "12 - Comfortably Numb.mp3"


def test_expected_path_uses_m4a_extension() -> None:
    path = _expected_path(_BASE, "Pink Floyd", "The Wall", 1, "In the Flesh?", "m4a")
    assert path.suffix == ".m4a"


def test_expected_path_sanitizes_artist_and_title() -> None:
    path = _expected_path(_BASE, "AC/DC", "Back in Black", 1, "You Shook Me All Night Long", "mp3")
    assert "AC_DC" in str(path)


# --- download_track ---


@patch("app.pipeline.downloader.yt_dlp.YoutubeDL")
def test_track_download_calls_ydl_download(MockYDL) -> None:
    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl
    intent = _track_intent()

    with patch.object(Path, "exists", return_value=False), patch.object(
        Path, "mkdir", return_value=None
    ):
        result = download_track("https://yt.com/v=abc", intent, _BASE)

    mock_ydl.download.assert_called_once_with(["https://yt.com/v=abc"])
    assert result.downloaded_count == 1
    assert result.skipped_count == 0
    assert not result.tracks[0].skipped


@patch("app.pipeline.downloader.yt_dlp.YoutubeDL")
def test_track_download_uses_mp3_by_default(MockYDL) -> None:
    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl
    intent = _track_intent()

    with patch.object(Path, "exists", return_value=False), patch.object(
        Path, "mkdir", return_value=None
    ):
        download_track("https://yt.com/v=abc", intent, _BASE)

    opts_used = MockYDL.call_args[0][0]
    codec = opts_used["postprocessors"][0]["preferredcodec"]
    assert codec == "mp3"


@patch("app.pipeline.downloader.yt_dlp.YoutubeDL")
def test_track_download_uses_m4a_when_flag_set(MockYDL) -> None:
    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl
    intent = _track_intent()

    with patch.object(Path, "exists", return_value=False), patch.object(
        Path, "mkdir", return_value=None
    ):
        download_track("https://yt.com/v=abc", intent, _BASE, use_m4a=True)

    opts_used = MockYDL.call_args[0][0]
    codec = opts_used["postprocessors"][0]["preferredcodec"]
    assert codec == "m4a"


@patch("app.pipeline.downloader.yt_dlp.YoutubeDL")
def test_track_download_skips_when_file_exists(MockYDL) -> None:
    intent = _track_intent()

    with patch.object(Path, "exists", return_value=True):
        result = download_track("https://yt.com/v=abc", intent, _BASE)

    MockYDL.assert_not_called()
    assert result.skipped_count == 1
    assert result.downloaded_count == 0
    assert result.tracks[0].skipped is True


@patch("app.pipeline.downloader.yt_dlp.YoutubeDL")
def test_track_result_has_track_number_01(MockYDL) -> None:
    mock_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_ydl
    intent = _track_intent()

    with patch.object(Path, "exists", return_value=False), patch.object(
        Path, "mkdir", return_value=None
    ):
        result = download_track("https://yt.com/v=abc", intent, _BASE)

    assert result.tracks[0].track_number == 1
    assert "01 -" in str(result.tracks[0].path)


# --- download_playlist ---


@patch("app.pipeline.downloader.yt_dlp.YoutubeDL")
def test_playlist_download_downloads_all_tracks(MockYDL) -> None:
    mock_info_ydl = MagicMock()
    mock_dl_ydl_1 = MagicMock()
    mock_dl_ydl_2 = MagicMock()
    MockYDL.return_value.__enter__.side_effect = [mock_info_ydl, mock_dl_ydl_1, mock_dl_ydl_2]
    mock_info_ydl.extract_info.return_value = _FAKE_PLAYLIST_INFO
    intent = _album_intent()

    with patch.object(Path, "exists", return_value=False), patch.object(
        Path, "mkdir", return_value=None
    ):
        result = download_playlist("https://yt.com/playlist=abc", intent, _BASE)

    assert result.downloaded_count == 2
    assert result.skipped_count == 0
    assert len(result.tracks) == 2


@patch("app.pipeline.downloader.yt_dlp.YoutubeDL")
def test_playlist_download_skips_existing_tracks(MockYDL) -> None:
    mock_info_ydl = MagicMock()
    MockYDL.return_value.__enter__.return_value = mock_info_ydl
    mock_info_ydl.extract_info.return_value = _FAKE_PLAYLIST_INFO
    intent = _album_intent()

    with patch.object(Path, "exists", return_value=True):
        result = download_playlist("https://yt.com/playlist=abc", intent, _BASE)

    mock_info_ydl.download.assert_not_called()
    assert result.skipped_count == 2
    assert result.downloaded_count == 0


@patch("app.pipeline.downloader.yt_dlp.YoutubeDL")
def test_playlist_download_uses_playlist_index_as_track_number(MockYDL) -> None:
    mock_info_ydl = MagicMock()
    MockYDL.return_value.__enter__.side_effect = [mock_info_ydl, MagicMock(), MagicMock()]
    mock_info_ydl.extract_info.return_value = _FAKE_PLAYLIST_INFO
    intent = _album_intent()

    with patch.object(Path, "exists", return_value=False), patch.object(
        Path, "mkdir", return_value=None
    ):
        result = download_playlist("https://yt.com/playlist=abc", intent, _BASE)

    numbers = [t.track_number for t in result.tracks]
    assert numbers == [1, 2]


@patch("app.pipeline.downloader.yt_dlp.YoutubeDL")
def test_playlist_all_tracks_use_same_format(MockYDL) -> None:
    mock_info_ydl = MagicMock()
    MockYDL.return_value.__enter__.side_effect = [mock_info_ydl, MagicMock(), MagicMock()]
    mock_info_ydl.extract_info.return_value = _FAKE_PLAYLIST_INFO
    intent = _album_intent()

    with patch.object(Path, "exists", return_value=False), patch.object(
        Path, "mkdir", return_value=None
    ):
        result = download_playlist("https://yt.com/playlist=abc", intent, _BASE, use_m4a=True)

    suffixes = {t.path.suffix for t in result.tracks}
    assert suffixes == {".m4a"}


@patch("app.pipeline.downloader.yt_dlp.YoutubeDL")
def test_playlist_partial_skip_counts_correctly(MockYDL) -> None:
    mock_info_ydl = MagicMock()
    MockYDL.return_value.__enter__.side_effect = [mock_info_ydl, MagicMock()]
    mock_info_ydl.extract_info.return_value = _FAKE_PLAYLIST_INFO
    intent = _album_intent()

    # Track 1 ("In the Flesh?") exists; track 2 ("The Thin Ice") does not.
    def _exists(self: Path) -> bool:
        return "In the Flesh" in self.name

    with patch.object(Path, "exists", _exists), patch.object(Path, "mkdir", return_value=None):
        result = download_playlist("https://yt.com/playlist=abc", intent, _BASE)

    assert result.skipped_count == 1
    assert result.downloaded_count == 1


# --- run_download router ---


@patch("app.pipeline.downloader.download_playlist")
@patch("app.pipeline.downloader.download_track")
def test_run_download_routes_track_intent(mock_track, mock_playlist) -> None:
    intent = _track_intent()
    run_download("https://yt.com/v=abc", intent, _BASE)
    mock_track.assert_called_once()
    mock_playlist.assert_not_called()


@patch("app.pipeline.downloader.download_playlist")
@patch("app.pipeline.downloader.download_track")
def test_run_download_routes_album_intent(mock_track, mock_playlist) -> None:
    intent = _album_intent()
    run_download("https://yt.com/playlist=abc", intent, _BASE)
    mock_playlist.assert_called_once()
    mock_track.assert_not_called()
