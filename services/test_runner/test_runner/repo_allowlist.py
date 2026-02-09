"""Repo allowlist loader – only pre-approved repos may be targeted."""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

_REPO_ALLOWLIST_PATH = os.environ.get(
    "REPO_ALLOWLIST_PATH", "/app/configs/repo_allowlist.yaml"
)


@dataclass(frozen=True)
class AllowedRepo:
    name: str
    url: str
    allowed_branches: tuple[str, ...]
    default_branch: str


def load_repo_allowlist(path: str | None = None) -> dict[str, AllowedRepo]:
    """Load the repo allowlist from YAML.

    Returns a mapping of repo_name -> AllowedRepo.
    """
    path = path or _REPO_ALLOWLIST_PATH
    with open(path, "rb") as f:
        data = yaml.safe_load(f)
    repos: dict[str, AllowedRepo] = {}
    for name, spec in data.get("repos", {}).items():
        repos[name] = AllowedRepo(
            name=name,
            url=spec["url"],
            allowed_branches=tuple(spec.get("allowed_branches", ["main"])),
            default_branch=spec.get("default_branch", "main"),
        )
    return repos


def _normalize_url(url: str) -> str:
    """Normalize a git URL for comparison."""
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    return url.lower()


def validate_repo_url(remote_url: str, path: str | None = None) -> AllowedRepo:
    """Validate that a remote URL is in the repo allowlist.

    Returns the matching AllowedRepo entry.
    Raises ValueError if the URL is not allowlisted.
    """
    repos = load_repo_allowlist(path)
    normalized = _normalize_url(remote_url)
    for repo in repos.values():
        if _normalize_url(repo.url) == normalized:
            return repo
    raise ValueError(
        f"Remote URL {remote_url!r} not in repo allowlist. "
        f"Allowed: {[r.url for r in repos.values()]}"
    )
