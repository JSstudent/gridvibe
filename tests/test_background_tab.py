"""A session tab the window holds without showing it, read and written as data.

`background-tab.js` is what a split and a divider resize from behind share:
the reading (queue settled first, best effort), the measuring, the hold a load
of the tab waits on, and the write (drop the cache, write, take the record).
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
        discard: groupId => log.push(`discard:${groupId}`),
        saveLayout: async args => {
            log.push('saveLayout');
            saves.push(args);
            if (options.saveThrows) throw new Error('connection lost');
            return options.saveResult === undefined ? { ok: true, revision: 8 } : options.saveResult;
        },
        adopt: (group, saved) => { log.push('adopt'); adopted.push({ group, saved }); },
        onError: error => log.push(`error:${error.message}`)
    };
    return { tab: backgroundTab.create(page), log, saves, adopted };
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
        const first = tab.hold('g-2');
        const second = tab.hold('g-2');
        const heldByTwo = !(await freeSoon(tab, 'g-2'));
        const otherFree = await freeSoon(tab, 'g-3');
        first();
        const heldByOne = !(await freeSoon(tab, 'g-2'));
        second();
        const free = await freeSoon(tab, 'g-2');
        // A release called twice frees nothing it does not hold.
        second();
        out.hold = { heldByTwo, otherFree, heldByOne, free, stillFree: await freeSoon(tab, 'g-2') };
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

    def test_a_refused_write_takes_the_record_without_an_arrangement(self):
        refused = self.out["writeRefused"]

        self.assertEqual(refused["saved"], {"ok": False, "error": "stale"})
        self.assertIsNone(refused["adopted"]["saved"])
        self.assertEqual(self.out["writeEmpty"], {"ok": False})


if __name__ == "__main__":
    unittest.main()
