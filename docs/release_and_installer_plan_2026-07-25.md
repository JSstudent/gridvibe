# GridVibe — Tagging, Releasing, and (later) Installing

**Single implementation plan. Supersedes `docs/github_tags_and_releases.md` (2026-07-18) and
`docs/installer_and_release_plan_2026-07-20.md`.**

Date: 2026-07-25 · Repo: `https://github.com/JSstudent/gridvibe` · Current version: `1.1.0` ·
Current tags: **none**

---

## 0. How to read this document

This plan is deliberately split into **two parts, separated by however long you want**.

> **Part 1 — `v1.2.0`: your first tag and release.** No packaging, no freezing, no installer.
> Ship the repository exactly as it runs today, tagged and released properly. Roughly **one day**
> of work, and everything in it is reversible.
>
> **Part 2 — `v2.0.0`: the installer.** PyInstaller, Inno Setup, the user-data split, and in-app
> updating for installed builds. Roughly **5–7 days**, and it changes where user state lives —
> which is exactly why it is a major version and why it is not bundled into your first tag.

Between them sits ordinary work: **1.3.0, 1.4.0, and so on** — normal feature releases cut with the
same procedure Part 1 establishes. That is the point of the split. By the time you start Part 2 you
will have cut several releases and the mechanics will be routine, so the only genuinely new thing in
Part 2 is the packaging itself.

**Section 5 is the everyday Git workflow** — clone, branch, commit, push, merge — written for the
loop you will be in between releases.

Every implementation step has the same shape:

> **Why** — the problem it solves.
> **Change** — the concrete files and code.
> **Verify** — how you know it worked.
> **Git** — the branch/commit/tag procedure, where a step involves one.

Nothing here instructs an agent to commit, tag, or push on your behalf — per `CLAUDE.md`, Git
history changes are yours to make. The Git steps are written so you can run them.

### Reading order

| You want to… | Read |
|---|---|
| Cut your first tag this week | §1, §3 (D1–D3), §4, §5, §6 |
| Get comfortable with Git and releases | §5, §6 |
| Understand what the installer will involve, later | §2.2, §3 (D4–D11), §7 |
| Know what is reversible | §11 |

---

## 1. Where the repo actually stands today

The two superseded documents were written against an older tree. Several of their blockers are
already fixed, and several new ones exist. This section is the verified baseline as of
2026-07-25, and it is what the rest of the plan is built on.

### 1.1 Verified facts

| Area | Verified state today | Where it matters |
|---|---|---|
| Version | `1.1.0` in **both** `pyproject.toml` (literal) and `gridvibe_version.py` | Part 1, Stage A |
| Version in UI | `web/api.py` passes `version=__version__` into both templates and returns it from `GET /api/health`. There is **no** `/api/version` route | Part 1 — do **not** add one, see §1.2 |
| Tags / Releases | Zero tags locally, zero on `origin`. `origin/main` = `f965026` (merge of PR #34) | Part 1 — 1.2.0 is the *first* tag; release notes must be curated, not auto-generated |
| CI | `.github/workflows/ci.yml` — ruff + `tests/run_tests.py` on push/PR to `main`, matrix ubuntu+windows × py3.10/3.11/3.12. No tag or release trigger | Part 1 uses it as-is; Part 2 adds a second workflow |
| Frontend assets | **Already vendored** — `web/static/vendor/` holds pinned xterm, xterm addons, socket.io, mermaid, highlight.js, diff2html. No CDN references remain in `templates/` | Part 2 — the old plan's "vendor the CDN assets" milestone is **done** |
| Frontend structure | `terminals.js` already split into `terminal-icons.js`, `voice-input.js`, `explorer-viewer.js`, `explorer-editor.js`; `tokens.css` exists | Both parts — new UI goes in a domain file (guardrail 6) |
| Restart machinery | `_build_restart_command()` in `web/webview_launcher.py` **already has a frozen branch**. The Windows `.venv` branch tests `is_file()` first, so an installed build correctly falls through to it | Part 2, Step C4 |
| Self-update | `web/selfupdate.py` → `git fetch` + `git pull --ff-only` on `BASE_DIR`, exposed at `POST /api/app-update`, driven from `web/static/js/launcher.js:2536` | Part 1, Step A2 — it fails with an unhelpful message for source-ZIP users, and Part 1 is what creates the first source ZIP |
| Writable state | `config.json`, `saved_sessions.json`, `runtime_state.json`, `.encryption_key`, `.known_hosts` all resolve to `BASE_DIR`; `logs/` resolves to `main.py`'s own directory | **Part 2 only.** Fine as-is for a source release |
| Config path split | `main.py` calls `load_config(args.config)` with a **CWD-relative** default `"config.json"`, while `web/config.py` defaults to `BASE_DIR/config.json` | Part 2, Step B3. They agree today only because the launchers `cd` first |
| Read-only bundled assets | `templates/`, `web/static/`, `default_config.json`, `agent_registry.json`, `docs/images/` (served live by `GET /docs/images/<file>`) | Part 2, Step D2 |
| Icon | `docs/images/GridVibe_icon.ico` exists; `_resolve_icon_path()` derives it from `Path(__file__).parent.parent` | Part 2, Step C1 |
| Vosk service | `web/voice.py:_ensure_vosk_service()` spawns `[sys.executable, "services/vosk_service.py"]` | Part 2, Step C2 — breaks when frozen |
| Whisper models | `WhisperModel(model, device, compute_type)` — no `download_root`, so models land in the user's Hugging Face cache | Part 2, Step B4 |
| Voice deps | `requirements-voice.txt` = `vosk`, `websockets`, `faster-whisper`, `numpy` — transitively `ctranslate2`, `onnxruntime`, `av`, `tokenizers`, `huggingface-hub` (hundreds of MB) | Part 2 — drives the two-edition decision |
| Windows PTY | `pywinpty` is imported by `web/terminal_io.py` for local Windows terminals, but lives in **`requirements-desktop.txt`**, not `requirements.txt` | Part 2, Step D1 |
| WebSocket transport | `SocketIO(..., async_mode="threading")`. `simple-websocket` + `wsproto` are installed only as **transitive** deps of `python-engineio` and are **not pinned** anywhere | Part 2, Step C5 |
| Python version | `.python-version` says `3.10`; the dev venv is `3.11.9`; `GridVibe.bat` prefers `py -3.12`; CI matrix tops out at `3.12` | Part 2, Step D1 |
| Launchers | `GridVibe.bat` bootstraps `.venv`, verifies native imports, offers Desktop/Browser, offers voice extras. `GridVibe.sh` covers Linux. `START_HERE/Start GridVibe.bat` is a findable shim | Part 1, Stage F — this *is* the 1.2.0 delivery mechanism |
| Doc visibility | `.gitignore` excludes `docs/r&d/`, `docs/archive/`, `docs/release.md` | This document lives at `docs/` (tracked) |

### 1.2 Corrections to the superseded documents

**Already done — do not re-plan these:** vendoring CDN frontend assets; splitting the `terminals.js`
monolith; the frozen branch in `_build_restart_command()`; same-origin CORS defaults and the
cross-origin write guard.

**Actively wrong advice in those documents:**

- `installer_and_release_plan_2026-07-20.md` §4 proposes `GET /api/version`. **Don't.** The version
  already reaches the frontend through the template context and `/api/health`. A third route that
  nothing new consumes is a guardrail-5 ("no dead endpoints") violation. Extend `/api/app-config`
  instead (Step A2).
- The same document proposes making `pyproject.toml`'s version dynamic via
  `[tool.setuptools.dynamic]`. That requires adding a `[build-system]` table and explicit package
  discovery to a flat-layout repo that is not currently pip-installable — real risk for zero release
  benefit. Use a test instead (Step A1).
- `docs/r&d/pyinstaller_windows_x64_plan.md` proposes a **second frozen executable** for the Vosk
  service. A `--vosk-service` argv switch on the *same* executable is simpler and avoids duplicating
  the voice runtime on disk (Step C2).
- Both documents assume `%ProgramFiles%`. Use a **per-user** install instead — no UAC prompt, and
  the auto-updater can replace files without elevation (D7).

### 1.3 Blockers — and which part they belong to

Seven things stand between today's tree and a packaged installer:

1. Writable state lives in the code directory. → Part 2, Stage B
2. Config resolution differs between `main.py` and `web/config.py`. → Part 2, Step B3
3. Whisper models go to an unmanaged cache. → Part 2, Step B4
4. Icon/asset paths assume a source tree. → Part 2, Step C1
5. The Vosk subprocess spawn breaks when frozen. → Part 2, Step C2
6. `simple-websocket` is unpinned and lazily imported. → Part 2, Step C5
7. Self-update has no release-based path. → Part 2, Stage G

**Not one of them blocks Part 1.** Tagging and releasing the source tree requires none of this
work, which is precisely why Part 1 is safe and why it goes first.

---

## 2. The two parts

### 2.1 Part 1 end state — `v1.2.0`

A properly tagged, properly released version of what already works:

```
Releases page → GridVibe 1.2.0
  ├── Source code (zip)      ← GitHub-generated, contains START_HERE\Start GridVibe.bat
  ├── Source code (tar.gz)   ← GitHub-generated
  └── Curated release notes  ← from CHANGELOG.md, breaking change first
```

| Who | How they get it | How they update |
|---|---|---|
| Developers / most users | `git clone`, then `GridVibe.bat` or `GridVibe.sh` | In-app **Check for updates** — existing `git pull --ff-only`, unchanged |
| Users who won't use Git | Release source ZIP → `START_HERE\Start GridVibe.bat` | Download the next release. **The app now says exactly that** instead of "not running from a git checkout" (Step A2) |

Python is still a prerequisite. That is honest and normal for an alpha developer tool, and
`GridVibe.bat` already handles the venv bootstrap well.

### 2.2 Part 2 end state — `v2.0.0`

```
Releases page → GridVibe 2.0.0
  ├── GridVibe-2.0.0-voice-setup.exe   ← recommended (core + offline voice runtime)
  ├── GridVibe-2.0.0-setup.exe         ← core only
  ├── SHA256SUMS
  └── Source code (zip / tar.gz)       ← the development channel continues unchanged
```

Windows users double-click one file. No Python, no `pip`, no venv. User state moves to
`%APPDATA%\GridVibe` so it survives upgrades. The source channel is untouched.

### 2.3 Version roadmap

| Version | What it is | When |
|---|---|---|
| `1.1.0` | Today, untagged | — |
| **`v1.2.0`** | **First tag. Source release. Part 1** | Next |
| `v1.3.0`, `v1.4.0`, … | Normal feature releases, same procedure (§6) | However many you want |
| `v1.x.y` | Patches/hotfixes as needed | As needed |
| **`v2.0.0`** | **Installer release. Part 2** | When you are ready |
| `v2.0.1` | Adds in-app updating for installed builds (D11) | Shortly after 2.0.0 |

There is no minimum or maximum number of releases between 1.2.0 and 2.0.0. Cut as many as you
like — that is the intended way to get comfortable before the packaging work.

---

## 3. Decisions locked in

Each is a real fork in the road, decided here so the steps below are unambiguous. Each says how to
override it.

**D1 — The first tag is `v1.2.0`, and it is a source release.**
`CHANGELOG.md` line 85 documents a genuinely breaking change: Socket.IO CORS now defaults to
same-origin instead of `*`, which breaks reverse-proxy deployments unless `security.cors_origins` is
set. Strict SemVer would say 2.0.0. But `pyproject.toml` classifies the project
`Development Status :: 3 - Alpha`, this is the first tag ever cut, and 2.0.0 as a first tag implies a
1.x history that was never released. Ship `1.2.0` with the migration warning as the first line of the
release notes.

**D2 — The installer release is `2.0.0`.**
Not marketing — it is genuinely breaking. Part 2 moves `config.json`, `saved_sessions.json`,
`runtime_state.json`, `.encryption_key`, and `.known_hosts` out of the repository directory and into
`%APPDATA%\GridVibe` for packaged builds, and introduces a completely different distribution and
update model. Anyone who scripted against the current file locations is affected. A major version is
the correct signal, and it gives the release notes room to explain the migration properly.
*Override:* if Part 2 lands with the data split turning out to be fully transparent in practice,
`1.5.0` would be defensible. Decide when you get there, not now.

**D3 — Releases between 1.2.0 and 2.0.0 are ordinary minors and patches.**
`1.3.0` for features, `1.2.1` for fixes. They use §6's checklist, take about ten minutes each, and
ship source archives only. This is the phase where tagging becomes muscle memory.

---

The remaining decisions apply to **Part 2 only**. They are recorded now so Part 2 starts with the
arguments already settled, but nothing in Part 1 depends on them.

**D4 — Windows x64 only for the 2.0.0 installer.**
`GridVibe.sh` already covers Linux development, and an AppImage carrying QtWebEngine is the single
largest and most fragile piece of the whole packaging problem. Shipping it late is cheap; shipping it
badly is not. Linux stays on the source channel and gets Stage K in a later release.

**D5 — Voice ships as a second installer edition, not a component checkbox.**
Voice is a first-class feature, so it cannot be a "download the runtime later" afterthought — the
frozen voice runtime must be *in* an installer. Two ways to do that:

| Option | One download for everyone | Non-voice users download | Release page |
|---|---|---|---|
| One installer, Inno `Components` checkbox | ✅ | ~500 MB regardless | 1 file |
| **Two editions (chosen)** | ❌ | ~100 MB | 2 files |

Two editions wins because the payload difference is roughly 5×, and because both build from **one**
spec file driven by an environment flag, so there is no duplicated build logic. The voice edition is
marked as the recommended download.
*Override:* if a single file matters more than size, Step E4 notes the `Components:` variant.

**D6 — Speech models are never bundled, in either edition.**
`faster-whisper` `base` is ~145 MB and `vosk-model-en-us-0.22` is ~1.8 GB; both are fetched from
upstream and both are cacheable. The voice edition's installer offers a checkbox — *"Download the
default voice model now (~145 MB)"* — which runs the app's own `--prefetch-voice-model` flag after
install. Declining is fine: the model downloads on first voice use. This keeps model logic in Python
rather than in Pascal installer script.

**D7 — Per-user install, no admin rights.**
`%LOCALAPPDATA%\Programs\GridVibe` with `PrivilegesRequired=lowest`. Consequence: the silent
auto-update in Stage G needs no elevation, which is what makes in-app updating viable at all.

**D8 — Update discovery uses the GitHub Releases API directly. No manifest hosting.**
`GET /repos/JSstudent/gridvibe/releases/latest` plus the `SHA256SUMS` asset is enough for a single
stable channel and needs zero infrastructure.

**D9 — No code signing for 2.0.0.**
An OV/EV Authenticode certificate is a recurring cost. Without it, SmartScreen shows "Windows
protected your PC" on first run until the binary accrues reputation. Document it; revisit when
downloads justify it.

**D10 — Frozen build targets CPython 3.12 on `windows-latest`.**
The repo currently disagrees with itself (`.python-version` 3.10, dev venv 3.11.9, `GridVibe.bat`
prefers 3.12). 3.12 is the newest version CI already tests. Step D1 pins it in one place.

**D11 — In-app updating for installed builds (Stage G) lands in 2.0.1, not 2.0.0.**
Three reasons:

1. **It cannot be honestly tested before an installer release exists.** Verifying it requires two
   published versions *with installer assets* and real checksums. The 1.x releases don't have them,
   so 2.0.0 is the first version this could possibly be tested against.
2. **It is the highest-consequence code in the plan.** It downloads an executable from the network
   and runs it silently. That deserves its own review cycle.
3. **Nothing is missing without it.** Step C3's dispatcher gives installed builds a clear, correct
   answer with a link to the Releases page. Manual installer updates are the norm for desktop apps.

---

# PART 1 — `v1.2.0`: your first tag and release

Five stages, roughly one day, all reversible. No packaging, no new dependencies, no changes to where
anything is stored.

## 4. Part 1 implementation

### Stage 0 — Baseline and branch

#### Step 0.1 — Cut the release-prep branch from current `main`

**Why.** Even a one-day change belongs on a branch, reviewed and merged like anything else. The tag
comes later, from `main`, and only after CI is green there.

**Git.**

```powershell
git switch main
git pull --ff-only
git switch -c release-prep-1.2.0
```

> You are already on `release-prep-1.2.0`, sitting exactly at `origin/main` (`f965026`) with no
> commits on it. Nothing to do — you have a clean starting point.

**Verify.** `git log --oneline -1` matches `origin/main`.

#### Step 0.2 — Mark the two superseded documents

**Why.** Three overlapping plans in `docs/` is how a repo ends up with contradictory instructions.

**Change.** Add a banner to the top of both files:

```markdown
> **Superseded (2026-07-25).** Replaced by
> [`docs/release_and_installer_plan_2026-07-25.md`](release_and_installer_plan_2026-07-25.md).
> Kept for history; several statements below are out of date — see §1.2 of the replacement.
```

Alternatively delete both; Git history keeps them.

**Verify.** `docs/` contains exactly one plan without a superseded banner.

---

### Stage A — Version integrity and an honest update message

Two small changes. The first makes release mistakes impossible; the second makes the release you are
about to publish actually usable by the people who download it.

#### Step A1 — Make version drift a test failure

**Why.** `CONTRIBUTING.md` already requires `pyproject.toml`, `gridvibe_version.py`, and
`CHANGELOG.md` to stay in sync, but nothing enforces it. A tag that disagrees with the version the
app reports is the most confusing release bug there is, and it is trivially preventable. Since this
is your first release and you will be cutting several more, enforcing it now pays for itself
immediately.

**Change.** New `tests/test_version.py` — no new dependency, no build-system changes:

```python
"""Version sources must agree — see docs/release_and_installer_plan_2026-07-25.md §A1."""

import re
import unittest
from pathlib import Path

from gridvibe_version import __version__

BASE_DIR = Path(__file__).resolve().parent.parent


class VersionConsistencyTestCase(unittest.TestCase):
    def test_pyproject_version_matches_gridvibe_version(self):
        text = (BASE_DIR / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        self.assertIsNotNone(match, "pyproject.toml has no literal version")
        self.assertEqual(match.group(1), __version__)

    def test_changelog_documents_the_current_version(self):
        text = (BASE_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertRegex(
            text,
            rf"^## {re.escape(__version__)} - \d{{4}}-\d{{2}}-\d{{2}}$",
            msg="CHANGELOG.md has no dated section for the current version",
        )
```

The second test is what makes the release procedure self-checking: bumping the version without dating
the changelog section fails `make check`.

**Note on scope.** The original plan also proposed `scripts/check_version.py` to verify the *tag*
matches the version. That guard only has value if a workflow runs it on tag push, and Part 1 adds no
workflow — shipping it now would be a dead script (guardrail 5). It moves to Part 2 alongside
`release.yml`. Until then, §6's checklist has a one-line manual check that does the same job.

**Verify.** `python tests/run_tests.py` passes. Temporarily edit `pyproject.toml` to `1.1.1`,
confirm the test fails, revert. Because it runs inside the existing `ci.yml`, drift is now caught on
every push and PR to `main` with no new infrastructure.

#### Step A2 — Tell source-ZIP users how to update

**Why.** This is the step that makes Part 1 worth doing beyond the tag itself. Today,
`perform_self_update()` raises *"This installation is not running from a git checkout"* whenever
`.git` is absent. For anyone who downloaded a release ZIP — a category of user that **does not exist
until you publish this release** — that message is technically true and completely unhelpful. You
are about to create those users; give them a real answer first.

**Change.**

1. `web/paths.py` gains the detector (it is a path/runtime fact, and every other module already
   imports from here):

   ```python
   def install_kind() -> str:
       """Return 'git' or 'source' for the running installation.

       Part 2 adds a 'frozen' branch for packaged builds.
       """
       return "git" if os.path.isdir(os.path.join(BASE_DIR, ".git")) else "source"
   ```

2. `web/selfupdate.py` gains a dispatcher — small now, and the extension point Part 2 needs:

   ```python
   def perform_app_update() -> Dict[str, Any]:
       if install_kind() == "git":
           return perform_self_update()
       raise AppUpdateError(
           "This copy was extracted from a source archive, so it cannot update itself. "
           "Download the latest release, or clone the repository to enable in-app updates.",
           400,
       )
   ```

   `web/api.py:624` calls `perform_app_update()` instead of `perform_self_update()`.

3. `web/api.py` — include `install_kind` and `version` in the `GET /api/app-config` payload.
   **Not** a new `/api/version` route (§1.2).

4. `web/static/js/launcher.js` — the existing update handler around line 2536 reads `install_kind`:
   `git` keeps today's behaviour; `source` shows the message above with a link to the Releases page.
   Use the page's `openGenericConfirmModal(...)` shell — never `alert()` (guardrail 4). Busy state
   toggles a CSS class rather than rewriting button markup (guardrail 8); colours come from
   `tokens.css` (guardrail 7).

**Verify.** `curl http://127.0.0.1:5050/api/app-config` includes `install_kind: "git"` in your
checkout. Copy the repo to a temp directory, delete `.git`, start it, and confirm the update button
gives the new message with a working link. Add both cases to `tests/test_api.py`.

**Git.** `git commit -m "Add version consistency test and install-kind-aware update messaging"`

**NOTE** implemented and commited up to here - szua

---

### Stage F — Document the source channel

#### Step F1 — Confirm the launchers are untouched

**Why.** `GridVibe.bat` is a genuinely good bootstrapper — it finds an interpreter, creates and
repairs `.venv`, verifies native wheels actually import, offers Desktop/Browser, and offers the voice
extras. Part 1 changes none of it. What matters is that it works from an **extracted ZIP**, not just
a clone, because that is what release users will have.

**Change.** No logic changes. One banner line so a screenshot in a bug report identifies the channel:

```
echo   Running from source (no installer — see Releases)
```

**Verify.** Extract a ZIP of the repo to a clean directory *with no `.git`*, run
`START_HERE\Start GridVibe.bat`, and confirm both Desktop and Browser modes start. This is the exact
experience a release-ZIP user will have, and it is worth testing before you publish rather than
after.

#### Step F2 — Write the documentation the release page needs

**Why.** A Releases page with a source ZIP and no explanation produces the same three questions
forever. Ten minutes of writing now removes them. `START_HERE/README.md` already exists precisely to
be findable inside a release archive.

**Change.**

- `README.md` — an **Install / Run** section that states plainly: GridVibe currently runs from
  source, Python 3.10+ is required, `GridVibe.bat` (Windows) or `GridVibe.sh` (Linux/macOS) handles
  the rest. Two ways to get it: `git clone` (recommended — enables in-app updates) or the release
  ZIP (no Git needed, update by downloading the next one). Add one line noting that a Windows
  installer is planned for 2.0.0, so nobody has to guess.
- `START_HERE/README.md` — keep the existing explanation of why the folder exists; add that this is
  the entry point for people who downloaded the ZIP.
- `CONTRIBUTING.md` — extend "Release Versioning" with a pointer to this document and the fact that
  `tests/test_version.py` now enforces the sync it describes.
- `CHANGELOG.md` — under `Unreleased`, record the update-messaging fix and the version test.

**Verify.** Hand the README to someone who has never seen the repo and watch where they hesitate.

**Git.** `git commit -m "Document the source install and update paths"`

---

### Stage I — Cut the release

Pure Git and GitHub procedure. Stages 0, A, and F must be merged to `main` with CI green before you
start. If any of this is unfamiliar, read §5 first — it covers the same commands with more
explanation.

#### Step I1 — Merge the release-prep branch

```powershell
git push -u origin release-prep-1.2.0
gh pr create --base main --title "Version guard, update messaging, release docs" --fill
# review, then:
gh pr merge --squash --delete-branch
```

**Wait for `ci.yml` to pass on `main`.** Do not proceed on a red build.

#### Step I2 — Bump the version and date the changelog

**Why.** The version bump is its own commit so the tag points at a commit whose *only* content is
"this is 1.2.0". That makes the tag's meaning unambiguous forever.

```powershell
git switch main
git pull --ff-only
```

Edit three files:

1. `gridvibe_version.py` → `__version__ = "1.2.0"`
2. `pyproject.toml` → `version = "1.2.0"`
3. `CHANGELOG.md` → rename the `## Unreleased` heading and open a new empty one above it:

   ```markdown
   ## Unreleased

   ## 1.2.0 - 2026-07-25
   ```

Step A1's test now enforces all three.

#### Step I3 — Run the checks, then commit

```powershell
make check                        # or: python tests/run_tests.py; python -m ruff check .

git add gridvibe_version.py pyproject.toml CHANGELOG.md
git commit -m "Release 1.2.0"
git push origin main
```

**Wait for `ci.yml` to pass on this exact commit** before tagging. A tag on a red commit is a tag you
will have to explain later.

#### Step I4 — Tag the release commit

An **annotated** tag (`-a`) records a message, an author, and a date. A lightweight tag is just a
pointer. Releases should always be annotated.

```powershell
git tag -a v1.2.0 -m "GridVibe 1.2.0"
git push origin v1.2.0
```

Note that `git push origin main` does **not** push tags — they need their own push. That trips
everyone up once.

**Treat a published tag as immutable.** If a problem appears afterwards, fix it forward and ship
`v1.2.1`. Never move `v1.2.0`.

#### Step I5 — Publish the GitHub Release

**Why the first release is special.** With no earlier tag, GitHub's auto-generated notes would cover
the *entire* repository history. Curate them by hand from `CHANGELOG.md` this once; from `v1.3.0`
onward, generated notes can compare against `v1.2.0` and become genuinely useful.

In the GitHub web UI: **Releases → Draft a new release → choose the existing `v1.2.0` tag.**

1. **Title:** `GridVibe 1.2.0`
2. **First line — the breaking change:**
   > ⚠ **Reverse-proxy installations:** Socket.IO CORS now defaults to same-origin. If GridVibe is
   > served from another origin, set `security.cors_origins` in `config.json`. `["*"]` restores the
   > previous behaviour.
3. **Highlights** from the `1.2.0` changelog section — curated, not pasted wholesale.
4. **How to run it:** Python 3.10+ required; clone or download the source ZIP and run
   `START_HERE\Start GridVibe.bat` / `GridVibe.sh`. Note that in-app updates need a clone.
5. Leave **Set as pre-release** unchecked; check **Set as the latest release**.
6. Save as draft, re-read it, then publish.

Or from the CLI, once you have notes in a scratch file:

```powershell
gh release create v1.2.0 --title "GridVibe 1.2.0" --notes-file RELEASE_NOTES.md --latest
```

`RELEASE_NOTES.md` is a temporary local file — do not commit it.

---

### Stage J — Verify the release

Small, because Part 1 ships no binaries. Do all of it — this is the run that teaches you what a
release actually produces.

**The release page**

- `v1.2.0` appears under **Releases** and is marked **Latest**.
- The breaking-change warning is the first thing visible.
- Source ZIP and tar.gz are both present and downloadable.
- The tag appears under **Tags** and points at your "Release 1.2.0" commit.

**The ZIP path — this is what release users get**

- Download the ZIP **from the release page** (not your local copy) and extract to a clean directory.
- Confirm there is **no `.git`** directory in it.
- Run `START_HERE\Start GridVibe.bat`. It creates `.venv`, installs dependencies, and starts.
- The UI shows version **1.2.0**; `http://127.0.0.1:5050/api/health` returns `"version": "1.2.0"`.
- **Check for updates** shows Step A2's message with a working link — not an error, and not a false
  "up to date".
- Open a local shell pane and an SSH session; open a file-explorer pane.

**The clone path**

- `git clone` into a clean directory, run `GridVibe.bat`, confirm it starts and reports 1.2.0.
- **Check for updates** reports up to date (you are on the newest `main` commit).

**Your own working copy**

- `git switch main && git pull --ff-only` — clean, no surprises.
- `git tag -l` lists `v1.2.0`; `git show v1.2.0` shows your annotation.

---

## 5. Everyday Git workflow

This is the loop between releases. It is deliberately more explanatory than the rest of the document
— §4 and §7 assume you know these commands; this section is where they get explained.

A note on `switch` and `restore`: older guides use `git checkout` for everything, which is why it is
confusing — one command that changes branches, discards edits, and extracts old files. Modern Git
splits it into `git switch` (branches) and `git restore` (files). Both still work; `checkout` is not
deprecated, but the newer names make the intent obvious and are used throughout below.

### 5.1 One-time setup on a new machine

```powershell
git config --global user.name  "JSstudent"
git config --global user.email "saso.zup@gmail.com"
git config --global init.defaultBranch main
git config --global pull.ff only        # refuse surprise merge commits on pull

git clone https://github.com/JSstudent/gridvibe.git
cd gridvibe
.\GridVibe.bat                          # creates .venv, installs deps, starts the app
```

`pull.ff only` is worth setting deliberately. It makes `git pull` fail loudly when your branch has
diverged instead of silently creating a merge commit — the single most common way a beginner's
history gets tangled.

### 5.2 The loop

Every change, however small, follows the same six steps.

**1 — Start from an up-to-date `main`.**

```powershell
git switch main
git pull --ff-only
```

**2 — Branch.** Never commit directly to `main`. A branch is free, isolates your work, and gives you
a PR to review against.

```powershell
git switch -c explorer-search-filter
```

Name it after the change, not the date or your initials.

**3 — Work, and check as you go.**

```powershell
# edit files …
make check                # ruff + the full unittest suite
git status                # what changed
git diff                  # exactly how it changed, unstaged
```

`git status` and `git diff` before every commit is the habit that prevents almost all accidental
commits.

**4 — Stage and commit.** Staging is Git's "which of my changes go in this commit" step.

```powershell
git add web/explorer.py web/static/js/explorer-viewer.js
git add -A                          # everything, including new and deleted files
git diff --staged                   # review exactly what you are about to commit
git commit -m "Add name filter to the explorer file tree"
```

Commit in logical units. Two unrelated fixes are two commits — that is what makes `git log` useful
later and what makes a single change revertable.

**5 — Push.**

```powershell
git push -u origin explorer-search-filter     # first push of a new branch
git push                                       # every push after that
```

`-u` links your local branch to the remote one so later `git push` and `git pull` need no arguments.

**6 — Open a PR, merge, clean up.**

```powershell
gh pr create --base main --fill
# CI runs; review the diff yourself in the GitHub UI
gh pr merge --squash --delete-branch

git switch main
git pull --ff-only
git branch -d explorer-search-filter          # delete the local copy too
```

**Why bother with a PR when you are the only developer?** Three concrete reasons: CI runs against
the merge, so `ci.yml` catches what you forgot to run locally; the diff view catches accidents
(a committed `config.json`, a stray debug print) far better than reading your own editor; and the
merged PR becomes a permanent, searchable record of *why* — which is exactly what your release notes
need six months later.

`--squash` collapses your branch's commits into one on `main`. For a solo project this keeps `main`
readable — one commit per change. Use `--merge` instead if you want the individual commits preserved.

### 5.3 Commit messages

This repo has no enforced format. The de facto style in `git log` is a short imperative summary,
sometimes with a bold prefix for larger features:

```
Explorer editor reliability: keep the Source viewport when entering edit mode
Add name filter to the explorer file tree
Fix SFTP save failing with "File not open for writing"
Release 1.2.0
```

Two rules worth keeping: describe **what changed and why**, not what you did (`Fix …`, not
`Changes to explorer.py`); and if the summary needs more than ~72 characters, put the detail in a
body paragraph after a blank line.

```powershell
git commit -m "Fix SFTP save failing on remote explorers" -m "Paramiko needs x+b for exclusive writable temp files; r+b silently opened read-only."
```

### 5.4 What never gets committed

`.gitignore` already covers these, but knowing *why* matters, because a `git add -A` on a
misconfigured clone would otherwise publish them:

| File | Why it must never be committed |
|---|---|
| `.encryption_key` | Decrypts every saved SSH password. Committing it publishes your credentials |
| `saved_sessions.json` | Hostnames, usernames, encrypted passwords |
| `config.json` | Your local settings, possibly local paths |
| `runtime_state.json` | Your workspace shape |
| `.known_hosts` | Which machines you connect to |
| `logs/`, `.venv/`, `__pycache__/` | Machine-local noise |

Before any commit that used `git add -A`, run `git status` and confirm none of these appear. If one
ever does:

```powershell
git restore --staged .encryption_key     # unstage it, keep the file on disk
```

If one was already committed **and pushed**, treat the secret as compromised: rotate it (delete
`.encryption_key`, re-enter saved passwords) rather than trusting that removing it from history is
enough.

### 5.5 Undo cheatsheet

The single most useful thing to internalise: **anything not yet pushed is easy to fix; anything
pushed should be fixed by adding a new commit, not by rewriting history.**

```powershell
# Discard uncommitted changes to one file
git restore web/api.py

# Discard ALL uncommitted changes (destructive — they are gone)
git restore .

# Unstage a file but keep the edit
git restore --staged web/api.py

# Fix the message of the last commit (only if NOT pushed)
git commit --amend -m "Better message"

# Undo the last commit, keep the changes staged (only if NOT pushed)
git reset --soft HEAD~1

# Undo a commit that IS pushed — creates a new commit that reverses it
git revert <sha>

# Park work in progress to deal with something else
git stash
git stash pop

# See where you have been, including "lost" commits after a bad reset
git reflog
```

`git reflog` is the safety net almost nobody knows about. It records every position `HEAD` has been
in for ~90 days, so a commit you thought you destroyed with a bad `reset` is usually still there and
recoverable with `git switch -c rescue <sha>`.

**Two commands to be careful with:** `git reset --hard` (discards work permanently) and
`git push --force` (rewrites shared history). Neither is needed in the workflow above. If you find
yourself reaching for one, `git revert` or a fresh branch is almost always the better answer.

### 5.6 Keeping a branch current

If `main` moves while you are working — likely if a branch lives more than a day or two:

```powershell
git switch main
git pull --ff-only
git switch explorer-search-filter
git merge main                       # brings main's changes into your branch
```

Resolve any conflicts, run `make check`, commit the merge. `git rebase main` produces cleaner history
but rewrites your branch's commits — fine on a branch only you have, avoid once anyone else has
pulled it. For a solo project either is fine; `merge` is harder to get wrong.

### 5.7 How tags and releases fit in

A **tag** is a permanent name for one commit. A **release** is GitHub's presentation layer built
around that tag — notes plus downloadable files.

```text
commit a1b2c3d  ──tag──▶  v1.2.0  ──GitHub Release──▶  "GridVibe 1.2.0"
                                                        ├── notes
                                                        ├── source zip / tar.gz
                                                        └── (2.0.0: installers)
```

Pushing a tag does not by itself create a Release; you publish that separately (Step I5).

```powershell
git tag -l                              # list tags
git show v1.2.0                         # the tag's message and its commit
git log --oneline v1.2.0..HEAD          # what has landed since the release
git tag -a v1.3.0 -m "GridVibe 1.3.0"   # annotated tag (always use -a for releases)
git push origin v1.3.0                  # tags need their own push
```

**Checking out a tag puts you in "detached HEAD".** That is normal and just means you are looking at
a commit rather than a branch — but note that GridVibe's own updater explicitly refuses to run in
that state (`perform_self_update()` returns *"This checkout is in detached HEAD mode"*). Get back
with `git switch main`.

```powershell
git switch --detach v1.2.0     # inspect the exact released code
git switch main                # back to normal
```

**Deleting a tag** is possible but rarely right:

```powershell
git tag -d v1.3.0                       # local only
git push --delete origin v1.3.0         # remote — anyone who fetched it keeps their copy
```

**Never re-point an existing tag at a different commit.** A tag that means two different things at
two different times is unrecoverable confusion for anyone who downloaded it. Fix forward with a new
patch version instead. This is the one Git rule in this document with no exceptions.

### 5.8 Branch hygiene

`git branch -a` currently lists sixteen remote branches, most of them merged or abandoned. That is
normal for a repo that grew organically, and it costs nothing — but it does make `git branch -a`
harder to read, and it obscures which branches are live.

```powershell
git branch --merged main                     # local branches fully merged — safe to delete
git branch -d <name>                          # delete a merged local branch
git fetch --prune                             # drop remote-tracking refs for deleted remotes
git push --delete origin <name>               # delete a remote branch you are done with
```

Worth a pass before starting Part 2, so the branch list reflects reality when the packaging work
begins. `--merged main` is the safe filter: it only lists branches whose commits are already on
`main`, so nothing unique is lost.

---

## 6. Releasing 1.3.0, 1.4.0, and beyond

After Part 1, every release is this. Ten minutes, most of it waiting for CI.

1. Merge everything intended for the release into `main`; confirm CI is green.
2. Decide the number: **patch** (`1.2.1`) for fixes only; **minor** (`1.3.0`) for new features;
   **major** — reserved for `2.0.0` and the installer (D2).
3. Bump `gridvibe_version.py` **and** `pyproject.toml`.
4. In `CHANGELOG.md`, date the `Unreleased` heading and open a fresh empty one above it.
5. `make check` — `tests/test_version.py` catches any of steps 3–4 you forgot.
6. Confirm the version you are about to tag:
   ```powershell
   python -c "from gridvibe_version import __version__; print(__version__)"
   ```
   It must match the tag you are about to create, minus the `v`. (Part 2 automates this check in
   CI via `scripts/check_version.py`.)
7. `git commit -m "Release X.Y.Z"` && `git push origin main`; wait for green CI.
8. `git tag -a vX.Y.Z -m "GridVibe X.Y.Z"` && `git push origin vX.Y.Z`.
9. Draft the release on GitHub. From `v1.3.0` onward you can use **Generate release notes** — with
   `v1.2.0` as the previous tag it produces a useful list of merged PRs — then edit it, put any
   breaking change first, and publish.
10. Spot-check: download the source ZIP from the release page, extract it somewhere clean, and
    confirm `START_HERE\Start GridVibe.bat` starts and reports the new version.

For a hotfix on a released version, branch from the tag, fix, PR to `main`, then follow the same
steps:

```powershell
git switch -c hotfix-1.2.1 v1.2.0
```

---

# PART 2 — `v2.0.0`: the installer

Do this after Part 1 has shipped and you have cut a few releases. Five stages plus a deferred sixth,
roughly 5–7 days. This part changes where user state lives, which is why it is a major version (D2)
and why §11 matters before you start.

## 7. Part 2 implementation

### Stage B — Separate the code root from the data root

This is the largest and highest-risk stage. It is also the one that makes packaging possible at all.
The governing rule: **source-mode behaviour must not change by a single byte.**

> **Back up your untracked state before starting — §11.3.** The code in this stage rolls back
> cleanly with `git switch main`, but `.encryption_key` is gitignored, decrypts every saved SSH
> password, and exists in exactly one place. One `Copy-Item` now removes the only irreversible risk
> in this plan.

#### Step B1 — Add `DATA_DIR`, `LOG_DIR`, `MODELS_DIR`, and `resource_path()`

**Why.** Six modules independently join paths onto `BASE_DIR`. Centralising the packaged-vs-source
decision in the module they all already import keeps it to one edit per consumer.
`CLAUDE.md` documents `web/paths.py` as exactly this — the shared path module — so this is not a new
abstraction, just a fuller one.

**Change.** Extend `web/paths.py`:

```python
"""Filesystem locations shared by GridVibe web modules.

Source checkouts keep every path inside the repository, exactly as before.
Frozen builds split three roots apart:

  BASE_DIR    read-only code and bundled assets (the PyInstaller payload)
  DATA_DIR    user state that must survive upgrade and uninstall
  LOG_DIR     rotating logs
  MODELS_DIR  downloaded speech models

GRIDVIBE_DATA_DIR overrides DATA_DIR in any mode (tests, portable installs).
"""

import os
import sys

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    INSTALL_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    INSTALL_DIR = BASE_DIR

APP_NAME = "GridVibe"


def _default_data_dir() -> str:
    if not FROZEN:
        return BASE_DIR                      # source mode: unchanged
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
    return os.path.join(
        os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "gridvibe"
    )


def _default_state_dir() -> str:
    if not FROZEN:
        return BASE_DIR
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
    return os.path.join(
        os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "gridvibe"
    )


DATA_DIR = os.environ.get("GRIDVIBE_DATA_DIR") or _default_data_dir()
LOG_DIR = os.path.join(_default_state_dir(), "logs")
MODELS_DIR = os.path.join(_default_state_dir(), "models")


def ensure_data_dirs() -> None:
    """Create the writable roots. Safe to call repeatedly."""
    for path in (DATA_DIR, LOG_DIR, MODELS_DIR):
        os.makedirs(path, exist_ok=True)


def resource_path(*parts: str) -> str:
    """Absolute path to a bundled, read-only asset."""
    return os.path.join(BASE_DIR, *parts)


def data_path(*parts: str) -> str:
    """Absolute path to a user-writable state file."""
    return os.path.join(DATA_DIR, *parts)
```

`install_kind()` from Step A2 gains its third branch here:

```python
def install_kind() -> str:
    if FROZEN:
        return "frozen"
    return "git" if os.path.isdir(os.path.join(BASE_DIR, ".git")) else "source"
```

**Verify.** `python -c "from web.paths import *; print(BASE_DIR, DATA_DIR, LOG_DIR)"` prints three
paths inside the repo. `GRIDVIBE_DATA_DIR` overrides `DATA_DIR`.

#### Step B2 — Move the five state files onto `DATA_DIR`

**Why.** These must survive an upgrade, and one of them (`.encryption_key`) decrypts saved SSH
passwords — losing it silently is the worst failure mode in this plan.

**Change.** One line each:

| File | Line | From | To |
|---|---|---|---|
| `web/config.py` | 21 | `os.path.join(BASE_DIR, "config.json")` | `data_path("config.json")` |
| `web/config.py` | 20 | `os.path.join(BASE_DIR, "default_config.json")` | `resource_path("default_config.json")` — read-only |
| `web/saved_sessions.py` | 24 | `os.path.join(BASE_DIR, "saved_sessions.json")` | `data_path("saved_sessions.json")` |
| `web/runtime_state.py` | 25 | `os.path.join(BASE_DIR, "runtime_state.json")` | `data_path("runtime_state.json")` |
| `web/secrets.py` | 12 | `os.path.join(BASE_DIR, ".encryption_key")` | `data_path(".encryption_key")` |
| `web/hostkeys.py` | 17 | `os.path.join(BASE_DIR, ".known_hosts")` | `data_path(".known_hosts")` |
| `web/agents.py` | 33 | `os.path.join(BASE_DIR, "agent_registry.json")` | `resource_path("agent_registry.json")` — read-only |

Each of the four writers (`config.py:117`, `runtime_state.py:143`, `saved_sessions.py:720`,
`secrets.py:21`) must call `ensure_data_dirs()` before its first write — on a fresh install
`%APPDATA%\GridVibe` does not exist yet. Keep the existing temp-file + `os.replace` pattern.

**Verify.** With `GRIDVIBE_DATA_DIR` pointed at an empty temp dir, start the app, save a session,
change a setting: the temp dir gains the files and the repo gains nothing.

#### Step B3 — Make `main.py`'s `--config` agree with `web/config.py`

**Why.** `main.py` takes `--config` defaulting to the CWD-relative string `"config.json"`, while
`web/config.py` independently defaults to its own constant. These agree today only because both
launchers `cd` to the repo root first. An installed `GridVibe.exe` started from a Start-Menu shortcut
has an arbitrary CWD, so the two would silently diverge — the server binding on defaults while the UI
edits a different file.

**Change.** In `main.py`, default `--config` to `None` and resolve through the same module:

```python
parser.add_argument(
    "--config",
    default=None,
    help="Path to configuration file (default: the per-install config.json)",
)
...
config_path = args.config or CONFIG_PATH          # from web.config
config = load_config(config_path) if os.path.exists(config_path) else {}
```

Also swap `main.py:28`'s `LOG_DIR` for the one from `web.paths`, and call `ensure_data_dirs()` in
`setup_logging()`.

Guardrail 4 requires explicit CLI flags to beat `config.json`. That concerns `--host`/`--port`/
`--debug`, which `resolve_server_settings()` already handles; an explicit `--config` still wins over
the default.

**Verify.** From a directory that is *not* the repo root, `python <repo>\main.py --port 5099` starts
and logs `Loaded configuration from <repo>\config.json`. Before this change it silently loaded
nothing.

#### Step B4 — Point speech models at `MODELS_DIR`

**Why.** `_ensure_whisper_model()` has no `download_root`, so models land in
`~/.cache/huggingface`. A packaged app needs them somewhere the installer's optional prefetch can
write and the app can report on. `.gitignore` already excludes `models/` and `vosk-model*/`.

**Change.**

1. `web/voice.py:131` — `WhisperModel(..., download_root=MODELS_DIR)`, after `ensure_data_dirs()`.
2. `services/vosk_service.py` — resolve the model directory under `MODELS_DIR` first.
3. New `--prefetch-voice-model` flag that loads the configured model once and exits with a clear
   status line. The installer's optional checkbox runs this (D6), and it doubles as a diagnostic.

**Verify.** Delete `MODELS_DIR`, start a voice session, confirm the model appears there and the
Hugging Face cache does not grow.

#### Step B5 — One-time migration from source to installed

**Why.** Someone who has been running `GridVibe.bat` for months has saved sessions and an
`.encryption_key` in the repo. Installing the packaged build must not lose them.

**Change.** `migrate_legacy_state()` in `web/paths.py`, called once from the frozen entry point only:

- No-op if `DATA_DIR/config.json` exists.
- Find a legacy tree via `GRIDVIBE_LEGACY_DIR` or an installer-recorded path.
- **Copy** (never move) `config.json`, `saved_sessions.json`, `runtime_state.json`,
  `.encryption_key`, `.known_hosts`.
- `saved_sessions.json` and `.encryption_key` migrate **as a pair or not at all** — copying one
  without the other produces sessions whose passwords can never be decrypted. Key first, verify,
  then sessions; on failure remove what was written and log a WARNING.
- Log copies at INFO and skips at DEBUG (guardrail 9).

**Verify.** Unit-test with two temp dirs: full set migrates; a set missing `.encryption_key` migrates
neither it nor `saved_sessions.json`; an existing `DATA_DIR/config.json` is never overwritten.

#### Step B6 — Run the suite

**Verify.** `make check`. Stage B touches path constants that hundreds of tests depend on
(`test_api.py` alone has 572). Fix everything here, before packaging adds a second variable.

**Git.** `git commit -m "Split code root from user-data root for packaged builds"`

---

### Stage C — Make frozen mode behave

#### Step C1 — Route bundled-asset lookups through `resource_path()`

**Change.**

- `web/webview_launcher.py:834` `_resolve_icon_path()` → `resource_path("docs", "images", "GridVibe_icon.ico")`
- `web/api.py:568` `docs_images()` → `send_from_directory(resource_path("docs", "images"), filename)`
- `web/app.py:59` — make the static folder explicit rather than relying on Flask's package-relative
  default: `Flask(__name__, template_folder=resource_path("templates"), static_folder=resource_path("web", "static"))`.
  Flask's default would *probably* resolve from the frozen payload, but "probably" is not a property
  you want to discover from a blank terminal page on a user's machine.

**Verify.** Source mode is byte-identical: launcher icon, `/docs/images/GridVibe.png`, and
`vendor/xterm.min.js` all still load.

#### Step C2 — Fix the Vosk subprocess spawn

**Why.** `web/voice.py:207-218` runs `[sys.executable, "<BASE_DIR>/services/vosk_service.py"]`.
Frozen, `sys.executable` *is* `GridVibe.exe` and the `.py` file is inside the payload — so this either
fails or launches a second full copy of GridVibe. Invisible until a user presses the microphone
button.

**Change.** The multi-call-binary pattern — same executable, dispatched on argv.

1. New `packaging/pyinstaller/entry.py`, the only thing the spec freezes:

   ```python
   """Frozen entry point. Dispatches on argv before importing the web stack."""

   import multiprocessing
   import sys


   def main() -> int:
       multiprocessing.freeze_support()   # must precede any other work

       if "--vosk-service" in sys.argv[1:]:
           sys.argv = [sys.argv[0], *[a for a in sys.argv[1:] if a != "--vosk-service"]]
           from services.vosk_service import main as vosk_main
           return vosk_main() or 0

       if "--prefetch-voice-model" in sys.argv[1:]:
           from web.voice import prefetch_default_model
           return prefetch_default_model()

       from web.paths import ensure_data_dirs, migrate_legacy_state
       ensure_data_dirs()
       migrate_legacy_state()

       from web.webview_launcher import main as launcher_main
       return launcher_main() or 0


   if __name__ == "__main__":
       sys.exit(main())
   ```

2. `web/voice.py:_ensure_vosk_service()` picks its command by mode:

   ```python
   if getattr(sys, "frozen", False):
       command = [sys.executable, "--vosk-service"]
   else:
       command = [sys.executable, resource_path("services", "vosk_service.py")]
   ```

   Keep `CREATE_NO_WINDOW`, readiness polling, and timeout handling exactly as they are.

3. `services/vosk_service.py` — guard its `sys.path.insert` bootstrap (lines 27–29) with
   `if not getattr(sys, "frozen", False):`.

This beats the r&d document's second-executable approach: one spec, one build, one future signing
target, and the voice edition does not carry two copies of `ctranslate2`.

**Verify.** Source mode unchanged. After Stage D, `GridVibe.exe --vosk-service` starts a WebSocket
server on 2700 and opens no window.

#### Step C3 — Give packaged builds an honest update response

**Change.** Step A2's dispatcher gains its third branch:

```python
def perform_app_update() -> Dict[str, Any]:
    kind = install_kind()
    if kind == "git":
        return perform_self_update()
    if kind == "frozen":
        return perform_release_update()      # 2.0.0 body below; Stage G replaces it in 2.0.1
    raise AppUpdateError(
        "This copy was extracted from a source archive, so it cannot update itself. "
        "Download the latest release, or clone the repository to enable in-app updates.",
        400,
    )
```

**This is the shipped 2.0.0 behaviour, not a placeholder** (D11):

```python
def perform_release_update() -> Dict[str, Any]:
    """Installed builds update by running the next installer (see D11)."""
    return {
        "updated": False,
        "restart_required": False,
        "install_kind": "frozen",
        "notes_url": "https://github.com/JSstudent/gridvibe/releases/latest",
        "message": (
            f"GridVibe {__version__} is an installed build. "
            "Download and run the latest installer to update — your settings, "
            "saved sessions, and keys are preserved."
        ),
    }
```

Stage G replaces the body of this one function and nothing else. Keep the signature and response keys
stable so the Step A2 frontend needs no rework.

**Verify.** `tests/test_api.py` covers all three `install_kind` values. The `frozen` case asserts a
200 with a `notes_url` — not an error.

#### Step C4 — Lock in the frozen restart path with a test

**Why.** `_build_restart_command()` already handles frozen mode, but only by luck of ordering: the
Windows branch checks `.venv\Scripts\python.exe` and `webview_launcher.py` with `is_file()` and falls
through when absent. Correct, and completely undocumented — the kind of thing a refactor breaks.

**Change.** Add a test to `tests/test_webview_launcher.py` patching `sys.frozen = True`, asserting
the command is `[sys.executable, *sys.argv[1:]]` with no `.venv` in it. Comment the ordering at
`web/webview_launcher.py:851`.

#### Step C5 — Pin the WebSocket transport dependency

**Why.** `SocketIO(async_mode="threading")` upgrades to real WebSockets only when `simple_websocket`
is importable. It is present only as a transitive dependency of `python-engineio`, is named in no
requirements file, and is imported lazily — the three conditions that make PyInstaller omit it. The
failure is silent: the app works, falls back to HTTP long-polling, and terminals feel sluggish.
Guardrail 3 explicitly forbids polling in place of push.

**Change.** Add to `requirements.txt` under the Socket.IO transport block:

```
simple-websocket>=1.1.0
wsproto>=1.3.2
```

and list both in the spec's `hiddenimports`.

**Verify.** Browser devtools show a `websocket` connection, not repeated
`/socket.io/?transport=polling`. Repeat against the frozen build — this is the check most likely to
catch a bad freeze.

**Git.** `git commit -m "Make frozen-mode resource, subprocess, and update paths correct"`

---

### Stage D — Freeze the application

#### Step D1 — Pin the build environment

**Change.**

1. New `requirements-packaging.txt`:

   ```
   -r requirements-desktop.txt
   -r requirements.txt
   pyinstaller>=6.11
   pyinstaller-hooks-contrib>=2024.10
   ```

   It pulls `requirements-desktop.txt` because `pywinpty` — which local Windows terminals require
   even in browser mode — lives there rather than in core. Freezing without it produces a build whose
   local shells fail with "Interactive Windows local terminals require pywinpty"
   (`web/terminal_io.py:988`).

2. New `requirements-packaging-voice.txt`: `-r requirements-packaging.txt` + `-r requirements-voice.txt`.

3. Reconcile `.python-version` to `3.12` (D10), or leave it as the minimum with a comment naming
   3.12 as the packaging interpreter — either, as long as one file states it.

**Verify.** A clean 3.12 venv installs `requirements-packaging.txt` and can
`import webview, winpty, paramiko, flask_socketio, simple_websocket`.

#### Step D2 — Write the PyInstaller spec

**Change.** New `packaging/pyinstaller/gridvibe.spec` — one spec, two editions:

```python
# -*- mode: python ; coding: utf-8 -*-
"""GridVibe frozen build. Set GRIDVIBE_WITH_VOICE=1 for the voice edition."""

import os

WITH_VOICE = os.environ.get("GRIDVIBE_WITH_VOICE") == "1"

datas = [
    ("templates",              "templates"),
    ("web/static",             "web/static"),
    ("services",               "services"),
    ("default_config.json",    "."),
    ("agent_registry.json",    "."),
    ("docs/images",            "docs/images"),
]

hiddenimports = [
    "engineio.async_drivers.threading",
    "simple_websocket", "wsproto",
    "flask_socketio", "socketio", "engineio",
    "paramiko", "cffi", "_cffi_backend", "cryptography",
    "markdown", "bleach", "websocket",
    "winpty",
]

VOICE_MODULES = [
    "faster_whisper", "ctranslate2", "onnxruntime", "av",
    "numpy", "vosk", "websockets", "tokenizers", "huggingface_hub",
]

if WITH_VOICE:
    hiddenimports += VOICE_MODULES
    excludes = ["tkinter", "matplotlib", "pytest", "IPython"]
else:
    excludes = VOICE_MODULES + ["tkinter", "matplotlib", "pytest", "IPython", "torch"]

a = Analysis(
    ["entry.py"],
    pathex=[os.path.abspath(os.path.join(SPECPATH, "..", ".."))],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="GridVibe",
    console=False,
    icon=os.path.join(SPECPATH, "..", "..", "docs", "images", "GridVibe_icon.ico"),
    version=os.path.join(SPECPATH, "version_info.txt"),
)
COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="GridVibe")
```

Also add `packaging/pyinstaller/version_info.txt` (generated from `__version__`) and a `README.md`
documenting prerequisites, build commands, and known hidden-import failure modes.

**On `console=False`.** GridVibe's normal mode is a native window; a console would be noise. The
trade-off is that early crashes have nowhere to print — which is exactly why Step D4's smoke test
polls `/api/health` rather than trusting a visual check.

#### Step D3 — Add the build scripts

**Change.** New `packaging/windows/build_app.ps1`:

```powershell
param([switch]$Voice)

$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\..\.."
Push-Location $root
try {
    $env:GRIDVIBE_WITH_VOICE = if ($Voice) { "1" } else { "0" }
    $edition = if ($Voice) { "voice" } else { "core" }

    python -m pip install -r (if ($Voice) { "requirements-packaging-voice.txt" }
                              else        { "requirements-packaging.txt" })
    python packaging/pyinstaller/make_version_info.py
    python -m PyInstaller --noconfirm --clean `
        --distpath "dist/$edition" --workpath "build/$edition" `
        packaging/pyinstaller/gridvibe.spec

    Set-Content -Path "dist/$edition/GridVibe/edition.txt" -Value $edition -NoNewline
    Write-Host "Built $edition edition -> dist/$edition/GridVibe"
} finally { Pop-Location }
```

`edition.txt` sits next to the exe and is what the Stage G updater reads to choose which installer
asset to download.

**Verify.** Expect roughly 90–130 MB (core) and 450–700 MB (voice).

#### Step D4 — Smoke-test the frozen build

**Why.** The two most likely first-freeze failures are a missing hidden import (app dies at startup,
invisibly, because `console=False`) and a silent transport downgrade. Both are cheap to detect
automatically and expensive to find by hand.

**Change.** New `packaging/windows/smoke_test.ps1`:

1. Start `GridVibe.exe --mode browser --port 5099`.
2. Poll `/api/health` for up to 30 s; assert `status == healthy` and the expected version.
3. `GET /terminals`; assert the HTML references `vendor/xterm.min.js` (proves `web/static` is
   bundled and the static folder resolves).
4. `GET /docs/images/GridVibe.png`; assert 200.
5. Voice edition: `GridVibe.exe --vosk-service`, assert port 2700 accepts a connection, kill it.
6. Kill the app; assert a clean exit and that `%APPDATA%\GridVibe\` was created.

Manual checks no script can cover, on a **clean VM with no Python**: native window opens, SSH
connects, a local PowerShell pane spawns (the `pywinpty` check), WSL detects distros, a file-explorer
pane lists a directory.

**Git.** `git commit -m "Add PyInstaller spec, build scripts, and frozen smoke test"`

---

### Stage E — The Windows installer

#### Step E1 — Installer skeleton

**Change.** New `packaging/windows/gridvibe.iss` (Inno Setup 6.3+):

```
#define AppVersion GetEnv("GRIDVIBE_VERSION")
#define Edition    GetEnv("GRIDVIBE_EDITION")     ; "core" | "voice"

[Setup]
AppId={{8E3B1E2A-6C4D-4E1F-9A77-GRIDVIBE0001}     ; fixed forever — upgrades match on this
AppName=GridVibe
AppVersion={#AppVersion}
DefaultDirName={autopf}\GridVibe
PrivilegesRequired=lowest                          ; per-user, no UAC (D7)
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=GridVibe-{#AppVersion}{#Edition == "voice" ? "-voice" : ""}-setup
SetupIconFile=..\..\docs\images\GridVibe_icon.ico
UninstallDisplayIcon={app}\GridVibe.exe
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes                              ; needed for silent auto-update
RestartApplications=no
```

- `[Files]` — recursive copy of `dist\{#Edition}\GridVibe\*` to `{app}`.
- `[Icons]` — Start Menu always; desktop shortcut behind a `[Tasks]` entry.
- `[Run]` — optional "Launch GridVibe" with `postinstall nowait skipifsilent`. `skipifsilent` is
  essential: the auto-updater runs the installer silently and restarts the app itself.
- `[UninstallDelete]` — the install dir only. **Never** `%APPDATA%\GridVibe`.

Both editions share this one script; `GRIDVIBE_EDITION` selects the source folder, the output
filename, and whether the voice pages appear.

#### Step E2 — WebView2 detection and bootstrap

**Change.** In `[Code]`, before install:

1. Check for a non-empty `pv` value under the Evergreen Runtime client GUID
   `{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}` in all three of
   `HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\...`,
   `HKLM\SOFTWARE\Microsoft\EdgeUpdate\Clients\...`, and
   `HKCU\Software\Microsoft\EdgeUpdate\Clients\...`.
2. If absent, use `DownloadTemporaryFile` (Inno 6.1+) to fetch the Evergreen bootstrapper from
   `https://go.microsoft.com/fwlink/p/?LinkId=2124703` and run it `/silent /install`.
3. **Failure is non-fatal.** Unlike the r&d document's recommendation, do not abort. GridVibe's
   `_open_browser_fallback()` already exists and works; warn that it will open in the browser and
   continue. Blocking an install over an optional window mode is the wrong trade.
4. Skip entirely in silent mode — an upgrade of a running app is proof its window mode works.

#### Step E3 — The voice edition's model prefetch

**Change.** Voice edition only:

- A wizard checkbox: **"Download the default voice model now (~145 MB). You can skip this — the model
  downloads on first use."** Default checked when the network is reachable.
- `[Run]`: `{app}\GridVibe.exe --prefetch-voice-model` with `runhidden`, `skipifsilent`, and the
  checkbox as its `Check:` condition. Failure is non-fatal.
- The core edition shows no voice page. Its Voice settings panel reports "Offline voice runtime not
  installed — download the Voice edition", linking to Releases.

#### Step E4 — Build the installers

**Change.** New `packaging/windows/build_installer.ps1`:

```powershell
param([switch]$Voice)

$ErrorActionPreference = "Stop"
$edition = if ($Voice) { "voice" } else { "core" }
$version = (python -c "import sys; sys.path.insert(0,'.'); from gridvibe_version import __version__; print(__version__)").Trim()

$env:GRIDVIBE_VERSION = $version
$env:GRIDVIBE_EDITION = $edition

& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" `
    /O"$PSScriptRoot\..\..\dist" `
    "$PSScriptRoot\gridvibe.iss"
```

> **Single-file variant (D5 override).** Set `[Components] core; voice` in the `.iss`, tag the voice
> payload's `[Files]` entries `Components: voice`, and build once with `-Voice`. Everything else is
> unchanged; the download becomes ~500 MB for everyone.

**Verify.** Both installers run on clean Windows 10 and 11 x64 VMs: no UAC prompt, Start-Menu entry
present, app launches, `%APPDATA%\GridVibe` created, uninstall removes the program but **leaves**
`%APPDATA%\GridVibe` intact.

**Git.** `git commit -m "Add Inno Setup installer for core and voice editions"`

---

### Stage H — Automate the build on tag push

#### Step H1 — `release.yml`

**Why.** Building installers by hand means the artifact depends on your laptop. It also means the
version guard is advisory. Neither is acceptable for something users download and run.

**Change.** New `.github/workflows/release.yml`. Leave `ci.yml` alone. This is also where
`scripts/check_version.py` (deferred from Part 1's Stage A) finally earns its place:

```python
# scripts/check_version.py
"""Fail the release build when the pushed tag disagrees with the app version."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gridvibe_version import __version__  # noqa: E402

tag = os.environ.get("RELEASE_TAG", "").strip()
if not tag:
    sys.exit("RELEASE_TAG is not set.")
if tag.lstrip("v") != __version__:
    sys.exit(f"Tag {tag!r} does not match gridvibe_version {__version__!r}.")
print(f"Version OK: {__version__} (tag {tag})")
```

```yaml
name: Release

on:
  push:
    tags: ['v*']
  workflow_dispatch:          # build installers on demand from any branch, no tag

permissions:
  contents: write

jobs:
  validate:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with: { python-version: "3.12" }
      - run: python -m pip install -r requirements-dev.txt
      - name: Tag matches app version
        if: startsWith(github.ref, 'refs/tags/')
        env: { RELEASE_TAG: "${{ github.ref_name }}" }
        run: python scripts/check_version.py
      - run: python -m ruff check .
      - run: python tests/run_tests.py

  build-windows:
    needs: validate
    runs-on: windows-latest
    strategy:
      fail-fast: false
      matrix:
        edition: [core, voice]
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with: { python-version: "3.12" }
      - name: Build frozen app
        run: |
          if ("${{ matrix.edition }}" -eq "voice") {
            .\packaging\windows\build_app.ps1 -Voice
          } else {
            .\packaging\windows\build_app.ps1
          }
      - name: Smoke test
        run: .\packaging\windows\smoke_test.ps1 -Edition ${{ matrix.edition }}
      - name: Build installer
        run: |
          if ("${{ matrix.edition }}" -eq "voice") {
            .\packaging\windows\build_installer.ps1 -Voice
          } else {
            .\packaging\windows\build_installer.ps1
          }
      - uses: actions/upload-artifact@v4
        with:
          name: installer-${{ matrix.edition }}
          path: dist/*.exe

  publish:
    needs: build-windows
    if: startsWith(github.ref, 'refs/tags/')     # manual runs stop at artifacts
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/download-artifact@v4
        with: { path: artifacts, pattern: installer-*, merge-multiple: true }
      - name: Generate SHA256SUMS
        run: cd artifacts && sha256sum *.exe > SHA256SUMS
      - name: Extract release notes from CHANGELOG
        run: python scripts/release_notes.py "${{ github.ref_name }}" > NOTES.md
      - uses: softprops/action-gh-release@v2
        with:
          files: artifacts/*
          body_path: NOTES.md
          draft: true          # review before it goes public
```

Notes on choices:

- `windows-latest` has Inno Setup 6 preinstalled. If that changes, add `choco install innosetup -y`
  — the build script already resolves `ISCC.exe` from `%ProgramFiles(x86)%`.
- The smoke test runs **between** freeze and installer build, so a broken freeze never becomes a
  published installer.
- `draft: true` — the workflow prepares the release; a human publishes it.
- **`workflow_dispatch` is the packaging safety net.** `ci.yml` does not freeze anything, so a commit
  that breaks the frozen build — a new lazily-imported dependency, a template that never made it into
  `datas` — lands on `main` invisibly and surfaces only when you tag. A manual run builds and
  smoke-tests both editions from any branch without creating a release. The two `if:` guards keep the
  version check and publish job tag-only; artifacts still upload, so this is also how you hand
  someone a test build. §8.5 covers when to use it.

#### Step H2 — `scripts/release_notes.py`

**Change.** Reads `CHANGELOG.md`, extracts the section for the given version, prints it with a
download table and any breaking-change warning at the top. Keeps notes and changelog from diverging.

**Git.** `git commit -m "Add tag-triggered release workflow"`

---

### Stage I2 — Cut the 2.0.0 release

Identical to Part 1's Stage I, with four differences:

1. The version is `2.0.0` and the tag is `v2.0.0`.
2. The release notes lead with the **data migration**: state moves to `%APPDATA%\GridVibe` for
   installed builds; source checkouts are unaffected; the one-time migration copies rather than
   moves.
3. `release.yml` builds and attaches the installers automatically, then drafts the release. You
   review and publish rather than assembling it by hand.
4. The download table names `GridVibe-2.0.0-voice-setup.exe` as recommended, includes sizes and
   SHA-256 values, and carries the SmartScreen note (D9): the installers are unsigned; Windows shows
   "Windows protected your PC" → More info → Run anyway.

---

### Stage J2 — Verify the 2.0.0 release

Run on clean VMs with no Python and no Git. Everything here has already failed for somebody.

**Install matrix**

| Case | Expected |
|---|---|
| Windows 10 x64, no WebView2 | Bootstrapper runs; native window opens |
| Windows 10 x64, WebView2 present | No download; native window opens |
| Windows 11 x64 | Native window opens |
| Non-admin standard user | No UAC prompt; installs to `%LOCALAPPDATA%\Programs` |
| Offline install | Succeeds; WebView2 step warns; app opens in browser mode |
| Core edition | Voice panel reports the runtime is absent, with a link — no crash |
| Voice edition, prefetch checked | Model lands in `%LOCALAPPDATA%\GridVibe\models` |
| Voice edition, prefetch skipped | First voice use downloads the model |

**Runtime checks (both editions)**

- Native window opens with the correct icon and title.
- Local `cmd` and PowerShell panes spawn — the `pywinpty` freeze check.
- WSL distro detection works.
- An SSH session connects; a saved session with a stored password decrypts (exercises
  `.encryption_key` under `%APPDATA%`).
- A file-explorer pane lists, previews, and downloads; the Git sidebar loads.
- Browser devtools show a **`websocket`** connection, not polling — the Step C5 check.
- Quit and relaunch: the workspace-restore banner offers the saved workspace.
- `%LOCALAPPDATA%\GridVibe\logs\gridvibe.log` exists, has no ANSI escapes, and shows no
  high-frequency polling (guardrail 9).

**Upgrade and removal**

- Install 2.0.0 over an existing 2.0.0: in-place, same AppId, no duplicate Start-Menu entry.
- Uninstall: program files gone, `%APPDATA%\GridVibe` **intact**.
- Reinstall after uninstall: previous sessions and settings return.

**Migration**

- On a machine with an existing source checkout, install and confirm settings and saved sessions
  appear, **and** that the repo copies are still there (copy, not move).

**Development channel — must be unaffected**

- Fresh clone → `GridVibe.bat` → both modes start; state stays in the repo; nothing in
  `%APPDATA%\GridVibe`; in-app update still fast-forwards.
- Release source ZIP (no `.git`) → `START_HERE\Start GridVibe.bat` starts; update check returns the
  Step A2 message.

**Update messaging (2.0.0)**

- In the installed build, **Check for updates** returns Step C3's message with a working link. It
  must not error, and must not claim GridVibe is up to date when it has no way of knowing.

---

### Stage G — Release-aware self-update — ⏭ **DEFERRED to 2.0.1**

> **Skip this in the 2.0.0 implementation** (D11). Specified in full so it can be picked up as
> self-contained work once a real 2.0.0 release exists to upgrade *from*.
>
> Two things 2.0.0 must ship to keep this cheap — both already in scope: the `perform_app_update()`
> dispatcher (Step C3) and the `edition.txt` marker (Step D3).

#### Step G1 — Version comparison without a new dependency

String comparison gets `1.10.0 < 1.9.0` wrong. `packaging.version` would solve it but is not a
declared dependency.

```python
def _parse_version(text: str) -> tuple[int, ...]:
    """Parse 'v1.2.0' / '1.2.0' into a comparable tuple. Unknown suffixes sort low."""
    core = text.strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    parts = []
    for chunk in core.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)
```

**Verify.** `1.10.0 > 1.9.0`, `1.2.0 == v1.2.0`, `2.0.0 > 1.9.9`.

#### Step G2 — Check for a newer release

1. `GET https://api.github.com/repos/JSstudent/gridvibe/releases/latest` with a 10 s timeout, an
   explicit `User-Agent: GridVibe/<version>`, and `Accept: application/vnd.github+json`. Use
   `urllib.request` — `requests` is not a dependency.
2. Compare `tag_name` to `__version__`. Not newer → a clean "already latest" response.
3. Newer → read `edition.txt`, pick the matching asset, read `SHA256SUMS`, return
   `{available, asset_url, sha256, notes_url}`.
4. Never download here. Discovery and application are separate calls.

**Rate limiting.** Unauthenticated GitHub API calls are capped at 60/hour per IP. The check is a
button press, so this is not a practical constraint — but cache for 15 minutes and surface a 403 as
"Update check is temporarily rate-limited" rather than a stack trace.

#### Step G3 — Apply the update

1. Download to a temp directory.
2. **Verify SHA-256 against `SHA256SUMS`.** Mismatch → delete, raise `AppUpdateError`, log WARNING.
   This is the security boundary of the whole feature; there is no "continue anyway".
3. Launch `installer.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART` detached.
4. Return `{"updated": True, "restart_required": True}` and let the existing
   `restart_application()` → `_build_restart_command()` machinery handle the relaunch (Step C4 proved
   this path).
5. Never touch `DATA_DIR`.

#### Step G4 — Wire the UI

Extend the Step A2 handler in `web/static/js/launcher.js`: on `frozen` with an update available, open
`openGenericConfirmModal(...)` showing the new version, download size, notes link, and Update /
Not now. Busy state toggles a CSS class (guardrail 8); failure shows a Retry affordance; colours from
`tokens.css` and stroke-style `currentColor` icons (guardrail 7).

**Verify.** End-to-end, and this is the test that matters: install 2.0.0, publish 2.0.1, press Check
for updates, confirm the prompt, the download verifies, the silent install runs, the app restarts as
2.0.1, and **saved sessions, config, and `.encryption_key` are all intact**.

---

### Stage K — Linux (deferred past 2.0.0)

Recorded so the decision is not re-litigated.

- **Freeze:** same spec, `ubuntu-22.04` runner (oldest supported glibc wins).
- **Package:** AppImage via `linuxdeploy` + `linuxdeploy-plugin-appimage`, plus a `.desktop` file and
  a PNG icon.
- **GUI:** ship **browser-mode-first**. Bundling QtWebEngine adds 150–250 MB and is the most fragile
  part of any Linux freeze; `_open_browser_fallback()` already handles this cleanly, and
  `_set_linux_qtwebengine_env()` shows how much special-casing native Linux already needs.
- **Voice:** same two-edition split.
- **Update:** download the new AppImage, verify SHA-256, `chmod +x`, atomically replace, restart.
  Simpler than Windows — no installer to invoke.
- **`.deb`:** only if apt integration is requested.

Until this ships, Linux users are on the source channel via `GridVibe.sh`, which works today.

---

## 8. Working on GridVibe after Part 2

Everything in §5 still applies unchanged. Part 2 adds five things to keep in mind, each because
breaking it produces a bug that is **invisible in source mode and only appears in the frozen build**
— the expensive kind.

### 8.1 Source mode is unchanged

State still lives in the repo: `config.json`, `saved_sessions.json`, `runtime_state.json`,
`.encryption_key`, `.known_hosts`, `logs\`. Stage B resolves every writable root back to `BASE_DIR`
when `sys.frozen` is false, precisely so this stays true.

One new capability, useful for reproducing a user's report — point a checkout at a throwaway state
directory, or at the installed build's:

```powershell
$env:GRIDVIBE_DATA_DIR = "$env:TEMP\gv-clean"     # empty config, no saved sessions
$env:GRIDVIBE_DATA_DIR = "$env:APPDATA\GridVibe"  # debug what the installed build sees
Remove-Item Env:\GRIDVIBE_DATA_DIR                 # back to in-repo state
```

### 8.2 The five new rules

**1. Never write into the code tree. New state files go through `data_path()`.**
`os.path.join(BASE_DIR, "something.json")` for a writable file works perfectly on your machine and
fails on an installed build, where the install directory may not be writable and is wiped on upgrade.
Writable → `data_path()`. Read-only asset → `resource_path()`.

**2. A new bundled asset must be added to the PyInstaller spec.**
The single most likely packaging regression: a new template or static file works flawlessly from
source and simply 404s in the frozen app. `datas` covers whole directories (`templates`,
`web/static`, `services`, `docs/images`) plus two named files, so a new file *inside* a covered
directory needs nothing — a new top-level file or directory needs a `datas` entry.

**3. A lazily or dynamically imported dependency needs a `hiddenimports` entry.**
PyInstaller finds imports by static analysis. `simple-websocket` (Step C5) is the cautionary tale: it
was silently absent, the app kept working, and Socket.IO quietly degraded to long-polling.

**4. A new dependency goes in the right requirements file.**

| Dependency is… | Goes in | Reaches the frozen build? |
|---|---|---|
| Needed to serve a terminal | `requirements.txt` | Yes |
| Native window / Windows PTY | `requirements-desktop.txt` | Yes — packaging pulls this too |
| Offline speech | `requirements-voice.txt` | **Voice edition only** |
| Lint/test only | `requirements-dev.txt` | No |

If you add to `requirements-voice.txt`, the core edition will not have it — guard the import and
degrade the way `web/voice.py` already does.

**5. The existing guardrails still apply.** Config keys through `RuntimeConfig`; shared explorer logic
in `web/explorer.py`; shared JS in `shared.js`; new frontend surfaces in their own file; colours from
`tokens.css`; no `alert`/`confirm`/`prompt`; no `socketio.emit` while holding a lock. See
`CLAUDE.md`. Packaging adds requirements; it removes none.

### 8.3 Verifying a change survives freezing

Not for every commit — when you have touched anything in §8.2, and once before every release.

```powershell
.\packaging\windows\build_app.ps1                  # ~2-4 min, core edition
.\packaging\windows\smoke_test.ps1 -Edition core
```

Add `-Voice` only if you touched `web/voice.py`, `services/vosk_service.py`, or
`requirements-voice.txt`; that build is much slower.

### 8.4 Catching packaging regressions between releases

`ci.yml` runs ruff and the unit tests. It does **not** freeze anything, so a change that breaks
packaging merges cleanly and surfaces only when someone pushes a tag.

- **On demand:** **Actions → Release → Run workflow** on any branch (Step H1's `workflow_dispatch`).
  Builds and smoke-tests both editions, uploads installers as artifacts, creates no release, needs no
  tag.
- **Scheduled:** if packaging breaks more than once, add `schedule: - cron: '0 6 * * 1'` for a weekly
  canary. Do not add packaging to `ci.yml` — a 4-minute freeze on every PR is a bad trade.

### 8.5 Trying the release pipeline without releasing

RC tags run the full pipeline including the version guard and draft release:

```powershell
git tag -a v2.1.0-rc1 -m "GridVibe 2.1.0 RC1"
git push origin v2.1.0-rc1
```

Two rules: **mark the release as a prerelease** (GitHub's `/releases/latest` — which Stage G's
updater reads — excludes prereleases, so an RC published normally would be offered to every installed
user); and `scripts/check_version.py` compares against `__version__`, so bump to the RC string, cut
the RC, then bump to the final string for the real tag.

### 8.6 Three questions when adding a feature

1. **Does it write a file?** → `data_path()`, and add it to Step B5's migration list if losing it
   would hurt.
2. **Does it add an asset or dependency?** → §8.2 rules 2–4, then run §8.3.
3. **Is it heavy, and optional?** → if it adds more than ~50 MB frozen and not everyone needs it, it
   belongs in the voice edition's dependency set or a new optional edition — not core. The core
   installer's size is the reason it exists.

---

## 9. Risk register

### Part 1 risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| First-tag release notes cover all history | Certain if auto-generated | Low | Curate from `CHANGELOG.md` this once (Step I5); from `v1.3.0` onward generated notes compare against `v1.2.0` |
| Tag pushed on a commit whose CI is red | Medium | Medium | Steps I1/I3 both say wait for green; §6 repeats it |
| Version files drift from the tag | Medium | Medium | `tests/test_version.py` runs in existing CI; §6 step 6 is a manual pre-tag check |
| Release-ZIP users hit the "not a git checkout" error | **Certain without Step A2** | Medium | Step A2 replaces it with a link to Releases — this is why A2 is in Part 1, not Part 2 |
| Forgetting `git push origin v1.2.0` (tags need their own push) | High, once | Low | Called out in Step I4 and §5.7; `git tag -l` on GitHub confirms |

### Part 2 risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `pywinpty` missing or broken in the frozen build → no local Windows terminals | **High** | High | Step D1 installs `requirements-desktop.txt`; Step D4 spawns a local pane on a clean VM |
| `simple-websocket` dropped by PyInstaller → silent long-polling fallback | **High** | Medium | Step C5 pins it, lists it as a hidden import, adds an explicit transport check to Stage J2 |
| `.encryption_key` lost during Step B5 migration → saved SSH passwords unrecoverable | Medium | **Critical** | Copy never move; key and sessions migrate as a pair or not at all; dedicated unit test; §11.3 backup |
| WebView2 absent and bootstrapper blocked → no native window | Medium | Low | Non-fatal; browser fallback exists and is exercised in Stage J2 |
| SmartScreen blocks the unsigned installer | **High** | Medium | Documented in README and release notes with published SHA-256 values (D9) |
| Voice edition size (~500 MB) deters downloads | Medium | Low | Two editions (D5); core is ~100 MB |
| Silent auto-update fails mid-install *(Stage G, 2.0.1)* | Low | High | Per-user install with `CloseApplications=yes`; Inno rolls back; SHA-256 verified before launch. Deferring to 2.0.1 means this is first exercised against a real 2.0.0 install |
| GitHub API rate limit on update checks *(Stage G, 2.0.1)* | Low | Low | Manual button, 15-minute cache, explicit 403 message |
| Stage B breaks source-mode paths for existing developers | Medium | Medium | Source mode resolves every root to `BASE_DIR` unchanged; `make check` runs before the stage is committed |
| A packaging regression merges to `main` unnoticed | Medium | Medium | `workflow_dispatch` before merging anything in §8.2 territory (§8.4) |
| 2.0.0 users must update by hand until Stage G ships | Certain | Low | Step C3's message links to Releases; §10.3 recommends 2.0.1 within weeks |

---

## 10. Effort and sequencing

### 10.1 Part 1 — `v1.2.0`

| Stage | Deliverable | Effort |
|---|---|---|
| 0 | Branch, supersede old docs | 15 min |
| A | Version consistency test + install-kind update messaging | 0.5 day |
| F | Source-channel documentation | 0.5 day |
| I | Version bump, tag, publish | 0.5 day |
| J | Verify the release and the ZIP path | 0.5 day |

**Total: roughly 1–1.5 days**, and every stage is independently useful even if you stop.

### 10.2 Between the parts

`1.3.0`, `1.4.0`, … — about **ten minutes each** using §6. This is the phase that makes Part 2
comfortable.

### 10.3 Part 2 — `v2.0.0`

| Stage | Deliverable | Effort | Blocks |
|---|---|---|---|
| B | `DATA_DIR` split + migration | **1–1.5 days** | D, E |
| C | Frozen-mode correctness | 1 day | D |
| D | PyInstaller build, both editions, smoke test | **1–2 days** | E |
| E | Inno Setup installers, WebView2, model prefetch | 1–1.5 days | I2 |
| H | `release.yml` + `check_version.py` | 0.5 day | I2 |
| I2 | Version bump, tag, publish | 0.5 day | — |
| J2 | Clean-VM verification | 0.5–1 day | — |

**Total: roughly 5–7 days.**

Deferred: Stage G (1–1.5 days, → 2.0.1), Stage K Linux (1–2 days), code signing (~0.5 day plus
certificate cost).

`B → C → D → E` is the critical path and must run in that order. `H` is independent. The most useful
checkpoint is **after Stage D**: a frozen `GridVibe.exe` that passes the smoke test on a clean VM
proves the hard part works; everything after is mechanical. If Part 2 is going to fail, it fails at
Stage D — get there early rather than saving it for last.

**Why 2.0.1 should follow quickly.** Stage G is strictly better built *after* an installer release
exists, because verifying it requires two published versions with installer assets. Cutting 2.0.0,
building Stage G against it, then cutting 2.0.1 is both lower-risk and the fastest route to a proven
update path. Plan for weeks, not months — every 2.0.0 user updates by hand until it ships.

---

## 11. Rollback and recovery

**Short version: everything in Part 1 except the tag push is reversible with `git switch main`. The
same is true of Part 2 except the tag push and installers you ran on your own machine. And Git never
protected the state files — it still doesn't.**

### 11.1 Your restore point already exists

`main` is at `f965026` and identical to `origin/main`, so the pre-release state is durable on GitHub,
not just on one machine. Abandoning a work branch costs nothing:

```powershell
git switch main                       # back to the last known-good tree
git branch -D release-prep-1.2.0      # or keep it and retry from a new branch
git switch -c retry-1.2.0
```

### 11.2 Reversibility by stage

| Stage | Reversible by `git switch main`? | Residue |
|---|---|---|
| **Part 1** — 0, A, F | **Fully** | None |
| **Part 1** — J | **Fully** | An extracted test ZIP in a temp directory — delete it |
| **Part 1** — I | **No — the tag push is the commit point** | §11.4 |
| **Part 2** — B | **Fully** — source mode resolves `DATA_DIR` back to `BASE_DIR` | Only if you ran with `GRIDVIBE_DATA_DIR` set or ran a frozen build — §11.3 |
| **Part 2** — C, H, G | **Fully** | None |
| **Part 2** — D | **Fully** | `dist\`, `build\` — gitignored, delete them |
| **Part 2** — E | Code yes | Testing an installer on your own machine creates `%LOCALAPPDATA%\Programs\GridVibe` and `%APPDATA%\GridVibe`. Remove via Settings → Apps |
| **Part 2** — I2 | **No** | §11.4 |

### 11.3 What Git was never protecting

`config.json`, `saved_sessions.json`, `runtime_state.json`, `.encryption_key`, and `.known_hosts` are
all gitignored. `git switch` neither restores nor destroys them — which cuts both ways.

**`.encryption_key` is the one genuinely irreversible item in this plan.** It decrypts every saved SSH
password, exists in exactly one place, and no checkout brings it back. Step B5's migration is
copy-only and pairs the key with `saved_sessions.json` for this reason, but take a copy before
Stage B:

```powershell
$backup = "$env:USERPROFILE\gridvibe-state-backup-$(Get-Date -Format yyyy-MM-dd)"
New-Item -ItemType Directory -Force $backup | Out-Null
Copy-Item config.json,saved_sessions.json,runtime_state.json,.encryption_key,.known_hosts `
  -Destination $backup -Force -ErrorAction SilentlyContinue
```

Keep it until Stage J2 passes.

**The subtle one:** if you ran the app with `GRIDVIBE_DATA_DIR` set, or ran a frozen build, new
sessions and settings went to that directory. Rolling the code back makes the app read the in-repo
copies again — the newer work is not lost, it is *invisible* until you copy it back. Check
`%APPDATA%\GridVibe` before concluding anything was destroyed.

**Also:** installing a build on your dev box means your `.encryption_key` now exists in two places.
Not a rollback problem, but know where your secrets are.

**`.venv` does not roll back either.** Reverting `requirements.txt` will not uninstall `pyinstaller`
or `simple-websocket`, so `make check` may pass for reasons the reverted requirements no longer
guarantee. After any rollback touching requirements: delete `.venv` and re-run `GridVibe.bat`.

### 11.4 After the tag — fix forward, never sideways

Pushing a release tag is the point of no return. You *can* delete one:

```powershell
git push --delete origin v1.2.0       # anyone who fetched it keeps their copy
```

…but **never re-point an existing tag at a different commit.** Fix forward with a new patch version.

Once a release is **published**, treat it as permanent even if you delete it: the asset URLs were
live. In Part 2, installers may already be on users' machines, and a repo rollback uninstalls
nothing.

For Part 2, Step H1's `draft: true` is the last safe checkpoint: the tag is pushed and artifacts are
built, but nothing is downloadable until a human publishes. If the draft looks wrong, delete the
draft, delete the tag, fix, and re-tag.

### 11.5 If Part 2 defeats you

The packaging stages fail *loudly* — a non-starting exe, a smoke test that never gets a
`/api/health` response — and they fail on your machine, not a user's. Nothing about that state is
dangerous.

And you always have Part 1's shape to fall back on: bump the version, tag, publish with source
archives only. That is a complete, legitimate release. The installer can wait for 2.1.0, or
indefinitely — nothing else in the project depends on it.

---

## Appendix A — File inventory

### Part 1

**New**

```
tests/test_version.py            Version/changelog sync test
```

**Modified**

```
web/paths.py               install_kind() — 'git' | 'source'
web/selfupdate.py          perform_app_update() dispatcher
web/api.py                 install_kind + version in /api/app-config;
                           /api/app-update → perform_app_update
web/static/js/launcher.js  Install-kind-aware update copy, in-page modal
GridVibe.bat               One banner line identifying the source channel
README.md                  Install / Run section; installer planned for 2.0.0
START_HERE/README.md       Entry point for ZIP downloaders
CONTRIBUTING.md            Pointer to this document; note the enforcing test
CHANGELOG.md               Unreleased entries, then the dated 1.2.0 section
docs/github_tags_and_releases.md               Superseded banner
docs/installer_and_release_plan_2026-07-20.md  Superseded banner
```

### Part 2

**New**

```
scripts/check_version.py                      Tag-vs-version guard (release CI)
scripts/release_notes.py                      CHANGELOG → release notes
requirements-packaging.txt                    Core freeze deps (pulls requirements-desktop.txt)
requirements-packaging-voice.txt              Voice freeze deps
packaging/pyinstaller/entry.py                Frozen entry point + argv dispatch
packaging/pyinstaller/gridvibe.spec           One spec, two editions
packaging/pyinstaller/make_version_info.py    Windows version resource generator
packaging/pyinstaller/version_info.txt        (generated)
packaging/pyinstaller/README.md               Build prerequisites and troubleshooting
packaging/windows/gridvibe.iss                Inno Setup script, both editions
packaging/windows/build_app.ps1               PyInstaller build
packaging/windows/build_installer.ps1         ISCC build
packaging/windows/smoke_test.ps1              Frozen-build smoke test
packaging/windows/README.md                   Maintainer build steps
.github/workflows/release.yml                 Tag-triggered build + draft release
```

**Modified**

```
web/paths.py             DATA_DIR / LOG_DIR / MODELS_DIR / resource_path / data_path /
                         ensure_data_dirs / migrate_legacy_state; install_kind gains 'frozen'
web/config.py            CONFIG_PATH → data_path; DEFAULT_CONFIG_PATH → resource_path
web/saved_sessions.py    SAVED_SESSIONS_PATH → data_path
web/runtime_state.py     RUNTIME_STATE_PATH → data_path
web/secrets.py           ENCRYPTION_KEY_PATH → data_path
web/hostkeys.py          KNOWN_HOSTS_PATH → data_path
web/agents.py            AGENT_REGISTRY_PATH → resource_path
web/app.py               Explicit template_folder and static_folder
web/api.py               docs_images → resource_path
web/selfupdate.py        'frozen' branch + the installed-build message (Step C3).
                         Release check / download / verify / apply is Stage G → 2.0.1
web/voice.py             download_root=MODELS_DIR; frozen-aware Vosk spawn;
                         prefetch_default_model()
web/webview_launcher.py  Icon via resource_path; comment on restart-branch ordering
web/static/js/launcher.js  Confirm / progress / retry flow is Stage G → 2.0.1
services/vosk_service.py Frozen-aware sys.path bootstrap and model directory
main.py                  LOG_DIR from web.paths; --config default via web.config
requirements.txt         Pin simple-websocket + wsproto
.python-version          Reconcile with the packaging interpreter (3.12)
README.md                Installer download section
```

---

## Appendix B — Command reference

**Everyday development**

```powershell
git switch main; git pull --ff-only
git switch -c my-change
make check                        # ruff + unittest
git add -A; git status; git diff --staged
git commit -m "Describe the change"
git push -u origin my-change
gh pr create --base main --fill
gh pr merge --squash --delete-branch
```

**Release (both parts)**

```powershell
git switch main && git pull --ff-only
#   bump gridvibe_version.py, pyproject.toml, CHANGELOG.md
make check
git add gridvibe_version.py pyproject.toml CHANGELOG.md
git commit -m "Release 1.2.0"
git push origin main
#   wait for green CI on this commit
git tag -a v1.2.0 -m "GridVibe 1.2.0"
git push origin v1.2.0                          # tags need their own push
gh release create v1.2.0 --title "GridVibe 1.2.0" --notes-file RELEASE_NOTES.md --latest
```

**Tag inspection**

```powershell
git tag -l                              # list tags
git show v1.2.0                         # tag message and commit
git log --oneline v1.2.0..HEAD          # what has landed since the release
git switch --detach v1.2.0              # inspect released code (detached HEAD)
git switch main                         # back to normal
```

**Part 2 — local packaging (Windows)**

```powershell
.\packaging\windows\build_app.ps1                  # core frozen app  -> dist\core\GridVibe\
.\packaging\windows\build_app.ps1 -Voice           # voice frozen app -> dist\voice\GridVibe\
.\packaging\windows\smoke_test.ps1 -Edition core
.\packaging\windows\build_installer.ps1            # -> dist\GridVibe-2.0.0-setup.exe
.\packaging\windows\build_installer.ps1 -Voice     # -> dist\GridVibe-2.0.0-voice-setup.exe
```

**Part 2 — CI on demand (no tag, no release)**

```
Actions → Release → Run workflow → pick a branch
   ⇒ builds + smoke-tests both editions, uploads installers as workflow artifacts
```

**Part 2 — installer switches**

```
GridVibe-2.0.0-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
GridVibe-2.0.0-setup.exe /SILENT                     # progress bar, no questions
```

**Part 2 — frozen-app flags**

```
GridVibe.exe                          # native window (browser fallback)
GridVibe.exe --mode browser           # browser mode
GridVibe.exe --vosk-service           # run as the Vosk WebSocket service (internal)
GridVibe.exe --prefetch-voice-model   # download the configured model, then exit
```

**State directory override (both parts, source mode)**

```powershell
$env:GRIDVIBE_DATA_DIR = "$env:TEMP\gv-clean"     # throwaway state       (Part 2)
$env:GRIDVIBE_DATA_DIR = "$env:APPDATA\GridVibe"  # installed build's state (Part 2)
Remove-Item Env:\GRIDVIBE_DATA_DIR                 # back to in-repo state
```
