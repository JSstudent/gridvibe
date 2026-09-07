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

    return {
        GENERIC_PANE_TITLE_PATTERN,
        PANE_KIND_AGENT,
        PANE_KIND_TERMINAL,
        PANE_KIND_EXPLORER,
        PANE_KIND_BROWSER,
        isGenericPaneTitle,
        agentKeyForSession,
        paneKindForSession,
        agentOptionFor,
        agentDisplayName,
        paneDisplayTitle
    };
}));
