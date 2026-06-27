from pydantic import BaseModel


class Candidate(BaseModel):
    url: str
    title: str
    duration_seconds: float
    channel_name: str
    view_count: int
    is_playlist: bool = False
    thumbnail_url: str | None = None
