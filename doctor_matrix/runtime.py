"""Runtime helpers for doctor matrix checks."""

from __future__ import annotations

import json
import os
import re
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ops.lib.exec_trigger import hq_request

from .models import CheckResult, RunDirContract

BODY_SAMPLE_LIMIT = 4096


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"doctor_matrix_{ts}_{os.getpid()}"


def _safe_json_loads(raw: str) -> Any:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _truncate_text(raw: str, *, limit: int = BODY_SAMPLE_LIMIT) -> str:
    txt = raw or ""
    if len(txt) <= limit:
        return txt
    return txt[:limit] + "\n...[truncated]"


_SENSITIVE_KEY_RE = re.compile(r"(token|secret|password|authorization|cookie|key)", re.IGNORECASE)
_SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s\",]+)"),
    re.compile(r"(?i)(x-openclaw-token\s*[:=]\s*)([^\s\",]+)"),
    re.compile(r"(?i)(cookie\s*[:=]\s*)([^\s\",]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b"),
]


def sanitize_text(raw: str) -> str:
    text = raw or ""
    for pat in _SENSITIVE_PATTERNS:
        if pat.groups >= 2:
            text = pat.sub(r"\1[REDACTED]", text)
        else:
            text = pat.sub("[REDACTED]", text)
    return text


def sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, val in value.items():
            key_str = str(key)
            if _SENSITIVE_KEY_RE.search(key_str):
                out[key_str] = "[REDACTED]"
            else:
                out[key_str] = sanitize_value(val)
        return out
    if isinstance(value, list):
        return [sanitize_value(v) for v in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


@dataclass
class HttpResponse:
    http_code: int
    body_text: str
    payload: Any
    base_label: str
    path: str
    source: str


@dataclass(frozen=True)
class CheckSpec:
    id: str
    scope: str
    project: str | None
    handler: Callable[["CheckBuilder", "MatrixRuntime"], CheckResult]


class CheckBuilder:
    """Per-check helper for deterministic evidence writing and result assembly."""

    def __init__(self, runtime: "MatrixRuntime", *, check_id: str, scope: str, project: str | None):
        self.runtime = runtime
        self.check_id = check_id
        self.scope = scope
        self.project = project
        self.started_at = now_utc_iso()
        self._started_monotonic = time.monotonic()
        self.evidence_dir = runtime.evidence_dir_for(check_id, scope=scope, project=project)
        self.evidence_paths: list[str] = []

    def _track(self, path: Path) -> str:
        rel = path.relative_to(self.runtime.bundle_dir).as_posix()
        self.evidence_paths.append(rel)
        return rel

    def write_json(self, name: str, payload: Any) -> str:
        path = self.evidence_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sanitize_value(payload), indent=2) + "\n", encoding="utf-8")
        return self._track(path)

    def write_text(self, name: str, text: str) -> str:
        path = self.evidence_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sanitize_text(text), encoding="utf-8")
        return self._track(path)

    def request(self, *, label: str, base_label: str, path: str, timeout: int = 10) -> HttpResponse:
        response = self.runtime.http_get(base_label=base_label, path=path, timeout=timeout)
        sample = _truncate_text(sanitize_text(response.body_text), limit=self.runtime.body_sample_limit)
        payload = {
            "source": response.source,
            "base_label": base_label,
            "base_url": self.runtime.base_urls.get(base_label),
            "path": path,
            "http_code": response.http_code,
            "body_sample": sample,
            "parsed_json": sanitize_value(response.payload),
        }
        self.write_json(f"{label}.json", payload)
        return response

    def finalize(
        self,
        *,
        status: str,
        message: str,
        error_class: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> CheckResult:
        finished_at = now_utc_iso()
        duration_ms = int((time.monotonic() - self._started_monotonic) * 1000)
        safe_details = sanitize_value(details or {})
        return CheckResult(
            id=self.check_id,
            scope=self.scope,  # type: ignore[arg-type]
            project=self.project,
            status=status,  # type: ignore[arg-type]
            error_class=error_class,
            message=message,
            started_at=self.started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            evidence_paths=list(self.evidence_paths),
            details=safe_details,
        )


class MatrixRuntime:
    """Execution runtime for the doctor matrix."""

    def __init__(
        self,
        *,
        repo_root: Path,
        artifacts_root: Path,
        bundle_dir: Path,
        frontdoor_base: str,
        localhost_base: str,
        run_id: str,
        mock: bool,
        mock_fixture: dict[str, Any] | None,
        mode: str,
        project_filter: set[str] | None,
    ):
        self.repo_root = repo_root
        self.artifacts_root = artifacts_root
        self.bundle_dir = bundle_dir
        self.run_id = run_id
        self.mock = bool(mock)
        self.mock_fixture = mock_fixture or {}
        self.mode = mode
        self.project_filter = set(project_filter or [])
        self.body_sample_limit = BODY_SAMPLE_LIMIT
        self.base_urls = {
            "frontdoor": frontdoor_base.rstrip("/"),
            "localhost": localhost_base.rstrip("/"),
        }
        self.bundle_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir_contracts: list[RunDirContract] = []

    def evidence_dir_for(self, check_id: str, *, scope: str, project: str | None) -> Path:
        safe_check = check_id.replace("/", "_")
        if scope == "core":
            path = self.bundle_dir / "evidence" / "core" / safe_check
        else:
            project_name = project or "unknown_project"
            path = self.bundle_dir / "evidence" / "projects" / project_name / safe_check
        path.mkdir(parents=True, exist_ok=True)
        return path

    def start_check(self, *, check_id: str, scope: str, project: str | None) -> CheckBuilder:
        return CheckBuilder(self, check_id=check_id, scope=scope, project=project)

    def write_bundle_json(self, name: str, payload: Any) -> Path:
        path = self.bundle_dir / name
        path.write_text(json.dumps(sanitize_value(payload), indent=2) + "\n", encoding="utf-8")
        return path

    def write_bundle_text(self, name: str, text: str) -> Path:
        path = self.bundle_dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def _mock_lookup(self, base_label: str, path: str) -> HttpResponse:
        base_map = self.mock_fixture.get(base_label)
        if not isinstance(base_map, dict):
            body = {"ok": False, "error": f"mock_base_missing:{base_label}"}
            return HttpResponse(
                http_code=404,
                body_text=json.dumps(body),
                payload=body,
                base_label=base_label,
                path=path,
                source="mock",
            )

        item = base_map.get(path)
        if item is None and path.endswith("?path="):
            item = base_map.get(path[:-1])
        if item is None:
            body = {"ok": False, "error": f"mock_path_missing:{path}"}
            return HttpResponse(
                http_code=404,
                body_text=json.dumps(body),
                payload=body,
                base_label=base_label,
                path=path,
                source="mock",
            )

        if not isinstance(item, dict):
            body = {"ok": False, "error": f"mock_invalid_shape:{path}"}
            return HttpResponse(
                http_code=500,
                body_text=json.dumps(body),
                payload=body,
                base_label=base_label,
                path=path,
                source="mock",
            )

        http_code = int(item.get("http_code", 200))
        if "body_text" in item and isinstance(item.get("body_text"), str):
            body_text = item["body_text"]
            payload = _safe_json_loads(body_text)
        else:
            body = item.get("body")
            if isinstance(body, str):
                body_text = body
                payload = _safe_json_loads(body_text)
            else:
                body_text = json.dumps(body if body is not None else {}, separators=(",", ":"))
                payload = body

        return HttpResponse(
            http_code=http_code,
            body_text=body_text,
            payload=payload,
            base_label=base_label,
            path=path,
            source="mock",
        )

    def http_get(self, *, base_label: str, path: str, timeout: int = 10) -> HttpResponse:
        if self.mock:
            return self._mock_lookup(base_label, path)

        base_url = self.base_urls.get(base_label)
        if not base_url:
            body = {"ok": False, "error": f"unknown_base_label:{base_label}"}
            return HttpResponse(
                http_code=0,
                body_text=json.dumps(body),
                payload=body,
                base_label=base_label,
                path=path,
                source="live",
            )

        code, body_text = hq_request(
            "GET",
            path,
            timeout=timeout,
            base_url=base_url,
        )
        payload = _safe_json_loads(body_text)
        return HttpResponse(
            http_code=int(code),
            body_text=body_text or "",
            payload=payload,
            base_label=base_label,
            path=path,
            source="live",
        )

    def execute(self, spec: CheckSpec) -> CheckResult:
        builder = self.start_check(check_id=spec.id, scope=spec.scope, project=spec.project)
        try:
            result = spec.handler(builder, self)
            return result
        except Exception as exc:  # noqa: BLE001
            trace = traceback.format_exc()
            builder.write_text("exception.txt", trace)
            return builder.finalize(
                status="FAIL",
                message=f"Unhandled exception in check: {exc}",
                error_class="CHECK_EXCEPTION",
                details={"exception_type": type(exc).__name__},
            )


def resolve_repo_root(default_file: Path) -> Path:
    """Resolve repo root from env or from file location."""

    env_root = os.environ.get("OPENCLAW_REPO_ROOT", "").strip()
    if env_root:
        p = Path(env_root)
        if p.exists():
            return p
    return default_file.resolve().parents[2]


def resolve_artifacts_root(repo_root: Path) -> Path:
    """Resolve canonical artifacts root (env -> /opt -> repo fallback)."""

    env_root = os.environ.get("OPENCLAW_ARTIFACTS_ROOT", "").strip()
    if env_root:
        return Path(env_root)

    vps_root = Path("/opt/ai-ops-runner/artifacts")
    if vps_root.exists():
        return vps_root

    return repo_root / "artifacts"


def load_mock_fixture(path: Path) -> dict[str, Any]:
    data = _safe_json_loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid mock fixture json: {path}")
    return data
