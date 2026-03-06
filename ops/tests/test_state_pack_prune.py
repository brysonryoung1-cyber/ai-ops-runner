import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops.lib.state_pack_contract import COMPLETION_MARKER_NAME, SCHEMA_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
PRUNE_SCRIPT = REPO_ROOT / "ops" / "scripts" / "state_pack_prune.sh"


def _seed_pack(path: Path, age_hours: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "RESULT.json").write_text(json.dumps({"status": "PASS"}) + "\n", encoding="utf-8")
    (path / COMPLETION_MARKER_NAME).write_text('{"ok":true}\n', encoding="utf-8")
    target_time = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).timestamp()
    os.utime(path, (target_time, target_time))


def _write_latest(state_pack_dir: Path, run_dir: Path) -> None:
    (state_pack_dir / "LATEST.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "reason": "state_pack_generated",
                "run_id": run_dir.name,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "latest_path": str(run_dir),
                "result_path": str(run_dir / "RESULT.json"),
                "schema_version": SCHEMA_VERSION,
                "sha": "abc1234",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_state_pack_prune_applies_retention_and_preserves_latest(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    state_pack_dir = artifacts_root / "system" / "state_pack"
    latest_dir = state_pack_dir / "pack_old_latest"
    newest_dir = state_pack_dir / "pack_newest"
    extra_dir = state_pack_dir / "pack_extra"
    expired_dir = state_pack_dir / "pack_expired"

    _seed_pack(latest_dir, age_hours=72)
    _seed_pack(newest_dir, age_hours=1)
    _seed_pack(extra_dir, age_hours=2)
    _seed_pack(expired_dir, age_hours=60)
    _write_latest(state_pack_dir, latest_dir)

    proc = subprocess.run(
        [
            str(PRUNE_SCRIPT),
            "--root",
            str(artifacts_root),
            "--keep-count",
            "1",
            "--keep-hours",
            "48",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads((state_pack_dir / "PRUNE_LAST.json").read_text(encoding="utf-8"))
    assert report["mode"] == "normal"
    assert report["latest_preserved"] is True
    assert latest_dir.exists()
    assert newest_dir.exists()
    assert not extra_dir.exists()
    assert not expired_dir.exists()
    assert report["deleted_count"] == 2


def test_state_pack_prune_disk_guard_uses_aggressive_keep_count(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    state_pack_dir = artifacts_root / "system" / "state_pack"
    dirs = []
    for idx in range(5):
        pack_dir = state_pack_dir / f"pack_{idx}"
        _seed_pack(pack_dir, age_hours=idx)
        dirs.append(pack_dir)
    _write_latest(state_pack_dir, dirs[-1])

    env = os.environ.copy()
    env["STATE_PACK_FAKE_USED_PCT"] = "97"
    env["STATE_PACK_KEEP_COUNT_MIN"] = "2"
    proc = subprocess.run(
        [
            str(PRUNE_SCRIPT),
            "--root",
            str(artifacts_root),
            "--keep-count",
            "5",
            "--disk-threshold-pct",
            "85",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads((state_pack_dir / "PRUNE_LAST.json").read_text(encoding="utf-8"))
    remaining = sorted(path.name for path in state_pack_dir.iterdir() if path.is_dir())

    assert report["mode"] == "disk_guard"
    assert report["reason"] == "DISK_GUARD_ACTIVE"
    assert report["used_pct"] == 97
    assert report["latest_preserved"] is True
    assert report["deleted_count"] == 2
    assert dirs[-1].name in remaining
    assert len(remaining) == 3
