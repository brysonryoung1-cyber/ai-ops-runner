"""Daily report stub: writes SUMMARY.md; blocked when kill_switch or CONFIG_INVALID."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import get_kill_switch, load_pred_markets_config, repo_root


def main() -> int:
    root = repo_root()
    cfg, config_error = load_pred_markets_config(root)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    base_dir = Path((cfg or {}).get("artifacts", {}).get("base_dir", "artifacts/pred_markets"))
    out_dir = root / base_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    if config_error:
        (out_dir / "SUMMARY.md").write_text(
            f"# pred_markets Daily — Blocked\n\n- **Error class**: CONFIG_INVALID\n- **Message**: {config_error}\n"
        )
        print(json.dumps({"ok": False, "run_id": run_id, "error_class": "CONFIG_INVALID"}))
        return 1

    if get_kill_switch(root):
        (out_dir / "SUMMARY.md").write_text(
            "# pred_markets Daily — Blocked\n\n- **Error class**: KILL_SWITCH_ENABLED\n"
        )
        print(json.dumps({"ok": False, "run_id": run_id, "error_class": "KILL_SWITCH_ENABLED"}))
        return 1

    (out_dir / "SUMMARY.md").write_text(
        f"# pred_markets Daily Report\n\n- **Run ID**: {run_id}\n- **Phase 0**: read-only mirror; no trading.\n"
    )
    print(json.dumps({"ok": True, "run_id": run_id}))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
