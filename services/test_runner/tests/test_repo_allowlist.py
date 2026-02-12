"""Tests for repo allowlist parsing and enforcement."""

from __future__ import annotations

import os

import pytest
import yaml

os.environ.setdefault("REPO_ALLOWLIST_PATH", "/dev/null")

from test_runner.repo_allowlist import (
    AllowedRepo,
    load_repo_allowlist,
    validate_repo_url,
)


@pytest.fixture
def repo_allowlist_file(tmp_path):
    data = {
        "repos": {
            "algo-nt8-orb": {
                "url": "git@github.com:brysonryoung1-cyber/algo-nt8-orb.git",
                "allowed_branches": ["main", "master"],
                "default_branch": "main",
            }
        }
    }
    f = tmp_path / "repo_allowlist.yaml"
    f.write_text(yaml.dump(data))
    return str(f)


def test_load_repo_allowlist(repo_allowlist_file):
    repos = load_repo_allowlist(repo_allowlist_file)
    assert "algo-nt8-orb" in repos
    assert len(repos) == 1


def test_allowed_repo_fields(repo_allowlist_file):
    repos = load_repo_allowlist(repo_allowlist_file)
    orb = repos["algo-nt8-orb"]
    assert isinstance(orb, AllowedRepo)
    assert orb.name == "algo-nt8-orb"
    assert orb.url == "git@github.com:brysonryoung1-cyber/algo-nt8-orb.git"
    assert orb.allowed_branches == ("main", "master")
    assert orb.default_branch == "main"


def test_validate_allowed_repo_exact_url(repo_allowlist_file):
    repo = validate_repo_url(
        "git@github.com:brysonryoung1-cyber/algo-nt8-orb.git",
        repo_allowlist_file,
    )
    assert repo.name == "algo-nt8-orb"


def test_validate_allowed_repo_without_git_suffix(repo_allowlist_file):
    repo = validate_repo_url(
        "git@github.com:brysonryoung1-cyber/algo-nt8-orb",
        repo_allowlist_file,
    )
    assert repo.name == "algo-nt8-orb"


def test_validate_allowed_repo_trailing_slash(repo_allowlist_file):
    repo = validate_repo_url(
        "git@github.com:brysonryoung1-cyber/algo-nt8-orb.git/",
        repo_allowlist_file,
    )
    assert repo.name == "algo-nt8-orb"


def test_validate_case_insensitive(repo_allowlist_file):
    repo = validate_repo_url(
        "git@github.com:BrysonRYoung1-Cyber/Algo-NT8-ORB.git",
        repo_allowlist_file,
    )
    assert repo.name == "algo-nt8-orb"


def test_reject_unknown_repo(repo_allowlist_file):
    with pytest.raises(ValueError, match="not in repo allowlist"):
        validate_repo_url(
            "https://github.com/evil-user/evil-repo.git",
            repo_allowlist_file,
        )


def test_reject_similar_but_different_user(repo_allowlist_file):
    with pytest.raises(ValueError, match="not in repo allowlist"):
        validate_repo_url(
            "https://github.com/other-user/algo-nt8-orb.git",
            repo_allowlist_file,
        )


def test_reject_similar_but_different_repo(repo_allowlist_file):
    with pytest.raises(ValueError, match="not in repo allowlist"):
        validate_repo_url(
            "https://github.com/brysonryoung1-cyber/algo-nt8-orb-EVIL.git",
            repo_allowlist_file,
        )


def test_reject_empty_url(repo_allowlist_file):
    with pytest.raises(ValueError, match="not in repo allowlist"):
        validate_repo_url("", repo_allowlist_file)


def test_repo_frozen(repo_allowlist_file):
    """AllowedRepo is frozen — no mutation."""
    repos = load_repo_allowlist(repo_allowlist_file)
    orb = repos["algo-nt8-orb"]
    with pytest.raises(AttributeError):
        orb.url = "https://evil.com/repo.git"  # type: ignore[misc]
