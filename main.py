from typing import Annotated

from fastapi import Depends, FastAPI
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.llm import get_llm
from app.models.download_intent import DownloadIntent
from app.pipeline.request_parser import parse_request

get_settings()

app = FastAPI(
    title="Music Downloader Agent",
    version="0.1.0",
)


class DownloadRequest(BaseModel):
    request: str


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/download")
def download(
    body: DownloadRequest,
    model: Annotated[BaseChatModel, Depends(get_llm)],
) -> DownloadIntent:
    return parse_request(body.request, model)
