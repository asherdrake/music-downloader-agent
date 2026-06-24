import sys
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from langgraph.graph.state import CompiledStateGraph

from app.core.config import get_settings
from app.core.llm import get_llm
from app.models.candidate import Candidate
from app.models.download_intent import DownloadIntent, PlaylistAction
from app.models.pipeline_state import PipelineState
from app.models.scored_candidate import ScoredCandidate
from app.pipeline.graph import create_graph

# _INITIAL_STATE: PipelineState = {
#     "request": (
#         "Download the album 3 by tricot, create a playlist for it, set the image "
#         "to the album art, and add the correct tracks in the album order."
#     ),
#     "download_intent": None,
#     "candidates": [],
#     "scored_candidates": [],
#     "search_iteration": 0,
#     "selected_candidate_url": None,
# }

# _INITIAL_STATE: PipelineState = {
#     "request": (
#         "Download the album For You by tatsuro yamashita, create a playlist for it, set the image "
#         "to the album art, and add the correct tracks in the album order."
#     ),
#     "download_intent": None,
#     "candidates": [],
#     "scored_candidates": [],
#     "search_iteration": 0,
#     "selected_candidate_url": None,
# }

# _INITIAL_STATE: PipelineState = {
#     "request": (
#         "Download the album Riff-Rain by School Food Punishment, create a playlist for it, set the image "
#         "to the album art, and add the correct tracks in the album order."
#     ),
#     "download_intent": None,
#     "candidates": [],
#     "scored_candidates": [],
#     "search_iteration": 0,
#     "selected_candidate_url": None,
# }

# _INITIAL_STATE: PipelineState = {
#     "request": (
#         "Download the album Ten no Mikaku by Yuu, create a playlist for it, set the image "
#         "to the album art, and add the correct tracks in the album order."
#     ),
#     "download_intent": None,
#     "candidates": [],
#     "scored_candidates": [],
#     "search_iteration": 0,
#     "selected_candidate_url": None,
# }

_INITIAL_STATE: PipelineState = {
    "request": (
        "Download the track The Danger of Not Mixing by tricot, specifically the Zepp DiverCity live version. Add it to my local files playlist."
    ),
    "download_intent": None,
    "candidates": [],
    "scored_candidates": [],
    "search_iteration": 0,
    "selected_candidate_url": None,
}

_CONFIG = {"configurable": {"thread_id": "t1"}}


def _format_duration(duration_seconds: float) -> str:
    total_seconds = int(duration_seconds)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d}"


def _print_playlist_action(action: PlaylistAction, index: int) -> None:
    cover = action.set_cover_image or "(none)"
    print(f"    [{index}] {action.playlist_name}")
    print(f"        create_if_missing: {action.create_if_missing}")
    print(f"        set_cover_image:   {cover}")
    print(f"        track_order:       {action.track_order}")


def _print_download_intent(intent: DownloadIntent | None) -> None:
    print("Download intent:")
    if intent is None:
        print("  (none)")
        return
    print(f"  Target:        {intent.target_type}")
    print(f"  Artist:        {intent.artist}")
    print(f"  Title:         {intent.title}")
    print(f"  Resource hint: {intent.resource_hint or '(none)'}")
    print(f"  Edition:       {intent.edition or '(none)'}")
    if intent.playlist_actions:
        print(f"  Playlist actions ({len(intent.playlist_actions)}):")
        for index, action in enumerate(intent.playlist_actions, start=1):
            _print_playlist_action(action, index)
    else:
        print("  Playlist actions: (none)")


def _print_candidate(candidate: Candidate, index: int) -> None:
    duration = _format_duration(candidate.duration_seconds)
    print(f"    [{index}] {candidate.title}")
    print(f"        URL:      {candidate.url}")
    print(f"        Channel:  {candidate.channel_name}")
    print(f"        Duration: {duration}  |  Views: {candidate.view_count:,}")
    print(f"        IsPlaylist: {candidate.is_playlist}")


def _print_scored_candidate(candidate: ScoredCandidate, index: int) -> None:
    _print_candidate(candidate, index)
    print(
        f"        Scores:   title={candidate.title_match_score:.2f}  "
        f"duration={candidate.duration_match_score:.2f}  "
        f"quality={candidate.source_quality_score:.2f}  "
        f"confidence={candidate.confidence_score:.2f}   "
        f"IsPlaylist={candidate.is_playlist}"
    )


def _print_interrupts(interrupts: Any) -> None:
    print("Interrupts:")
    if not interrupts:
        print("  (none)")
        return
    for index, interrupt in enumerate(interrupts, start=1):
        value = getattr(interrupt, "value", interrupt)
        print(f"  [{index}] {value!r}")


def print_pipeline_state(state: PipelineState | dict[str, Any]) -> None:
    print("=" * 60)
    print("Pipeline state")
    print("=" * 60)
    print(f"Request:\n  {state.get('request', '(none)')}")
    print()
    _print_download_intent(state.get("download_intent"))
    print()
    print(f"Search iteration:      {state.get('search_iteration', 0)}")
    print(f"Selected candidate URL: {state.get('selected_candidate_url') or '(none)'}")
    print()

    candidates: list[Candidate] = state.get("candidates") or []
    print(f"Candidates ({len(candidates)}):")
    if candidates:
        for index, candidate in enumerate(candidates, start=1):
            _print_candidate(candidate, index)
    else:
        print("  (none)")
    print()

    scored: list[ScoredCandidate] = state.get("scored_candidates") or []
    print(f"Scored candidates ({len(scored)}):")
    if scored:
        for index, candidate in enumerate(scored, start=1):
            _print_scored_candidate(candidate, index)
    else:
        print("  (none)")

    if "__interrupt__" in state:
        print()
        _print_interrupts(state["__interrupt__"])
    print("=" * 60)


with SqliteSaver.from_conn_string(":memory:") as checkpointer:
    graph: CompiledStateGraph = create_graph(get_llm(), get_settings(), checkpointer)
    candidate_review_state: PipelineState = graph.invoke(_INITIAL_STATE, _CONFIG)
    print_pipeline_state(candidate_review_state)
    selected_url = candidate_review_state["__interrupt__"][0].value["candidates"][0].url
    result: PipelineState = graph.invoke(Command(resume=selected_url), _CONFIG)
    print_pipeline_state(result)
