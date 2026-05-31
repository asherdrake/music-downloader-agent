from app.models.candidate import Candidate


class ScoredCandidate(Candidate):
    title_match_score: float
    duration_match_score: float
    source_quality_score: float
    confidence_score: float
