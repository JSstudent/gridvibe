"""The crew model and wire layer, executed rather than read.

`agent-crews.js` turns the dashboard reading's `links` into the crews both
dashboard surfaces draw. Its model and geometry are DOM-free, so they run here
in Node:

- **The index is always a forest.** One edge per (requester, worker) pair, the
  newest round winning; one parent per worker, the requester of its newest
  link; a loop broken at its earliest-handed link, so every walk terminates.
- **One drawn state per link.** `linkPhase` is the truth table the wire, the
  chip and the board pill all read. A report is its status whether or not it
  has been collected; collecting is a fact beside the phase.
- **The chip counts workers, not assignments.**
- **A narrow reading draws one bus per orchestrator,** each in a lane of its own
  (up to a limit; past it, lanes are shared only by crews whose rows do not
  overlap), spaced far enough apart to read as separate lines, and asks for
  exactly the gutter its lanes need. A branch ends on its own worker's row.

The wire layer is the module's one DOM part, so it runs against a small stub
of the elements it touches: it must rewrite its SVG only when the picture
changes, never key anything on `link_id`, and survive the scroller's contents
being replaced under it.

Source assertions are kept to what only the stylesheet and the markup can
say: the reduced-motion rule, the tokens the wires are drawn in, and where the
pages load the module.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC = REPO_ROOT / "web" / "static"
AGENT_CREWS_JS = STATIC / "js" / "agent-crews.js"
DASHBOARD_CSS = STATIC / "css" / "agent-dashboard.css"
TEMPLATES = REPO_ROOT / "templates"

NODE = shutil.which("node")

HARNESS = r"""
const crews = require(AGENT_CREWS_JS);
function report(value) { process.stdout.write(JSON.stringify(value)); }

let handed = 0;
function link(requester, worker, extra) {
    handed += 1;
    return Object.assign({
        link_id: `l${handed}`,
        requester_session_id: requester,
        worker_session_id: worker,
        state: 'working',
        read: true,
        status: '',
        collected: false,
        round: 1
    }, extra || {});
}

function plain(index) {
    const byRequester = {};
    index.byRequester.forEach((links, id) => {
        byRequester[id] = links.map(l => `${l.worker_session_id}#${l.link_id}`);
    });
    const byWorker = {};
    index.byWorker.forEach((l, id) => { byWorker[id] = l.requester_session_id; });
    const rootOf = {};
    index.rootOf.forEach((root, id) => { rootOf[id] = root; });
    return { byRequester, byWorker, rootOf, roots: index.roots, edges: index.edges.length };
}
"""

# A stub of the few DOM touches the wire layer makes: rows found by
# `data-session-id`, their rectangles and their card's, and an SVG whose
# `innerHTML` writes are counted and whose `[data-crew]` groups are real
# objects carrying a class list.
DOM_STUB = r"""
function classList(initial) {
    const set = new Set(initial || []);
    return {
        add: n => set.add(n),
        remove: n => set.delete(n),
        toggle: (n, on) => { const want = on === undefined ? !set.has(n) : Boolean(on); want ? set.add(n) : set.delete(n); return want; },
        contains: n => set.has(n),
        list: () => Array.from(set).sort()
    };
}

function makeSvg() {
    const attrs = {};
    let html = '';
    const svg = {
        parentNode: null,
        writes: 0,
        groups: [],
        classList: classList(),
        paused: 0,
        setAttribute: (k, v) => { attrs[k] = String(v); },
        getAttribute: k => attrs[k],
        querySelectorAll: () => svg.groups,
        pauseAnimations: () => { svg.paused += 1; },
        unpauseAnimations: () => { svg.paused -= 1; }
    };
    Object.defineProperty(svg, 'innerHTML', {
        get: () => html,
        set: value => {
            html = value;
            svg.writes += 1;
            svg.groups = Array.from(value.matchAll(/<(g|circle) class="([^"]*)" data-crew="([^"]*)"/g)).map(m => ({
                tag: m[1],
                crew: m[3],
                getAttribute: k => (k === 'data-crew' ? m[3] : null),
                classList: classList(m[2].split(' '))
            }));
        }
    });
    return svg;
}

function makePage(rows) {
    // rows: { id: { top, card } } ; every card spans x 20..220, rows are 20px tall.
    const cards = {};
    const doc = {
        defaultView: { ResizeObserver: null },
        createElementNS: (ns, tag) => { doc.created += 1; return makeSvg(); },
        created: 0
    };
    const children = [];
    const container = {
        ownerDocument: doc,
        scrollLeft: 0,
        scrollTop: 0,
        classList: classList(),
        style: {
            props: {},
            getPropertyValue(name) { return this.props[name] || ''; },
            setProperty(name, value) { this.props[name] = value; },
            removeProperty(name) { delete this.props[name]; }
        },
        get firstChild() { return children[0] || null; },
        children,
        rows,
        getBoundingClientRect: () => ({ left: 0, top: 0, right: 300, bottom: 1000 }),
        insertBefore: (node, before) => {
            const at = before ? children.indexOf(before) : children.length;
            children.splice(at < 0 ? children.length : at, 0, node);
            node.parentNode = container;
        },
        removeChild: node => {
            const at = children.indexOf(node);
            if (at >= 0) children.splice(at, 1);
            node.parentNode = null;
        },
        replaceContents: () => {
            children.slice().forEach(node => container.removeChild(node));
        },
        querySelector: selector => {
            const m = /^\[data-session-id="(.*)"\]$/.exec(selector);
            const id = m && m[1];
            const row = id && container.rows[id];
            if (!row) return null;
            const card = cards[row.card] || (cards[row.card] = {
                getBoundingClientRect: () => ({ left: 20, right: 220, top: 0, bottom: 0 })
            });
            return {
                getBoundingClientRect: () => ({
                    left: 24, right: 216, top: row.top - container.scrollTop, bottom: row.top + 20 - container.scrollTop
                }),
                closest: () => card
            };
        }
    };
    return { doc, container };
}

function svgOf(page) { return page.container.children.find(node => node.writes !== undefined) || null; }
"""


def _templates_script_order(template: str):
    html = (TEMPLATES / template).read_text(encoding="utf-8")
    return [
        match.group(1)
        for match in re.finditer(r"filename='js/([^']+)'", html)
    ]


@unittest.skipUnless(NODE, "Node.js is required for the agent crew tests")
class AgentCrewsNodeTestCase(unittest.TestCase):
    def _run_node(self, body: str, dom: bool = False):
        script = (
            f"const AGENT_CREWS_JS = {json.dumps(str(AGENT_CREWS_JS))};\n"
            + HARNESS
            + (DOM_STUB if dom else "")
            + "\n" + body + "\n"
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


class IndexCrewsTestCase(AgentCrewsNodeTestCase):
    def test_a_nested_crew_is_one_tree_under_its_orchestrator(self):
        result = self._run_node(
            """
            const index = crews.indexCrews({ links: [
                link('orch', 'a'), link('orch', 'b'), link('a', 'a1'), link('x', 'y')
            ] });
            report({
                index: plain(index),
                members: crews.crewMembers(index, 'orch'),
                notARoot: crews.crewMembers(index, 'a')
            });
            """
        )
        index = result["index"]
        self.assertEqual(index["roots"], ["orch", "x"])
        self.assertEqual(
            index["rootOf"],
            {"orch": "orch", "a": "orch", "b": "orch", "a1": "orch", "x": "x", "y": "x"},
        )
        self.assertEqual(index["byWorker"], {"a": "orch", "b": "orch", "a1": "a", "y": "x"})
        # Depth-first, siblings in the order they were handed.
        self.assertEqual(result["members"], ["orch", "a", "a1", "b"])
        self.assertEqual(result["notARoot"], [])

    def test_one_edge_per_pair_and_the_newest_round_wins_in_place(self):
        """An uncollected report and its follow-up round are both in the
        reading; only the follow-up is drawn, and the worker keeps its place
        among its siblings rather than moving to the end."""
        result = self._run_node(
            """
            const index = crews.indexCrews({ links: [
                link('orch', 'a', { state: 'reported', status: 'done', round: 1 }),
                link('orch', 'b'),
                link('orch', 'a', { read: false, round: 2 })
            ] });
            report(plain(index));
            """
        )
        self.assertEqual(result["byRequester"], {"orch": ["a#l3", "b#l2"]})
        self.assertEqual(result["edges"], 2)

    def test_order_follows_the_list_and_survives_a_dropped_round(self):
        """The store drops a collected round before it adds the follow-up, so
        the pair moves to the end of the reading. Siblings and crews hold
        their places by where their panes are listed; a ghost follows."""
        result = self._run_node(
            """
            const panes = ids => ({ workspaces: [{ groups: [{ panes: ids.map(session_id => ({ session_id })) }] }] });
            const listed = panes(['o', 'a', 'b', 'x', 'y']);
            const before = crews.indexCrews(Object.assign({ links: [
                link('x', 'y'),
                link('o', 'a', { link_id: 'r1', state: 'reported', status: 'done', collected: true }),
                link('o', 'ghost'),
                link('o', 'b')
            ] }, listed));
            const after = crews.indexCrews(Object.assign({ links: [
                link('x', 'y'),
                link('o', 'ghost'),
                link('o', 'b'),
                link('o', 'a', { link_id: 'r2', round: 2 })
            ] }, listed));
            const order = index => index.byRequester.get('o').map(l => l.worker_session_id);
            report({ before: order(before), after: order(after), roots: after.roots });
            """
        )
        self.assertEqual(result["before"], ["a", "b", "ghost"])
        self.assertEqual(result["after"], ["a", "b", "ghost"])
        self.assertEqual(result["roots"], ["o", "x"])

    def test_a_worker_hangs_under_the_requester_of_its_newest_link(self):
        """A pane re-tasked by someone else with `set_pane_agent` keeps its old
        settled link in the reading; the tree places it once."""
        result = self._run_node(
            """
            const index = crews.indexCrews({ links: [
                link('first', 'w', { state: 'reported', status: 'done', collected: true }),
                link('first', 'other'),
                link('second', 'w')
            ] });
            report(plain(index));
            """
        )
        self.assertEqual(result["byWorker"], {"w": "second", "other": "first"})
        self.assertEqual(result["byRequester"], {"first": ["other#l2"], "second": ["w#l3"]})
        self.assertEqual(result["roots"], ["first", "second"])

    def test_a_loop_is_broken_at_its_earliest_handed_link(self):
        result = self._run_node(
            """
            const two = crews.indexCrews({ links: [link('a', 'b'), link('b', 'a')] });
            const three = crews.indexCrews({ links: [
                link('p', 'q'), link('q', 'r'), link('r', 'p'), link('r', 'tail'), link('q', 'r', { round: 2 })
            ] });
            report({
                two: plain(two),
                three: plain(three),
                members: crews.crewMembers(three, three.roots[0])
            });
            """
        )
        # a -> b was handed first, so it goes; b becomes the root.
        self.assertEqual(result["two"]["byWorker"], {"a": "b"})
        self.assertEqual(result["two"]["roots"], ["b"])
        self.assertEqual(result["two"]["rootOf"], {"a": "b", "b": "b"})
        # p -> q is the earliest-handed edge of the loop (q -> r's newest round
        # is the latest), so q is the root of the whole crew.
        three = result["three"]
        self.assertEqual(three["roots"], ["q"])
        self.assertNotIn("q", three["byWorker"])
        self.assertEqual(set(three["rootOf"].values()), {"q"})
        self.assertEqual(result["members"], ["q", "r", "p", "tail"])

    def test_nothing_to_index_is_an_empty_forest(self):
        result = self._run_node(
            """
            report([
                plain(crews.indexCrews(null)),
                plain(crews.indexCrews({})),
                plain(crews.indexCrews({ links: [
                    link('a', 'a'), link('', 'b'), link('c', '')
                ] }))
            ]);
            """
        )
        empty = {"byRequester": {}, "byWorker": {}, "rootOf": {}, "roots": [], "edges": 0}
        self.assertEqual(result, [empty, empty, empty])


class LinkPhaseTestCase(AgentCrewsNodeTestCase):
    def test_the_phase_truth_table(self):
        result = self._run_node(
            """
            const rows = [
                ['handed', { state: 'working', read: false }],
                ['working', { state: 'working', read: true }],
                ['done', { state: 'reported', status: 'done' }],
                ['failed', { state: 'reported', status: 'failed' }],
                ['blocked', { state: 'reported', status: 'blocked' }],
                ['done', { state: 'reported', status: 'done', collected: true }],
                ['blocked', { state: 'reported', status: 'blocked', collected: true }],
                ['failed', { state: 'reported', status: 'failed', collected: true }],
                ['ended', { state: 'ended', reason: 'pane closed' }],
                ['ended', { state: 'ended', read: true, collected: true }]
            ];
            report(rows.map(([want, row]) => [want, crews.linkPhase(row)]));
            """
        )
        for want, got in result:
            self.assertEqual(got, want)

    def test_collecting_a_report_changes_nothing_that_is_drawn(self):
        """Collected is a fact beside the phase, put into words by the board
        and never a second look: the wire and its end dot are the same
        whether the orchestrator has taken the report or not."""
        result = self._run_node(
            """
            const both = status => [false, true].map(collected => ({
                wire: crews.wireClasses({ state: 'reported', status, collected }),
                end: crews.endClasses({ state: 'reported', status, collected }),
                collected: crews.linkCollected({ state: 'reported', status, collected })
            }));
            report({
                done: both('done'), blocked: both('blocked'), failed: both('failed'),
                working: crews.linkCollected({ state: 'working', collected: true }),
                phases: crews.PHASES,
                awaitedWorking: crews.wireClasses({ state: 'working', read: true }, { awaited: true }),
                awaitedHanded: crews.wireClasses({ state: 'working', read: false }, { awaited: true }),
                awaitedDone: crews.wireClasses({ state: 'reported', status: 'done' }, { awaited: true })
            });
            """
        )
        for status in ("done", "blocked", "failed"):
            with self.subTest(status=status):
                fresh, taken = result[status]
                self.assertEqual(fresh["wire"], f"dash-wire is-{status}")
                self.assertEqual(fresh["end"], f"dash-wire-end is-{status}")
                self.assertEqual((fresh["wire"], fresh["end"]), (taken["wire"], taken["end"]))
                self.assertEqual([fresh["collected"], taken["collected"]], [False, True])
        # Only a report can have been collected.
        self.assertFalse(result["working"])
        self.assertNotIn("collected", result["phases"])
        # The glow is for a worker still on it, nothing else.
        self.assertEqual(result["awaitedWorking"], "dash-wire is-working is-awaited")
        self.assertNotIn("is-awaited", result["awaitedHanded"])
        self.assertNotIn("is-awaited", result["awaitedDone"])

    def test_both_waiting_kinds_have_their_own_words(self):
        result = self._run_node(
            """
            report(['crew', 'task', '', 'other', null].map(crews.waitingWord));
            """
        )
        self.assertEqual(
            result,
            ["Waiting on its crew", "Standing by for its next task", "", "", ""],
        )


class CrewSummaryTestCase(AgentCrewsNodeTestCase):
    def test_the_chip_counts_each_worker_once_by_its_current_round(self):
        result = self._run_node(
            """
            const index = crews.indexCrews({ links: [
                link('orch', 'a', { state: 'reported', status: 'done' }),
                link('orch', 'b', { state: 'reported', status: 'failed' }),
                link('orch', 'c', { read: false }),
                link('orch', 'a', { round: 2 }),
                link('orch', 'd', { state: 'reported', status: 'blocked', collected: true }),
                link('orch', 'e', { state: 'ended' }),
                link('a', 'a1', { state: 'reported', status: 'done' })
            ] });
            report({
                orch: crews.crewSummary(index, 'orch'),
                a: crews.crewSummary(index, 'a'),
                nobody: crews.crewSummary(index, 'nobody'),
                none: crews.crewSummary(null, 'orch')
            });
            """
        )
        # a's follow-up is working again, so it is not reported; b, d count.
        self.assertEqual(result["orch"], {"total": 5, "reported": 2})
        self.assertEqual(result["a"], {"total": 1, "reported": 1})
        self.assertEqual(result["nobody"], {"total": 0, "reported": 0})
        self.assertEqual(result["none"], {"total": 0, "reported": 0})

    def test_awaited_requesters_are_the_panes_waiting_on_their_crew(self):
        result = self._run_node(
            """
            const snapshot = { workspaces: [{ groups: [
                { panes: [{ session_id: 'o', waiting: 'crew' }, { session_id: 'w', waiting: 'task' }] },
                { panes: [{ session_id: 'q', waiting: '' }] }
            ] }] };
            report(Array.from(crews.awaitingRequesters(snapshot)));
            """
        )
        self.assertEqual(result, ["o"])


class LaneGeometryTestCase(AgentCrewsNodeTestCase):
    def test_lanes_pack_shortest_first_and_overlapping_spans_get_their_own(self):
        result = self._run_node(
            """
            report({
                // Two spans leaving one row, a long one and a short one.
                siblings: crews.assignLanes([{ lo: 0, hi: 100 }, { lo: 0, hi: 30 }]),
                // Apart, they share the innermost lane.
                apart: crews.assignLanes([{ lo: 0, hi: 30 }, { lo: 40, hi: 90 }]),
                // A chain shares a row: touching is overlapping.
                touching: crews.assignLanes([{ lo: 0, hi: 30 }, { lo: 30, hi: 60 }]),
                reversed: crews.assignLanes([{ lo: 50, hi: 10 }]),
                empty: crews.assignLanes([]),
                // However many nest, each keeps a lane: nothing collapses.
                nested: crews.assignLanes(Array.from({ length: 9 }, (_, i) => ({ lo: 0, hi: 10 * (i + 1) })))
            });
            """
        )
        self.assertEqual(result["siblings"], {"lanes": [1, 0], "count": 2})
        self.assertEqual(result["apart"], {"lanes": [0, 0], "count": 1})
        self.assertEqual(result["touching"], {"lanes": [0, 1], "count": 2})
        self.assertEqual(result["reversed"], {"lanes": [0], "count": 1})
        self.assertEqual(result["empty"], {"lanes": [], "count": 0})
        self.assertEqual(result["nested"]["lanes"], list(range(9)))
        self.assertEqual(result["nested"]["count"], 9)

    def test_a_crew_of_six_is_one_bus_with_six_branches(self):
        """The sidebar's tight case: one orchestrator, six workers. One lane,
        one spine and a short tick into each worker, not six curves."""
        result = self._run_node(
            """
            const wire = y => ({ requester: 'o', from: { x: 20, y: 10 }, to: { x: 20, y } });
            const plan = crews.planBuses([50, 90, 130, 170, 210, 250].map(wire));
            const bus = plan.buses[0];
            report({
                buses: plan.buses.length, lanes: plan.lanes, gutter: plan.gutter,
                x: bus.x, top: bus.top, bottom: bus.bottom,
                paths: crews.busPaths(bus)
            });
            """
        )
        self.assertEqual(result["buses"], 1)
        self.assertEqual(result["lanes"], 1)
        # 12 px out from the card edge, and 6 px of air beyond the line.
        self.assertEqual(result["x"], 8)
        self.assertEqual(result["gutter"], 18)
        self.assertEqual((result["top"], result["bottom"]), (10, 250))
        self.assertEqual(result["paths"]["trunk"], "M20 10 H8 M8 10 V250")
        self.assertEqual(
            result["paths"]["branches"],
            [f"M8 {y} H20" for y in (50, 90, 130, 170, 210, 250)],
        )

    def test_overlapping_orchestrators_get_lanes_and_the_gutter_grows_with_them(self):
        result = self._run_node(
            """
            const wire = (requester, fromY, y) => ({
                requester, from: { x: 20, y: fromY }, to: { x: 20, y }
            });
            // `a` is both a worker of `o` and the orchestrator of its own
            // crew: its rows sit inside `o`'s span, so they cannot share a lane.
            const nested = crews.planBuses([wire('o', 10, 50), wire('a', 50, 90), wire('o', 10, 130)]);
            // Two crews one above the other do not share one either: a shared
            // lane reads as one long line down the edge.
            const apart = crews.planBuses([wire('o', 10, 50), wire('p', 200, 240)]);
            report({
                nested: nested.buses.map(bus => [bus.requester, bus.lane, bus.x]),
                nestedGutter: nested.gutter,
                apart: apart.buses.map(bus => [bus.requester, bus.lane]),
                apartGutter: apart.gutter,
                none: crews.planBuses([]),
                one: crews.planBuses([wire('o', 10, 50)]).gutter
            });
            """
        )
        # The shorter span is nearest the cards; the longer is a lane out.
        self.assertEqual(result["nested"], [["o", 1, 0], ["a", 0, 8]])
        self.assertEqual(result["nestedGutter"], 26)
        self.assertEqual(result["apart"], [["o", 0], ["p", 1]])
        self.assertEqual(result["apartGutter"], 26)
        self.assertEqual(result["none"], {"buses": [], "lanes": 0, "gutter": 0})
        self.assertEqual(result["one"], 18)

    def test_a_nested_orchestrators_bus_clears_its_own_cards_indent(self):
        """In the dialog's narrow board a deeper card sits further right, so
        its bus sits right of the root's and the gutter is measured from the
        leftmost card only."""
        result = self._run_node(
            """
            const plan = crews.planBuses([
                { requester: 'o', from: { x: 26, y: 10 }, to: { x: 44, y: 50 } },
                { requester: 'a', from: { x: 44, y: 50 }, to: { x: 62, y: 90 } }
            ]);
            report(plan.buses.map(bus => [bus.requester, bus.lane, bus.x]).concat([[plan.gutter]]));
            """
        )
        # `o` (rows 10-50) and `a` (rows 50-90) touch at row 50, so two lanes.
        self.assertEqual(result, [["o", 0, 14], ["a", 1, 24], [18]])

    def test_every_orchestrator_has_its_own_lane_and_the_lanes_are_well_apart(self):
        result = self._run_node(
            """
            const wire = (requester, fromY, y) => ({
                requester, from: { x: 20, y: fromY }, to: { x: 20, y }
            });
            // Three crews far apart on the column, as in a real session list.
            const three = crews.planBuses([wire('a', 10, 50), wire('b', 400, 440), wire('c', 800, 840)]);
            // One more than the limit: lanes are shared again, but only by crews
            // whose rows do not overlap.
            const many = Array.from({ length: crews.MAX_DISTINCT_LANES + 1 },
                (_, i) => wire(`o${i}`, i * 100, i * 100 + 40));
            const crowded = crews.planBuses(many);
            // Past the limit, crews that do overlap still get different lanes.
            const overlapping = crews.planBuses(Array.from({ length: crews.MAX_DISTINCT_LANES + 1 },
                (_, i) => wire(`p${i}`, 10, 50 + i)));
            report({
                step: crews.LANE_STEP,
                inset: crews.LANE_INSET,
                three: three.buses.map(bus => [bus.lane, bus.x]),
                threeLanes: three.lanes,
                crowded: crowded.lanes,
                overlappingLanes: new Set(overlapping.buses.map(bus => bus.lane)).size,
                distinct: crews.assignDistinctLanes([{ lo: 0, hi: 30 }, { lo: 0, hi: 10 }, { lo: 50, hi: 80 }]),
                limit: crews.MAX_DISTINCT_LANES
            });
            """
        )
        self.assertGreaterEqual(result["step"], 8)
        self.assertGreaterEqual(result["inset"], 12)
        # Never a shared lane, however far apart the crews sit.
        self.assertEqual(result["three"], [[0, 8], [1, 0], [2, -8]])
        self.assertEqual(result["threeLanes"], 3)
        # Each lane is one line, `step` away from the next.
        xs = [x for _lane, x in result["three"]]
        self.assertTrue(all(a - b == result["step"] for a, b in zip(xs, xs[1:])))
        self.assertEqual(result["limit"], 6)
        # Past the limit the column packs by row overlap, so seven crews one
        # below another need one lane, not seven.
        self.assertEqual(result["crowded"], 1)
        self.assertEqual(result["overlappingLanes"], result["limit"] + 1)
        # The shortest span is nearest the cards, ties in the order given.
        self.assertEqual(result["distinct"], {"lanes": [1, 0, 2], "count": 3})

    def test_a_wide_boards_curve_runs_from_the_parent_to_the_child(self):
        result = self._run_node(
            """
            report({
                fan: crews.fanPath({ x: 100, y: 10 }, { x: 160, y: 50 }),
                closeFan: crews.fanPath({ x: 100, y: 10 }, { x: 110, y: 50 })
            });
            """
        )
        # Control points half the gap out, but never under 16 px.
        self.assertEqual(result["fan"], "M100 10 C130 10 130 50 160 50")
        self.assertEqual(result["closeFan"], "M100 10 C116 10 94 50 110 50")


class WireLayerTestCase(AgentCrewsNodeTestCase):
    def test_a_poll_with_nothing_new_leaves_the_svg_alone(self):
        """A flowing dash restarts whenever its element is rewritten, so the
        layer writes only a changed picture. A new round of the same pair is
        not a change until its phase is."""
        result = self._run_node(
            """
            const page = makePage({ o: { top: 0, card: 1 }, a: { top: 40, card: 1 }, b: { top: 80, card: 2 } });
            const layer = crews.createWireLayer({ container: page.container, mode: 'lane' });
            const snap = links => ({ links, workspaces: [] });
            layer.paint(snap([link('o', 'a', { link_id: 'r1' }), link('o', 'b')]));
            const svg = svgOf(page);
            const first = svg.writes;
            layer.paint(snap([link('o', 'a', { link_id: 'r1' }), link('o', 'b', { link_id: 'l2' })]));
            const afterSame = svg.writes;
            layer.paint(snap([link('o', 'a', { link_id: 'r2', round: 2 }), link('o', 'b', { link_id: 'l2' })]));
            const afterRound = svg.writes;
            layer.paint(snap([link('o', 'a', { link_id: 'r2', state: 'reported', status: 'done' }), link('o', 'b', { link_id: 'l2' })]));
            report({
                first, afterSame, afterRound, afterPhase: svg.writes,
                groups: svg.groups.filter(g => g.tag === 'g').length,
                ariaHidden: svg.getAttribute('aria-hidden'),
                cls: svg.getAttribute('class'),
                hosted: page.container.classList.contains('has-dash-wires'),
                html: svg.innerHTML
            });
            """,
            dom=True,
        )
        self.assertEqual(result["first"], 1)
        self.assertEqual(result["afterSame"], 1)
        self.assertEqual(result["afterRound"], 1)
        self.assertEqual(result["afterPhase"], 2)
        # One bus for the one orchestrator, whatever it handed out.
        self.assertEqual(result["groups"], 1)
        self.assertEqual(result["ariaHidden"], "true")
        self.assertEqual(result["cls"], "dash-wires")
        self.assertTrue(result["hosted"])
        self.assertNotIn("r2", result["html"])
        # A settled report is a still line: nothing travels along any wire.
        self.assertNotIn("animateMotion", result["html"])
        self.assertNotIn("dash-wire-pulse", result["html"])
        self.assertIn("dash-wire dash-wire-bus", result["html"])
        # The trunk starts at the orchestrator's own row (x 24), past its
        # card's edge (x 20), and the spine is one lane out from the card.
        self.assertIn('d="M24 10 H8 M8 10 V90"', result["html"])

    def test_separate_crews_get_separate_lanes_and_a_smaller_reading_gives_the_room_back(self):
        """Three crews in three cards, as in a real session list: three buses
        in three lanes, none of them the other's trunk. Closing panes down to
        one crew gives the gutter back, so a smaller crew never keeps the old
        width."""
        result = self._run_node(
            """
            const page = makePage({
                o1: { top: 0, card: 1 }, w1: { top: 40, card: 1 },
                o2: { top: 100, card: 2 }, w2: { top: 140, card: 2 }, w3: { top: 180, card: 2 },
                o3: { top: 240, card: 3 }, w4: { top: 280, card: 3 }
            });
            const layer = crews.createWireLayer({ container: page.container, mode: 'lane' });
            const gutter = () => page.container.style.getPropertyValue('--dash-wire-gutter');
            const trunks = () => Array.from(svgOf(page).innerHTML
                .matchAll(/dash-wire-bus" d="M(-?\\d+) (-?\\d+) H(-?\\d+) M(-?\\d+) (-?\\d+) V(-?\\d+)"/g))
                .map(m => ({ from: Number(m[2]), x: Number(m[3]), top: Number(m[5]), bottom: Number(m[6]) }));
            layer.paint({ links: [link('o1', 'w1'), link('o2', 'w2'), link('o2', 'w3'), link('o3', 'w4')] });
            const three = { gutter: gutter(), trunks: trunks() };
            layer.paint({ links: [link('o2', 'w2'), link('o2', 'w3')] });
            const one = { gutter: gutter(), trunks: trunks() };
            report({ three, one });
            """,
            dom=True,
        )
        three = result["three"]
        self.assertEqual(len(three["trunks"]), 3)
        xs = [trunk["x"] for trunk in three["trunks"]]
        self.assertEqual(len(set(xs)), 3, "every crew has a lane of its own")
        spread = sorted(xs)
        self.assertTrue(all(b - a >= 8 for a, b in zip(spread, spread[1:])))
        # Each spine spans its own crew's rows and nobody else's.
        spans = sorted((trunk["top"], trunk["bottom"]) for trunk in three["trunks"])
        self.assertEqual(spans, [(10, 50), (110, 190), (250, 290)])
        # 12 + 2 * 8 px out from the card edge, plus 6 px of air.
        self.assertEqual(three["gutter"], "34px")
        # One crew left: one lane's gutter again, and its bus back at lane 0.
        self.assertEqual(result["one"]["gutter"], "18px")
        self.assertEqual([trunk["x"] for trunk in result["one"]["trunks"]], [8])

    def test_a_branch_ends_on_its_own_row_and_a_nested_trunk_leaves_below_the_branch(self):
        result = self._run_node(
            """
            const page = makePage({
                o: { top: 0, card: 1 }, a: { top: 40, card: 1 }, c: { top: 80, card: 1 }
            });
            const layer = crews.createWireLayer({ container: page.container, mode: 'lane' });
            layer.paint({ links: [link('o', 'a'), link('a', 'c')] });
            const html = svgOf(page).innerHTML;
            report({
                paths: Array.from(html.matchAll(/<path class="([^"]*)" d="([^"]*)"/g)).map(m => [m[1], m[2]]),
                dots: Array.from(html.matchAll(/<circle class="([^"]*)" r="2.6" cx="([^"]*)" cy="([^"]*)"/g))
                    .map(m => [m[1], Number(m[2]), Number(m[3])])
            });
            """,
            dom=True,
        )
        paths = dict((cls + str(i), d) for i, (cls, d) in enumerate(result["paths"]))
        trunks = [d for cls, d in result["paths"] if "dash-wire-bus" in cls]
        branches = [d for cls, d in result["paths"] if "dash-wire-bus" not in cls]
        # Row `a` is 40-60 (cy 50): `o`'s branch arrives at its middle and `a`'s
        # own trunk leaves 5 px below it, so the two are never one line.
        self.assertIn("M0 50 H24", branches)
        self.assertIn("M8 90 H24", branches)
        self.assertTrue(any(d.startswith("M24 55 H8 ") for d in trunks), trunks)
        self.assertTrue(any(d.startswith("M24 10 H0 ") for d in trunks), trunks)
        # The end dots sit on the rows themselves (x 24), not on the card edge.
        self.assertTrue(all(x == 24 for _cls, x, _y in result["dots"]))
        self.assertTrue(paths)

    def test_a_worker_without_a_row_draws_no_wire(self):
        result = self._run_node(
            """
            const page = makePage({ o: { top: 0, card: 1 }, a: { top: 40, card: 1 } });
            const layer = crews.createWireLayer({ container: page.container, mode: 'lane' });
            layer.paint({ links: [link('o', 'a'), link('o', 'gone')] });
            const svg = svgOf(page);
            report({ groups: svg.groups.filter(g => g.tag === 'g').length });
            """,
            dom=True,
        )
        self.assertEqual(result["groups"], 1)

    def test_a_tree_repaint_that_drops_the_svg_gets_it_back(self):
        result = self._run_node(
            """
            const page = makePage({ o: { top: 0, card: 1 }, a: { top: 40, card: 1 } });
            const layer = crews.createWireLayer({ container: page.container, mode: 'lane' });
            const snapshot = { links: [link('o', 'a')] };
            layer.paint(snapshot);
            page.container.replaceContents();
            layer.paint(snapshot);
            const svg = svgOf(page);
            report({ attached: Boolean(svg), first: page.container.firstChild === svg, writes: svg.writes, created: page.doc.created });
            """,
            dom=True,
        )
        self.assertTrue(result["attached"])
        self.assertTrue(result["first"])
        # Re-attached and redrawn, without minting a second element.
        self.assertEqual(result["writes"], 2)
        self.assertEqual(result["created"], 1)

    def test_a_reattached_svg_never_brings_back_wires_that_are_gone(self):
        result = self._run_node(
            """
            const page = makePage({ o: { top: 0, card: 1 }, a: { top: 40, card: 1 } });
            const layer = crews.createWireLayer({ container: page.container, mode: 'lane' });
            layer.paint({ links: [link('o', 'a')] });
            page.container.replaceContents();
            layer.paint({ links: [] });
            const empty = svgOf(page).innerHTML;
            layer.paint({ links: [link('o', 'a')] });
            page.container.rows = {};
            page.container.replaceContents();
            layer.paint({ links: [link('o', 'a')] });
            report({ empty, rowless: svgOf(page).innerHTML });
            """,
            dom=True,
        )
        self.assertEqual(result["empty"], "")
        self.assertEqual(result["rowless"], "")

    def test_a_dropped_round_reordering_the_reading_rewrites_nothing(self):
        result = self._run_node(
            """
            const page = makePage({ o: { top: 0, card: 1 }, a: { top: 40, card: 1 }, b: { top: 80, card: 1 } });
            const layer = crews.createWireLayer({ container: page.container, mode: 'lane' });
            const panes = { workspaces: [{ groups: [{ panes: [{ session_id: 'o' }, { session_id: 'a' }, { session_id: 'b' }] }] }] };
            layer.paint(Object.assign({ links: [
                link('o', 'a', { link_id: 'r1', state: 'reported', status: 'done' }),
                link('o', 'a', { link_id: 'r2', round: 2 }),
                link('o', 'b', { link_id: 'b1' })
            ] }, panes));
            const svg = svgOf(page);
            const html = svg.innerHTML;
            // The requester collects round 1, so the store drops it: a's
            // current round now trails b in the reading.
            layer.paint(Object.assign({ links: [
                link('o', 'b', { link_id: 'b1' }),
                link('o', 'a', { link_id: 'r2', round: 2 })
            ] }, panes));
            report({ writes: svg.writes, same: svg.innerHTML === html });
            """,
            dom=True,
        )
        self.assertEqual(result["writes"], 1)
        self.assertTrue(result["same"])

    def test_highlight_dims_other_crews_in_place_and_survives_a_repaint(self):
        result = self._run_node(
            """
            const page = makePage({
                o: { top: 0, card: 1 }, a: { top: 40, card: 1 },
                p: { top: 80, card: 2 }, b: { top: 120, card: 2 }
            });
            const layer = crews.createWireLayer({ container: page.container, mode: 'lane' });
            layer.paint({ links: [link('o', 'a', { state: 'reported', status: 'done' }), link('p', 'b')] });
            const svg = svgOf(page);
            layer.highlight('o');
            const dimmed = svg.groups.filter(g => g.classList.contains('is-dimmed')).map(g => `${g.tag}:${g.crew}`);
            const writes = svg.writes;
            layer.paint({ links: [link('o', 'a', { state: 'reported', status: 'done' }), link('p', 'b', { state: 'ended' })] });
            const afterRepaint = svg.groups.filter(g => g.classList.contains('is-dimmed')).map(g => `${g.tag}:${g.crew}`);
            layer.highlight('');
            report({
                dimmed, writes, afterRepaint,
                cleared: svg.groups.filter(g => g.classList.contains('is-dimmed')).length
            });
            """,
            dom=True,
        )
        self.assertEqual(result["dimmed"], ["g:p"])
        # Dimming is a class on what is drawn, never a rewrite.
        self.assertEqual(result["writes"], 1)
        self.assertEqual(result["afterRepaint"], ["g:p"])
        self.assertEqual(result["cleared"], 0)

    def test_the_awaited_glow_follows_the_orchestrators_waiting_reading(self):
        result = self._run_node(
            """
            const page = makePage({ o: { top: 0, card: 1 }, a: { top: 40, card: 1 } });
            const layer = crews.createWireLayer({ container: page.container, mode: 'lane' });
            const panes = waiting => ({ workspaces: [{ groups: [{ panes: [{ session_id: 'o', waiting }] }] }] });
            layer.paint(Object.assign({ links: [link('o', 'a', { link_id: 'x' })] }, panes('')));
            const idle = svgOf(page).innerHTML.includes('is-awaited');
            layer.paint(Object.assign({ links: [link('o', 'a', { link_id: 'x' })] }, panes('crew')));
            report({ idle, waiting: svgOf(page).innerHTML.includes('is-awaited') });
            """,
            dom=True,
        )
        self.assertFalse(result["idle"])
        self.assertTrue(result["waiting"])

    def test_pause_and_dispose(self):
        result = self._run_node(
            """
            const observed = [];
            const page = makePage({ o: { top: 0, card: 1 }, a: { top: 40, card: 1 } });
            page.doc.defaultView.ResizeObserver = function (callback) {
                this.observe = el => observed.push({ el, callback });
                this.disconnect = () => { observed.length = 0; };
            };
            const layer = crews.createWireLayer({ container: page.container, mode: 'lane' });
            const watching = observed.length === 1 && observed[0].el === page.container;
            layer.paint({ links: [link('o', 'a')] });
            const svg = svgOf(page);
            // A resize re-measures from the last reading.
            page.container.rows.a.top = 90;
            observed[0].callback();
            const moved = svg.writes;
            layer.setPaused(true);
            const pausedClass = svg.classList.contains('dash-wires-paused');
            layer.setPaused(false);
            const resumed = !svg.classList.contains('dash-wires-paused') && svg.paused === 0;
            layer.dispose();
            layer.paint({ links: [link('o', 'a')] });
            report({
                watching, moved, pausedClass, resumed,
                stopped: observed.length,
                detached: svg.parentNode === null && page.container.children.length === 0,
                unhosted: !page.container.classList.contains('has-dash-wires')
            });
            """,
            dom=True,
        )
        self.assertTrue(result["watching"])
        self.assertEqual(result["moved"], 2)
        self.assertTrue(result["pausedClass"])
        self.assertTrue(result["resumed"])
        self.assertEqual(result["stopped"], 0)
        self.assertTrue(result["detached"])
        self.assertTrue(result["unhosted"])

    def test_a_crew_of_six_draws_one_bus_coloured_per_worker(self):
        result = self._run_node(
            """
            const page = makePage({
                o: { top: 0, card: 1 }, a: { top: 40, card: 2 }, b: { top: 80, card: 3 },
                c: { top: 120, card: 4 }, d: { top: 160, card: 5 }, e: { top: 200, card: 6 },
                f: { top: 240, card: 7 }
            });
            const layer = crews.createWireLayer({ container: page.container, mode: 'lane' });
            layer.paint({ links: [
                link('o', 'a', { state: 'reported', status: 'done' }),
                link('o', 'b', { state: 'reported', status: 'blocked', collected: true }),
                link('o', 'c', { state: 'reported', status: 'failed' }),
                link('o', 'd'), link('o', 'e', { read: false }), link('o', 'f', { state: 'ended' })
            ] });
            const html = svgOf(page).innerHTML;
            report({
                groups: svgOf(page).groups.filter(g => g.tag === 'g').length,
                paths: (html.match(/<path /g) || []).length,
                classes: Array.from(html.matchAll(/<path class="([^"]*)"/g)).map(m => m[1]),
                gutter: page.container.style.getPropertyValue('--dash-wire-gutter')
            });
            """,
            dom=True,
        )
        # Seven paths: the quiet trunk and spine, then one branch per worker.
        self.assertEqual(result["groups"], 1)
        self.assertEqual(result["paths"], 7)
        self.assertEqual(result["classes"], [
            "dash-wire dash-wire-bus",
            "dash-wire is-done",
            "dash-wire is-blocked",
            "dash-wire is-failed",
            "dash-wire is-working",
            "dash-wire is-handed",
            "dash-wire is-ended",
        ])
        # One lane is all it needs.
        self.assertEqual(result["gutter"], "18px")

    def test_the_gutter_is_what_the_lanes_need_and_goes_with_the_last_crew(self):
        result = self._run_node(
            """
            const page = makePage({
                o: { top: 0, card: 1 }, a: { top: 40, card: 1 }, c: { top: 80, card: 1 }
            });
            const layer = crews.createWireLayer({ container: page.container, mode: 'lane' });
            const gutter = () => page.container.style.getPropertyValue('--dash-wire-gutter');
            layer.paint({ links: [link('o', 'a')] });
            const one = gutter();
            // `a` now has a crew of its own inside `o`'s rows: two lanes.
            layer.paint({ links: [link('o', 'a'), link('a', 'c')] });
            const two = gutter();
            layer.paint({ links: [] });
            const none = gutter();
            layer.paint({ links: [link('o', 'a')] });
            layer.dispose();
            report({ one, two, none, disposed: gutter() });
            """,
            dom=True,
        )
        self.assertEqual(result["one"], "18px")
        self.assertEqual(result["two"], "26px")
        # No crew leaves the column exactly as wide as it always was.
        self.assertEqual(result["none"], "")
        self.assertEqual(result["disposed"], "")

    def test_a_fan_layer_asks_for_no_gutter(self):
        result = self._run_node(
            """
            const page = makePage({ o: { top: 0, card: 1 }, a: { top: 40, card: 1 } });
            const layer = crews.createWireLayer({ container: page.container, mode: 'fan' });
            layer.paint({ links: [link('o', 'a')] });
            report(page.container.style.getPropertyValue('--dash-wire-gutter'));
            """,
            dom=True,
        )
        self.assertEqual(result, "")

    def test_fan_wires_run_from_the_parents_right_edge_to_the_childs_left(self):
        result = self._run_node(
            """
            const page = makePage({ o: { top: 0, card: 1 }, a: { top: 40, card: 1 } });
            const layer = crews.createWireLayer({ container: page.container, mode: 'fan' });
            layer.paint({ links: [link('o', 'a')] });
            report(svgOf(page).innerHTML);
            """,
            dom=True,
        )
        # Rows in the stub span x 24..216, so a fan wire leaves at 216 and
        # enters at 24, ignoring the card.
        self.assertIn('d="M216 10 C', result)
        self.assertIn(' 24 50"', result)


class StylesheetAndPagesTestCase(unittest.TestCase):
    WIRE_TOKENS = {"--gv-accent", "--gv-success", "--gv-warning", "--gv-danger", "--gv-dialog-muted"}

    def _wire_section(self) -> str:
        css = DASHBOARD_CSS.read_text(encoding="utf-8")
        return css[css.index("/* ── Crew wires ──"):]

    def test_wires_are_drawn_in_the_status_tokens_only(self):
        section = self._wire_section()
        self.assertEqual(set(re.findall(r"var\((--[\w-]+)\)", section)), self.WIRE_TOKENS)
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", section))

    def test_reduced_motion_holds_the_wires_and_the_waiting_mark_still(self):
        css = DASHBOARD_CSS.read_text(encoding="utf-8")
        blocks = re.findall(
            r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", css, flags=re.S
        )
        joined = "\n".join(blocks)
        # Every wire that moves holds still: the working flow and the settled
        # report's pulse alike.
        for state in ("is-working", "is-done", "is-blocked", "is-failed"):
            with self.subTest(state=state):
                self.assertIn(f".dash-wire.{state}", joined)
        self.assertRegex(joined, r"\.dash-wire\.is-failed \{ animation: none; \}")
        self.assertRegex(joined, r"\.dash-state-waiting \.dash-state-dot,[^{]*\{ animation: none; \}")

    def test_a_settled_report_is_a_solid_line_that_only_pulses(self):
        section = self._wire_section()
        for status, token in (("done", "success"), ("blocked", "warning"), ("failed", "danger")):
            with self.subTest(status=status):
                self.assertRegex(
                    section,
                    rf"\.dash-wire\.is-{status} \{{ stroke: var\(--gv-{token}\); \}}",
                )
                self.assertRegex(
                    section,
                    rf"\.dash-wire-end\.is-{status} \{{ fill: var\(--gv-{token}\); \}}",
                )
        settled = re.search(
            r"\.dash-wire\.is-done,\s*\.dash-wire\.is-blocked,\s*\.dash-wire\.is-failed \{([^}]*)\}",
            section,
        )
        self.assertIsNotNone(settled)
        # Slow, and never the flow that marks work in progress; no dashes.
        self.assertIn("animation: dash-wire-settle 3.6s", settled.group(1))
        self.assertNotIn("dash-wire-flow", settled.group(1))
        self.assertNotIn("stroke-dasharray", settled.group(1))
        # Nothing travels along a wire any more, and there is no collected look.
        self.assertNotIn("dash-wire-pulse", section)
        self.assertNotIn("is-collected", section)
        self.assertNotIn("is-tone-", section)

    def test_the_waiting_mark_is_a_dotted_accent_ring(self):
        css = DASHBOARD_CSS.read_text(encoding="utf-8")
        rule = re.search(r"\.dash-state-waiting \.dash-state-dot \{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        self.assertIn("dotted var(--gv-accent)", rule.group(1))
        self.assertIn("animation: dash-spin", rule.group(1))

    def test_both_pages_load_the_module_between_its_inputs_and_the_dialog(self):
        for template in ("terminals.html", "index.html"):
            with self.subTest(template=template):
                order = _templates_script_order(template)
                crews = order.index("agent-crews.js")
                self.assertLess(order.index("session-colour.js"), crews)
                self.assertLess(order.index("agent-identity.js"), crews)
                self.assertLess(crews, order.index("dashboard-dialog.js"))


if __name__ == "__main__":
    unittest.main()
