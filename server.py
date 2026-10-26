#!/usr/bin/env python3
"""Small, runnable reference service for the AI serving platform console.

The service uses only the Python standard library. In production, put it behind
an authenticated API gateway and use a separately managed database/queue.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
DB_PATH = Path(os.environ.get("PLATFORM_DB", str(DATA / "platform.sqlite3")))
PORT = int(os.environ.get("PORT", "8080"))
HOST = os.environ.get("HOST", "127.0.0.1")
BACKEND = os.environ.get("MODEL_BACKEND", "demo").lower()
MODEL_NAME = os.environ.get("MODEL_NAME", "demo-text-model")
MODEL_REVISION = os.environ.get("MODEL_REVISION", "demo-v1")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/")
MAX_ACTIVE = int(os.environ.get("MAX_ACTIVE_REQUESTS", "8"))
MAX_QUEUE = int(os.environ.get("MAX_QUEUED_REQUESTS", "100"))
MAX_TENANT_ACTIVE = int(os.environ.get("MAX_TENANT_ACTIVE", "4"))
MAX_WORKFLOW_STEPS = 8

DB_LOCK = threading.RLock()
SCHEDULER_WAKE = threading.Event()
ACTIVE_REQUESTS: dict[str, threading.Event] = {}
ACTIVE_LOCK = threading.RLock()
WORKFLOW_THREADS: set[str] = set()
WORKFLOW_LOCK = threading.RLock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DB_PATH, timeout=20, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def init_db() -> None:
    with DB_LOCK, db() as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS requests (
              id TEXT PRIMARY KEY, tenant TEXT NOT NULL, prompt TEXT NOT NULL,
              workflow_id TEXT,
              status TEXT NOT NULL, priority INTEGER NOT NULL, deadline_at REAL,
              config_json TEXT NOT NULL, output TEXT NOT NULL DEFAULT '',
              token_count INTEGER, queue_ms REAL,
              started_at TEXT, completed_at TEXT, error TEXT,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS requests_status_priority ON requests(status, priority DESC, created_at);
            CREATE TABLE IF NOT EXISTS request_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
              kind TEXT NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(request_id) REFERENCES requests(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS request_events_req ON request_events(request_id, id);
            CREATE TABLE IF NOT EXISTS workflow_runs (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
              status TEXT NOT NULL, input_json TEXT NOT NULL, graph_json TEXT NOT NULL,
              state_json TEXT NOT NULL, config_json TEXT NOT NULL, error TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workflow_steps (
              run_id TEXT NOT NULL, step_id TEXT NOT NULL, step_type TEXT NOT NULL,
              status TEXT NOT NULL, input_json TEXT NOT NULL, output_json TEXT,
              error TEXT, attempts INTEGER NOT NULL DEFAULT 1,
              started_at TEXT NOT NULL, completed_at TEXT,
              PRIMARY KEY(run_id, step_id),
              FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS workflow_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
              kind TEXT NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS approvals (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step_id TEXT NOT NULL,
              status TEXT NOT NULL, decision TEXT, created_at TEXT NOT NULL,
              decided_at TEXT,
              UNIQUE(run_id, step_id),
              FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS tool_effects (
              run_id TEXT NOT NULL, step_id TEXT NOT NULL, tool_name TEXT NOT NULL,
              result_json TEXT NOT NULL, created_at TEXT NOT NULL,
              PRIMARY KEY(run_id, step_id)
            );
            """
        )
        request_columns = {row["name"] for row in con.execute("PRAGMA table_info(requests)")}
        if "workflow_id" not in request_columns:
            con.execute("ALTER TABLE requests ADD COLUMN workflow_id TEXT")
        # A process restart cannot resume an in-flight token stream. Durable
        # workflows, by contrast, resume below from their last committed step.
        con.execute(
            "UPDATE requests SET status='failed', error='Service restarted during inference; submit the request again.', completed_at=? WHERE status='running'",
            (now(),),
        )
        con.execute("UPDATE workflow_runs SET status='running', updated_at=? WHERE status='running'", (now(),))


def emit_request(request_id: str, kind: str, data: dict[str, Any]) -> None:
    with DB_LOCK, db() as con:
        con.execute(
            "INSERT INTO request_events(request_id,kind,data_json,created_at) VALUES(?,?,?,?)",
            (request_id, kind, json.dumps(data, separators=(",", ":")), now()),
        )


def emit_workflow(run_id: str, kind: str, data: dict[str, Any]) -> None:
    with DB_LOCK, db() as con:
        con.execute(
            "INSERT INTO workflow_events(run_id,kind,data_json,created_at) VALUES(?,?,?,?)",
            (run_id, kind, json.dumps(data, separators=(",", ":")), now()),
        )


class Runtime:
    """Adapter contract shared by model families and serving backends."""

    label = "runtime adapter"

    def generate(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        cancelled: threading.Event,
        on_token: Callable[[str], None],
        model_name: str | None = None,
        timeout_seconds: float = 120,
    ) -> tuple[str, int | None]:
        raise NotImplementedError


class DemoRuntime(Runtime):
    label = "Deterministic demo runtime"

    def generate(self, prompt: str, max_tokens: int, temperature: float, cancelled: threading.Event,
                 on_token: Callable[[str], None], model_name: str | None = None,
                 timeout_seconds: float = 120) -> tuple[str, int | None]:
        excerpt = re.sub(r"\s+", " ", prompt).strip()[:180]
        answer = (f"Demo response for: {excerpt}. This output comes from the local reference runtime, "
                  "not a trained model. Configure the OpenAI-compatible adapter to serve a pinned model.")
        words = answer.split(" ")[:max_tokens]
        output = ""
        for word in words:
            if cancelled.is_set():
                raise InterruptedError("Request cancelled by operator")
            token = word if not output else " " + word
            output += token
            on_token(token)
            time.sleep(0.035)
        return output, None


class OpenAICompatibleRuntime(Runtime):
    """Streams a pinned model through a local OpenAI-compatible serving API."""

    label = "OpenAI-compatible streaming API"

    def __init__(self, base_url: str = OPENAI_BASE_URL):
        self.base_url = base_url.rstrip("/")

    def generate(self, prompt: str, max_tokens: int, temperature: float, cancelled: threading.Event,
                 on_token: Callable[[str], None], model_name: str | None = None,
                 timeout_seconds: float = 120) -> tuple[str, int | None]:
        payload = json.dumps({
            "model": model_name or MODEL_NAME,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream_options": {"include_usage": True},
        }).encode("utf-8")
        req = Request(f"{self.base_url}/chat/completions", data=payload,
                      headers={"Content-Type": "application/json", "Accept": "text/event-stream"}, method="POST")
        output = ""
        output_tokens: int | None = None
        try:
            with urlopen(req, timeout=max(0.1, timeout_seconds)) as response:
                for raw in response:
                    if cancelled.is_set():
                        raise InterruptedError("Cancellation requested; stopping the response stream")
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    usage = chunk.get("usage")
                    if isinstance(usage, dict) and isinstance(usage.get("completion_tokens"), int):
                        output_tokens = usage["completion_tokens"]
                    choices = chunk.get("choices") or []
                    delta = choices[0].get("delta", {}) if choices else {}
                    token = delta.get("content") or ""
                    if token:
                        output += token
                        on_token(token)
        except (HTTPError, URLError, TimeoutError) as exc:
            detail = getattr(exc, "reason", None) or str(exc)
            raise RuntimeError(f"Model backend request failed: {detail}") from exc
        return output, output_tokens


RUNTIME: Runtime = OpenAICompatibleRuntime() if BACKEND in {"openai", "vllm", "llamacpp"} else DemoRuntime()


def runtime_for(config: dict[str, Any]) -> Runtime:
    backend = config.get("backend", BACKEND)
    if backend in {"openai", "vllm", "llamacpp"}:
        return OpenAICompatibleRuntime(config.get("endpoint", OPENAI_BASE_URL))
    if backend == "demo":
        return DemoRuntime()
    raise ValueError(f"Runtime adapter is not registered: {backend}")


def snapshot_config(max_tokens: int, temperature: float) -> dict[str, Any]:
    return {
        "backend": BACKEND,
        "model": MODEL_NAME,
        "model_revision": MODEL_REVISION,
        "runtime": RUNTIME.label,
        "endpoint": OPENAI_BASE_URL if BACKEND in {"openai", "vllm", "llamacpp"} else "in-process",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "device": "managed by configured inference backend",
        "precision": "managed by configured inference backend",
        "quantization": "managed by configured inference backend",
    }


def run_inference(request_id: str, cancel_event: threading.Event) -> None:
    started_wall = time.time()
    with DB_LOCK, db() as con:
        row = con.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if not row or row["status"] != "queued":
            with ACTIVE_LOCK:
                ACTIVE_REQUESTS.pop(request_id, None)
            SCHEDULER_WAKE.set()
            return
        created = datetime.fromisoformat(row["created_at"]).timestamp()
        config = json.loads(row["config_json"])
        con.execute("UPDATE requests SET status='running', started_at=?, queue_ms=? WHERE id=?",
                    (now(), max(0, (started_wall - created) * 1000), request_id))
    emit_request(request_id, "running", {"queueMs": round(max(0, (started_wall - created) * 1000), 1)})
    chunks: list[str] = []
    deadline_expired = threading.Event()
    deadline_seconds = max(0.01, row["deadline_at"] - time.time()) if row["deadline_at"] else 120
    deadline_timer = threading.Timer(deadline_seconds, lambda: (deadline_expired.set(), cancel_event.set()))
    deadline_timer.daemon = True
    deadline_timer.start()

    def token_out(token: str) -> None:
        if row["deadline_at"] and time.time() > row["deadline_at"]:
            raise TimeoutError("Request deadline expired while running")
        chunks.append(token)
        # Tokens are a separate event type from scheduler/control events.
        emit_request(request_id, "token", {"text": token})
        with DB_LOCK, db() as con:
            con.execute("UPDATE requests SET output=? WHERE id=?", ("".join(chunks), request_id))

    try:
        if row["deadline_at"] and time.time() > row["deadline_at"]:
            raise TimeoutError("Request deadline expired while queued")
        timeout_seconds = max(0.1, row["deadline_at"] - time.time()) if row["deadline_at"] else 120
        output, output_tokens = runtime_for(config).generate(row["prompt"], config["max_tokens"], config["temperature"], cancel_event, token_out, config["model"], timeout_seconds)
        if deadline_expired.is_set():
            raise TimeoutError("Request deadline expired while running")
        if cancel_event.is_set():
            raise InterruptedError("Request cancelled by operator")
        duration = max(0.001, time.time() - started_wall)
        with DB_LOCK, db() as con:
            con.execute("UPDATE requests SET status='completed', output=?, token_count=?, completed_at=? WHERE id=?",
                        (output, output_tokens, now(), request_id))
        emit_request(request_id, "completed", {"tokens": output_tokens,
                                                "tokensPerSecond": round(output_tokens / duration, 2) if output_tokens is not None else None,
                                                "usageAvailable": output_tokens is not None,
                                                "durationMs": round(duration * 1000, 1)})
    except InterruptedError as exc:
        if deadline_expired.is_set():
            message = "Request deadline expired while running"
            with DB_LOCK, db() as con:
                con.execute("UPDATE requests SET status='failed', error=?, completed_at=? WHERE id=?",
                            (message, now(), request_id))
            emit_request(request_id, "failed", {"message": message, "canResume": False, "action": "Increase the deadline or resubmit"})
        else:
            with DB_LOCK, db() as con:
                con.execute("UPDATE requests SET status='cancelled', error=?, completed_at=? WHERE id=?",
                            (str(exc), now(), request_id))
            emit_request(request_id, "cancelled", {"message": str(exc), "canResume": False})
    except Exception as exc:  # Persist backend errors so the operator can act.
        message = "Request deadline expired while running" if deadline_expired.is_set() else str(exc)[:500]
        with DB_LOCK, db() as con:
            con.execute("UPDATE requests SET status='failed', error=?, completed_at=? WHERE id=?",
                        (message, now(), request_id))
        emit_request(request_id, "failed", {"message": message, "canResume": False,
                                             "action": "Increase the deadline or resubmit" if deadline_expired.is_set() else "Review the backend and resubmit"})
    finally:
        deadline_timer.cancel()
        with ACTIVE_LOCK:
            ACTIVE_REQUESTS.pop(request_id, None)
        SCHEDULER_WAKE.set()


def scheduler_loop() -> None:
    while True:
        launched = False
        with ACTIVE_LOCK:
            capacity = max(0, MAX_ACTIVE - len(ACTIVE_REQUESTS))
        if capacity:
            with DB_LOCK, db() as con:
                rows = con.execute(
                    "SELECT id,tenant FROM requests WHERE status='queued' ORDER BY priority DESC, created_at ASC LIMIT ?",
                    (MAX_QUEUE,),
                ).fetchall()
            tenant_counts: dict[str, int] = {}
            with ACTIVE_LOCK:
                for req_id in ACTIVE_REQUESTS:
                    with db() as con:
                        active = con.execute("SELECT tenant FROM requests WHERE id=?", (req_id,)).fetchone()
                    if active:
                        tenant_counts[active["tenant"]] = tenant_counts.get(active["tenant"], 0) + 1
                for row in rows:
                    if capacity <= 0:
                        break
                    if row["id"] in ACTIVE_REQUESTS:
                        continue
                    if tenant_counts.get(row["tenant"], 0) >= MAX_TENANT_ACTIVE:
                        continue
                    cancellation = threading.Event()
                    ACTIVE_REQUESTS[row["id"]] = cancellation
                    tenant_counts[row["tenant"]] = tenant_counts.get(row["tenant"], 0) + 1
                    threading.Thread(target=run_inference, args=(row["id"], cancellation), daemon=True).start()
                    capacity -= 1
                    launched = True
        SCHEDULER_WAKE.wait(0.12 if not launched else 0.02)
        SCHEDULER_WAKE.clear()


WORKFLOW_GRAPH = {
    "name": "Draft, validate, and publish",
    "version": "1.0.0",
    "max_steps": MAX_WORKFLOW_STEPS,
    "entry": "draft",
    "nodes": [
        {"id": "draft", "type": "model", "label": "Draft response", "next": "validate"},
        {"id": "validate", "type": "function", "function": "validate_draft", "label": "Validate output",
         "routes": {"valid": "review", "invalid": "revise"}},
        {"id": "revise", "type": "model", "label": "Refine draft", "next": "validate"},
        {"id": "review", "type": "approval", "label": "Human approval", "next": "publish"},
        {"id": "publish", "type": "tool", "tool": "save_preview", "permission": "artifact.preview.write",
         "label": "Save approved preview", "next": "done"},
        {"id": "done", "type": "terminal", "label": "Complete"},
    ],
}
PERMITTED_TOOLS = {"save_preview": "artifact.preview.write"}


def workflow_view(row: sqlite3.Row) -> dict[str, Any]:
    state = json.loads(row["state_json"])
    return {"id": row["id"], "name": row["name"], "version": row["version"], "status": row["status"],
            "currentNode": state.get("current_node"), "steps": state.get("executed_steps", 0),
            "createdAt": row["created_at"], "updatedAt": row["updated_at"], "error": row["error"],
            "canResume": row["status"] == "failed" and bool(state.get("can_resume", state.get("current_node")))}


def enqueue_workflow_model(run_id: str, step_id: str, prompt: str, config: dict[str, Any]) -> tuple[str, str]:
    """Route workflow model steps through the same admission queue as API calls."""
    request_id = str(uuid.uuid4())
    stamp = now()
    deadline_seconds = 600
    while True:
        with DB_LOCK, db() as con:
            queued = con.execute("SELECT count(*) AS n FROM requests WHERE status='queued'").fetchone()["n"]
            if queued < MAX_QUEUE:
                con.execute("INSERT INTO requests(id,tenant,prompt,workflow_id,status,priority,deadline_at,config_json,created_at) VALUES(?,?,?,?,'queued',?,?,?,?)",
                            (request_id, "workflow", prompt, run_id, 1, time.time() + deadline_seconds, json.dumps(config), stamp))
                con.execute("INSERT INTO request_events(request_id,kind,data_json,created_at) VALUES(?,'queued',?,?)",
                            (request_id, json.dumps({"priority": 1, "deadlineSeconds": deadline_seconds,
                                                     "workflowId": run_id, "stepId": step_id}), stamp))
                break
        time.sleep(0.15)
    SCHEDULER_WAKE.set()
    emit_workflow(run_id, "step.queued", {"stepId": step_id, "requestId": request_id})
    while True:
        with db() as con:
            row = con.execute("SELECT status,output,error,token_count FROM requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise RuntimeError("Scheduled workflow inference request disappeared")
        if row["status"] == "completed":
            emit_workflow(run_id, "step.inference_completed", {"stepId": step_id, "requestId": request_id,
                                                                 "tokens": row["token_count"]})
            return request_id, row["output"]
        if row["status"] in {"failed", "cancelled"}:
            raise RuntimeError(f"Workflow inference failed: {row['error'] or row['status']}")
        time.sleep(0.1)


def checkpoint(run_id: str, node: dict[str, Any], status: str, node_input: Any,
               output: Any, next_node: str | None, state: dict[str, Any], run_status: str = "running") -> None:
    stamp = now()
    state["current_node"] = next_node
    state["executed_steps"] = int(state.get("executed_steps", 0)) + 1
    state["can_resume"] = False
    if output is not None:
        state.setdefault("outputs", {})[node["id"]] = output
    with DB_LOCK, db() as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "INSERT INTO workflow_steps(run_id,step_id,step_type,status,input_json,output_json,error,started_at,completed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,step_id) DO UPDATE SET status=excluded.status, "
            "input_json=excluded.input_json,output_json=excluded.output_json,error=excluded.error,completed_at=excluded.completed_at, "
            "attempts=workflow_steps.attempts+1",
            (run_id, node["id"], node["type"], status, json.dumps(node_input),
             json.dumps(output) if output is not None else None, None, stamp, stamp),
        )
        con.execute("UPDATE workflow_runs SET state_json=?,status=?,updated_at=? WHERE id=?",
                    (json.dumps(state), run_status, stamp, run_id))
        con.commit()


def run_workflow(run_id: str) -> None:
    with WORKFLOW_LOCK:
        if run_id in WORKFLOW_THREADS:
            return
        WORKFLOW_THREADS.add(run_id)
    try:
        while True:
            with DB_LOCK, db() as con:
                row = con.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
            if not row or row["status"] not in {"running", "failed"}:
                return
            graph = json.loads(row["graph_json"])
            nodes = {node["id"]: node for node in graph["nodes"]}
            state = json.loads(row["state_json"])
            node_id = state.get("current_node") or graph["entry"]
            if node_id not in nodes:
                raise RuntimeError(f"Workflow checkpoint points to missing node: {node_id}")
            if state.get("executed_steps", 0) >= graph["max_steps"]:
                raise RuntimeError(f"Workflow exceeded its {graph['max_steps']} step limit")
            node = nodes[node_id]
            inputs = json.loads(row["input_json"])
            outputs = state.setdefault("outputs", {})
            latest_model = outputs.get("revise") or outputs.get("draft") or {}
            latest_text = latest_model.get("text", "") if isinstance(latest_model, dict) else ""

            if node["type"] == "model":
                if node_id == "draft":
                    prompt = inputs["prompt"]
                else:
                    prompt = f"Improve this draft and retain its intent: {latest_text}"
                run_config = json.loads(row["config_json"])
                request_id, text = enqueue_workflow_model(run_id, node_id, prompt, run_config)
                result = {"text": text, "model": run_config["model"], "modelRevision": run_config["model_revision"]}
                checkpoint(run_id, node, "completed", {"prompt": prompt}, result, node.get("next"), state)
                emit_workflow(run_id, "step.completed", {"stepId": node_id, "type": "model"})
            elif node["type"] == "function":
                if node["function"] != "validate_draft":
                    raise PermissionError(f"Deterministic function is not registered: {node['function']}")
                valid = len(latest_text.strip()) >= 12
                result = {"valid": valid, "reason": "minimum content length met" if valid else "draft is too short"}
                next_node = node["routes"]["valid" if valid else "invalid"]
                checkpoint(run_id, node, "completed", {"text": latest_text}, result, next_node, state)
                emit_workflow(run_id, "step.completed", {"stepId": node_id, "type": "function", "route": next_node})
            elif node["type"] == "approval":
                stamp = now()
                approval_id = str(uuid.uuid4())
                with DB_LOCK, db() as con:
                    con.execute("BEGIN IMMEDIATE")
                    con.execute("INSERT OR IGNORE INTO approvals(id,run_id,step_id,status,created_at) VALUES(?,?,?,'pending',?)",
                                (approval_id, run_id, node_id, stamp))
                    con.execute("INSERT OR IGNORE INTO workflow_steps(run_id,step_id,step_type,status,input_json,started_at) VALUES(?,?,?,'awaiting_approval',?,?)",
                                (run_id, node_id, node["type"], json.dumps({"text": latest_text}), stamp))
                    con.execute("UPDATE workflow_runs SET status='awaiting_approval',updated_at=? WHERE id=?", (stamp, run_id))
                    con.commit()
                emit_workflow(run_id, "awaiting_approval", {"stepId": node_id, "preview": latest_text})
                return
            elif node["type"] == "tool":
                required_permission = node.get("permission")
                if PERMITTED_TOOLS.get(node.get("tool")) != required_permission:
                    raise PermissionError(f"Tool permission denied: {node.get('tool')} requires {required_permission}")
                with DB_LOCK, db() as con:
                    existing = con.execute("SELECT result_json FROM tool_effects WHERE run_id=? AND step_id=?",
                                           (run_id, node_id)).fetchone()
                    if existing:
                        result = json.loads(existing["result_json"])
                    else:
                        result = {"saved": True, "title": "Approved model draft", "text": latest_text[:1000]}
                        con.execute("INSERT INTO tool_effects(run_id,step_id,tool_name,result_json,created_at) VALUES(?,?,?,?,?)",
                                    (run_id, node_id, node["tool"], json.dumps(result), now()))
                checkpoint(run_id, node, "completed", {"text": latest_text}, result, node.get("next"), state)
                emit_workflow(run_id, "step.completed", {"stepId": node_id, "type": "tool", "permission": required_permission})
            elif node["type"] == "terminal":
                checkpoint(run_id, node, "completed", {}, {"completed": True}, None, state, "completed")
                emit_workflow(run_id, "completed", {"message": "Workflow reached its terminal node"})
                return
            else:
                raise ValueError(f"Unsupported workflow node type: {node['type']}")
    except Exception as exc:
        message = str(exc)[:500]
        with DB_LOCK, db() as con:
            row = con.execute("SELECT state_json FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
            state = json.loads(row["state_json"]) if row else {}
            state["can_resume"] = bool(state.get("current_node")) and not isinstance(exc, (PermissionError, ValueError, KeyError)) and "step limit" not in message.lower()
            con.execute("UPDATE workflow_runs SET status='failed',error=?,state_json=?,updated_at=? WHERE id=?",
                        (message, json.dumps(state), now(), run_id))
        emit_workflow(run_id, "failed", {"message": message, "canResume": state["can_resume"],
                                          "action": "Inspect the failed step, then resume" if state["can_resume"] else "Correct the workflow configuration before starting a new run"})
    finally:
        with WORKFLOW_LOCK:
            WORKFLOW_THREADS.discard(run_id)


def serialize_request(row: sqlite3.Row) -> dict[str, Any]:
    config = json.loads(row["config_json"])
    created = datetime.fromisoformat(row["created_at"]).timestamp()
    return {"id": row["id"], "tenant": row["tenant"], "status": row["status"], "priority": row["priority"],
            "prompt": row["prompt"], "output": row["output"], "workflowId": row["workflow_id"], "tokens": row["token_count"],
            "queueMs": round(row["queue_ms"] or 0, 1), "createdAt": row["created_at"],
            "ageSeconds": round(time.time() - created, 1), "model": config["model"], "modelRevision": config["model_revision"],
            "error": row["error"], "canResume": False}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body_json(self) -> dict[str, Any]:
        length = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object")
        return data

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if path == "/" or path == "/index.html":
                return self.send_file("index.html", "text/html; charset=utf-8")
            if path.startswith("/static/"):
                return self.send_file(path.removeprefix("/static/"), None)
            if path == "/api/overview":
                return self.send_json(self.overview())
            if path == "/api/events":
                with db() as con:
                    rows = con.execute(
                        "SELECT source,kind,data_json,created_at FROM ("
                        "SELECT request_id AS source,kind,data_json,created_at,id AS event_order FROM request_events "
                        "UNION ALL SELECT run_id AS source,kind,data_json,created_at,id AS event_order FROM workflow_events"
                        ") ORDER BY created_at DESC,event_order DESC LIMIT 18"
                    ).fetchall()
                return self.send_json({"events": [{"source": r["source"], "kind": r["kind"],
                    "data": json.loads(r["data_json"]), "createdAt": r["created_at"]} for r in rows]})
            if path == "/api/models":
                return self.send_json({"models": [{"id": MODEL_NAME, "revision": MODEL_REVISION, "backend": BACKEND,
                    "runtime": RUNTIME.label, "status": "ready" if BACKEND == "demo" else "configured",
                    "device": "backend managed", "precision": "backend managed", "quantization": "backend managed"}]})
            if path == "/api/deployments":
                return self.send_json({"deployments": [{"id": "dep-local-primary", "name": "Local primary", "model": MODEL_NAME,
                    "revision": MODEL_REVISION, "backend": BACKEND, "status": "ready" if BACKEND == "demo" else "configured",
                    "endpoint": "/api/inference", "activeLimit": MAX_ACTIVE, "queueLimit": MAX_QUEUE}]})
            if path == "/api/requests":
                with db() as con:
                    rows = con.execute("SELECT * FROM requests ORDER BY created_at DESC LIMIT 100").fetchall()
                return self.send_json({"requests": [serialize_request(row) for row in rows]})
            if path == "/api/workflows":
                with db() as con:
                    rows = con.execute("SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT 100").fetchall()
                return self.send_json({"template": WORKFLOW_GRAPH, "runs": [workflow_view(row) for row in rows]})
            if path == "/api/approvals":
                with db() as con:
                    rows = con.execute("SELECT a.*,w.name,w.version,w.state_json FROM approvals a JOIN workflow_runs w ON w.id=a.run_id WHERE a.status='pending' ORDER BY a.created_at").fetchall()
                approvals = []
                for row in rows:
                    state = json.loads(row["state_json"])
                    draft = state.get("outputs", {}).get("draft", {}).get("text", "")
                    approvals.append({"id": row["id"], "runId": row["run_id"], "stepId": row["step_id"],
                                      "workflow": row["name"], "version": row["version"], "preview": draft,
                                      "createdAt": row["created_at"]})
                return self.send_json({"approvals": approvals})
            if path == "/api/health":
                return self.send_json(self.health())
            match = re.fullmatch(r"/api/requests/([\w-]+)/events", path)
            if match:
                return self.stream_events(match.group(1))
            match = re.fullmatch(r"/api/workflows/([\w-]+)", path)
            if match:
                return self.workflow_detail(match.group(1))
            return self.send_json({"error": "Not found"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            payload = self.body_json()
            if path == "/api/inference":
                return self.submit_inference(payload)
            if path == "/api/workflows/start":
                return self.start_workflow(payload)
            match = re.fullmatch(r"/api/requests/([\w-]+)/cancel", path)
            if match:
                return self.cancel_request(match.group(1))
            match = re.fullmatch(r"/api/approvals/([\w-]+)/(approve|reject)", path)
            if match:
                return self.decide_approval(match.group(1), match.group(2), payload)
            match = re.fullmatch(r"/api/workflows/([\w-]+)/resume", path)
            if match:
                return self.resume_workflow(match.group(1))
            return self.send_json({"error": "Not found"}, 404)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def send_file(self, name: str, content_type: str | None) -> None:
        static_root = STATIC.resolve()
        candidate = (static_root / name).resolve()
        if not candidate.is_relative_to(static_root) or not candidate.is_file():
            return self.send_json({"error": "Not found"}, 404)
        body = candidate.read_bytes()
        if content_type is None:
            content_type = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}.get(candidate.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def submit_inference(self, payload: dict[str, Any]) -> None:
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20_000:
            raise ValueError("prompt must be a non-empty string of at most 20,000 characters")
        tenant = payload.get("tenant", "default")
        if not isinstance(tenant, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", tenant):
            raise ValueError("tenant must contain 1–64 letters, numbers, dots, underscores, or hyphens")
        priority = payload.get("priority", 1)
        max_tokens = payload.get("max_tokens", 128)
        temperature = payload.get("temperature", 0.2)
        deadline_seconds = payload.get("deadline_seconds", 120)
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 3:
            raise ValueError("priority must be an integer from 0 to 3")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 4096:
            raise ValueError("max_tokens must be an integer from 1 to 4096")
        if not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, int) or not 1 <= deadline_seconds <= 3600:
            raise ValueError("deadline_seconds must be an integer from 1 to 3600")
        with DB_LOCK, db() as con:
            queued = con.execute("SELECT count(*) AS n FROM requests WHERE status='queued'").fetchone()["n"]
            tenant_queued = con.execute("SELECT count(*) AS n FROM requests WHERE status='queued' AND tenant=?", (tenant,)).fetchone()["n"]
            if queued >= MAX_QUEUE:
                return self.send_json({"error": "Queue is full. Retry after capacity is available.", "retryable": True}, 429)
            if tenant_queued >= MAX_TENANT_ACTIVE * 4:
                return self.send_json({"error": "Tenant queue quota reached.", "retryable": True}, 429)
            request_id = str(uuid.uuid4())
            stamp = now()
            config = snapshot_config(max_tokens, float(temperature))
            con.execute("INSERT INTO requests(id,tenant,prompt,status,priority,deadline_at,config_json,created_at) VALUES(?,?,?,'queued',?,?,?,?)",
                        (request_id, tenant, prompt.strip(), priority, time.time() + deadline_seconds, json.dumps(config), stamp))
            con.execute("INSERT INTO request_events(request_id,kind,data_json,created_at) VALUES(?,'queued',?,?)",
                        (request_id, json.dumps({"priority": priority, "deadlineSeconds": deadline_seconds}), stamp))
        SCHEDULER_WAKE.set()
        return self.send_json({"id": request_id, "status": "queued", "model": MODEL_NAME, "modelRevision": MODEL_REVISION,
                               "events": f"/api/requests/{request_id}/events"}, 202)

    def cancel_request(self, request_id: str) -> None:
        with DB_LOCK, db() as con:
            row = con.execute("SELECT status FROM requests WHERE id=?", (request_id,)).fetchone()
            if not row:
                return self.send_json({"error": "Request not found"}, 404)
            if row["status"] == "queued":
                con.execute("UPDATE requests SET status='cancelled',error='Cancelled before dispatch',completed_at=? WHERE id=?", (now(), request_id))
                emit_request(request_id, "cancelled", {"message": "Cancelled before dispatch", "canResume": False})
                return self.send_json({"status": "cancelled"})
            if row["status"] != "running":
                return self.send_json({"status": row["status"]}, 409)
        with ACTIVE_LOCK:
            event = ACTIVE_REQUESTS.get(request_id)
            if event:
                event.set()
        return self.send_json({"status": "cancellation_requested"}, 202)

    def start_workflow(self, payload: dict[str, Any]) -> None:
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20_000:
            raise ValueError("prompt must be a non-empty string of at most 20,000 characters")
        if payload.get("template", "draft-validate-publish") != "draft-validate-publish":
            raise ValueError("Unknown workflow template")
        run_id = str(uuid.uuid4())
        config = snapshot_config(120, 0.2)
        state = {"current_node": "draft", "executed_steps": 0, "outputs": {}}
        stamp = now()
        with DB_LOCK, db() as con:
            con.execute("INSERT INTO workflow_runs(id,name,version,status,input_json,graph_json,state_json,config_json,created_at,updated_at) VALUES(?,?,?,'running',?,?,?,?,?,?)",
                        (run_id, WORKFLOW_GRAPH["name"], WORKFLOW_GRAPH["version"], json.dumps({"prompt": prompt.strip()}),
                         json.dumps(WORKFLOW_GRAPH), json.dumps(state), json.dumps(config), stamp, stamp))
        emit_workflow(run_id, "queued", {"workflowVersion": WORKFLOW_GRAPH["version"]})
        threading.Thread(target=run_workflow, args=(run_id,), daemon=True).start()
        return self.send_json({"id": run_id, "status": "running", "version": WORKFLOW_GRAPH["version"]}, 202)

    def decide_approval(self, approval_id: str, decision: str, payload: dict[str, Any]) -> None:
        note = payload.get("note", "")
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError("note must be a string of at most 500 characters")
        with DB_LOCK, db() as con:
            con.execute("BEGIN IMMEDIATE")
            approval = con.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if not approval or approval["status"] != "pending":
                con.rollback()
                return self.send_json({"error": "Pending approval not found"}, 404)
            run = con.execute("SELECT * FROM workflow_runs WHERE id=?", (approval["run_id"],)).fetchone()
            graph = json.loads(run["graph_json"])
            node = next(n for n in graph["nodes"] if n["id"] == approval["step_id"])
            state = json.loads(run["state_json"])
            if decision == "approve":
                state["current_node"] = node["next"]
                state["executed_steps"] = int(state.get("executed_steps", 0)) + 1
                con.execute("UPDATE workflow_steps SET status='approved',output_json=?,completed_at=? WHERE run_id=? AND step_id=?",
                            (json.dumps({"decision": "approved", "note": note}), now(), run["id"], node["id"]))
                con.execute("UPDATE workflow_runs SET status='running',state_json=?,updated_at=? WHERE id=?",
                            (json.dumps(state), now(), run["id"]))
            else:
                con.execute("UPDATE workflow_steps SET status='rejected',error=?,completed_at=? WHERE run_id=? AND step_id=?",
                            (note or "Rejected by reviewer", now(), run["id"], node["id"]))
                con.execute("UPDATE workflow_runs SET status='rejected',error=?,updated_at=? WHERE id=?",
                            (note or "Rejected by reviewer", now(), run["id"]))
            con.execute("UPDATE approvals SET status=?,decision=?,decided_at=? WHERE id=?",
                        ("approved" if decision == "approve" else "rejected", note, now(), approval_id))
            con.commit()
        emit_workflow(run["id"], "approval." + ("approved" if decision == "approve" else "rejected"),
                      {"stepId": node["id"], "note": note})
        if decision == "approve":
            threading.Thread(target=run_workflow, args=(run["id"],), daemon=True).start()
        return self.send_json({"status": "approved" if decision == "approve" else "rejected", "runId": run["id"]})

    def resume_workflow(self, run_id: str) -> None:
        with DB_LOCK, db() as con:
            row = con.execute("SELECT status FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return self.send_json({"error": "Workflow not found"}, 404)
            if row["status"] != "failed":
                return self.send_json({"error": "Only failed workflows can be resumed"}, 409)
            con.execute("UPDATE workflow_runs SET status='running',error=NULL,updated_at=? WHERE id=?", (now(), run_id))
        threading.Thread(target=run_workflow, args=(run_id,), daemon=True).start()
        return self.send_json({"status": "resuming", "id": run_id}, 202)

    def workflow_detail(self, run_id: str) -> None:
        with db() as con:
            row = con.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return self.send_json({"error": "Workflow not found"}, 404)
            steps = con.execute("SELECT * FROM workflow_steps WHERE run_id=? ORDER BY started_at", (run_id,)).fetchall()
            events = con.execute("SELECT * FROM workflow_events WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        result = workflow_view(row)
        result.update({"graph": json.loads(row["graph_json"]), "input": json.loads(row["input_json"]),
                       "configuration": json.loads(row["config_json"]), "error": row["error"],
                       "stepDetails": [{"id": s["step_id"], "type": s["step_type"], "status": s["status"],
                                        "input": json.loads(s["input_json"]), "output": json.loads(s["output_json"]) if s["output_json"] else None,
                                        "error": s["error"], "attempts": s["attempts"]} for s in steps],
                       "events": [{"kind": e["kind"], "data": json.loads(e["data_json"]), "createdAt": e["created_at"]} for e in events]})
        return self.send_json(result)

    def stream_events(self, request_id: str) -> None:
        try:
            since = int(self.headers.get("Last-Event-ID", "0"))
        except ValueError:
            since = 0
        with db() as con:
            exists = con.execute("SELECT id FROM requests WHERE id=?", (request_id,)).fetchone()
        if not exists:
            return self.send_json({"error": "Request not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(b": connected\n\n")
        self.wfile.flush()
        while True:
            with db() as con:
                events = con.execute("SELECT * FROM request_events WHERE request_id=? AND id>? ORDER BY id", (request_id, since)).fetchall()
                status_row = con.execute("SELECT status FROM requests WHERE id=?", (request_id,)).fetchone()
            for event in events:
                since = event["id"]
                payload = json.dumps({"kind": event["kind"], "data": json.loads(event["data_json"]), "createdAt": event["created_at"]}, ensure_ascii=False)
                self.wfile.write(f"id: {since}\nevent: {event['kind']}\ndata: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            if status_row and status_row["status"] in {"completed", "failed", "cancelled"} and not events:
                return
            time.sleep(0.18)

    def overview(self) -> dict[str, Any]:
        with db() as con:
            counts = {row["status"]: row["n"] for row in con.execute("SELECT status,count(*) AS n FROM requests GROUP BY status")}
            recent = con.execute("SELECT token_count,started_at,completed_at FROM requests WHERE status='completed' AND token_count IS NOT NULL AND completed_at>=?",
                                 (datetime.fromtimestamp(time.time() - 300, timezone.utc).isoformat(),)).fetchall()
            runs = {row["status"]: row["n"] for row in con.execute("SELECT status,count(*) AS n FROM workflow_runs GROUP BY status")}
        elapsed = sum(max(0.01, (datetime.fromisoformat(r["completed_at"]).timestamp() - datetime.fromisoformat(r["started_at"]).timestamp())) for r in recent)
        tokens = sum(r["token_count"] for r in recent)
        with ACTIVE_LOCK:
            active = len(ACTIVE_REQUESTS)
        return {"requests": counts, "workflows": runs, "active": active, "queued": counts.get("queued", 0),
                "queueLimit": MAX_QUEUE, "activeLimit": MAX_ACTIVE,
                "tokensPerSecond5m": round(tokens / elapsed, 2) if recent and elapsed else None,
                "tokenUsageAvailable": bool(recent),
                "backend": BACKEND, "model": MODEL_NAME, "modelRevision": MODEL_REVISION,
                "window": "rolling 5 minutes", "measured": True}

    def health(self) -> dict[str, Any]:
        with db() as con:
            queued = con.execute("SELECT count(*) AS n FROM requests WHERE status='queued'").fetchone()["n"]
            awaiting = con.execute("SELECT count(*) AS n FROM workflow_runs WHERE status='awaiting_approval'").fetchone()["n"]
            failed = con.execute("SELECT count(*) AS n FROM requests WHERE status='failed' AND created_at>=?",
                                 (datetime.fromtimestamp(time.time() - 3600, timezone.utc).isoformat(),)).fetchone()["n"]
        with ACTIVE_LOCK:
            active = len(ACTIVE_REQUESTS)
        return {"status": "operational" if BACKEND == "demo" else "configured", "runtime": RUNTIME.label,
                "queue": {"queued": queued, "capacity": MAX_QUEUE, "pressurePercent": round(queued / max(1, MAX_QUEUE) * 100, 1),
                          "active": active, "activeCapacity": MAX_ACTIVE},
                "workflowApprovalsPending": awaiting, "failedRequestsLastHour": failed,
                "kvCache": {"status": "backend-managed", "telemetry": "unavailable",
                            "detail": "The OpenAI-compatible API does not expose KV block allocation or sharing telemetry."},
                "gpu": {"status": "unavailable", "detail": "No accelerator metrics adapter is configured."},
                "storage": {"engine": "SQLite WAL", "path": str(DB_PATH.name), "durability": "local process checkpoints"}}


def main() -> None:
    init_db()
    threading.Thread(target=scheduler_loop, name="request-scheduler", daemon=True).start()
    with db() as con:
        resumable = con.execute("SELECT id FROM workflow_runs WHERE status='running'").fetchall()
    for row in resumable:
        threading.Thread(target=run_workflow, args=(row["id"],), daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"AI Serving Platform listening on http://{HOST}:{PORT} ({BACKEND} backend, model {MODEL_NAME}@{MODEL_REVISION})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
