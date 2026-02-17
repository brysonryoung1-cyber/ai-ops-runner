"""Single entrypoint for hostd: pred_markets.mirror.run, .mirror.backfill, .report.health, .report.daily."""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2:
        print('{"ok":false,"error":"Usage: run.py <mirror_run|mirror_backfill|report_health|report_daily>"}')
        return 1
    sub = sys.argv[1].strip().lower()
    if sub == "mirror_run":
        from .mirror import run_mirror
        return run_mirror(mode="mirror_run")
    if sub == "mirror_backfill":
        from .mirror import run_mirror
        return run_mirror(mode="mirror_backfill")
    if sub == "report_health":
        from .report_health import main as report_main
        return report_main()
    if sub == "report_daily":
        from .report_daily import main as daily_main
        return daily_main()
    print(f'{{"ok":false,"error":"Unknown subcommand: {sub}"}}')
    return 1


if __name__ == "__main__":
    sys.exit(main())
