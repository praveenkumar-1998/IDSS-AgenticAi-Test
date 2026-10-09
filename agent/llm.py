import os

from langchain_core.language_models import BaseChatModel

DEFAULT_MODELS = {
    "ollama": "llama3.2:3b",
    "gemini": "gemini-flash-latest",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-5-5",
}

# Older names from the first version of this project's .env
LEGACY_MODEL_VARS = {
    "ollama": "OLLAMA_MODEL",
    "gemini": "GEMINI_MODEL",
    "anthropic": "AGENT_MODEL",
    "openai": "OPENAI_MODEL",
}


def model_name(provider: str, override: str | None = None) -> str:
    return override or os.getenv(LEGACY_MODEL_VARS.get(provider, ""), "") or DEFAULT_MODELS[provider]


def build_chat_model(provider: str, model: str | None = None, temperature: float = 0.0) -> BaseChatModel:
    """Providers are imported lazily so a missing optional package only matters
    when that provider is actually selected."""
    if provider not in DEFAULT_MODELS:
        raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Use one of: {', '.join(DEFAULT_MODELS)}")
    name = model_name(provider, model)

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=name,
            base_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
            temperature=temperature,
            num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "8192")),
            # qwen3 and friends think out loud by default; on a laptop CPU that
            # turns a 5s answer into a 60s one.
            reasoning=os.getenv("OLLAMA_THINK", "false").lower() == "true",
            keep_alive="30m",
        )
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("LLM_PROVIDER=gemini but GEMINI_API_KEY is not set.")
        return ChatGoogleGenerativeAI(model=name, google_api_key=key, temperature=temperature, max_retries=2)
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set.")
        return ChatOpenAI(model=name, temperature=temperature, max_retries=2, timeout=60)

    from langchain_anthropic import ChatAnthropic
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set.")
    return ChatAnthropic(model=name, temperature=temperature, max_retries=2, max_tokens=4096, timeout=60)
