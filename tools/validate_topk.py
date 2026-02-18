"""Validate a topk.json file against the canonical schema.

Enforces:
  - JSON Schema v draft-07 structural validation
  - Typed param value/type consistency (int, double, bool, string)
  - BACKTEST_ONLY === true (fail-closed)
  - Case-sensitive param names (no duplicates after lowering)

Exit codes:
  0 = valid
  1 = validation error (prints single JSON error object to stdout)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "topk.schema.json"

_TYPE_CHECKERS: dict[str, type | tuple[type, ...]] = {
    "int": int,
    "double": (int, float),
    "bool": bool,
    "string": str,
}


class TopkValidationError:
    """Structured validation failure."""

    def __init__(self, error_class: str, message: str, path: str = "") -> None:
        self.error_class = error_class
        self.message = message
        self.path = path

    def to_dict(self) -> dict[str, str]:
        d: dict[str, str] = {
            "error_class": self.error_class,
            "message": self.message,
        }
        if self.path:
            d["path"] = self.path
        return d


def _load_schema() -> dict[str, Any]:
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def _check_typed_params(params: dict[str, Any]) -> TopkValidationError | None:
    """Verify each param's value matches its declared type."""
    for name, spec in params.items():
        declared = spec.get("type")
        value = spec.get("value")
        if declared not in _TYPE_CHECKERS:
            return TopkValidationError(
                "INVALID_PARAM_TYPE",
                f"params.{name}.type '{declared}' is not one of: int, double, bool, string",
                path=f"params.{name}.type",
            )
        expected = _TYPE_CHECKERS[declared]
        if declared == "int" and isinstance(value, bool):
            return TopkValidationError(
                "PARAM_TYPE_MISMATCH",
                f"params.{name}: expected int but got bool",
                path=f"params.{name}.value",
            )
        if declared == "double" and isinstance(value, bool):
            return TopkValidationError(
                "PARAM_TYPE_MISMATCH",
                f"params.{name}: expected double but got bool",
                path=f"params.{name}.value",
            )
        if not isinstance(value, expected):
            return TopkValidationError(
                "PARAM_TYPE_MISMATCH",
                f"params.{name}: expected {declared} but got {type(value).__name__}",
                path=f"params.{name}.value",
            )
    return None


def _check_case_sensitive_params(params: dict[str, Any]) -> TopkValidationError | None:
    """Detect param names that collide when lowercased (case-sensitivity violation)."""
    seen: dict[str, str] = {}
    for name in params:
        lower = name.lower()
        if lower in seen:
            return TopkValidationError(
                "PARAM_CASE_COLLISION",
                f"params '{name}' collides with '{seen[lower]}' (case-insensitive duplicate)",
                path=f"params.{name}",
            )
        seen[lower] = name
    return None


def validate_topk(data: Any) -> TopkValidationError | None:
    """Validate a parsed topk.json object. Returns None on success, error on failure."""
    schema = _load_schema()

    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        path = ".".join(str(p) for p in first.absolute_path) or "(root)"

        if "BACKTEST_ONLY" in path or (
            hasattr(first, "validator") and first.validator == "const"
        ):
            return TopkValidationError(
                "BACKTEST_ONLY_REQUIRED",
                "BACKTEST_ONLY must be true",
                path=path,
            )

        if first.validator == "required":
            missing = first.message
            return TopkValidationError(
                "MISSING_REQUIRED_FIELD",
                missing,
                path=path,
            )

        return TopkValidationError(
            "SCHEMA_VALIDATION_ERROR",
            first.message,
            path=path,
        )

    if not isinstance(data, dict):
        return TopkValidationError("SCHEMA_VALIDATION_ERROR", "topk must be an object")

    if data.get("BACKTEST_ONLY") is not True:
        return TopkValidationError(
            "BACKTEST_ONLY_REQUIRED",
            "BACKTEST_ONLY must be exactly true (fail-closed)",
            path="BACKTEST_ONLY",
        )

    params = data.get("params", {})
    err = _check_typed_params(params)
    if err:
        return err
    err = _check_case_sensitive_params(params)
    if err:
        return err

    return None


def validate_topk_file(path: str | Path) -> TopkValidationError | None:
    """Load and validate a topk.json file from disk."""
    p = Path(path)
    if not p.exists():
        return TopkValidationError("FILE_NOT_FOUND", f"topk.json not found: {p}")
    try:
        with open(p) as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        return TopkValidationError("INVALID_JSON", f"JSON parse error: {exc}")
    return validate_topk(data)


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <topk.json>", file=sys.stderr)
        sys.exit(2)
    err = validate_topk_file(sys.argv[1])
    if err:
        print(json.dumps(err.to_dict(), indent=2))
        sys.exit(1)
    print('{"status":"ok"}')


if __name__ == "__main__":
    main()
