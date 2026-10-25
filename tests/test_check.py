from __future__ import annotations

from pathlib import Path

import pytest

from conventions import __version__, check, config, synced
from conventions.cli import main

from .conftest import git

V = f"v{__version__}"


def problems(root: Path, public: bool = False) -> list[str]:
    return check.run(root, config.load(root), public=public)


def test_synced_repo_passes(repo: Path) -> None:
    assert problems(repo, public=True) == []
    assert main(["-C", str(repo), "check", "--public"]) == 0


def test_sync_is_idempotent(repo: Path) -> None:
    assert synced.sync(repo, config.load(repo)) == []


def test_edited_synced_file_fails(repo: Path) -> None:
    path = repo / ".conventions/CLAUDE.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nAn extra rule.\n", encoding="utf-8")
    assert problems(repo) == [
        f".conventions/CLAUDE.md differs from conventions {V}; "
        "run `conventions sync` at the pinned version and commit the result"
    ]
    assert main(["-C", str(repo), "check"]) == 1


def test_sync_repairs_an_edited_file(repo: Path) -> None:
    (repo / ".gitattributes").write_text("* text=auto\n", encoding="utf-8")
    assert synced.sync(repo, config.load(repo)) == ["updated .gitattributes"]
    assert problems(repo) == []


def test_crlf_checkout_still_matches(repo: Path) -> None:
    path = repo / ".conventions/CLAUDE.md"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert problems(repo) == []


def test_missing_and_stray_files_fail(repo: Path) -> None:
    (repo / ".conventions/ruff.toml").unlink()
    (repo / ".conventions/notes.md").write_text("mine\n", encoding="utf-8")
    found = problems(repo)
    assert any(p.startswith(".conventions/ruff.toml is missing") for p in found)
    assert any(p.startswith(".conventions/notes.md is not a synced file") for p in found)


def test_version_mismatch_fails(repo: Path) -> None:
    toml = repo / config.CONFIG_NAME
    toml.write_text(toml.read_text(encoding="utf-8").replace(f'"{V}"', '"v0.9.0"'), encoding="utf-8")
    found = problems(repo)
    assert any(f"pins v0.9.0 but this is conventions {V}" in p for p in found)
    assert any(f"uses conventions-check.yml@{V}, but .conventions.toml pins v0.9.0" in p for p in found)


# --- managed blocks ----------------------------------------------------------------------------------


def test_block_keeps_repo_content_around_it(repo: Path) -> None:
    path = repo / "CONTRIBUTING.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n## Local setup\n\nRun it.\n", encoding="utf-8")
    assert problems(repo) == []
    assert synced.sync(repo, config.load(repo)) == []
    assert path.read_text(encoding="utf-8").endswith("## Local setup\n\nRun it.\n")


def test_edited_block_fails(repo: Path) -> None:
    path = repo / ".gitignore"
    path.write_text(path.read_text(encoding="utf-8").replace("# conventions:end", "*.log\n# conventions:end"))
    assert problems(repo) == [
        f".gitignore: the conventions block differs from {V}; "
        "run `conventions sync` at the pinned version and commit the result"
    ]


def test_block_inserted_below_existing_title(tmp_path: Path) -> None:
    block = synced.BLOCKS[0]
    managed = "<!-- conventions:begin -->\nX\n<!-- conventions:end -->\n"
    out = synced.put_block("# My project\n\nIntro.\n", block, managed)
    assert out == f"# My project\n\n{managed}\nIntro.\n"


def test_dev_flow_block_differs(repo: Path) -> None:
    trunk = config.load(repo)
    dev = config.Config(version=trunk.version, profile=trunk.profile, flow="dev")
    block = synced.BLOCKS[0]
    assert synced.block_text(trunk, block) != synced.block_text(dev, block)
    assert dev.protected_branches == ["main", "dev"]


# --- CLAUDE.md ---------------------------------------------------------------------------------------


def test_claude_md_import_is_first(repo: Path) -> None:
    path = repo / "CLAUDE.md"
    assert path.read_text(encoding="utf-8").startswith("@.conventions/CLAUDE.md\n")
    path.write_text("# Notes\n\n@.conventions/CLAUDE.md\n", encoding="utf-8")
    assert problems(repo) == ["CLAUDE.md must start with `@.conventions/CLAUDE.md`"]


def test_existing_claude_md_is_prepended(tmp_path: Path) -> None:
    assert synced.claude_md("# Mine\n", "x") == "@.conventions/CLAUDE.md\n\n# Mine\n"


# --- hooks -------------------------------------------------------------------------------------------


def test_hooks_are_executable_in_git(repo: Path) -> None:
    hook = ".conventions/githooks/pre-commit"
    assert git(repo, "ls-files", "-s", hook).split()[0] == "100755"
    git(repo, "update-index", "--chmod=-x", hook)
    assert problems(repo) == [f"{hook} is not executable in git; `git add --chmod=+x {hook}`"]


def test_hooks_path_is_set(repo: Path) -> None:
    assert git(repo, "config", "core.hooksPath").strip() == ".conventions/githooks"


# --- workflows and layout ----------------------------------------------------------------------------


def test_missing_ci_job_fails(repo: Path) -> None:
    ci = repo / ".github/workflows/ci.yml"
    ci.write_text(ci.read_text(encoding="utf-8").replace("  ci:\n", "  tests:\n"), encoding="utf-8")
    found = problems(repo)
    assert any("needs a job `ci` that uses vexouLuna/conventions/.github/workflows/python-ci.yml" in p for p in found)


@pytest.mark.parametrize("owner", ["vEXOULZ", "vexouluna", "VEXOULZ"])
def test_either_owner_passes(repo: Path, owner: str) -> None:
    # The owner was renamed from vEXOULZ to vexouLuna; GitHub matches owners case-insensitively.
    ci = repo / ".github/workflows/ci.yml"
    ci.write_text(ci.read_text(encoding="utf-8").replace("vexouLuna/", f"{owner}/"), encoding="utf-8")
    assert problems(repo) == []


def test_another_owner_fails(repo: Path) -> None:
    ci = repo / ".github/workflows/ci.yml"
    ci.write_text(ci.read_text(encoding="utf-8").replace("vexouLuna/", "someone/"), encoding="utf-8")
    assert any("needs a job `ci`" in p for p in problems(repo))


def test_secrets_inherit_fails(repo: Path) -> None:
    ci = repo / ".github/workflows/ci.yml"
    text = ci.read_text(encoding="utf-8")
    explicit = "    secrets:\n      CONVENTIONS_PRIVATE_PATTERNS: ${{ secrets.CONVENTIONS_PRIVATE_PATTERNS }}\n"
    ci.write_text(text.replace(explicit, "    secrets: inherit  # every secret\n"), encoding="utf-8")
    assert [p for p in problems(repo) if "secrets: inherit" in p] == [
        ".github/workflows/ci.yml:9: `secrets: inherit` passes every repository secret; "
        "pass only the ones the reusable workflow declares, e.g. "
        "`CONVENTIONS_PRIVATE_PATTERNS: ${{ secrets.CONVENTIONS_PRIVATE_PATTERNS }}`"
    ]
    # A comment that mentions it is fine.
    ci.write_text(text.replace("jobs:\n", "# secrets: inherit\njobs:\n"), encoding="utf-8")
    assert problems(repo) == []


def test_stray_workflow_fails_unless_listed(repo: Path) -> None:
    (repo / ".github/workflows/tests.yml").write_text("name: tests\n", encoding="utf-8")
    assert any(p.startswith(".github/workflows/tests.yml") for p in problems(repo))
    toml = repo / config.CONFIG_NAME
    toml.write_text(toml.read_text(encoding="utf-8") + 'extra_workflows = ["tests.yml"]\n', encoding="utf-8")
    assert problems(repo) == []


@pytest.mark.parametrize(
    "rel",
    ["docker-compose.yml", "compose.override.yaml", "deploy/compose.yaml", "docker/Dockerfile", ".githooks/pre-push"],
)
def test_layout(repo: Path, rel: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n", encoding="utf-8")
    assert [p for p in problems(repo) if p.startswith(rel)]


def test_python_settings(repo: Path) -> None:
    py = repo / "pyproject.toml"
    py.write_text(
        py.read_text(encoding="utf-8").replace(">=3.13", ">=3.12").replace("strict = true", "strict = false")
        + "line-length = 100\n",
        encoding="utf-8",
    )
    found = problems(repo)
    assert any("requires-python" in p for p in found)
    assert any("strict = true" in p for p in found)


def test_required_checks() -> None:
    cfg = config.Config(version="v1.0.0", profile="python-service", extra_checks=["web-editor"])
    assert cfg.required_checks == [
        "conventions / branch-name",
        "conventions / check",
        "conventions / version",
        "ci / lint",
        "ci / test",
        "image / build",
        "web-editor",
    ]


GOOD_CHANGESETS = [
    "---\n'@vex/vods-core': patch\n---\n\nFix the timeline.\n",
    '---\n"@vex/ui": minor\n"@vex/vods-core": major\n---\n\nTwo packages.\n',
    "---\r\nvex-root: patch\r\n---\r\n\r\nCRLF checkout, unquoted name.\r\n",
    "---\n---\n\nAn empty changeset releases nothing.\n",
    "\n---\n'@vex/vods-core': none\n---\n\nLeading blank line, no release.\n",
]
BAD_CHANGESETS = [
    "Compiled VODs keep their parts.\n",  # the vods-core d2f37da case: the header was overwritten
    "---\n'@vex/vods-core': patch\n\nNo closing line.\n",
    "---\n'@vex/vods-core': bugfix\n---\n\nUnknown bump.\n",
    "---\n'@vex/vods-core' patch\n---\n\nNo colon.\n",
    "Above the header.\n---\n'@vex/vods-core': patch\n---\n\nText before the header.\n",
    "",
]


@pytest.mark.parametrize("text", GOOD_CHANGESETS)
def test_good_changeset_passes(repo: Path, text: str) -> None:
    (repo / ".changeset").mkdir()
    (repo / ".changeset/README.md").write_text("# Changesets\n\nNo header here.\n", encoding="utf-8")
    (repo / ".changeset/good.md").write_bytes(text.encode())
    assert problems(repo) == []


@pytest.mark.parametrize("text", BAD_CHANGESETS)
def test_broken_changeset_fails(repo: Path, text: str) -> None:
    (repo / ".changeset").mkdir()
    (repo / ".changeset/broken.md").write_text(text, encoding="utf-8")
    found = problems(repo)
    assert len(found) == 1 and found[0].startswith(".changeset/broken.md: ")
    assert main(["-C", str(repo), "check"]) == 1


# --- private infrastructure --------------------------------------------------------------------------

# Built by concatenation, so this file doesn't trip the check it tests.
PRIVATE_IP = "192." + "168.1.20"
HOME = "/home/" + "vex/stacks"


@pytest.mark.parametrize("line", [f"host = {PRIVATE_IP}", f"cd {HOME}", "root /s" + "rv/www/site;"])
def test_private_infra_found_in_public_repo(repo: Path, line: str) -> None:
    (repo / "notes.md").write_text(f"one\n{line}\n", encoding="utf-8")
    found = problems(repo, public=True)
    assert len(found) == 1 and found[0].startswith("notes.md:2: ")
    assert problems(repo, public=False) == []


@pytest.mark.parametrize("line", ["version 10.2.3.4.5", "see https://example.com/srv/", "1.192.168.1.1"])
def test_private_infra_ignores_lookalikes(repo: Path, line: str) -> None:
    (repo / "notes.md").write_text(f"{line}\n", encoding="utf-8")
    assert problems(repo, public=True) == []


def test_private_infra_allow_marker(repo: Path) -> None:
    (repo / "notes.md").write_text(f"{PRIVATE_IP}  # conventions:allow-infra\n", encoding="utf-8")
    assert problems(repo, public=True) == []


def test_private_patterns_from_env(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "notes.md").write_text("ssh into secret-box-01 now\n", encoding="utf-8")
    monkeypatch.setenv(check.PRIVATE_PATTERNS_ENV, r"secret-box-\d+")
    found = problems(repo, public=True)
    assert found == [f"notes.md:1: matches a private pattern ({check.PRIVATE_PATTERNS_ENV}) in a public repo"]
    assert "secret-box" not in found[0]
