"""The Files tree marks where the Git scope is: the pin, and a live Follow.

A pin captures the folder that was being browsed and then freezes there, so
navigating away used to leave nothing on screen saying *where* it was: the
Graph header's pin button reported only that a pin existed. A restored
workspace that came back in some other folder therefore read as a pin that had
been lost. Follow has the mirrored problem -- it is a mode with no fixed
path, so the only report that it was on at all was a pressed button in a
sidebar the reader may not have open.

Both markers are derived, never persisted -- the pin is
``_explorerGitPinnedPath`` answered per row, Follow is the sidebar's own
browsing scope answered per row -- and they move by painting exactly the rows
that disagree, because rebuilding ``[data-explorer-tree-body]`` empties the
tree's scroller and the capture-phase scroll listener would persist that
clamped 0 as the reader's position. One walk paints both.

A row may wear both, and that is the point: while Follow is on it overrides
the pin without replacing it, so collapsing the two into one mark would hide
the pin that is still there -- the "pin was lost" reading these markers exist
to prevent.

The Graph header's pin button is the pin marker's other half and lives here for
that reason: both surfaces answer "is the pin *here*" from the one predicate in
``explorer-git-pin.js``, and a second test file over the same module is how
they would come to disagree. The button asks *here*, not *is there a pin* -- so
it is pressed only while the browsed folder is the pinned one, re-pins from
anywhere else in a single write, and never clears a pin made somewhere else.
The Follow marker answers "is Follow *here*" through the same predicate, which
is why it is asserted here rather than beside it.

Executed in Node against the real modules: the predicate, the row markup, the
paint and the click are run, not read. Where Follow is pointing is taken from
the real ``explorer-git-sidebar.js`` derivation rather than restated, since a
marker naming a different path from the repo bar's Follow row is the failure
worth catching. Only the markup hooks themselves
(``explorer-tree-pin-mark``, ``explorer-tree-follow-mark``, the titles) are
asserted as text, which is the documented exception for rendered markup.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
TREE_JS = STATIC_JS / "explorer-tree.js"
PIN_JS = STATIC_JS / "explorer-git-pin.js"
SIDEBAR_JS = STATIC_JS / "explorer-git-sidebar.js"
# The panel renderer's own policy collaborators, loaded rather than
# stubbed so the render under test is the real one.
SIDEBAR_POLICY_JS = [
    STATIC_JS / "explorer-git-active.js",
    STATIC_JS / "explorer-git-search.js",
]

NODE = shutil.which("node")

# Two root-level folders and a file, plus a nested folder, so "exactly one row"
# is an observation over a tree that has somewhere else to put the marker.
FIXTURE_CHILDREN = {
    "": [
        {"type": "directory", "entry_kind": "directory", "path": "docs", "name": "docs"},
        {"type": "directory", "entry_kind": "directory", "path": "web", "name": "web"},
        {"type": "file", "entry_kind": "file", "path": "readme.md", "name": "readme.md"},
    ],
    "web": [{"type": "directory", "entry_kind": "directory", "path": "web/static", "name": "static"}],
    "web/static": [],
    "docs": [],
}

# The two entry shapes where `entry_kind` and `type` part company, which is
# exactly where the render and the paint could disagree about which row is the
# pin:
#   * a symlink -- `_explorer_entry_kind()` answers "link" while `type`
#     collapses everything that is not a directory to "file";
#   * a filtered-tree row whose parent listing is not cached --
#     `explorerTreeSearchRowsHtml()` passes `entry_kind: ''` for it, and that
#     is the ordinary case for a search hit deep in the tree.
# Both go through the same `explorerTreeRowHtml()`, so the divergence is
# reproduced here by the entry shape rather than by a second row builder.
DIVERGENT_KIND_CHILDREN = {
    "": [
        {"type": "directory", "entry_kind": "directory", "path": "web", "name": "web"},
        {"type": "file", "entry_kind": "link", "path": "link.txt", "name": "link.txt"},
        {"type": "file", "entry_kind": "", "path": "notes.md", "name": "notes.md"},
    ],
    "web": [],
}

# The tree's own globals come from explorer-viewer.js and terminal-icons.js on
# the page; the marker only needs them to render something identifiable.
SANDBOX = r"""
const fs = require('fs');
const vm = require('vm');

function makeSandbox() {
    const sandbox = {
        console,
        document: {
            getElementById: () => null,
            querySelector: () => null,
            querySelectorAll: () => [],
            addEventListener() {},
            body: { dataset: {}, addEventListener() {} }
        },
        navigator: {},
        setTimeout,
        clearTimeout,
        requestAnimationFrame: () => 0,
        terminals: [],
        sessionIds: [],
        EXPLORER_TREE_INDENT_PX: 12,
        EXPLORER_GIT_PIN_ICON: '<svg class="explorer-btn-icon" data-icon="pin"></svg>',
        EXPLORER_GIT_FOLLOW_ICON: '<svg class="explorer-btn-icon" data-icon="follow"></svg>',
        EXPLORER_OPEN_FOLDER_ICON: '<svg class="explorer-btn-icon"></svg>',
        EXPLORER_OPEN_TAB_ICON: '<svg class="explorer-btn-icon"></svg>',
        EXPLORER_FOLDER_ICON: '<span class="explorer-icon"></span>',
        UI_CHEVRON_DOWN_ICON: '<svg></svg>',
        UI_CHEVRON_RIGHT_ICON: '<svg></svg>',
        explorerFileTypeIconHtml: () => '<span class="explorer-icon"></span>',
        explorerGitStatusLabel: () => '',
        explorerGitBadgeHtml: () => '',
        escHtml: value => String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
    };
    sandbox.globalThis = sandbox;
    // The tree reaches its policy module through `window`, exactly as the
    // other explorer-git-* adapters do.
    sandbox.window = sandbox;
    return sandbox;
}

function loadSandbox(pinJs, treeJs) {
    const sandbox = makeSandbox();
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(pinJs, 'utf8'), sandbox);
    vm.runInContext(fs.readFileSync(treeJs, 'utf8'), sandbox);
    return sandbox;
}

/* The tree state is gated on `instanceof Set`/`Map`, which fails across vm
   realms, so the pane is assembled inside the context. */
function installPane(sandbox, fixture, expanded, pinned, pinnedKind = 'dir') {
    sandbox.__fixture = { fixture, expanded, pinned, pinnedKind };
    vm.runInContext(`
        sessionIds[0] = 'sess-0';
        terminals[0] = {
            _explorerTreeExpanded: new Set(__fixture.expanded),
            _explorerTreeChildren: new Map(Object.entries(__fixture.fixture)),
            _explorerTreeErrors: new Map(),
            _explorerTreeLoading: new Set()
        };
        if (__fixture.pinned !== null) {
            terminals[0]._explorerGitPinnedPath = __fixture.pinned;
            terminals[0]._explorerGitPinKind = __fixture.pinnedKind;
        }
    `, sandbox);
    return sandbox;
}
"""

# A tree panel with just enough behaviour for the paint: rows keep their own
# identity and their own child list, so "were these the same nodes afterwards"
# and "which children moved" are observations rather than inferences. The
# panel's scrollTop is a plain field -- nothing in the paint may touch it.
PAINT_HARNESS = SANDBOX + r"""
let nodeSeq = 0;

function makeMark() {
    return {
        nodeId: ++nodeSeq,
        className: 'explorer-tree-pin-mark',
        _host: null,
        remove() {
            const at = this._host ? this._host.indexOf(this) : -1;
            if (at !== -1) { this._host.splice(at, 1); }
        }
    };
}

function makeOpenControl(className, children) {
    return {
        nodeId: ++nodeSeq,
        className,
        insertAdjacentHTML(position, markup) {
            if (position !== 'beforebegin') { throw new Error('unexpected ' + position); }
            if (!markup.includes('explorer-tree-pin-mark')) { throw new Error('not a mark'); }
            const mark = makeMark();
            mark._host = children;
            children.splice(children.indexOf(this), 0, mark);
        }
    };
}

/* A row stub carrying the attribute set the real row markup carries. Both are
   present and they are deliberately allowed to disagree: `explorerContextKind`
   is the entry's `entry_kind` (`directory`/`file`/`link`/`other`, and '' for a
   filtered row whose parent listing is not cached), while the Git scope
   attributes are derived from `entry.type`, which knows only directory/file.
   The paint must read the scope pair -- reading the other field is what made
   it strip the marker the render had just placed. */
function makeRow(spec) {
    const path = spec.path;
    const kind = spec.kind;
    const marked = Boolean(spec.marked);
    const children = [];
    const row = {
        nodeId: ++nodeSeq,
        dataset: {
            explorerContextPath: path,
            explorerContextKind: spec.entryKind === undefined ? kind : spec.entryKind,
            explorerGitScopePath: path,
            explorerGitScopeKind: kind === 'directory' ? 'dir' : 'file'
        },
        children,
        insertAdjacentHTML(position, markup) {
            if (position !== 'beforeend') { throw new Error('unexpected ' + position); }
            if (!markup.includes('explorer-tree-pin-mark')) { throw new Error('not a mark'); }
            const mark = makeMark();
            mark._host = children;
            children.push(mark);
        },
        querySelector(selector) {
            const wanted = selector.split(',').map(part => part.trim().replace(/^\./, ''));
            return children.find(child => wanted.includes(child.className)) || null;
        }
    };
    if (marked) {
        const mark = makeMark();
        mark._host = children;
        children.push(mark);
    }
    children.push(makeOpenControl(
        kind === 'directory' ? 'explorer-open-folder-btn' : 'explorer-open-tab-btn',
        children
    ));
    return row;
}

const pinned = process.argv[4] === '__none__' ? null : process.argv[4];
const rowSpec = JSON.parse(process.argv[5]);
const pinnedKind = process.argv[6] || 'dir';

const sandbox = loadSandbox(process.argv[2], process.argv[3]);
installPane(sandbox, {}, [], pinned, pinnedKind);

const rows = rowSpec.map(spec => makeRow(spec));
const rootMark = { nodeId: ++nodeSeq, hidden: true };
const panel = {
    scrollTop: 412,
    scrollLeft: 7,
    querySelector: selector => (
        selector === '[data-explorer-tree-pin-root]' ? rootMark : null
    ),
    querySelectorAll: selector => (selector === '.explorer-tree-row' ? rows : [])
};
sandbox.document.getElementById = id => (id === 'explorer-tree-panel-0' ? panel : null);

const before = rows.map(row => row.nodeId);
sandbox.applyExplorerTreeScopeMarks(0);

const hasMark = row => row.children.some(c => c.className === 'explorer-tree-pin-mark');

process.stdout.write(JSON.stringify({
    rowIdsUnchanged: JSON.stringify(before) === JSON.stringify(rows.map(row => row.nodeId)),
    marked: rows.filter(hasMark).map(row => row.dataset.explorerContextPath),
    // The marker's position inside every row that has one: it belongs
    // immediately left of that row's open control.
    markBeforeOpen: rows.every(row => {
        const at = row.children.findIndex(c => c.className === 'explorer-tree-pin-mark');
        if (at === -1) { return true; }
        const open = row.children.findIndex(
            c => String(c.className).startsWith('explorer-open-')
        );
        return open === -1 || at < open;
    }),
    rootHidden: rootMark.hidden,
    scrollTop: panel.scrollTop,
    scrollLeft: panel.scrollLeft
}));
"""

# The rendered tree body, straight out of the real row builder.
RENDER_HARNESS = SANDBOX + r"""
const pinned = process.argv[5] === '__none__' ? null : process.argv[5];
const pinnedKind = process.argv[6] || 'dir';
const sandbox = loadSandbox(process.argv[2], process.argv[3]);
installPane(sandbox, JSON.parse(process.argv[4]), ['web'], pinned, pinnedKind);

const html = sandbox.renderExplorerTreeNodes(sandbox.terminals[0], '', 0);
// Which rows carry the marker: `split` hands back the markup between one row's
// path attribute and the next one's, so each slice is that row, and a marker
// landing on the wrong row is reported as that row's path.
const rows = html.split('data-explorer-context-path="').slice(1).map(part => ({
    path: part.slice(0, part.indexOf('"')),
    body: part
}));
const marked = rows
    .filter(row => row.body.includes('explorer-tree-pin-mark'))
    .map(row => row.path);

process.stdout.write(JSON.stringify({
    html,
    marked,
    // Within the row that has it, the marker precedes that row's own open
    // control -- compared per row, since every row has an open control.
    markBeforeOpen: rows.every(row => {
        const at = row.body.indexOf('explorer-tree-pin-mark');
        if (at === -1) { return true; }
        const open = row.body.search(/explorer-open-(folder|tab)-btn/);
        return open === -1 || at < open;
    }),
    markerCount: (html.match(/explorer-tree-pin-mark/g) || []).length
}));
"""

# Render, then paint over what was rendered.
#
# The render and the paint each decide which rows wear the marker, and they
# decided it from two different fields: the markup asks `entry.type`
# (directory/file) while the paint used to ask `data-explorer-context-kind`,
# which carries `entry_kind` (directory/file/link/other, and '' for a filtered
# row whose parent listing is not cached). Wherever those disagree the paint
# stripped a marker the render had just placed -- and the paint runs on every
# navigation and every pin write, so the marker appeared once and vanished on
# the next click.
#
# This harness is the agreement itself, executed: the rows the paint walks are
# built out of the real rendered markup rather than by hand, so the two halves
# cannot be given different inputs by the test.
ROUND_TRIP_HARNESS = SANDBOX + r"""
let nodeSeq = 0;

function attr(markup, name) {
    const at = markup.indexOf(name + '="');
    if (at === -1) { return ''; }
    const from = at + name.length + 2;
    return markup.slice(from, markup.indexOf('"', from));
}

/* One row stub per rendered row, carrying that row's own attributes and
   whether the render gave it a marker. */
function rowFromMarkup(markup) {
    const children = [];
    const row = {
        nodeId: ++nodeSeq,
        dataset: {
            explorerContextPath: attr(markup, 'data-explorer-context-path'),
            explorerContextKind: attr(markup, 'data-explorer-context-kind'),
            explorerGitScopePath: attr(markup, 'data-explorer-git-scope-path'),
            explorerGitScopeKind: attr(markup, 'data-explorer-git-scope-kind')
        },
        children,
        insertAdjacentHTML(position, added) {
            if (!added.includes('explorer-tree-pin-mark')) { throw new Error('not a mark'); }
            children.push({ className: 'explorer-tree-pin-mark', remove() {
                children.splice(children.indexOf(this), 1);
            } });
        },
        querySelector(selector) {
            const wanted = selector.split(',').map(part => part.trim().replace(/^\./, ''));
            return children.find(child => wanted.includes(child.className)) || null;
        }
    };
    if (markup.includes('explorer-tree-pin-mark')) {
        children.push({ className: 'explorer-tree-pin-mark', remove() {
            children.splice(children.indexOf(this), 1);
        } });
    }
    return row;
}

const pinned = process.argv[5] === '__none__' ? null : process.argv[5];
const pinnedKind = process.argv[6] || 'dir';
const sandbox = loadSandbox(process.argv[2], process.argv[3]);
installPane(sandbox, JSON.parse(process.argv[4]), ['web'], pinned, pinnedKind);

const html = sandbox.renderExplorerTreeNodes(sandbox.terminals[0], '', 0);
const rows = html
    .split('data-explorer-context-path="')
    .slice(1)
    .map(part => rowFromMarkup('data-explorer-context-path="' + part));

const hasMark = row => row.children.some(c => c.className === 'explorer-tree-pin-mark');
const marks = () => rows.filter(hasMark).map(row => row.dataset.explorerContextPath);
const rendered = marks();

const rootMark = { nodeId: ++nodeSeq, hidden: true };
const panel = {
    querySelector: selector => (
        selector === '[data-explorer-tree-pin-root]' ? rootMark : null
    ),
    querySelectorAll: selector => (selector === '.explorer-tree-row' ? rows : [])
};
sandbox.document.getElementById = id => (id === 'explorer-tree-panel-0' ? panel : null);

sandbox.applyExplorerTreeScopeMarks(0);
const painted = marks();
// A second pass must be a no-op too: the paint is run on every navigation.
sandbox.applyExplorerTreeScopeMarks(0);

process.stdout.write(JSON.stringify({
    rendered,
    painted,
    repainted: marks(),
    rootHidden: rootMark.hidden
}));
"""

# The Graph header's pin button, run in the real sidebar module against a panel
# stub that keeps node identity. `innerHTML` on the panel is a trap: the paint
# must be attribute-only, because the panel carries the commit-message textarea
# and re-rendering it takes the caret.
BUTTON_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const spec = JSON.parse(process.argv[5]);
let nodeSeq = 0;
let presentationWrites = 0;

const classes = new Set(['explorer-search-btn', 'explorer-git-pin-toggle']);
const button = {
    nodeId: ++nodeSeq,
    title: spec.button.title,
    attributes: { 'aria-pressed': spec.button.pressed, 'aria-label': spec.button.title },
    classList: {
        toggle(name, on) { if (on) { classes.add(name); } else { classes.delete(name); } },
        contains: name => classes.has(name)
    },
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name]; },
    addEventListener() {}
};

/* The caret the paint must not take: a focused commit-message textarea with a
   selection in it, standing in the same panel. */
const textarea = {
    focused: true,
    selection: [3, 9],
    focus() { this.focused = true; },
    setSelectionRange(a, b) { this.selection = [a, b]; }
};

const scopeClear = { nodeId: ++nodeSeq, hidden: spec.scopeClearHidden, addEventListener() {} };
let innerHtmlWrites = 0;
const panel = {
    nodeId: ++nodeSeq,
    get innerHTML() { return ''; },
    set innerHTML(value) { innerHtmlWrites += 1; textarea.focused = false; },
    classList: { add() {}, remove() {} },
    querySelector(selector) {
        if (selector === '[data-explorer-git-pin-toggle]') { return button; }
        if (selector === '[data-explorer-git-scope-clear]') { return scopeClear; }
        return null;
    },
    querySelectorAll: () => []
};

const sandbox = {
    console,
    document: {
        getElementById: id => (id === 'explorer-git-panel-0' ? panel : null),
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
        body: { dataset: {}, addEventListener() {} }
    },
    navigator: {},
    setTimeout,
    clearTimeout,
    requestAnimationFrame: () => 0,
    fetch: () => Promise.reject(new Error('a pin write may issue no request of its own')),
    terminals: [],
    sessionIds: [],
    notePanePresentationChanged: () => { presentationWrites += 1; },
    /* The panel renderer's page-side collaborators. The pin paint touches
       none of them; they are here so a pin *write* can run the real render
       that follows it instead of being kept away from it. */
    wireExplorerCopyPathMenu: () => {},
    renderExplorerGitFileRows: () => '',
    explorerGitGraphHtml: () => '',
    ensureExplorerGitCommitSearchState: pane => (
        pane.__search || (pane.__search = { open: false, query: '', activeIndex: 0, mode: 'subject' })
    ),
    EXPLORER_GIT_PIN_ICON: '<svg data-icon="pin"></svg>',
    EXPLORER_GIT_FOLLOW_ICON: '<svg data-icon="follow"></svg>',
    EXPLORER_GIT_SEARCH_ICON: '<svg data-icon="search"></svg>',
    EXPLORER_GIT_HASH_ICON: '<svg data-icon="hash"></svg>',
    EXPLORER_GIT_REVERT_ICON: '<svg data-icon="revert"></svg>',
    EXPLORER_GIT_TOGGLE_ICON: '<svg data-icon="git"></svg>',
    EXPLORER_FOLDER_ICON: '<svg data-icon="folder"></svg>',
    UI_PLUS_ICON: '<svg data-icon="plus"></svg>',
    UI_MINUS_ICON: '<svg data-icon="minus"></svg>',
    UI_CHEVRON_DOWN_ICON: '<svg></svg>',
    UI_CHEVRON_RIGHT_ICON: '<svg></svg>',
    escHtml: value => String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox);
process.argv.slice(6).forEach(path => {
    vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
});
vm.runInContext(fs.readFileSync(process.argv[4], 'utf8'), sandbox);

sandbox.__spec = spec;
vm.runInContext(`
    sessionIds[0] = 'sess-0';
    terminals[0] = { _explorerPath: __spec.browsed };
    if (__spec.pinned !== null) { terminals[0]._explorerGitPinnedPath = __spec.pinned; }
`, sandbox);

// The tree's own paint has its own cases below; here it is only counted, so
// that "both surfaces move together" is an observation.
const treeMarks = [];
sandbox.applyExplorerTreeScopeMarks = index => { treeMarks.push(index); };

/* The regression the end state cannot show: an unpin followed by a pin leaves
   the same pane field as one write. Count them. */
const writes = [];
const realWrite = sandbox.setExplorerGitPinnedScope;
sandbox.setExplorerGitPinnedScope = function (index, pinnedPath) {
    writes.push(pinnedPath);
    return realWrite.call(null, index, pinnedPath);
};
const loads = [];
sandbox.loadExplorerGitRepo = async index => { loads.push(index); };

const rendered = sandbox.explorerGitPinState(sandbox.terminals[0]);

async function main() {
    if (spec.action === 'click') {
        await sandbox.toggleExplorerGitPinHere(0);
    } else if (spec.action === 'clear') {
        await sandbox.clearExplorerGitPinnedScope(0);
    } else {
        sandbox.refreshExplorerGitScopeAffordances(0);
    }
    process.stdout.write(JSON.stringify({
        rendered,
        writes,
        loads: loads.length,
        presentationWrites,
        pinnedAfter: typeof sandbox.terminals[0]._explorerGitPinnedPath === 'string'
            ? sandbox.terminals[0]._explorerGitPinnedPath
            : null,
        pressed: button.getAttribute('aria-pressed'),
        title: button.title,
        ariaLabel: button.getAttribute('aria-label'),
        elsewhereClass: button.classList.contains('is-pinned-elsewhere'),
        buttonNodeId: button.nodeId,
        scopeClearHidden: scopeClear.hidden,
        innerHtmlWrites,
        treeMarks,
        textareaFocused: textarea.focused,
        textareaSelection: textarea.selection
    }));
}
main().catch(error => { console.error(error); process.exit(1); });
"""

# The scope chip and the pin button as rendered markup, out of the real panel
# renderer: "Clear pin is there only when the pin is elsewhere" is a property
# of what is built, not only of what is later painted onto it.
PANEL_RENDER_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const spec = JSON.parse(process.argv[5]);
let html = '';
const panel = {
    get innerHTML() { return html; },
    set innerHTML(value) { html = value; },
    classList: { add() {}, remove() {} },
    querySelector: () => null,
    querySelectorAll: () => []
};

const sandbox = {
    console,
    document: {
        getElementById: id => (id === 'explorer-git-panel-0' ? panel : null),
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
        body: { dataset: {}, addEventListener() {} }
    },
    navigator: {},
    setTimeout,
    clearTimeout,
    requestAnimationFrame: () => 0,
    terminals: [],
    sessionIds: [],
    notePanePresentationChanged: () => {},
    wireExplorerCopyPathMenu: () => {},
    renderExplorerGitFileRows: () => '',
    explorerGitGraphHtml: () => '',
    EXPLORER_GIT_PIN_ICON: '<svg data-icon="pin"></svg>',
    EXPLORER_GIT_FOLLOW_ICON: '<svg data-icon="follow"></svg>',
    EXPLORER_GIT_SEARCH_ICON: '<svg data-icon="search"></svg>',
    EXPLORER_GIT_HASH_ICON: '<svg data-icon="hash"></svg>',
    EXPLORER_GIT_REVERT_ICON: '<svg data-icon="revert"></svg>',
    EXPLORER_GIT_TOGGLE_ICON: '<svg data-icon="git"></svg>',
    EXPLORER_FOLDER_ICON: '<svg data-icon="folder"></svg>',
    UI_PLUS_ICON: '<svg data-icon="plus"></svg>',
    UI_MINUS_ICON: '<svg data-icon="minus"></svg>',
    UI_CHEVRON_DOWN_ICON: '<svg></svg>',
    UI_CHEVRON_RIGHT_ICON: '<svg></svg>',
    escHtml: value => String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox);
process.argv.slice(6).forEach(path => {
    vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
});
vm.runInContext(fs.readFileSync(process.argv[4], 'utf8'), sandbox);

sandbox.__spec = spec;
vm.runInContext(`
    sessionIds[0] = 'sess-0';
    terminals[0] = {
        _explorerPath: __spec.browsed,
        _explorerGitFollowBrowsing: __spec.following,
        _explorerGitRepoLoaded: true,
        _explorerGitRepo: { git: { branch: 'main', repo_name: 'gridvibe' }, commits: [] }
    };
    if (__spec.pinned !== null) { terminals[0]._explorerGitPinnedPath = __spec.pinned; }
`, sandbox);

sandbox.renderExplorerGitPanel(0);

// The button's own slice of the markup, so an attribute belonging to a
// neighbouring control is never read as the button's.
const at = html.indexOf('data-explorer-git-pin-toggle');
const buttonHtml = at === -1
    ? ''
    : html.slice(html.lastIndexOf('<button', at), html.indexOf('</button>', at));

process.stdout.write(JSON.stringify({
    html,
    buttonHtml,
    scopeClearCount: (html.match(/data-explorer-git-scope-clear/g) || []).length,
    scopeClearHidden: /data-explorer-git-scope-clear[^>]*\bhidden\b/.test(html)
}));
"""


PREDICATE_HARNESS = r"""
const api = require(process.argv[2]);
const cases = JSON.parse(process.argv[3]);
process.stdout.write(JSON.stringify(
    cases.map(args => api.explorerGitPathIsPinned(...args))
));
"""


def _run(script, *args):
    with TemporaryDirectory() as script_dir:
        path = Path(script_dir) / "harness.js"
        path.write_text(script, encoding="utf-8")
        return subprocess.run(
            [NODE, str(path), *args], capture_output=True, text=True, check=False
        )


@unittest.skipUnless(NODE, "Node.js is required for explorer tree pin mark tests")
class ExplorerGitPinPredicateTestCase(unittest.TestCase):
    """One predicate answers the marker, so its edges are the marker's edges."""

    def _ask(self, cases):
        completed = _run(PREDICATE_HARNESS, str(PIN_JS), json.dumps(cases))
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_a_pin_matches_its_own_path_and_nothing_else(self):
        self.assertEqual(
            self._ask([["web/static", "web/static"], ["web/static", "docs"]]),
            [True, False],
        )

    def test_an_ancestor_pin_is_not_a_pin_here(self):
        # Exact equality, never a prefix match: a pin on web/ is a pin on web/,
        # and marking web/static too would point at a scope nobody chose.
        self.assertEqual(
            self._ask([["web", "web/static"], ["web/static", "web"]]),
            [False, False],
        )
        # A prefix that is not a path boundary must not match either.
        self.assertEqual(self._ask([["web", "website"]]), [False])

    def test_the_empty_string_is_the_root_and_is_a_real_pin(self):
        self.assertEqual(self._ask([["", ""], ["", "docs"]]), [True, False])

    def test_no_pin_is_the_field_s_type_not_its_truthiness(self):
        # `''` is falsy and is a pin; `null`/`undefined` are the absence of one.
        self.assertEqual(self._ask([[None, ""], [None, "docs"]]), [False, False])

    def test_the_same_path_with_a_different_kind_is_not_the_pin(self):
        self.assertEqual(
            self._ask(
                [
                    ["src/app.js", "src/app.js", "file", "file"],
                    ["src/app.js", "src/app.js", "file", "dir"],
                    ["src", "src", "dir", "file"],
                ]
            ),
            [True, False, False],
        )


@unittest.skipUnless(NODE, "Node.js is required for explorer Git menu tests")
class ExplorerGitScopeMenuPolicyTestCase(unittest.TestCase):
    HARNESS = r"""
const api = require(process.argv[2]);
const cases = JSON.parse(process.argv[3]);
process.stdout.write(JSON.stringify({
    menus: cases.map(spec => api.explorerGitScopeMenuItem(spec)),
    follow: JSON.parse(process.argv[4]).map(spec => api.explorerGitFollowMenuItem(spec))
}));
"""

    def _ask(self, cases, follow=None):
        completed = _run(
            self.HARNESS,
            str(PIN_JS),
            json.dumps(cases),
            json.dumps(follow if follow is not None else []),
        )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_pin_unpin_and_disabled_outside_worktree_are_one_menu_policy(self):
        result = self._ask(
            [
                {
                    "pinnedPath": "src/app.js",
                    "pinnedKind": "file",
                    "targetPath": "src/app.js",
                    "targetKind": "file",
                },
                {
                    "pinnedPath": "src/app.js",
                    "pinnedKind": "file",
                    "targetPath": "src/lib.js",
                    "targetKind": "file",
                },
                {
                    "targetPath": "outside.txt",
                    "targetKind": "file",
                    "worktreeAvailable": False,
                },
            ]
        )
        unpin, repin, outside = result["menus"]
        self.assertEqual(unpin["label"], "Unpin Git")
        self.assertEqual(unpin["action"], "unpin")
        self.assertEqual(repin["label"], "Pin Git here")
        self.assertFalse(repin["disabled"])
        self.assertTrue(outside["disabled"])
        self.assertIn("not inside a Git worktree", outside["title"])

    def test_multi_selection_drops_the_pin_and_the_follow_entry_alike(self):
        """Both entries are one exact path, so several rows name neither."""
        multi = {"targetPath": "src/app.js", "targetKind": "file", "targetCount": 3}
        result = self._ask([multi], follow=[multi])
        self.assertIsNone(result["menus"][0])
        self.assertIsNone(result["follow"][0])

    def test_follow_asks_whether_it_is_here_not_whether_it_is_on(self):
        """The pin button's rule, on the row: only the followed row unfollows.

        Answering "is Follow on" left a reader following one folder with no
        way to say "follow this file" -- every other row offered to switch
        Follow off instead of to move it.
        """
        off, here, elsewhere, kind_differs = self._ask(
            [],
            follow=[
                {"following": False, "targetPath": "src/app.js", "targetKind": "file"},
                {
                    "following": True,
                    "followedPath": "src/app.js",
                    "followedKind": "file",
                    "targetPath": "src/app.js",
                    "targetKind": "file",
                },
                {
                    "following": True,
                    "followedPath": "src",
                    "followedKind": "dir",
                    "targetPath": "src/app.js",
                    "targetKind": "file",
                },
                {
                    "following": True,
                    "followedPath": "src/app.js",
                    "followedKind": "dir",
                    "targetPath": "src/app.js",
                    "targetKind": "file",
                },
            ],
        )["follow"]
        self.assertEqual(off["action"], "follow")
        self.assertEqual(here["action"], "unfollow")
        self.assertEqual(here["label"], "Unfollow Git browsing")
        self.assertEqual(elsewhere["action"], "follow")
        self.assertEqual(elsewhere["label"], "Follow Git browsing")
        self.assertIn("file", elsewhere["title"])
        # A path is a scope only together with its kind, exactly as the pin is.
        self.assertEqual(kind_differs["action"], "follow")

    def test_a_disabled_follow_entry_is_disabled_and_never_dropped(self):
        item = self._ask(
            [],
            follow=[{"targetPath": "src", "targetKind": "dir", "disabled": True}],
        )["follow"][0]
        self.assertEqual(item["label"], "Follow Git browsing")
        self.assertTrue(item["disabled"])


@unittest.skipUnless(NODE, "Node.js is required for explorer Git scope tests")
class ExplorerGitBrowseTargetTestCase(unittest.TestCase):
    """The browsed scope: a highlighted row overrides it until navigation moves.

    Follow used to read navigation alone, so choosing Follow on a file row in
    the directory listing scoped Git to the folder the listing was showing.
    The override carries the derived scope it was made against, which is what
    makes "until the next browsing act" need no invalidation hook: navigation
    moves the derived scope, the two stop agreeing, and the override is gone.
    """

    HARNESS = r"""
const api = require(process.argv[2]);
const spec = JSON.parse(process.argv[3]);
process.stdout.write(JSON.stringify(spec.map(step => api.explorerGitBrowsedScope(
    step.basePath,
    step.baseKind,
    step.override === null || step.override === undefined
        ? null
        : api.explorerGitBrowseOverride(
            step.override.path,
            step.override.kind,
            step.override.basePath,
            step.override.baseKind
        )
))));
"""

    def _ask(self, steps):
        completed = _run(self.HARNESS, str(PIN_JS), json.dumps(steps))
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_no_override_is_plain_navigation(self):
        self.assertEqual(
            self._ask([{"basePath": "web/static", "baseKind": "dir", "override": None}]),
            [{"path": "web/static", "kind": "dir"}],
        )

    def test_a_file_highlighted_in_a_listing_beats_the_folder_it_lists(self):
        override = {
            "path": "web/api.py",
            "kind": "file",
            "basePath": "web",
            "baseKind": "dir",
        }
        self.assertEqual(
            self._ask([{"basePath": "web", "baseKind": "dir", "override": override}]),
            [{"path": "web/api.py", "kind": "file"}],
        )

    def test_the_next_navigation_supersedes_it_with_nothing_to_clear(self):
        override = {
            "path": "web/api.py",
            "kind": "file",
            "basePath": "web",
            "baseKind": "dir",
        }
        held, moved_folder, opened_file = self._ask(
            [
                {"basePath": "web", "baseKind": "dir", "override": override},
                {"basePath": "docs", "baseKind": "dir", "override": override},
                {"basePath": "web/explorer.py", "baseKind": "file", "override": override},
            ]
        )
        self.assertEqual(held, {"path": "web/api.py", "kind": "file"})
        self.assertEqual(moved_folder, {"path": "docs", "kind": "dir"})
        self.assertEqual(opened_file, {"path": "web/explorer.py", "kind": "file"})

    def test_the_explorer_root_is_a_real_base_and_a_real_override(self):
        # '' is a scope, not an absence, exactly as it is for the pin.
        self.assertEqual(
            self._ask(
                [
                    {
                        "basePath": "",
                        "baseKind": "dir",
                        "override": {
                            "path": "",
                            "kind": "dir",
                            "basePath": "",
                            "baseKind": "dir",
                        },
                    }
                ]
            ),
            [{"path": "", "kind": "dir"}],
        )

    def test_an_override_is_never_built_from_an_absent_path(self):
        self.assertEqual(
            self._ask([{"basePath": "web", "baseKind": "dir", "override": {"path": None}}]),
            [{"path": "web", "kind": "dir"}],
        )


@unittest.skipUnless(NODE, "Node.js is required for explorer tree pin mark tests")
class ExplorerTreePinMarkRenderTestCase(unittest.TestCase):
    """The rendered tree: which row wears the marker, and how many do."""

    def _render(self, pinned, kind="dir"):
        completed = _run(
            RENDER_HARNESS,
            str(PIN_JS),
            str(TREE_JS),
            json.dumps(FIXTURE_CHILDREN),
            "__none__" if pinned is None else pinned,
            kind,
        )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_the_marker_lands_on_exactly_the_pinned_row(self):
        result = self._render("web")
        self.assertEqual(result["marked"], ["web"])
        self.assertEqual(result["markerCount"], 1)

    def test_a_nested_pin_is_marked_on_its_own_row(self):
        result = self._render("web/static")
        self.assertEqual(result["marked"], ["web/static"])
        self.assertEqual(result["markerCount"], 1)

    def test_no_pin_marks_no_row(self):
        result = self._render(None)
        self.assertEqual(result["marked"], [])
        self.assertEqual(result["markerCount"], 0)

    def test_a_root_pin_marks_no_row_because_the_body_lists_the_root_s_children(self):
        # Honest rather than approximate: the tree body has no row for the
        # explorer root, so a root pin is reported by the FILES head's marker
        # (see the paint tests) and never by a nearby row standing in for it.
        result = self._render("")
        self.assertEqual(result["marked"], [])
        self.assertEqual(result["markerCount"], 0)

    def test_a_pin_on_a_folder_that_is_not_rendered_marks_nothing(self):
        # A collapsed ancestor, or a path outside the tree entirely. The Graph
        # header still names the scope; the tree does not auto-expand to it.
        self.assertEqual(self._render("web/static/js")["marked"], [])
        self.assertEqual(self._render("elsewhere")["marked"], [])

    def test_the_marker_is_a_span_carrying_the_documented_hooks(self):
        # Markup hooks are the documented source-assertion exception: the class
        # the CSS and the paint both key on, and the title that says what the
        # marker means. A <span>, because the tree *reports* the pin -- moving
        # it is the Graph header button's job.
        html = self._render("web")["html"]
        self.assertIn('class="explorer-tree-pin-mark"', html)
        self.assertIn('title="Git scope pinned here"', html)
        self.assertIn('aria-label="Git scope pinned here"', html)
        self.assertIn('role="img"', html)
        marker_at = html.index("explorer-tree-pin-mark")
        self.assertEqual(html.rfind("<span", 0, marker_at), html.rfind("<", 0, marker_at))

    def test_each_tree_row_exposes_its_exact_git_scope_to_the_shared_menu(self):
        html = self._render(None)["html"]
        self.assertIn('data-explorer-git-scope-path="web"', html)
        self.assertIn('data-explorer-git-scope-kind="dir"', html)
        self.assertIn('data-explorer-git-scope-path="readme.md"', html)
        self.assertIn('data-explorer-git-scope-kind="file"', html)
        self.assertEqual(
            html.count('data-explorer-git-scope-surface="tree"'),
            html.count('data-explorer-context-path="'),
        )

    def test_the_marker_sits_immediately_left_of_the_open_control(self):
        # Per row, not per document: every row carries an open control, so the
        # comparison is only meaningful inside the row that has the marker.
        self.assertTrue(self._render("web")["markBeforeOpen"])
        self.assertTrue(self._render("readme.md", "file")["markBeforeOpen"])

    def test_a_file_pin_marks_the_file_row_and_not_a_same_spelled_directory(self):
        result = self._render("readme.md", "file")
        self.assertEqual(result["marked"], ["readme.md"])
        self.assertEqual(result["markerCount"], 1)


@unittest.skipUnless(NODE, "Node.js is required for explorer tree pin mark tests")
class ExplorerTreePinMarkRenderPaintAgreementTestCase(unittest.TestCase):
    """The render and the paint answer "is the pin here" the same way.

    They are two halves of one marker: the row markup places it, and the paint
    moves it afterwards on every navigation and every pin write. If they read
    different fields, the marker appears on a full render and is stripped by
    the next click -- so the agreement is asserted by running the paint over
    the markup the render actually produced, never over rows built by hand.
    """

    def _round_trip(self, children, pinned, kind="dir"):
        completed = _run(
            ROUND_TRIP_HARNESS,
            str(PIN_JS),
            str(TREE_JS),
            json.dumps(children),
            "__none__" if pinned is None else pinned,
            kind,
        )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def _assert_agrees(self, children, pinned, kind, expected):
        result = self._round_trip(children, pinned, kind)
        self.assertEqual(result["rendered"], expected)
        # The paint neither adds a marker the render withheld nor removes one
        # the render placed, and running it again changes nothing either.
        self.assertEqual(result["painted"], result["rendered"])
        self.assertEqual(result["repainted"], result["rendered"])

    def test_a_symlink_row_keeps_the_marker_the_render_gave_it(self):
        # entry_kind "link", type "file": the paint must read the row's Git
        # scope kind ("file"), not its entry kind.
        self._assert_agrees(DIVERGENT_KIND_CHILDREN, "link.txt", "file", ["link.txt"])

    def test_a_filtered_row_with_no_entry_kind_keeps_its_marker(self):
        # The shape a search hit takes when its parent listing is not cached.
        self._assert_agrees(DIVERGENT_KIND_CHILDREN, "notes.md", "file", ["notes.md"])

    def test_the_two_halves_agree_for_every_row_in_the_fixture(self):
        for path, kind in (
            ("web", "dir"),
            ("link.txt", "file"),
            ("notes.md", "file"),
            (None, "dir"),
        ):
            with self.subTest(pinned=path, kind=kind):
                self._assert_agrees(
                    DIVERGENT_KIND_CHILDREN, path, kind, [] if path is None else [path]
                )

    def test_a_directory_pin_is_unaffected_by_a_file_kind_row_beside_it(self):
        self._assert_agrees(FIXTURE_CHILDREN, "web/static", "dir", ["web/static"])

    def test_a_root_pin_still_marks_the_head_and_no_row(self):
        result = self._round_trip(DIVERGENT_KIND_CHILDREN, "", "dir")
        self.assertEqual(result["rendered"], [])
        self.assertEqual(result["painted"], [])
        self.assertFalse(result["rootHidden"])


@unittest.skipUnless(NODE, "Node.js is required for explorer tree pin mark tests")
class ExplorerTreePinMarkPaintTestCase(unittest.TestCase):
    """The move is a paint: two rows change, the tree body does not."""

    ROWS = [
        {"path": "docs", "kind": "directory"},
        {"path": "web", "kind": "directory", "marked": True},
        {"path": "readme.md", "kind": "file"},
    ]

    def _paint(self, pinned, rows=None, kind="dir"):
        completed = _run(
            PAINT_HARNESS,
            str(PIN_JS),
            str(TREE_JS),
            "__none__" if pinned is None else pinned,
            json.dumps(self.ROWS if rows is None else rows),
            kind,
        )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_the_mark_moves_between_two_rows_without_rebuilding_them(self):
        result = self._paint("docs")
        self.assertEqual(result["marked"], ["docs"])
        # The rows the reader is looking at are the same nodes: a rebuild would
        # have replaced them, and taken the scroll offset with them.
        self.assertTrue(result["rowIdsUnchanged"])
        self.assertEqual(result["scrollTop"], 412)
        self.assertEqual(result["scrollLeft"], 7)

    def test_a_painted_mark_lands_left_of_the_row_s_open_control(self):
        self.assertTrue(self._paint("docs")["markBeforeOpen"])

    def test_clearing_the_pin_takes_the_mark_off_and_adds_none(self):
        result = self._paint(None)
        self.assertEqual(result["marked"], [])
        self.assertTrue(result["rowIdsUnchanged"])
        self.assertEqual(result["scrollTop"], 412)

    def test_a_file_row_can_carry_the_mark_beside_its_open_in_tab_button(self):
        result = self._paint("readme.md", kind="file")
        self.assertEqual(result["marked"], ["readme.md"])
        self.assertTrue(result["markBeforeOpen"])

    def test_painting_the_pin_that_is_already_shown_changes_nothing(self):
        # Idempotent: a freshly rendered tree already carries the marker in its
        # row markup, so the paint that follows a render must be a no-op.
        result = self._paint("web")
        self.assertEqual(result["marked"], ["web"])
        self.assertTrue(result["rowIdsUnchanged"])

    def test_the_head_s_root_marker_is_shown_only_for_a_root_pin(self):
        # The tree body lists the root's children, so the FILES head stands in
        # for the explorer root. Toggled by attribute, never added and removed:
        # rebuilding the head would drop the caret out of the name filter.
        self.assertFalse(self._paint("")["rootHidden"])
        self.assertTrue(self._paint("web")["rootHidden"])
        self.assertTrue(self._paint(None)["rootHidden"])

    def test_a_root_pin_marks_the_head_and_no_row(self):
        result = self._paint("")
        self.assertEqual(result["marked"], [])
        self.assertFalse(result["rootHidden"])


@unittest.skipUnless(NODE, "Node.js is required for explorer tree pin mark tests")
class ExplorerGitPinButtonStateTestCase(unittest.TestCase):
    """The button asks "is the pin *here*", not "is there a pin"."""

    def _state(self, pinned, browsed):
        script = (
            "const api = require(process.argv[2]);"
            "const [pinned, browsed] = JSON.parse(process.argv[3]);"
            "process.stdout.write(JSON.stringify("
            "api.explorerGitPinButtonState(pinned, browsed)));"
        )
        completed = _run(script, str(PIN_JS), json.dumps([pinned, browsed]))
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_the_pin_here_state_is_pressed_and_clears(self):
        state = self._state("web/static/js", "web/static/js")
        self.assertEqual(state["state"], "here")
        self.assertTrue(state["pressed"])
        self.assertEqual(state["title"], "Clear pinned Git folder")
        # And the named clear stands beside it. It used to be withheld here on
        # the grounds that the pressed button already clears -- which took the
        # only *named* way to unpin away at exactly the folder a reader is
        # standing in when they decide to.
        self.assertTrue(state["clearAvailable"])

    def test_a_pin_elsewhere_is_not_pressed_and_names_where_it_is(self):
        state = self._state("web/static/js", "docs")
        self.assertEqual(state["state"], "elsewhere")
        self.assertFalse(state["pressed"])
        # The title has to say *where*, or the reader cannot tell that a click
        # is about to move something rather than create it.
        self.assertIn("web/static/js", state["title"])
        self.assertTrue(state["title"].startswith("Pin Git to this folder"))
        self.assertTrue(state["clearAvailable"])

    def test_no_pin_offers_to_pin_here_and_no_clear(self):
        state = self._state(None, "docs")
        self.assertEqual(state["state"], "none")
        self.assertFalse(state["pressed"])
        self.assertEqual(state["title"], "Pin Git to the current folder")
        self.assertFalse(state["clearAvailable"])

    def test_a_root_pin_is_here_at_the_root_and_elsewhere_below_it(self):
        # `''` is a real pin, so a root pin browsed at the root is pressed --
        # the state is the field's type, never its truthiness.
        self.assertEqual(self._state("", "")["state"], "here")
        self.assertEqual(self._state("", "web")["state"], "elsewhere")
        self.assertIn("root", self._state("", "web")["title"])

    def test_an_ancestor_pin_is_elsewhere_never_here(self):
        # The reason the predicate is exact equality: an ancestor match would
        # let the button clear a pin the user made in another folder.
        self.assertEqual(self._state("web", "web/static")["state"], "elsewhere")
        self.assertEqual(self._state("web/static", "web")["state"], "elsewhere")
        self.assertEqual(self._state("web", "website")["state"], "elsewhere")

    def test_the_marker_and_the_button_agree_for_every_pair(self):
        # One predicate, two surfaces: the row the marker lands on is exactly
        # the browsed path the button calls `here`. A disagreement here would
        # mean the two stopped sharing it.
        pairs = [
            ("web", "web"), ("web", "web/static"), ("", ""), ("", "docs"),
            ("web/static", "web/static"), (None, "web"), (None, ""),
        ]
        script = (
            "const api = require(process.argv[2]);"
            "process.stdout.write(JSON.stringify("
            "JSON.parse(process.argv[3]).map(([pin, path]) => ["
            "api.explorerGitPathIsPinned(pin, path),"
            "api.explorerGitPinButtonState(pin, path).state === 'here'])));"
        )
        completed = _run(script, str(PIN_JS), json.dumps(pairs))
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        for marked, here in json.loads(completed.stdout):
            self.assertEqual(marked, here)


@unittest.skipUnless(NODE, "Node.js is required for explorer tree pin mark tests")
class ExplorerGitPinButtonClickTestCase(unittest.TestCase):
    """One gesture is one write, and never clears somebody else's pin."""

    def _act(self, pinned, browsed, action="click", scope_clear_hidden=True):
        completed = _run(
            BUTTON_HARNESS,
            str(PIN_JS),
            str(TREE_JS),
            str(SIDEBAR_JS),
            json.dumps({
                "pinned": pinned,
                "browsed": browsed,
                "action": action,
                "scopeClearHidden": scope_clear_hidden,
                "button": {"title": "stale title", "pressed": "stale"},
            }),
            *[str(path) for path in SIDEBAR_POLICY_JS],
        )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_clicking_while_pinned_elsewhere_re_pins_in_exactly_one_write(self):
        result = self._act("web/static/js", "docs")
        self.assertEqual(result["pinnedAfter"], "docs")
        # The end state cannot tell an unpin-then-pin from one write, and the
        # difference is a visible flash at the intermediate root scope plus a
        # second repository round trip -- so count them.
        self.assertEqual(result["writes"], ["docs"])
        self.assertEqual(result["loads"], 1)
        self.assertEqual(result["presentationWrites"], 1)

    def test_clicking_while_the_pin_is_here_clears_it(self):
        result = self._act("docs", "docs")
        self.assertIsNone(result["pinnedAfter"])
        self.assertEqual(result["writes"], [None])
        self.assertEqual(result["loads"], 1)

    def test_clicking_with_no_pin_pins_the_browsed_folder(self):
        result = self._act(None, "docs")
        self.assertEqual(result["pinnedAfter"], "docs")
        self.assertEqual(result["writes"], ["docs"])

    def test_clicking_at_the_root_pins_the_root_rather_than_nothing(self):
        result = self._act(None, "")
        self.assertEqual(result["pinnedAfter"], "")
        self.assertEqual(result["writes"], [""])
        # And clicking again there clears it, because '' equals ''.
        self.assertIsNone(self._act("", "")["pinnedAfter"])

    def test_an_ancestor_pin_is_never_cleared_by_a_click_in_a_child(self):
        result = self._act("web", "web/static")
        self.assertEqual(result["pinnedAfter"], "web/static")
        self.assertEqual(result["writes"], ["web/static"])

    def test_the_write_repaints_both_surfaces(self):
        # The tree marker and the button move on the same event, from the one
        # entry point, so they cannot report different folders.
        result = self._act(None, "docs")
        self.assertEqual(result["treeMarks"], [0])
        self.assertEqual(result["pressed"], "true")


@unittest.skipUnless(NODE, "Node.js is required for explorer tree pin mark tests")
class ExplorerGitPinButtonPaintTestCase(unittest.TestCase):
    """Navigation repaints the button attribute-only -- never the panel."""

    def _paint(self, pinned, browsed, scope_clear_hidden=True):
        completed = _run(
            BUTTON_HARNESS,
            str(PIN_JS),
            str(TREE_JS),
            str(SIDEBAR_JS),
            json.dumps({
                "pinned": pinned,
                "browsed": browsed,
                "action": "paint",
                "scopeClearHidden": scope_clear_hidden,
                "button": {"title": "stale title", "pressed": "stale"},
            }),
            *[str(path) for path in SIDEBAR_POLICY_JS],
        )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_walking_into_the_pinned_folder_presses_the_button(self):
        result = self._paint("docs", "docs")
        self.assertEqual(result["pressed"], "true")
        self.assertEqual(result["title"], "Clear pinned Git folder")
        self.assertFalse(result["elsewhereClass"])

    def test_walking_away_from_the_pin_marks_it_as_elsewhere(self):
        result = self._paint("docs", "web/static")
        self.assertEqual(result["pressed"], "false")
        self.assertIn("docs", result["title"])
        # aria-pressed is binary, so the third state is carried beside it --
        # without the class, "no pin" and "pin elsewhere" look identical.
        self.assertTrue(result["elsewhereClass"])

    def test_the_class_comes_off_again_when_the_pin_is_cleared(self):
        result = self._paint(None, "web/static")
        self.assertFalse(result["elsewhereClass"])
        self.assertEqual(result["pressed"], "false")
        self.assertEqual(result["title"], "Pin Git to the current folder")

    def test_the_title_and_the_aria_label_say_the_same_thing(self):
        result = self._paint("docs", "web")
        self.assertEqual(result["ariaLabel"], result["title"])

    def test_the_paint_replaces_no_markup_and_keeps_the_caret(self):
        result = self._paint("docs", "web")
        # The panel carries the commit-message textarea and the commit-search
        # input: re-rendering it to move a pin would take the caret with it.
        self.assertEqual(result["innerHtmlWrites"], 0)
        self.assertTrue(result["textareaFocused"])
        self.assertEqual(result["textareaSelection"], [3, 9])
        # And the button the reader may be hovering is the same node.
        self.assertEqual(result["buttonNodeId"], self._paint("docs", "web")["buttonNodeId"])

    def test_the_scope_chip_s_clear_pin_is_shown_wherever_a_pin_is(self):
        # Toggled by `hidden`, never added and removed, so a write can move it
        # before the reload has re-rendered the panel. It answers "is there a
        # pin", so the pinned folder shows it too and plain navigation between
        # the two never takes it away.
        self.assertFalse(self._paint("docs", "web")["scopeClearHidden"])
        self.assertFalse(self._paint("docs", "docs")["scopeClearHidden"])
        # No pin, nothing to clear: an inert control is worse than none.
        self.assertTrue(self._paint(None, "web", scope_clear_hidden=False)["scopeClearHidden"])

    def test_the_scope_chip_s_clear_goes_through_the_one_writer(self):
        result = self._act_clear("web/static/js", "docs")
        self.assertIsNone(result["pinnedAfter"])
        self.assertEqual(result["writes"], [None])
        self.assertEqual(result["loads"], 1)

    def test_the_scope_chip_s_clear_also_unpins_the_folder_being_browsed(self):
        # The row's clear and the pressed button are two doors to one write:
        # standing on the pin, either one removes it, and neither re-pins.
        result = self._act_clear("docs", "docs")
        self.assertIsNone(result["pinnedAfter"])
        self.assertEqual(result["writes"], [None])
        self.assertEqual(result["loads"], 1)

    def _act_clear(self, pinned, browsed):
        completed = _run(
            BUTTON_HARNESS,
            str(PIN_JS),
            str(TREE_JS),
            str(SIDEBAR_JS),
            json.dumps({
                "pinned": pinned,
                "browsed": browsed,
                "action": "clear",
                "scopeClearHidden": False,
                "button": {"title": "stale title", "pressed": "stale"},
            }),
            *[str(path) for path in SIDEBAR_POLICY_JS],
        )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


@unittest.skipUnless(NODE, "Node.js is required for explorer tree pin mark tests")
class ExplorerGitPinPanelMarkupTestCase(unittest.TestCase):
    """What the rendered panel says before anything is painted onto it."""

    def _render(self, pinned, browsed, following=False):
        completed = _run(
            PANEL_RENDER_HARNESS,
            str(PIN_JS),
            str(TREE_JS),
            str(SIDEBAR_JS),
            json.dumps({"pinned": pinned, "browsed": browsed, "following": following}),
            *[str(path) for path in SIDEBAR_POLICY_JS],
        )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_the_rendered_button_already_carries_its_state(self):
        # A render and the paint must agree, or the button flickers between
        # them on every load.
        here = self._render("docs", "docs")["buttonHtml"]
        self.assertIn('aria-pressed="true"', here)
        self.assertNotIn("is-pinned-elsewhere", here)

        elsewhere = self._render("docs", "web")["buttonHtml"]
        self.assertIn('aria-pressed="false"', elsewhere)
        self.assertIn("is-pinned-elsewhere", elsewhere)
        self.assertIn("docs", elsewhere)

        none = self._render(None, "web")["buttonHtml"]
        self.assertIn('aria-pressed="false"', none)
        self.assertNotIn("is-pinned-elsewhere", none)

    def test_the_scope_chip_carries_exactly_one_clear_pin(self):
        result = self._render("docs", "web")
        self.assertEqual(result["scopeClearCount"], 1)
        self.assertFalse(result["scopeClearHidden"])

    def test_the_clear_pin_is_offered_on_the_pinned_folder_too(self):
        # Still exactly one, and visible: the pressed button is a shortcut to
        # the same write, not the only way out of a pin.
        result = self._render("docs", "docs")
        self.assertEqual(result["scopeClearCount"], 1)
        self.assertFalse(result["scopeClearHidden"])

    def test_following_without_a_pin_carries_no_clear_at_all(self):
        # Follow gets its own row and names its scope, but Clear pin belongs to
        # the row that names the pin -- and there is no pin, so no such row.
        result = self._render(None, "web", following=True)
        self.assertEqual(result["scopeClearCount"], 0)


@unittest.skipUnless(NODE, "Node.js is required for explorer tree pin mark tests")
class ExplorerGitScopeLinesTestCase(unittest.TestCase):
    """A pin and a live Follow are two scopes, so they are two rows.

    They used to share one, which showed the *effective* scope: with Follow on
    it named the browsed folder, wore the chain icon, and still carried
    **Clear pin** -- an action about a path that was not on the row. Nothing
    said where the pin was, and the only button that could move something
    pointed at the wrong one of the two.
    """

    def _lines(self, pinned, browsed, following, pinned_kind="dir", browsed_kind="dir"):
        script = (
            "const api = require(process.argv[2]);"
            "const args = JSON.parse(process.argv[3]);"
            "process.stdout.write(JSON.stringify("
            "api.explorerGitScopeLines(...args)));"
        )
        completed = _run(
            script,
            str(PIN_JS),
            json.dumps([pinned, browsed, following, pinned_kind, browsed_kind]),
        )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_the_default_scope_gets_no_row_at_all(self):
        # An unpinned, non-following pane is scoped to the explorer root;
        # naming the default on every pane is noise.
        self.assertEqual(self._lines(None, "web", False), [])

    def test_a_pin_alone_is_one_row_naming_the_pinned_path(self):
        lines = self._lines("open5gs/docs", "open5gs/lib", False)
        self.assertEqual([line["kind"] for line in lines], ["pin"])
        self.assertEqual(lines[0]["label"], "open5gs/docs")
        self.assertIn("Git scope pinned to: open5gs/docs", lines[0]["title"])
        self.assertFalse(lines[0]["overridden"])

    def test_follow_alone_is_one_row_naming_the_browsed_folder(self):
        lines = self._lines(None, "open5gs/lib", True)
        self.assertEqual([line["kind"] for line in lines], ["follow"])
        self.assertEqual(lines[0]["label"], "open5gs/lib")
        self.assertIn("follows the browsed folder", lines[0]["title"])
        # Nothing to clear: there is no pin.
        self.assertFalse(lines[0]["clearAvailable"])

    def test_file_scopes_name_the_file_and_record_its_kind(self):
        pinned = self._lines(
            "src/components/app.js", "docs/guide.md", False, "file", "file"
        )[0]
        followed = self._lines(
            None, "docs/guide.md", True, "dir", "file"
        )[0]
        self.assertEqual(pinned["label"], "app.js")
        self.assertEqual(pinned["scopeKind"], "file")
        self.assertEqual(followed["label"], "guide.md")
        self.assertIn("browsed file", followed["title"])

    def test_a_file_row_is_short_on_the_row_and_whole_in_its_title(self):
        """A leaf is what fits the column; a leaf is not what identifies a
        scope. Two files called app.js in different folders are one label and
        two pins, and a pin the reader cannot tell from another one reads as a
        pin that was lost -- which is the failure this row exists to prevent.
        """
        pinned = self._lines(
            "src/components/app.js", "docs", False, "file", "dir"
        )[0]
        self.assertEqual(pinned["label"], "app.js")
        self.assertEqual(pinned["path"], "src/components/app.js")
        self.assertIn("Git scope pinned to: src/components/app.js", pinned["title"])

        followed = self._lines(None, "docs/deep/guide.md", True, "dir", "file")[0]
        self.assertEqual(followed["label"], "guide.md")
        self.assertEqual(followed["path"], "docs/deep/guide.md")
        self.assertIn("docs/deep/guide.md", followed["title"])

    def test_a_folder_row_has_nothing_to_abbreviate(self):
        line = self._lines("open5gs/docs", "web", False)[0]
        self.assertEqual(line["label"], line["path"])
        self.assertEqual(line["path"], "open5gs/docs")

    def test_the_root_is_named_root_in_the_path_as_well_as_the_label(self):
        line = self._lines("", "web", False)[0]
        self.assertEqual(line["label"], "root")
        self.assertEqual(line["path"], "root")

    def test_the_button_names_where_the_pin_is_in_full(self):
        # The one place that tells the reader a click is about to *move* the
        # pin has to say what it would be moved away from.
        state = self._run(
            "const api = require(process.argv[2]);"
            "process.stdout.write(JSON.stringify("
            "api.explorerGitPinButtonState('src/components/app.js', 'docs', 'file', 'dir')));",
            str(PIN_JS),
        )
        if state.returncode != 0:
            self.fail(f"node harness failed:\n{state.stderr}")
        self.assertIn(
            "pinned: src/components/app.js", json.loads(state.stdout)["title"]
        )

    def test_a_file_path_that_is_only_separators_still_gets_a_name(self):
        # `explorerGitPinLabel` has to be total: no leaf to take means the
        # whole-path spelling stands in, never an empty label.
        labels = self._run(
            "const api = require(process.argv[2]);"
            "process.stdout.write(JSON.stringify(["
            "api.explorerGitPinLabel('', 'file'),"
            "api.explorerGitPinLabel('///', 'file'),"
            "api.explorerGitPinPathLabel('')]));",
            str(PIN_JS),
        )
        if labels.returncode != 0:
            self.fail(f"node harness failed:\n{labels.stderr}")
        self.assertEqual(json.loads(labels.stdout), ["root", "///", "root"])

    @staticmethod
    def _run(script, *args):
        return _run(script, *args)

    def test_both_controls_give_both_rows_pin_first(self):
        lines = self._lines("open5gs/docs", "open5gs/lib", True)
        self.assertEqual([line["kind"] for line in lines], ["pin", "follow"])
        self.assertEqual(lines[0]["label"], "open5gs/docs")
        self.assertEqual(lines[1]["label"], "open5gs/lib")

    def test_the_pin_row_stays_while_follow_overrides_it(self):
        # Follow overrides a pin rather than replacing it -- turning Follow off
        # lands back on the pin -- so dropping the row would read as a lost pin.
        line = self._lines("open5gs/docs", "open5gs/lib", True)[0]
        self.assertTrue(line["overridden"])
        # And it says so in words, not by the styling alone.
        self.assertIn("overridden while Follow is on", line["title"])

    def test_clear_pin_belongs_only_to_the_row_that_names_the_pin(self):
        # The whole point of the split: a Follow row can never carry it.
        for following in (False, True):
            lines = self._lines("open5gs/docs", "open5gs/lib", following)
            by_kind = {line["kind"]: line for line in lines}
            self.assertTrue(by_kind["pin"]["clearAvailable"])
            if following:
                self.assertFalse(by_kind["follow"]["clearAvailable"])

    def test_the_pin_row_offers_its_clear_from_the_pinned_folder_too(self):
        # Clear pin answers "is there a pin", not "is the pin elsewhere", so
        # walking in and out of the pinned folder never moves the control the
        # reader reaches for -- the browsed path does not enter into it.
        for browsed in ("open5gs/docs", "open5gs/lib", "", None):
            line = self._lines("open5gs/docs", browsed, False)[0]
            self.assertTrue(line["clearAvailable"], browsed)

    def test_a_root_pin_and_a_root_follow_are_both_named_root(self):
        # '' and "no pin" ask the server for the same thing, so a root pin
        # without a word for it round-trips perfectly and still reads as lost.
        self.assertEqual(self._lines("", "web", False)[0]["label"], "root")
        self.assertEqual(self._lines(None, "", True)[0]["label"], "root")


# The Follow marker, end to end: the real row builder renders it, and the real
# paint is then run over the rows the render produced. Where Follow points is
# not restated here -- `explorer-git-sidebar.js` is loaded so the marker is
# placed by the same `explorerGitBrowsingScope()` the repo bar's Follow row is
# built from.
FOLLOW_HARNESS = SANDBOX + r"""
let nodeSeq = 0;

function attr(markup, name) {
    const at = markup.indexOf(name + '="');
    if (at === -1) { return ''; }
    const from = at + name.length + 2;
    return markup.slice(from, markup.indexOf('"', from));
}

function markClassOf(markup) {
    if (markup.includes('explorer-tree-follow-mark')) { return 'explorer-tree-follow-mark'; }
    if (markup.includes('explorer-tree-pin-mark')) { return 'explorer-tree-pin-mark'; }
    throw new Error('not a scope marker: ' + markup);
}

/* Every child a row can hold, each able to take a marker immediately before
   itself -- which is how the paint places one, and the only way the ordering
   between the two markers can be observed. */
function makeNode(className, children) {
    const node = {
        nodeId: ++nodeSeq,
        className,
        remove() {
            const at = children.indexOf(node);
            if (at !== -1) { children.splice(at, 1); }
        },
        insertAdjacentHTML(position, markup) {
            if (position !== 'beforebegin') { throw new Error('unexpected ' + position); }
            children.splice(children.indexOf(node), 0, makeNode(markClassOf(markup), children));
        }
    };
    return node;
}

const ROW_SLOTS = [
    'explorer-tree-pin-mark',
    'explorer-tree-follow-mark',
    'explorer-open-folder-btn',
    'explorer-open-tab-btn'
];

/* One row stub per rendered row, its children rebuilt in the order the markup
   put them in -- so "the render placed the pin ahead of Follow" and "the paint
   put a late arrival back in that order" are the same observation. */
function rowFromMarkup(markup) {
    const children = [];
    ROW_SLOTS
        .map(name => [name, markup.indexOf(name)])
        .filter(pair => pair[1] !== -1)
        .sort((a, b) => a[1] - b[1])
        .forEach(pair => { children.push(makeNode(pair[0], children)); });
    return {
        nodeId: ++nodeSeq,
        dataset: {
            explorerContextPath: attr(markup, 'data-explorer-context-path'),
            explorerContextKind: attr(markup, 'data-explorer-context-kind'),
            explorerGitScopePath: attr(markup, 'data-explorer-git-scope-path'),
            explorerGitScopeKind: attr(markup, 'data-explorer-git-scope-kind')
        },
        children,
        insertAdjacentHTML(position, added) {
            if (position !== 'beforeend') { throw new Error('unexpected ' + position); }
            children.push(makeNode(markClassOf(added), children));
        },
        querySelector(selector) {
            const wanted = selector.split(',').map(part => part.trim().replace(/^\./, ''));
            return children.find(child => wanted.includes(child.className)) || null;
        }
    };
}

const spec = JSON.parse(process.argv[5]);
const sandbox = makeSandbox();
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox);
// The real "where is Follow pointing" derivation, not a restatement of it.
vm.runInContext(fs.readFileSync(process.argv[4], 'utf8'), sandbox);

sandbox.__spec = spec;
vm.runInContext(`
    sessionIds[0] = 'sess-0';
    terminals[0] = {
        _explorerTreeExpanded: new Set(['web']),
        _explorerTreeChildren: new Map(Object.entries(__spec.children)),
        _explorerTreeErrors: new Map(),
        _explorerTreeLoading: new Set(),
        _explorerGitFollowBrowsing: __spec.following,
        _explorerMode: __spec.mode,
        _explorerPath: __spec.path,
        _explorerFilePath: __spec.filePath,
        _explorerGitBrowseTarget: __spec.browseTarget
    };
    if (__spec.pinned !== null) {
        terminals[0]._explorerGitPinnedPath = __spec.pinned;
        terminals[0]._explorerGitPinKind = __spec.pinnedKind;
    }
`, sandbox);

const html = sandbox.renderExplorerTreeNodes(sandbox.terminals[0], '', 0);
const rows = html
    .split('data-explorer-context-path="')
    .slice(1)
    .map(part => rowFromMarkup('data-explorer-context-path="' + part));

const wearing = cls => rows
    .filter(row => row.children.some(child => child.className === cls))
    .map(row => row.dataset.explorerContextPath);
const pinMarks = () => wearing('explorer-tree-pin-mark');
const followMarks = () => wearing('explorer-tree-follow-mark');
// In every row holding both, the pin comes first -- the order the row markup
// uses, which a marker arriving later by paint must not invert.
const orderHeld = () => rows.every(row => {
    const pinAt = row.children.findIndex(c => c.className === 'explorer-tree-pin-mark');
    const followAt = row.children.findIndex(c => c.className === 'explorer-tree-follow-mark');
    const openAt = row.children.findIndex(c => String(c.className).startsWith('explorer-open-'));
    if (pinAt !== -1 && followAt !== -1 && pinAt > followAt) { return false; }
    if (openAt === -1) { return true; }
    return (pinAt === -1 || pinAt < openAt) && (followAt === -1 || followAt < openAt);
});

const rendered = { pin: pinMarks(), follow: followMarks(), order: orderHeld() };

const rootPinMark = { nodeId: ++nodeSeq, hidden: true };
const rootFollowMark = { nodeId: ++nodeSeq, hidden: true };
const panel = {
    scrollTop: 412,
    querySelector: selector => {
        if (selector === '[data-explorer-tree-pin-root]') { return rootPinMark; }
        if (selector === '[data-explorer-tree-follow-root]') { return rootFollowMark; }
        return null;
    },
    querySelectorAll: selector => (selector === '.explorer-tree-row' ? rows : [])
};
sandbox.document.getElementById = id => (id === 'explorer-tree-panel-0' ? panel : null);

const beforeIds = rows.map(row => row.nodeId);
sandbox.applyExplorerTreeScopeMarks(0);
const painted = { pin: pinMarks(), follow: followMarks(), order: orderHeld() };

// A second pass is a no-op: the paint runs on every navigation.
sandbox.applyExplorerTreeScopeMarks(0);
const repainted = { pin: pinMarks(), follow: followMarks(), order: orderHeld() };

// Then move Follow somewhere else and paint again, without re-rendering: this
// is the move the marker exists to make visible.
let moved = null;
if (spec.moveTo !== undefined) {
    sandbox.terminals[0]._explorerPath = spec.moveTo;
    sandbox.terminals[0]._explorerMode = 'directory';
    sandbox.terminals[0]._explorerFilePath = '';
    sandbox.terminals[0]._explorerGitBrowseTarget = null;
    sandbox.applyExplorerTreeScopeMarks(0);
    moved = { pin: pinMarks(), follow: followMarks(), order: orderHeld() };
}

process.stdout.write(JSON.stringify({
    html,
    rendered,
    painted,
    repainted,
    moved,
    rowIdsUnchanged: JSON.stringify(beforeIds) === JSON.stringify(rows.map(row => row.nodeId)),
    rootPinHidden: rootPinMark.hidden,
    rootFollowHidden: rootFollowMark.hidden,
    scrollTop: panel.scrollTop
}));
"""

# Turning Follow on is a scope move, and the tree has to hear about it before
# the repository round trip it also starts.
FOLLOW_TOGGLE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const order = [];
const sandbox = {
    console,
    document: {
        getElementById: () => null,
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
        body: { dataset: {}, addEventListener() {} }
    },
    navigator: {},
    setTimeout,
    clearTimeout,
    requestAnimationFrame: () => 0,
    fetch: () => Promise.reject(new Error('a Follow toggle may issue no request of its own')),
    terminals: [],
    sessionIds: [],
    notePanePresentationChanged: () => { order.push('presentation'); },
    applyExplorerTreeScopeMarks: () => { order.push('tree'); },
    escHtml: value => String(value == null ? '' : value)
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox);

vm.runInContext(`
    sessionIds[0] = 'sess-0';
    terminals[0] = { _explorerPath: 'web/static' };
`, sandbox);
sandbox.invalidateExplorerGitRepo = () => { order.push('invalidate'); };
sandbox.loadExplorerGitRepo = async () => { order.push('load'); };

async function main() {
    await sandbox.toggleExplorerGitFollowBrowsing(0);
    process.stdout.write(JSON.stringify({
        order,
        following: Boolean(sandbox.terminals[0]._explorerGitFollowBrowsing)
    }));
}
main().catch(error => { console.error(error); process.exit(1); });
"""


@unittest.skipUnless(NODE, "Node.js is required for explorer tree scope mark tests")
class ExplorerTreeFollowMarkTestCase(unittest.TestCase):
    """Follow puts the chain on the row it is tracking, and only that row."""

    def _marks(self, **spec):
        payload = {
            "children": FIXTURE_CHILDREN,
            "following": True,
            "mode": "directory",
            "path": "",
            "filePath": "",
            "browseTarget": None,
            "pinned": None,
            "pinnedKind": "dir",
        }
        payload.update(spec)
        completed = _run(
            FOLLOW_HARNESS,
            str(PIN_JS),
            str(TREE_JS),
            str(SIDEBAR_JS),
            json.dumps(payload),
        )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_the_chain_lands_on_exactly_the_followed_folder(self):
        result = self._marks(path="web")
        self.assertEqual(result["rendered"]["follow"], ["web"])
        self.assertEqual(result["painted"]["follow"], ["web"])

    def test_follow_off_marks_nothing_anywhere(self):
        result = self._marks(following=False, path="web")
        self.assertEqual(result["rendered"]["follow"], [])
        self.assertEqual(result["painted"]["follow"], [])
        self.assertTrue(result["rootFollowHidden"])

    def test_following_an_open_file_marks_the_file_and_not_its_folder(self):
        # A file is a first-class Git scope, so Follow tracks the open file
        # rather than the folder the listing happens to be showing.
        result = self._marks(mode="file", filePath="readme.md", path="")
        self.assertEqual(result["rendered"]["follow"], ["readme.md"])
        self.assertEqual(result["painted"]["follow"], ["readme.md"])

    def test_a_row_singled_out_in_the_listing_beats_the_folder_it_lists(self):
        # The browse override is a pointer gesture over the derived scope, and
        # the marker reports the scope Git actually uses -- not the derivation.
        result = self._marks(
            path="",
            browseTarget={
                "path": "docs",
                "kind": "dir",
                "basePath": "",
                "baseKind": "dir",
            },
        )
        self.assertEqual(result["painted"]["follow"], ["docs"])

    def test_following_the_explorer_root_marks_the_head_and_no_row(self):
        # The body lists the root's *children*, so the root has no row of its
        # own; the FILES head stands in for it, by attribute.
        result = self._marks(path="")
        self.assertEqual(result["rendered"]["follow"], [])
        self.assertEqual(result["painted"]["follow"], [])
        self.assertFalse(result["rootFollowHidden"])

    def test_following_a_folder_that_is_not_rendered_marks_nothing(self):
        # A collapsed ancestor, or a path outside the tree entirely: the tree
        # reports the scope, it never expands itself to reach it.
        self.assertEqual(self._marks(path="web/static/js")["painted"]["follow"], [])
        self.assertEqual(self._marks(path="elsewhere")["painted"]["follow"], [])

    def test_an_ancestor_of_the_followed_folder_is_not_marked(self):
        result = self._marks(path="web/static")
        self.assertEqual(result["painted"]["follow"], ["web/static"])

    def test_the_marker_is_a_span_carrying_the_documented_hooks(self):
        html = self._marks(path="web")["html"]
        self.assertIn('class="explorer-tree-follow-mark"', html)
        self.assertIn('title="Git scope follows this row"', html)
        self.assertIn('aria-label="Git scope follows this row"', html)
        marker_at = html.index("explorer-tree-follow-mark")
        self.assertEqual(html.rfind("<span", 0, marker_at), html.rfind("<", 0, marker_at))


@unittest.skipUnless(NODE, "Node.js is required for explorer tree scope mark tests")
class ExplorerTreeScopeMarksTogetherTestCase(unittest.TestCase):
    """A pin and a live Follow are two scopes, and the tree says so."""

    def _marks(self, **spec):
        payload = {
            "children": FIXTURE_CHILDREN,
            "following": True,
            "mode": "directory",
            "path": "",
            "filePath": "",
            "browseTarget": None,
            "pinned": None,
            "pinnedKind": "dir",
        }
        payload.update(spec)
        completed = _run(
            FOLLOW_HARNESS,
            str(PIN_JS),
            str(TREE_JS),
            str(SIDEBAR_JS),
            json.dumps(payload),
        )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_a_pin_and_a_follow_on_different_rows_are_two_marks(self):
        result = self._marks(pinned="docs", path="web")
        self.assertEqual(result["painted"]["pin"], ["docs"])
        self.assertEqual(result["painted"]["follow"], ["web"])

    def test_a_pin_the_follow_is_standing_on_keeps_its_own_mark(self):
        # Follow overrides the pin while it is on; it does not replace it, and
        # a row that dropped the pin mark would read as a pin that was lost.
        result = self._marks(pinned="web", path="web")
        self.assertEqual(result["painted"]["pin"], ["web"])
        self.assertEqual(result["painted"]["follow"], ["web"])

    def test_the_pin_stays_ahead_of_the_follow_and_both_precede_the_open_control(self):
        for spec in ({"pinned": "web", "path": "web"}, {"pinned": "docs", "path": "web"}):
            result = self._marks(**spec)
            self.assertTrue(result["rendered"]["order"], spec)
            self.assertTrue(result["painted"]["order"], spec)

    def test_a_follow_arriving_by_paint_lands_where_the_render_would_put_it(self):
        # The pin is rendered into the row and Follow is painted in afterwards
        # by the move below, which is the ordering the paint has to reproduce.
        result = self._marks(pinned="web", path="docs", moveTo="web")
        self.assertEqual(result["painted"]["follow"], ["docs"])
        self.assertEqual(result["moved"]["follow"], ["web"])
        self.assertEqual(result["moved"]["pin"], ["web"])
        self.assertTrue(result["moved"]["order"])

    def test_the_marks_move_without_rebuilding_the_rows_or_touching_the_scroll(self):
        result = self._marks(pinned="docs", path="docs", moveTo="web")
        self.assertEqual(result["moved"]["follow"], ["web"])
        self.assertTrue(result["rowIdsUnchanged"])
        self.assertEqual(result["scrollTop"], 412)

    def test_painting_what_is_already_shown_changes_nothing(self):
        result = self._marks(pinned="docs", path="web")
        self.assertEqual(result["rendered"], result["painted"])
        self.assertEqual(result["painted"], result["repainted"])


@unittest.skipUnless(NODE, "Node.js is required for explorer tree scope mark tests")
class ExplorerGitFollowToggleTestCase(unittest.TestCase):
    """Turning Follow on moves the tree's marker before the repository loads."""

    def _toggle(self):
        completed = _run(FOLLOW_TOGGLE_HARNESS, str(PIN_JS), str(SIDEBAR_JS))
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_the_tree_is_painted_before_the_repository_round_trip(self):
        # The marker reports a pane field that has already moved, so waiting
        # out the load to show it would be reporting the request, not the
        # state -- the same rule the pin write already follows.
        result = self._toggle()
        self.assertTrue(result["following"])
        self.assertIn("tree", result["order"])
        self.assertIn("load", result["order"])
        self.assertLess(result["order"].index("tree"), result["order"].index("load"))


if __name__ == "__main__":
    unittest.main()
