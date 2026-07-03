"""Trace-level evaluation — step efficiency and tool correctness.

This module defines the data contract for trajectory-level evaluation.
Current demo agents only produce terminal audit_log entries (no step events),
so the actual evaluation is not implemented — only the schema and loader.

To enable trace evaluation:
  1. Agent outputs data/trace_log.jsonl with one entry per step
  2. Each entry has at minimum: case_id, step_idx, action
  3. Optional fields: tool_name, args_ok, duration_ms
  4. Configure trace_field_map in agents.yaml if field names differ

Schema:
  Each line in trace_log.jsonl is a JSON object:
  {
    "case_id": "C001",
    "step_idx": 0,
    "action": "tool_call",      // tool_call | reason | route
    "tool_name": "check_rules", // only for tool_call
    "args_ok": true,            // null if not applicable
    "duration_ms": 12
  }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TraceStep:
    """A single step in an agent's execution trace."""
    case_id: str
    step_idx: int
    action: str          # tool_call | reason | route
    tool_name: str = ""
    args_ok: bool | None = None
    duration_ms: float = 0.0
    raw: dict = field(default_factory=dict)


def load_traces(agent_cfg: dict) -> list[TraceStep] | None:
    """Load trace_log.jsonl from the agent's repo, if it exists.

    Returns None if no trace data is available (not an error — demo agents
    don't produce traces). Returns empty list if file exists but is empty.

    Field mapping is configurable via agent_cfg["trace_field_map"]:
      case_id: field name for case ID (default: case_id)
      step_idx: field name for step index (default: step_idx)
      action: field name for action type (default: action)
    """
    repo_path = agent_cfg.get("repo")
    if not repo_path:
        return None

    trace_path = Path(repo_path) / "data" / "trace_log.jsonl"
    if not trace_path.exists():
        return None

    tfm = agent_cfg.get("trace_field_map", {})
    case_id_field = tfm.get("case_id", "case_id")
    step_idx_field = tfm.get("step_idx", "step_idx")
    action_field = tfm.get("action", "action")
    tool_name_field = tfm.get("tool_name", "tool_name")
    args_ok_field = tfm.get("args_ok", "args_ok")
    duration_field = tfm.get("duration_ms", "duration_ms")

    steps = []
    with open(trace_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            steps.append(TraceStep(
                case_id=str(entry.get(case_id_field, "")),
                step_idx=int(entry.get(step_idx_field, 0)),
                action=str(entry.get(action_field, "")),
                tool_name=str(entry.get(tool_name_field, "")),
                args_ok=entry.get(args_ok_field),
                duration_ms=float(entry.get(duration_field, 0) or 0),
                raw=entry,
            ))

    return steps
