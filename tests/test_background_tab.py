"""A session tab the window holds without showing it, read and written as data.

`background-tab.js` is what a split and a divider resize from behind share:
the reading (queue settled first, best effort), the measuring, the hold a load
of the tab waits on, and the writes: the split's drops the cache, then writes;
the resize's writes geometry alone and keeps the cached view, handing it the
arrangement that landed or marking it stale when the outcome is unknown.
Executed in Node against a page that records what it is asked to do.
"""

import json
import unittest

from tests.test_background_split import BACKGROUND_TAB_JS, NODE, _run_node_file

TAB_HARNESS = r"""
const backgroundTab = require(TAB_PATH);

function fakePage(options = {}) {
    const log = [];
    const saves = [];
    const adopted = [];
    /* The page's cached views: one per tab, holding a disposable xterm. */
    const views = new Map([['g-2', {
        xterm: { disposed: false, dispose() { this.disposed = true; } },
        columnWeights: [1, 1],
        stale: false
    }]]);
    const page = {
        settle: async groupId => {
            log.push(`settle:${groupId}`);
            if (options.settleThrows) throw new Error('queue failed');
        },
        readModel: async groupId => {
            log.push(`readModel:${groupId}`);
            if (options.readThrows) throw new Error('list failed');
            return options.model === undefined ? { groupId, ids: ['pane-a'] } : options.model;
        },
        measure: model => { log.push('measure'); return { narrow: false, model }; },
        discard: groupId => {
            log.push(`discard:${groupId}`);
            /* The page's drop: the cached view's panes are disposed. */
            const cached = views.get(groupId);
            if (cached) {
                cached.xterm.dispose();
                views.delete(groupId);
            }
        },
        updateGeometry: (groupId, layout) => {
            log.push(`updateGeometry:${groupId}`);
            const cached = views.get(groupId);
            if (cached) cached.columnWeights = layout.columnWeights.slice();
        },
        markGeometryStale: groupId => {
            log.push(`markGeometryStale:${groupId}`);
            const cached = views.get(groupId);
            if (cached) cached.stale = true;
        },
        saveLayout: async args => {
            log.push('saveLayout');
            saves.push(args);
            if (options.saveThrows) throw new Error('connection lost');
            if (options.saveHangs) return new Promise(() => {});
            if (options.saveLate) {
                return new Promise(resolve => setTimeout(() => {
                    log.push('late-answer');
                    resolve({ ok: true, revision: 8 });
                }, options.saveLate));
            }
            return options.saveResult === undefined ? { ok: true, revision: 8 } : options.saveResult;
        },
        adopt: (group, saved) => { log.push('adopt'); adopted.push({ group, saved }); },
        onError: error => log.push(`error:${error.message}`),
        writeTimeoutMs: options.writeTimeoutMs
    };
    const view = groupId => {
        const cached = views.get(groupId);
        return cached
            ? { disposed: cached.xterm.disposed, columnWeights: cached.columnWeights, stale: cached.stale }
            : null;
    };
    return { tab: backgroundTab.create(page), log, saves, adopted, view };
}

async function freeSoon(tab, groupId) {
    return Promise.race([
        tab.settled(groupId).then(() => true),
        new Promise(resolve => setTimeout(() => resolve(false), 30))
    ]);
}

const LAYOUT = {
    ids: ['pane-a', 'pane-b'],
    rects: [{ x: 1, y: 1, w: 8, h: 8 }, { x: 9, y: 1, w: 8, h: 8 }],
    columnWeights: [1.5, 0.5], rowWeights: [1], baseCount: 2
};

const out = {};

(async () => {
    // ── the hold ──
    {
        const { tab } = fakePage();
        out.idle = await freeSoon(tab, 'g-2');
        const heldBefore = tab.held('g-2');
        const countBefore = tab.holdCount('g-2');
        const first = tab.hold('g-2');
        const second = tab.hold('g-2');
        const heldByTwo = !(await freeSoon(tab, 'g-2'));
        const heldNow = tab.held('g-2') && !tab.held('g-3');
        const otherFree = await freeSoon(tab, 'g-3');
        first();
        const heldByOne = !(await freeSoon(tab, 'g-2'));
        second();
        const free = await freeSoon(tab, 'g-2');
        // A release called twice frees nothing it does not hold.
        second();
        out.hold = {
            heldByTwo, otherFree, heldByOne, free, stillFree: await freeSoon(tab, 'g-2'),
            heldBefore, heldNow, heldAfter: tab.held('g-2'),
            // Counted per tab, and never taken back by a release.
            counts: [countBefore, tab.holdCount('g-2'), tab.holdCount('g-3')]
        };
    }
    {
        // A load waiting when a second edit starts waits for that one too.
        const { tab } = fakePage();
        const events = [];
        const first = tab.hold('g-2');
        const waiting = tab.settled('g-2').then(() => events.push('load'));
        const second = tab.hold('g-2');
        first();
        await new Promise(resolve => setTimeout(resolve, 5));
        events.push('second-released');
        second();
        await waiting;
        out.chained = events;
    }

    // ── the reading ──
    {
        const t = fakePage();
        const model = await t.tab.read('g-2');
        out.read = { model, log: t.log };
    }
    {
        const t = fakePage({ settleThrows: true });
        const model = await t.tab.read('g-2');
        out.readUnsettled = { model, log: t.log };
    }
    {
        const t = fakePage({ readThrows: true });
        let thrown = '';
        try { await t.tab.read('g-2'); } catch (error) { thrown = error.message; }
        out.readThrew = thrown;
    }
    {
        const t = fakePage({ model: null });
        out.readGone = await t.tab.read('g-2');
    }

    // ── the write ──
    {
        const t = fakePage();
        const saved = await t.tab.write('g-2', 7, LAYOUT);
        t.tab.adopt({ group_id: 'g-2' }, LAYOUT, saved);
        out.write = { saved, log: t.log, save: t.saves[0], adopted: t.adopted[0] };
    }
    {
        const t = fakePage();
        const saved = await t.tab.write('g-2', undefined, LAYOUT);
        out.writeNoRevision = { saved, log: t.log };
    }
    {
        const t = fakePage({ saveThrows: true });
        const saved = await t.tab.write('g-2', 7, LAYOUT);
        t.tab.adopt({ group_id: 'g-2' }, LAYOUT, saved);
        out.writeThrew = { saved, log: t.log, adopted: t.adopted[0] };
    }
    {
        const t = fakePage({ saveResult: { ok: false, error: 'stale' } });
        const saved = await t.tab.write('g-2', 7, LAYOUT);
        t.tab.adopt({ group_id: 'g-2' }, LAYOUT, saved);
        out.writeRefused = { saved, adopted: t.adopted[0] };
    }
    {
        const t = fakePage({ saveResult: null });
        out.writeEmpty = await t.tab.write('g-2', 7, LAYOUT);
    }

    // ── the geometry write ──
    {
        const t = fakePage();
        const saved = await t.tab.writeGeometry('g-2', 7, LAYOUT);
        out.geometry = { saved, log: t.log, save: t.saves[0], view: t.view('g-2') };
    }
    {
        // The review's reproduction: a resize the server refused, against a
        // tab whose cached view holds a live xterm.
        const t = fakePage({ saveResult: { ok: false, error: 'Presentation revision is stale' } });
        const saved = await t.tab.writeGeometry('g-2', 7, LAYOUT);
        out.geometryRefused = { saved, log: t.log, view: t.view('g-2') };
        // The split's write, refused the same way, still drops the view.
        const s = fakePage({ saveResult: { ok: false, error: 'Presentation revision is stale' } });
        await s.tab.write('g-2', 7, LAYOUT);
        out.splitRefused = { log: s.log, view: s.view('g-2') };
    }
    {
        const t = fakePage();
        const saved = await t.tab.writeGeometry('g-2', undefined, LAYOUT);
        out.geometryNoRevision = { saved, log: t.log, view: t.view('g-2') };
    }
    for (const [name, options] of [
        ['geometryThrew', { saveThrows: true }],
        ['geometryUnknown', { saveResult: { ok: false, unknown: true, error: 'no revision' } }],
        ['geometryOldRevision', { saveResult: { ok: true, revision: 7 } }],
        ['geometryNoAnswerRevision', { saveResult: { ok: true } }]
    ]) {
        const t = fakePage(options);
        const saved = await t.tab.writeGeometry('g-2', 7, LAYOUT);
        out[name] = { saved, log: t.log, view: t.view('g-2') };
    }

    // ── the deadline ──
    {
        // The resize's own sequence: hold, write, release. A write that never
        // answers must not hold the tab, or a load of it waits for ever.
        const t = fakePage({ saveHangs: true, writeTimeoutMs: 20 });
        const release = t.tab.hold('g-2');
        const loaded = t.tab.settled('g-2').then(() => true);
        let saved = null;
        try {
            saved = await t.tab.writeGeometry('g-2', 7, LAYOUT);
        } finally {
            release();
        }
        out.geometryTimedOut = {
            saved,
            log: t.log,
            view: t.view('g-2'),
            aborted: Boolean(t.saves[0].signal && t.saves[0].signal.aborted),
            loaded: await Promise.race([
                loaded, new Promise(resolve => setTimeout(() => resolve(false), 30))
            ])
        };
    }
    {
        // An answer after the deadline changes nothing: the view was already
        // marked to read the stored arrangement.
        const t = fakePage({ saveLate: 40, writeTimeoutMs: 10 });
        const saved = await t.tab.writeGeometry('g-2', 7, LAYOUT);
        await new Promise(resolve => setTimeout(resolve, 60));
        out.geometryLate = { saved, log: t.log, view: t.view('g-2') };
    }
    {
        const t = fakePage({ saveHangs: true, writeTimeoutMs: 10 });
        out.writeTimedOut = await t.tab.write('g-2', 7, LAYOUT);
    }
    console.log(JSON.stringify(out));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipIf(NODE is None, "Node.js is required for the background-tab suite")
class BackgroundTabTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _run_node_file(
            f"const TAB_PATH = {json.dumps(str(BACKGROUND_TAB_JS))};\n" + TAB_HARNESS
        )

    def test_a_tab_nobody_is_editing_loads_at_once(self):
        self.assertTrue(self.out["idle"])

    def test_a_tab_is_held_until_every_edit_of_it_is_released(self):
        hold = self.out["hold"]

        self.assertTrue(hold["heldByTwo"])
        self.assertTrue(hold["heldByOne"])
        self.assertTrue(hold["free"])
        self.assertTrue(hold["stillFree"])
        # Only the tab being edited is held.
        self.assertTrue(hold["otherFree"])
        # And the page can ask, synchronously, whether it is.
        self.assertFalse(hold["heldBefore"])
        self.assertTrue(hold["heldNow"])
        self.assertFalse(hold["heldAfter"])

    def test_every_hold_of_a_tab_is_counted_even_once_released(self):
        """A load compares the count across its read: a hold that began and
        ended while the read was out still shows."""
        self.assertEqual(self.out["hold"]["counts"], [0, 2, 0])

    def test_a_load_waits_for_an_edit_that_started_while_it_waited(self):
        self.assertEqual(self.out["chained"], ["second-released", "load"])

    def test_the_tab_is_read_after_its_queued_presentation(self):
        read = self.out["read"]

        self.assertEqual(read["log"], ["settle:g-2", "readModel:g-2"])
        self.assertEqual(read["model"]["ids"], ["pane-a"])

    def test_a_queue_that_would_not_settle_is_reported_and_not_fatal(self):
        unsettled = self.out["readUnsettled"]

        self.assertEqual(unsettled["model"]["ids"], ["pane-a"])
        self.assertIn("error:queue failed", unsettled["log"])

    def test_a_list_that_cannot_be_read_is_an_error_not_a_guess(self):
        self.assertEqual(self.out["readThrew"], "list failed")
        self.assertIsNone(self.out["readGone"])

    def test_the_cache_is_dropped_before_the_arrangement_is_written(self):
        write = self.out["write"]

        self.assertEqual(write["log"], ["discard:g-2", "saveLayout", "adopt"])
        self.assertEqual(write["save"]["groupId"], "g-2")
        self.assertEqual(write["save"]["expectedRevision"], 7)
        self.assertEqual(write["save"]["columnWeights"], [1.5, 0.5])
        self.assertEqual(write["saved"], {"ok": True, "revision": 8})
        # The record is taken with what was written and the revision it got.
        self.assertEqual(write["adopted"]["saved"]["revision"], 8)
        self.assertEqual(write["adopted"]["saved"]["ids"], ["pane-a", "pane-b"])

    def test_nothing_is_written_without_a_revision_to_write_against(self):
        case = self.out["writeNoRevision"]

        self.assertFalse(case["saved"]["ok"])
        # Dropped all the same: the tab is rebuilt from the server.
        self.assertEqual(case["log"], ["discard:g-2"])

    def test_a_write_that_threw_may_have_landed_and_says_so(self):
        case = self.out["writeThrew"]

        self.assertFalse(case["saved"]["ok"])
        self.assertTrue(case["saved"]["thrown"])
        self.assertEqual(case["saved"]["error"], "connection lost")
        self.assertIn("error:connection lost", case["log"])
        self.assertIsNone(case["adopted"]["saved"])

    def test_a_split_write_drops_the_cached_view_even_when_refused(self):
        refused = self.out["splitRefused"]

        self.assertEqual(refused["log"], ["discard:g-2", "saveLayout"])
        self.assertIsNone(refused["view"])

    def test_a_geometry_write_that_landed_keeps_the_view_and_hands_it_the_layout(self):
        case = self.out["geometry"]

        self.assertEqual(case["saved"], {"ok": True, "revision": 8})
        self.assertEqual(case["log"], ["saveLayout", "updateGeometry:g-2"])
        self.assertEqual(case["save"]["expectedRevision"], 7)
        self.assertEqual(case["save"]["columnWeights"], [1.5, 0.5])
        self.assertEqual(
            case["view"], {"disposed": False, "columnWeights": [1.5, 0.5], "stale": False}
        )

    def test_a_refused_geometry_write_leaves_the_cached_view_untouched(self):
        refused = self.out["geometryRefused"]

        self.assertEqual(refused["saved"], {"ok": False, "error": "Presentation revision is stale"})
        self.assertEqual(refused["log"], ["saveLayout"])
        # The xterm is not disposed and the view is exactly as it was.
        self.assertEqual(
            refused["view"], {"disposed": False, "columnWeights": [1, 1], "stale": False}
        )

    def test_a_geometry_write_with_no_revision_touches_nothing(self):
        case = self.out["geometryNoRevision"]

        self.assertFalse(case["saved"]["ok"])
        self.assertEqual(case["log"], [])
        self.assertEqual(
            case["view"], {"disposed": False, "columnWeights": [1, 1], "stale": False}
        )

    def test_a_geometry_write_that_may_have_landed_marks_the_view_stale(self):
        for name in (
            "geometryThrew", "geometryUnknown", "geometryOldRevision", "geometryNoAnswerRevision"
        ):
            with self.subTest(case=name):
                case = self.out[name]
                self.assertNotIn("updateGeometry:g-2", case["log"])
                self.assertIn("markGeometryStale:g-2", case["log"])
                self.assertFalse(any(step.startswith("discard") for step in case["log"]))
                self.assertEqual(
                    case["view"], {"disposed": False, "columnWeights": [1, 1], "stale": True}
                )
        self.assertTrue(self.out["geometryThrew"]["saved"]["thrown"])

    def test_a_geometry_write_that_never_answers_releases_the_tab_as_unknown(self):
        case = self.out["geometryTimedOut"]

        self.assertTrue(case["loaded"])
        self.assertTrue(case["aborted"])
        self.assertFalse(case["saved"]["ok"])
        # It may still land, so it is an unknown outcome, not a refusal.
        self.assertTrue(case["saved"]["thrown"])
        self.assertIn("did not answer", case["saved"]["error"])
        self.assertIn("markGeometryStale:g-2", case["log"])
        self.assertTrue(any(step.startswith("error:") for step in case["log"]))
        self.assertEqual(
            case["view"], {"disposed": False, "columnWeights": [1, 1], "stale": True}
        )

    def test_an_answer_after_the_deadline_is_not_taken_into_the_view(self):
        case = self.out["geometryLate"]

        self.assertTrue(case["saved"]["thrown"])
        self.assertIn("late-answer", case["log"])
        self.assertNotIn("updateGeometry:g-2", case["log"])
        self.assertTrue(case["view"]["stale"])

    def test_a_split_write_that_never_answers_is_bounded_too(self):
        case = self.out["writeTimedOut"]

        self.assertFalse(case["ok"])
        self.assertTrue(case["thrown"])

    def test_a_refused_write_takes_the_record_without_an_arrangement(self):
        refused = self.out["writeRefused"]

        self.assertEqual(refused["saved"], {"ok": False, "error": "stale"})
        self.assertIsNone(refused["adopted"]["saved"])
        self.assertEqual(self.out["writeEmpty"], {"ok": False})


if __name__ == "__main__":
    unittest.main()
