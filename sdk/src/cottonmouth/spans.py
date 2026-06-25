from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class Span:
    trace_id: str
    span_id: str = field(default_factory=_uuid)
    parent_span_id: str = ""
    agent_name: str = ""
    agent_version: str = ""
    span_type: str = "agent_run"  # agent_run | llm_call | tool_call | decision | retrieval | permission_check
    name: str = ""
    status: str = "started"  # started | completed | failed | timeout
    start_time: str = field(default_factory=_now_iso)
    end_time: str = ""
    duration_ms: int = 0
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    temperature: float = 0.0
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_output: dict[str, Any] = field(default_factory=dict)
    decision_type: str = ""  # branch | loop_continue | loop_break | delegate | tool_select
    options_considered: list[dict[str, Any]] = field(default_factory=list)
    chosen_option: str = ""
    reasoning: str = ""
    # Permission audit (span_type == "permission_check"): what the agent asked to
    # do, the policy that applied, and whether it was allowed.
    permission_result: str = ""  # allow | deny
    permission_policy: str = ""

    _start_mono: float = field(default_factory=time.monotonic, repr=False)

    def finish(self, status: str = "completed", error: str = "") -> None:
        self.end_time = _now_iso()
        self.duration_ms = int((time.monotonic() - self._start_mono) * 1000)
        self.status = status
        if error:
            self.error = error

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("_start_mono", None)
        return d
