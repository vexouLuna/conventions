"""`conventions check`: does this repo match the version of the conventions it pins?

Each check returns a list of problems; an empty list everywhere means the repo passes.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from conventions import __version__
from conventions.config import Config
from conventions.synced import (
    BLOCKS,
    CLAUDE_IMPORT,
    CONVENTIONS_DIR,
    HOOKS_DIR,
    block_text,
    find_block,
    has_claude_import,
    is_git_repo,
    normalise,
    whole_files,
)

SYNC_HINT = "run `conventions sync` at the pinned version and commit the result"
WORKFLOWS = ".github/workflows"
ALLOWED_WORKFLOWS = {"ci.yml", "publish.yml"}
COMPOSE_NAMES = {"compose.yaml", "compose.dev.yaml"}
COMPOSE_RE = re.compile(r"^(docker-)?compose(\.[\w-]+)?\.ya?ml$")
SKIP_SCAN = {"uv.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"}
ALLOW_MARKER = "conventions:allow-infra"

# Private infrastructure a public repo must not name. Written so that this file doesn't match itself.
_PRIVATE_IP = (
    r"(?<![\d.])(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?![\d.])"
)
BUILTIN_PATTERNS = (
    ("a private IP address", re.compile(_PRIVATE_IP)),
    ("the owner's home directory", re.compile(r"/home/ve[x]\b")),
    ("a server path under /srv", re.compile(r"(?<![\w.])/sr[v]/")),
)
# More patterns (host names, domains of internal machines) come from this environment variable, one
# regular expression per line. In CI it's a repository secret, so the patterns stay out of public repos;
# a match is reported by file and line only, never by what matched.
PRIVATE_PATTERNS_ENV = "CONVENTIONS_PRIVATE_PATTERNS"


def run(root: Path, cfg: Config, public: bool = False) -> list[str]:
    files = tracked_files(root)
    problems = [
        *check_version(cfg),
        *check_whole_files(root, cfg, files),
        *check_blocks(root, cfg),
        *check_claude(root),
        *check_hooks_executable(root, cfg),
        *check_workflows(root, cfg),
        *check_layout(files),
        *check_changesets(root),
    ]
    if cfg.language == "python":
        problems += check_python(root)
    if cfg.language == "node":
        problems += check_node(root)
    if public:
        problems += check_private_infra(root, files)
    return problems


def tracked_files(root: Path) -> list[str]:
    if is_git_repo(root):
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return sorted({f for f in out.split("\0") if f and (root / f).is_file()})
    skip = {".git", "node_modules", ".venv", "__pycache__", ".ruff_cache", ".mypy_cache", ".pytest_cache"}
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and not skip & set(p.relative_to(root).parts)
    )


def check_version(cfg: Config) -> list[str]:
    if cfg.self_repo or cfg.version == f"v{__version__}":
        return []
    return [f".conventions.toml pins {cfg.version} but this is conventions v{__version__}; run the pinned version"]


def check_whole_files(root: Path, cfg: Config, files: list[str]) -> list[str]:
    problems = []
    wanted = whole_files(cfg)
    for rel, content in wanted.items():
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel} is missing; {SYNC_HINT}")
        elif normalise(path.read_text(encoding="utf-8")) != content:
            problems.append(f"{rel} differs from conventions {cfg.version}; {SYNC_HINT}")
    for rel in files:
        if rel.startswith(CONVENTIONS_DIR + "/") and rel not in wanted:
            problems.append(f"{rel} is not a synced file, and nothing else belongs in {CONVENTIONS_DIR}/")
    return problems


def check_blocks(root: Path, cfg: Config) -> list[str]:
    problems = []
    for block in BLOCKS:
        path = root / block.path
        if not path.is_file():
            problems.append(f"{block.path} is missing; {SYNC_HINT}")
            continue
        found = find_block(path.read_text(encoding="utf-8"))
        if found is None:
            problems.append(f"{block.path} has no conventions block; {SYNC_HINT}")
        elif found != block_text(cfg, block):
            problems.append(f"{block.path}: the conventions block differs from {cfg.version}; {SYNC_HINT}")
    return problems


def check_claude(root: Path) -> list[str]:
    path = root / "CLAUDE.md"
    if not path.is_file():
        return [f"CLAUDE.md is missing; it starts with `{CLAUDE_IMPORT}`"]
    if not has_claude_import(path.read_text(encoding="utf-8")):
        return [f"CLAUDE.md must start with `{CLAUDE_IMPORT}`"]
    return []


def check_hooks_executable(root: Path, cfg: Config) -> list[str]:
    if not is_git_repo(root):
        return []
    hooks = [rel for rel in whole_files(cfg) if rel.startswith(HOOKS_DIR + "/")]
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-s", "--", *hooks], capture_output=True, text=True, check=True
    ).stdout
    modes = {line.split("\t", 1)[1]: line.split()[0] for line in out.splitlines() if "\t" in line}
    return [
        f"{rel} is not executable in git; `git add --chmod=+x {rel}`"
        for rel in hooks
        if rel in modes and modes[rel] != "100755"
    ]


# --- workflows ---------------------------------------------------------------------------------------


def workflow_jobs(text: str) -> dict[str, str | None]:
    """Job id -> the `uses:` of a job that calls a reusable workflow (None for a job with steps).

    Enough YAML for the workflows these repos write: `jobs:` at column 0, job ids indented two spaces,
    their keys four.
    """
    jobs: dict[str, str | None] = {}
    in_jobs = False
    current: str | None = None
    for line in normalise(text).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            in_jobs = line.rstrip() == "jobs:"
            current = None
            continue
        if not in_jobs:
            continue
        m = re.match(r"^  ([\w-]+):\s*$", line)
        if m:
            current = m.group(1)
            jobs[current] = None
            continue
        m = re.match(r"^    uses:\s*['\"]?([^'\"\s#]+)", line)
        if m and current:
            jobs[current] = m.group(1)
    return jobs


def _expected_calls(cfg: Config) -> list[tuple[str, str, str]]:
    """(workflow file, job id, reusable workflow) that a repo of this profile must have."""
    calls = [("ci.yml", "conventions", "conventions-check.yml")]
    if cfg.language == "python":
        calls.append(("ci.yml", "ci", "python-ci.yml"))
    if cfg.language == "node":
        calls.append(("ci.yml", "ci", "node-ci.yml"))
    if cfg.profile == "python-service":
        calls.append(("ci.yml", "image", "publish-image.yml"))
    if cfg.profile == "site":
        calls.append(("publish.yml", "publish", "publish-site.yml"))
    if cfg.flow == "dev":
        calls.append(("release.yml", "prepare", "release-prepare.yml"))
        calls.append(("release.yml", "finish", "release-finish.yml"))
    return calls


# The GitHub owner of this repo: vexouLuna, renamed from vEXOULZ in Oct 2026. GitHub matches owners
# case-insensitively, and the old name still redirects, so a workflow that names it keeps passing.
OWNERS = ("vEXOULZ", "vexouLuna")
REMOTE_USES = re.compile(rf"^(?i:{'|'.join(OWNERS)})/conventions/\.github/workflows/([\w.-]+)@(.+)$")
# `secrets: inherit` hands every repository secret to the called workflow; name the ones it declares instead.
SECRETS_INHERIT = re.compile(r"^\s*secrets:\s*['\"]?inherit['\"]?\s*(#.*)?$")


def check_workflows(root: Path, cfg: Config) -> list[str]:
    problems = []
    wdir = root / WORKFLOWS
    present = sorted(p.name for p in wdir.glob("*.y*ml")) if wdir.is_dir() else []
    allowed = ALLOWED_WORKFLOWS | set(cfg.extra_workflows)
    if cfg.flow == "dev":
        allowed.add("release.yml")
    if cfg.self_repo:
        # The reusable workflows themselves live here.
        allowed |= {p.name for p in wdir.glob("*.yml") if "workflow_call:" in p.read_text(encoding="utf-8")}
    for name in present:
        if name not in allowed:
            problems.append(
                f"{WORKFLOWS}/{name}: workflows are ci.yml (checks), publish.yml (publishing) and, "
                'in a flow = "dev" repo, release.yml; '
                "add it to extra_workflows in .conventions.toml if it really is neither"
            )

    texts = {name: (wdir / name).read_text(encoding="utf-8") for name in present}
    for name, text in texts.items():
        for n, line in enumerate(normalise(text).splitlines(), 1):
            if SECRETS_INHERIT.match(line):
                problems.append(
                    f"{WORKFLOWS}/{name}:{n}: `secrets: inherit` passes every repository secret; "
                    "pass only the ones the reusable workflow declares, e.g. "
                    "`CONVENTIONS_PRIVATE_PATTERNS: ${{ secrets.CONVENTIONS_PRIVATE_PATTERNS }}`"
                )

    jobs_by_file = {name: workflow_jobs(text) for name, text in texts.items()}
    for name, jobs in jobs_by_file.items():
        for job, uses in jobs.items():
            m = REMOTE_USES.match(uses or "")
            if m and m.group(2) != cfg.version:
                problems.append(
                    f"{WORKFLOWS}/{name}: job {job} uses {m.group(1)}@{m.group(2)}, "
                    f"but .conventions.toml pins {cfg.version}"
                )

    for file, job, reusable in _expected_calls(cfg):
        uses = jobs_by_file.get(file, {}).get(job)
        want = f"./.github/workflows/{reusable}" if cfg.self_repo else None
        ok = uses is not None and (
            uses == want if cfg.self_repo else (m := REMOTE_USES.match(uses)) is not None and m.group(1) == reusable
        )
        if not ok:
            problems.append(
                f"{WORKFLOWS}/{file} needs a job `{job}` that uses "
                f"vexouLuna/conventions/.github/workflows/{reusable}@{cfg.version}; "
                "the job ids make the required check names"
            )
    return problems


# --- layout ------------------------------------------------------------------------------------------


def check_layout(files: list[str]) -> list[str]:
    problems = []
    for rel in files:
        name = rel.rsplit("/", 1)[-1]
        if COMPOSE_RE.match(name) and (name not in COMPOSE_NAMES or "/" in rel):
            problems.append(f"{rel}: compose files are compose.yaml and compose.dev.yaml, at the repo root")
        if name.startswith("Dockerfile") and rel != "Dockerfile":
            problems.append(f"{rel}: one multi-stage Dockerfile at the repo root; services are --target stages")
        if rel.startswith(".githooks/") and not rel.startswith(".githooks/local/"):
            problems.append(f"{rel}: the shared hooks live in {HOOKS_DIR}/; a repo's own hook goes in .githooks/local/")
    return problems


# --- languages ---------------------------------------------------------------------------------------


def _pyproject(root: Path) -> dict[str, Any] | None:
    path = root / "pyproject.toml"
    if not path.is_file():
        return None
    return tomllib.loads(path.read_text(encoding="utf-8"))


def check_python(root: Path) -> list[str]:
    problems = []
    pv = root / ".python-version"
    if not pv.is_file() or pv.read_text(encoding="utf-8").strip() != "3.13":
        problems.append(".python-version must contain 3.13")
    if not (root / "uv.lock").is_file():
        problems.append("uv.lock is missing; CI runs `uv sync --locked`")
    data = _pyproject(root)
    if data is None:
        return [*problems, "pyproject.toml is missing"]

    requires = data.get("project", {}).get("requires-python", "")
    if not requires.startswith(">=3.13"):
        problems.append(f'pyproject.toml: requires-python must be ">=3.13", not {requires!r}')

    tool = data.get("tool", {})
    ruff = tool.get("ruff", {})
    if ruff.get("extend") != ".conventions/ruff.toml":
        problems.append('pyproject.toml: [tool.ruff] needs extend = ".conventions/ruff.toml"')
    for key in ("line-length", "target-version", "select"):
        if key in ruff:
            problems.append(f"pyproject.toml: [tool.ruff] {key} comes from .conventions/ruff.toml; remove it")
    if "select" in ruff.get("lint", {}):
        problems.append("pyproject.toml: [tool.ruff.lint] select comes from .conventions/ruff.toml; use extend-select")

    mypy = tool.get("mypy", {})
    if mypy.get("strict") is not True:
        problems.append("pyproject.toml: [tool.mypy] needs strict = true (relax per module in overrides)")
    if mypy.get("python_version", "3.13") != "3.13":
        problems.append('pyproject.toml: [tool.mypy] python_version must be "3.13"')
    return problems


def check_node(root: Path) -> list[str]:
    problems = []
    if not (root / "package-lock.json").is_file():
        problems.append("package-lock.json is missing; CI runs `npm ci`")
    path = root / "package.json"
    if not path.is_file():
        return [*problems, "package.json is missing"]
    engines = json.loads(path.read_text(encoding="utf-8")).get("engines", {})
    node = engines.get("node", "") if isinstance(engines, dict) else ""
    if not re.search(r"(?<!\d)22(?!\d)", node):
        problems.append(f'package.json: engines.node must name Node 22 (e.g. ">=22"), not {node!r}')
    return problems


# A changeset's header: `---`, one `"<pkg>": <bump>` line per package (none at all is an empty changeset), `---`.
CHANGESET_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*\r?$", re.S | re.M)
CHANGESET_LINE_RE = re.compile(r"""^\s*(?:"[^"]+"|'[^']+'|[^\s:'"]+)\s*:\s*(major|minor|patch|none)\s*$""")


def check_changesets(root: Path) -> list[str]:
    """Each changeset must parse, or `changeset version` fails at release time instead of in the PR."""
    problems = []
    for path in sorted((root / ".changeset").glob("*.md")):
        if path.name == "README.md":
            continue
        rel = f".changeset/{path.name}"
        # Stricter than @changesets/parse, which takes the first `---` pair anywhere and drops text above it.
        match = CHANGESET_RE.match(path.read_text(encoding="utf-8").lstrip())
        if not match:
            problems.append(f'{rel}: no `---` header; start it with `---`, `"<pkg>": patch`, `---`')
            continue
        for line in match.group(1).splitlines():
            if line.strip() and not CHANGESET_LINE_RE.match(line):
                problems.append(f'{rel}: {line.strip()!r} is not `"<pkg>": major|minor|patch` in the header')
    return problems


# --- public repos ------------------------------------------------------------------------------------


def private_patterns() -> list[re.Pattern[str]]:
    raw = os.environ.get(PRIVATE_PATTERNS_ENV, "")
    return [re.compile(line.strip()) for line in raw.splitlines() if line.strip()]


def check_private_infra(root: Path, files: list[str]) -> list[str]:
    extra = private_patterns()
    problems = []
    for rel in files:
        if rel.rsplit("/", 1)[-1] in SKIP_SCAN:
            continue
        data = (root / rel).read_bytes()
        if len(data) > 2_000_000 or b"\0" in data[:8192]:
            continue
        for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if ALLOW_MARKER in line:
                continue
            for label, pattern in BUILTIN_PATTERNS:
                if pattern.search(line):
                    problems.append(f"{rel}:{n}: {label} in a public repo")
            if any(p.search(line) for p in extra):
                problems.append(f"{rel}:{n}: matches a private pattern ({PRIVATE_PATTERNS_ENV}) in a public repo")
    return problems
