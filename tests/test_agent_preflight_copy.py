"""Behavioral coverage for the agent preflight's install-command surface.

The real ``renderAgentPreflight`` and ``copyAgentInstallCommand`` are sliced out
of ``launcher.js`` and executed in Node against a stub row, so what is asserted
is the markup the launcher actually produces and what the click actually writes
-- not the spelling of either function.

Two defects are pinned here. A long install URL used to be clipped off the right
of the card (Hermes' 52-character host has no break opportunity in it, and the
box is a grid whose single anonymous item could not shrink below its min-content
width), and there was no way to take the command off the card at all short of
selecting it by hand.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = PROJECT_ROOT / "web" / "static" / "js"
LAUNCHER_JS = STATIC_JS / "launcher.js"
LAUNCHER_CSS = PROJECT_ROOT / "web" / "static" / "css" / "launcher.css"

NODE = shutil.which("node")

HERMES_WINDOWS_INSTALL = "iex (irm https://hermes-agent.nousresearch.com/install.ps1)"


def _slice(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


RENDER_HARNESS = """
function escHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
function _clearAgentStatusClasses() {}
function _resetAgentOptionLabels() {}

function stubElement(extra) {
    const classes = new Set();
    return Object.assign({
        className: '',
        textContent: '',
        innerHTML: '',
        title: '',
        classList: {
            add: name => classes.add(name),
            remove: name => classes.delete(name),
            contains: name => classes.has(name)
        }
    }, extra || {});
}

const select = stubElement({
    options: [{ dataset: {}, textContent: '' }],
    selectedIndex: 0
});
const disclosure = stubElement({ open: false });
const summary = stubElement();
const summaryLabel = stubElement();
const copy = stubElement();
const nodes = {
    '.startup-mode-select': select,
    '.agent-preflight-disclosure': disclosure,
    '.agent-preflight-summary': summary,
    '.agent-preflight-summary-label': summaryLabel,
    '.agent-preflight-copy': copy
};
const row = { querySelector: selector => nodes[selector] || null };
"""

RENDER_TAIL = """
renderAgentPreflight(row, JSON.parse(process.argv[2]));
console.log(JSON.stringify({ html: copy.innerHTML, title: select.title }));
"""

CLICK_HARNESS = """
const spec = JSON.parse(process.argv[2]);
const written = [];
const notices = [];
const timers = [];
const agentInstallCopyTimerState = new WeakMap();
const classes = new Set();
async function copyTextToClipboard(text) {
    written.push(String(text));
    return spec.copySucceeds;
}
function showGridVibeNotice(text, type) { notices.push({ text, type }); }
setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
const button = {
    dataset: { installCommand: spec.command },
    isConnected: spec.connected,
    classList: {
        add: name => classes.add(name),
        remove: name => classes.delete(name),
        contains: name => classes.has(name)
    }
};
"""

CLICK_TAIL = """
copyAgentInstallCommand(button).then(() => {
    const copiedImmediately = classes.has('is-copied');
    timers.forEach(entry => entry.fn());
    console.log(JSON.stringify({
        written,
        notices,
        copiedImmediately,
        copiedAfterTimer: classes.has('is-copied'),
        timerCount: timers.length,
        timerDelay: timers.length ? timers[0].ms : null
    }));
});
"""


def _run_node(script: str, argument: str) -> dict:
    with TemporaryDirectory() as workdir:
        script_path = Path(workdir) / "harness.js"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [NODE, str(script_path), argument],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "Node.js is required for agent preflight copy tests")
class AgentPreflightInstallCommandTestCase(unittest.TestCase):
    """``renderAgentPreflight`` executed against a stub row."""

    def _render(self, payload: dict) -> dict:
        launcher_js = LAUNCHER_JS.read_text(encoding="utf-8")
        icon = _slice(
            launcher_js,
            "    const AGENT_INSTALL_COPY_ICON =",
            "\n\n",
        )
        render = _slice(
            launcher_js,
            "    function renderAgentPreflight(row, payload) {",
            "    async function copyAgentInstallCommand(button) {",
        )
        script = RENDER_HARNESS + icon + "\n" + render + RENDER_TAIL
        return _run_node(script, json.dumps(payload))

    def _hermes_payload(self) -> dict:
        return {
            "status": "installed",
            "status_label": "Installed",
            "message": "Hermes Agent is available in cmd.",
            "target": {"label": "cmd"},
            "install": {
                "label": "Install script",
                "command": HERMES_WINDOWS_INSTALL,
                "manual_only": False,
            },
            "warning": "The installer bootstraps Python, Node.js, ripgrep, and ffmpeg.",
            "missing_prerequisites": [],
        }

    def test_install_command_renders_whole_and_in_its_own_row(self):
        """The command that clipped is emitted complete, in an element of its
        own -- not as a run of inline text the grid could not shrink."""
        rendered = self._render(self._hermes_payload())["html"]

        self.assertIn(
            '<code class="agent-preflight-command">'
            + HERMES_WINDOWS_INSTALL
            + "</code>",
            rendered,
        )
        # Every row is a real element, so each can be given min-width: 0.
        self.assertNotIn("<br>", rendered)
        self.assertIn('class="agent-preflight-line agent-preflight-headline"', rendered)
        self.assertIn("Target: <code>cmd</code>", rendered)

    def test_copy_button_carries_the_exact_command_on_the_label_row(self):
        """The button rides on the label's row (so it lands on the box's right
        content edge) and carries the command it will copy, rather than reading
        it back out of the rendered text."""
        rendered = self._render(self._hermes_payload())["html"]

        head_start = rendered.index('class="agent-preflight-install-head"')
        head = rendered[head_start:rendered.index("</div>", head_start)]
        self.assertIn("Install script:", head)
        self.assertIn('class="agent-preflight-copy-btn"', head)
        self.assertIn('data-install-command="' + HERMES_WINDOWS_INSTALL + '"', head)
        self.assertIn('onclick="copyAgentInstallCommand(this)"', head)
        # Guardrail 7: a stroke-style currentColor SVG, never a glyph.
        self.assertIn('stroke="currentColor"', head)

    def test_no_install_command_renders_no_copy_button(self):
        payload = self._hermes_payload()
        payload["install"] = {"label": "", "command": "", "manual_only": False}
        rendered = self._render(payload)["html"]

        self.assertNotIn("agent-preflight-copy-btn", rendered)
        self.assertNotIn("agent-preflight-install", rendered)

    def test_agent_preflight_rows_may_shrink_and_wrap(self):
        """The CSS half of the clipping fix: a grid item's default min-width is
        its min-content, which an unbreakable URL pushes past the pane."""
        css = LAUNCHER_CSS.read_text(encoding="utf-8")

        rows = css[
            css.index(".agent-preflight-copy > * {"):
            css.index(".agent-preflight-install {")
        ]
        self.assertIn("min-width: 0;", rows)
        self.assertIn("overflow-wrap: anywhere;", rows)
        # The command itself is what actually has to break mid-token.
        code_rule = css[
            css.index(".agent-preflight-copy code {"):
            css.index(".agent-preflight-install {")
        ]
        self.assertIn("overflow-wrap: anywhere;", code_rule)


@unittest.skipUnless(NODE, "Node.js is required for agent preflight copy tests")
class AgentInstallCopyClickTestCase(unittest.TestCase):
    """``copyAgentInstallCommand`` executed against a stub button."""

    def _click(self, command: str, copy_succeeds: bool, connected: bool = True) -> dict:
        launcher_js = LAUNCHER_JS.read_text(encoding="utf-8")
        handler = _slice(
            launcher_js,
            "    async function copyAgentInstallCommand(button) {",
            "    function buildAgentPreflightPayload(row) {",
        )
        script = CLICK_HARNESS + handler + CLICK_TAIL
        return _run_node(
            script,
            json.dumps(
                {
                    "command": command,
                    "copySucceeds": copy_succeeds,
                    "connected": connected,
                }
            ),
        )

    def test_a_click_copies_the_command_and_confirms_on_the_button(self):
        outcome = self._click(HERMES_WINDOWS_INSTALL, copy_succeeds=True)

        self.assertEqual(outcome["written"], [HERMES_WINDOWS_INSTALL])
        # Guardrail 8: a confirmed state is a class, never new markup, and a
        # copy is not an event the one global banner has to carry.
        self.assertTrue(outcome["copiedImmediately"])
        self.assertEqual(outcome["notices"], [])
        # The confirmation is transient and clears itself.
        self.assertEqual(outcome["timerCount"], 1)
        self.assertFalse(outcome["copiedAfterTimer"])

    def test_a_failed_copy_is_reported_and_never_looks_successful(self):
        outcome = self._click(HERMES_WINDOWS_INSTALL, copy_succeeds=False)

        self.assertEqual(outcome["written"], [HERMES_WINDOWS_INSTALL])
        self.assertFalse(outcome["copiedImmediately"])
        self.assertEqual(len(outcome["notices"]), 1)
        self.assertEqual(outcome["notices"][0]["type"], "error")
        self.assertIn("Could not copy", outcome["notices"][0]["text"])
        self.assertEqual(outcome["timerCount"], 0)

    def test_a_button_the_re_render_replaced_schedules_nothing(self):
        """The preflight rebuilds this box on every check, so the button that
        was clicked may already be detached when the write resolves."""
        outcome = self._click(
            HERMES_WINDOWS_INSTALL, copy_succeeds=True, connected=False
        )

        self.assertEqual(outcome["written"], [HERMES_WINDOWS_INSTALL])
        self.assertEqual(outcome["timerCount"], 0)
        self.assertFalse(outcome["copiedImmediately"])

    def test_an_empty_command_writes_nothing(self):
        outcome = self._click("", copy_succeeds=True)

        self.assertEqual(outcome["written"], [])
        self.assertEqual(outcome["notices"], [])


if __name__ == "__main__":
    unittest.main()
