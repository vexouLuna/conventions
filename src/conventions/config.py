"""`.conventions.toml`: which version a repo pins, its profile and its release flow."""

from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = ".conventions.toml"

# Profile -> the language its files and checks come from (None: no language-specific files).
PROFILES: dict[str, str | None] = {
    "python-service": "python",
    "python-lib": "python",
    "node-lib": "node",
    "site": "node",
    "docs": None,
}
FLOWS = ("trunk", "dev")

# Required checks, as GitHub names them: "<caller job> / <reusable workflow job>". Every repo's ci.yml
# calls conventions-check.yml as job `conventions`, the language workflow as job `ci`, and (for a
# service) publish-image.yml as job `image`, so the names are the same everywhere.
COMMON_CHECKS = ["conventions / branch-name", "conventions / check", "conventions / version"]
PROFILE_CHECKS: dict[str, list[str]] = {
    "python-service": ["ci / lint", "ci / test", "image / build"],
    "python-lib": ["ci / lint", "ci / test"],
    "node-lib": ["ci / lint", "ci / test", "ci / build"],
    "site": ["ci / lint", "ci / test", "ci / build"],
    "docs": [],
}

VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+$")
OWNER = "vexouLuna"
_GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    version: str
    profile: str
    flow: str = "trunk"
    extra_checks: list[str] = field(default_factory=list)
    extra_workflows: list[str] = field(default_factory=list)
    # True only in vexouLuna/conventions itself, which checks against its own checkout rather than a pin.
    self_repo: bool = False
    # `status = { page = "...", endpoints = [...] }`: Gatus endpoint keys whose badges go in the README.
    status_page: str = ""
    status_endpoints: list[str] = field(default_factory=list)
    # `json_badges = [{ label, url, query }]`: one value from a public JSON endpoint, e.g. a count.
    json_badges: list[dict[str, str]] = field(default_factory=list)
    # "owner/name" on GitHub, from the origin remote; not read from the file.
    repo: str = f"{OWNER}/repo"

    @property
    def language(self) -> str | None:
        return PROFILES[self.profile]

    @property
    def required_checks(self) -> list[str]:
        return COMMON_CHECKS + PROFILE_CHECKS[self.profile] + self.extra_checks

    @property
    def protected_branches(self) -> list[str]:
        return ["main", "dev"] if self.flow == "dev" else ["main"]

    @property
    def default_branch(self) -> str:
        return "dev" if self.flow == "dev" else "main"


def github_repo(root: Path) -> str:
    """ "owner/name" from the origin remote, else this owner and the folder's name."""
    url = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True, check=False
    ).stdout.strip()
    m = _GITHUB_REMOTE_RE.search(url)
    return m.group(1) if m else f"{OWNER}/{root.resolve().name}"


def load(root: Path) -> Config:
    path = root / CONFIG_NAME
    if not path.is_file():
        raise ConfigError(f"no {CONFIG_NAME} here; start with `conventions init --profile <profile>`")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{CONFIG_NAME}: {e}") from e

    known = {"version", "profile", "flow", "extra_checks", "extra_workflows", "self", "status", "json_badges"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"{CONFIG_NAME}: unknown keys {', '.join(unknown)}")

    version = data.get("version")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise ConfigError(f'{CONFIG_NAME}: version must look like "v1.2.3", got {version!r}')
    profile = data.get("profile")
    if profile not in PROFILES:
        raise ConfigError(f"{CONFIG_NAME}: profile must be one of {', '.join(PROFILES)}, got {profile!r}")
    flow = data.get("flow", "trunk")
    if flow not in FLOWS:
        raise ConfigError(f"{CONFIG_NAME}: flow must be one of {', '.join(FLOWS)}, got {flow!r}")

    def str_list(key: str) -> list[str]:
        value = data.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{CONFIG_NAME}: {key} must be a list of strings")
        return value

    self_repo = data.get("self", False)
    if not isinstance(self_repo, bool):
        raise ConfigError(f"{CONFIG_NAME}: self must be true or false")

    status = data.get("status", {})
    page = status.get("page", "") if isinstance(status, dict) else None
    endpoints = status.get("endpoints", []) if isinstance(status, dict) else None
    if (
        not isinstance(page, str)
        or not isinstance(endpoints, list)
        or not all(isinstance(e, str) for e in endpoints)
        or set(status) - {"page", "endpoints"}
        or bool(page) != bool(endpoints)
        or (page and not page.startswith("https://"))
    ):
        raise ConfigError(f'{CONFIG_NAME}: status must be {{ page = "https://...", endpoints = ["<gatus key>", ...] }}')

    json_badges = data.get("json_badges", [])
    if not isinstance(json_badges, list) or not all(
        isinstance(b, dict)
        and set(b) == {"label", "url", "query"}
        and all(isinstance(v, str) and v for v in b.values())
        and b["url"].startswith("https://")
        and b["query"].startswith("$")
        for b in json_badges
    ):
        raise ConfigError(
            f'{CONFIG_NAME}: json_badges must be [{{ label = "...", url = "https://...", query = "$.path" }}, ...]'
        )

    return Config(
        version=version,
        profile=profile,
        flow=flow,
        extra_checks=str_list("extra_checks"),
        extra_workflows=str_list("extra_workflows"),
        self_repo=self_repo,
        status_page=page.rstrip("/"),
        status_endpoints=endpoints,
        json_badges=json_badges,
        repo=github_repo(root),
    )


def render(version: str, profile: str, flow: str) -> str:
    return (
        "# Which version of vexouLuna/conventions this repo follows, and how.\n"
        "# See https://github.com/vexouLuna/conventions#conventionstoml\n"
        f'version = "{version}"\n'
        f'profile = "{profile}"\n'
        f'flow = "{flow}"\n'
        "# extra_checks = []      # required checks beyond the profile's, e.g. a repo-specific job\n"
        "# extra_workflows = []   # workflow files beyond ci.yml and publish.yml\n"
        '# status = { page = "https://status.example.com", endpoints = ["group_name"] }  # Gatus badges\n'
    )
