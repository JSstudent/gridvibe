"""Cached tab DOM has one owner; background updates keep the visible panes."""

import unittest

from tests.test_pane_connecting_overlay import (
    HARNESS_STUBS,
    TERMINALS_JS,
    TERMINALS_SOURCE,
    PaneOverlayTestCase,
    _js_function_source,
)

CACHE_SOURCE = "\n\n".join(
    _js_function_source(TERMINALS_JS, name)
    for name in (
        "cacheVisibleGroupView",
        "restoreCachedGroupView",
        "dropCachedGroupView",
        "hasMatchingSessionIds",
        "hasMatchingSessionViews",
        "isExplorerPaneInstance",
        "isBrowserPaneInstance",
        "loadSessionGroups",
        "setFocusedTerminal",
        "getLayoutClass",
    )
)

CACHE_STUBS = r"""
/* Browser semantics relevant to this bug: fragments move their children,
   detached cards cannot be found by document, and clearing markup unmounts
   cards. className and classList share the same state. */
class ViewNode {
    constructor(id = '', nodeType = 1) {
        this.id = id;
        this.nodeType = nodeType;
        this.children = [];
        this.parentNode = null;
        this.dataset = {};
        this.className = '';
        this.style = {
            getPropertyValue: name => this.style[name] || '',
            setProperty: (name, value) => { this.style[name] = value; },
            removeProperty: name => { delete this.style[name]; }
        };
        const names = () => this.className.split(/\s+/).filter(Boolean);
        this.classList = {
            add: (...values) => { this.className = [...new Set([...names(), ...values])].join(' '); },
            remove: (...values) => { this.className = names().filter(name => !values.includes(name)).join(' '); },
            contains: value => names().includes(value)
        };
    }
    get firstChild() { return this.children[0] || null; }
    set innerHTML(value) { this.children.slice().forEach(child => child.remove()); }
    appendChild(child) {
        if (child.nodeType === 11) {
            while (child.firstChild) this.appendChild(child.firstChild);
        } else {
            child.remove();
            child.parentNode = this;
            this.children.push(child);
        }
        return child;
    }
    remove() {
        if (this.parentNode) {
            this.parentNode.children = this.parentNode.children.filter(node => node !== this);
            this.parentNode = null;
        }
        if (this === document.activeElement) document.activeElement = null;
    }
}
const page = new ViewNode();
const grid = new ViewNode('terminalsGrid');
grid.className = 'layout-single';
page.appendChild(grid);
page.appendChild(new ViewNode('emptyState'));
page.appendChild(new ViewNode('sessionLabel'));
document.getElementById = id => {
    const visit = root => root.id === id ? root : root.children.map(visit).find(Boolean);
    return visit(page) || null;
};
document.createDocumentFragment = () => new ViewNode('', 11);
document.activeElement = null;
/* Exceptions caught by the page must still fail the harness. */
console.error = (...args) => { throw new Error(args.map(String).join(' ')); };

var resizeObservers = [];
var _focusedTerminalIndex = -1;
var currentWorkspaceId = 'default';
const workspaceSaveTargets = new Map();
var GROUPS = [{ group_id: 'g1' }, { group_id: 'g2' }];
var LAYOUT = 'grid';
var geometryAdopted = false;
calls.disposed = [];
calls.rooms = [];
calls.observed = 0;
calls.viewport = [];
socket = { emit: (event, payload) => calls.rooms.push([event, payload.session_id]) };
sessionGroups = GROUPS.slice();
knownGroupIds = GROUPS.map(group => group.group_id);
fetch = async path => ({ ok: true, json: async () => path.startsWith('/api/session-groups')
    ? { groups: GROUPS } : { sessions: SESSIONS, layout: LAYOUT } });
function getGroupById(id) { return sessionGroups.find(group => group.group_id === id); }
function setExplorerWorkspaceAppearance() {}
function adoptPresentationRevisions() {}
function syncLocationToGroup() {}
function _stopAllVoice() {}
function clearActiveTerminalHighlight() {
    _focusedTerminalIndex = -1;
    grid.classList.remove('terminal-focus');
}
function isPlainTerminalCard(card) { return Boolean(card); }
function paintActiveTerminalCard() {}
function paintDashboardInputTarget() {}
function clearActiveGridResize() {}
function clearResizeHandles() {}
function captureCachedPaneUiState() {
    terminals.forEach(pane => { pane._cachedTerminalViewport = { viewportY: 17 }; });
}
function noteGroupPresentationChanged() {}
function clearFitTimers() {}
function disconnectObservers() {}
function renderResizeHandles() {}
function restoreCachedPaneUiState() {}
function restoreActiveTerminalObservers() { calls.observed += 1; }
function cancelExplorerFilesystemUiForSession() {}
function forgetExplorerSessionMarkdownAppearance() {}
function clearSessionRoutes() {}
function presentationController() { return null; }
function releaseExplorerResourcesIfIdle() {}
function adoptStoredGeometryForStaleView() {
    calls.geometryReads = (calls.geometryReads || 0) + 1;
    return geometryAdopted;
}
restoreTerminalViewportState = (pane, state) => calls.viewport.push(state);

function mountPane(index, session, pane = null) {
    const card = new ViewNode(`tc-${index}`);
    card.dataset.slot = String(index);
    grid.appendChild(card);
    terminals[index] = pane || {
        _attached: true, _session: session,
        _paneType: session.startup_mode,
        term: { dispose: () => calls.disposed.push(session.session_id) }
    };
    sessionIds[index] = session.session_id;
    return { card, pane: terminals[index] };
}
buildGrid = (sessions, layout) => {
    calls.rebuilt.push(true);
    terminals.forEach(pane => pane.term.dispose());
    grid.innerHTML = '';
    terminals = [];
    sessionIds = [];
    grid.className = getLayoutClass(sessions.length, layout);
    sessions.forEach((session, index) => mountPane(index, session));
    visibleGroupId = activeGroupId;
    gridBuilt = true;
};
cloneSplitSlotRects = (rects = splitSlotRects) => Array.isArray(rects)
    ? rects.map(rect => ({ ...rect })) : null;
function seedA() {
    SESSIONS = [session(0, 'connected')];
    return mountPane(0, SESSIONS[0]);
}
async function swapTo(groupId) {
    activeGroupId = groupId;
    SESSIONS = [session(0, 'connected', { session_id: groupId === 'g1' ? 's1' : 's-b' })];
    await initialLoad();
}
async function roundTrip() {
    const original = seedA();
    await swapTo('g2');
    await swapTo('g1');
    calls.rebuilt = [];
    calls.rooms = [];
    return original;
}
"""


class SessionViewCacheTestCase(PaneOverlayTestCase):
    harness_stubs = HARNESS_STUBS + CACHE_STUBS
    terminals_source = TERMINALS_SOURCE + CACHE_SOURCE

    def test_background_agent_tabs_preserve_restored_panes_and_focus(self):
        result = self._run_node(
            """
            const original = await roundTrip();
            document.activeElement = original.card;
            setFocusedTerminal(0);
            GROUPS.push({ group_id: 'crew', opened_by: 'agent' });
            await refreshStatuses();
            GROUPS.push({ group_id: 'nested', opened_by: 'agent' });
            await refreshStatuses();
            await refreshStatuses();
            report({
                active: activeGroupId, tabs: knownGroupIds,
                sameCard: grid.firstChild === original.card,
                samePane: terminals[0] === original.pane,
                focus: document.activeElement === original.card,
                target: _focusedTerminalIndex, cards: grid.children.length,
                cacheRetained: cachedGroupViews.has('g1'),
                rebuilt: calls.rebuilt, rooms: calls.rooms, disposed: calls.disposed
            });
            """
        )
        self.assertEqual(result["active"], "g1")
        self.assertEqual(result["tabs"], ["g1", "g2", "crew", "nested"])
        self.assertTrue(result["sameCard"])
        self.assertTrue(result["samePane"])
        self.assertTrue(result["focus"])
        self.assertEqual(result["target"], 0)
        self.assertEqual(result["cards"], 1)
        self.assertFalse(result["cacheRetained"])
        self.assertEqual(result["rebuilt"], [])
        self.assertEqual(result["rooms"], [])
        self.assertEqual(result["disposed"], [])

    def test_focus_and_broadcast_classes_allow_current_view_reuse(self):
        for layout in ("layout-single", "layout-2-horizontal", "layout-split-local"):
            with self.subTest(layout=layout):
                result = self._run_node(
                    f"""
                    const original = seedA();
                    if ('{layout}' === 'layout-2-horizontal') {{
                        SESSIONS.push(session(1, 'connected'));
                        mountPane(1, SESSIONS[1]);
                        LAYOUT = 'horizontal';
                    }}
                    grid.className = '{layout} broadcast-input terminal-focus';
                    document.activeElement = original.card;
                    await refreshStatuses();
                    await initialLoad();
                    report({{ same: grid.firstChild === original.card,
                        focus: document.activeElement === original.card,
                        rebuilt: calls.rebuilt, rooms: calls.rooms }});
                    """
                )
                self.assertTrue(result["same"])
                self.assertTrue(result["focus"])
                self.assertEqual(result["rebuilt"], [])
                self.assertEqual(result["rooms"], [])

    def test_repeated_swaps_transfer_views_and_preserve_split_geometry(self):
        result = self._run_node(
            """
            const original = seedA();
            grid.className = 'layout-split-local broadcast-input';
            splitSlotRects = [{ x: 1, y: 1, w: 16, h: 16 }];
            splitColumnWeights = [2, 1];
            splitRowWeights = [3, 1];
            originalSplitSlotCount = 4;
            for (let i = 0; i < 3; i += 1) {
                await swapTo('g2');
                await swapTo('g1');
            }
            report({ same: grid.firstChild === original.card,
                samePane: terminals[0] === original.pane,
                cachedVisible: cachedGroupViews.has('g1'),
                cachedOther: cachedGroupViews.get('g2').fragment.children.length,
                rects: splitSlotRects, columns: splitColumnWeights,
                rows: splitRowWeights, base: originalSplitSlotCount,
                rebuilt: calls.rebuilt.length, disposed: calls.disposed,
                rooms: calls.rooms, viewport: calls.viewport, observed: calls.observed });
            """
        )
        self.assertTrue(result["same"])
        self.assertTrue(result["samePane"])
        self.assertFalse(result["cachedVisible"])
        self.assertEqual(result["cachedOther"], 1)
        self.assertEqual(result["rects"], [{"x": 1, "y": 1, "w": 16, "h": 16}])
        self.assertEqual(result["columns"], [2, 1])
        self.assertEqual(result["rows"], [3, 1])
        self.assertEqual(result["base"], 4)
        self.assertEqual(result["rebuilt"], 1)
        self.assertEqual(result["disposed"], [])
        self.assertEqual(result["rooms"], [["join_session", "s-b"]])
        self.assertEqual(result["observed"], 5)
        self.assertIn({"viewportY": 17}, result["viewport"])

    def test_invalid_fragment_is_rejected_before_clearing_the_live_grid(self):
        for invalid in ("empty", "wrong_card", "missing_instance", "missing_id"):
            with self.subTest(invalid=invalid):
                result = self._run_node(
                    f"""
                    const original = seedA();
                    const fragment = document.createDocumentFragment();
                    if ('{invalid}' !== 'empty') fragment.appendChild(new ViewNode(
                        '{invalid}' === 'wrong_card' ? 'tc-9' : 'tc-0'));
                    cachedGroupViews.set('g2', {{ fragment, className: 'layout-single',
                        terminals: ['{invalid}' === 'missing_instance' ? null : original.pane],
                        sessionIds: '{invalid}' === 'missing_id' ? [] : ['s-b'] }});
                    const restored = restoreCachedGroupView('g2');
                    report({{ restored, same: grid.firstChild === original.card,
                        samePane: terminals[0] === original.pane }});
                    """
                )
                self.assertFalse(result["restored"])
                self.assertTrue(result["same"])
                self.assertTrue(result["samePane"])

    def test_empty_cache_recovers_through_the_normal_load(self):
        result = self._run_node(
            """
            seedA();
            await swapTo('g2');
            const cached = cachedGroupViews.get('g1');
            cached.fragment.innerHTML = '';
            calls.rebuilt = [];
            await swapTo('g1');
            await refreshStatuses();
            report({ cards: grid.children.length, rebuilt: calls.rebuilt.length,
                cached: cachedGroupViews.has('g1'), disposed: calls.disposed });
            """
        )
        self.assertEqual(result["cards"], 1)
        self.assertEqual(result["rebuilt"], 1)
        self.assertFalse(result["cached"])
        self.assertEqual(result["disposed"], ["s1"])

    def test_missing_visible_cards_recover_on_refresh(self):
        result = self._run_node(
            """
            seedA();
            grid.innerHTML = '';
            await refreshStatuses();
            await refreshStatuses();
            report({ cards: grid.children.length, rebuilt: calls.rebuilt.length });
            """
        )
        self.assertEqual(result["cards"], 1)
        self.assertEqual(result["rebuilt"], 1)

    def test_legitimate_session_type_count_and_layout_changes_rebuild(self):
        for change in ("identity", "type", "count", "layout"):
            with self.subTest(change=change):
                result = self._run_node(
                    f"""
                    seedA();
                    if ('{change}' === 'identity') SESSIONS[0].session_id = 'replacement';
                    if ('{change}' === 'type') SESSIONS[0].startup_mode = 'browser';
                    if ('{change}' === 'count') SESSIONS.push(session(1, 'connected'));
                    if ('{change}' === 'layout') {{
                        SESSIONS.push(session(1, 'connected'));
                        mountPane(1, SESSIONS[1]);
                        grid.className = 'layout-2-vertical';
                        LAYOUT = 'horizontal';
                    }}
                    await refreshStatuses();
                    report({{ cards: grid.children.length, rebuilt: calls.rebuilt.length }});
                    """
                )
                self.assertEqual(result["rebuilt"], 1)
                self.assertEqual(result["cards"], 2 if change in ("count", "layout") else 1)

    def test_stale_geometry_must_be_adopted_before_a_cached_view_is_reused(self):
        for adopted in (True, False):
            with self.subTest(adopted=adopted):
                result = self._run_node(
                    f"""
                    const original = seedA();
                    await swapTo('g2');
                    cachedGroupViews.get('g1').geometryStale = true;
                    geometryAdopted = {str(adopted).lower()};
                    calls.rebuilt = [];
                    await swapTo('g1');
                    report({{ same: grid.firstChild === original.card,
                        geometryReads: calls.geometryReads,
                        rebuilt: calls.rebuilt.length, cached: cachedGroupViews.has('g1') }});
                    """
                )
                self.assertEqual(result["same"], adopted)
                self.assertEqual(result["geometryReads"], 1)
                self.assertEqual(result["rebuilt"], 0 if adopted else 1)
                self.assertFalse(result["cached"])

    def test_a_late_tab_read_cannot_restore_the_tab_the_user_left(self):
        result = self._run_node(
            """
            const original = await roundTrip();
            const normalFetch = fetch;
            let releaseRead;
            let readStarted;
            const started = new Promise(resolve => { readStarted = resolve; });
            fetch = async path => {
                if (path === '/api/sessions' && activeGroupId === 'g2') {
                    const sessions = SESSIONS.slice();
                    readStarted();
                    return { ok: true, json: () => new Promise(resolve => {
                        releaseRead = () => resolve({ sessions, layout: LAYOUT });
                    }) };
                }
                return normalFetch(path);
            };
            const pending = swapTo('g2');
            await started;
            await swapTo('g1');
            releaseRead();
            await pending;
            report({ active: activeGroupId, visible: visibleGroupId,
                same: grid.firstChild === original.card, rebuilt: calls.rebuilt,
                cachedOther: cachedGroupViews.has('g2'), rooms: calls.rooms });
            """
        )
        self.assertEqual(result["active"], "g1")
        self.assertEqual(result["visible"], "g1")
        self.assertTrue(result["same"])
        self.assertTrue(result["cachedOther"])
        self.assertEqual(result["rebuilt"], [])
        self.assertEqual(result["rooms"], [])


if __name__ == "__main__":
    unittest.main()
