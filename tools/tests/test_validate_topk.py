"""Tests for tools.validate_topk — schema + typed param validation."""
from __future__ import annotations

import copy
import json
import pytest
from pathlib import Path
from tools.validate_topk import validate_topk, validate_topk_file, TopkValidationError

VALID_TOPK: dict = {
    "candidate_id": "orb-nq-v103-001",
    "strategy_name": "ORBStrategy",
    "strategy_version": "1.0.3",
    "instrument": "NQ 03-26",
    "timeframe": "5 Min",
    "date_ranges": [
        {"start": "2025-01-02", "end": "2025-06-30"},
        {"start": "2025-07-01", "end": "2025-12-31"},
    ],
    "sessions": "CME US Index Futures RTH",
    "params": {
        "OrbMinutes": {"type": "int", "value": 30},
        "ProfitTarget": {"type": "double", "value": 50.0},
        "UseTrailing": {"type": "bool", "value": True},
        "SessionLabel": {"type": "string", "value": "RTH"},
    },
    "fees_slippage": {
        "commission_per_side": 2.05,
        "slippage_ticks": 2,
    },
    "BACKTEST_ONLY": True,
}


def _make(**overrides) -> dict:
    d = copy.deepcopy(VALID_TOPK)
    for k, v in overrides.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    return d


class TestValidCases:
    def test_valid_topk(self):
        assert validate_topk(VALID_TOPK) is None

    def test_valid_minimal_single_range(self):
        d = _make(
            date_ranges=[{"start": "2025-01-01", "end": "2025-03-31"}],
            params={"Qty": {"type": "int", "value": 1}},
        )
        assert validate_topk(d) is None


class TestMissingBacktestOnly:
    def test_missing_backtest_only(self):
        d = _make(BACKTEST_ONLY=None)
        err = validate_topk(d)
        assert err is not None
        assert err.error_class in ("MISSING_REQUIRED_FIELD", "BACKTEST_ONLY_REQUIRED")

    def test_backtest_only_false(self):
        d = copy.deepcopy(VALID_TOPK)
        d["BACKTEST_ONLY"] = False
        err = validate_topk(d)
        assert err is not None
        assert err.error_class == "BACKTEST_ONLY_REQUIRED"


class TestWrongParamCasing:
    def test_case_collision(self):
        d = copy.deepcopy(VALID_TOPK)
        d["params"]["orbMinutes"] = {"type": "int", "value": 15}
        d["params"]["OrbMinutes"] = {"type": "int", "value": 30}
        err = validate_topk(d)
        assert err is not None
        assert err.error_class == "PARAM_CASE_COLLISION"


class TestWrongParamType:
    def test_int_gets_string(self):
        d = copy.deepcopy(VALID_TOPK)
        d["params"]["OrbMinutes"]["value"] = "thirty"
        err = validate_topk(d)
        assert err is not None
        assert err.error_class == "PARAM_TYPE_MISMATCH"
        assert "OrbMinutes" in err.message

    def test_bool_in_int_slot(self):
        d = copy.deepcopy(VALID_TOPK)
        d["params"]["OrbMinutes"]["value"] = True
        err = validate_topk(d)
        assert err is not None
        assert err.error_class == "PARAM_TYPE_MISMATCH"

    def test_double_gets_string(self):
        d = copy.deepcopy(VALID_TOPK)
        d["params"]["ProfitTarget"]["value"] = "high"
        err = validate_topk(d)
        assert err is not None
        assert err.error_class == "PARAM_TYPE_MISMATCH"

    def test_int_accepted_for_double(self):
        """int values are acceptable for double params."""
        d = copy.deepcopy(VALID_TOPK)
        d["params"]["ProfitTarget"]["value"] = 50
        assert validate_topk(d) is None


class TestMissingRequiredFields:
    @pytest.mark.parametrize("field", [
        "candidate_id", "strategy_name", "strategy_version",
        "instrument", "timeframe", "date_ranges", "sessions",
        "params", "fees_slippage",
    ])
    def test_missing_field(self, field):
        d = _make(**{field: None})
        err = validate_topk(d)
        assert err is not None
        assert err.error_class in ("MISSING_REQUIRED_FIELD", "SCHEMA_VALIDATION_ERROR")


class TestFileValidation:
    def test_missing_file(self, tmp_path):
        err = validate_topk_file(tmp_path / "nonexistent.json")
        assert err is not None
        assert err.error_class == "FILE_NOT_FOUND"

    def test_invalid_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{bad json")
        err = validate_topk_file(p)
        assert err is not None
        assert err.error_class == "INVALID_JSON"

    def test_valid_file(self, tmp_path):
        p = tmp_path / "topk.json"
        p.write_text(json.dumps(VALID_TOPK))
        assert validate_topk_file(p) is None


class TestErrorSerialization:
    def test_to_dict(self):
        e = TopkValidationError("TEST_CLASS", "msg", "path.a")
        d = e.to_dict()
        assert d == {"error_class": "TEST_CLASS", "message": "msg", "path": "path.a"}

    def test_to_dict_no_path(self):
        e = TopkValidationError("TEST_CLASS", "msg")
        d = e.to_dict()
        assert "path" not in d
