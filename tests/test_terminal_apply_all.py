"""Behavioral coverage for Terminal Setup's "Same for all".

`terminal-apply-all.js` is DOM-free and require()-able, so what a follower
copies from Terminal 1 and what it keeps of its own is executed in Node rather
than asserted as source text. The launcher's wiring (the checkbox, the script
include) is pinned as markup at the end.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
MODULE_JS = ROOT / "web" / "static" / "js" / "terminal-apply-all.js"
INDEX_HTML = ROOT / "templates" / "index.html"

NODE = shutil.which("node")


def template_draft(**overrides):
    draft = {
        "title": "Builder",
        "directory": "src",
        "initial_command": "claude",
        "initial_command_mode": "agent",
        "startup_mode": "agent",
        "agent_selection": "claude",
        "custom_agent": "",
        "agent_auto_mode": True,
        "agent_mcp": True,
        "agent_mcp_override": True,
        "browser_tabs": [],
        "browser_active_tab": 0,
        "distribution": "",
        "use_wsl": False,
        "use_powershell": False,
        "tmux": True,
        "tmux_session": "work",
        "explorer_open_tabs": [],
        "explorer_git_pin_active": False,
        "explorer_git_pinned_path": "",
    }
    draft.update(overrides)
    return draft


def follower_draft(**overrides):
    draft = {
        "title": "Logs",
        "directory": "logs",
        "initial_command": "tail -f app.log",
        "initial_command_mode": "command",
        "startup_mode": "terminal",
        "agent_selection": "",
        "custom_agent": "",
        "agent_auto_mode": False,
        "agent_mcp": False,
        "agent_mcp_override": False,
        "tmux": False,
        "tmux_session": "",
    }
    draft.update(overrides)
    return draft


@unittest.skipUnless(NODE, "Node is required for the frontend behavior tests")
class TerminalApplyAllTests(unittest.TestCase):
    def _run(self, expression, *args):
        with TemporaryDirectory() as tmp:
            script = Path(tmp) / "run.js"
            script.write_text(
                "const api = require(process.argv[2]);\n"
                "const args = JSON.parse(process.argv[3]);\n"
                f"const result = ({expression})(...args);\n"
                "process.stdout.write(JSON.stringify(result));\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [NODE, str(script), str(MODULE_JS), json.dumps(list(args))],
                capture_output=True,
                text=True,
                check=True,
            )
        return json.loads(completed.stdout)

    def _follow(self, template, own, index=1):
        return self._run("api.followTemplate", template, own, index)

    def test_a_follower_runs_what_terminal_one_runs(self):
        result = self._follow(template_draft(), follower_draft())
        for key in (
            "directory",
            "startup_mode",
            "initial_command",
            "initial_command_mode",
            "agent_selection",
            "agent_auto_mode",
            "agent_mcp",
            "tmux",
        ):
            self.assertEqual(result[key], template_draft()[key], key)

    def test_a_follower_keeps_its_own_title(self):
        result = self._follow(template_draft(), follower_draft(title="Logs"))
        self.assertEqual(result["title"], "Logs")

    def test_an_untitled_follower_is_named_by_its_own_position(self):
        result = self._follow(template_draft(), follower_draft(title=""), 4)
        self.assertEqual(result["title"], "Terminal 5")

    def test_terminal_ones_tmux_session_name_is_never_copied(self):
        # The server refuses two panes on one tmux session; a follower with no
        # name of its own gets a fresh session at launch instead.
        result = self._follow(template_draft(tmux_session="work"), follower_draft(tmux_session=""))
        self.assertTrue(result["tmux"])
        self.assertEqual(result["tmux_session"], "")

    def test_a_follower_keeps_its_own_tmux_session_name(self):
        result = self._follow(template_draft(), follower_draft(tmux_session="db-shell"))
        self.assertEqual(result["tmux_session"], "db-shell")

    def test_a_kept_tmux_name_rides_unticked_when_terminal_one_has_tmux_off(self):
        result = self._follow(
            template_draft(tmux=False, tmux_session=""),
            follower_draft(tmux=True, tmux_session="db-shell"),
        )
        self.assertFalse(result["tmux"])
        self.assertEqual(result["tmux_session"], "db-shell")

    def test_override_mode_is_never_copied_or_kept(self):
        result = self._follow(
            template_draft(agent_mcp_override=True),
            follower_draft(startup_mode="agent", agent_mcp=True, agent_mcp_override=True),
        )
        self.assertTrue(result["agent_mcp"])
        self.assertFalse(result["agent_mcp_override"])

    def test_explorer_state_survives_only_on_the_same_folder(self):
        explorer = {
            "startup_mode": "explorer",
            "initial_command_mode": "explorer",
            "initial_command": "",
        }
        template = template_draft(
            directory="src",
            explorer_open_tabs=["main.py"],
            explorer_git_pin_active=True,
            explorer_git_pinned_path="pkg",
            **explorer,
        )
        same_folder = follower_draft(
            directory="src",
            explorer_open_tabs=["README.md"],
            explorer_git_pin_active=False,
            explorer_git_pinned_path="",
            explorer_theme="light",
            **explorer,
        )
        kept = self._follow(template, same_folder)
        self.assertEqual(kept["explorer_open_tabs"], ["README.md"])
        self.assertFalse(kept["explorer_git_pin_active"])
        self.assertEqual(kept["explorer_theme"], "light")

        moved = self._follow(template, dict(same_folder, directory="docs"))
        self.assertEqual(moved["directory"], "src")
        # Neither its own paths (relative to another folder) nor Terminal 1's.
        self.assertEqual(moved["explorer_open_tabs"], [])
        self.assertFalse(moved["explorer_git_pin_active"])
        self.assertEqual(moved["explorer_git_pinned_path"], "")
        self.assertEqual(moved["explorer_root_directory"], "")

    def test_a_follower_does_not_share_terminal_ones_lists(self):
        template = template_draft(browser_tabs=["http://a/"])
        result = self._run(
            "(template, own) => { const out = api.followTemplate(template, own, 1);"
            " out.browser_tabs.push('x'); return template.browser_tabs; }",
            template,
            follower_draft(),
        )
        self.assertEqual(result, ["http://a/"])

    def test_drafts_follow_only_within_the_count(self):
        drafts = [template_draft(), follower_draft(title="Two"), follower_draft(title="Three")]
        result = self._run("api.applyTemplateToDrafts", drafts, 2)
        self.assertEqual(result[0], drafts[0])
        self.assertEqual(result[1]["startup_mode"], "agent")
        self.assertEqual(result[1]["title"], "Two")
        # Past the count: kept as it was, for when the count grows again.
        self.assertEqual(result[2], drafts[2])

    def test_no_drafts_is_no_drafts(self):
        self.assertEqual(self._run("api.applyTemplateToDrafts", [], 4), [])


def _slice(source, start, end):
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


# The launcher's real "Same for all" functions and its form reader, run against
# stub rows. A row's draft is whatever the test gave it; an `invalid` row
# throws from the per-row reader the way a browser row with no URL does.
LAUNCHER_HARNESS_HEAD = r"""
const api = require(process.argv[2]);
const timers = new Map();
let timerSeq = 0;
const window = {
    GridVibeTerminalApplyAll: api,
    setTimeout(fn) { timerSeq += 1; timers.set(timerSeq, fn); return timerSeq; },
    clearTimeout(id) { timers.delete(id); }
};
function runTimers() {
    const pending = Array.from(timers.values());
    timers.clear();
    pending.forEach(fn => fn());
}
const MAX_SESSIONS = 0;
const DEFAULT_TERMINALS = [];
let terminalApplyAll = false;
let terminalApplyAllTimer = null;
let selectedCount = 0;
let rows = [];

function makeRow(draft, invalid = false) {
    const classes = new Set(['t-row']);
    const field = value => ({ value });
    const row = {
        draft, invalid, dataset: {},
        classList: {
            add: name => classes.add(name),
            contains: name => classes.has(name),
            toggle(name, force) {
                const on = force === undefined ? !classes.has(name) : Boolean(force);
                if (on) classes.add(name); else classes.delete(name);
                return on;
            }
        },
        querySelector(selector) {
            if (selector === '.t-title') return field(draft.title || '');
            if (selector === '.t-tmux-session') return field(draft.tmux_session || '');
            return null;
        },
        contains: target => target === row.input,
        input: { matches: () => false },
        replaceWith(next) { rows[rows.indexOf(row)] = next; }
    };
    return row;
}
function collectTerminalDraft(row) {
    if (row.invalid) throw new Error('Enter a browser URL before launching.');
    return JSON.parse(JSON.stringify(row.draft));
}
function terminalRowMarkup(draft) { return JSON.stringify(draft); }
function bindTerminalRowInteractions() {}
function syncTerminalCommandState() {}
function scheduleAgentPreflight() {}
function toggleTerminalRowFold() {}
const checkbox = { checked: false, disabled: false };
const badge = { textContent: '', title: '' };
const document = {
    querySelectorAll: () => rows.slice(),
    querySelector: selector => (selector.includes('.t-badge') ? badge : rows[0]),
    getElementById: () => checkbox,
    createElement: () => ({
        set innerHTML(markup) {
            this.content = { firstElementChild: makeRow(JSON.parse(markup)) };
        }
    })
};
function setUp(drafts) {
    rows = drafts.map(([draft, invalid]) => makeRow(draft, invalid));
    selectedCount = rows.length;
    terminalApplyAll = false;
    terminalApplyAllTimer = null;
    timers.clear();
}
function tick(on) {
    checkbox.checked = on;
    toggleTerminalApplyAll(checkbox);
}
function editTerminalOne(changes) {
    Object.assign(rows[0].draft, changes);
    scheduleTerminalTemplateSync({ type: 'input', target: rows[0].input });
}
function view(row) {
    return {
        follows: row.classList.contains('t-row-follows'),
        title: row.draft.title,
        initial_command: row.draft.initial_command,
        startup_mode: row.draft.startup_mode
    };
}
"""


@unittest.skipUnless(NODE, "Node is required for the frontend behavior tests")
class TerminalApplyAllLauncherTests(unittest.TestCase):
    def _run(self, body, drafts):
        source = (ROOT / "web" / "static" / "js" / "launcher.js").read_text(encoding="utf-8")
        script = "\n".join(
            [
                LAUNCHER_HARNESS_HEAD,
                _slice(source, "    function collectTerminalDrafts() {", "    function renderCountOptions() {"),
                _slice(
                    source,
                    "    function isTerminalFollowerRow(row) {",
                    "    /* Panel fold state is a per-browser view preference",
                ),
                f"setUp({json.dumps(drafts)});",
                f"process.stdout.write(JSON.stringify((() => {{ {body} }})()));",
            ]
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.js"
            path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(path), str(MODULE_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)
        return json.loads(completed.stdout)

    def test_a_follower_the_form_cannot_read_still_takes_the_copy(self):
        browser = follower_draft(title="Docs", startup_mode="browser", initial_command="")
        out = self._run(
            "tick(true); return { badge: badge.textContent, follower: view(rows[1]) };",
            [[template_draft(), False], [browser, True]],
        )
        self.assertEqual(out["badge"], "All")
        self.assertEqual(
            out["follower"],
            {"follows": True, "title": "Docs", "initial_command": "claude", "startup_mode": "agent"},
        )

    def test_a_launch_or_save_read_carries_an_edit_still_waiting_to_be_copied(self):
        out = self._run(
            "tick(true); editTerminalOne({ initial_command: 'codex', agent_selection: 'codex' });"
            " const drafts = collectTerminalDrafts();"
            " return { follower: drafts[1].initial_command, pending: timers.size };",
            [[template_draft(), False], [follower_draft(), False]],
        )
        self.assertEqual(out, {"follower": "codex", "pending": 0})

    def test_unticking_keeps_the_edit_still_waiting_to_be_copied(self):
        out = self._run(
            "tick(true); editTerminalOne({ initial_command: 'codex' }); tick(false);"
            " runTimers(); return view(rows[1]);",
            [[template_draft(), False], [follower_draft(), False]],
        )
        self.assertEqual(
            out,
            {"follows": False, "title": "Logs", "initial_command": "codex", "startup_mode": "agent"},
        )

    def test_the_coalesced_copy_lands_once_typing_settles(self):
        out = self._run(
            "tick(true); editTerminalOne({ initial_command: 'codex' }); runTimers();"
            " return { follower: view(rows[1]), timer: terminalApplyAllTimer };",
            [[template_draft(), False], [follower_draft(), False]],
        )
        self.assertEqual(out["follower"]["initial_command"], "codex")
        self.assertIsNone(out["timer"])


class TerminalApplyAllMarkupTests(unittest.TestCase):
    def test_launcher_ships_the_checkbox_and_the_module(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('id="terminalApplyAll"', html)
        self.assertIn("toggleTerminalApplyAll(this)", html)
        module = html.index("js/terminal-apply-all.js")
        launcher = html.index("js/launcher.js")
        self.assertLess(module, launcher)
        # It sits in the Terminal Setup card.
        card = re.search(r'id="terminalSetupCard".*?id="terminalRows"', html, re.DOTALL)
        self.assertIsNotNone(card)
        self.assertIn('id="terminalApplyAll"', card.group(0))


if __name__ == "__main__":
    unittest.main()
