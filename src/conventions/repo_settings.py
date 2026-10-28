"""`conventions repo-settings --check | --apply`: the GitHub settings every repo shares.

- Merge commits only (no squash, no rebase), and the head branch deleted on merge.
- `main` protected, and `dev` too in a `flow = "dev"` repo: changes only through a pull request, the
  required checks green on an up-to-date branch, admins included, conversations resolved, no force
  pushes or deletion.
- In a `flow = "dev"` repo, `dev` is the default branch, so pull requests target it by default.
- A ruleset on `v*` tags: anyone who may push can create a release tag (the release workflow does), but
  nobody can move or delete one, admins included. Every repo's CI and pins resolve those tags.

A private repo on GitHub Free can't have branch protection or rulesets. There the merge settings still
apply and are still checked, and each protected branch and the tag ruleset get a warning instead of a
failure.

This is the only way these settings change; the GitHub UI is for looking. It talks to GitHub through
the `gh` CLI, so it acts as whoever `gh auth status` says.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from conventions.config import Config

REPO_FIELDS = {
    "allow_merge_commit": True,
    "allow_squash_merge": False,
    "allow_rebase_merge": False,
    "delete_branch_on_merge": True,
}


TAG_RULESET: dict[str, Any] = {
    "name": "release tags",
    "target": "tag",
    "enforcement": "active",
    "bypass_actors": [],
    "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
    "rules": [{"type": "update"}, {"type": "deletion"}],
}
TAGS = "v* tags"


# What GitHub answers (HTTP 403) when a plan doesn't allow branch protection on a private repo.
UPGRADE_NEEDED = "Upgrade to GitHub Pro or make this repository public"


class GhError(Exception):
    pass


def gh(*args: str, payload: dict[str, Any] | None = None) -> Any:
    cmd = ["gh", "api", *args]
    if payload is not None:
        cmd += ["--input", "-"]
    proc = subprocess.run(
        cmd,
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GhError(proc.stderr.strip() or proc.stdout.strip())
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def current_repo() -> str:
    proc = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GhError(proc.stderr.strip())
    return proc.stdout.strip()


def protection_payload(cfg: Config) -> dict[str, Any]:
    return {
        "required_status_checks": {"strict": True, "contexts": cfg.required_checks},
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 0,
        },
        "restrictions": None,
        "required_linear_history": False,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "required_conversation_resolution": True,
    }


def default_branch(cfg: Config) -> str:
    return "dev" if cfg.flow == "dev" else "main"


def diff_repo(actual: dict[str, Any], cfg: Config) -> list[str]:
    problems = [
        f"{key} is {actual.get(key)!r}, should be {want!r}"
        for key, want in REPO_FIELDS.items()
        if actual.get(key) != want
    ]
    if actual.get("default_branch") != default_branch(cfg):
        problems.append(f"default branch is {actual.get('default_branch')!r}, should be {default_branch(cfg)!r}")
    return problems


def diff_protection(branch: str, actual: dict[str, Any] | None, cfg: Config) -> list[str]:
    if actual is None:
        return [f"{branch} is not protected"]
    problems = []
    checks = actual.get("required_status_checks") or {}
    if not checks.get("strict"):
        problems.append(f"{branch}: branches need not be up to date before merging")
    have = sorted(checks.get("contexts") or [])
    want = sorted(cfg.required_checks)
    if have != want:
        problems.append(f"{branch}: required checks are {have}, should be {want}")
    if not (actual.get("enforce_admins") or {}).get("enabled"):
        problems.append(f"{branch}: admins are not included")
    if actual.get("required_pull_request_reviews") is None:
        problems.append(f"{branch}: changes need not go through a pull request")
    if (actual.get("allow_force_pushes") or {}).get("enabled"):
        problems.append(f"{branch}: force pushes are allowed")
    if (actual.get("allow_deletions") or {}).get("enabled"):
        problems.append(f"{branch}: the branch can be deleted")
    if not (actual.get("required_conversation_resolution") or {}).get("enabled"):
        problems.append(f"{branch}: conversations need not be resolved")
    return problems


def get_protection(repo: str, branch: str) -> dict[str, Any] | None:
    try:
        result: dict[str, Any] = gh(f"repos/{repo}/branches/{branch}/protection")
        return result
    except GhError as e:
        if "Branch not protected" in str(e) or "Not Found" in str(e):
            return None
        raise


def get_tag_ruleset(repo: str) -> dict[str, Any] | None:
    """The repo's own ruleset named like TAG_RULESET, with its rules, or None."""
    for summary in gh(f"repos/{repo}/rulesets"):
        if summary.get("name") == TAG_RULESET["name"] and summary.get("source") == repo:
            result: dict[str, Any] = gh(f"repos/{repo}/rulesets/{summary['id']}")
            return result
    return None


def diff_tag_ruleset(actual: dict[str, Any] | None) -> list[str]:
    if actual is None:
        return [f"no ruleset protects {TAGS}"]
    problems = []
    for key in ("target", "enforcement"):
        if actual.get(key) != TAG_RULESET[key]:
            problems.append(f"{TAGS}: ruleset {key} is {actual.get(key)!r}, should be {TAG_RULESET[key]!r}")
    if actual.get("bypass_actors"):
        problems.append(f"{TAGS}: the ruleset can be bypassed")
    want_refs = TAG_RULESET["conditions"]["ref_name"]["include"]
    have_refs = (actual.get("conditions") or {}).get("ref_name", {}).get("include")
    if have_refs != want_refs:
        problems.append(f"{TAGS}: ruleset applies to {have_refs!r}, should be {want_refs!r}")
    have = sorted(rule.get("type", "") for rule in actual.get("rules") or [])
    want = sorted(rule["type"] for rule in TAG_RULESET["rules"])
    if have != want:
        problems.append(f"{TAGS}: ruleset rules are {have}, should be {want}")
    return problems


def unprotectable(e: GhError, repo_info: dict[str, Any]) -> bool:
    return UPGRADE_NEEDED in str(e) and bool(repo_info.get("private"))


def free_warning(branch: str) -> str:
    return f"{branch} can't be protected: private repo on GitHub Free"


def check(repo: str, cfg: Config) -> tuple[list[str], list[str]]:
    """The problems, and the warnings: branches that can't be protected on this plan."""
    repo_info = gh(f"repos/{repo}")
    problems = diff_repo(repo_info, cfg)
    warnings = []
    for branch in cfg.protected_branches:
        try:
            protection = get_protection(repo, branch)
        except GhError as e:
            if not unprotectable(e, repo_info):
                raise
            warnings.append(free_warning(branch))
            continue
        problems += diff_protection(branch, protection, cfg)
    try:
        problems += diff_tag_ruleset(get_tag_ruleset(repo))
    except GhError as e:
        if not unprotectable(e, repo_info):
            raise
        warnings.append(free_warning(TAGS))
    return problems, warnings


def apply(repo: str, cfg: Config) -> tuple[list[str], list[str]]:
    """What was done, and the warnings: branches that can't be protected on this plan."""
    done = []
    warnings = []
    repo_info = gh("-X", "PATCH", f"repos/{repo}", payload={**REPO_FIELDS, "default_branch": default_branch(cfg)})
    done.append(f"{repo}: merge commits only, branches deleted on merge, default branch {default_branch(cfg)}")
    for branch in cfg.protected_branches:
        try:
            gh("-X", "PUT", f"repos/{repo}/branches/{branch}/protection", payload=protection_payload(cfg))
        except GhError as e:
            if not unprotectable(e, repo_info or {}):
                raise
            warnings.append(free_warning(branch))
            continue
        done.append(f"{repo}: {branch} protected; required checks {', '.join(cfg.required_checks)}")
    try:
        existing = get_tag_ruleset(repo)
        if existing is None:
            gh("-X", "POST", f"repos/{repo}/rulesets", payload=TAG_RULESET)
        else:
            gh("-X", "PUT", f"repos/{repo}/rulesets/{existing['id']}", payload=TAG_RULESET)
    except GhError as e:
        if not unprotectable(e, repo_info or {}):
            raise
        warnings.append(free_warning(TAGS))
        return done, warnings
    done.append(f"{repo}: {TAGS} protected; no update, no deletion")
    return done, warnings
