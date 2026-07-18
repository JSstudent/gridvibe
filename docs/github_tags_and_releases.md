# GitHub Tags and Releases for GridVibe

This guide explains the relationship between Git tags, GitHub Releases, and
GridVibe's own version number. It also documents the release process that fits
the repository's current packaging, CI, and self-update behavior.

## Tags and releases are different things

A Git tag is a durable name pointing to one exact commit:

```text
commit 633adce
      ^
   v1.2.0
```

A GitHub Release is a presentation and distribution layer built around that
tag:

```text
release commit -> tag v1.2.0 -> GitHub Release "GridVibe 1.2.0"
                                      |-- release notes
                                      |-- source ZIP/tar.gz
                                      `-- optional installers/assets
```

For GridVibe, the related identifiers should look like this:

| Item | Example | Purpose |
|---|---|---|
| Application version | `1.2.0` | Displayed by GridVibe and stored in package metadata |
| Git tag | `v1.2.0` | Permanently identifies the released commit |
| Release title | `GridVibe 1.2.0` | Human-readable title on GitHub |
| Release notes | Highlights and upgrade warnings | Explain what changed |
| Release assets | Source archive or a future installer | Files users download |

GitHub automatically offers ZIP and tar.gz archives containing the repository
at the tagged commit. A release can also contain manually uploaded binaries or
installers. Pushing a tag does not by itself publish a GitHub Release, although
GitHub's release form can create the tag and release together.

Useful GitHub documentation:

- [About releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
- [Managing releases](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)
- [Automatically generated release notes](https://docs.github.com/en/repositories/releasing-projects-on-github/automatically-generated-release-notes)

### Drafts, prereleases, and latest releases

- A **draft release** is a private preparation area that is not yet public.
- A **prerelease** marks a beta, release candidate, or otherwise unstable
  version.
- The **Latest** release normally identifies the current stable version users
  should download.

## GridVibe's current release setup

As of 2026-07-18, GridVibe has no remote Git tags and no GitHub Releases. The
repository nevertheless already contains most of the source-level release
conventions:

- `pyproject.toml` and `gridvibe_version.py` both contain version `1.1.0`.
- `CHANGELOG.md` has an `Unreleased` section and historical `1.1.0` and `0.1.0`
  sections.
- `CONTRIBUTING.md` requires those three files to stay synchronized and
  specifies tags in the form `v0.1.0`.
- `START_HERE/README.md` is explicitly intended to make the Windows launcher
  easy to find in GitHub release archives.

The current GitHub Actions workflow runs for pushes and pull requests targeting
`main`. It does not run specifically for tag pushes or published releases.
Publishing a release today would therefore create release notes and source
downloads, but it would not automatically build or attach an installer, wheel,
or other artifact.

## Choosing the next version

The application version is currently `1.1.0`. Given the amount of new
functionality in `CHANGELOG.md` under `Unreleased`, the practical next version
would likely be:

```text
Application version: 1.2.0
Tag:                 v1.2.0
Release title:       GridVibe 1.2.0
```

The unreleased changelog also documents a breaking configuration change for
reverse-proxy installations. If strict semantic-version compatibility is
required, that could justify `2.0.0`. If the change is treated as an operational
migration during GridVibe's alpha stage, `1.2.0` is reasonable as long as the
warning is prominent in the release notes.

A patch version such as `1.1.1` would not accurately represent the number of
new features.

## Recommended release process

### 1. Prepare the release on `main`

Merge all intended work into `main`. Do not tag an arbitrary feature branch.

Update both version sources:

```toml
# pyproject.toml
version = "1.2.0"
```

```python
# gridvibe_version.py
__version__ = "1.2.0"
```

In `CHANGELOG.md`, change the existing heading:

```markdown
## Unreleased
```

to a dated release heading and add a new empty `Unreleased` section above it:

```markdown
## Unreleased

## 1.2.0 - 2026-07-18
```

### 2. Run the release checks

On Windows without `make`:

```powershell
python tests/run_tests.py
python -m ruff check .
```

Alternatively:

```powershell
make check
```

Do not publish a release until the local checks and the GitHub Actions run for
the release commit succeed.

### 3. Commit and push the release state

```powershell
git switch main
git pull --ff-only
git add pyproject.toml gridvibe_version.py CHANGELOG.md
git commit -m "Release 1.2.0"
git push origin main
```

Wait for CI on `main` to pass before creating the tag.

### 4. Tag the exact release commit

An annotated tag records a tag message and is preferable for an intentional
release marker:

```powershell
git tag -a v1.2.0 -m "GridVibe 1.2.0"
git push origin v1.2.0
```

Treat a published release tag as immutable. If a problem is found after
publication, fix it in a new commit and publish a new patch version such as
`v1.2.1` instead of moving `v1.2.0`.

### 5. Publish the GitHub Release

In the GitHub web interface:

1. Open **Releases**.
2. Select **Draft a new release**.
3. Choose the existing `v1.2.0` tag.
4. Use `GridVibe 1.2.0` as the release title.
5. Add curated highlights, fixes, upgrade warnings, and installation notes.
6. Attach any separately built and verified assets, if applicable.
7. Save as a draft for review or publish it.

The equivalent GitHub CLI command can be used once release notes have been
prepared in a file:

```powershell
gh release create v1.2.0 `
  --title "GridVibe 1.2.0" `
  --notes-file RELEASE_NOTES.md
```

`RELEASE_NOTES.md` in that example can be a temporary local file; it does not
need to be committed.

## Release notes for the first tagged release

Because the repository currently has no earlier tag, GitHub's first generated
release notes may cover the entire repository history. For the first tagged
release, manually curate the notes from `CHANGELOG.md` instead of publishing a
large unreviewed generated list.

After `v1.2.0` exists, future generated release notes can compare against it and
will produce a more useful list of merged pull requests, contributors, and the
full comparison link.

Do not invent or backfill historical tags unless the exact commits representing
those releases have been identified confidently.

## What users download today

The normal GridVibe release download is currently GitHub's generated source ZIP
or tar.gz archive. That is workable because the repository contains:

- `GridVibe.bat` and `START_HERE/Start GridVibe.bat` for Windows.
- `GridVibe.sh` for Linux.
- Dependency files used by the launchers to create or repair the virtual
  environment.

There is no current packaged `.exe`, Windows installer, or validated Python
wheel release process. Although `pyproject.toml` contains project metadata, a
release should not be advertised as a PyPI-installable desktop application
until packaging, static assets, entry points, and artifact tests are wired up.

## Important self-update limitation

GridVibe's self-update flow is branch-based, not release-based. It requires a
real Git checkout with:

- a checked-out branch rather than detached `HEAD`;
- a clean working tree;
- a configured upstream branch; and
- the ability to fetch and fast-forward with `git pull --ff-only`.

Consequently:

- Users who clone the repository can use GridVibe's self-update mechanism.
- Users who download and extract a GitHub release ZIP cannot use it because the
  archive does not contain the `.git` checkout metadata.
- Tags do not currently select or gate updates. A cloned installation follows
  its configured upstream branch, normally `main`.

If GridVibe should eventually offer a stable-release update channel, the updater
will need release-aware behavior rather than only following the latest commit on
the tracked branch.

## Possible future automation

A dedicated release workflow could later react to a published GitHub Release:

```yaml
on:
  release:
    types: [published]
```

That workflow could validate the tag/version match, run tests, build platform
artifacts, generate checksums, and attach verified files to the release. Until
such a workflow exists, those steps are manual and publishing a release only
provides GitHub's generated source archives plus any assets uploaded by the
maintainer.
