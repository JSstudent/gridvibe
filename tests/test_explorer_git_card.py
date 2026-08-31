"""The commit card, and the gesture that opens it.

One commit row used to answer to two gestures. Hovering it produced a card,
after a delay, whether or not the reader wanted one -- and right-clicking it
produced a small menu with the two copy entries, which is what a reader
actually goes to a commit row for. The card had the facts and no controls; the
menu had the controls and no facts.

They are one surface now: the right-click opens the card, and the two copy
entries are buttons inside it, beside the message and beside the object id.
Hovering does nothing.

What that costs, and what these tests hold:

* The row renders **no** card. A scope may carry three hundred commits, and
  the old markup emitted a full card for every one of them on every repaint to
  show at most one; the card is built when it is asked for.
* The card is built on ``document.body``, not in the row and not in the Git
  panel. It holds buttons, so it cannot be inside the row button; and the panel
  is a scroller, so anything laid out inside it is capped at the sidebar's
  width -- which wrapped the full forty-character object id, the one field a
  reader opens this card to copy, onto two lines. It is sized by its own
  content instead, floored at the width of the row it hangs off.
* Its copy controls put exactly the strings ``explorer-git-menu.js`` says on
  the clipboard -- that module is still the only answer to what a commit copies
  -- and a value the log did not carry leaves its control **disabled, not
  dropped**, the rule the menu entry lived by.
* Living outside the panel makes the dismissals load-bearing rather than
  incidental: a card in the panel died with its innerHTML and moved with its
  scroll, and this one does neither. Escape, an outside press, a copy, a
  re-render, a scroll of the list under it, and the pane being released each
  have to say so -- and each has to take all three document-level listeners
  with it, or a pane the reader has left keeps answering keystrokes.

Neither ``renderExplorerGitPanel()`` nor ``openExplorerGitCommitCard()`` is
DOM-free, so both run in a Node ``vm`` against a small real DOM -- one that
parses the markup it is handed, so the panel under test is the panel the page
would build and the row that is right-clicked is a row that render produced.
The gesture goes in through the panel's own delegated ``contextmenu``
listener, so what is exercised is the dispatch as well as the card.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = ROOT / "web" / "static" / "js"

# The page's own load order, so the classic scripts see each other exactly as
# they do in the browser. The sidebar reaches into explorer-viewer.js for the
# escaper, half its icons and the delegated context-menu wiring, so the real
# modules are loaded rather than stubbed: a stubbed renderer would only prove
# the stub was called.
MODULES = [
    STATIC_JS / name
    for name in (
        "explorer-persistence.js",
        "explorer-scroll.js",
        "explorer-theme-store.js",
        "explorer-selection.js",
        "explorer-tiers.js",
        "explorer-repaint.js",
        "explorer-git-menu.js",
        "explorer-git-active.js",
        "explorer-git-search.js",
        "explorer-git-pin.js",
        "explorer-git-graph.js",
        "terminal-icons.js",
        "explorer-viewer.js",
        "explorer-tree.js",
        "explorer-git-sidebar.js",
        "explorer-scroll-adapter.js",
        "explorer-diff.js",
        "explorer-tabs.js",
        "explorer-edit-highlight.js",
        "explorer-edit-overlay.js",
        "explorer-edit-find.js",
        "explorer-editor.js",
        "explorer-search.js",
        "explorer-tree-search.js",
        "explorer-overview.js",
    )
]

NODE = shutil.which("node")

FULL_HASH = "d9c2d8abcc9bd7f62f33be5b03c413c70cb3047a"
SHORT_HASH = "d9c2d8a"
MESSAGE = "(fix) The Diff view no longer flickers"

COMMIT = {
    "hash": SHORT_HASH,
    "full_hash": FULL_HASH,
    "subject": "(HEAD -> main) " + MESSAGE,
    "message": MESSAGE,
    "refs": "HEAD -> main",
    "author": "SasoZup",
    "authored_at": "2026-08-31T08:45:00+02:00",
    "graph": "*",
    "files": [],
}

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const modules = JSON.parse(process.argv[2]);
const spec = JSON.parse(process.argv[3]);

/* ── A small real DOM ──────────────────────────────────────────────────────
   Small, but not a set of no-ops: the panel's markup has to be parsed back
   into nodes, because the row that is right-clicked has to be a row the
   render produced and the card has to be found where the page would find it.
   Only what these modules actually use is implemented. */

/* HTML's void elements only. The stroke icons' <path>, <line>, <rect> and
   <polyline> all carry explicit closing tags, and treating them as void made
   every </path> pop somebody else's element off the stack. */
const VOID_TAGS = new Set([
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
    'link', 'meta', 'param', 'source', 'track', 'wbr'
]);

function decodeEntities(value) {
    return String(value)
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&amp;/g, '&');
}

function camel(name) {
    return name.replace(/-([a-z0-9])/g, (_, chr) => chr.toUpperCase());
}

function makeElement(tag) {
    const node = {
        tagName: String(tag).toUpperCase(),
        attributes: Object.create(null),
        dataset: Object.create(null),
        children: [],
        parentNode: null,
        listeners: Object.create(null),
        ownText: '',
        disabled: false,
        hidden: false,
        value: '',
        id: '',
        scrollTop: 0,
        scrollLeft: 0,
        scrollHeight: 0,
        scrollWidth: 0,
        clientHeight: 0,
        clientWidth: 0,
        offsetHeight: 90,
        offsetWidth: 240,
        isRoot: false,
        // Recorded rather than ignored: the card's position and its width
        // floor are written here and nowhere else, so this is where a test
        // can see them.
        style: { setProperty() {}, removeProperty() {}, getPropertyValue: () => '' }
    };
    const classes = new Set();
    node.classes = classes;
    node.classList = {
        add: (...names) => names.forEach(name => classes.add(name)),
        remove: (...names) => names.forEach(name => classes.delete(name)),
        contains: name => classes.has(name),
        toggle(name, force) {
            const on = force === undefined ? !classes.has(name) : Boolean(force);
            if (on) classes.add(name); else classes.delete(name);
            return on;
        }
    };
    node.setAttribute = (name, value) => {
        node.attributes[name] = String(value);
        if (name === 'class') {
            classes.clear();
            String(value).split(/\s+/).filter(Boolean).forEach(one => classes.add(one));
        } else if (name === 'id') {
            node.id = String(value);
        } else if (name.startsWith('data-')) {
            node.dataset[camel(name.slice(5))] = String(value);
        } else if (name === 'disabled') {
            node.disabled = true;
        } else if (name === 'hidden') {
            node.hidden = true;
        }
    };
    node.getAttribute = name => (name in node.attributes ? node.attributes[name] : null);
    node.hasAttribute = name => name in node.attributes;
    node.removeAttribute = name => { delete node.attributes[name]; };
    node.appendChild = child => {
        child.parentNode = node;
        node.children.push(child);
        return child;
    };
    node.removeChild = child => {
        const at = node.children.indexOf(child);
        if (at >= 0) node.children.splice(at, 1);
        child.parentNode = null;
    };
    node.remove = () => { node.parentNode?.removeChild(node); };
    node.replaceChildren = () => { node.children.forEach(c => { c.parentNode = null; }); node.children = []; };
    node.insertAdjacentHTML = (position, html) => {
        if (position !== 'beforeend') throw new Error('unsupported position ' + position);
        parseInto(node, String(html));
    };
    node.addEventListener = (type, fn) => {
        (node.listeners[type] = node.listeners[type] || []).push(fn);
    };
    node.removeEventListener = (type, fn) => {
        const list = node.listeners[type] || [];
        const at = list.indexOf(fn);
        if (at >= 0) list.splice(at, 1);
    };
    node.focus = () => { sandbox.document.activeElement = node; };
    node.blur = () => { if (sandbox.document.activeElement === node) sandbox.document.activeElement = null; };
    node.scrollTo = () => {};
    /* Geometry the placement maths reads. `rectTop`/`rectHeight`/`rectWidth`
       are set by the harness on the two elements that decide where the card
       goes -- the row it hangs off and the panel it must stay inside -- so a
       test can put a row at the bottom of a tall panel and see the flip. */
    node.getBoundingClientRect = () => {
        const top = node.rectTop || 0;
        const height = node.rectHeight || 20;
        const width = node.rectWidth || 240;
        return { top, bottom: top + height, height, left: node.rectLeft || 0,
                 right: (node.rectLeft || 0) + width, width };
    };
    node.cloneNode = () => makeElement(tag);
    node.contains = other => {
        let walk = other;
        while (walk) { if (walk === node) return true; walk = walk.parentNode; }
        return false;
    };
    node.closest = selector => {
        let walk = node;
        while (walk) { if (matchesSelector(walk, selector)) return walk; walk = walk.parentNode; }
        return null;
    };
    node.matches = selector => matchesSelector(node, selector);
    node.querySelector = selector => queryAll(node, selector)[0] || null;
    node.querySelectorAll = selector => queryAll(node, selector);
    Object.defineProperty(node, 'textContent', {
        get() {
            return node.children.reduce(
                (text, child) => text + child.textContent, node.ownText
            );
        },
        set(value) {
            node.replaceChildren();
            node.ownText = String(value);
        }
    });
    Object.defineProperty(node, 'innerHTML', {
        get() { return node.rawHtml || ''; },
        set(value) {
            node.rawHtml = String(value);
            node.replaceChildren();
            node.ownText = '';
            parseInto(node, String(value));
        }
    });
    Object.defineProperty(node, 'isConnected', {
        get() {
            let walk = node;
            while (walk) { if (walk.isRoot) return true; walk = walk.parentNode; }
            return false;
        }
    });
    Object.defineProperty(node, 'firstChild', { get: () => node.children[0] || null });
    return node;
}

/* One compound selector -- tag, #id, .class, [attr], [attr="v"], :not(...),
   :disabled -- against one node. */
function matchesCompound(node, compound) {
    let rest = compound;
    if (!rest || rest === '*') return true;
    const tag = /^[a-zA-Z][\w-]*/.exec(rest);
    if (tag) {
        if (node.tagName !== tag[0].toUpperCase()) return false;
        rest = rest.slice(tag[0].length);
    }
    const parts = /#([\w:-]+)|\.([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]|:not\(([^)]*)\)|:disabled|:enabled/g;
    let part;
    while ((part = parts.exec(rest))) {
        if (part[1] !== undefined) {
            if (node.id !== part[1]) return false;
        } else if (part[2] !== undefined) {
            if (!node.classList.contains(part[2])) return false;
        } else if (part[3] !== undefined) {
            const has = part[3] in node.attributes;
            if (!has) return false;
            if (part[4] !== undefined && node.attributes[part[3]] !== part[4]) return false;
        } else if (part[5] !== undefined) {
            if (matchesCompound(node, part[5])) return false;
        } else if (part[0] === ':disabled') {
            if (!node.disabled) return false;
        } else if (part[0] === ':enabled') {
            if (node.disabled) return false;
        }
    }
    return true;
}

function matchesSelector(node, selector) {
    return String(selector).split(',').some(one => {
        const segments = one.trim().split(/\s+/).filter(Boolean);
        if (!segments.length) return false;
        if (!matchesCompound(node, segments[segments.length - 1])) return false;
        let walk = node.parentNode;
        for (let at = segments.length - 2; at >= 0; at -= 1) {
            let found = false;
            while (walk) {
                const ancestor = walk;
                walk = walk.parentNode;
                if (matchesCompound(ancestor, segments[at])) { found = true; break; }
            }
            if (!found) return false;
        }
        return true;
    });
}

function descendants(node, into) {
    node.children.forEach(child => { into.push(child); descendants(child, into); });
    return into;
}

function queryAll(node, selector) {
    const scoped = /^:scope\s*>\s*/.exec(String(selector));
    if (scoped) {
        const rest = String(selector).slice(scoped[0].length);
        return node.children.filter(child => matchesCompound(child, rest));
    }
    return descendants(node, []).filter(child => matchesSelector(child, selector));
}

const TAG_PATTERN = /<(\/?)([a-zA-Z][\w-]*)((?:\s+[^\s=>/]+(?:="[^"]*")?)*)\s*(\/?)>/g;
const ATTR_PATTERN = /([^\s=]+)(?:="([^"]*)")?/g;

function parseInto(root, html) {
    const stack = [root];
    TAG_PATTERN.lastIndex = 0;
    let at = 0;
    let found;
    while ((found = TAG_PATTERN.exec(html))) {
        const text = html.slice(at, found.index);
        at = TAG_PATTERN.lastIndex;
        if (text.trim()) stack[stack.length - 1].ownText += decodeEntities(text);
        const [, closing, tag, attrs, selfClosing] = found;
        if (closing) {
            if (stack.length > 1) stack.pop();
            continue;
        }
        const element = makeElement(tag);
        ATTR_PATTERN.lastIndex = 0;
        let attr;
        while ((attr = ATTR_PATTERN.exec(attrs))) {
            if (!attr[1]) continue;
            element.setAttribute(attr[1], decodeEntities(attr[2] === undefined ? '' : attr[2]));
        }
        stack[stack.length - 1].appendChild(element);
        if (!selfClosing && !VOID_TAGS.has(tag.toLowerCase())) stack.push(element);
    }
    const tail = html.slice(at);
    if (tail.trim()) stack[stack.length - 1].ownText += decodeEntities(tail);
}

/* ── The page ─────────────────────────────────────────────────────────── */

const roots = new Map();
const documentListeners = Object.create(null);
const copied = [];

const documentBody = makeElement('body');
documentBody.isRoot = true;
roots.set('#body', documentBody);

const sandbox = {
    console,
    URL,
    URLSearchParams,
    encodeURIComponent,
    AbortController,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    requestAnimationFrame: () => 0,
    cancelAnimationFrame() {},
    performance: { now: () => 0 },
    CSS: { highlights: { set() {}, delete() {} } },
    terminals: [],
    sessionIds: [],
    navigator: {},
    addEventListener() {},
    removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    // The card is clamped into the window, so the window has to have a width.
    innerWidth: 1280,
    innerHeight: 800,
    /* shared.js's own escaper, which this harness does not load. Copied rather
       than neutered: the card's values reach the parser above through it, so a
       stub that did not escape would let a subject containing markup rewrite
       the page the assertions read. */
    escHtml: value => String(value === null || value === undefined ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;'),
    document: {
        activeElement: null,
        getElementById(id) {
            /* The tree first: an element the page built into the body (the
               commit card) is found where it is, not invented as an empty
               root beside it. */
            const built = this.querySelectorAll('#' + id)[0];
            if (built) {
                return built;
            }
            if (!roots.has(id)) {
                const element = makeElement('div');
                element.setAttribute('id', id);
                element.isRoot = true;
                roots.set(id, element);
            }
            return roots.get(id);
        },
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
        querySelectorAll(selector) {
            const out = [];
            roots.forEach(root => {
                if (matchesSelector(root, selector)) out.push(root);
                descendants(root, []).forEach(node => {
                    if (matchesSelector(node, selector)) out.push(node);
                });
            });
            return out;
        },
        createElement: tag => makeElement(tag),
        createDocumentFragment: () => makeElement('div'),
        addEventListener(type, fn) {
            (documentListeners[type] = documentListeners[type] || []).push(fn);
        },
        removeEventListener(type, fn) {
            const list = documentListeners[type] || [];
            const at = list.indexOf(fn);
            if (at >= 0) list.splice(at, 1);
        },
        // A real element: the card is built into it.
        body: documentBody
    },
    fetch: async () => ({ ok: true, json: async () => ({}) })
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
modules.forEach(path => vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox));

/* Everything above is the real module; what is left belongs to terminals.js,
   which this harness cannot load. `_copyText` is the one that matters -- it is
   what the card's controls are handed -- so it records instead of writing to a
   clipboard nothing here has. A name still missing after the render surfaces
   as a ReferenceError rather than being swallowed. */
sandbox._copyText = value => { copied.push(value); };
[
    'notePanePresentationChanged', 'applyExplorerTheme', 'explorerPaneLiveTheme',
    'isExplorerSession', 'showGridVibeNotice', 'openGenericConfirmModal',
    'markActiveExplorerPane'
].forEach(name => {
    if (typeof sandbox[name] === 'undefined') sandbox[name] = () => undefined;
});

const pane = {
    _session: { startup_mode: 'explorer', explorer_root_directory: '/repo' },
    _attached: true,
    _explorerMode: 'directory',
    _explorerPath: '',
    _explorerTabs: [],
    _explorerGitSidebarOpen: true,
    _explorerGitRepoLoaded: true,
    _explorerGitRepo: {
        anchor_path: '',
        revision: 'rev-1',
        git: { available: true, repo_name: 'repo', repo_path: '', branch: 'main' },
        changes: [],
        commits: spec.commits,
        commit_limit: 60,
        commit_page: 60,
        commit_limit_max: 300,
        commit_has_more: false
    }
};
sandbox.terminals[0] = pane;
sandbox.sessionIds[0] = 'sess-0';

const panel = sandbox.document.getElementById('explorer-git-panel-0');
panel.classList.add('explorer-git-panel');
panel.rectTop = 0;
panel.rectHeight = 400;
/* The pane card the panel sits in, so the commit card can read the pane's own
   explorer theme off it the way it does on the page. The panel stays
   reachable by id; it is simply no longer the top of its own tree. */
const paneCard = makeElement('div');
paneCard.classList.add('explorer-pane');
paneCard.setAttribute('data-explorer-theme', spec.paneTheme || 'dark');
paneCard.isRoot = true;
paneCard.appendChild(panel);
panel.isRoot = false;
roots.set('#pane', paneCard);

function fire(node, type, event) {
    (node.listeners[type] || []).slice().forEach(fn => fn(event));
}

function fireDocument(type, event) {
    (documentListeners[type] || []).slice().forEach(fn => fn(event));
}

function commitRow(hash) {
    return panel.querySelectorAll('[data-explorer-git-commit-toggle]')
        .find(row => row.dataset.explorerGitCommitToggle === hash) || null;
}

function textOf(node) {
    return node ? node.textContent.replace(/\s+/g, ' ').trim() : null;
}

function describeCopy(button) {
    return {
        key: button.dataset.explorerGitCommitCopy,
        disabled: Boolean(button.disabled),
        title: button.getAttribute('title') || '',
        label: button.getAttribute('aria-label') || '',
        icons: button.querySelectorAll('svg').length
    };
}

function snapshot(label) {
    const card = documentBody.querySelector('.explorer-git-commit-card');
    const active = sandbox.document.activeElement;
    return {
        step: label,
        // On the body, and nowhere near the panel: both counted, because
        // "one card, and not inside the scroller" is the whole arrangement.
        cards: documentBody.querySelectorAll('.explorer-git-commit-card').length,
        cardsInPanel: panel.querySelectorAll('.explorer-git-commit-card').length,
        rows: panel.querySelectorAll('[data-explorer-git-commit-toggle]').length,
        markedRows: panel.querySelectorAll('.explorer-diff-commit.is-card-open').length,
        cardOpen: card ? card.classList.contains('is-open') : false,
        cardRole: card ? card.getAttribute('role') : null,
        cardAriaHidden: card ? card.getAttribute('aria-hidden') : null,
        cardTheme: card ? card.dataset.explorerTheme || null : null,
        cardInsideButton: card ? Boolean(card.closest('button')) : null,
        cardOnBody: card ? card.parentNode === documentBody : null,
        cardStyle: card
            ? { top: card.style.top, left: card.style.left, minWidth: card.style.minWidth }
            : null,
        message: card ? textOf(card.querySelector('.explorer-git-commit-card-message')) : null,
        hint: card ? textOf(card.querySelector('.explorer-git-commit-card-hint')) : null,
        fields: card
            ? card.querySelectorAll('.explorer-git-commit-card-row').map(row => ({
                label: textOf(row.querySelector('.explorer-git-commit-card-label')),
                value: textOf(row.querySelector('.explorer-git-commit-card-value')),
                copy: row.querySelector('[data-explorer-git-commit-copy]')
                    ?.dataset.explorerGitCommitCopy || null,
                slots: row.querySelectorAll('.explorer-git-commit-card-copy-slot').length
            }))
            : [],
        headCopy: card
            ? card.querySelector('.explorer-git-commit-card-head [data-explorer-git-commit-copy]')
                ?.dataset.explorerGitCommitCopy || null
            : null,
        copyButtons: card
            ? card.querySelectorAll('[data-explorer-git-commit-copy]').map(describeCopy)
            : [],
        rowLabels: panel.querySelectorAll('[data-explorer-git-commit-toggle]')
            .map(row => row.getAttribute('aria-label') || ''),
        focused: active
            ? (active.dataset.explorerGitCommitCopy
                || active.dataset.explorerGitCommitToggle
                || active.tagName)
            : null,
        copied: copied.slice(),
        /* Against the page's own baseline: explorer-viewer.js installs a
           document keydown listener of its own at load (the selection's
           Escape), so what is observable here is what the card added. */
        docListeners: {
            mousedown: (documentListeners.mousedown || []).length - baseline.mousedown,
            keydown: (documentListeners.keydown || []).length - baseline.keydown,
            scroll: (documentListeners.scroll || []).length - baseline.scroll
        }
    };
}

const baseline = {
    mousedown: (documentListeners.mousedown || []).length,
    keydown: (documentListeners.keydown || []).length,
    scroll: (documentListeners.scroll || []).length
};

const steps = [];
const outside = makeElement('div');
outside.isRoot = true;
roots.set('outside', outside);

/* Each step settles before the next one runs and before it is looked at. The
   outside-press listener is armed on a timer, the way the context menu's is,
   so the gesture that opens the card cannot close what it just opened. */
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

async function run() {
for (const action of spec.actions) {
    if (action.do === 'render') {
        sandbox.renderExplorerGitPanel(0);
    } else if (action.do === 'hover') {
        const row = commitRow(action.commit);
        fire(panel, 'pointerover', { target: row });
        fire(panel, 'focusin', { target: row });
    } else if (action.do === 'contextmenu') {
        const row = commitRow(action.commit);
        if (row && action.rowTop !== undefined) row.rectTop = action.rowTop;
        if (row && action.rowWidth !== undefined) row.rectWidth = action.rowWidth;
        // Through the panel's own delegated listener, so the dispatch is
        // exercised too -- including the branch that decides a commit row is
        // not a filesystem entry.
        fire(panel, 'contextmenu', {
            target: row,
            clientX: 10,
            clientY: 10,
            preventDefault() {}
        });
    } else if (action.do === 'copy') {
        const button = documentBody.querySelectorAll('[data-explorer-git-commit-copy]')
            .find(one => one.dataset.explorerGitCommitCopy === action.key);
        if (button) fire(button, 'click', { preventDefault() {} });
    } else if (action.do === 'escape') {
        fireDocument('keydown', { key: 'Escape', preventDefault() {} });
    } else if (action.do === 'outside') {
        fireDocument('mousedown', { target: outside });
    } else if (action.do === 'scroll') {
        fireDocument('scroll', { target: panel });
    } else if (action.do === 'scroll-elsewhere') {
        fireDocument('scroll', { target: outside });
    } else if (action.do === 'inside') {
        fireDocument('mousedown', {
            target: documentBody.querySelector('[data-explorer-git-commit-copy]')
        });
    } else if (action.do === 'release') {
        sandbox.explorerReleasePaneWork(pane);
    }
    await tick();
    steps.push(snapshot(action.do + (action.key ? ':' + action.key : '')));
}
}

run().then(() => {
    process.stdout.write(JSON.stringify(steps));
}).catch(error => {
    console.error(error);
    process.exit(1);
});
"""


@unittest.skipUnless(NODE, "Node.js is required for the commit card tests")
class ExplorerGitCommitCardHarness(unittest.TestCase):
    def _run(self, actions, commits=None, pane_theme="dark"):
        spec = {
            "commits": commits if commits is not None else [COMMIT],
            "actions": actions,
            "paneTheme": pane_theme,
        }
        with TemporaryDirectory() as script_dir:
            script = Path(script_dir) / "card.js"
            script.write_text(HARNESS, encoding="utf-8")
            completed = subprocess.run(
                [
                    NODE,
                    str(script),
                    json.dumps([str(path) for path in MODULES]),
                    json.dumps(spec),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
                check=False,
            )
        if completed.returncode != 0:
            self.fail("node harness failed:\n" + completed.stderr)
        return json.loads(completed.stdout.strip().splitlines()[-1])


class ExplorerGitCommitCardGestureTestCase(ExplorerGitCommitCardHarness):
    """Which gesture produces a card, and which produces nothing."""

    def test_the_render_builds_no_card_at_all(self):
        # A scope may carry three hundred commits; a card per row was three
        # hundred cards rebuilt on every repaint to show at most one. And the
        # row markup gained nothing in exchange -- the card is built
        # elsewhere, so the row is exactly the row it always was.
        rendered = self._run([{"do": "render"}])[0]

        self.assertEqual(rendered["rows"], 1)
        self.assertEqual(rendered["cards"], 0)

    def test_hovering_a_row_produces_nothing(self):
        steps = self._run([
            {"do": "render"},
            {"do": "hover", "commit": SHORT_HASH},
        ])

        self.assertEqual(steps[1]["cards"], 0)
        self.assertEqual(steps[1]["markedRows"], 0)

    def test_the_right_click_opens_the_card_on_the_row_it_was_made_on(self):
        steps = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
        ])
        opened = steps[1]

        self.assertEqual(opened["cards"], 1)
        self.assertTrue(opened["cardOpen"])
        self.assertEqual(opened["markedRows"], 1)

    def test_the_card_is_built_on_the_body_and_not_inside_the_panel(self):
        # It carries buttons, so it cannot be inside the row button. And the
        # panel is a scroller, so it cannot be inside the panel either without
        # being capped at the sidebar's width.
        opened = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
        ])[1]

        self.assertFalse(opened["cardInsideButton"])
        self.assertTrue(opened["cardOnBody"])
        self.assertEqual(opened["cardsInPanel"], 0)
        # And so it is no longer hidden from assistive technology, which it had
        # to be while every word in it was part of the button's own name.
        self.assertIsNone(opened["cardAriaHidden"])
        self.assertEqual(opened["cardRole"], "group")

    def test_the_row_still_says_everything_the_card_does_without_one(self):
        # The card is only on screen while the reader keeps it there, so the
        # row's accessible name still has to carry the facts.
        rendered = self._run([{"do": "render"}])[0]

        label = rendered["rowLabels"][0]
        self.assertIn(MESSAGE, label)
        self.assertIn("SasoZup", label)
        self.assertIn("31 Aug 2026", label)

    def test_the_card_reports_the_fields_the_row_has_no_room_for(self):
        opened = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
        ])[1]

        self.assertEqual(opened["message"], MESSAGE)
        self.assertEqual(
            [field["label"] for field in opened["fields"]],
            ["Author", "Date", "Commit", "Refs"],
        )
        by_label = {field["label"]: field["value"] for field in opened["fields"]}
        self.assertEqual(by_label["Author"], "SasoZup")
        self.assertEqual(by_label["Commit"], FULL_HASH)
        self.assertEqual(by_label["Refs"], "HEAD -> main")
        # The author's own wall clock, never the reader's: converting it would
        # move the commit across a date boundary for anyone in another zone.
        self.assertTrue(by_label["Date"].startswith("31 Aug 2026, 08:45"))
        self.assertEqual(opened["hint"], "Click to list this commit’s files")


class ExplorerGitCommitCardPlacementTestCase(ExplorerGitCommitCardHarness):
    """How wide it is, and where it lands.

    Both are written as inline styles by ``openExplorerGitCommitCard()``,
    because a card on ``document.body`` has nothing else to take them from.
    """

    def _opened(self, **action):
        step = {"do": "contextmenu", "commit": SHORT_HASH}
        step.update(action)
        return self._run([{"do": "render"}, step])[1]

    def test_the_card_is_never_narrower_than_the_row_it_hangs_off(self):
        # Its width floor, so a commit with little to report still reads as a
        # card belonging to that row rather than a small box beside it. The
        # ceiling and everything between the two are CSS's.
        opened = self._opened(rowWidth=312)

        self.assertEqual(opened["cardStyle"]["minWidth"], "312px")

    def test_the_floor_is_the_row_and_not_the_sidebar(self):
        # The point of the whole arrangement: `min-width` is a floor, so the
        # content -- the full forty-character object id above all -- may take
        # the card wider than the panel it was opened from. Nothing here caps
        # it at the panel's width.
        narrow = self._opened(rowWidth=200)
        wide = self._opened(rowWidth=420)

        self.assertEqual(narrow["cardStyle"]["minWidth"], "200px")
        self.assertEqual(wide["cardStyle"]["minWidth"], "420px")

    def test_a_row_with_room_under_it_opens_downward(self):
        # Two pixels of overlap with the row, the way it hung before.
        opened = self._opened(rowTop=100)

        self.assertEqual(opened["cardStyle"]["top"], "118px")

    def test_a_row_near_the_panels_bottom_flips_above_instead(self):
        # The panel is 400 tall in the harness; a row at 390 has no room under
        # it. The card is 20 tall, so above means 390 - 20 + 2.
        opened = self._opened(rowTop=390)

        self.assertEqual(opened["cardStyle"]["top"], "372px")

    def test_the_card_is_pulled_back_inside_the_window(self):
        # A wide card opened from a row near the right edge would otherwise
        # hang off it: this is the one clamp in the horizontal position, and
        # there is no decision anywhere else in it.
        inside = self._opened(rowTop=100)
        self.assertEqual(inside["cardStyle"]["left"], "8px")

    def test_the_card_wears_the_panes_own_explorer_theme(self):
        # It is outside `.explorer-pane`, so a pane toggled opposite the app
        # would open a card in the other theme unless it says which pane it
        # came from.
        for theme in ("dark", "light"):
            with self.subTest(theme=theme):
                opened = self._run(
                    [{"do": "render"}, {"do": "contextmenu", "commit": SHORT_HASH}],
                    pane_theme=theme,
                )[1]
                self.assertEqual(opened["cardTheme"], theme)


class ExplorerGitCommitCardCopyTestCase(ExplorerGitCommitCardHarness):
    """The two entries the context menu used to hold, now controls in the card."""

    def test_the_card_carries_exactly_the_two_copy_controls(self):
        opened = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
        ])[1]

        self.assertEqual(
            [button["key"] for button in opened["copyButtons"]],
            ["message", "hash"],
        )
        # Beside the message, and beside the object id -- not anywhere else.
        self.assertEqual(opened["headCopy"], "message")
        self.assertEqual(
            [(field["label"], field["copy"]) for field in opened["fields"]],
            [("Author", None), ("Date", None), ("Commit", "hash"), ("Refs", None)],
        )

    def test_every_field_reserves_the_copy_column_whether_or_not_it_uses_it(self):
        # The rows are `display: contents` over a three-column grid: a row that
        # skipped its slot would push every row below it a column across.
        opened = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
        ])[1]

        self.assertEqual([field["slots"] for field in opened["fields"]], [1, 1, 1, 1])

    def test_the_controls_are_icons_and_name_themselves(self):
        opened = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
        ])[1]

        by_key = {button["key"]: button for button in opened["copyButtons"]}
        self.assertEqual(by_key["hash"]["label"], "Copy commit hash")
        self.assertEqual(by_key["message"]["label"], "Copy commit message")
        self.assertEqual(by_key["hash"]["title"], "Copy " + FULL_HASH)
        # Stroke SVG, not a glyph (guardrail 7).
        self.assertEqual([button["icons"] for button in opened["copyButtons"]], [1, 1])

    def test_the_hash_control_copies_the_full_object_id(self):
        # The row shows the abbreviation; the clipboard gets what a command or
        # an issue reference wants.
        steps = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
            {"do": "copy", "key": "hash"},
        ])

        self.assertEqual(steps[1]["copied"], [])
        self.assertEqual(steps[2]["copied"], [FULL_HASH])

    def test_the_message_control_copies_the_undecorated_subject(self):
        # `(HEAD -> main)` is how the row renders, never what the author wrote.
        steps = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
            {"do": "copy", "key": "message"},
        ])

        self.assertEqual(steps[2]["copied"], [MESSAGE])

    def test_a_copy_closes_the_card(self):
        # The surface going away is the only acknowledgement a clipboard write
        # gets, exactly as it was when this was a menu entry.
        steps = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
            {"do": "copy", "key": "hash"},
        ])

        self.assertEqual(steps[2]["cards"], 0)
        self.assertEqual(steps[2]["markedRows"], 0)
        self.assertEqual(steps[2]["focused"], SHORT_HASH)

    def test_a_value_the_log_did_not_carry_is_disabled_not_dropped(self):
        # The menu entry's rule: a stable shape, and a row that lost its data
        # says so instead of the control quietly not being there.
        bare = dict(COMMIT)
        bare.pop("message")
        bare.pop("full_hash")
        bare["hash"] = "notahex"
        opened = self._run(
            [{"do": "render"}, {"do": "contextmenu", "commit": "notahex"}],
            commits=[bare],
        )[1]

        by_key = {button["key"]: button for button in opened["copyButtons"]}
        self.assertTrue(by_key["hash"]["disabled"])
        self.assertIn("no commit id", by_key["hash"]["title"])
        self.assertTrue(by_key["message"]["disabled"])

    def test_no_control_copies_anything_until_it_is_clicked(self):
        opened = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
        ])[1]

        self.assertEqual(opened["copied"], [])


class ExplorerGitCommitCardDismissalTestCase(ExplorerGitCommitCardHarness):
    """It is dismissed the way the menu it replaces was, and takes its listeners."""

    def test_the_card_takes_the_keyboard_when_it_opens(self):
        opened = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
        ])[1]

        self.assertEqual(opened["focused"], "message")

    def test_escape_closes_it_and_returns_focus_to_the_row(self):
        steps = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
            {"do": "escape"},
        ])

        self.assertEqual(steps[2]["cards"], 0)
        self.assertEqual(steps[2]["focused"], SHORT_HASH)
        self.assertEqual(steps[2]["docListeners"], {"mousedown": 0, "keydown": 0, "scroll": 0})

    def test_a_press_outside_closes_it_and_one_inside_does_not(self):
        outside = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
            {"do": "outside"},
        ])
        inside = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
            {"do": "inside"},
        ])

        self.assertEqual(outside[2]["cards"], 0)
        self.assertEqual(inside[2]["cards"], 1)

    def test_re_rendering_the_panel_under_it_closes_it(self):
        # The card lives in the panel's innerHTML, so the render takes the
        # element -- but not the two document listeners it left behind.
        steps = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
            {"do": "render"},
        ])

        self.assertEqual(steps[2]["cards"], 0)
        self.assertEqual(steps[2]["docListeners"], {"mousedown": 0, "keydown": 0, "scroll": 0})

    def test_discarding_the_pane_releases_the_card_with_it(self):
        # A pane nobody can see must stop answering keystrokes.
        steps = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
            {"do": "release"},
        ])

        self.assertEqual(steps[2]["cards"], 0)
        self.assertEqual(steps[2]["docListeners"], {"mousedown": 0, "keydown": 0, "scroll": 0})

    def test_scrolling_the_list_under_it_closes_it(self):
        # It is pinned to a row in viewport coordinates, so a scroll that moves
        # that row leaves it pointing at nothing. A card laid out inside the
        # panel scrolled with the row and needed no rule for this.
        steps = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
            {"do": "scroll"},
        ])

        self.assertEqual(steps[2]["cards"], 0)
        self.assertEqual(
            steps[2]["docListeners"], {"mousedown": 0, "keydown": 0, "scroll": 0}
        )

    def test_a_scroll_somewhere_else_leaves_it_alone(self):
        # The pane has several scrollers. Closing on all of them would be a
        # disappearance the reader cannot account for.
        steps = self._run([
            {"do": "render"},
            {"do": "contextmenu", "commit": SHORT_HASH},
            {"do": "scroll-elsewhere"},
        ])

        self.assertEqual(steps[2]["cards"], 1)

    def test_a_second_right_click_replaces_the_first_card(self):
        # One at a time, the way the menu it replaces was.
        second = dict(COMMIT)
        second["hash"] = "ab12cd3"
        second["full_hash"] = "ab12cd34ef56789012345678901234567890abcd"
        second["message"] = "(feat) Another commit"
        second["subject"] = second["message"]
        second["refs"] = ""
        steps = self._run(
            [
                {"do": "render"},
                {"do": "contextmenu", "commit": SHORT_HASH},
                {"do": "contextmenu", "commit": "ab12cd3"},
            ],
            commits=[COMMIT, second],
        )

        self.assertEqual(steps[2]["cards"], 1)
        self.assertEqual(steps[2]["markedRows"], 1)
        self.assertEqual(steps[2]["message"], "(feat) Another commit")
        self.assertEqual(steps[2]["docListeners"], {"mousedown": 1, "keydown": 1, "scroll": 1})


if __name__ == "__main__":
    unittest.main()
