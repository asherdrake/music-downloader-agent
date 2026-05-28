from langchain_core.language_models import BaseChatModel

from app.core.config import get_settings


def get_llm() -> BaseChatModel:
    settings = get_settings()
    provider = settings.llm_provider
    model_name = settings.llm_model or None

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model_name or "claude-haiku-4-5-20251001",
            api_key=settings.anthropic_api_key or None,  # type: ignore[arg-type]
        )
    if provider == "openai":
        from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]

        return ChatOpenAI(
            model=model_name or "gpt-4o-mini",
            api_key=settings.openai_api_key or None,  # type: ignore[arg-type]
        )
    # provider == "google"
    from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[import-not-found]

    return ChatGoogleGenerativeAI(
        model=model_name or "gemini-1.5-flash",
        google_api_key=settings.google_api_key or None,  # type: ignore[arg-type]
    )
