"""What a repo should contain, and `conventions sync`, which writes it.

Two kinds of synced content:

- Whole files (everything under files/common/ and files/<language>/), copied byte for byte. Nothing else
  may live in `.conventions/`.
- Managed blocks inside files the repo otherwise owns (README.md's badges, CONTRIBUTING.md, the PR
  template, .gitignore): only the text between the `conventions:begin` and `conventions:end` markers is synced.

The repo's own CLAUDE.md imports `.conventions/CLAUDE.md` on its first line.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from conventions.config import OWNER, Config

FILES = files("conventions") / "files"
CONVENTIONS_DIR = ".conventions"
HOOKS_DIR = ".conventions/githooks"
CLAUDE_IMPORT = "@.conventions/CLAUDE.md"

_NOTE = "synced from vexouLuna/conventions; edit it there, then run `conventions sync`"
MARKERS = {
    "md": (f"<!-- conventions:begin: {_NOTE} -->", "<!-- conventions:end -->"),
    "hash": (f"# conventions:begin: {_NOTE}", "# conventions:end"),
}
_BLOCK_RE = re.compile(
    r"^(?:<!--|#) conventions:begin\b[^\n]*\n.*?^(?:<!--|#) conventions:end\b[^\n]*(?:\n|\Z)",
    re.MULTILINE | re.DOTALL,
)


@dataclass(frozen=True)
class Block:
    path: str
    style: str  # key into MARKERS
    parts: tuple[str, ...]  # under files/blocks/; {flow} and {language} are filled in, missing parts skipped
    placement: str  # "after-title": below a leading "# " heading; "top": first thing in the file
    create: str  # what a new file holds above the block
    render: Callable[[Config], str] | None = None  # builds the body from the config instead of parts


# Profiles that publish versions as tags.
TAGGED_PROFILES = {"python-lib", "node-lib", "python-service"}


def badges(cfg: Config) -> str:
    gh = f"https://github.com/{cfg.repo}"
    ci = f"{gh}/actions/workflows/ci.yml"
    lines = [f"[![CI]({ci}/badge.svg?branch={cfg.default_branch})]({ci}?query=branch%3A{cfg.default_branch})"]
    if cfg.profile == "site":
        publish = f"{gh}/actions/workflows/publish.yml"
        lines.append(f"[![publish]({publish}/badge.svg?branch=main)]({publish}?query=branch%3Amain)")
    if cfg.profile in TAGGED_PROFILES:
        lines.append(
            f"[![version](https://img.shields.io/github/v/tag/{cfg.repo}?sort=semver&label=version)]({gh}/tags)"
        )
    if not cfg.self_repo:
        pin = f"https://img.shields.io/badge/conventions-{cfg.version}-blue"
        lines.append(
            f"[![conventions {cfg.version}]({pin})](https://github.com/{OWNER}/conventions/tree/{cfg.version})"
        )
    for key in cfg.status_endpoints:
        api = f"{cfg.status_page}/api/v1/endpoints/{key}"
        lines.append(f"[![{key}]({api}/health/badge.svg)]({cfg.status_page})")
        lines.append(f"[![{key} uptime 7d]({api}/uptimes/7d/badge.svg)]({cfg.status_page})")
    for b in cfg.json_badges:
        q = urlencode({"url": b["url"], "query": b["query"], "label": b["label"]}, quote_via=quote)
        site = "{0.scheme}://{0.netloc}".format(urlsplit(b["url"]))
        lines.append(f"[![{b['label']}](https://img.shields.io/badge/dynamic/json?{q})]({site})")
    return "\n".join(lines)


BLOCKS = (
    Block(
        "CONTRIBUTING.md",
        "md",
        ("contributing/head.md", "contributing/flow-{flow}.md", "contributing/tail.md"),
        "after-title",
        "# Contributing\n",
    ),
    Block(".github/pull_request_template.md", "md", ("pull_request_template.md",), "top", ""),
    Block(".gitignore", "hash", ("gitignore/common", "gitignore/{language}"), "top", ""),
    Block("README.md", "md", (), "after-title", "", render=badges),
)


def normalise(text: str) -> str:
    return text.replace("\r\n", "\n")


def _walk(node: Traversable, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for child in node.iterdir():
        rel = f"{prefix}{child.name}"
        if child.is_dir():
            out.update(_walk(child, rel + "/"))
        else:
            out[rel] = normalise(child.read_text(encoding="utf-8"))
    return out


def whole_files(cfg: Config) -> dict[str, str]:
    """Repo path -> exact content, for every whole synced file."""
    wanted = _walk(FILES / "common")
    if cfg.language:
        wanted.update(_walk(FILES / cfg.language))
    return dict(sorted(wanted.items()))


def block_text(cfg: Config, block: Block) -> str:
    """The managed block for this repo, markers included, ending in a newline."""
    begin, end = MARKERS[block.style]
    if block.render:
        return f"{begin}\n{block.render(cfg)}\n{end}\n"
    body = []
    for part in block.parts:
        name = part.format(flow=cfg.flow, language=cfg.language or "")
        node = FILES / "blocks" / name
        if name.endswith("/") or not node.is_file():
            continue
        body.append(normalise(node.read_text(encoding="utf-8")))
    return f"{begin}\n{''.join(body).strip(chr(10))}\n{end}\n"


def find_block(text: str) -> str | None:
    m = _BLOCK_RE.search(normalise(text))
    if not m:
        return None
    found = m.group(0)
    return found if found.endswith("\n") else found + "\n"


def put_block(existing: str | None, block: Block, managed: str) -> str:
    if existing is None:
        return f"{block.create}\n{managed}" if block.create else managed
    text = normalise(existing)
    if _BLOCK_RE.search(text):
        return _BLOCK_RE.sub(lambda _: managed, text, count=1)
    if block.placement == "after-title" and text.startswith("# "):
        title, _, rest = text.partition("\n")
        rest = rest.lstrip("\n")
        return f"{title}\n\n{managed}" + (f"\n{rest}" if rest else "")
    return managed + (f"\n{text}" if text.strip() else "")


def claude_md(existing: str | None, repo_name: str) -> str:
    if existing is None:
        return (
            f"{CLAUDE_IMPORT}\n\n# {repo_name}\n\n"
            "Repo-specific notes for Claude go here; the shared rules come from the import above.\n"
        )
    text = normalise(existing)
    if has_claude_import(text):
        return text
    return f"{CLAUDE_IMPORT}\n\n{text}"


def has_claude_import(text: str) -> bool:
    first = next((line.strip() for line in normalise(text).splitlines() if line.strip()), "")
    return first == CLAUDE_IMPORT


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def is_git_repo(root: Path) -> bool:
    return _git(root, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def sync(root: Path, cfg: Config) -> list[str]:
    """Write every synced file and block. Returns what changed, as human-readable lines."""
    changes: list[str] = []

    def write(rel: str, content: str) -> None:
        path = root / rel
        old = normalise(path.read_text(encoding="utf-8")) if path.is_file() else None
        if old == content:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
        changes.append(f"{'updated' if old is not None else 'created'} {rel}")

    wanted = whole_files(cfg)
    for rel, content in wanted.items():
        write(rel, content)

    conv = root / CONVENTIONS_DIR
    if conv.is_dir():
        for path in sorted(conv.rglob("*")):
            rel = path.relative_to(root).as_posix()
            if path.is_file() and rel not in wanted:
                path.unlink()
                changes.append(f"removed {rel} (no longer synced)")

    for block in BLOCKS:
        path = root / block.path
        existing = path.read_text(encoding="utf-8") if path.is_file() else None
        write(block.path, put_block(existing, block, block_text(cfg, block)))

    claude = root / "CLAUDE.md"
    write("CLAUDE.md", claude_md(claude.read_text(encoding="utf-8") if claude.is_file() else None, root.name))

    hooks = sorted(rel for rel in wanted if rel.startswith(HOOKS_DIR + "/"))
    for rel in hooks:
        (root / rel).chmod(0o755)
    if is_git_repo(root):
        # Windows has no executable bit to carry, so record it in the index, where CI reads it from.
        _git(root, "add", "--chmod=+x", *hooks)
        if _git(root, "config", "core.hooksPath").stdout.strip() != HOOKS_DIR:
            _git(root, "config", "core.hooksPath", HOOKS_DIR)
            changes.append(f"set git config core.hooksPath {HOOKS_DIR}")
    return changes
