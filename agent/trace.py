"""Append-only JSONL trace: one line per routing decision, tool call and turn.

Cheap enough to leave on in production and easy to ship to whatever log
pipeline is around. For token-level traces set LANGSMITH_TRACING=true; LangChain
picks that up without code changes.
"""
import json
import logging
import threading
import time
from pathlib import Path

from agent.http import request_id


class Tracer:
    def __init__(self, log_dir: Path, session: str):
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / "agent-trace.jsonl"
        self.session = session
        self._lock = threading.Lock()

    def __call__(self, event: str, **fields) -> None:
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "session": self.session,
                  "request_id": request_id.get(), "event": event, **fields}
        line = json.dumps(record, default=str, ensure_ascii=False)
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_dir / "agent.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    # httpx logs every request at INFO; that's noise next to our own trace
    logging.getLogger("httpx").setLevel(logging.WARNING)
