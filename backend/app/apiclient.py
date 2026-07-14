"""Thin synchronous HTTP client for the Market Gap Finder API.

Shared by the CLI (``app.cli``) and the MCP server (``app.mcp_server``) so both
speak to the same running backend the web UI uses — one engine, three surfaces.

The base URL comes from ``GAPFINDER_API_URL`` (default
``http://127.0.0.1:8000/api``; 127.0.0.1 on purpose — ``localhost`` can resolve
to an IPv6 squatter on this machine). Every method returns plain dicts/lists
(parsed JSON); HTTP errors surface as :class:`ApiError` carrying the backend's
``detail`` message, never a stack trace.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterator, Optional

import httpx

def _default_base_url() -> str:
    # Honor the justfile's API_PORT override (a Docker stack squats :8000 on
    # this machine, so the backend often runs on :8010).
    port = os.environ.get("API_PORT", "8000")
    return f"http://127.0.0.1:{port}/api"

# Endpoints that run source fetches and/or LLM calls need generous timeouts.
SLOW_TIMEOUT = 600.0
FAST_TIMEOUT = 30.0


class ApiError(RuntimeError):
    """An HTTP-level failure, carrying the backend's human-readable detail."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def base_url() -> str:
    return os.environ.get("GAPFINDER_API_URL", _default_base_url()).rstrip("/")


class GapFinderClient:
    """Synchronous client over the /api surface. One instance per process."""

    def __init__(self, url: Optional[str] = None):
        self.url = (url or base_url()).rstrip("/")
        self._http = httpx.Client(base_url=self.url, timeout=FAST_TIMEOUT)

    # ------------------------------------------------------------------ core
    def _request(self, method: str, path: str, *, json_body: Any = None,
                 params: Optional[dict] = None, timeout: float = FAST_TIMEOUT) -> Any:
        try:
            resp = self._http.request(
                method, path, json=json_body, params=params, timeout=timeout
            )
        except httpx.ConnectError as exc:
            raise ApiError(
                0,
                f"Could not reach the backend at {self.url} — is it running? "
                "(start it with `just backend`)",
            ) from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:  # noqa: BLE001 - non-JSON error body
                detail = resp.text
            raise ApiError(resp.status_code, str(detail))
        if not resp.content:
            return None
        return resp.json()

    # ---------------------------------------------------------------- health
    def health(self) -> dict:
        return self._request("GET", "/health")

    def usage(self) -> dict:
        return self._request("GET", "/usage")

    def set_usage_policy(self, daily_cap_tokens: Optional[int] = None,
                         limit_pct: Optional[float] = None) -> dict:
        return self._request("POST", "/usage/policy", json_body={
            "daily_cap_tokens": daily_cap_tokens, "limit_pct": limit_pct,
        })

    # -------------------------------------------------------------- analysis
    def analyze(self, area: str, sub_segments: Optional[list[str]] = None) -> dict:
        return self._request("POST", "/analyze", json_body={
            "area": area, "sub_segments": sub_segments or [],
        }, timeout=SLOW_TIMEOUT)

    # ------------------------------------------------------- intake / scout
    def intake(self, domain: str, brief: str = "") -> dict:
        return self._request("POST", "/projects/intake",
                             json_body={"domain": domain, "brief": brief},
                             timeout=SLOW_TIMEOUT)

    def scout(self, brief: str = "", avoid: Optional[list[str]] = None) -> dict:
        return self._request("POST", "/projects/scout",
                             json_body={"brief": brief, "avoid": avoid or []},
                             timeout=SLOW_TIMEOUT)

    def sort_research(self, text: str) -> dict:
        return self._request("POST", "/projects/sort-research",
                             json_body={"text": text}, timeout=SLOW_TIMEOUT)

    # -------------------------------------------------------------- projects
    def create_project(self, req: dict) -> dict:
        return self._request("POST", "/projects", json_body=req, timeout=SLOW_TIMEOUT)

    def list_projects(self) -> list[dict]:
        return self._request("GET", "/projects")

    def get_project(self, pid: str) -> dict:
        return self._request("GET", f"/projects/{pid}")

    def get_tree(self, pid: str) -> dict:
        return self._request("GET", f"/projects/{pid}/tree")

    def delete_project(self, pid: str) -> dict:
        return self._request("DELETE", f"/projects/{pid}")

    def control(self, pid: str, body: dict) -> dict:
        return self._request("POST", f"/projects/{pid}/control", json_body=body)

    def rerun(self, pid: str, autostart: bool = False) -> dict:
        return self._request("POST", f"/projects/{pid}/rerun",
                             json_body={"autostart": autostart})

    def diff(self, pid: str, against: str) -> dict:
        return self._request("GET", f"/projects/{pid}/diff",
                             params={"against": against})

    def research_pack(self, pid: str, nid: str, refresh: bool = False) -> dict:
        return self._request(
            "POST", f"/projects/{pid}/nodes/{nid}/research-pack",
            params={"refresh": int(refresh)}, timeout=SLOW_TIMEOUT,
        )

    # ------------------------------------------------------ cross-project views
    def portfolio(self) -> list[dict]:
        return self._request("GET", "/portfolio")

    def graveyard(self, q: str = "", limit: int = 50) -> list[dict]:
        return self._request("GET", "/graveyard", params={"q": q, "limit": limit})

    def watch(self) -> list[dict]:
        return self._request("GET", "/watch")

    def watch_sweep(self) -> dict:
        return self._request("POST", "/watch/sweep", timeout=SLOW_TIMEOUT)

    def preferences(self) -> dict:
        return self._request("GET", "/preferences")

    def distill_preferences(self) -> dict:
        return self._request("POST", "/preferences/distill", timeout=SLOW_TIMEOUT)

    def update_preferences(self, text: str, status: str) -> dict:
        return self._request("POST", "/preferences", json_body={
            "learned_preferences": text, "status": status,
        })

    # ----------------------------------------------------------------- events
    def stream_events(self, pid: str, after: Optional[int] = None) -> Iterator[dict]:
        """Tail the project's SSE stream, yielding parsed event payloads.

        The initial ``snapshot`` frame is skipped (callers hydrate via
        :meth:`get_tree` if they need it); keep-alive comments are ignored.
        """
        params = {"after": after} if after is not None else None
        with self._http.stream(
            "GET", f"/projects/{pid}/events", params=params, timeout=None
        ) as resp:
            if resp.status_code >= 400:
                raise ApiError(resp.status_code, "Could not open the event stream.")
            event_name = None
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    if event_name == "snapshot":
                        event_name = None
                        continue
                    event_name = None
                    try:
                        yield json.loads(line.split(":", 1)[1].strip())
                    except json.JSONDecodeError:
                        continue
