"""Tests for repo allowlist parsing, URL canonicalization, and enforcement."""

from __future__ import annotations

import os

import pytest
import yaml

os.environ.setdefault("REPO_ALLOWLIST_PATH", "/dev/null")

from test_runner.repo_allowlist import (
    AllowedRepo,
    AllowlistConfigError,
    RepoNameMismatchError,
    RepoNotAllowedError,
    canonicalize_url,
    load_repo_allowlist,
    validate_repo,
    validate_repo_url,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# canonicalize_url() unit tests
# ---------------------------------------------------------------------------


class TestCanonicalizeUrl:
    """Verify the canonical URL normalizer handles all Git URL forms."""

    def test_ssh_with_git_suffix(self):
        assert (
            canonicalize_url("git@github.com:brysonryoung1-cyber/algo-nt8-orb.git")
            == "github.com/brysonryoung1-cyber/algo-nt8-orb"
        )

    def test_ssh_without_git_suffix(self):
        assert (
            canonicalize_url("git@github.com:brysonryoung1-cyber/algo-nt8-orb")
            == "github.com/brysonryoung1-cyber/algo-nt8-orb"
        )

    def test_https_with_git_suffix(self):
        assert (
            canonicalize_url(
                "https://github.com/brysonryoung1-cyber/algo-nt8-orb.git"
            )
            == "github.com/brysonryoung1-cyber/algo-nt8-orb"
        )

    def test_https_without_git_suffix(self):
        assert (
            canonicalize_url(
                "https://github.com/brysonryoung1-cyber/algo-nt8-orb"
            )
            == "github.com/brysonryoung1-cyber/algo-nt8-orb"
        )

    def test_ssh_prefix_form(self):
        assert (
            canonicalize_url(
                "ssh://git@github.com/brysonryoung1-cyber/algo-nt8-orb.git"
            )
            == "github.com/brysonryoung1-cyber/algo-nt8-orb"
        )

    def test_http_form(self):
        assert (
            canonicalize_url(
                "http://github.com/brysonryoung1-cyber/algo-nt8-orb.git"
            )
            == "github.com/brysonryoung1-cyber/algo-nt8-orb"
        )

    def test_trailing_slash(self):
        assert (
            canonicalize_url(
                "https://github.com/brysonryoung1-cyber/algo-nt8-orb.git/"
            )
            == "github.com/brysonryoung1-cyber/algo-nt8-orb"
        )

    def test_case_insensitive(self):
        assert (
            canonicalize_url(
                "HTTPS://GitHub.com/BrysonRYoung1-Cyber/Algo-NT8-ORB.git"
            )
            == "github.com/brysonryoung1-cyber/algo-nt8-orb"
        )

    def test_ssh_and_https_are_equivalent(self):
        """THE critical test: SSH and HTTPS forms of the same repo must
        produce identical canonical strings."""
        ssh = canonicalize_url(
            "git@github.com:brysonryoung1-cyber/algo-nt8-orb.git"
        )
        https = canonicalize_url(
            "https://github.com/brysonryoung1-cyber/algo-nt8-orb.git"
        )
        assert ssh == https

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="Empty remote URL"):
            canonicalize_url("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="Empty remote URL"):
            canonicalize_url("   ")

    def test_unparseable_raises(self):
        with pytest.raises(ValueError, match="Cannot parse Git URL"):
            canonicalize_url("not-a-url")

    def test_leading_trailing_whitespace_stripped(self):
        assert (
            canonicalize_url("  https://github.com/org/repo.git  ")
            == "github.com/org/repo"
        )


# ---------------------------------------------------------------------------
# load_repo_allowlist() tests
# ---------------------------------------------------------------------------


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


def test_repo_frozen(repo_allowlist_file):
    """AllowedRepo is frozen -- no mutation."""
    repos = load_repo_allowlist(repo_allowlist_file)
    orb = repos["algo-nt8-orb"]
    with pytest.raises(AttributeError):
        orb.url = "https://evil.com/repo.git"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# load_repo_allowlist() error handling (-> AllowlistConfigError -> 400)
# ---------------------------------------------------------------------------


class TestAllowlistConfigErrors:
    """Missing / malformed config must raise AllowlistConfigError, not crash."""

    def test_missing_file(self, tmp_path):
        with pytest.raises(AllowlistConfigError, match="not found"):
            load_repo_allowlist(str(tmp_path / "nope.yaml"))

    def test_invalid_yaml(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(": : : [broken")
        with pytest.raises(AllowlistConfigError, match="invalid"):
            load_repo_allowlist(str(bad))

    def test_missing_repos_key(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text(yaml.dump({"something_else": {}}))
        with pytest.raises(AllowlistConfigError, match="missing 'repos' key"):
            load_repo_allowlist(str(f))

    def test_repos_not_a_mapping(self, tmp_path):
        f = tmp_path / "list.yaml"
        f.write_text(yaml.dump({"repos": ["a", "b"]}))
        with pytest.raises(AllowlistConfigError, match="must be a mapping"):
            load_repo_allowlist(str(f))

    def test_repo_entry_missing_url(self, tmp_path):
        f = tmp_path / "nourl.yaml"
        f.write_text(yaml.dump({"repos": {"bad": {"no_url_here": True}}}))
        with pytest.raises(AllowlistConfigError, match="missing required 'url'"):
            load_repo_allowlist(str(f))

    def test_repo_entry_url_not_string(self, tmp_path):
        f = tmp_path / "badurl.yaml"
        f.write_text(yaml.dump({"repos": {"bad": {"url": 12345}}}))
        with pytest.raises(AllowlistConfigError, match="must be a string"):
            load_repo_allowlist(str(f))


# ---------------------------------------------------------------------------
# validate_repo_url() — backward compat wrapper (URL-only validation)
# ---------------------------------------------------------------------------


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
    with pytest.raises(RepoNotAllowedError, match="not in repo allowlist"):
        validate_repo_url(
            "https://github.com/evil-user/evil-repo.git",
            repo_allowlist_file,
        )


def test_reject_similar_but_different_user(repo_allowlist_file):
    with pytest.raises(RepoNotAllowedError, match="not in repo allowlist"):
        validate_repo_url(
            "https://github.com/other-user/algo-nt8-orb.git",
            repo_allowlist_file,
        )


def test_reject_similar_but_different_repo(repo_allowlist_file):
    with pytest.raises(RepoNotAllowedError, match="not in repo allowlist"):
        validate_repo_url(
            "https://github.com/brysonryoung1-cyber/algo-nt8-orb-EVIL.git",
            repo_allowlist_file,
        )


def test_reject_empty_url(repo_allowlist_file):
    with pytest.raises(ValueError, match="must not be empty"):
        validate_repo_url("", repo_allowlist_file)


# ---------------------------------------------------------------------------
# SSH <-> HTTPS equivalence (the main bug-class fix)
# ---------------------------------------------------------------------------


class TestSshHttpsEquivalence:
    """SSH and HTTPS forms of the same repo MUST both be accepted when
    the allowlist contains either form."""

    def test_https_matches_ssh_allowlist(self, repo_allowlist_file):
        """Allowlist has SSH URL; HTTPS form of same repo must match."""
        repo = validate_repo_url(
            "https://github.com/brysonryoung1-cyber/algo-nt8-orb.git",
            repo_allowlist_file,
        )
        assert repo.name == "algo-nt8-orb"

    def test_https_no_suffix_matches_ssh_allowlist(self, repo_allowlist_file):
        repo = validate_repo_url(
            "https://github.com/brysonryoung1-cyber/algo-nt8-orb",
            repo_allowlist_file,
        )
        assert repo.name == "algo-nt8-orb"

    def test_http_matches_ssh_allowlist(self, repo_allowlist_file):
        repo = validate_repo_url(
            "http://github.com/brysonryoung1-cyber/algo-nt8-orb.git",
            repo_allowlist_file,
        )
        assert repo.name == "algo-nt8-orb"

    def test_ssh_prefix_form_matches(self, repo_allowlist_file):
        repo = validate_repo_url(
            "ssh://git@github.com/brysonryoung1-cyber/algo-nt8-orb.git",
            repo_allowlist_file,
        )
        assert repo.name == "algo-nt8-orb"

    def test_https_allowlist_matches_ssh_input(self, tmp_path):
        """Reverse case: allowlist has HTTPS URL; SSH input must match."""
        data = {
            "repos": {
                "my-repo": {
                    "url": "https://github.com/org/my-repo.git",
                }
            }
        }
        f = tmp_path / "allowlist.yaml"
        f.write_text(yaml.dump(data))
        repo = validate_repo_url(
            "git@github.com:org/my-repo.git",
            str(f),
        )
        assert repo.name == "my-repo"


# ---------------------------------------------------------------------------
# validate_repo() — full validation with repo_name consistency
# ---------------------------------------------------------------------------


class TestRepoNameConsistency:
    """repo_name must match the allowlisted entry for the resolved URL."""

    def test_correct_name_passes(self, repo_allowlist_file):
        repo = validate_repo(
            remote_url="git@github.com:brysonryoung1-cyber/algo-nt8-orb.git",
            repo_name="algo-nt8-orb",
            path=repo_allowlist_file,
        )
        assert repo.name == "algo-nt8-orb"

    def test_wrong_name_rejected(self, repo_allowlist_file):
        with pytest.raises(RepoNameMismatchError, match="does not match"):
            validate_repo(
                remote_url="git@github.com:brysonryoung1-cyber/algo-nt8-orb.git",
                repo_name="totally-different-name",
                path=repo_allowlist_file,
            )

    def test_none_name_skips_check(self, repo_allowlist_file):
        """When repo_name is None, name consistency is not enforced."""
        repo = validate_repo(
            remote_url="git@github.com:brysonryoung1-cyber/algo-nt8-orb.git",
            repo_name=None,
            path=repo_allowlist_file,
        )
        assert repo.name == "algo-nt8-orb"

    def test_mismatch_with_https_url(self, repo_allowlist_file):
        """Name check works even when URL form differs from allowlist."""
        with pytest.raises(RepoNameMismatchError, match="does not match"):
            validate_repo(
                remote_url="https://github.com/brysonryoung1-cyber/algo-nt8-orb",
                repo_name="wrong-name",
                path=repo_allowlist_file,
            )

    def test_correct_name_with_https_url(self, repo_allowlist_file):
        repo = validate_repo(
            remote_url="https://github.com/brysonryoung1-cyber/algo-nt8-orb",
            repo_name="algo-nt8-orb",
            path=repo_allowlist_file,
        )
        assert repo.name == "algo-nt8-orb"
