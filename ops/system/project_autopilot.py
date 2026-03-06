#!/usr/bin/env python3
"""Project autopilot lane (fail-closed, proof-first, 0-LLM)."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ops.lib.aiops_remote_helpers import (
    TERMINAL_FAIL,
    TERMINAL_RUNNING,
    TERMINAL_SUCCESS,
    TERMINAL_WAITING,
    canonical_novnc_url,
    classify_soma_terminal_status,
    parse_artifact_browse_proof,
    parse_run_poll_response,
)
from ops.lib.artifacts_root import get_artifacts_root
from ops.lib.exec_trigger import TriggerResult, hq_request, trigger_exec
from ops.lib.notifier import (
    _is_valid_webhook_url,
    build_alert_hash,
    resolve_discord_webhook_url,
    send_discord_webhook_alert,
)
from ops.system.soma_preflight import run_soma_preflight

DEFAULT_MAX_SECONDS = 2100
DEFAULT_POLL_INTERVAL = "6..24"
DEFAULT_HQ_BASE = "http://127.0.0.1:8787"
DEFAULT_STATE_ROOT = Path("/var/lib/ai-ops-runner/soma_autopilot")
DOCTOR_TIMEOUT_SEC = 300
SEEN_ALERTS_MAX = 200
RUN_TO_DONE_SCAN_LIMIT = 200
PREFLIGHT_TRANSITIONS_NOTIFY = {
    ("GO", "HUMAN_ONLY"),
    ("HUMAN_ONLY", "GO"),
    ("GO", "NO_GO"),
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"project_autopilot_{ts}_{secrets.token_hex(4)}"


def parse_poll_interval(raw: str) -> tuple[int, int]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("poll interval is required")
    if ".." in text:
        left, right = text.split("..", 1)
        lo = int(left.strip())
        hi = int(right.strip())
    else:
        lo = int(text)
        hi = max(lo, 24)
    if lo < 1 or hi < lo:
        raise ValueError(f"invalid poll interval range: {raw!r}")
    return lo, hi


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n")


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty output")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("output did not contain json object")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("json payload was not an object")
    return parsed


def rel_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def sanitize_error_class(value: Any, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip().upper()
    if not text:
        return default
    text = re.sub(r"[^A-Z0-9_]", "_", text)
    return text or default


def parse_error_class_from_text(raw: str | None) -> str | None:
    text = str(raw or "")
    m = re.search(r"error_class:\s*([A-Z0-9_]+)", text)
    if m:
        return sanitize_error_class(m.group(1), default="")
    return None


def resolve_repo_root() -> Path:
    env_root = os.environ.get("OPENCLAW_REPO_ROOT", "").strip()
    if env_root:
        p = Path(env_root)
        if p.exists():
            return p
    return REPO_ROOT


def resolve_artifacts_root(arg_value: str, repo_root: Path) -> Path:
    if arg_value.strip():
        return Path(arg_value).expanduser()
    return get_artifacts_root(repo_root=repo_root)


def resolve_state_root(arg_value: str, artifacts_root: Path) -> Path:
    if arg_value.strip():
        root = Path(arg_value).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        return root
    try:
        DEFAULT_STATE_ROOT.mkdir(parents=True, exist_ok=True)
        return DEFAULT_STATE_ROOT
    except OSError:
        fallback = artifacts_root / "system" / "project_autopilot_state"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


@contextmanager
def temporary_env(values: dict[str, str]):
    prev: dict[str, str | None] = {}
    for key, value in values.items():
        prev[key] = os.environ.get(key)
        os.environ[key] = value
    try:
        yield
    finally:
        for key, old in prev.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def run_doctor_core(
    *,
    bundle_dir: Path,
    hq_base: str,
    mock: bool,
    mock_status: str,
) -> dict[str, Any]:
    raw_dir = bundle_dir / "raw"
    if mock:
        status = "PASS" if mock_status == "PASS" else "FAIL"
        payload = {
            "ok": status == "PASS",
            "status": status,
            "run_id": f"doctor_matrix_mock_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            "bundle_dir": str(bundle_dir / "doctor_matrix_mock"),
            "failed_checks": [] if status == "PASS" else ["CORE.MOCK.FAIL"],
            "mock": True,
        }
        atomic_write_json(raw_dir / "doctor_matrix_stdout.json", payload)
        atomic_write_text(raw_dir / "doctor_matrix_stderr.txt", "")
        return payload

    cmd = [sys.executable, "-m", "system.doctor_matrix", "--mode", "core"]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["OPENCLAW_REPO_ROOT"] = str(REPO_ROOT)
    env["OPENCLAW_HQ_BASE"] = hq_base
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=DOCTOR_TIMEOUT_SEC,
            check=False,
            cwd=str(REPO_ROOT),
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        atomic_write_text(raw_dir / "doctor_matrix_stdout.txt", str(exc.stdout or ""))
        atomic_write_text(raw_dir / "doctor_matrix_stderr.txt", str(exc.stderr or ""))
        return {
            "ok": False,
            "status": "FAIL",
            "error_class": "DOCTOR_SUBPROCESS_TIMEOUT",
            "rc": -1,
            "stdout_len": len(str(exc.stdout or "")),
            "stderr_tail": str(exc.stderr or "")[-2048:].splitlines()[-30:],
            "cmd": cmd,
        }
    atomic_write_text(raw_dir / "doctor_matrix_stdout.txt", proc.stdout or "")
    atomic_write_text(raw_dir / "doctor_matrix_stderr.txt", proc.stderr or "")
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        stderr_text = proc.stderr or ""
        return {
            "ok": False,
            "status": "FAIL",
            "error_class": "DOCTOR_EMPTY_OUTPUT" if not (proc.stdout or "").strip() else "DOCTOR_SUBPROCESS_FAILED",
            "rc": proc.returncode,
            "stdout_len": len(proc.stdout or ""),
            "stderr_tail": stderr_text[-2048:].splitlines()[-30:],
            "cmd": cmd,
        }
    try:
        payload = parse_json_object(proc.stdout or "")
    except ValueError as exc:
        return {
            "ok": False,
            "status": "FAIL",
            "error_class": "DOCTOR_PARSE_FAILED",
            "rc": proc.returncode,
            "stdout_len": len(proc.stdout or ""),
            "stderr_tail": (proc.stderr or "")[-2048:].splitlines()[-30:],
            "parse_error": str(exc)[:240],
            "cmd": cmd,
        }
    payload["subprocess_exit_code"] = proc.returncode
    return payload


def default_pinned_novnc_url() -> str:
    explicit = os.environ.get("OPENCLAW_PINNED_NOVNC_URL", "").strip()
    if explicit:
        return explicit
    tailscale_host = os.environ.get("OPENCLAW_TAILSCALE_HOSTNAME", "").strip()
    if tailscale_host:
        return canonical_novnc_url(f"https://{tailscale_host}")
    frontdoor = os.environ.get("OPENCLAW_FRONTDOOR_BASE_URL", "").strip()
    if frontdoor:
        return canonical_novnc_url(frontdoor)
    return canonical_novnc_url("https://aiops-1.tailc75c62.ts.net")


def load_action_ids(repo_root: Path) -> set[str]:
    registry = repo_root / "config" / "action_registry.json"
    data = read_json(registry)
    if not data:
        return set()
    actions = data.get("actions")
    if not isinstance(actions, list):
        return set()
    out: set[str] = set()
    for action in actions:
        if not isinstance(action, dict):
            continue
        aid = action.get("id")
        if isinstance(aid, str) and aid.strip():
            out.add(aid.strip())
    return out


def build_autopilot_alert_hash(
    *,
    project: str,
    terminal_status: str,
    error_class: str,
    run_id: str,
) -> str:
    return build_alert_hash(
        event_type=f"{project}:{terminal_status}",
        matrix_status=error_class,
        failed_checks=[run_id],
    )


def format_alert_message(
    *,
    project: str,
    action: str,
    terminal_status: str,
    run_id: str,
    error_class: str,
    proof_path: str,
    novnc_url: str | None,
) -> str:
    lines = [
        "OpenClaw Project Autopilot alert",
        f"- project: `{project}`",
        f"- action: `{action}`",
        f"- terminal_status: `{terminal_status}`",
        f"- run_id: `{run_id}`",
        f"- error_class: `{error_class}`",
        f"- proof_path: `{proof_path}`",
    ]
    if novnc_url:
        lines.append(f"- novnc_url: {novnc_url}")
    return "\n".join(lines)


def base_alert_payload(webhook_preflight: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "SKIPPED",
        "needed": False,
        "sent": False,
        "deduped": False,
        "hash": "",
        "terminal_error_class": "",
        "error_class": "",
        "message": "",
        "notify": webhook_preflight if not webhook_preflight.get("ok") else {},
    }


def format_preflight_transition_message(
    *,
    current_status: str,
    reasons: list[str],
    proof_path: str,
    novnc_url: str | None,
    gate_expiry: str | None,
) -> str:
    reason_text = ", ".join(reasons) if reasons else "none"
    if current_status == "HUMAN_ONLY":
        lines = [
            "Soma needs you: Kajabi login",
            f"reason_codes: {reason_text}",
            f"proof_path: {proof_path}",
            f"novnc_url: {novnc_url or ''}",
            f"gate_expiry: {gate_expiry or ''}",
            "instruction: Log in + 2FA then CLOSE noVNC window to release profile lock",
        ]
        return "\n".join(lines)
    if current_status == "NO_GO":
        return (
            "Soma preflight NO_GO\n"
            f"reason_codes: {reason_text}\n"
            f"proof_path: {proof_path}"
        )
    return (
        "Soma preflight GO\n"
        f"reason_codes: {reason_text}\n"
        f"proof_path: {proof_path}"
    )


def send_preflight_transition_alert(
    *,
    previous_status: str | None,
    current_status: str,
    reasons: list[str],
    proof_path: str,
    novnc_url: str | None,
    gate_expiry: str | None,
    state: dict[str, Any],
    webhook_preflight: dict[str, Any],
) -> dict[str, Any]:
    base = base_alert_payload(webhook_preflight)
    prev = previous_status or ""
    curr = current_status.upper()

    should_notify = False
    if prev:
        should_notify = (prev, curr) in PREFLIGHT_TRANSITIONS_NOTIFY
    elif curr in {"HUMAN_ONLY", "NO_GO"}:
        should_notify = True
    if not should_notify:
        return base

    alert_hash = build_alert_hash(
        event_type=f"preflight:{prev or 'NONE'}->{curr}",
        matrix_status=curr,
        failed_checks=reasons,
    )
    prior_hash = str(state.get("preflight_last_alert_hash") or "")
    if prior_hash and prior_hash == alert_hash:
        return {
            **base,
            "status": "DEDUPED",
            "needed": True,
            "deduped": True,
            "hash": alert_hash,
        }

    message = format_preflight_transition_message(
        current_status=curr,
        reasons=reasons,
        proof_path=proof_path,
        novnc_url=novnc_url,
        gate_expiry=gate_expiry,
    )
    if not webhook_preflight.get("ok"):
        notify = dict(webhook_preflight)
    else:
        notify = send_discord_webhook_alert(content=message)
    sent = bool(notify.get("ok"))
    if sent:
        state["preflight_last_alert_hash"] = alert_hash
    return {
        **base,
        "status": "SENT" if sent else "ERROR",
        "needed": True,
        "sent": sent,
        "hash": alert_hash,
        "error_class": str(notify.get("error_class") or ""),
        "message": str(notify.get("message") or ""),
        "notify": notify,
    }


@dataclass
class PollResult:
    terminal_status: str
    run_status: str
    run_obj: dict[str, Any]
    poll_count: int
    elapsed_sec: float
    run_artifact_dir: str | None
    run_artifact_dir_resolution: str
    run_to_done_dir: str | None
    proof_payload: dict[str, Any] | None
    precheck_payload: dict[str, Any] | None
    proof_path: str | None
    precheck_path: str | None
    browse_error: str | None
    run_to_done_resolution_method: str
    pointer_console_run_id_seen: str | None
    pointer_run_dir_seen: str | None
    fs_scan_checked_count: int
    pointer_http_code: int | None
    novnc_url: str | None


class BaseHQClient:
    def trigger(self, project: str, action: str) -> TriggerResult:  # pragma: no cover - interface
        raise NotImplementedError

    def poll_run(self, run_id: str) -> tuple[int, str]:  # pragma: no cover - interface
        raise NotImplementedError

    def browse(self, rel_path_value: str) -> tuple[int, str]:  # pragma: no cover - interface
        raise NotImplementedError

    def sleep(self, seconds: float) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class RealHQClient(BaseHQClient):
    def __init__(self, hq_base: str) -> None:
        self.hq_base = hq_base.rstrip("/") or DEFAULT_HQ_BASE

    def trigger(self, project: str, action: str) -> TriggerResult:
        with temporary_env({"OPENCLAW_HQ_BASE": self.hq_base}):
            return trigger_exec(project, action)

    def poll_run(self, run_id: str) -> tuple[int, str]:
        return hq_request("GET", f"/api/runs?id={run_id}", timeout=20, base_url=self.hq_base)

    def browse(self, rel_path_value: str) -> tuple[int, str]:
        encoded = quote(rel_path_value.strip("/"), safe="")
        return hq_request(
            "GET",
            f"/api/artifacts/browse?path={encoded}",
            timeout=20,
            base_url=self.hq_base,
        )

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))


@dataclass
class MockActionSpec:
    trigger: dict[str, Any]
    polls: list[dict[str, Any]]


@dataclass
class MockSpec:
    actions: dict[str, MockActionSpec]
    run_to_done_entries: list[str]
    run_to_done_dir: str | None
    proof_payload: dict[str, Any] | None
    precheck_payload: dict[str, Any] | None
    pointer_payload: dict[str, Any] | None


def _mock_run_to_done_entry_from_run_id(run_id: str) -> str:
    m = re.match(r"^(\d{14})-", run_id)
    if m:
        ts = m.group(1)
        date = ts[:8]
        clock = ts[8:]
        return f"run_to_done_{date}T{clock}Z_mock0000"
    now_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_to_done_{now_ts}_mock0000"


def default_mock_spec(args: argparse.Namespace) -> MockSpec:
    run_id = (args.mock_run_id or "").strip() or "20260305120000-mock"
    terminal = str(args.mock_terminal_status).strip().upper()
    err = sanitize_error_class(args.mock_error_class, default="MOCK_FAIL")
    run_to_done_entry = _mock_run_to_done_entry_from_run_id(run_id)
    run_to_done_dir = f"artifacts/soma_kajabi/run_to_done/{run_to_done_entry}"
    if terminal == TERMINAL_WAITING:
        proof_payload = {
            "status": TERMINAL_WAITING,
            "novnc_url": "https://aiops-1.tailc75c62.ts.net/novnc/vnc.html?autoconnect=1&reconnect=true&reconnect_delay=2000&path=/websockify",
        }
        run_status = "success"
    elif terminal == TERMINAL_SUCCESS:
        proof_payload = {
            "status": TERMINAL_SUCCESS,
            "acceptance_path": "artifacts/soma_kajabi/acceptance/mock_run",
        }
        run_status = "success"
    else:
        proof_payload = {
            "status": TERMINAL_FAIL,
            "error_class": err,
        }
        run_status = "failure"
    action_specs = {
        args.action: MockActionSpec(
            trigger={
                "state": "ACCEPTED",
                "status_code": 202,
                "run_id": run_id,
                "body": {"ok": True, "run_id": run_id, "status": "running"},
            },
            polls=[
                {"http_code": 200, "run": {"run_id": run_id, "status": "running", "artifact_dir": "artifacts/hostd/mock_soma"}},
                {"http_code": 200, "run": {"run_id": run_id, "status": run_status, "artifact_dir": "artifacts/hostd/mock_soma"}},
            ],
        ),
    }
    if str(args.mock_validator_status).upper() in {"PASS", "FAIL"}:
        validator_run = "20260305121000-mockv"
        validator_status = "success" if str(args.mock_validator_status).upper() == "PASS" else "failure"
        action_specs["soma_kajabi_verify_business_dod"] = MockActionSpec(
            trigger={
                "state": "ACCEPTED",
                "status_code": 202,
                "run_id": validator_run,
                "body": {"ok": True, "run_id": validator_run, "status": "running"},
            },
            polls=[
                {"http_code": 200, "run": {"run_id": validator_run, "status": validator_status, "artifact_dir": "artifacts/hostd/mock_validator"}},
            ],
        )
    return MockSpec(
        actions=action_specs,
        run_to_done_entries=[run_to_done_entry],
        run_to_done_dir=run_to_done_dir,
        proof_payload=proof_payload,
        precheck_payload={"status": "PASS"},
        pointer_payload=None,
    )


def load_mock_spec(path: str, defaults: MockSpec) -> MockSpec:
    if not path.strip():
        return defaults
    loaded = read_json(Path(path).expanduser())
    if not loaded:
        return defaults
    actions = dict(defaults.actions)
    loaded_actions = loaded.get("actions")
    if isinstance(loaded_actions, dict):
        for action_name, raw_spec in loaded_actions.items():
            if not isinstance(action_name, str) or not isinstance(raw_spec, dict):
                continue
            trigger_part = raw_spec.get("trigger")
            poll_part = raw_spec.get("polls")
            trigger_data = trigger_part if isinstance(trigger_part, dict) else {}
            polls_data = poll_part if isinstance(poll_part, list) else []
            actions[action_name] = MockActionSpec(trigger=trigger_data, polls=[p for p in polls_data if isinstance(p, dict)])
    run_to_done_entries = defaults.run_to_done_entries
    raw_entries = loaded.get("run_to_done_entries")
    if isinstance(raw_entries, list):
        run_to_done_entries = [str(e) for e in raw_entries if str(e).strip()]
    run_to_done_dir = str(loaded.get("run_to_done_dir") or defaults.run_to_done_dir or "")
    return MockSpec(
        actions=actions,
        run_to_done_entries=run_to_done_entries,
        run_to_done_dir=run_to_done_dir or None,
        proof_payload=loaded.get("proof_payload") if isinstance(loaded.get("proof_payload"), dict) else defaults.proof_payload,
        precheck_payload=loaded.get("precheck_payload") if isinstance(loaded.get("precheck_payload"), dict) else defaults.precheck_payload,
        pointer_payload=loaded.get("pointer_payload") if isinstance(loaded.get("pointer_payload"), dict) else defaults.pointer_payload,
    )


class MockHQClient(BaseHQClient):
    def __init__(self, spec: MockSpec):
        self.spec = spec
        self._run_action: dict[str, str] = {}
        self._poll_index: dict[str, int] = {}

    def trigger(self, project: str, action: str) -> TriggerResult:
        _ = project
        action_spec = self.spec.actions.get(action)
        if action_spec is None:
            run_id = f"20260305120000-{sanitize_error_class(action, default='MOCK').lower()}"
            return TriggerResult(
                status_code=202,
                state="ACCEPTED",
                message=f"mock accepted action={action}",
                run_id=run_id,
                body={"ok": True, "run_id": run_id, "status": "running"},
            )
        trigger = action_spec.trigger
        state = str(trigger.get("state") or "ACCEPTED").upper()
        status_code = int(trigger.get("status_code") or (409 if state == "ALREADY_RUNNING" else 202))
        run_id = str(trigger.get("run_id") or "").strip() or None
        body = trigger.get("body")
        payload = body if isinstance(body, dict) else {}
        if run_id and "run_id" not in payload and state == "ACCEPTED":
            payload["run_id"] = run_id
        if run_id and "active_run_id" not in payload and state == "ALREADY_RUNNING":
            payload["active_run_id"] = run_id
        message = str(trigger.get("message") or f"mock trigger state={state} action={action}")
        result = TriggerResult(
            status_code=status_code,
            state=state,
            message=message,
            run_id=run_id,
            body=payload,
        )
        if result.run_id:
            self._run_action[result.run_id] = action
            self._poll_index.setdefault(result.run_id, 0)
        return result

    def poll_run(self, run_id: str) -> tuple[int, str]:
        action = self._run_action.get(run_id, "")
        action_spec = self.spec.actions.get(action)
        if not action_spec or not action_spec.polls:
            payload = {"ok": True, "run": {"run_id": run_id, "status": "success", "artifact_dir": "artifacts/hostd/mock"}}
            return 200, json.dumps(payload)
        idx = self._poll_index.get(run_id, 0)
        poll_spec = action_spec.polls[min(idx, len(action_spec.polls) - 1)]
        self._poll_index[run_id] = idx + 1
        code = int(poll_spec.get("http_code") or 200)
        if "body" in poll_spec and isinstance(poll_spec.get("body"), dict):
            payload = poll_spec["body"]
        else:
            run_obj = poll_spec.get("run")
            if not isinstance(run_obj, dict):
                run_obj = {"run_id": run_id, "status": "success", "artifact_dir": "artifacts/hostd/mock"}
            payload = {"ok": True, "run": run_obj}
        return code, json.dumps(payload)

    def browse(self, rel_path_value: str) -> tuple[int, str]:
        rel = rel_path_value.strip("/")
        if rel == "soma_kajabi/run_to_done/LATEST_RUN.json" and self.spec.pointer_payload is not None:
            return 200, json.dumps(
                {
                    "content": json.dumps(self.spec.pointer_payload),
                    "contentType": "json",
                    "fileName": "LATEST_RUN.json",
                    "entries": [],
                }
            )
        if rel == "soma_kajabi/run_to_done":
            entries = [{"name": name, "type": "dir"} for name in self.spec.run_to_done_entries]
            return 200, json.dumps({"entries": entries})
        if rel.endswith("PROOF.json") and self.spec.proof_payload is not None:
            return 200, json.dumps(
                {
                    "content": json.dumps(self.spec.proof_payload),
                    "contentType": "json",
                    "fileName": "PROOF.json",
                    "entries": [],
                }
            )
        if rel.endswith("PRECHECK.json") and self.spec.precheck_payload is not None:
            return 200, json.dumps(
                {
                    "content": json.dumps(self.spec.precheck_payload),
                    "contentType": "json",
                    "fileName": "PRECHECK.json",
                    "entries": [],
                }
            )
        return 404, json.dumps({"ok": False, "error": f"mock browse missing: {rel}"})

    def sleep(self, seconds: float) -> None:
        _ = seconds


def write_raw_response(path: Path, http_code: int, body: str) -> None:
    content = {
        "http_code": int(http_code),
        "body": body,
    }
    atomic_write_json(path, content)


def try_fetch_artifact_json(
    *,
    client: BaseHQClient,
    raw_dir: Path,
    rel_path_value: str,
    raw_name: str,
) -> tuple[int, dict[str, Any] | None]:
    code, body = client.browse(rel_path_value)
    write_raw_response(raw_dir / raw_name, code, body)
    if code != 200:
        return code, None
    payload = parse_artifact_browse_proof(body)
    if isinstance(payload, dict):
        return code, payload
    return code, None


def rel_artifact_path(path: Path, artifacts_root: Path) -> str:
    try:
        relative = path.resolve().relative_to(artifacts_root.resolve())
        return f"artifacts/{relative.as_posix()}"
    except ValueError:
        return str(path)


def _result_from_local_run_dir(
    *,
    out: dict[str, Any],
    dir_path: Path,
    artifacts_root: Path,
    resolution_method: str,
) -> dict[str, Any]:
    run_dir = rel_artifact_path(dir_path, artifacts_root)
    proof_file = dir_path / "PROOF.json"
    precheck_file = dir_path / "PRECHECK.json"
    out["run_to_done_dir"] = run_dir
    out["run_artifact_dir"] = run_dir
    out["run_artifact_dir_resolution"] = resolution_method
    out["run_to_done_resolution_method"] = resolution_method
    if proof_file.exists():
        out["proof_path"] = rel_artifact_path(proof_file, artifacts_root)
        out["proof_payload"] = read_json(proof_file)
    if precheck_file.exists():
        out["precheck_path"] = rel_artifact_path(precheck_file, artifacts_root)
        out["precheck_payload"] = read_json(precheck_file)
    return out


def _resolve_run_to_done_by_fs_scan(
    *,
    remote_run_id: str,
    artifacts_root: Path,
    limit: int = RUN_TO_DONE_SCAN_LIMIT,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "matched_dir": None,
        "proof_payload": None,
        "precheck_payload": None,
        "fs_scan_checked_count": 0,
    }
    run_root = artifacts_root / "soma_kajabi" / "run_to_done"
    if not remote_run_id.strip() or not run_root.is_dir():
        return out
    try:
        candidates = sorted(
            (p for p in run_root.glob("run_to_done_*") if p.is_dir()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return out

    for dir_path in candidates[:limit]:
        out["fs_scan_checked_count"] += 1
        proof_payload = read_json(dir_path / "PROOF.json")
        if not isinstance(proof_payload, dict):
            continue
        console_run_id = str(proof_payload.get("console_run_id") or "").strip()
        if console_run_id != remote_run_id:
            continue
        out["matched_dir"] = dir_path
        out["proof_payload"] = proof_payload
        out["precheck_payload"] = read_json(dir_path / "PRECHECK.json")
        return out
    return out


def _read_pointer_payload(artifacts_root: Path) -> dict[str, Any] | None:
    pointer_path = artifacts_root / "soma_kajabi" / "run_to_done" / "LATEST_RUN.json"
    return read_json(pointer_path)


def build_webhook_preflight() -> dict[str, Any]:
    webhook_url, source = resolve_discord_webhook_url()
    if _is_valid_webhook_url(webhook_url):
        return {
            "ok": True,
            "source": source,
            "error_class": None,
            "status_code": None,
            "message": "",
        }
    return {
        "ok": False,
        "source": source,
        "error_class": "DISCORD_WEBHOOK_INVALID",
        "status_code": None,
        "message": "Discord webhook URL is missing or invalid.",
    }


def resolve_run_to_done_artifacts(
    *,
    client: BaseHQClient,
    remote_run_id: str,
    raw_dir: Path,
    artifacts_root: Path,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "run_artifact_dir": None,
        "run_artifact_dir_resolution": "none",
        "run_to_done_resolution_method": "none",
        "run_to_done_dir": None,
        "proof_payload": None,
        "precheck_payload": None,
        "proof_path": None,
        "precheck_path": None,
        "browse_error": None,
        "proof_http_code": None,
        "precheck_http_code": None,
        "pointer_console_run_id_seen": None,
        "pointer_run_dir_seen": None,
        "fs_scan_checked_count": 0,
        "pointer_http_code": None,
    }
    reasons: list[str] = []

    fs_result = _resolve_run_to_done_by_fs_scan(
        remote_run_id=remote_run_id,
        artifacts_root=artifacts_root,
    )
    out["fs_scan_checked_count"] = int(fs_result.get("fs_scan_checked_count") or 0)
    matched_dir = fs_result.get("matched_dir")
    if isinstance(matched_dir, Path):
        out = _result_from_local_run_dir(
            out=out,
            dir_path=matched_dir,
            artifacts_root=artifacts_root,
            resolution_method="fs_scan",
        )
        if isinstance(fs_result.get("proof_payload"), dict):
            out["proof_payload"] = fs_result["proof_payload"]
        if isinstance(fs_result.get("precheck_payload"), dict):
            out["precheck_payload"] = fs_result["precheck_payload"]
        return out
    if remote_run_id.strip():
        reasons.append("fs_scan_no_match")

    pointer_payload = _read_pointer_payload(artifacts_root)
    if pointer_payload is not None:
        raw_console = pointer_payload.get("console_run_id")
        if not isinstance(raw_console, str) or not raw_console.strip():
            legacy_run_id = pointer_payload.get("run_id")
            raw_console = legacy_run_id if isinstance(legacy_run_id, str) else ""
        pointer_console_run_id = raw_console.strip()
        raw_run_dir = pointer_payload.get("run_dir")
        pointer_run_dir = raw_run_dir.strip() if isinstance(raw_run_dir, str) else ""
        out["pointer_console_run_id_seen"] = pointer_console_run_id or None
        out["pointer_run_dir_seen"] = pointer_run_dir or None
        if pointer_console_run_id and pointer_run_dir and pointer_console_run_id == remote_run_id:
            dir_path = artifacts_root / "soma_kajabi" / "run_to_done" / pointer_run_dir
            if dir_path.is_dir():
                return _result_from_local_run_dir(
                    out=out,
                    dir_path=dir_path,
                    artifacts_root=artifacts_root,
                    resolution_method="pointer",
                )
            reasons.append(f"pointer_run_dir_missing:{pointer_run_dir}")
        elif not pointer_console_run_id:
            reasons.append("pointer_console_run_id_missing")
        elif not pointer_run_dir:
            reasons.append("pointer_run_dir_missing")
        else:
            reasons.append(
                f"pointer_console_run_id_mismatch:{pointer_console_run_id}!={remote_run_id}"
            )
        out["pointer_http_code"] = 200
    else:
        pointer_code, pointer_body = client.browse("soma_kajabi/run_to_done/LATEST_RUN.json")
        out["pointer_http_code"] = int(pointer_code)
        write_raw_response(raw_dir / "browse_run_to_done_pointer.json", pointer_code, pointer_body)
        if pointer_code == 200:
            remote_pointer = parse_artifact_browse_proof(pointer_body)
            if isinstance(remote_pointer, dict):
                raw_console = remote_pointer.get("console_run_id")
                if not isinstance(raw_console, str) or not raw_console.strip():
                    legacy_run_id = remote_pointer.get("run_id")
                    raw_console = legacy_run_id if isinstance(legacy_run_id, str) else ""
                pointer_console_run_id = raw_console.strip()
                raw_run_dir = remote_pointer.get("run_dir")
                pointer_run_dir = raw_run_dir.strip() if isinstance(raw_run_dir, str) else ""
                out["pointer_console_run_id_seen"] = pointer_console_run_id or None
                out["pointer_run_dir_seen"] = pointer_run_dir or None
                if pointer_console_run_id and pointer_run_dir and pointer_console_run_id == remote_run_id:
                    run_dir = f"artifacts/soma_kajabi/run_to_done/{pointer_run_dir}"
                    out["run_to_done_dir"] = run_dir
                    out["run_artifact_dir"] = run_dir
                    out["run_artifact_dir_resolution"] = "pointer"
                    out["run_to_done_resolution_method"] = "pointer"
                    proof_rel = f"{run_dir.removeprefix('artifacts/').rstrip('/')}/PROOF.json"
                    precheck_rel = f"{run_dir.removeprefix('artifacts/').rstrip('/')}/PRECHECK.json"
                    proof_code, proof_payload = try_fetch_artifact_json(
                        client=client,
                        raw_dir=raw_dir,
                        rel_path_value=proof_rel,
                        raw_name="browse_run_to_done_proof.json",
                    )
                    precheck_code, precheck_payload = try_fetch_artifact_json(
                        client=client,
                        raw_dir=raw_dir,
                        rel_path_value=precheck_rel,
                        raw_name="browse_run_to_done_precheck.json",
                    )
                    out["proof_http_code"] = proof_code
                    out["precheck_http_code"] = precheck_code
                    if proof_payload is not None:
                        out["proof_payload"] = proof_payload
                        out["proof_path"] = f"{run_dir.rstrip('/')}/PROOF.json"
                    if precheck_payload is not None:
                        out["precheck_payload"] = precheck_payload
                        out["precheck_path"] = f"{run_dir.rstrip('/')}/PRECHECK.json"
                    return out
                if not pointer_console_run_id:
                    reasons.append("pointer_console_run_id_missing")
                elif not pointer_run_dir:
                    reasons.append("pointer_run_dir_missing")
                else:
                    reasons.append(
                        f"pointer_console_run_id_mismatch:{pointer_console_run_id}!={remote_run_id}"
                    )
            else:
                reasons.append("pointer_parse_failed")
        else:
            reasons.append(f"pointer_http_{pointer_code}")

    if reasons:
        out["browse_error"] = "; ".join(reasons)
    return out


def derive_error_class(
    *,
    run_obj: dict[str, Any],
    proof_payload: dict[str, Any] | None,
    precheck_payload: dict[str, Any] | None,
    default: str,
) -> str:
    for payload in (proof_payload, precheck_payload):
        if isinstance(payload, dict):
            value = payload.get("error_class")
            if isinstance(value, str) and value.strip():
                return sanitize_error_class(value)
    run_value = run_obj.get("error_class")
    if isinstance(run_value, str) and run_value.strip():
        return sanitize_error_class(run_value)
    summary_value = run_obj.get("error_summary")
    parsed = parse_error_class_from_text(summary_value if isinstance(summary_value, str) else None)
    if parsed:
        return parsed
    return sanitize_error_class(default)


def poll_to_terminal(
    *,
    client: BaseHQClient,
    remote_run_id: str,
    max_seconds: int,
    poll_min: int,
    poll_max: int,
    raw_dir: Path,
    artifacts_root: Path,
) -> PollResult:
    started = time.monotonic()
    poll_interval = poll_min
    poll_count = 0
    run_status = ""
    run_obj: dict[str, Any] = {}
    run_artifact_dir: str | None = None
    terminal_status = TERMINAL_RUNNING
    novnc_url: str | None = None
    run_to_done_info: dict[str, Any] | None = None
    previous_status: str | None = None

    while (time.monotonic() - started) <= max_seconds:
        poll_count += 1
        code, body = client.poll_run(remote_run_id)
        write_raw_response(raw_dir / f"poll_{poll_count:03d}.json", code, body)
        if code == 200:
            parsed = parse_run_poll_response(body)
            run_status = str(parsed.get("status") or "")
            run_obj = parsed.get("run") if isinstance(parsed.get("run"), dict) else {}
            if parsed.get("artifact_dir"):
                run_artifact_dir = str(parsed["artifact_dir"])
            if run_status != previous_status:
                poll_interval = poll_min
                previous_status = run_status
            if run_to_done_info is None or str(
                run_to_done_info.get("run_to_done_resolution_method") or "none"
            ) == "none":
                run_to_done_info = resolve_run_to_done_artifacts(
                    client=client,
                    remote_run_id=remote_run_id,
                    raw_dir=raw_dir,
                    artifacts_root=artifacts_root,
                )
            proof_payload = run_to_done_info.get("proof_payload") if run_to_done_info else None
            classified = classify_soma_terminal_status(run_status, proof_payload if isinstance(proof_payload, dict) else None)
            terminal_status = str(classified.get("terminal_status") or TERMINAL_RUNNING)
            novnc_url = classified.get("novnc_url") if isinstance(classified.get("novnc_url"), str) else None
            if terminal_status != TERMINAL_RUNNING:
                break
        elapsed = time.monotonic() - started
        if elapsed >= max_seconds:
            break
        client.sleep(poll_interval)
        poll_interval = min(poll_interval * 2, poll_max)

    elapsed_sec = round(time.monotonic() - started, 3)
    if run_to_done_info is None:
        run_to_done_info = resolve_run_to_done_artifacts(
            client=client,
            remote_run_id=remote_run_id,
            raw_dir=raw_dir,
            artifacts_root=artifacts_root,
        )
    if terminal_status == TERMINAL_RUNNING:
        terminal_status = TERMINAL_FAIL
    run_to_done_dir = run_to_done_info.get("run_to_done_dir")
    resolved_run_artifact_dir = run_to_done_info.get("run_artifact_dir")
    if isinstance(resolved_run_artifact_dir, str) and resolved_run_artifact_dir.strip():
        run_artifact_dir = resolved_run_artifact_dir
    return PollResult(
        terminal_status=terminal_status,
        run_status=run_status,
        run_obj=run_obj,
        poll_count=poll_count,
        elapsed_sec=elapsed_sec,
        run_artifact_dir=run_artifact_dir,
        run_artifact_dir_resolution=str(run_to_done_info.get("run_artifact_dir_resolution") or "none"),
        run_to_done_dir=run_to_done_dir if isinstance(run_to_done_dir, str) else None,
        proof_payload=run_to_done_info.get("proof_payload"),
        precheck_payload=run_to_done_info.get("precheck_payload"),
        proof_path=run_to_done_info.get("proof_path"),
        precheck_path=run_to_done_info.get("precheck_path"),
        browse_error=run_to_done_info.get("browse_error"),
        run_to_done_resolution_method=str(
            run_to_done_info.get("run_to_done_resolution_method") or "none"
        ),
        pointer_console_run_id_seen=run_to_done_info.get("pointer_console_run_id_seen"),
        pointer_run_dir_seen=run_to_done_info.get("pointer_run_dir_seen"),
        fs_scan_checked_count=int(run_to_done_info.get("fs_scan_checked_count") or 0),
        pointer_http_code=run_to_done_info.get("pointer_http_code"),
        novnc_url=novnc_url,
    )


def poll_generic_action(
    *,
    client: BaseHQClient,
    run_id: str,
    max_seconds: int,
    poll_min: int,
    poll_max: int,
    raw_dir: Path,
    raw_prefix: str,
) -> dict[str, Any]:
    started = time.monotonic()
    poll_interval = poll_min
    count = 0
    run_obj: dict[str, Any] = {}
    run_status = ""
    while (time.monotonic() - started) <= max_seconds:
        count += 1
        code, body = client.poll_run(run_id)
        write_raw_response(raw_dir / f"{raw_prefix}_poll_{count:03d}.json", code, body)
        if code == 200:
            parsed = parse_run_poll_response(body)
            run_obj = parsed.get("run") if isinstance(parsed.get("run"), dict) else {}
            run_status = str(parsed.get("status") or "")
            if run_status not in {"running", "queued"}:
                break
        elapsed = time.monotonic() - started
        if elapsed >= max_seconds:
            break
        client.sleep(poll_interval)
        poll_interval = min(poll_interval * 2, poll_max)
    elapsed_sec = round(time.monotonic() - started, 3)
    terminal = TERMINAL_SUCCESS if run_status == "success" else TERMINAL_FAIL
    if run_status in {"running", "queued", ""}:
        terminal = TERMINAL_FAIL
    return {
        "terminal_status": terminal,
        "run_status": run_status,
        "run_obj": run_obj,
        "poll_count": count,
        "elapsed_sec": elapsed_sec,
    }


def send_terminal_alert(
    *,
    project: str,
    action: str,
    terminal_status: str,
    remote_run_id: str,
    error_class: str,
    proof_path: str,
    novnc_url: str | None,
    state: dict[str, Any],
    webhook_preflight: dict[str, Any],
) -> dict[str, Any]:
    try:
        terminal = terminal_status.upper()
        base = {
            "status": "SKIPPED",
            "needed": False,
            "sent": False,
            "deduped": False,
            "hash": "",
            "terminal_error_class": error_class,
            "error_class": "",
            "message": "",
            "notify": webhook_preflight if not webhook_preflight.get("ok") else {},
        }
        if terminal not in {TERMINAL_WAITING, TERMINAL_FAIL}:
            return base
        alert_hash = build_autopilot_alert_hash(
            project=project,
            terminal_status=terminal,
            error_class=error_class,
            run_id=remote_run_id,
        )
        seen = state.get("seen_alert_hashes")
        if not isinstance(seen, list):
            seen = []
        if alert_hash in seen:
            return {
                **base,
                "status": "DEDUPED",
                "needed": True,
                "deduped": True,
                "hash": alert_hash,
            }
        message = format_alert_message(
            project=project,
            action=action,
            terminal_status=terminal,
            run_id=remote_run_id,
            error_class=error_class,
            proof_path=proof_path,
            novnc_url=novnc_url,
        )
        if not webhook_preflight.get("ok"):
            notify = dict(webhook_preflight)
        else:
            notify = send_discord_webhook_alert(content=message)
        sent = bool(notify.get("ok"))
        if sent:
            seen = [*seen, alert_hash][-SEEN_ALERTS_MAX:]
            state["seen_alert_hashes"] = seen
        return {
            **base,
            "status": "SENT" if sent else "ERROR",
            "needed": True,
            "sent": sent,
            "hash": alert_hash,
            "error_class": str(notify.get("error_class") or ""),
            "message": str(notify.get("message") or ""),
            "notify": notify,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "ERROR",
            "needed": True,
            "sent": False,
            "deduped": False,
            "hash": "",
            "terminal_error_class": error_class,
            "error_class": "DISCORD_UNKNOWN",
            "message": f"send_terminal_alert raised {type(exc).__name__}: {str(exc)[:240]}",
            "notify": {
                "ok": False,
                "error_class": "DISCORD_UNKNOWN",
                "message": f"send_terminal_alert raised {type(exc).__name__}: {str(exc)[:240]}",
            },
        }


def format_warning_entry(item: Any) -> str:
    if isinstance(item, dict):
        warning = str(item.get("warning") or "WARNING").strip() or "WARNING"
        validator = str(item.get("validator") or "").strip()
        error_class = str(item.get("error_class") or "").strip()
        parts = [warning]
        if validator:
            parts.append(f"validator={validator}")
        if error_class:
            parts.append(f"error_class={error_class}")
        return " ".join(parts)
    return str(item)


def build_summary(result: dict[str, Any]) -> str:
    lines = [
        f"# Project Autopilot — {result.get('run_id')}",
        "",
        f"- Project: `{result.get('project')}`",
        f"- Action: `{result.get('action')}`",
        f"- Status: **{result.get('status')}**",
        f"- Started at: `{result.get('started_at')}`",
        f"- Finished at: `{result.get('finished_at')}`",
        f"- Doctor status: `{result.get('doctor', {}).get('status')}`",
        f"- Remote run id: `{result.get('remote_run_id')}`",
        f"- Poll count: `{result.get('poll', {}).get('poll_count')}`",
        f"- Poll elapsed sec: `{result.get('poll', {}).get('elapsed_sec')}`",
        f"- Run artifact resolution: `{result.get('run_artifact_dir_resolution')}`",
    ]
    preflight = result.get("preflight") if isinstance(result.get("preflight"), dict) else {}
    if preflight:
        lines.extend(
            [
                f"- Preflight status: `{preflight.get('status')}`",
                f"- Preflight reasons: `{', '.join(preflight.get('reasons') or [])}`",
                f"- Preflight proof: `{preflight.get('result_path')}`",
            ]
        )
    links = result.get("links") if isinstance(result.get("links"), dict) else {}
    if links:
        lines.extend(
            [
                f"- run_to_done_dir: `{links.get('run_to_done_dir')}`",
                f"- proof_path: `{links.get('proof_path')}`",
                f"- precheck_path: `{links.get('precheck_path')}`",
            ]
        )
    alert = result.get("alert") if isinstance(result.get("alert"), dict) else {}
    if alert:
        lines.extend(
            [
                f"- Alert status: `{alert.get('status')}`",
                f"- Alert needed: `{alert.get('needed')}`",
                f"- Alert sent: `{alert.get('sent')}`",
                f"- Alert deduped: `{alert.get('deduped')}`",
            ]
        )
        if alert.get("error_class"):
            lines.append(f"- Alert error_class: `{alert.get('error_class')}`")
        if alert.get("message"):
            lines.append(f"- Alert message: `{alert.get('message')}`")
    validators = result.get("validators")
    if isinstance(validators, list) and validators:
        lines.extend(["", "## Validators"])
        for item in validators:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('action')}` => `{item.get('status')}` "
                f"(run_id={item.get('run_id')}, terminal={item.get('terminal_status')})"
            )
    warnings = result.get("warnings")
    if isinstance(warnings, list) and warnings:
        lines.extend(["", "## Warnings"])
        lines.extend([f"- {format_warning_entry(w)}" for w in warnings if str(w).strip()])
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenClaw project autopilot lane")
    parser.add_argument("--project", default="soma_kajabi")
    parser.add_argument("--action", default="soma_run_to_done")
    parser.add_argument("--max-seconds", type=int, default=DEFAULT_MAX_SECONDS)
    parser.add_argument(
        "--poll-interval",
        default=DEFAULT_POLL_INTERVAL,
        help="Polling interval range as min..max (seconds), default 6..24",
    )
    parser.add_argument("--hq-base", default=DEFAULT_HQ_BASE)
    parser.add_argument("--state-root", default="")
    parser.add_argument("--artifacts-root", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--validator-actions",
        default="soma_kajabi_verify_business_dod",
        help="Comma-separated validator action ids to run after SUCCESS if present in registry",
    )
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--mock-doctor-status", choices=("PASS", "FAIL"), default="PASS")
    parser.add_argument("--mock-terminal-status", choices=("SUCCESS", "WAITING_FOR_HUMAN", "FAIL"), default="SUCCESS")
    parser.add_argument("--mock-run-id", default="20260305120000-mock")
    parser.add_argument("--mock-error-class", default="MOCK_FAIL")
    parser.add_argument("--mock-hq-file", default="")
    parser.add_argument("--mock-validator-status", choices=("SKIP", "PASS", "FAIL"), default="SKIP")
    parser.add_argument("--exit-nonzero-on-terminal-fail", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or []))
    poll_min, poll_max = parse_poll_interval(args.poll_interval)
    if args.max_seconds < 1:
        raise ValueError("--max-seconds must be >= 1")

    repo_root = resolve_repo_root()
    artifacts_root = resolve_artifacts_root(args.artifacts_root, repo_root)
    run_id = args.run_id.strip() or build_run_id()
    bundle_dir = artifacts_root / "system" / "project_autopilot" / run_id
    raw_dir = bundle_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    state_root = resolve_state_root(args.state_root, artifacts_root)
    state_path = state_root / f"{args.project}.json"
    try:
        atomic_write_text(state_root / "enabled.txt", "1\n")
    except OSError:
        pass
    state = read_json(state_path) or {}

    if args.mock:
        mock_spec = load_mock_spec(args.mock_hq_file, default_mock_spec(args))
        client: BaseHQClient = MockHQClient(mock_spec)
    else:
        client = RealHQClient(args.hq_base)
    terminal_alerts_enabled = bool(args.mock)
    webhook_preflight = build_webhook_preflight()
    previous_preflight_status = str(state.get("last_preflight_status") or "").strip().upper() or None

    started_at = now_utc()
    result: dict[str, Any] = {
        "run_id": run_id,
        "project": args.project,
        "action": args.action,
        "started_at": started_at,
        "finished_at": started_at,
        "status": TERMINAL_RUNNING,
        "error_class": None,
        "remote_run_id": None,
        "run_artifact_dir_resolution": "none",
        "run_to_done_resolution_method": "none",
        "pointer_console_run_id_seen": None,
        "pointer_run_dir_seen": None,
        "fs_scan_checked_count": 0,
        "pointer_http_code": None,
        "doctor": {},
        "trigger": {},
        "poll": {},
        "links": {},
        "preflight": {},
        "alert": base_alert_payload(webhook_preflight),
        "validators": [],
        "warnings": [],
        "bundle_dir": rel_path(bundle_dir, repo_root),
    }
    if not webhook_preflight.get("ok"):
        result["warnings"].append(
            {
                "warning": "DISCORD_WEBHOOK_UNAVAILABLE",
                "error_class": webhook_preflight.get("error_class"),
            }
        )

    def finalize(exit_code: int) -> int:
        result["finished_at"] = now_utc()
        try:
            atomic_write_json(bundle_dir / "RESULT.json", result)
            atomic_write_text(bundle_dir / "SUMMARY.md", build_summary(result))
            state["last_result"] = {
                "run_id": run_id,
                "status": result.get("status"),
                "error_class": result.get("error_class"),
                "remote_run_id": result.get("remote_run_id"),
                "finished_at": result.get("finished_at"),
            }
            if isinstance(result.get("preflight"), dict):
                pf_status = str(result["preflight"].get("status") or "").strip().upper()
                if pf_status:
                    state["last_preflight_status"] = pf_status
            atomic_write_json(state_path, state)
            return exit_code
        except OSError as exc:
            result["warnings"].append(f"FINALIZE_WRITE_FAILED:{type(exc).__name__}")
            result["finalize_error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
            return 1

    def maybe_send_terminal_alert(
        *,
        terminal_status: str,
        remote_run_id: str,
        error_class: str,
        proof_path: str,
        novnc_url: str | None,
    ) -> dict[str, Any]:
        if not terminal_alerts_enabled:
            return base_alert_payload(webhook_preflight)
        return send_terminal_alert(
            project=args.project,
            action=args.action,
            terminal_status=terminal_status,
            remote_run_id=remote_run_id,
            error_class=error_class,
            proof_path=proof_path,
            novnc_url=novnc_url,
            state=state,
            webhook_preflight=webhook_preflight,
        )

    try:
        preflight_enabled = args.project == "soma_kajabi" and args.action == "soma_run_to_done"
        if preflight_enabled:
            preflight_payload = run_soma_preflight(
                artifacts_root=artifacts_root,
                hq_base=args.hq_base,
                run_id=f"{run_id}_preflight",
                mock=args.mock,
            )
            atomic_write_json(bundle_dir / "soma_preflight.json", preflight_payload)
            preflight_status = str(preflight_payload.get("status") or "NO_GO").strip().upper()
            preflight_reasons = [
                str(item).strip()
                for item in (preflight_payload.get("reasons") or [])
                if str(item).strip()
            ]
            preflight_result_path = str(
                preflight_payload.get("result_path")
                or rel_path(bundle_dir / "soma_preflight.json", repo_root)
            )
            result["preflight"] = {
                "status": preflight_status,
                "reasons": preflight_reasons,
                "result_path": preflight_result_path,
                "active_run_id": preflight_payload.get("active_run_id"),
                "active_status": preflight_payload.get("active_status"),
                "novnc_url": preflight_payload.get("novnc_url"),
                "gate_expiry": preflight_payload.get("gate_expiry"),
                "checks": preflight_payload.get("checks"),
            }

            transition_alert = send_preflight_transition_alert(
                previous_status=previous_preflight_status,
                current_status=preflight_status,
                reasons=preflight_reasons,
                proof_path=preflight_result_path,
                novnc_url=(
                    str(preflight_payload.get("novnc_url")).strip()
                    if isinstance(preflight_payload.get("novnc_url"), str)
                    else None
                ),
                gate_expiry=(
                    str(preflight_payload.get("gate_expiry")).strip()
                    if isinstance(preflight_payload.get("gate_expiry"), str)
                    else None
                ),
                state=state,
                webhook_preflight=webhook_preflight,
            )
            if bool(transition_alert.get("needed")):
                result["alert"] = transition_alert

            if preflight_status == "HUMAN_ONLY":
                result["status"] = TERMINAL_WAITING
                result["error_class"] = "WAITING_FOR_HUMAN"
                result["remote_run_id"] = preflight_payload.get("active_run_id")
                result["novnc_url"] = preflight_payload.get("novnc_url")
                return finalize(0)

            if preflight_status == "NO_GO":
                result["status"] = "NO_GO"
                result["error_class"] = sanitize_error_class(
                    preflight_reasons[0] if preflight_reasons else "SOMA_PREFLIGHT_NO_GO",
                    default="SOMA_PREFLIGHT_NO_GO",
                )
                result["remote_run_id"] = preflight_payload.get("active_run_id")
                return finalize(0)
        else:
            result["preflight"] = {
                "status": "SKIP",
                "reasons": ["NON_SOMA_LANE"],
                "result_path": None,
                "active_run_id": None,
                "active_status": "idle",
                "novnc_url": None,
                "gate_expiry": None,
                "checks": {},
            }

        doctor_payload = run_doctor_core(
            bundle_dir=bundle_dir,
            hq_base=args.hq_base,
            mock=args.mock,
            mock_status=args.mock_doctor_status,
        )
        doctor_status = "PASS" if str(doctor_payload.get("status")) == "PASS" else "FAIL"
        result["doctor"] = {
            "status": doctor_status,
            "run_id": doctor_payload.get("run_id"),
            "bundle_dir": doctor_payload.get("bundle_dir"),
            "failed_checks": doctor_payload.get("failed_checks") or [],
            "error_class": doctor_payload.get("error_class"),
            "rc": doctor_payload.get("rc"),
            "stdout_len": doctor_payload.get("stdout_len"),
            "stderr_tail": doctor_payload.get("stderr_tail") or [],
            "cmd": doctor_payload.get("cmd"),
        }
        if doctor_payload.get("parse_error"):
            result["doctor"]["parse_error"] = doctor_payload.get("parse_error")
        if doctor_status != "PASS":
            result["status"] = TERMINAL_FAIL
            result["error_class"] = "DOCTOR_FAILED"
            proof_for_alert = rel_path(bundle_dir / "RESULT.json", repo_root)
            result["alert"] = maybe_send_terminal_alert(
                terminal_status=TERMINAL_FAIL,
                remote_run_id=run_id,
                error_class="DOCTOR_FAILED",
                proof_path=proof_for_alert,
                novnc_url=None,
            )
            return finalize(0)

        trigger = client.trigger(args.project, args.action)
        result["trigger"] = {
            "state": trigger.state,
            "status_code": trigger.status_code,
            "message": trigger.message,
            "run_id": trigger.run_id,
            "body": trigger.body,
        }
        atomic_write_json(raw_dir / "trigger.json", result["trigger"])

        if trigger.state == "FAILED":
            error_class = sanitize_error_class(trigger.body.get("error_class") if isinstance(trigger.body, dict) else None, default="TRIGGER_FAILED")
            result["status"] = TERMINAL_FAIL
            result["error_class"] = error_class
            proof_for_alert = rel_path(bundle_dir / "RESULT.json", repo_root)
            result["alert"] = maybe_send_terminal_alert(
                terminal_status=TERMINAL_FAIL,
                remote_run_id=run_id,
                error_class=error_class,
                proof_path=proof_for_alert,
                novnc_url=None,
            )
            return finalize(0)

        remote_run_id = (trigger.run_id or "").strip()
        if trigger.state == "ALREADY_RUNNING" and not remote_run_id:
            result["status"] = TERMINAL_FAIL
            result["error_class"] = "ALREADY_RUNNING_NO_ACTIVE_RUN_ID"
            proof_for_alert = rel_path(bundle_dir / "RESULT.json", repo_root)
            result["alert"] = maybe_send_terminal_alert(
                terminal_status=TERMINAL_FAIL,
                remote_run_id=run_id,
                error_class="ALREADY_RUNNING_NO_ACTIVE_RUN_ID",
                proof_path=proof_for_alert,
                novnc_url=None,
            )
            return finalize(0)
        if not remote_run_id:
            result["status"] = TERMINAL_FAIL
            result["error_class"] = "RUN_ID_MISSING"
            proof_for_alert = rel_path(bundle_dir / "RESULT.json", repo_root)
            result["alert"] = maybe_send_terminal_alert(
                terminal_status=TERMINAL_FAIL,
                remote_run_id=run_id,
                error_class="RUN_ID_MISSING",
                proof_path=proof_for_alert,
                novnc_url=None,
            )
            return finalize(0)

        result["remote_run_id"] = remote_run_id

        poll_result = poll_to_terminal(
            client=client,
            remote_run_id=remote_run_id,
            max_seconds=args.max_seconds,
            poll_min=poll_min,
            poll_max=poll_max,
            raw_dir=raw_dir,
            artifacts_root=artifacts_root,
        )
        result["poll"] = {
            "terminal_status": poll_result.terminal_status,
            "run_status": poll_result.run_status,
            "poll_count": poll_result.poll_count,
            "elapsed_sec": poll_result.elapsed_sec,
            "run_artifact_dir": poll_result.run_artifact_dir,
            "run_artifact_dir_resolution": poll_result.run_artifact_dir_resolution,
            "run_to_done_resolution_method": poll_result.run_to_done_resolution_method,
            "browse_error": poll_result.browse_error,
            "pointer_console_run_id_seen": poll_result.pointer_console_run_id_seen,
            "pointer_run_dir_seen": poll_result.pointer_run_dir_seen,
            "fs_scan_checked_count": poll_result.fs_scan_checked_count,
            "pointer_http_code": poll_result.pointer_http_code,
        }
        result["run_artifact_dir_resolution"] = poll_result.run_artifact_dir_resolution
        result["run_to_done_resolution_method"] = poll_result.run_to_done_resolution_method
        result["pointer_console_run_id_seen"] = poll_result.pointer_console_run_id_seen
        result["pointer_run_dir_seen"] = poll_result.pointer_run_dir_seen
        result["fs_scan_checked_count"] = poll_result.fs_scan_checked_count
        result["pointer_http_code"] = poll_result.pointer_http_code
        result["links"] = {
            "run_to_done_dir": poll_result.run_to_done_dir,
            "proof_path": poll_result.proof_path,
            "precheck_path": poll_result.precheck_path,
        }
        if poll_result.proof_payload is not None:
            atomic_write_json(bundle_dir / "run_to_done_PROOF.json", poll_result.proof_payload)
        if poll_result.precheck_payload is not None:
            atomic_write_json(bundle_dir / "run_to_done_PRECHECK.json", poll_result.precheck_payload)

        if poll_result.terminal_status == TERMINAL_WAITING:
            novnc_url = poll_result.novnc_url or default_pinned_novnc_url()
            result["status"] = TERMINAL_WAITING
            result["error_class"] = TERMINAL_WAITING
            result["novnc_url"] = novnc_url
            proof_for_alert = poll_result.proof_path or rel_path(bundle_dir / "RESULT.json", repo_root)
            result["alert"] = maybe_send_terminal_alert(
                terminal_status=TERMINAL_WAITING,
                remote_run_id=remote_run_id,
                error_class=TERMINAL_WAITING,
                proof_path=proof_for_alert,
                novnc_url=novnc_url,
            )
            return finalize(0)

        if poll_result.terminal_status != TERMINAL_SUCCESS:
            error_class = derive_error_class(
                run_obj=poll_result.run_obj,
                proof_payload=poll_result.proof_payload,
                precheck_payload=poll_result.precheck_payload,
                default="TERMINAL_FAIL",
            )
            result["status"] = TERMINAL_FAIL
            result["error_class"] = error_class
            proof_for_alert = poll_result.proof_path or rel_path(bundle_dir / "RESULT.json", repo_root)
            result["alert"] = maybe_send_terminal_alert(
                terminal_status=TERMINAL_FAIL,
                remote_run_id=remote_run_id,
                error_class=error_class,
                proof_path=proof_for_alert,
                novnc_url=None,
            )
            return finalize(1 if args.exit_nonzero_on_terminal_fail else 0)

        # SUCCESS path: optional deterministic validators if configured in registry.
        action_ids = load_action_ids(repo_root)
        validator_actions = [
            item.strip()
            for item in str(args.validator_actions or "").split(",")
            if item.strip()
        ]
        validators: list[dict[str, Any]] = []
        for validator_action in validator_actions:
            if validator_action not in action_ids:
                validators.append(
                    {
                        "action": validator_action,
                        "status": "SKIP_MISSING",
                        "terminal_status": None,
                        "run_id": None,
                        "error_class": None,
                    }
                )
                continue
            trig = client.trigger(args.project, validator_action)
            raw_name = f"validator_{validator_action.replace('.', '_')}_trigger.json"
            atomic_write_json(
                raw_dir / raw_name,
                {
                    "state": trig.state,
                    "status_code": trig.status_code,
                    "message": trig.message,
                    "run_id": trig.run_id,
                    "body": trig.body,
                },
            )
            if trig.state == "FAILED" or not (trig.run_id or "").strip():
                error_class = sanitize_error_class(
                    trig.body.get("error_class") if isinstance(trig.body, dict) else None,
                    default=f"VALIDATOR_TRIGGER_FAIL_{validator_action}",
                )
                validators.append(
                    {
                        "action": validator_action,
                        "status": "FAIL",
                        "terminal_status": TERMINAL_FAIL,
                        "run_id": trig.run_id,
                        "error_class": error_class,
                    }
                )
                result["warnings"].append(
                    {
                        "warning": "VALIDATOR_TRIGGER_FAILED",
                        "validator": validator_action,
                        "error_class": error_class,
                    }
                )
                continue
            val_run_id = str(trig.run_id)
            val_result = poll_generic_action(
                client=client,
                run_id=val_run_id,
                max_seconds=min(args.max_seconds, 900),
                poll_min=poll_min,
                poll_max=poll_max,
                raw_dir=raw_dir,
                raw_prefix=f"validator_{validator_action.replace('.', '_')}",
            )
            val_terminal = str(val_result.get("terminal_status") or TERMINAL_FAIL)
            val_error = derive_error_class(
                run_obj=val_result.get("run_obj") if isinstance(val_result.get("run_obj"), dict) else {},
                proof_payload=None,
                precheck_payload=None,
                default=f"VALIDATOR_FAIL_{validator_action}",
            ) if val_terminal == TERMINAL_FAIL else None
            validators.append(
                {
                    "action": validator_action,
                    "status": "PASS" if val_terminal == TERMINAL_SUCCESS else "FAIL",
                    "terminal_status": val_terminal,
                    "run_id": val_run_id,
                    "error_class": val_error,
                    "poll_count": val_result.get("poll_count"),
                    "elapsed_sec": val_result.get("elapsed_sec"),
                }
            )
            if val_terminal != TERMINAL_SUCCESS:
                result["warnings"].append(
                    {
                        "warning": "VALIDATOR_FAILED",
                        "validator": validator_action,
                        "error_class": val_error,
                    }
                )

        result["validators"] = validators
        result["status"] = TERMINAL_SUCCESS
        result["error_class"] = None
        result["alert"] = {
            "status": "SKIPPED",
            "needed": False,
            "sent": False,
            "deduped": False,
            "hash": "",
            "terminal_error_class": "",
            "error_class": "",
            "message": "",
            "notify": webhook_preflight if not webhook_preflight.get("ok") else {},
        }
        return finalize(0)
    except Exception as exc:  # noqa: BLE001
        result["status"] = TERMINAL_FAIL
        result["error_class"] = sanitize_error_class(type(exc).__name__, default="AUTOPILOT_EXCEPTION")
        result["warnings"].append(f"exception:{type(exc).__name__}:{str(exc)[:240]}")
        proof_for_alert = rel_path(bundle_dir / "RESULT.json", repo_root)
        result["alert"] = maybe_send_terminal_alert(
            terminal_status=TERMINAL_FAIL,
            remote_run_id=str(result.get("remote_run_id") or run_id),
            error_class=str(result["error_class"]),
            proof_path=proof_for_alert,
            novnc_url=None,
        )
        return finalize(0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
