from pydantic import BaseModel, Field


class TrackListing(BaseModel):
    position: str
    title: str
    duration_seconds: float | None = None


class Release(BaseModel):
    discogs_id: int
    artist: str
    album_title: str
    year: int
    genres: list[str] = Field(default_factory=list)
    artwork_url: str | None = None
    tracklist: list[TrackListing] = Field(default_factory=list)
