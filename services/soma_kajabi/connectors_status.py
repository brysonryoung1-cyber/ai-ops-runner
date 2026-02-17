#!/usr/bin/env python3
"""Print connector status JSON for doctor/HQ. No secrets in output."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure repo root in path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.soma_kajabi.connector_config import connectors_status, _repo_root


def main() -> int:
    root = _repo_root()
    status = connectors_status(root)
    print(json.dumps(status, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
