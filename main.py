from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.llm import get_llm
from app.models.scored_candidate import ScoredCandidate
from app.pipeline.graph import create_graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.checkpoint_db_path.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(settings.checkpoint_db_path)) as checkpointer:
        app.state.checkpointer = checkpointer
        yield


app = FastAPI(
    title="Music Downloader Agent",
    version="0.1.0",
    lifespan=lifespan,
)


class DownloadRequest(BaseModel):
    request: str


class CandidateReviewResponse(BaseModel):
    status: Literal["candidate_review"]
    thread_id: str
    candidates: list[ScoredCandidate]


class SearchExhaustedResponse(BaseModel):
    status: Literal["search_exhausted"]
    thread_id: str


class ResumeRequest(BaseModel):
    thread_id: str
    selected_candidate_url: str


class ResumeCompleteResponse(BaseModel):
    status: Literal["completed"]
    thread_id: str
    selected_candidate_url: str


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/download")
def download(
    body: DownloadRequest,
    request: Request,
    model: Annotated[BaseChatModel, Depends(get_llm)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CandidateReviewResponse | SearchExhaustedResponse:
    thread_id = str(uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    graph = create_graph(model, settings, request.app.state.checkpointer)
    graph.invoke(
        {
            "request": body.request,
            "download_intent": None,
            "candidates": [],
            "scored_candidates": [],
            "search_iteration": 0,
            "selected_candidate_url": None,
        },
        config,
    )
    snapshot = graph.get_state(config)
    if snapshot.next:
        interrupt_value = snapshot.tasks[0].interrupts[0].value
        return CandidateReviewResponse(
            status="candidate_review",
            thread_id=thread_id,
            candidates=interrupt_value["candidates"],
        )
    return SearchExhaustedResponse(status="search_exhausted", thread_id=thread_id)


@app.post("/resume")
def resume(
    body: ResumeRequest,
    request: Request,
    model: Annotated[BaseChatModel, Depends(get_llm)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ResumeCompleteResponse | CandidateReviewResponse | SearchExhaustedResponse:
    config = {"configurable": {"thread_id": body.thread_id}}
    graph = create_graph(model, settings, request.app.state.checkpointer)
    snapshot = graph.get_state(config)
    if not snapshot.next:
        raise HTTPException(status_code=404, detail="Thread not found or already completed")
    graph.invoke(Command(resume=body.selected_candidate_url), config)
    snapshot = graph.get_state(config)
    if snapshot.next:
        interrupt_value = snapshot.tasks[0].interrupts[0].value
        return CandidateReviewResponse(
            status="candidate_review",
            thread_id=body.thread_id,
            candidates=interrupt_value["candidates"],
        )
    final_state = snapshot.values
    return ResumeCompleteResponse(
        status="completed",
        thread_id=body.thread_id,
        selected_candidate_url=final_state.get("selected_candidate_url", ""),
    )
