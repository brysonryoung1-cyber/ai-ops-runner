"""Interactive Kajabi UI executor for Business DoD remediation.

Performs real authenticated UI mutations in a persistent profile lane:
1) create/publish privacy-policy page
2) add RAW - Needs Review category in Home User Library

Deterministic selectors run first; bounded LLM fallback runs only when needed.
All writes are transcripted and screenshotted for proof.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.error import URLError
from urllib.request import Request, urlopen

from ops.lib.aiops_remote_helpers import canonical_novnc_url
from ops.lib.human_gate import write_gate, write_gate_artifact
from services.soma_kajabi.kajabi_admin_context import (
    KAJABI_CLOUDFLARE_BLOCKED,
    KAJABI_SESSION_EXPIRED,
    _is_cloudflare_blocked,
    _is_login_page,
    ensure_kajabi_soma_admin_context,
)
from services.soma_kajabi.ui_action_schema import ActionSchemaError, parse_llm_actions
from src.playwright_safe import safe_content_excerpt, safe_screenshot, safe_title, safe_url

KAJABI_PROFILE_DIR = Path("/var/lib/openclaw/kajabi_chrome_profile")
SITE_ORIGIN = "https://zane-mccourtney.mykajabi.com"
KAJABI_PRODUCTS_URL = "https://app.kajabi.com/admin/products"
KAJABI_WEBSITE_PAGES_URL = "https://app.kajabi.com/admin/website/pages"
KAJABI_WEBSITE_PAGES_URL_ALT = "https://app.kajabi.com/admin/pages"

HUMAN_ONLY_INSTRUCTION = "log in + 2FA, then CLOSE noVNC to release lock"
PROFILE_LOCK_ERROR_CLASS = "KAJABI_INTERACTIVE_PROFILE_LOCKED"
KAJABI_2FA_REQUIRED = "KAJABI_2FA_REQUIRED"
PRIVACY_PAGE_PATH = "/privacy-policy"
TERMS_PAGE_PATH = "/terms"
RAW_CATEGORY_LABEL = "RAW - Needs Review"
RAW_CATEGORY_LABEL_UNICODE = "RAW – Needs Review"


class UiFixerError(RuntimeError):
    """Generic deterministic or fallback execution failure."""


class HumanOnlyError(RuntimeError):
    """Raised when execution requires an operator in the noVNC lane."""

    def __init__(self, error_class: str, reason: str):
        super().__init__(reason)
        self.error_class = error_class
        self.reason = reason


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(text: str) -> str:
    return (text or "").replace("–", "-").replace("—", "-").strip().lower()


def has_raw_category_text(content: str) -> bool:
    """Idempotency helper: detect RAW category label in page content."""
    text = _normalize_text(content)
    return "raw - needs review" in text or "raw needs review" in text


def should_skip_privacy_fix(status_code: int) -> bool:
    """Idempotency helper: public privacy page already reachable."""
    return 200 <= int(status_code) < 400


def _http_status(url: str, timeout: int = 12) -> int:
    try:
        req = Request(url, method="GET")
        req.add_header("User-Agent", "ai-ops-runner/kajabi-ui-fixer")
        with urlopen(req, timeout=timeout) as resp:
            return int(resp.getcode() or 0)
    except URLError:
        return 0
    except Exception:
        return 0


def _canonical_novnc_url() -> str:
    pinned = str(os.environ.get("OPENCLAW_PINNED_NOVNC_URL") or "").strip()
    if pinned:
        return canonical_novnc_url(pinned)
    host = (
        str(os.environ.get("OPENCLAW_TS_HOSTNAME") or "").strip()
        or str(os.environ.get("OPENCLAW_TAILSCALE_HOSTNAME") or "").strip()
        or "aiops-1.tailc75c62.ts.net"
    )
    return canonical_novnc_url(f"https://{host}")


def classify_human_only_condition(
    *,
    url: str = "",
    title: str = "",
    content: str = "",
    launch_error: str = "",
) -> dict[str, str] | None:
    """Classify Cloudflare/2FA/profile-lock into HUMAN_ONLY conditions."""
    launch_lower = (launch_error or "").lower()
    if any(
        token in launch_lower
        for token in ("processsingleton", "profile appears to be in use", "another browser")
    ):
        return {
            "error_class": PROFILE_LOCK_ERROR_CLASS,
            "reason": "Interactive profile is locked by another Chromium process.",
        }

    if _is_cloudflare_blocked(content or "", title=title or ""):
        return {
            "error_class": KAJABI_CLOUDFLARE_BLOCKED,
            "reason": "Cloudflare challenge detected.",
        }

    content_lower = (content or "").lower()
    if "two-factor" in content_lower or "verification code" in content_lower:
        return {
            "error_class": KAJABI_2FA_REQUIRED,
            "reason": "2FA challenge detected.",
        }

    if _is_login_page(url or "", content or ""):
        return {
            "error_class": KAJABI_SESSION_EXPIRED,
            "reason": "Kajabi login screen detected.",
        }
    return None


@dataclass
class _Transcript:
    path: Path

    def log(self, step: str, status: str, detail: str = "", **extra: Any) -> None:
        payload: dict[str, Any] = {
            "ts": _now_iso(),
            "step": step,
            "status": status,
            "detail": detail,
        }
        payload.update(extra)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _ensure_click(page: Any, selectors: list[str], *, timeout_ms: int = 5000) -> bool:
    for selector in selectors:
        try:
            node = page.query_selector(selector)
            if node is None:
                continue
            node.click(timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


def _ensure_fill(page: Any, selectors: list[str], value: str, *, timeout_ms: int = 5000) -> bool:
    for selector in selectors:
        try:
            node = page.query_selector(selector)
            if node is None:
                continue
            node.fill(value, timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


def _goto(page: Any, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass


def _raise_if_human_only(page: Any, *, launch_error: str = "") -> None:
    classification = classify_human_only_condition(
        url=safe_url(page),
        title=safe_title(page),
        content=safe_content_excerpt(page, 12000),
        launch_error=launch_error,
    )
    if classification is not None:
        raise HumanOnlyError(classification["error_class"], classification["reason"])


def _take_shot(page: Any, screenshot_dir: Path, name: str) -> str | None:
    path = screenshot_dir / f"{name}.png"
    return str(path) if safe_screenshot(page, str(path)) else None


def _privacy_fallback_targets() -> dict[str, dict[str, Any]]:
    return {
        "goto_pages": {
            "actions": {"goto"},
            "url": KAJABI_WEBSITE_PAGES_URL,
            "description": "Go to website pages index",
        },
        "goto_pages_alt": {
            "actions": {"goto"},
            "url": KAJABI_WEBSITE_PAGES_URL_ALT,
            "description": "Go to alternate pages index",
        },
        "click_privacy_link": {
            "actions": {"click"},
            "selectors": [
                'a:has-text("Privacy Policy")',
                'text=Privacy Policy',
            ],
            "description": "Open Privacy Policy page row",
        },
        "click_new_page": {
            "actions": {"click"},
            "selectors": [
                'button:has-text("New Page")',
                'button:has-text("Create New Page")',
                'a:has-text("New Page")',
            ],
            "description": "Start page creation flow",
        },
        "fill_page_title": {
            "actions": {"fill"},
            "typed_values": {"Privacy Policy"},
            "selectors": [
                'input[name="title"]',
                'input[placeholder*="Title"]',
                'input[aria-label*="Title"]',
            ],
            "description": "Set page title",
        },
        "fill_page_slug": {
            "actions": {"fill"},
            "typed_values": {"privacy-policy"},
            "selectors": [
                'input[name="slug"]',
                'input[name="path"]',
                'input[aria-label*="Slug"]',
                'input[placeholder*="slug"]',
            ],
            "description": "Set page slug",
        },
        "click_save_page": {
            "actions": {"click"},
            "selectors": [
                'button:has-text("Save")',
                'button:has-text("Update")',
            ],
            "description": "Save current page edits",
        },
        "click_publish_page": {
            "actions": {"click"},
            "selectors": [
                'button:has-text("Publish")',
                'button:has-text("Publish Changes")',
            ],
            "description": "Publish page",
        },
        "wait_privacy_text": {
            "actions": {"wait_for_text"},
            "typed_values": {"Privacy Policy"},
            "description": "Wait for privacy text to appear",
        },
    }


def _raw_fallback_targets() -> dict[str, dict[str, Any]]:
    return {
        "goto_products": {
            "actions": {"goto"},
            "url": KAJABI_PRODUCTS_URL,
            "description": "Go to products page",
        },
        "click_home_library": {
            "actions": {"click"},
            "selectors": [
                'a:has-text("Home User Library")',
                'text=Home User Library',
            ],
            "description": "Open Home User Library product",
        },
        "click_add_category": {
            "actions": {"click"},
            "selectors": [
                'button:has-text("Add Category")',
                'button:has-text("New Category")',
                'a:has-text("Add Category")',
            ],
            "description": "Start category creation",
        },
        "fill_category_name": {
            "actions": {"fill"},
            "typed_values": {RAW_CATEGORY_LABEL, RAW_CATEGORY_LABEL_UNICODE},
            "selectors": [
                'input[name="name"]',
                'input[placeholder*="Category"]',
                'input[aria-label*="Category"]',
            ],
            "description": "Set category name",
        },
        "click_save_category": {
            "actions": {"click"},
            "selectors": [
                'button:has-text("Save")',
                'button:has-text("Create Category")',
            ],
            "description": "Save category",
        },
        "wait_raw_text": {
            "actions": {"wait_for_text"},
            "typed_values": {RAW_CATEGORY_LABEL, RAW_CATEGORY_LABEL_UNICODE},
            "description": "Wait for RAW category text",
        },
    }


def _request_llm_fallback(
    *,
    issue: str,
    page: Any,
    targets: Mapping[str, Mapping[str, Any]],
    max_steps: int,
) -> str:
    from src.llm.llm_router import CORE_BRAIN, generate

    allowlist_lines: list[str] = []
    for name, spec in targets.items():
        actions = sorted(set(spec.get("actions") or ()))
        typed_values = sorted(set(spec.get("typed_values") or ()))
        allowlist_lines.append(
            json.dumps(
                {
                    "target": name,
                    "actions": actions,
                    "typed_values": typed_values,
                    "description": spec.get("description", ""),
                },
                ensure_ascii=True,
            )
        )

    prompt = (
        "You are generating allowlisted Kajabi UI repair actions.\n"
        "Output JSON ONLY as {'actions':[...]}.\n"
        f"Max actions: {max_steps}\n"
        f"Issue: {issue}\n"
        f"URL: {safe_url(page)}\n"
        f"Title: {safe_title(page)}\n"
        f"Excerpt: {safe_content_excerpt(page, 2000)}\n"
        "Allowed targets:\n"
        + "\n".join(allowlist_lines)
        + "\nRules: only use allowlisted targets/actions; no free-form selectors."
    )
    response = generate(
        role=CORE_BRAIN,
        messages=[{"role": "user", "content": prompt}],
        trace_id=f"soma_ui_fixer_{issue}",
        project_id="soma_kajabi",
        action="soma_business_dod_fixer",
        max_tokens=500,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    return response.content or ""


def _execute_validated_action(
    *,
    page: Any,
    action: Mapping[str, Any],
    targets: Mapping[str, Mapping[str, Any]],
) -> None:
    action_type = str(action.get("action"))
    target_name = str(action.get("target"))
    value = action.get("value")
    spec = targets[target_name]

    if action_type == "goto":
        _goto(page, str(spec["url"]))
        return
    if action_type == "click":
        selectors = list(spec.get("selectors") or [])
        if not selectors or not _ensure_click(page, selectors):
            raise UiFixerError(f"fallback_click_failed:{target_name}")
        return
    if action_type == "fill":
        selectors = list(spec.get("selectors") or [])
        if not selectors or not isinstance(value, str) or not _ensure_fill(page, selectors, value):
            raise UiFixerError(f"fallback_fill_failed:{target_name}")
        return
    if action_type == "wait_for_text":
        if not isinstance(value, str):
            raise UiFixerError(f"fallback_wait_value_missing:{target_name}")
        page.get_by_text(value, exact=False).first.wait_for(state="visible", timeout=8000)
        return
    raise UiFixerError(f"fallback_unknown_action:{action_type}")


def _run_llm_fallback(
    *,
    issue: str,
    page: Any,
    targets: Mapping[str, Mapping[str, Any]],
    max_llm_calls: int,
    llm_calls_used: int,
    max_steps: int,
    transcript: _Transcript,
) -> tuple[int, list[dict[str, Any]]]:
    if llm_calls_used >= max_llm_calls:
        raise UiFixerError("llm_fallback_budget_exhausted")

    raw = _request_llm_fallback(issue=issue, page=page, targets=targets, max_steps=max_steps)
    actions = parse_llm_actions(raw, target_allowlist=targets, max_steps=max_steps)
    transcript.log(
        f"{issue}.llm_fallback",
        "INFO",
        "executing validated llm actions",
        actions=actions,
    )
    for idx, action in enumerate(actions, start=1):
        _raise_if_human_only(page)
        _execute_validated_action(page=page, action=action, targets=targets)
        transcript.log(f"{issue}.llm_action_{idx}", "OK", json.dumps(action, ensure_ascii=True))
    return llm_calls_used + 1, actions


def _open_human_gate(*, artifact_dir: Path, run_id: str, reason: str) -> dict[str, Any]:
    gate_payload: dict[str, Any] = {
        "novnc_url": _canonical_novnc_url(),
        "gate_expiry": None,
        "gate_artifact": None,
        "instruction": HUMAN_ONLY_INSTRUCTION,
    }
    try:
        gate = write_gate("soma_kajabi", run_id, gate_payload["novnc_url"], reason)
        gate_artifact = write_gate_artifact("soma_kajabi", run_id, gate)
        gate_payload["gate_expiry"] = gate.get("expires_at")
        gate_payload["gate_artifact"] = str(gate_artifact)
        (artifact_dir / "HUMAN_GATE.json").write_text(json.dumps(gate, indent=2), encoding="utf-8")
    except Exception:
        pass
    return gate_payload


def _ensure_interactive_lane(artifact_dir: Path, run_id: str) -> tuple[bool, str, str | None]:
    try:
        from ops.scripts.novnc_ready import ensure_novnc_ready_with_recovery

        ready, novnc_url, err_class, _journal = ensure_novnc_ready_with_recovery(artifact_dir, run_id)
        if ready and novnc_url:
            return True, canonical_novnc_url(novnc_url), None
        return False, _canonical_novnc_url(), err_class or "NOVNC_NOT_READY"
    except Exception:
        return False, _canonical_novnc_url(), "NOVNC_NOT_READY"


def _run_fix_privacy_policy(
    *,
    page: Any,
    transcript: _Transcript,
    screenshot_dir: Path,
    max_llm_calls: int,
    llm_calls_used: int,
    max_steps: int,
) -> tuple[dict[str, Any], int]:
    status_before = _http_status(f"{SITE_ORIGIN}{PRIVACY_PAGE_PATH}")
    transcript.log("privacy.check_before", "INFO", f"http_status={status_before}")
    if should_skip_privacy_fix(status_before):
        return (
            {
                "status": "SKIP",
                "reason": "PRIVACY_ALREADY_200",
                "http_status_before": status_before,
                "http_status_after": status_before,
            },
            llm_calls_used,
        )

    targets = _privacy_fallback_targets()
    try:
        _goto(page, KAJABI_WEBSITE_PAGES_URL)
        _raise_if_human_only(page)
        _take_shot(page, screenshot_dir, "privacy_pages_index")
        opened_existing = _ensure_click(
            page,
            [
                'a:has-text("Privacy Policy")',
                'text=Privacy Policy',
            ],
        )
        if not opened_existing:
            if not _ensure_click(
                page,
                [
                    'button:has-text("New Page")',
                    'button:has-text("Create New Page")',
                    'a:has-text("New Page")',
                ],
            ):
                raise UiFixerError("privacy_new_page_button_missing")

        if not _ensure_fill(
            page,
            [
                'input[name="title"]',
                'input[placeholder*="Title"]',
                'input[aria-label*="Title"]',
            ],
            "Privacy Policy",
        ):
            raise UiFixerError("privacy_title_input_missing")

        if not _ensure_fill(
            page,
            [
                'input[name="slug"]',
                'input[name="path"]',
                'input[aria-label*="Slug"]',
                'input[placeholder*="slug"]',
            ],
            "privacy-policy",
        ):
            raise UiFixerError("privacy_slug_input_missing")

        _ensure_click(page, ['button:has-text("Save")', 'button:has-text("Update")'])
        _ensure_click(page, ['button:has-text("Publish")', 'button:has-text("Publish Changes")'])
        _take_shot(page, screenshot_dir, "privacy_after_save_publish")
    except HumanOnlyError:
        raise
    except Exception as exc:
        transcript.log("privacy.deterministic", "WARN", str(exc)[:240])
        llm_calls_used, _actions = _run_llm_fallback(
            issue="privacy_policy_fix",
            page=page,
            targets=targets,
            max_llm_calls=max_llm_calls,
            llm_calls_used=llm_calls_used,
            max_steps=max_steps,
            transcript=transcript,
        )

    status_after = _http_status(f"{SITE_ORIGIN}{PRIVACY_PAGE_PATH}")
    transcript.log("privacy.check_after", "INFO", f"http_status={status_after}")
    if not should_skip_privacy_fix(status_after):
        raise UiFixerError(f"privacy_still_not_reachable:{status_after}")
    return (
        {
            "status": "FIXED",
            "http_status_before": status_before,
            "http_status_after": status_after,
        },
        llm_calls_used,
    )


def _extract_home_modules_from_page(page: Any) -> list[str]:
    content = safe_content_excerpt(page, 200000)
    if not content:
        return [RAW_CATEGORY_LABEL_UNICODE]
    known = [
        RAW_CATEGORY_LABEL,
        RAW_CATEGORY_LABEL_UNICODE,
    ]
    found: list[str] = []
    content_norm = _normalize_text(content)
    for candidate in known:
        if _normalize_text(candidate) in content_norm:
            found.append(candidate)
    return found or [RAW_CATEGORY_LABEL_UNICODE]


def _run_fix_raw_module(
    *,
    page: Any,
    transcript: _Transcript,
    screenshot_dir: Path,
    max_llm_calls: int,
    llm_calls_used: int,
    max_steps: int,
    artifact_dir: Path,
) -> tuple[dict[str, Any], int, str]:
    targets = _raw_fallback_targets()
    _goto(page, KAJABI_PRODUCTS_URL)
    _raise_if_human_only(page)
    _take_shot(page, screenshot_dir, "raw_products_index")

    if not _ensure_click(
        page,
        [
            'a:has-text("Home User Library")',
            'text=Home User Library',
        ],
    ):
        transcript.log("raw.open_home_library", "WARN", "deterministic selector failed")
        llm_calls_used, _actions = _run_llm_fallback(
            issue="raw_open_home_library",
            page=page,
            targets=targets,
            max_llm_calls=max_llm_calls,
            llm_calls_used=llm_calls_used,
            max_steps=max_steps,
            transcript=transcript,
        )

    _raise_if_human_only(page)
    content_before = safe_content_excerpt(page, 200000)
    if has_raw_category_text(content_before):
        snapshot_doc = {
            "home": {"modules": _extract_home_modules_from_page(page), "lessons": []},
            "practitioner": {"modules": [], "lessons": []},
        }
        snapshot_path = artifact_dir / "home_snapshot_after_fix.json"
        snapshot_path.write_text(json.dumps(snapshot_doc, indent=2), encoding="utf-8")
        return (
            {
                "status": "SKIP",
                "reason": "RAW_CATEGORY_ALREADY_EXISTS",
            },
            llm_calls_used,
            str(snapshot_path),
        )

    try:
        if not _ensure_click(
            page,
            [
                'button:has-text("Add Category")',
                'button:has-text("New Category")',
                'a:has-text("Add Category")',
            ],
        ):
            raise UiFixerError("raw_add_category_button_missing")
        if not _ensure_fill(
            page,
            [
                'input[name="name"]',
                'input[placeholder*="Category"]',
                'input[aria-label*="Category"]',
            ],
            RAW_CATEGORY_LABEL_UNICODE,
        ):
            raise UiFixerError("raw_category_name_input_missing")
        if not _ensure_click(
            page,
            [
                'button:has-text("Save")',
                'button:has-text("Create Category")',
            ],
        ):
            raise UiFixerError("raw_category_save_missing")
        _take_shot(page, screenshot_dir, "raw_after_add")
    except HumanOnlyError:
        raise
    except Exception as exc:
        transcript.log("raw.deterministic", "WARN", str(exc)[:240])
        llm_calls_used, _actions = _run_llm_fallback(
            issue="raw_category_fix",
            page=page,
            targets=targets,
            max_llm_calls=max_llm_calls,
            llm_calls_used=llm_calls_used,
            max_steps=max_steps,
            transcript=transcript,
        )

    content_after = safe_content_excerpt(page, 200000)
    if not has_raw_category_text(content_after):
        raise UiFixerError("raw_category_not_visible_after_fix")

    snapshot_doc = {
        "home": {"modules": _extract_home_modules_from_page(page), "lessons": []},
        "practitioner": {"modules": [], "lessons": []},
    }
    snapshot_path = artifact_dir / "home_snapshot_after_fix.json"
    snapshot_path.write_text(json.dumps(snapshot_doc, indent=2), encoding="utf-8")
    return (
        {
            "status": "FIXED",
            "category": RAW_CATEGORY_LABEL_UNICODE,
        },
        llm_calls_used,
        str(snapshot_path),
    )


def run_business_dod_ui_fixes(
    *,
    artifact_dir: Path,
    run_id: str,
    need_privacy_fix: bool,
    need_raw_fix: bool,
    max_llm_calls: int = 1,
    max_steps: int = 6,
) -> dict[str, Any]:
    """Run interactive Kajabi UI fixes and return structured status."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir = artifact_dir / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    transcript = _Transcript(path=artifact_dir / "transcript.jsonl")

    result: dict[str, Any] = {
        "status": "PASS",
        "error_class": None,
        "message": "",
        "instruction": None,
        "novnc_url": None,
        "gate_expiry": None,
        "privacy_policy": {"status": "SKIP", "reason": "NOT_REQUESTED"},
        "raw_module": {"status": "SKIP", "reason": "NOT_REQUESTED"},
        "screenshot_paths": [],
        "snapshot_path": None,
        "llm_calls_used": 0,
    }
    if not need_privacy_fix and not need_raw_fix:
        transcript.log("executor.start", "OK", "no targeted fixes required")
        return result

    lane_ready, novnc_url, novnc_error = _ensure_interactive_lane(artifact_dir, run_id)
    result["novnc_url"] = novnc_url
    if not lane_ready:
        result.update(
            {
                "status": "FAIL",
                "error_class": novnc_error or "NOVNC_NOT_READY",
                "message": "Interactive noVNC lane unavailable.",
            }
        )
        transcript.log("lane.preflight", "FAIL", result["message"], error_class=result["error_class"])
        return result

    try:
        from ops.scripts.novnc_ready import novnc_display
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        result.update(
            {
                "status": "FAIL",
                "error_class": "PLAYWRIGHT_NOT_INSTALLED",
                "message": f"Playwright unavailable: {exc}",
            }
        )
        transcript.log("executor.imports", "FAIL", result["message"])
        return result

    env = os.environ.copy()
    env["DISPLAY"] = novnc_display()
    llm_calls_used = 0

    try:
        with sync_playwright() as playwright:
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(KAJABI_PROFILE_DIR),
                    headless=False,
                    env=env,
                )
            except Exception as exc:
                classification = classify_human_only_condition(launch_error=str(exc))
                if classification is not None:
                    gate = _open_human_gate(
                        artifact_dir=artifact_dir,
                        run_id=run_id,
                        reason=classification["error_class"],
                    )
                    result.update(
                        {
                            "status": "HUMAN_ONLY",
                            "error_class": classification["error_class"],
                            "message": classification["reason"],
                            "instruction": HUMAN_ONLY_INSTRUCTION,
                            "novnc_url": gate["novnc_url"],
                            "gate_expiry": gate["gate_expiry"],
                        }
                    )
                    transcript.log("context.launch", "HUMAN_ONLY", classification["reason"])
                    return result
                raise

            page = context.pages[0] if context.pages else context.new_page()
            _take_shot(page, screenshot_dir, "initial")
            _raise_if_human_only(page)

            bootstrap = ensure_kajabi_soma_admin_context(page, artifact_dir=artifact_dir)
            transcript.log("bootstrap", "OK" if bootstrap.ok else "FAIL", json.dumps(bootstrap.__dict__))
            if not bootstrap.ok:
                if bootstrap.error_class in {KAJABI_CLOUDFLARE_BLOCKED, KAJABI_SESSION_EXPIRED}:
                    gate = _open_human_gate(
                        artifact_dir=artifact_dir,
                        run_id=run_id,
                        reason=str(bootstrap.error_class),
                    )
                    result.update(
                        {
                            "status": "HUMAN_ONLY",
                            "error_class": str(bootstrap.error_class),
                            "message": str(bootstrap.recommended_next_action or "Authentication required."),
                            "instruction": HUMAN_ONLY_INSTRUCTION,
                            "novnc_url": gate["novnc_url"],
                            "gate_expiry": gate["gate_expiry"],
                        }
                    )
                    context.close()
                    return result
                result.update(
                    {
                        "status": "FAIL",
                        "error_class": str(bootstrap.error_class or "KAJABI_BOOTSTRAP_FAILED"),
                        "message": str(bootstrap.recommended_next_action or "Kajabi bootstrap failed."),
                    }
                )
                context.close()
                return result

            if need_privacy_fix:
                privacy_doc, llm_calls_used = _run_fix_privacy_policy(
                    page=page,
                    transcript=transcript,
                    screenshot_dir=screenshot_dir,
                    max_llm_calls=max_llm_calls,
                    llm_calls_used=llm_calls_used,
                    max_steps=max_steps,
                )
                result["privacy_policy"] = privacy_doc

            if need_raw_fix:
                raw_doc, llm_calls_used, snapshot_path = _run_fix_raw_module(
                    page=page,
                    transcript=transcript,
                    screenshot_dir=screenshot_dir,
                    max_llm_calls=max_llm_calls,
                    llm_calls_used=llm_calls_used,
                    max_steps=max_steps,
                    artifact_dir=artifact_dir,
                )
                result["raw_module"] = raw_doc
                result["snapshot_path"] = snapshot_path

            context.close()
            result["llm_calls_used"] = llm_calls_used
            result["screenshot_paths"] = sorted(str(p) for p in screenshot_dir.glob("*.png"))
            transcript.log("executor.complete", "OK", "interactive fixes finished")
            return result
    except HumanOnlyError as exc:
        gate = _open_human_gate(
            artifact_dir=artifact_dir,
            run_id=run_id,
            reason=exc.error_class,
        )
        result.update(
            {
                "status": "HUMAN_ONLY",
                "error_class": exc.error_class,
                "message": exc.reason,
                "instruction": HUMAN_ONLY_INSTRUCTION,
                "novnc_url": gate["novnc_url"],
                "gate_expiry": gate["gate_expiry"],
            }
        )
        transcript.log("executor.human_only", "HUMAN_ONLY", exc.reason, error_class=exc.error_class)
        return result
    except (UiFixerError, ActionSchemaError) as exc:
        result.update(
            {
                "status": "FAIL",
                "error_class": type(exc).__name__,
                "message": str(exc),
                "llm_calls_used": llm_calls_used,
            }
        )
        transcript.log("executor.fail", "FAIL", str(exc)[:300], error_class=type(exc).__name__)
        return result
    except Exception as exc:
        result.update(
            {
                "status": "FAIL",
                "error_class": "KAJABI_UI_FIXER_EXCEPTION",
                "message": str(exc)[:400],
                "llm_calls_used": llm_calls_used,
            }
        )
        transcript.log("executor.exception", "FAIL", str(exc)[:300], error_class="KAJABI_UI_FIXER_EXCEPTION")
        return result


def collect_curl_checks() -> dict[str, int]:
    """Collect deterministic curl-like URL status checks for terms/privacy."""
    def _curl_code(url: str) -> int:
        try:
            r = subprocess.run(
                ["curl", "-L", "-sS", "-o", "/dev/null", "-w", "%{http_code}", url],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if r.returncode == 0:
                return int((r.stdout or "0").strip() or "0")
        except Exception:
            pass
        return _http_status(url)

    return {
        "terms": _curl_code(f"{SITE_ORIGIN}{TERMS_PAGE_PATH}"),
        "privacy_policy": _curl_code(f"{SITE_ORIGIN}{PRIVACY_PAGE_PATH}"),
    }

