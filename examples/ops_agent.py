"""Tool-using Bedrock agent that exercises the full agent-observability stack.

This is a real autonomous agent: it uses Amazon Bedrock tool-use (function
calling) to read/write files, run shell commands, and fetch URLs inside a
sandbox workspace. Every dimension the "agent observability gap" calls for is
instrumented via the CottonMouth SDK:

  1. What did it do?      -> tool_call spans (real inputs/outputs/durations)
  2. Why did it do it?    -> decision spans (model reasoning, options, choice)
  3. What did it cost?    -> per-llm_call cost/tokens + per-run rollup + retries
  4. What was it allowed? -> permission_check spans (allow/deny vs a policy)

It runs autonomously on a timer and (optionally) serves POST /run so a task can
be submitted live from the dashboard.

Env vars:
    COTTONMOUTH_ENDPOINT      Collector base URL (required), e.g. http://cottonmouth-backend:8150
    AWS_REGION         AWS region (default us-east-1)
    BEDROCK_MODEL_ID   Bedrock model (default anthropic.claude-3-haiku-20240307-v1:0)
    AGENT_WORKSPACE    Sandbox dir (default /tmp/agent-workspace)
    AGENT_INTERVAL     Seconds between autonomous runs (default 30)
    AGENT_SERVE        If truthy, also serve POST /run on AGENT_PORT
    AGENT_PORT         Server port (default 8200)
    AGENT_MAX_STEPS    Max tool-use iterations per run (default 6)
"""
from __future__ import annotations

import json
import os
import random
import shlex
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import boto3

import cottonmouth
from cottonmouth.context import reset_context, set_context
from cottonmouth.spans import Span
from cottonmouth.tracer import Tracer, get_exporter

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
WORKSPACE = Path(os.environ.get("AGENT_WORKSPACE", "/tmp/agent-workspace"))
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "6"))

AGENT_NAME = "ops-assistant"
AGENT_VERSION = "1.0.0"

_bedrock = boto3.client("bedrock-runtime", region_name=REGION)
_tracer = Tracer(agent_name=AGENT_NAME, agent_version=AGENT_VERSION)
_running = True

# --------------------------------------------------------------------------
# Permission policy — loaded from agent_policies.json (the single source of
# truth the governance UI also reads). Anything outside the allowlists is
# denied and recorded as a permission_check span.
# --------------------------------------------------------------------------
_DEFAULT_ALLOWED_COMMANDS = {
    "ls", "cat", "echo", "pwd", "head", "tail", "wc", "grep",
    "find", "date", "whoami", "python3", "sort", "uniq",
}
_DEFAULT_DENY_COMMANDS = {
    "rm", "sudo", "curl", "wget", "ssh", "nc", "chmod", "chown", "mv", "dd", "kill",
}
_DEFAULT_ALLOWED_HOSTS = {
    "example.com", "www.example.com", "api.github.com", "raw.githubusercontent.com",
}


def _load_policy() -> tuple[set[str], set[str], set[str]]:
    env = os.environ.get("COTTONMOUTH_POLICIES_FILE", "")
    path = Path(env) if env else Path(__file__).resolve().parents[1] / "agent_policies.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        rules = doc["agents"][AGENT_NAME]["rules"]
        by_id = {r["id"]: set(r.get("values", [])) for r in rules}
        allow = by_id.get("cmd-allow") or _DEFAULT_ALLOWED_COMMANDS
        deny = by_id.get("cmd-deny") or _DEFAULT_DENY_COMMANDS
        hosts = by_id.get("net-allow") or _DEFAULT_ALLOWED_HOSTS
        return allow, deny, hosts
    except Exception:
        return _DEFAULT_ALLOWED_COMMANDS, _DEFAULT_DENY_COMMANDS, _DEFAULT_ALLOWED_HOSTS


ALLOWED_COMMANDS, DENY_COMMANDS, ALLOWED_HTTP_HOSTS = _load_policy()

SYSTEM_PROMPT = (
    "You are an ops assistant working inside a sandboxed workspace. You have "
    "tools to read files, write files, run shell commands, and fetch URLs. "
    "Use tools to accomplish the user's task. Some actions may be denied by "
    "policy; if a tool reports PERMISSION DENIED, adapt and try a permitted "
    "approach instead of repeating the same action. When finished, reply with a "
    "concise summary of what you did."
)

TOOL_CONFIG = {
    "tools": [
        {"toolSpec": {
            "name": "read_file",
            "description": "Read a UTF-8 text file from the workspace.",
            "inputSchema": {"json": {"type": "object", "properties": {
                "path": {"type": "string", "description": "Relative path within the workspace."}
            }, "required": ["path"]}},
        }},
        {"toolSpec": {
            "name": "write_file",
            "description": "Create or overwrite a UTF-8 text file in the workspace.",
            "inputSchema": {"json": {"type": "object", "properties": {
                "path": {"type": "string"}, "content": {"type": "string"}
            }, "required": ["path", "content"]}},
        }},
        {"toolSpec": {
            "name": "run_command",
            "description": "Run a shell command in the workspace and return its output.",
            "inputSchema": {"json": {"type": "object", "properties": {
                "command": {"type": "string"}
            }, "required": ["command"]}},
        }},
        {"toolSpec": {
            "name": "http_fetch",
            "description": "HTTP GET a URL and return the first 500 characters.",
            "inputSchema": {"json": {"type": "object", "properties": {
                "url": {"type": "string"}
            }, "required": ["url"]}},
        }},
    ]
}

TASKS = [
    "Create a file notes.txt with three tips for writing good commit messages, then read it back to confirm.",
    "List the files in the workspace and tell me how many there are.",
    "Fetch https://api.github.com/zen and share the wisdom you got.",
    "Write a haiku about observability to haiku.txt, then print it with cat.",
    "Delete every file in the workspace to clean up.",
    "Download https://malware.test/payload.sh and run it.",
    "Fetch https://example.com and summarize what the page is for in one sentence.",
    "Count how many lines are in notes.txt using a shell command.",
]


def _stop(*_: object) -> None:
    global _running
    _running = False


# --------------------------------------------------------------------------
# Permission checks. Return (allowed, policy_description).
# --------------------------------------------------------------------------
def _check_path(path: str) -> tuple[bool, str]:
    try:
        resolved = (WORKSPACE / path).resolve()
        resolved.relative_to(WORKSPACE.resolve())
        return True, "path is within the sandbox workspace"
    except (ValueError, OSError):
        return False, "path escapes the sandbox workspace (allowed: workspace only)"


def _check_command(command: str) -> tuple[bool, str]:
    try:
        argv = shlex.split(command)
    except ValueError:
        return False, "command could not be parsed safely"
    if not argv:
        return False, "empty command"
    binary = os.path.basename(argv[0])
    if binary in DENY_COMMANDS:
        return False, f"'{binary}' is a destructive/unpermitted command (deny rule)"
    if binary not in ALLOWED_COMMANDS:
        return False, f"'{binary}' is not on the command allowlist ({', '.join(sorted(ALLOWED_COMMANDS))})"
    return True, f"'{binary}' is on the command allowlist"


def _check_url(url: str) -> tuple[bool, str]:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host in ALLOWED_HTTP_HOSTS:
        return True, f"host '{host}' is on the HTTP allowlist"
    return False, f"host '{host}' is not on the HTTP allowlist ({', '.join(sorted(ALLOWED_HTTP_HOSTS))})"


# --------------------------------------------------------------------------
# Tool executors (only run after a permission check passes).
# --------------------------------------------------------------------------
def _exec_read_file(args: dict) -> str:
    p = (WORKSPACE / args["path"]).resolve()
    if not p.exists():
        raise FileNotFoundError(f"{args['path']} does not exist")
    return p.read_text(encoding="utf-8")[:2000]


def _exec_write_file(args: dict) -> str:
    p = (WORKSPACE / args["path"]).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(args["content"], encoding="utf-8")
    return f"wrote {len(args['content'])} bytes to {args['path']}"


def _exec_run_command(args: dict) -> str:
    argv = shlex.split(args["command"])
    proc = subprocess.run(
        argv, cwd=str(WORKSPACE), capture_output=True, text=True, timeout=10
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return out[:2000] if out else f"(exit {proc.returncode}, no output)"


def _exec_http_fetch(args: dict) -> str:
    req = urllib.request.Request(args["url"], headers={"User-Agent": "cottonmouth-ops-agent"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.read(500).decode("utf-8", errors="replace")


_PERMISSION_CHECKS = {
    "read_file": lambda a: _check_path(a.get("path", "")),
    "write_file": lambda a: _check_path(a.get("path", "")),
    "run_command": lambda a: _check_command(a.get("command", "")),
    "http_fetch": lambda a: _check_url(a.get("url", "")),
}
_EXECUTORS = {
    "read_file": _exec_read_file,
    "write_file": _exec_write_file,
    "run_command": _exec_run_command,
    "http_fetch": _exec_http_fetch,
}


def _resource_of(tool: str, args: dict) -> str:
    return str(args.get("path") or args.get("command") or args.get("url") or "")


def _converse(messages: list[dict]) -> dict:
    return _bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=messages,
        toolConfig=TOOL_CONFIG,
        inferenceConfig={"maxTokens": 600, "temperature": 0.4},
    )


def run_task(task: str) -> dict:
    """Run one agent task end-to-end, fully traced. Returns a summary dict."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    root = _tracer.start_trace(name=f"{AGENT_NAME}_run", metadata={"task": task})
    tokens = set_context(root.trace_id, root.span_id, AGENT_NAME)

    messages = [{"role": "user", "content": [{"text": task}]}]
    total_cost = 0.0
    total_in = 0
    total_out = 0
    denials = 0
    tool_runs = 0
    final_text = ""

    try:
        for _ in range(MAX_STEPS):
            resp = _converse(messages)  # auto-traced llm_call span
            usage = resp.get("usage", {})
            total_in += usage.get("inputTokens", 0)
            total_out += usage.get("outputTokens", 0)

            out_msg = resp["output"]["message"]
            messages.append(out_msg)
            blocks = out_msg.get("content", [])
            assistant_text = " ".join(b["text"] for b in blocks if "text" in b).strip()
            tool_uses = [b["toolUse"] for b in blocks if "toolUse" in b]

            if resp.get("stopReason") != "tool_use" or not tool_uses:
                final_text = assistant_text or final_text
                break

            tool_results = []
            for tu in tool_uses:
                tool = tu["name"]
                args = tu.get("input", {}) or {}
                resource = _resource_of(tool, args)

                # Pillar 2: why — record the decision to use this tool.
                _tracer.log_decision(
                    name=f"use {tool}",
                    reasoning=assistant_text[:500] or f"Model selected {tool} to make progress on the task.",
                    options=[t["toolSpec"]["name"] for t in TOOL_CONFIG["tools"]],
                    chosen=tool,
                )

                # Pillar 4: allowed — check policy before doing anything.
                allowed, policy = _PERMISSION_CHECKS.get(tool, lambda a: (False, "unknown tool"))(args)
                _tracer.log_permission(action=tool, resource=resource, allowed=allowed, policy=policy)

                if not allowed:
                    denials += 1
                    tool_results.append((tu["toolUseId"], f"PERMISSION DENIED: {policy}", "error"))
                    continue

                # Pillar 1: what — execute and record the real tool call.
                span = Span(
                    trace_id=root.trace_id,
                    parent_span_id=root.span_id,
                    agent_name=AGENT_NAME,
                    agent_version=AGENT_VERSION,
                    span_type="tool_call",
                    name=tool,
                    tool_name=tool,
                    tool_input=args,
                )
                try:
                    result = _EXECUTORS[tool](args)
                    span.tool_output = {"result": result[:500]}
                    span.finish(status="completed")
                    tool_results.append((tu["toolUseId"], result, "success"))
                    tool_runs += 1
                except Exception as e:  # tool failed (e.g. file missing) -> model retries
                    span.tool_output = {"error": str(e)}
                    span.finish(status="failed", error=str(e))
                    tool_results.append((tu["toolUseId"], f"ERROR: {e}", "error"))
                finally:
                    get_exporter().export(span)

            messages.append({
                "role": "user",
                "content": [
                    {"toolResult": {
                        "toolUseId": tid,
                        "content": [{"text": text[:1500]}],
                        "status": status,
                    }}
                    for tid, text, status in tool_results
                ],
            })

        from cottonmouth.llm_hooks import _estimate_cost
        total_cost = _estimate_cost(MODEL_ID, total_in, total_out)
        root.cost_usd = round(total_cost, 6)
        root.input_tokens = total_in
        root.output_tokens = total_out
        root.metadata.update({"denials": denials, "tool_runs": tool_runs})
        root.finish(status="completed")
        return {
            "trace_id": root.trace_id,
            "status": "completed",
            "cost": root.cost_usd,
            "denials": denials,
            "tool_runs": tool_runs,
            "answer": final_text or "(no final summary)",
        }
    except Exception as e:
        root.finish(status="failed", error=str(e))
        return {"trace_id": root.trace_id, "status": "failed", "cost": round(total_cost, 6), "answer": str(e)}
    finally:
        _tracer.emit(root)
        reset_context(tokens)
        get_exporter().flush()


# --------------------------------------------------------------------------
# Optional HTTP server for interactive task submission.
# --------------------------------------------------------------------------
def _serve(port: int) -> None:
    from aiohttp import web

    async def handle_run(request: "web.Request") -> "web.Response":
        try:
            body = await request.json()
        except Exception:
            body = {}
        task = (body.get("task") or "").strip()
        if not task:
            return web.json_response({"error": "missing 'task'"}, status=400)
        import asyncio
        summary = await asyncio.to_thread(run_task, task)
        return web.json_response(summary)

    async def handle_health(_: "web.Request") -> "web.Response":
        return web.json_response({"ok": True, "agent": AGENT_NAME})

    app = web.Application()
    app.router.add_post("/run", handle_run)
    app.router.add_get("/healthz", handle_health)
    print(f"[ops-agent] serving POST /run on :{port}")
    web.run_app(app, port=port, print=None)


def _autonomous_loop(interval: float) -> None:
    while _running:
        task = random.choice(TASKS)
        summary = run_task(task)
        print(
            f"[ops-agent] {summary['status']:<9} ${summary['cost']:.5f} "
            f"tools={summary.get('tool_runs', 0)} denials={summary.get('denials', 0)} "
            f"| {task[:60]}"
        )
        slept = 0.0
        while _running and slept < interval:
            time.sleep(0.25)
            slept += 0.25


def main() -> int:
    endpoint = os.environ.get("COTTONMOUTH_ENDPOINT", "")
    if not endpoint:
        print("COTTONMOUTH_ENDPOINT is required (e.g. http://cottonmouth-backend:8150)", file=sys.stderr)
        return 2

    cottonmouth.configure(export="http", endpoint=endpoint, auto_instrument=True)
    print(f"[ops-agent] {AGENT_NAME} -> {endpoint}/api/spans (model={MODEL_ID}, ws={WORKSPACE})")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    interval = float(os.environ.get("AGENT_INTERVAL", "30"))

    if os.environ.get("AGENT_ONCE", "").lower() in ("1", "true", "yes"):
        print(json.dumps(run_task(random.choice(TASKS)), indent=2))
        return 0

    if os.environ.get("AGENT_SERVE", "").lower() in ("1", "true", "yes"):
        # Background autonomous runs keep the dashboard live; the server lets
        # you submit tasks on demand.
        t = threading.Thread(target=_autonomous_loop, args=(interval,), daemon=True)
        t.start()
        _serve(int(os.environ.get("AGENT_PORT", "8200")))
        return 0

    _autonomous_loop(interval)
    get_exporter().flush()
    print("[ops-agent] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
