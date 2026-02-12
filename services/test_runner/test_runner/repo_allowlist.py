"""Repo allowlist loader – only pre-approved repos may be targeted.

Security-critical module.  Changes here require review.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import yaml

_REPO_ALLOWLIST_PATH = os.environ.get(
    "REPO_ALLOWLIST_PATH", "/app/configs/repo_allowlist.yaml"
)


# ---------------------------------------------------------------------------
# Exception hierarchy  (all map to HTTP 400 in the API layer)
# ---------------------------------------------------------------------------


class AllowlistConfigError(Exception):
    """Repo allowlist config is missing, unreadable, or malformed."""


class RepoNotAllowedError(ValueError):
    """Remote URL is not in the repo allowlist."""


class RepoNameMismatchError(ValueError):
    """Caller-supplied repo_name does not match the allowlisted entry."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AllowedRepo:
    name: str
    url: str
    allowed_branches: tuple[str, ...]
    default_branch: str


# ---------------------------------------------------------------------------
# Canonical Git URL normalization
#
# Both  https://github.com/ORG/REPO(.git)
# and   git@github.com:ORG/REPO(.git)
# become the canonical form:  github.com/org/repo
# ---------------------------------------------------------------------------

# SSH:  git@host:path(.git)  or  ssh://git@host/path(.git)
_SSH_RE = re.compile(
    r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/*$",
    re.IGNORECASE,
)

# HTTPS:  https://host/path(.git)
_HTTPS_RE = re.compile(
    r"^https?://([^/]+)/(.+?)(?:\.git)?/*$",
    re.IGNORECASE,
)


def canonicalize_url(url: str) -> str:
    """Reduce *any* common Git remote URL to ``host/owner/repo`` (lowercase).

    >>> canonicalize_url("https://github.com/Org/Repo.git")
    'github.com/org/repo'
    >>> canonicalize_url("git@github.com:Org/Repo.git")
    'github.com/org/repo'

    Raises ``ValueError`` for unparseable URLs (including empty strings).
    """
    url = url.strip()
    if not url:
        raise ValueError("Empty remote URL")

    m = _SSH_RE.match(url)
    if m:
        host, path = m.group(1), m.group(2)
        return f"{host}/{path}".lower()

    m = _HTTPS_RE.match(url)
    if m:
        host, path = m.group(1), m.group(2)
        return f"{host}/{path}".lower()

    raise ValueError(f"Cannot parse Git URL: {url!r}")


# ---------------------------------------------------------------------------
# Allowlist loading
# ---------------------------------------------------------------------------


def load_repo_allowlist(path: str | None = None) -> dict[str, AllowedRepo]:
    """Load the repo allowlist from YAML.

    Returns a mapping of ``repo_name -> AllowedRepo``.

    Raises :class:`AllowlistConfigError` for missing / malformed files.
    """
    path = path or _REPO_ALLOWLIST_PATH
    try:
        with open(path, "rb") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise AllowlistConfigError(
            f"Repo allowlist config not found: {path}"
        )
    except (yaml.YAMLError, OSError) as exc:
        raise AllowlistConfigError(
            f"Repo allowlist config invalid: {path}: {exc}"
        )

    if not isinstance(data, dict) or "repos" not in data:
        raise AllowlistConfigError(
            f"Repo allowlist config missing 'repos' key: {path}"
        )
    repos_data = data["repos"]
    if not isinstance(repos_data, dict):
        raise AllowlistConfigError(
            f"Repo allowlist 'repos' must be a mapping: {path}"
        )

    repos: dict[str, AllowedRepo] = {}
    for name, spec in repos_data.items():
        if not isinstance(spec, dict) or "url" not in spec:
            raise AllowlistConfigError(
                f"Repo entry {name!r} missing required 'url' field"
            )
        if not isinstance(spec["url"], str):
            raise AllowlistConfigError(
                f"Repo entry {name!r} 'url' must be a string, "
                f"got {type(spec['url']).__name__}"
            )
        repos[name] = AllowedRepo(
            name=name,
            url=spec["url"],
            allowed_branches=tuple(spec.get("allowed_branches", ["main"])),
            default_branch=spec.get("default_branch", "main"),
        )
    return repos


# ---------------------------------------------------------------------------
# Internal normalization (delegates to canonicalize_url, safe fallback)
# ---------------------------------------------------------------------------


def _normalize_url(url: str) -> str:
    """Best-effort normalization for comparison.

    Uses :func:`canonicalize_url` when possible; falls back to a simple
    strip/lower/de-suffix for exotic URL forms.
    """
    try:
        return canonicalize_url(url)
    except ValueError:
        url = url.strip().rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        return url.lower()


# ---------------------------------------------------------------------------
# Public validation API
# ---------------------------------------------------------------------------


def validate_repo(
    remote_url: str,
    repo_name: str | None = None,
    path: str | None = None,
) -> AllowedRepo:
    """Validate *remote_url* against the repo allowlist.

    If *repo_name* is given, also enforces that it matches the allowlisted
    entry's ``name`` field — preventing callers from claiming one repo while
    targeting a different allowed repo.

    Returns the matching :class:`AllowedRepo`.

    Raises:
        AllowlistConfigError: config missing / malformed.
        ValueError:           empty / unparseable URL.
        RepoNotAllowedError:  URL not in allowlist.
        RepoNameMismatchError: repo_name doesn't match allowlisted name.
    """
    if not remote_url or not remote_url.strip():
        raise ValueError("remote_url must not be empty")

    repos = load_repo_allowlist(path)
    normalized = _normalize_url(remote_url)

    matched: AllowedRepo | None = None
    for repo in repos.values():
        if _normalize_url(repo.url) == normalized:
            matched = repo
            break

    if matched is None:
        raise RepoNotAllowedError(
            f"Remote URL {remote_url!r} not in repo allowlist. "
            f"Allowed: {[r.url for r in repos.values()]}"
        )

    if repo_name is not None and repo_name != matched.name:
        raise RepoNameMismatchError(
            f"repo_name {repo_name!r} does not match allowlisted name "
            f"{matched.name!r} for URL {remote_url!r}"
        )

    return matched


def validate_repo_url(remote_url: str, path: str | None = None) -> AllowedRepo:
    """Backward-compatible wrapper — validates URL only (no name check)."""
    return validate_repo(remote_url, repo_name=None, path=path)
