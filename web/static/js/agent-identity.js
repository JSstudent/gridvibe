/* GridVibeAgentIdentity — what a pane is, and what to call it.

   Two surfaces name the same pane: the pane header in the workspace window,
   and the dashboard row that lists it. They have to agree, or the same agent
   reads as "Claude" in one place and "Terminal 3" in the other — so the rule
   is here once and both sides paint from it. The backend deliberately holds no
   copy: `/api/dashboard` publishes what a pane *is* (its startup mode, its
   agent selection, its status) and never what to call it.

   The one decision worth stating is what counts as a name:

     · The launcher writes "Terminal 1" into every pane's title field as a
       *placeholder* — it is the value of an input the user never had to touch,
       not something anybody chose. So a title of that shape is treated as
       unset, which is what lets an agent pane name itself. A title the user
       actually typed always wins, including over the agent.
     · An agent pane is named from the registry the launcher and the pane
       header menu already read (`AGENT_OPTIONS`), by its `display_name` —
       prose, not the binary, the same way the relaunch menu names one. The
       binary is still the fallback when the registry has nothing to say, and
       a custom agent is named by the command's own first word.
     · `startup_mode === 'agent'` is the only marker of an agent pane, matching
       terminal-shell.js: a startup *command* is not an agent.

   It also holds the *second* thing both surfaces say about a pane: what it is
   running on. `paneTransportLabel` turns the three transport facts the backend
   publishes (`mode`, and the `use_wsl`/`use_powershell` precedence
   terminal-shell.js's `paneShellKind` already reads) into the one short word
   the dashboard tags a row with. It is here rather than in the dashboard for
   the reason the naming rule is: it is a *naming* decision over facts the
   server states, and the server states them precisely so it does not have to
   make it.

   DOM-free and require()-able from Node so the rule is executed by tests
   rather than asserted as source text. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeAgentIdentity = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* "Terminal", optionally followed by a number, and nothing else. Anchored
       and whitespace-tolerant so "  terminal 12 " is still the placeholder,
       while "Terminal — build" is a name somebody wrote. */
    const GENERIC_PANE_TITLE_PATTERN = /^\s*terminal\s*\d*\s*$/i;

    const PANE_KIND_AGENT = 'agent';
    const PANE_KIND_TERMINAL = 'terminal';
    const PANE_KIND_EXPLORER = 'explorer';
    const PANE_KIND_BROWSER = 'browser';

    /* The one pane mode that is not local. Everything else runs on this
       machine, and which shell it runs there is the `use_*` precedence. */
    const PANE_MODE_SSH = 'ssh';

    const TRANSPORT_LABEL_SSH = 'SSH';
    const TRANSPORT_LABEL_WSL = 'WSL';
    const TRANSPORT_LABEL_POWERSHELL = 'PowerShell';
    const TRANSPORT_LABEL_CMD = 'cmd';

    function text(value) {
        return String(value === null || value === undefined ? '' : value).trim();
    }

    function isGenericPaneTitle(title) {
        const candidate = text(title);
        return !candidate || GENERIC_PANE_TITLE_PATTERN.test(candidate);
    }

    /* The agent a pane runs, or '' for anything else. Mirrors
       terminal-shell.js's `paneAgentKey`: only a pane whose startup mode *is*
       agent names one, so a terminal that happens to have been handed a
       command is still a terminal. */
    function agentKeyForSession(session) {
        if (!session || session.startup_mode !== PANE_KIND_AGENT) {
            return '';
        }
        return text(session.agent_selection || session.custom_agent).toLowerCase();
    }

    function paneKindForSession(session) {
        const mode = text(session && session.startup_mode);
        if (mode === PANE_KIND_EXPLORER) return PANE_KIND_EXPLORER;
        if (mode === PANE_KIND_BROWSER) return PANE_KIND_BROWSER;
        if (mode === PANE_KIND_AGENT) return PANE_KIND_AGENT;
        return PANE_KIND_TERMINAL;
    }

    function agentOptionFor(key, options) {
        const normalized = text(key).toLowerCase();
        if (!normalized || !Array.isArray(options)) {
            return null;
        }
        return options.find(option => option && text(option.value).toLowerCase() === normalized) || null;
    }

    /* A custom agent has no registry entry, so it is named by the first word
       of the command that starts it — "my-agent --resume" is "my-agent". The
       whole command line would be a paragraph in a pane header. */
    function customAgentName(session) {
        return text(session && session.custom_agent).split(/\s+/)[0] || '';
    }

    /* The one place a pane's agent turns into words. Prose from the registry
       when it has some, then the registry's binary label, then whatever the
       pane itself named — never an empty string for a pane that is running
       something. */
    function agentDisplayName(session, options) {
        const key = agentKeyForSession(session);
        if (!key) {
            return '';
        }
        const option = agentOptionFor(key, options);
        if (option && key !== 'other') {
            return text(option.display_name) || text(option.label) || key;
        }
        return customAgentName(session) || (key === 'other' ? '' : key);
    }

    /* What the pane header prints and what the dashboard row prints. `index`
       is the pane's position in its group, which is the only thing the
       "Terminal N" fallback has ever been counted from. */
    function paneDisplayTitle(session, index, options) {
        const title = text(session && session.title);
        if (!isGenericPaneTitle(title)) {
            return title;
        }
        const agentName = agentDisplayName(session, options);
        if (agentName) {
            return agentName;
        }
        return title || `Terminal ${Number(index || 0) + 1}`;
    }

    function agentChatTitle(session, options) {
        const key = agentKeyForSession(session);
        let title = text(session?.activity?.title);
        // These agents decorate their published title with a changing status
        // marker. It is separate from the conversation's name.
        if (['claude', 'hermes', 'copilot'].includes(key)) {
            title = title.replace(/^[\u2800-\u28ff✳✻✽✶✢⏺⏳✓⚠🤖]\uFE0F?\s*/u, '').trim();
        }
        const placeholders = [key, agentDisplayName(session, options)];
        if (key === 'kimi') placeholders.push('kimi-code', 'kimi code');
        if (key === 'copilot') placeholders.push('GitHub Copilot');
        if (key === 'codex') placeholders.push('Codex');
        return placeholders.some(value => text(value).toLowerCase() === title.toLowerCase()) ? '' : title;
    }

    /* What a pane is running on, in one word.

       An SSH pane is remote and that is the whole answer — which host it is on
       is already the row's own note, and repeating it in a tag would push the
       agent's name off the line. A local pane is named by the shell family it
       actually started, in terminal-shell.js's own precedence (WSL beats
       PowerShell beats cmd), because that is the dimension its relaunch menu
       offers and the two have to agree about what the pane is running now.

       A named WSL distro is worth carrying: two agents on two distros are two
       different machines as far as their work is concerned. */
    function paneTransportLabel(session) {
        if (!session) {
            return '';
        }
        if (text(session.mode).toLowerCase() === PANE_MODE_SSH) {
            return TRANSPORT_LABEL_SSH;
        }
        if (session.use_wsl) {
            const distribution = text(session.distribution);
            return distribution ? `${TRANSPORT_LABEL_WSL} · ${distribution}` : TRANSPORT_LABEL_WSL;
        }
        return session.use_powershell ? TRANSPORT_LABEL_POWERSHELL : TRANSPORT_LABEL_CMD;
    }

    return {
        GENERIC_PANE_TITLE_PATTERN,
        PANE_KIND_AGENT,
        PANE_KIND_TERMINAL,
        PANE_KIND_EXPLORER,
        PANE_KIND_BROWSER,
        PANE_MODE_SSH,
        TRANSPORT_LABEL_SSH,
        TRANSPORT_LABEL_WSL,
        TRANSPORT_LABEL_POWERSHELL,
        TRANSPORT_LABEL_CMD,
        isGenericPaneTitle,
        agentKeyForSession,
        paneKindForSession,
        agentOptionFor,
        agentDisplayName,
        paneDisplayTitle,
        agentChatTitle,
        paneTransportLabel
    };
}));
