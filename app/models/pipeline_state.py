from typing import NotRequired, TypedDict

from app.models.candidate import Candidate
from app.models.download_intent import DownloadIntent
from app.models.scored_candidate import ScoredCandidate


class PipelineState(TypedDict):
    request: str
    download_intent: NotRequired[DownloadIntent | None]
    candidates: NotRequired[list[Candidate]]
    scored_candidates: NotRequired[list[ScoredCandidate]]
    search_iteration: NotRequired[int]
    selected_candidate_url: NotRequired[str | None]
