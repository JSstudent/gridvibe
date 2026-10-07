/* "Same for all" in the launcher's Terminal Setup card.

   With the box ticked, Terminal 1 is the template and the only card on
   show; every other terminal follows it. launcher.js re-renders the hidden
   followers from the drafts this module returns whenever Terminal 1 changes,
   so they always hold what each pane will launch with, saving a preset
   stores real values, and unticking shows each row as it was given.

   What a follower copies is everything that says what the pane *runs*: the
   folder, the startup mode and agent, the command or URL, auto mode and the
   MCP tools, the local shell, and whether it runs in tmux. What it keeps is
   what makes it a pane of its own:

   - its title, so eight panes are not all called "Terminal 1";
   - its tmux session name. Copying one would put every pane on the same
     tmux session, which the server refuses outright, and a saved preset's
     per-pane name is what reattaches that pane's own session. A follower
     with no name gets a fresh session at launch;
   - agent override mode, which is never copied and never kept. It is granted
     by the person for one row through the in-page warning, and a box that
     copied it would grant it to panes nobody confirmed it for;
   - its saved explorer state (open tabs, pinned Git folder, root), but only
     while it was already an explorer on the same folder. Those paths are
     relative to the pane's own root; on a new folder they would reopen as
     missing files, so the follower starts clean there.

   DOM-free and Node-tested. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeTerminalApplyAll = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* The saved explorer state a pane carries invisibly through the form,
       with the value a fresh pane starts from. */
    const EXPLORER_STATE_DEFAULTS = Object.freeze({
        explorer_root_directory: '',
        explorer_root_configured: false,
        explorer_tree_open: false,
        explorer_git_open: false,
        explorer_git_follow_browsing: false,
        explorer_git_pin_active: false,
        explorer_git_pinned_path: '',
        explorer_git_pin_kind: 'dir',
        explorer_search_open: false,
        explorer_open_tabs: [],
        explorer_active_tab: '',
        explorer_tab_views: {},
        explorer_md_preset: '',
        explorer_md_font: '',
        explorer_source_font: '',
        explorer_theme: 'dark'
    });

    function clone(value) {
        if (Array.isArray(value)) return value.map(clone);
        if (value && typeof value === 'object') {
            return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, clone(item)]));
        }
        return value;
    }

    function isExplorer(draft) {
        return String(draft?.startup_mode || '') === 'explorer';
    }

    function sameFolder(a, b) {
        return String(a?.directory || '').trim() === String(b?.directory || '').trim();
    }

    /* One follower's draft: the template's settings with the follower's own
       identity. `own` is the follower's current draft, and its tmux_session
       is the name typed in its row whether or not tmux is ticked there. */
    function followTemplate(template, own, index) {
        const source = template || {};
        const self = own || {};
        const keepsExplorerState = isExplorer(self) && isExplorer(source) && sameFolder(self, source);
        const draft = clone(source);
        Object.keys(EXPLORER_STATE_DEFAULTS).forEach(key => {
            draft[key] = keepsExplorerState && key in self
                ? clone(self[key])
                : clone(EXPLORER_STATE_DEFAULTS[key]);
        });
        draft.title = String(self.title || '').trim() || `Terminal ${index + 1}`;
        draft.tmux = source.tmux === true;
        draft.tmux_session = String(self.tmux_session || '').trim();
        draft.agent_mcp_override = false;
        return draft;
    }

    /* The whole form's drafts with the first `count` following drafts[0];
       drafts past `count` (kept for when the count grows again) are left
       as they are. */
    function applyTemplateToDrafts(drafts, count) {
        const list = Array.isArray(drafts) ? drafts : [];
        if (!list.length) return [];
        const template = list[0];
        const limit = Math.min(list.length, Math.max(0, Number(count) || 0));
        return list.map((draft, index) => (
            index > 0 && index < limit ? followTemplate(template, draft, index) : draft
        ));
    }

    return {
        EXPLORER_STATE_DEFAULTS,
        followTemplate,
        applyTemplateToDrafts
    };
}));
