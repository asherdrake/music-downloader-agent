from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from app.core.config import Settings
from app.models.pipeline_state import PipelineState
from app.pipeline.candidate_evaluator import evaluate_candidates
from app.pipeline.candidate_search import search_candidates
from app.pipeline.downloader import run_download
from app.pipeline.request_parser import parse_request

_MAX_SEARCH_ITERATIONS: int = 3


def _route_after_evaluate(state: PipelineState) -> str:
    if state.get("scored_candidates"):
        return "candidate_review"
    if state.get("search_iteration", 0) >= _MAX_SEARCH_ITERATIONS:
        return END
    return "search_candidates"


def create_graph(
    llm: BaseChatModel,
    settings: Settings,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    def parse_request_node(state: PipelineState) -> dict:
        download_intent = parse_request(state["request"], llm)
        return {"download_intent": download_intent}

    def search_candidates_node(state: PipelineState) -> dict:
        candidates = search_candidates(state["download_intent"])
        return {
            "candidates": candidates,
            "search_iteration": state.get("search_iteration", 0) + 1,
        }

    def evaluate_candidates_node(state: PipelineState) -> dict:
        scored = evaluate_candidates(
            state.get("candidates", []),
            state["download_intent"],
        )
        print(scored)
        return {"scored_candidates": scored}

    def candidate_review_node(state: PipelineState) -> dict:
        selected_url: str = interrupt(
            {"candidates": state.get("scored_candidates", [])}
        )
        return {"selected_candidate_url": selected_url}

    def download_node(state: PipelineState) -> dict:
        result = run_download(
            url=state["selected_candidate_url"],
            intent=state["download_intent"],
            local_files_directory=settings.local_files_directory,
            use_m4a=state.get("use_m4a", False),
        )
        return {"download_result": result}

    builder: StateGraph = StateGraph(PipelineState)
    builder.add_node("parse_request", parse_request_node)
    builder.add_node("search_candidates", search_candidates_node)
    builder.add_node("evaluate_candidates", evaluate_candidates_node)
    builder.add_node("candidate_review", candidate_review_node)
    builder.add_node("download", download_node)

    builder.set_entry_point("parse_request")
    builder.add_edge("parse_request", "search_candidates")
    builder.add_edge("search_candidates", "evaluate_candidates")
    builder.add_conditional_edges(
        "evaluate_candidates",
        _route_after_evaluate,
        {
            "candidate_review": "candidate_review",
            "search_candidates": "search_candidates",
            END: END,
        },
    )
    builder.add_edge("candidate_review", "download")
    builder.add_edge("download", END)

    return builder.compile(checkpointer=checkpointer)
