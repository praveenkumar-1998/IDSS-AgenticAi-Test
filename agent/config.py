import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class ServiceConfig:
    name: str
    url: str
    description: str = ""
    # operation names to hide from the agent, e.g. ["update_order"]
    exclude: list[str] = field(default_factory=list)
    # extra operations that need a human yes/no, on top of every DELETE
    confirm: list[str] = field(default_factory=list)


@dataclass
class Settings:
    provider: str
    model: str | None
    temperature: float
    services: list[ServiceConfig]
    read_only: bool
    confirm_writes: bool
    router_threshold: int
    max_steps: int
    history_messages: int
    max_tool_chars: int
    http_timeout: float
    log_dir: Path


def load_services(path: Path | None = None) -> list[ServiceConfig]:
    """Read services.toml. `<NAME>_SERVICE_URL` in the environment wins over the file,
    which keeps per-environment URLs (docker, k8s, staging) out of the repo."""
    path = path or Path(os.getenv("AGENT_SERVICES_FILE", ROOT / "services.toml"))
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    services = []
    for entry in raw.get("service", []):
        name = entry["name"]
        url = os.getenv(f"{name.upper()}_SERVICE_URL") or entry["url"]
        services.append(ServiceConfig(
            name=name,
            url=url.rstrip("/"),
            description=entry.get("description", ""),
            exclude=list(entry.get("exclude", [])),
            confirm=list(entry.get("confirm", [])),
        ))
    return services


def load_settings() -> Settings:
    return Settings(
        provider=os.getenv("LLM_PROVIDER", "ollama").strip().lower(),
        model=os.getenv("LLM_MODEL") or None,
        temperature=float(os.getenv("LLM_TEMPERATURE", "0")),
        services=load_services(),
        read_only=_bool("AGENT_READ_ONLY", False),
        confirm_writes=_bool("AGENT_CONFIRM_WRITES", False),
        router_threshold=int(os.getenv("AGENT_ROUTER_THRESHOLD", "30")),
        max_steps=int(os.getenv("AGENT_MAX_STEPS", "12")),
        history_messages=int(os.getenv("AGENT_HISTORY_MESSAGES", "30")),
        max_tool_chars=int(os.getenv("AGENT_MAX_TOOL_CHARS", "12000")),
        http_timeout=float(os.getenv("SERVICE_TIMEOUT", "10")),
        log_dir=Path(os.getenv("AGENT_LOG_DIR", ROOT / "logs")),
    )
