import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Set once per user turn so every downstream call can be correlated in service logs.
request_id: ContextVar[str] = ContextVar("request_id", default="-")

IDEMPOTENT = {"GET", "HEAD", "OPTIONS"}


class ServiceError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ServiceClient:
    """Thin httpx wrapper for one microservice.

    Only idempotent requests are retried. Retrying a POST after a timeout could
    create the same order twice, which is worse than surfacing the error.
    """

    def __init__(self, name: str, base_url: str, timeout: float = 10.0,
                 retries: int = 2, transport: httpx.BaseTransport | None = None):
        self.name = name
        self.base_url = base_url
        self.retries = retries
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

    def request(self, method: str, path: str, *, params: dict | None = None,
                json: Any = None) -> Any:
        method = method.upper()
        attempts = 1 + (self.retries if method in IDEMPOTENT else 0)
        for attempt in range(1, attempts + 1):
            try:
                resp = self._http.request(method, path, params=params, json=json,
                                          headers={"X-Request-ID": request_id.get()})
            except httpx.TransportError as exc:
                if attempt < attempts:
                    time.sleep(0.3 * attempt)
                    continue
                raise ServiceError(f"{self.name} service is unreachable at {self.base_url} ({type(exc).__name__})") from exc

            if resp.status_code >= 500 and attempt < attempts:
                time.sleep(0.3 * attempt)
                continue
            return self._decode(method, path, resp)

    def get(self, path: str, **params) -> Any:
        return self.request("GET", path, params=params or None)

    def get_all(self, path: str, page_size: int = 200, **params) -> list:
        """Walk skip/limit pagination. Counting from a single page is how agents end up
        reporting "100 customers" when there are 4,000."""
        rows: list = []
        skip = 0
        while True:
            page = self.get(path, skip=skip, limit=page_size, **params)
            rows.extend(page)
            if len(page) < page_size:
                return rows
            skip += page_size

    def close(self) -> None:
        self._http.close()

    def _decode(self, method: str, path: str, resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            log.info("%s %s%s -> %s", method, self.base_url, path, resp.status_code)
            raise ServiceError(f"{self.name}: {method} {path} returned {resp.status_code}: {detail}",
                               status=resp.status_code)
        if not resp.content:
            return None
        return resp.json()


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:12]
    request_id.set(rid)
    return rid
