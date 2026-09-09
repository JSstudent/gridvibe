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

   The same question is asked once more, of the *conversation* a pane is in
   rather than of the pane, and `paneChatLine` is the whole answer. Three rules
   hold it up, and the first two are the same shape: what a pane announces is
   not automatically a name for what it is doing.

     · **A title the shell wrote is not a chat title.** ConPTY forwards a
       console-title change as OSC 0 and bash's stock `PS1` carries one too, so
       `C:\WINDOWS\system32\cmd.exe` and `you@host: ~/work` arrive in exactly
       the field an agent publishes its conversation name in. `isShellSelfTitle`
       is where they are told apart, and its load-bearing rule is comparison
       rather than pattern: a title that *is* the pane's own directory restates
       a fact the row already holds, whoever wrote it.
     · **An id is not a name either.** Codex is launched asking for its
       `thread-title` (`web/agents.py`), and an unnamed thread's title *is* its
       id, so a fresh Codex pane announces a bare UUID. `isOpaqueIdentifierTitle`
       is a peer of the rule above rather than a clause in it: nothing about it
       is a shell, and the honest reading is that the thread has no name yet.
     · **An agent that has announced nothing says so.** The ladder used to fall
       through to the pane's directory, which meant a freshly opened agent was
       labelled with an absolute path — clipped, on a `nowrap` line, at exactly
       the segment that identified it — and a pane whose transport had not come
       up yet was labelled with the agent's own name a second time on a row that
       already states it. Neither answers "which chat is this". `New session`
       does, and the place rides beside it as a leaf, because it is the only
       thing left that tells two fresh agents apart.

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

    /* What a row says about an agent that has announced no conversation yet.

       "New session" and not a substitute for one: the ladder below used to
       fall through to the pane's directory, so a freshly opened agent was
       labelled with an absolute path and a pane whose transport had not come
       up yet was labelled with the agent's own name a second time. Neither
       answers "which chat is this", and both read as a title the agent chose. */
    const AGENT_NEW_SESSION_LABEL = 'New session';

    /* The separator between that label and the place it is in. A middle dot
       rather than a dash, matching the WSL transport tag's own join. */
    const CHAT_LINE_SEPARATOR = ' · ';

    /* Titles a *shell* announces about itself, which are not conversation
       names. ConPTY forwards a console-title change as OSC 0, and bash's stock
       `PS1` carries one too, so these reach the same field an agent publishes
       its chat title in -- and nothing downstream could tell them apart.

       Windows decorates the console title in place rather than replacing it:
       an elevated console is prefixed "Administrator: " and cmd's mark mode
       prefixes "Select ". Both are stripped before the comparison, or the
       label they decorate stops matching. */
    const SHELL_TITLE_DECORATION_PATTERN = /^(?:administrator|select)\s*:?\s+/i;

    const SHELL_SELF_TITLES = [
        'cmd', 'cmd.exe', 'command prompt',
        'powershell', 'powershell.exe', 'windows powershell', 'pwsh', 'pwsh.exe',
        'bash', 'sh', 'zsh', 'fish', 'wsl', 'wsl.exe'
    ];

    /* `user@host: ~/dir`, which is what bash's stock `PS1` puts in OSC 0. The
       tail is one whitespace-free token on purpose: "user@host: fix the
       parser" is a sentence somebody wrote, not a prompt. */
    const PROMPT_TITLE_PATTERN = /^[^\s@]+@[^\s:]+:\s*\S*$/;

    /* A drive root, a UNC share, or a POSIX/`~` root -- the three ways a title
       that is only a path can begin. */
    const PATH_ROOT_PATTERN = /^(?:[A-Za-z]:[\\/]|\\\\|~?\/)/;

    /* An identifier standing where a name should be.

       Codex is launched with `tui.terminal_title=['thread-title']`
       (`web/agents.py`), and an unnamed thread's title *is* its id — so a fresh
       Codex pane announces `01a08612-11ad-7673-989b-4110ba7f8494`, which is a
       perfectly truthful answer to a question nobody asked. It is the same
       defect as a shell announcing itself and gets its own rule rather than
       joining that one, because it is not a shell and not this app's doing:
       the pane has no conversation *name* yet, which is exactly what the
       fallback below exists to say.

       Both shapes are the whole title and one token -- a hyphenated UUID of
       any version, or the same id unhyphenated. The bare-hex floor is 24 so a
       short commit id, which is prose a reader may well have titled a chat
       with, is left alone. */
    const OPAQUE_ID_PATTERNS = [
        /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
        /^[0-9a-f]{24,}$/i
    ];

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

    /* One path, in the one spelling two of them can be compared in: separators
       folded together, a trailing one dropped, case set aside. Windows is
       case-insensitive and the two sides here are a title the shell printed and
       a directory the backend observed, which routinely differ only in case. */
    function normalizePath(value) {
        return text(value).replace(/[\\/]+/g, '/').replace(/\/+$/, '').toLowerCase();
    }

    /* The last segment of a path -- the part that identifies it among the panes
       on a dashboard, and the part `text-overflow: ellipsis` clips off first
       when the whole path is printed. */
    function pathLeaf(value) {
        const parts = text(value).split(/[\\/]+/).filter(Boolean);
        return parts.length ? parts[parts.length - 1] : '';
    }

    /* Is this title the shell talking about itself rather than an agent naming
       a conversation? Four shapes, in the order they cost least to decide.

       The load-bearing one is the third: a title that *is* the pane's own
       directory restates a fact the row already holds, whoever wrote it, and
       that needs no guess about what a path looks like. The two patterns are
       for the announcements a pane makes before there is a directory to
       compare against -- a console image path, and bash's stock prompt.

       Every rule is anchored to the *whole* title, because a conversation title
       is prose and prose is what must survive: "C:\Users cleanup" has a drive
       root and a space and stays, while "~/work" has a root and no space and
       goes. */
    function isShellSelfTitle(title, session) {
        const candidate = text(title);
        if (!candidate) {
            return false;
        }
        const bare = candidate.replace(SHELL_TITLE_DECORATION_PATTERN, '').trim();
        if (SHELL_SELF_TITLES.includes(bare.toLowerCase())) {
            return true;
        }
        if (PROMPT_TITLE_PATTERN.test(bare)) {
            return true;
        }
        const directory = text(session && session.directory);
        if (directory && normalizePath(bare) === normalizePath(directory)) {
            return true;
        }
        if (!PATH_ROOT_PATTERN.test(bare)) {
            return false;
        }
        return !/\s/.test(bare) || (bare.match(/[\\/]/g) || []).length >= 2;
    }

    /* Is this title an id rather than a name? See `OPAQUE_ID_PATTERNS`. */
    function isOpaqueIdentifierTitle(title) {
        const candidate = text(title);
        return Boolean(candidate) && OPAQUE_ID_PATTERNS.some(pattern => pattern.test(candidate));
    }

    function agentChatTitle(session, options) {
        const key = agentKeyForSession(session);
        let title = text(session?.activity?.title);
        // These agents decorate their published title with a changing status
        // marker. It is separate from the conversation's name.
        if (['claude', 'hermes', 'copilot'].includes(key)) {
            title = title.replace(/^[\u2800-\u28ff✳✻✽✶✢⏺⏳✓⚠🤖]\uFE0F?\s*/u, '').trim();
        }
        if (isShellSelfTitle(title, session) || isOpaqueIdentifierTitle(title)) {
            return '';
        }
        const placeholders = [key, agentDisplayName(session, options)];
        if (key === 'kimi') placeholders.push('kimi-code', 'kimi code');
        if (key === 'copilot') placeholders.push('GitHub Copilot');
        if (key === 'codex') placeholders.push('Codex');
        return placeholders.some(value => text(value).toLowerCase() === title.toLowerCase()) ? '' : title;
    }

    /* Where a pane is, short enough to sit on a row beside something else.

       The leaf and not the path: the line it goes on is one `nowrap` row with
       an ellipsis at its end, so an absolute path is clipped at exactly the
       segment that identifies it and two agents in two repositories read
       identically. A remote pane keeps its host -- the transport chip says only
       "SSH", so nothing else on the row says which machine. */
    function paneLocationLabel(session) {
        const directory = text(session && session.directory);
        const leaf = pathLeaf(directory) || directory;
        const host = text(session && session.mode).toLowerCase() === PANE_MODE_SSH
            ? text(session && session.host)
            : '';
        if (host && leaf) {
            return `${host}:${leaf}`;
        }
        return leaf || host;
    }

    /* The whole of a pane's second line: which conversation it is.

       The announced chat title leads, then a title the reader typed, and an
       agent that has announced neither *says so* rather than borrowing the next
       fact down. That fallthrough is what labelled a freshly opened agent with
       an absolute path, and a pane whose transport had not come up yet with the
       agent's own name a second time -- on a row that already states it.

       The place is kept beside the label because it is the only thing left that
       tells two fresh agents apart; it is a leaf, so it survives the ellipsis.
       A pane that is not an agent has no conversation to be new, and keeps the
       fallback it always had. */
    function paneChatLine(session, index, options) {
        const announced = agentChatTitle(session, options);
        if (announced) {
            return announced;
        }
        const typed = text(session && session.title);
        if (typed && !isGenericPaneTitle(typed)) {
            return typed;
        }
        const location = paneLocationLabel(session);
        if (paneKindForSession(session) === PANE_KIND_AGENT) {
            return location
                ? `${AGENT_NEW_SESSION_LABEL}${CHAT_LINE_SEPARATOR}${location}`
                : AGENT_NEW_SESSION_LABEL;
        }
        return location || paneDisplayTitle(session, index, options);
    }

    /* The same line, plus the full path the line shortened away. The row has
       one line and a hover; shortening for the line is only affordable because
       the hover still holds what was dropped. */
    function paneChatTooltip(session, index, options) {
        const line = paneChatLine(session, index, options);
        const directory = text(session && session.directory);
        if (!directory) {
            return line;
        }
        const host = text(session && session.mode).toLowerCase() === PANE_MODE_SSH
            ? text(session && session.host)
            : '';
        const full = host ? `${host}: ${directory}` : directory;
        return line.includes(full) ? line : `${line}\n${full}`;
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
        AGENT_NEW_SESSION_LABEL,
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
        isShellSelfTitle,
        isOpaqueIdentifierTitle,
        paneLocationLabel,
        paneChatLine,
        paneChatTooltip,
        paneTransportLabel
    };
}));
