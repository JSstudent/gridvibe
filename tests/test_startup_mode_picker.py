"""The launcher's Startup Mode picker, executed in Node.

`startup-mode-picker.js` draws a button and a listbox over the row's native
select, so each mode can wear its pane-header glyph and each agent its own mark
and brand colour. It is tested by running it: the real module against a small
DOM stub, and the launcher's real option markup sliced out of `launcher.js`.

What is pinned is that the select stays the one source of truth:

- **The list is the select's options.** Headings from optgroups, the hidden
  "Select agent…" placeholder never offered, a disabled mode shown but not
  choosable, and keys that skip both.
- **A pick is an option choice.** It writes `select.value` and dispatches the
  `change` the launcher already listens for -- and a re-pick of the current row
  dispatches nothing.
- **The button repaints from the select.** The selected row's mark and brand
  key, the preflight's status word (read off the option's own suffix) and its
  status class and tooltip.
- **The launcher's options say what each row wears.** Every plain mode names
  its icon, every agent its registry display name, glyph key and command.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = REPO_ROOT / "web" / "static" / "js"
PICKER_JS = STATIC_JS / "startup-mode-picker.js"
AGENT_GLYPHS_JS = STATIC_JS / "agent-glyphs.js"
LAUNCHER_JS = STATIC_JS / "launcher.js"

NODE = shutil.which("node")


def _slice(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


# Just enough DOM for the adapter: elements with class lists, attributes,
# listeners and innerHTML; a listbox whose rows are looked up by the index the
# module wrote into its markup; and a select built from option records.
DOM_STUB = r"""
function classList(initial) {
    const names = new Set(initial || []);
    return {
        add: (...n) => n.forEach(x => names.add(x)),
        remove: (...n) => n.forEach(x => names.delete(x)),
        contains: n => names.has(n),
        toggle: (n, on) => { (on === undefined ? !names.has(n) : on) ? names.add(n) : names.delete(n); },
        [Symbol.iterator]: () => names.values()
    };
}

function element(tag) {
    const el = {
        tagName: String(tag).toUpperCase(),
        attributes: {},
        dataset: {},
        style: {},
        listeners: {},
        hidden: false,
        disabled: false,
        title: '',
        innerHTML: '',
        isConnected: true,
        children: [],
        rows: new Map(),
        classList: classList(),
        set className(value) { this.classList = classList(String(value).split(/\s+/).filter(Boolean)); },
        setAttribute(name, value) { this.attributes[name] = String(value); },
        getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; },
        removeAttribute(name) { delete this.attributes[name]; },
        addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
        removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter(f => f !== fn); },
        dispatchEvent(event) { (this.listeners[event.type] || []).forEach(fn => fn(event)); return true; },
        insertAdjacentElement(_where, node) { inserted.push(node); return node; },
        appendChild(node) { this.children.push(node); return node; },
        contains(node) { return node === this; },
        focus() { focused = this; },
        getBoundingClientRect() { return { left: 20, top: 100, bottom: 130, width: 240 }; },
        get scrollHeight() { return 300; },
        get offsetWidth() { return 260; },
        querySelector(selector) {
            const byIndex = /^\[data-picker-index="(\d+)"\]$/.exec(selector);
            if (byIndex) {
                const index = byIndex[1];
                const id = new RegExp(`id="([^"]+)"\\s+role="option"\\s+data-picker-index="${index}"`).exec(this.innerHTML);
                if (!id) return null;
                if (!this.rows.has(index)) {
                    this.rows.set(index, { id: id[1], classList: classList(), scrollIntoView() {} });
                }
                return this.rows.get(index);
            }
            if (selector === '.startup-mode-option.is-active') {
                return [...this.rows.values()].find(row => row.classList.contains('is-active')) || null;
            }
            return null;
        }
    };
    return el;
}

let focused = null;
const inserted = [];
const document = {
    body: element('body'),
    createElement: tag => element(tag),
    addEventListener() {},
    removeEventListener() {}
};
const window = { innerWidth: 1280, innerHeight: 800, addEventListener() {}, removeEventListener() {} };
globalThis.document = document;
globalThis.window = window;
globalThis.Event = class { constructor(type) { this.type = type; } };

/* A select from option records: [{ value, text, dataset, disabled, hidden,
   group }]. Options sharing a `group` sit under one optgroup, in order. */
function makeSelect(records, value) {
    const select = element('select');
    const options = [];
    let group = null;
    records.forEach(record => {
        const option = element('option');
        option.value = record.value;
        option.textContent = record.text;
        Object.assign(option.dataset, record.dataset || {});
        option.disabled = Boolean(record.disabled);
        option.hidden = Boolean(record.hidden);
        options.push(option);
        if (record.group) {
            if (!group || group.label !== record.group) {
                group = element('optgroup');
                group.label = record.group;
                select.children.push(group);
            }
            group.children.push(option);
        } else {
            group = null;
            select.children.push(option);
        }
    });
    select.options = options;
    Object.defineProperty(select, 'value', {
        get() { const o = options[this.selectedIndex]; return o ? o.value : ''; },
        set(v) { this.selectedIndex = options.findIndex(o => o.value === v); }
    });
    Object.defineProperty(select, 'selectedIndex', {
        get() { return options.findIndex(o => o.selected); },
        set(i) { options.forEach((o, j) => { o.selected = j === i; }); }
    });
    select.value = value;
    return select;
}

const RECORDS = [
    { value: 'terminal', text: 'Terminal', dataset: { icon: 'terminal' } },
    { value: 'command', text: 'Initial Command', dataset: { icon: 'command' } },
    { value: 'browser', text: 'Browser', dataset: { icon: 'browser' }, disabled: true },
    { value: 'agent:', text: 'Select agent…', dataset: { icon: 'agent' }, hidden: true, group: 'Agent' },
    { value: 'agent:claude', text: 'Claude Code', group: 'Agent',
      dataset: { icon: 'agent', agent: 'claude', hint: 'claude', baseLabel: 'Claude Code' } },
    { value: 'agent:codex', text: 'OpenAI Codex CLI', group: 'Agent',
      dataset: { icon: 'agent', agent: 'codex', hint: 'codex', baseLabel: 'OpenAI Codex CLI' } }
];

function iconMarkup(entry) { return `<i data-glyph="${entry.icon}:${entry.agent}"></i>`; }

function listRows(list) {
    const rows = [];
    const row = /<div\s+class="startup-mode-option([^"]*)"[\s\S]*?data-picker-index="(\d+)"[\s\S]*?<span class="startup-mode-name"([^>]*)>([^<]*)<\/span>/g;
    let match;
    while ((match = row.exec(list.innerHTML)) !== null) {
        rows.push({ index: Number(match[2]), label: match[4], classes: match[1].trim(), name: match[3].trim() });
    }
    return rows;
}

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for the Startup Mode picker tests")
class StartupModePickerTestCase(unittest.TestCase):
    def _run_node(self, body: str, prelude: str = ""):
        script = (
            DOM_STUB
            + prelude
            + f"const picker = require({json.dumps(str(PICKER_JS))});\n"
            + body
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


class PickerListTestCase(StartupModePickerTestCase):
    def test_the_list_is_the_selects_options_with_its_groups(self):
        result = self._run_node(
            """
            const entries = picker.startupPickerEntries(makeSelect(RECORDS, 'terminal'));
            report(entries.map(e => e.kind === 'group' ? `# ${e.label}` : `${e.label}|${e.hint}|${e.disabled}`));
            """
        )
        # The placeholder exists only so a half-made draft has something to
        # be; it is never offered.
        self.assertEqual(
            result,
            [
                "Terminal||false",
                "Initial Command||false",
                "Browser||true",
                "# Agent",
                "Claude Code|claude|false",
                "OpenAI Codex CLI|codex|false",
            ],
        )

    def test_keys_skip_headings_and_disabled_rows_and_stop_at_the_ends(self):
        result = self._run_node(
            """
            const entries = picker.startupPickerEntries(makeSelect(RECORDS, 'terminal'));
            report({
                fromCommandDown: picker.stepPickerIndex(entries, 1, 1),
                fromClaudeUp: picker.stepPickerIndex(entries, 4, -1),
                pastEnd: picker.stepPickerIndex(entries, 5, 1),
                first: picker.edgePickerIndex(entries, false),
                last: picker.edgePickerIndex(entries, true)
            });
            """
        )
        self.assertEqual(result["fromCommandDown"], 4)
        self.assertEqual(result["fromClaudeUp"], 1)
        self.assertEqual(result["pastEnd"], 5)
        self.assertEqual(result["first"], 0)
        self.assertEqual(result["last"], 5)

    def test_the_placeholder_is_still_what_the_button_shows_when_selected(self):
        result = self._run_node(
            """
            report(picker.startupPickerSelected(makeSelect(RECORDS, 'agent:')).label);
            """
        )
        self.assertEqual(result, "Select agent…")


class PickerAdapterTestCase(StartupModePickerTestCase):
    def test_enhancing_hides_the_select_and_paints_the_selected_row(self):
        result = self._run_node(
            """
            const select = makeSelect(RECORDS, 'agent:claude');
            const trigger = picker.enhance(select, { iconMarkup });
            report({
                hidden: select.hidden,
                ariaHidden: select.getAttribute('aria-hidden'),
                inserted: inserted[0] === trigger,
                html: trigger.innerHTML,
                again: picker.enhance(select, { iconMarkup }) === trigger
            });
            """
        )
        self.assertTrue(result["hidden"])
        self.assertEqual(result["ariaHidden"], "true")
        self.assertTrue(result["inserted"])
        self.assertTrue(result["again"])
        self.assertIn('data-glyph="agent:claude"', result["html"])
        self.assertIn('<span class="startup-mode-name" data-agent="claude">Claude Code</span>', result["html"])
        self.assertNotIn("startup-mode-status", result["html"])

    def test_the_button_follows_the_preflight_written_to_the_select(self):
        result = self._run_node(
            """
            const select = makeSelect(RECORDS, 'agent:codex');
            const trigger = picker.enhance(select, { iconMarkup });
            select.options[5].textContent = 'OpenAI Codex CLI · Installed';
            select.classList.add('status-installed');
            select.title = 'codex is installed';
            picker.sync(select);
            const painted = {
                html: trigger.innerHTML,
                classes: [...trigger.classList],
                title: trigger.title
            };
            select.options[5].textContent = 'OpenAI Codex CLI';
            select.classList.remove('status-installed');
            select.title = '';
            picker.sync(select);
            report({ painted, cleared: { html: trigger.innerHTML, classes: [...trigger.classList] } });
            """
        )
        self.assertIn('<span class="startup-mode-status">Installed</span>', result["painted"]["html"])
        self.assertIn(">OpenAI Codex CLI</span>", result["painted"]["html"])
        self.assertIn("status-installed", result["painted"]["classes"])
        self.assertEqual(result["painted"]["title"], "codex is installed")
        self.assertNotIn("startup-mode-status", result["cleared"]["html"])
        self.assertNotIn("status-installed", result["cleared"]["classes"])

    def test_a_pick_is_an_option_choice_and_a_repick_is_nothing(self):
        result = self._run_node(
            """
            const select = makeSelect(RECORDS, 'terminal');
            const changes = [];
            select.addEventListener('change', () => changes.push(select.value));
            const trigger = picker.enhance(select, { iconMarkup });
            const list = () => document.body.children[0];

            trigger.dispatchEvent({ type: 'click' });
            const opened = {
                expanded: trigger.getAttribute('aria-expanded'),
                rows: listRows(list()),
                active: list().getAttribute('aria-activedescendant')
            };
            const click = index => list().dispatchEvent({
                type: 'click',
                target: { closest: () => ({ dataset: { pickerIndex: String(index) } }) }
            });
            click(4);
            const afterPick = {
                value: select.value,
                expanded: trigger.getAttribute('aria-expanded'),
                hidden: list().hidden,
                focusedTrigger: focused === trigger,
                html: trigger.innerHTML
            };
            trigger.dispatchEvent({ type: 'click' });
            click(2);
            const disabledStillOpen = !list().hidden;
            click(4);
            report({ opened, afterPick, disabledStillOpen, changes, value: select.value });
            """
        )
        opened = result["opened"]
        self.assertEqual(opened["expanded"], "true")
        self.assertEqual(
            [row["label"] for row in opened["rows"]],
            ["Terminal", "Initial Command", "Browser", "Claude Code", "OpenAI Codex CLI"],
        )
        self.assertIn("is-selected", opened["rows"][0]["classes"])
        self.assertIn("is-disabled", opened["rows"][2]["classes"])
        self.assertEqual(opened["rows"][3]["name"], 'data-agent="claude"')
        self.assertTrue(opened["active"].endswith("-0"))

        after = result["afterPick"]
        self.assertEqual(after["value"], "agent:claude")
        self.assertEqual(after["expanded"], "false")
        self.assertTrue(after["hidden"])
        self.assertTrue(after["focusedTrigger"])
        self.assertIn(">Claude Code</span>", after["html"])
        # A disabled row is shown but not choosable, and re-choosing the row the
        # select already holds tells the launcher nothing new.
        self.assertTrue(result["disabledStillOpen"])
        self.assertEqual(result["changes"], ["agent:claude"])

    def test_the_keyboard_moves_past_disabled_rows_and_escape_changes_nothing(self):
        result = self._run_node(
            """
            const select = makeSelect(RECORDS, 'command');
            const changes = [];
            select.addEventListener('change', () => changes.push(select.value));
            const trigger = picker.enhance(select, { iconMarkup });
            const list = () => document.body.children[0];
            const key = (target, name) => target.dispatchEvent({
                type: 'keydown', key: name, preventDefault() {}, stopPropagation() {}
            });

            key(trigger, 'ArrowDown');
            key(list(), 'ArrowDown');
            const activeAfterDown = list().getAttribute('aria-activedescendant');
            key(list(), 'Escape');
            const afterEscape = { value: select.value, hidden: list().hidden, changes: changes.length };
            key(trigger, 'Enter');
            key(list(), 'End');
            key(list(), 'Enter');
            report({ activeAfterDown, afterEscape, value: select.value, changes });
            """
        )
        # From Initial Command, down skips the disabled Browser row and the
        # Agent heading and lands on the first agent.
        self.assertTrue(result["activeAfterDown"].endswith("-4"))
        self.assertEqual(result["afterEscape"], {"value": "command", "hidden": True, "changes": 0})
        self.assertEqual(result["value"], "agent:codex")
        self.assertEqual(result["changes"], ["agent:codex"])


LAUNCHER_PRELUDE = r"""
function escHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
window.GridVibeAgentGlyphs = require(__AGENT_GLYPHS__);
const AGENT_OPTIONS = [
    { value: 'claude', label: 'claude', display_name: 'Claude Code' },
    { value: 'grok', label: 'grok', display_name: 'Grok Build (xAI)' },
    { value: 'house', label: 'house', display_name: 'House Agent' },
    { value: 'other', label: 'other', display_name: 'other' }
];
let connectionMode = 'ssh';
const TERMINAL_PROMPT_ICON = '<svg data-icon="prompt"></svg>';
const EXPLORER_MODE_FOLDER_ICON = '<svg data-icon="folder"></svg>';
const BROWSER_MODE_GLOBE_ICON = '<svg data-icon="globe"></svg>';

/* The launcher's markup read back as option records. */
function parseOptions(html) {
    const records = [];
    let group = '';
    const token = /<optgroup label="([^"]*)">|<\/optgroup>|<option\b([^>]*)>([^<]*)<\/option>/g;
    let match;
    while ((match = token.exec(html)) !== null) {
        if (match[1] !== undefined) { group = match[1]; continue; }
        if (match[0] === '</optgroup>') { group = ''; continue; }
        const attrs = match[2];
        const dataset = {};
        attrs.replace(/data-([a-z-]+)="([^"]*)"/g, (_all, key, value) => {
            dataset[key.replace(/-([a-z])/g, (_x, c) => c.toUpperCase())] = value;
        });
        records.push({
            value: /value="([^"]*)"/.exec(attrs)[1],
            text: match[3].trim(),
            dataset,
            disabled: /\sdisabled\b/.test(attrs),
            hidden: /\shidden\b/.test(attrs),
            group
        });
    }
    return records;
}
"""


class LauncherOptionsTestCase(StartupModePickerTestCase):
    def _launcher_source(self) -> str:
        source = LAUNCHER_JS.read_text(encoding="utf-8")
        return (
            _slice(source, "function renderStartupModeOptions(", "function syncStartupModePicker(")
        )

    def test_each_launcher_option_names_what_its_row_wears(self):
        prelude = LAUNCHER_PRELUDE.replace("__AGENT_GLYPHS__", json.dumps(str(AGENT_GLYPHS_JS)))
        result = self._run_node(
            self._launcher_source()
            + """
            const records = parseOptions(renderStartupModeOptions({ mode: 'agent', agentSelection: 'grok' }));
            const select = makeSelect(records, 'agent:grok');
            report({
                entries: picker.startupPickerEntries(select).filter(e => e.kind === 'option')
                    .map(e => ({ label: e.label, icon: e.icon, agent: e.agent, hint: e.hint, disabled: e.disabled })),
                selected: picker.startupPickerSelected(select).label,
                icons: {
                    terminal: startupModeIconMarkup({ icon: 'terminal' }),
                    explorer: startupModeIconMarkup({ icon: 'explorer' }),
                    browser: startupModeIconMarkup({ icon: 'browser' }),
                    grok: startupModeIconMarkup({ icon: 'agent', agent: 'grok' }),
                    command: startupModeIconMarkup({ icon: 'command' }),
                    custom: startupModeIconMarkup({ icon: 'agent', agent: 'default' })
                }
            });
            """,
            prelude=prelude,
        )
        entries = {entry["label"]: entry for entry in result["entries"]}
        self.assertEqual(entries["Terminal"]["icon"], "terminal")
        self.assertEqual(entries["Initial Command"]["icon"], "command")
        self.assertEqual(entries["File Explorer"]["icon"], "explorer")
        # Browser panes exist only on a WSL target.
        self.assertEqual(entries["Browser"], {
            "label": "Browser", "icon": "browser", "agent": "", "hint": "", "disabled": True,
        })
        # Agents by the name their pane will carry, with the command beside it.
        self.assertEqual(entries["Grok Build (xAI)"], {
            "label": "Grok Build (xAI)", "icon": "agent", "agent": "grok", "hint": "grok", "disabled": False,
        })
        # An agent GridVibe has not drawn, and the free-text entry, fall back
        # to the shared mark under the key no brand colour matches.
        self.assertEqual(entries["House Agent"]["agent"], "default")
        self.assertEqual(entries["Custom agent"]["agent"], "default")
        self.assertEqual(entries["Custom agent"]["hint"], "")
        self.assertEqual(result["selected"], "Grok Build (xAI)")

        icons = result["icons"]
        self.assertIn('data-icon="prompt"', icons["terminal"])
        self.assertIn('data-icon="folder"', icons["explorer"])
        self.assertIn('data-icon="globe"', icons["browser"])
        self.assertIn("/docs/images/agent/grok-ai-icon.svg", icons["grok"])
        # An initial command wears the boxed prompt an undrawn agent does.
        self.assertIn("<svg", icons["command"])
        self.assertEqual(icons["command"], icons["custom"])


if __name__ == "__main__":
    unittest.main()
