# conventions

The shared conventions for the vexouLuna repos, and the tooling that keeps every repo on them. Each job
has one name and one way of being done. A conventions change reaches every repo as a pull request, and
CI fails when a repo drifts.

A rule written only in a document still drifts. So everything here is also code that is copied into the
repos and checked:

| What | How it reaches a repo | What catches drift |
|---|---|---|
| Shared Claude rules, hooks, `.gitattributes`, ruff config, `.nvmrc` | copied whole by `conventions sync` | `conventions / check` |
| `CONTRIBUTING.md`, PR template and `.gitignore` sections | a managed block inside the repo's own file | `conventions / check` |
| CI: lint, types, tests, image and site publishing | reusable workflows, pinned to a tag | `conventions / check` (the pin) |
| The repo's own version, and releases | `conventions version`, `release.yml` in a `flow = "dev"` repo | `conventions / version` |
| Merge methods, branch protection, required checks, the `v*` tag ruleset | `conventions repo-settings --apply` | `conventions repo-settings --check` |
| New versions | the Renovate preset opens a PR | — |

## Adopting it

From the repo's root, on a Conventional Branch (`chore/adopt-conventions`):

```sh
uvx --from git+https://github.com/vexouLuna/conventions@v3.2.0 conventions init --profile python-service
```

This writes `.conventions.toml` and then syncs. Then:

1. Write `.github/workflows/ci.yml` with the jobs your profile needs (see [Profiles](#profiles)). The
   reusable workflows' headers show how to call each one.
2. Fix what `conventions check` reports, and commit.
3. After the pull request merges, run `conventions repo-settings --apply`. The required checks are named
   after the jobs, so protection has to follow the new names.
4. In `renovate.json`, extend the shared preset: `{"extends": ["github>vexouLuna/conventions//renovate/default"]}`.
   It pins the actions in your workflows to commit SHAs and their service images to digests, and keeps
   them current. The reusable workflows here are pinned the same way.

## `.conventions.toml`

```toml
version = "v3.0.0"          # the release this repo follows; every workflow `uses: …@` must match it
profile = "python-service"  # see Profiles
flow = "trunk"              # "trunk": work merges into main. "dev": work integrates on dev, main is production
extra_checks = []           # required checks beyond the profile's, e.g. a repo-specific CI job
extra_workflows = []        # workflow files beyond ci.yml and publish.yml
```

## Profiles

Every repo's `ci.yml` calls `conventions-check.yml` as job `conventions`. The other jobs depend on the
profile. Job ids are fixed because GitHub names a reusable workflow's checks `<job id> / <job name>`,
which keeps the required check names the same in every repo.

| Profile | Jobs in `ci.yml` (and `publish.yml`) | Required checks |
|---|---|---|
Every repo's required checks start with `conventions / branch-name`, `conventions / check` and
`conventions / version`; the profile adds:

| Profile | Jobs in `ci.yml` (and `publish.yml`) | Required checks it adds |
|---|---|---|
| `python-service` | `conventions`, `ci` → `python-ci.yml`, `image` → `publish-image.yml` | `ci / lint`, `ci / test`, `image / build` |
| `python-lib` | `conventions`, `ci` → `python-ci.yml` | `ci / lint`, `ci / test` |
| `node-lib` | `conventions`, `ci` → `node-ci.yml` | `ci / lint`, `ci / test`, `ci / build` |
| `site` | as `node-lib`, plus `publish` → `publish-site.yml` in `publish.yml` | as `node-lib` |
| `docs` | `conventions` | — |

A `flow = "dev"` repo also has `release.yml`, with job `prepare` → `release-prepare.yml` and job
`finish` → `release-finish.yml` (see [Releasing a flow = "dev" repo](#releasing-a-flow--dev-repo)).

`conventions required-checks` prints the list for a repo.

## What `conventions check` enforces

- Every synced file and managed block matches the pinned version. Line endings don't count.
- `CLAUDE.md` starts with `@.conventions/CLAUDE.md`.
- The hooks are executable in git.
- Workflows are only `ci.yml` and `publish.yml` (plus `release.yml` in a `flow = "dev"` repo, and
  `extra_workflows`), with the profile's jobs, each pinned to the same version as `.conventions.toml`.
- No workflow passes `secrets: inherit`. A job that calls a reusable workflow passes only the secrets it
  declares, e.g. `CONVENTIONS_PRIVATE_PATTERNS: ${{ secrets.CONVENTIONS_PRIVATE_PATTERNS }}` to
  `conventions-check.yml` and `RELEASE_TOKEN: ${{ secrets.RELEASE_TOKEN }}` to the release workflows.
- Compose files are only `compose.yaml` and `compose.dev.yaml`, at the root. One `Dockerfile`, at the
  root. Nothing in `.githooks/` except `.githooks/local/`.
- Every changeset in `.changeset/` (when there is one) opens with a `---` header of
  `"<pkg>": major|minor|patch|none` lines, so a broken one fails the PR instead of `changeset version`.
- **Python:**
  - `.python-version` is 3.13, and `uv.lock` exists.
  - `requires-python >= 3.13`.
  - ruff extends `.conventions/ruff.toml` without overriding it.
  - mypy is `strict`. mypy can't extend a shared file, so this checks the settings instead.
- **Node:** `package-lock.json` exists, and `engines.node` names 22.
- **With `--public`** (CI adds it in public repos): no private IP addresses, home directories or server
  paths.
  - More patterns, such as machine names, come from the `CONVENTIONS_PRIVATE_PATTERNS` repository
    secret, one regular expression per line, so the patterns themselves stay private.
  - A line that legitimately needs one carries `conventions:allow-infra`.

## `conventions version`

The `conventions / version` job runs `conventions version check`. The same command works locally:

```sh
conventions version show            # the repo's version (nothing if it has none)
conventions version check           # every file that carries it agrees
conventions version check --against origin/main   # ...and it is a release: above main's, tag unused
conventions version next            # what the next release should be, from the commits since the last v* tag
conventions version set 1.4.0       # write it everywhere
```

The version lives in the root `pyproject.toml` (`[project] version`), the project's own block in
`uv.lock`, `__version__` in `src/<module>/__init__.py` or `<module>/__init__.py`, and `package.json`
with `package-lock.json`. Workspace members keep their own versions. A repo without any passes.

`next` reads the Conventional Commits since the last `v*` tag: `!` or a `BREAKING CHANGE:` footer is a
major (a minor before 1.0.0), `feat` a minor, anything else a patch. A version already raised by hand
since the tag wins.

In CI the job checks agreement on every run. On a pull request into `main` it adds `--against
origin/main`, which in a `flow = "dev"` repo fails unless the version went up and its tag doesn't exist.
A trunk repo gets only a note.

## Releasing a flow = "dev" repo

A `flow = "dev"` repo adds `.github/workflows/release.yml`; [`release-finish.yml`](.github/workflows/release-finish.yml)
has the whole file to copy. Then:

1. **Actions → release → Run workflow**, from `dev`, with `bump` left at `auto` or set by hand.
   `prepare` works out the version, commits it on `release/X-Y-Z` and opens "Release vX.Y.Z" into
   `main`, listing the pull requests it carries.
2. Review and merge it (a merge commit).
3. `finish` tags the merge commit `vX.Y.Z` and publishes the GitHub release with generated notes, which
   runs the tag's CI (the `:vX.Y.Z` image). It then opens "Bring release vX.Y.Z back into dev".
4. Merge that.

A `hotfix/*` into `main` sets its version with `conventions version set` and is finished the same way.

**`RELEASE_TOKEN`.** Branches, pull requests and tags created with the workflow's own `GITHUB_TOKEN`
don't start other workflows, so neither the release pull request's CI nor the tag's image would run.
Both workflows therefore use a repository secret `RELEASE_TOKEN`: a fine-grained personal access token
(or a GitHub App token) for the repo with **Contents: read and write** and **Pull requests: read and
write**. They fail at once, saying so, when it is missing. Only the steps that push or call `gh` see
it: the checkout keeps no credentials, and the conventions CLI runs in steps without a token.

**Release tags can't move.** `conventions repo-settings --apply` puts a ruleset on `v*` tags in every
repo: anyone who may push can create one (that is how `finish` releases), nobody can update or delete one,
admins included. Every repo's `uses:` pins and `.conventions.toml` resolve those tags, so a moved tag
would change CI everywhere.

## Releasing this repo

1. Change things on a branch here. CI runs this repo's own check, from the checkout, against itself.
2. Bump the version (`uv run conventions version set X.Y.Z`) and the `@vX.Y.Z` examples in the
   workflow headers and this README, then merge.
3. Tag the merge commit `vX.Y.Z` and push the tag.
4. Renovate opens a PR in each repo that bumps the pin and the `uses:` refs together. That PR fails
   `conventions / check` until someone runs `conventions sync` on its branch and pushes the result. The
   failure is intended: it is how the new files reach the repo.

Semver: a change that makes a passing repo fail is a major version, a new optional input or rule a
repo can opt into is a minor, and a fix is a patch.

## Developing

```sh
uv sync --all-extras
uv run pytest
uv run ruff check && uv run ruff format --check && uv run mypy
uv run conventions check --public
```
