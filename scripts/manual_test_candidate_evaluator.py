from app.models.candidate import Candidate
from app.models.download_intent import DownloadIntent
from scripts.manual_test_candidate_search import candidates
from app.pipeline.candidate_evaluator import evaluate_candidates


intent = DownloadIntent(
    target_type="Album",
    artist="tricot",
    title="3",
)


def print_candidates(candidates: list[Candidate]) -> None:
    def format_duration(duration_seconds: float) -> str:
        total_seconds = int(duration_seconds)
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes}:{seconds:02d}"

    print(f"Candidates ({len(candidates)}):")
    if not candidates:
        print("  (none)")
        return
    for index, candidate in enumerate(candidates, start=1):
        duration = format_duration(candidate.duration_seconds)
        print(f"  [{index}] {candidate.title}")
        print(f"      URL:      {candidate.url}")
        print(f"      Channel:  {candidate.channel_name}")
        print(f"      Duration: {duration}  |  Views: {candidate.view_count:,}")


print_candidates(candidates)

scored_candidates = evaluate_candidates(candidates, intent)
print("======= SCORED =========")
print_candidates(scored_candidates)
