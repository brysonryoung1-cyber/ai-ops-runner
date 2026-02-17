#!/usr/bin/env python3
"""Generate apps/openclaw-console/src/lib/action_registry.generated.ts from config/action_registry.json."""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(os.environ.get("OPENCLAW_REPO_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
REGISTRY_JSON = REPO_ROOT / "config" / "action_registry.json"
OUT_TS = REPO_ROOT / "apps" / "openclaw-console" / "src" / "lib" / "action_registry.generated.ts"


def main() -> int:
    if not REGISTRY_JSON.exists():
        print(f"Missing {REGISTRY_JSON}")
        return 1
    with open(REGISTRY_JSON) as f:
        data = json.load(f)
    actions = data.get("actions") or []
    action_to_hostd: dict[str, str] = {}
    project_actions: dict[str, list[str]] = {}
    for a in actions:
        aid = a.get("id")
        if not aid:
            continue
        action_to_hostd[aid] = aid
        for alias in a.get("aliases") or []:
            action_to_hostd[alias] = aid
        pid = a.get("project_id")
        if pid:
            project_actions.setdefault(pid, []).append(aid)

    lines = [
        "// Auto-generated from config/action_registry.json — do not edit.",
        "// Run: python3 ops/export_action_registry_ts.py",
        "",
        "/** Map UI/API action name -> hostd action name. Single source: config/action_registry.json */",
        "export const ACTION_TO_HOSTD: Record<string, string> = {",
    ]
    for key in sorted(action_to_hostd.keys()):
        lines.append(f'  "{key}": "{action_to_hostd[key]}",')
    lines.append("};")
    lines.append("")
    lines.append("/** Project ID -> set of allowlisted action ids for POST /api/projects/[projectId]/run */")
    lines.append("export const PROJECT_ACTIONS: Record<string, ReadonlySet<string>> = {")
    for pid in sorted(project_actions.keys()):
        ids = sorted(project_actions[pid])
        lines.append(f'  {json.dumps(pid)}: new Set({json.dumps(ids)}),')
    lines.append("};")
    lines.append("")

    OUT_TS.parent.mkdir(parents=True, exist_ok=True)
    OUT_TS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_TS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
