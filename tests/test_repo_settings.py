from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conventions import config, repo_settings
from conventions.cli import main

REPO = "owner/sample"
FREE_403 = f"gh: {repo_settings.UPGRADE_NEEDED} to enable this feature. (HTTP 403)"


class FakeGh:
    """Stands in for `repo_settings.gh`: answers from a table keyed by (method, path), records the calls."""

    def __init__(self, answers: dict[tuple[str, str], Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(self, *args: str, payload: dict[str, Any] | None = None) -> Any:
        method, path = (args[1], args[2]) if args[0] == "-X" else ("GET", args[0])
        self.calls.append((method, path, payload))
        answer = self.answers[(method, path)]
        if isinstance(answer, repo_settings.GhError):
            raise answer
        return answer


def repo_info(private: bool) -> dict[str, Any]:
    return {**repo_settings.REPO_FIELDS, "default_branch": "main", "private": private}


def good_protection(cfg: config.Config) -> dict[str, Any]:
    return {
        "required_status_checks": {"strict": True, "contexts": cfg.required_checks},
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": {},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_conversation_resolution": {"enabled": True},
    }


def install(monkeypatch: pytest.MonkeyPatch, answers: dict[tuple[str, str], Any]) -> FakeGh:
    fake = FakeGh(answers)
    monkeypatch.setattr(repo_settings, "gh", fake)
    return fake


def run(repo: Path, mode: str) -> int:
    return main(["-C", str(repo), "repo-settings", mode, "--repo", REPO])


PROTECTION = f"repos/{REPO}/branches/main/protection"
RULESETS = f"repos/{REPO}/rulesets"
RULESET = {**repo_settings.TAG_RULESET, "id": 7, "source": REPO}
GOOD_RULESET = {("GET", RULESETS): [RULESET], ("GET", f"{RULESETS}/7"): RULESET}
# On GitHub Free a private repo has no rulesets either; listing them answers the same 403.
FREE_RULESET = {("GET", RULESETS): repo_settings.GhError(FREE_403)}


def test_check_passes_when_settings_match(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = config.load(repo)
    install(
        monkeypatch,
        {("GET", f"repos/{REPO}"): repo_info(False), ("GET", PROTECTION): good_protection(cfg), **GOOD_RULESET},
    )
    assert run(repo, "--check") == 0
    assert capsys.readouterr().out == f"ok: {REPO} settings match (main protected)\n"


def test_check_private_free_repo_warns_and_passes(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install(
        monkeypatch,
        {
            ("GET", f"repos/{REPO}"): repo_info(True),
            ("GET", PROTECTION): repo_settings.GhError(FREE_403),
            **FREE_RULESET,
        },
    )
    assert run(repo, "--check") == 0
    assert capsys.readouterr().out == (
        f"warning: {REPO}: main can't be protected: private repo on GitHub Free\n"
        f"warning: {REPO}: v* tags can't be protected: private repo on GitHub Free\n"
        f"ok: {REPO} settings match\n"
    )


def test_check_private_free_repo_still_checks_merge_settings(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install(
        monkeypatch,
        {
            ("GET", f"repos/{REPO}"): {**repo_info(True), "allow_squash_merge": True},
            ("GET", PROTECTION): repo_settings.GhError(FREE_403),
            **FREE_RULESET,
        },
    )
    assert run(repo, "--check") == 1
    assert capsys.readouterr().out == (
        f"warning: {REPO}: main can't be protected: private repo on GitHub Free\n"
        f"warning: {REPO}: v* tags can't be protected: private repo on GitHub Free\n"
        f"error: {REPO}: allow_squash_merge is True, should be False\n"
    )


def test_check_public_repo_403_still_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install(
        monkeypatch,
        {("GET", f"repos/{REPO}"): repo_info(False), ("GET", PROTECTION): repo_settings.GhError(FREE_403)},
    )
    assert run(repo, "--check") == 1
    assert repo_settings.UPGRADE_NEEDED in capsys.readouterr().err


def test_check_other_errors_still_fail(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install(
        monkeypatch,
        {
            ("GET", f"repos/{REPO}"): repo_info(True),
            ("GET", PROTECTION): repo_settings.GhError("gh: Resource not accessible by integration (HTTP 403)"),
        },
    )
    assert run(repo, "--check") == 1
    assert "Resource not accessible" in capsys.readouterr().err


def test_apply_protects_main(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = config.load(repo)
    fake = install(
        monkeypatch,
        {
            ("PATCH", f"repos/{REPO}"): repo_info(False),
            ("PUT", PROTECTION): {},
            ("GET", RULESETS): [],
            ("POST", RULESETS): RULESET,
        },
    )
    assert run(repo, "--apply") == 0
    assert [(m, p) for m, p, _ in fake.calls] == [
        ("PATCH", f"repos/{REPO}"),
        ("PUT", PROTECTION),
        ("GET", RULESETS),
        ("POST", RULESETS),
    ]
    assert fake.calls[1][2] == repo_settings.protection_payload(cfg)
    assert fake.calls[3][2] == repo_settings.TAG_RULESET
    out = capsys.readouterr().out
    assert "warning" not in out
    assert f"ok: {REPO}: main protected" in out
    assert f"ok: {REPO}: v* tags protected" in out


def test_apply_private_free_repo_sets_merge_settings_and_warns(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = install(
        monkeypatch,
        {
            ("PATCH", f"repos/{REPO}"): repo_info(True),
            ("PUT", PROTECTION): repo_settings.GhError(FREE_403),
            **FREE_RULESET,
        },
    )
    assert run(repo, "--apply") == 0
    assert fake.calls[0] == ("PATCH", f"repos/{REPO}", {**repo_settings.REPO_FIELDS, "default_branch": "main"})
    assert capsys.readouterr().out == (
        f"ok: {REPO}: merge commits only, branches deleted on merge, default branch main\n"
        f"warning: {REPO}: main can't be protected: private repo on GitHub Free\n"
        f"warning: {REPO}: v* tags can't be protected: private repo on GitHub Free\n"
    )


def test_apply_public_repo_403_still_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install(
        monkeypatch,
        {("PATCH", f"repos/{REPO}"): repo_info(False), ("PUT", PROTECTION): repo_settings.GhError(FREE_403)},
    )
    assert run(repo, "--apply") == 1
    assert repo_settings.UPGRADE_NEEDED in capsys.readouterr().err


def test_check_fails_without_tag_ruleset(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = config.load(repo)
    install(
        monkeypatch,
        {("GET", f"repos/{REPO}"): repo_info(False), ("GET", PROTECTION): good_protection(cfg), ("GET", RULESETS): []},
    )
    assert run(repo, "--check") == 1
    assert capsys.readouterr().out == f"error: {REPO}: no ruleset protects v* tags\n"


def test_check_fails_when_tag_ruleset_lets_tags_move(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = config.load(repo)
    weak = {**RULESET, "enforcement": "evaluate", "rules": [{"type": "deletion"}]}
    install(
        monkeypatch,
        {
            ("GET", f"repos/{REPO}"): repo_info(False),
            ("GET", PROTECTION): good_protection(cfg),
            ("GET", RULESETS): [{**weak, "rules": None}, {**weak, "id": 8, "source": "the-org"}],
            ("GET", f"{RULESETS}/7"): weak,
        },
    )
    assert run(repo, "--check") == 1
    assert capsys.readouterr().out == (
        f"error: {REPO}: v* tags: ruleset enforcement is 'evaluate', should be 'active'\n"
        f"error: {REPO}: v* tags: ruleset rules are ['deletion'], should be ['deletion', 'update']\n"
    )


def test_apply_updates_an_existing_tag_ruleset(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(
        monkeypatch,
        {
            ("PATCH", f"repos/{REPO}"): repo_info(False),
            ("PUT", PROTECTION): {},
            **GOOD_RULESET,
            ("PUT", f"{RULESETS}/7"): RULESET,
        },
    )
    assert run(repo, "--apply") == 0
    assert fake.calls[-1] == ("PUT", f"{RULESETS}/7", repo_settings.TAG_RULESET)
