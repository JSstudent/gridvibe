"""The launcher's per-row Override checkbox, executed in Node.

The real row helpers are sliced out of ``launcher.js`` and run against stub
elements, so what is asserted is the markup the row renders, what a sync does
to the box, what a launch or preset save reads from it, and what the enable
dialog decides -- not the spelling of any of it.

The rules pinned here: the box sits wherever the MCP box does and uses the same
``agentMcpSupported`` predicate; it is disabled and cleared while MCP is
unticked, and cleared when the row's agent changes; ticking it only asks,
through the shared in-page dialog, and only a Confirm on a row that still
offers it ticks it; and the row states ``agent_mcp_override`` only beside
``agent_mcp`` on an agent row.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_JS = PROJECT_ROOT / "web" / "static" / "js" / "launcher.js"
LAUNCHER_CSS = PROJECT_ROOT / "web" / "static" / "css" / "launcher.css"
SHARED_JS = PROJECT_ROOT / "web" / "static" / "js" / "shared.js"

NODE = shutil.which("node")


def _slice(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


def _launcher_helpers() -> str:
    source = LAUNCHER_JS.read_text(encoding="utf-8")
    # The warning copy lives in shared.js, where the pane menu reads it too.
    shared = SHARED_JS.read_text(encoding="utf-8")
    return "\n".join(
        [
            _slice(
                shared,
                "    const AGENT_MCP_OVERRIDE_CONFIRM = {",
                "    function initGenericConfirmModal() {",
            ),
            _slice(
                source,
                "    function agentMcpSupported(agentValue) {",
                "    function normalizeTerminalCommandUi(terminal) {",
            ),
            _slice(
                source,
                "    function normalizeTerminalCommandUi(terminal) {",
                "    function startupSelectValue(mode, agentSelection) {",
            ),
            _slice(
                source,
                "    function startupSelectValue(mode, agentSelection) {",
                "    function renderStartupModeOptions(commandUi) {",
            ),
            _slice(
                source,
                "    function syncTerminalAgentMcpState(row, commandMode, selectedAgent) {",
                "    function _resetAgentOptionLabels(select) {",
            ),
        ]
    )


HARNESS_HEAD = r"""
const AGENT_OPTIONS = [
    { value: 'claude', mcp_supported: true, mcp_description: 'Claude tools' },
    { value: 'codex', mcp_supported: true, mcp_description: 'Codex tools' },
    { value: 'aider', mcp_supported: false }
];
function getKnownAgentValues() { return AGENT_OPTIONS.map(option => option.value); }
function agentAutoModeFlag() { return ''; }
function getTerminalCommandMode(row) { return row.dataset.commandMode; }
function escHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

const dialogs = [];
let answerDialog = null;
function openGenericConfirmModal(options) {
    dialogs.push(options);
    return new Promise(resolve => { answerDialog = resolve; });
}

function el({ classes = [], checked = false, disabled = false, dataset = {}, children = {} } = {}) {
    const set = new Set(classes);
    return {
        checked,
        disabled,
        isConnected: true,
        dataset: { ...dataset },
        textContent: '',
        attrs: {},
        classList: {
            add: name => set.add(name),
            remove: name => set.delete(name),
            contains: name => set.has(name),
            toggle(name, force) {
                const on = force === undefined ? !set.has(name) : Boolean(force);
                if (on) set.add(name); else set.delete(name);
                return on;
            }
        },
        classes: () => Array.from(set).sort(),
        setAttribute(name, value) { this.attrs[name] = value; },
        querySelector: selector => children[selector] || null
    };
}

/* A row as the launcher renders it for an agent whose MCP box is shown. */
function makeRow({ mode = 'agent', agent = 'claude', mcp = false, override = false,
                   overrideAgent = agent, mcpShown = true } = {}) {
    const mcpBox = el({ checked: mcp });
    const overrideBox = el({ checked: override, disabled: !mcp });
    const mcpField = el({
        classes: mcpShown ? [] : ['hidden'],
        children: { '.t-agent-mcp': mcpBox, '.tip-btn': el() }
    });
    const overrideField = el({
        classes: [...(mcpShown ? [] : ['hidden']), ...(mcp ? [] : ['is-disabled'])],
        dataset: { agent: overrideAgent },
        children: { '.t-agent-mcp-override': overrideBox, '.tip-btn': el() }
    });
    const select = el();
    select.value = mode === 'agent' ? `agent:${agent}` : mode;
    const nodes = {
        '.startup-mode-select': select,
        '.t-agent-mcp-field': mcpField,
        '.t-agent-mcp-help': el(),
        '.t-agent-mcp': mcpBox,
        '.t-agent-mcp-override-field': overrideField,
        '.t-agent-mcp-override-help': el(),
        '.t-agent-mcp-override': overrideBox,
        '.t-agent-custom': el(),
        '.t-agent-auto-mode': el()
    };
    return {
        dataset: { commandMode: mode },
        querySelector: selector => nodes[selector] || null,
        select, mcpBox, overrideBox, mcpField, overrideField
    };
}

function boxState(row) {
    return {
        mcp: row.mcpBox.checked,
        override: row.overrideBox.checked,
        overrideDisabled: row.overrideBox.disabled,
        overrideHidden: row.overrideField.classList.contains('hidden'),
        overrideGreyed: row.overrideField.classList.contains('is-disabled')
    };
}

function sync(row) {
    const selection = parseStartupSelection(row.select.value);
    syncTerminalAgentMcpState(row, selection.mode, selection.mode === 'agent' ? selection.agent : '');
}

function overrideInput(html) {
    const match = html.match(/<input class="t-agent-mcp-override"[^>]*>/);
    return match ? match[0] : null;
}
function overrideLabel(html) {
    const match = html.match(/<label class="check-field t-agent-mcp-override-field[^>]*>/);
    return match ? match[0] : null;
}
function mcpLabel(html) {
    const match = html.match(/<label class="check-field t-agent-mcp-field[^>]*>/);
    return match ? match[0] : null;
}
"""

HARNESS_TAIL = r"""
async function main() {
    const out = {};

    /* ---- markup ---- */
    const render = ui => renderTerminalAgentMcpFields({ mode: 'agent', agentSelection: 'claude', ...ui });
    const off = render({ agentMcp: false, agentMcpOverride: false });
    const on = render({ agentMcp: true, agentMcpOverride: true });
    const staleGrant = render({ agentMcp: false, agentMcpOverride: true });
    const noTools = render({ agentSelection: 'aider', agentMcp: false, agentMcpOverride: false });
    const notAgent = render({ mode: 'terminal', agentSelection: '', agentMcp: false });
    out.markup = {
        offInput: overrideInput(off),
        offLabel: overrideLabel(off),
        onInput: overrideInput(on),
        onLabel: overrideLabel(on),
        staleInput: overrideInput(staleGrant),
        noToolsLabel: overrideLabel(noTools),
        noToolsMcpLabel: mcpLabel(noTools),
        notAgentLabel: overrideLabel(notAgent),
        overrideAfterMcp: on.indexOf('t-agent-mcp-override-field') > on.indexOf('t-agent-mcp-field'),
        helpText: (on.match(/<div class="inline-tip t-agent-mcp-override-help">([^<]*)<\/div>/) || [])[1] || ''
    };

    /* ---- preset -> form ---- */
    const preset = extra => normalizeTerminalCommandUi({
        startup_mode: 'agent', agent_selection: 'claude', agent_mcp: true, ...extra
    });
    out.normalize = {
        granted: preset({ agent_mcp_override: true }).agentMcpOverride,
        absent: preset({}).agentMcpOverride,
        truthyString: preset({ agent_mcp_override: 'true' }).agentMcpOverride,
        withoutMcp: preset({ agent_mcp: false, agent_mcp_override: true }).agentMcpOverride,
        unsupportedAgent: preset({ agent_selection: 'aider', agent_mcp_override: true }).agentMcpOverride
    };

    /* ---- sync ---- */
    let row = makeRow({ mcp: false, override: true });
    sync(row);
    out.syncMcpOff = boxState(row);

    row = makeRow({ mcp: true, override: true });
    sync(row);
    out.syncMcpOn = boxState(row);

    row = makeRow({ mcp: true, override: true });
    row.mcpBox.checked = false;  // the user unticks MCP
    sync(row);
    const afterUntick = boxState(row);
    row.mcpBox.checked = true;   // and ticks it again
    sync(row);
    out.syncUntickThenRetick = { afterUntick, afterRetick: boxState(row) };

    row = makeRow({ mcp: true, override: true });
    row.select.value = 'agent:aider';
    sync(row);
    out.syncAgentWithoutTools = boxState(row);

    row = makeRow({ mcp: true, override: true });
    row.select.value = 'agent:codex';
    sync(row);
    out.syncOtherAgentWithTools = boxState(row);

    /* ---- form -> launch payload / preset ---- */
    const read = r => readRowAgentMcpFlags(r, r.dataset.commandMode);
    out.read = {
        both: read(makeRow({ mcp: true, override: true })),
        mcpOnly: read(makeRow({ mcp: true, override: false })),
        staleOverride: read(makeRow({ mcp: false, override: true })),
        unsupported: read(makeRow({ agent: 'aider', mcp: true, override: true })),
        notAgent: read(makeRow({ mode: 'terminal', mcp: true, override: true }))
    };

    /* ---- leaving agent mode ---- */
    row = makeRow({ mcp: true, override: true });
    resetTerminalCommandOnModeChange(row, 'terminal');
    out.leaveAgent = boxState(row);

    /* ---- enable dialog ---- */
    async function tick(answer, meanwhile) {
        dialogs.length = 0;
        const r = makeRow({ mcp: true, override: false });
        r.overrideBox.checked = true;   // the browser ticks it on click
        const pending = handleAgentMcpOverrideToggle(r.overrideBox);
        const whileOpen = r.overrideBox.checked;
        if (meanwhile) meanwhile(r);
        answerDialog(answer);
        await pending;
        return { whileOpen, after: r.overrideBox.checked, dialogs: dialogs.slice() };
    }
    out.confirm = await tick(true);
    out.decline = await tick(false);
    out.confirmAfterMcpUntick = await tick(true, r => {
        r.mcpBox.checked = false;
        sync(r);
    });
    out.confirmAfterRebuild = await tick(true, r => { r.overrideBox.isConnected = false; });
    out.confirmAfterAgentSwitch = await tick(true, r => {
        r.select.value = 'agent:codex';
        sync(r);
    });
    out.confirmAfterMcpOffOn = await tick(true, r => {
        r.mcpBox.checked = false;
        sync(r);
        r.mcpBox.checked = true;
        sync(r);
    });
    out.confirmAfterSameAgentResync = await tick(true, r => { sync(r); });

    dialogs.length = 0;
    row = makeRow({ mcp: true, override: true });
    row.overrideBox.checked = false;   // unticking
    await handleAgentMcpOverrideToggle(row.overrideBox);
    out.untick = { dialogs: dialogs.length, after: row.overrideBox.checked };

    console.log(JSON.stringify(out));
}
main().catch(error => { console.error(error); process.exit(1); });
"""


def _run() -> dict:
    script = HARNESS_HEAD + _launcher_helpers() + HARNESS_TAIL
    with TemporaryDirectory() as workdir:
        path = Path(workdir) / "harness.js"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [NODE, str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "Node.js is required for launcher override tests")
class LauncherOverrideToggleTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _run()

    # -- markup -------------------------------------------------------------

    def test_override_renders_beside_mcp_and_is_disabled_until_mcp_is_ticked(self):
        markup = self.out["markup"]
        self.assertTrue(markup["overrideAfterMcp"])
        self.assertIn(" disabled", markup["offInput"])
        self.assertNotIn(" checked", markup["offInput"])
        self.assertIn("is-disabled", markup["offLabel"])
        self.assertNotIn("hidden", markup["offLabel"])

    def test_a_saved_grant_renders_ticked_and_usable(self):
        markup = self.out["markup"]
        self.assertIn(" checked", markup["onInput"])
        self.assertNotIn(" disabled", markup["onInput"])
        self.assertNotIn("is-disabled", markup["onLabel"])
        self.assertIn('data-agent="claude"', markup["onLabel"])

    def test_a_grant_without_mcp_never_renders_ticked(self):
        self.assertNotIn(" checked", self.out["markup"]["staleInput"])

    def test_override_uses_the_mcp_predicate_for_visibility(self):
        markup = self.out["markup"]
        self.assertIn("hidden", markup["noToolsMcpLabel"])
        self.assertIn("hidden", markup["noToolsLabel"])
        self.assertIn("hidden", markup["notAgentLabel"])

    def test_the_help_states_the_consequence(self):
        help_text = self.out["markup"]["helpText"]
        self.assertIn("without asking first", help_text)
        self.assertIn("cannot touch its own pane", help_text)

    # -- preset -> form -----------------------------------------------------

    def test_a_preset_brings_the_grant_back_only_as_a_stated_true_beside_mcp(self):
        normalize = self.out["normalize"]
        self.assertIs(normalize["granted"], True)
        self.assertIs(normalize["absent"], False)
        self.assertIs(normalize["truthyString"], False)
        self.assertIs(normalize["withoutMcp"], False)
        self.assertIs(normalize["unsupportedAgent"], False)

    # -- sync ---------------------------------------------------------------

    def test_mcp_unticked_disables_and_clears_the_override(self):
        state = self.out["syncMcpOff"]
        self.assertFalse(state["override"])
        self.assertTrue(state["overrideDisabled"])
        self.assertTrue(state["overrideGreyed"])
        self.assertFalse(state["overrideHidden"])

    def test_mcp_ticked_keeps_a_granted_override(self):
        state = self.out["syncMcpOn"]
        self.assertTrue(state["override"])
        self.assertFalse(state["overrideDisabled"])
        self.assertFalse(state["overrideGreyed"])

    def test_unticking_mcp_loses_the_grant_for_good(self):
        """An override with no tools is meaningless state; re-ticking MCP
        enables the box again but does not bring the old answer back."""
        states = self.out["syncUntickThenRetick"]
        self.assertFalse(states["afterUntick"]["override"])
        self.assertTrue(states["afterUntick"]["overrideDisabled"])
        self.assertFalse(states["afterRetick"]["override"])
        self.assertFalse(states["afterRetick"]["overrideDisabled"])

    def test_an_agent_without_tools_hides_and_clears_both_boxes(self):
        state = self.out["syncAgentWithoutTools"]
        self.assertFalse(state["mcp"])
        self.assertFalse(state["override"])
        self.assertTrue(state["overrideHidden"])
        self.assertTrue(state["overrideDisabled"])

    def test_switching_to_another_agent_clears_the_consent(self):
        state = self.out["syncOtherAgentWithTools"]
        self.assertTrue(state["mcp"])
        self.assertFalse(state["override"])
        self.assertFalse(state["overrideDisabled"])

    def test_leaving_agent_mode_clears_the_override(self):
        self.assertFalse(self.out["leaveAgent"]["override"])

    # -- form -> launch payload / preset ------------------------------------

    def test_the_row_states_the_grant_only_beside_mcp_on_an_agent_row(self):
        read = self.out["read"]
        self.assertEqual(read["both"], {"agent_mcp": True, "agent_mcp_override": True})
        self.assertEqual(read["mcpOnly"], {"agent_mcp": True, "agent_mcp_override": False})
        self.assertEqual(
            read["staleOverride"], {"agent_mcp": False, "agent_mcp_override": False}
        )
        self.assertEqual(
            read["unsupported"], {"agent_mcp": False, "agent_mcp_override": False}
        )
        self.assertEqual(read["notAgent"], {"agent_mcp": False, "agent_mcp_override": False})

    # -- enable dialog ------------------------------------------------------

    def test_ticking_asks_in_the_shared_dialog_and_confirm_ticks_it(self):
        outcome = self.out["confirm"]
        self.assertFalse(outcome["whileOpen"])
        self.assertTrue(outcome["after"])
        self.assertEqual(len(outcome["dialogs"]), 1)
        dialog = outcome["dialogs"][0]
        self.assertTrue(dialog["danger"])
        self.assertIn(
            "close, move, relaunch, re-mode and clear panes it did not create",
            dialog["copy"],
        )
        self.assertIn("without asking first", dialog["copy"])
        self.assertIn("It still cannot touch its own pane.", dialog["copy"])
        # §2.6: the honest cost -- one tool call instead of a hand-built request.
        self.assertIn("one tool call", dialog["note"])

    def test_declining_leaves_it_unticked(self):
        outcome = self.out["decline"]
        self.assertFalse(outcome["whileOpen"])
        self.assertFalse(outcome["after"])

    def test_a_confirm_after_mcp_was_unticked_ticks_nothing(self):
        self.assertFalse(self.out["confirmAfterMcpUntick"]["after"])

    def test_a_confirm_for_a_rebuilt_row_ticks_nothing(self):
        self.assertFalse(self.out["confirmAfterRebuild"]["after"])

    def test_a_confirm_given_for_another_agent_ticks_nothing(self):
        """The row switched CLI while the dialog was open: the box is enabled
        again for the new agent, but the answer was for the old one."""
        self.assertFalse(self.out["confirmAfterAgentSwitch"]["after"])

    def test_a_confirm_across_an_mcp_untick_and_retick_ticks_nothing(self):
        self.assertFalse(self.out["confirmAfterMcpOffOn"]["after"])

    def test_an_unrelated_resync_does_not_withdraw_the_confirmation(self):
        self.assertTrue(self.out["confirmAfterSameAgentResync"]["after"])

    def test_unticking_needs_no_dialog(self):
        self.assertEqual(self.out["untick"], {"dialogs": 0, "after": False})


class LauncherOverrideWiringTestCase(unittest.TestCase):
    """Markup hooks the behavioural harness cannot reach."""

    def test_row_binding_and_draft_collection_use_the_helpers(self):
        source = LAUNCHER_JS.read_text(encoding="utf-8")
        bind = _slice(
            source,
            "    function bindTerminalRowInteractions() {",
            "    function buildTerminalRows(",
        )
        self.assertIn("'.t-agent-mcp-override'", bind)
        self.assertIn("handleAgentMcpOverrideToggle(", bind)
        self.assertIn("syncTerminalAgentMcpOverrideState(", bind)

        collect = _slice(
            source,
            "    function collectTerminalDrafts() {",
            "    function renderCountOptions()",
        )
        self.assertIn("readRowAgentMcpFlags(", collect)
        self.assertIn("agent_mcp_override:", collect)

        self.assertIn("${renderTerminalAgentMcpFields(commandUi)}", source)

        handler = _slice(
            source,
            "    async function handleAgentMcpOverrideToggle(checkbox) {",
            "    function renderTerminalAgentMcpFields(commandUi) {",
        )
        self.assertIn("openGenericConfirmModal(", handler)
        self.assertNotIn("window.confirm", handler)

    def test_disabled_override_has_a_greyed_style(self):
        css = LAUNCHER_CSS.read_text(encoding="utf-8")
        self.assertIn(".t-agent-mcp-override-field.is-disabled", css)


if __name__ == "__main__":
    unittest.main()
