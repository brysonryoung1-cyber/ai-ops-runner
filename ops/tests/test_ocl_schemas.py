"""Unit tests for OCL v1 schemas (ocl_task, ocl_result)."""
import json
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OCL_TASK = REPO_ROOT / "ops" / "schemas" / "ocl_task.schema.json"
OCL_RESULT = REPO_ROOT / "ops" / "schemas" / "ocl_result.schema.json"


def test_ocl_task_schema_exists():
    assert OCL_TASK.exists(), "ocl_task.schema.json must exist"


def test_ocl_result_schema_exists():
    assert OCL_RESULT.exists(), "ocl_result.schema.json must exist"


def test_ocl_task_schema_valid_json():
    with open(OCL_TASK) as f:
        data = json.load(f)
    assert data.get("type") == "object"
    assert "action" in data.get("required", [])
    assert "properties" in data


def test_ocl_result_schema_valid_json():
    with open(OCL_RESULT) as f:
        data = json.load(f)
    assert data.get("type") == "object"
    required = data.get("required", [])
    assert "status" in required
    assert "checks" in required
    assert "evidence" in required
    assert data.get("properties", {}).get("status", {}).get("enum") == ["ok", "fail", "partial"]


def test_ocl_task_minimal_valid():
    with open(OCL_TASK) as f:
        schema = json.load(f)
    task = {"action": "doctor", "read_only": True}
    # Simple validation: required keys present
    for r in schema.get("required", []):
        assert r in task, f"Missing required: {r}"


def test_ocl_result_minimal_valid():
    with open(OCL_RESULT) as f:
        schema = json.load(f)
    result = {
        "status": "ok",
        "checks": [{"name": "health", "pass": True}],
        "evidence": [{"path": "artifacts/system/state_pack/x/health_public.json"}],
    }
    for r in schema.get("required", []):
        assert r in result, f"Missing required: {r}"
