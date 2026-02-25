"""State machine for Soma Auto-Finish. Persists stage.json and SUMMARY.md per stage.

Delegates to ops.soma.auto_finish_state_machine for canonical state.
"""

from __future__ import annotations

from pathlib import Path

# Re-export from canonical module
from ops.soma.auto_finish_state_machine import (
    AUTH_NEEDED_ERROR_CLASSES,
    STAGES,
    append_summary_line,
    is_auth_needed_error,
    write_stage,
)


