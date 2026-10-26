"""Typed, dependency-free Python client for the Northstar reference API."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ControlEvent:
    event_id: int | None
    kind: str
    data: dict[str, Any]
    created_at: str | None = None


class PlatformClient:
    """Small synchronous client; token content stays separate from control events."""

    def __init__(self, base_url: str = "http://127.0.0.1:8080", timeout: float = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _json(self, method: str, route: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(self.base_url + route, data=body,
                          headers={"Content-Type": "application/json"}, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read() or b"{}")
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}").get("error", str(exc))
            except Exception:
                detail = str(exc)
            raise RuntimeError(f"Platform API error ({exc.code}): {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Platform API unavailable: {exc.reason}") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Platform API returned an unexpected response")
        return result

    def submit_inference(self, prompt: str, *, tenant: str = "default", priority: int = 1,
                         max_tokens: int = 128, temperature: float = 0.2,
                         deadline_seconds: int = 120) -> dict[str, Any]:
        return self._json("POST", "/api/inference", {
            "prompt": prompt, "tenant": tenant, "priority": priority,
            "max_tokens": max_tokens, "temperature": temperature,
            "deadline_seconds": deadline_seconds,
        })

    def stream_events(self, request_id: str) -> Iterator[ControlEvent]:
        request = Request(self.base_url + f"/api/requests/{quote(request_id, safe='')}/events",
                          headers={"Accept": "text/event-stream"})
        try:
            with urlopen(request, timeout=None) as response:
                event_id: int | None = None
                kind = "message"
                data_lines: list[str] = []
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if not line:
                        if data_lines:
                            payload = json.loads("\n".join(data_lines))
                            yield ControlEvent(event_id, kind, payload.get("data", {}), payload.get("createdAt"))
                        event_id, kind, data_lines = None, "message", []
                    elif line.startswith("id:"):
                        try:
                            event_id = int(line[3:].strip())
                        except ValueError:
                            event_id = None
                    elif line.startswith("event:"):
                        kind = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
        except HTTPError as exc:
            raise RuntimeError(f"Unable to read request events ({exc.code})") from exc
        except URLError as exc:
            raise RuntimeError(f"Request event stream unavailable: {exc.reason}") from exc

    def start_workflow(self, prompt: str, *, template: str = "draft-validate-publish") -> dict[str, Any]:
        return self._json("POST", "/api/workflows/start", {"prompt": prompt, "template": template})

    def approvals(self) -> list[dict[str, Any]]:
        return self._json("GET", "/api/approvals").get("approvals", [])

    def decide_approval(self, approval_id: str, *, approve: bool, note: str = "") -> dict[str, Any]:
        action = "approve" if approve else "reject"
        return self._json("POST", f"/api/approvals/{quote(approval_id, safe='')}/{action}", {"note": note})

    def workflow(self, run_id: str) -> dict[str, Any]:
        return self._json("GET", f"/api/workflows/{quote(run_id, safe='')}")

    def resume_workflow(self, run_id: str) -> dict[str, Any]:
        return self._json("POST", f"/api/workflows/{quote(run_id, safe='')}/resume", {})
