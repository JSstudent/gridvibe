"""What a pane is called, executed rather than asserted as source text.

`agent-identity.js` is DOM-free and require()-able, so every case below runs the
shipped rule over the pane payload the backend actually publishes.

The rule exists because two surfaces name the same pane — the pane header in
the workspace window and the dashboard row that lists it — and what is pinned
here is the part that is easy to get subtly wrong:

- **"Terminal 1" is a placeholder, not a name.** The launcher writes it into
  every pane's title field as the default value of an input nobody had to
  touch, so a title of that shape has to be treated as unset or an agent pane
  could never name itself.
- **A title the user typed always wins**, including over the agent — otherwise
  naming a pane would silently stop working the moment it ran one.
- **A startup command is not an agent.** Only `startup_mode === 'agent'` marks
  one, matching terminal-shell.js; reading `agent_selection` alone would name a
  plain terminal after an agent it is not running.
- **An agent with no registry entry still gets a name** — its own key, or the
  first word of a custom command — because a blank name on a running agent is
  the one answer that helps nobody.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_IDENTITY_JS = REPO_ROOT / "web" / "static" / "js" / "agent-identity.js"

NODE = shutil.which("node")

# The registry the pages hand in, trimmed to the shape the module reads.
HARNESS_STUBS = """
const AGENT_OPTIONS = [
    { value: 'claude', label: 'claude', display_name: 'Claude Code' },
    { value: 'codex', label: 'codex', display_name: 'OpenAI Codex CLI' },
    { value: 'kilo', label: 'kilo', display_name: '' },
    { value: 'other', label: 'other', display_name: 'other' }
];

function pane(overrides) {
    return Object.assign({
        session_id: 's1',
        title: 'Terminal 1',
        startup_mode: 'terminal',
        agent_selection: '',
        custom_agent: '',
        mode: 'ssh'
    }, overrides || {});
}

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for the agent identity tests")
class AgentIdentityTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        script = (
            HARNESS_STUBS
            + AGENT_IDENTITY_JS.read_text(encoding="utf-8")
            + "\nconst identity = module.exports;\n"
            + body
            + "\n"
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

    def test_a_generic_title_on_an_agent_pane_becomes_the_agents_name(self):
        titles = self._run_node(
            """
            report({
                claude: identity.paneDisplayTitle(
                    pane({ title: 'Terminal 1', startup_mode: 'agent', agent_selection: 'claude' }),
                    0, AGENT_OPTIONS
                ),
                codex: identity.paneDisplayTitle(
                    pane({ title: 'Terminal 3', startup_mode: 'agent', agent_selection: 'codex' }),
                    2, AGENT_OPTIONS
                ),
                untitled: identity.paneDisplayTitle(
                    pane({ title: '', startup_mode: 'agent', agent_selection: 'claude' }),
                    1, AGENT_OPTIONS
                )
            });
            """
        )
        self.assertEqual(titles["claude"], "Claude Code")
        self.assertEqual(titles["codex"], "OpenAI Codex CLI")
        self.assertEqual(titles["untitled"], "Claude Code")

    def test_a_title_the_user_typed_outranks_the_agent(self):
        title = self._run_node(
            """
            report(identity.paneDisplayTitle(
                pane({ title: 'Build box', startup_mode: 'agent', agent_selection: 'claude' }),
                0, AGENT_OPTIONS
            ));
            """
        )
        self.assertEqual(title, "Build box")

    def test_chat_titles_drop_provider_placeholders_and_status_markers(self):
        result = self._run_node(
            """
            report([
                ['claude', '✳ Fix dashboard navigation'],
                ['hermes', '⠋ Rename current conversation'],
                ['copilot', '🤖 GitHub Copilot'],
                ['kimi', 'kimi-code'],
                ['codex', 'Codex'],
                ['codex', '⚠ Keep literal conversation punctuation'],
                ['other', '✳ Custom agent title'],
                ['kimi', 'Multiword active chat title']
            ].map(([key, title]) => identity.agentChatTitle(pane({
                startup_mode: 'agent', agent_selection: key, activity: { title }
            }), AGENT_OPTIONS)));
            """
        )
        self.assertEqual(result, [
            "Fix dashboard navigation", "Rename current conversation", "", "", "",
            "⚠ Keep literal conversation punctuation", "✳ Custom agent title",
            "Multiword active chat title",
        ])

    def test_a_plain_pane_keeps_the_terminal_n_fallback(self):
        # A placeholder on a pane with no agent is still printed as the server
        # holds it, exactly as the header always has -- treating it as unset is
        # what lets an *agent* pane rename itself, not a licence to rewrite a
        # title nobody asked to change. Only a pane with no title at all is
        # counted from its own position.
        titles = self._run_node(
            """
            report([
                identity.paneDisplayTitle(pane({ title: 'Terminal 1' }), 0, AGENT_OPTIONS),
                identity.paneDisplayTitle(pane({ title: '' }), 4, AGENT_OPTIONS),
                identity.paneDisplayTitle(pane({ title: '  terminal 7  ' }), 6, AGENT_OPTIONS)
            ]);
            """
        )
        self.assertEqual(titles, ["Terminal 1", "Terminal 5", "terminal 7"])

    def test_a_startup_command_is_not_an_agent(self):
        # agent_selection can survive on a pane that was relaunched as a plain
        # shell; only the startup mode says what it is running now.
        result = self._run_node(
            """
            const stale = pane({ startup_mode: 'terminal', agent_selection: 'claude' });
            report({
                key: identity.agentKeyForSession(stale),
                title: identity.paneDisplayTitle(stale, 0, AGENT_OPTIONS)
            });
            """
        )
        self.assertEqual(result["key"], "")
        self.assertEqual(result["title"], "Terminal 1")

    def test_an_agent_the_registry_does_not_know_is_named_anyway(self):
        names = self._run_node(
            """
            report({
                unlisted: identity.paneDisplayTitle(
                    pane({ startup_mode: 'agent', agent_selection: 'aider' }), 0, AGENT_OPTIONS
                ),
                noDisplayName: identity.paneDisplayTitle(
                    pane({ startup_mode: 'agent', agent_selection: 'kilo' }), 0, AGENT_OPTIONS
                ),
                custom: identity.paneDisplayTitle(
                    pane({
                        startup_mode: 'agent',
                        agent_selection: 'other',
                        custom_agent: 'my-agent --resume'
                    }),
                    0, AGENT_OPTIONS
                ),
                noRegistryAtAll: identity.paneDisplayTitle(
                    pane({ startup_mode: 'agent', agent_selection: 'claude' }), 0, []
                )
            });
            """
        )
        self.assertEqual(names["unlisted"], "aider")
        self.assertEqual(names["noDisplayName"], "kilo")
        self.assertEqual(names["custom"], "my-agent")
        self.assertEqual(names["noRegistryAtAll"], "claude")

    def test_every_pane_kind_is_reported(self):
        kinds = self._run_node(
            """
            report(['terminal', 'agent', 'explorer', 'browser', 'nonsense'].map(
                mode => identity.paneKindForSession(pane({ startup_mode: mode }))
            ));
            """
        )
        self.assertEqual(
            kinds, ["terminal", "agent", "explorer", "browser", "terminal"]
        )

    def test_a_pane_with_nothing_stated_still_answers(self):
        answers = self._run_node(
            """
            report({
                kind: identity.paneKindForSession(null),
                key: identity.agentKeyForSession(null),
                title: identity.paneDisplayTitle(null, 2, AGENT_OPTIONS),
                transport: identity.paneTransportLabel(null)
            });
            """
        )
        self.assertEqual(answers["kind"], "terminal")
        self.assertEqual(answers["key"], "")
        self.assertEqual(answers["title"], "Terminal 3")
        self.assertEqual(answers["transport"], "")

    # ── What a pane runs on ──
    # The one word the dashboard tags a row with. It reads the same three facts
    # the relaunch menu's `paneShellKind` does, and in the same precedence,
    # because a row that said `cmd` about a pane whose reset menu has WSL
    # ticked would be the two surfaces disagreeing about the same pane.

    def _labels(self, cases: str):
        return self._run_node(
            "report(%s.map(overrides => identity.paneTransportLabel(pane(overrides))));"
            % cases
        )

    def test_a_remote_pane_is_named_by_its_transport_and_not_its_host(self):
        # The host is already the row's own note; repeating it in the tag would
        # push the agent's name off the line.
        self.assertEqual(
            self._labels("[{ mode: 'ssh', host: '10.0.0.5' }]"), ["SSH"]
        )

    def test_a_local_pane_is_named_by_the_shell_it_actually_started(self):
        labels = self._labels(
            """[
                { mode: 'wsl', use_wsl: true, use_powershell: true },
                { mode: 'wsl', use_powershell: true },
                { mode: 'wsl' }
            ]"""
        )
        # WSL beats PowerShell beats cmd — terminal-shell.js's own order.
        self.assertEqual(labels, ["WSL", "PowerShell", "cmd"])

    def test_a_named_distro_is_carried_because_it_is_a_different_machine(self):
        labels = self._labels(
            """[
                { mode: 'wsl', use_wsl: true, distribution: 'Ubuntu' },
                { mode: 'wsl', use_wsl: true, distribution: '   ' }
            ]"""
        )
        self.assertEqual(labels, ["WSL · Ubuntu", "WSL"])

    def test_a_remote_pane_is_remote_whatever_shell_flags_it_carries(self):
        self.assertEqual(
            self._labels("[{ mode: 'SSH', use_powershell: true }]"), ["SSH"]
        )


if __name__ == "__main__":
    unittest.main()
