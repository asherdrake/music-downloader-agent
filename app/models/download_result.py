from pathlib import Path

from pydantic import BaseModel


class TrackResult(BaseModel):
    track_number: int
    title: str
    path: Path
    skipped: bool


class DownloadResult(BaseModel):
    url: str
    use_m4a: bool
    tracks: list[TrackResult]
    downloaded_count: int
    skipped_count: int
