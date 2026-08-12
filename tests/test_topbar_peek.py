"""Behavioral coverage for the hidden top bar's hover reveal.

`topbar-peek.js` takes its elements and timers from an injected runtime, so
both halves run in Node against the real module: the tests drive the same
pointer, focus, and menu events the page dispatches and read back the state and
the `{hidden, peeking}` reports terminals.js maps onto its two body classes,
rather than asserting source text.

The contracts pinned here are the ones the feature exists for: a hidden bar
comes back on demand, an open Sessions…/Workspace… menu can never be closed out
from under the pointer, fullscreen hides the bar without touching the stored
`topbar_visible`, and a reveal is an overlay — it never reports the flow
change that costs every terminal a refit.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
TOPBAR_PEEK_JS = STATIC_JS / "topbar-peek.js"

NODE = shutil.which("node")

# Hand-stubbed elements and a controllable clock. The adapter only ever
# registers listeners, asks the runtime for elements and timers, and focuses one
# control, so this is the whole DOM surface it can reach.
HARNESS_STUBS = """
function stubElement(id) {
    const listeners = new Map();
    const element = {
        id,
        focusCount: 0,
        children: new Set(),
        addEventListener: (name, handler) => {
            if (!listeners.has(name)) { listeners.set(name, []); }
            listeners.get(name).push(handler);
        },
        listens: name => listeners.has(name),
        fire: (name, event) => {
            (listeners.get(name) || []).forEach(handler => handler(event || {}));
        },
        focus: () => { element.focusCount += 1; }
    };
    element.contains = node => node === element || element.children.has(node);
    return element;
}

function fakeClock() {
    const pending = new Map();
    let sequence = 0;
    return {
        pending,
        setTimeout: (fn, ms) => {
            sequence += 1;
            pending.set(sequence, { fn, ms });
            return sequence;
        },
        clearTimeout: handle => { pending.delete(handle); },
        fire: () => {
            const due = [...pending.values()];
            pending.clear();
            due.forEach(entry => entry.fn());
        }
    };
}

function harness(peek) {
    const elements = new Map();
    [peek.ZONE_ID, peek.HANDLE_ID, peek.BAR_ID, peek.BAR_FOCUS_ID].forEach(
        id => elements.set(id, stubElement(id))
    );
    const bar = elements.get(peek.BAR_ID);
    bar.children.add(elements.get(peek.BAR_FOCUS_ID));

    const clock = fakeClock();
    const changes = [];
    // Exactly what terminals.js does with a report, so the class the layout
    // keys on is what the assertions read.
    const classes = new Set();
    const controller = peek.create({
        getElement: id => elements.get(id) || null,
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout,
        onChange: change => {
            changes.push(change);
            if (change.hidden) { classes.add('topbar-hidden'); }
            else { classes.delete('topbar-hidden'); }
            if (change.peeking) { classes.add('topbar-peek'); }
            else { classes.delete('topbar-peek'); }
        }
    });
    controller.attach();

    const zone = elements.get(peek.ZONE_ID);
    return {
        controller,
        clock,
        changes,
        classes,
        el: id => elements.get(id),
        bodyClasses: () => [...classes].sort(),
        refits: () => changes.filter(change => change.hiddenChanged).length,
        // The pointer arriving at the top edge: the strip is entered, and the
        // revealed bar then covers it, so the browser hands the pointer on.
        hoverEdge: () => {
            zone.fire('pointerenter');
            zone.fire('pointerleave');
            bar.fire('pointerenter');
        },
        leaveBar: () => bar.fire('pointerleave')
    };
}
"""


@unittest.skipUnless(NODE, "Node.js is required for top bar peek tests")
class TopbarPeekNodeTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        script = HARNESS_STUBS + "\nconst peek = require(process.argv[2]);\n" + body
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(TOPBAR_PEEK_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


class TopbarPeekPolicyTestCase(TopbarPeekNodeTestCase):
    """The pure half — no DOM, no timers, no globals."""

    def test_either_input_alone_takes_the_bar_out_of_the_flow(self):
        result = self._run_node(
            """
            const { isHidden } = peek.policy;
            process.stdout.write(JSON.stringify({
                shown: isHidden({ collapsed: false, fullscreen: false }),
                chevron: isHidden({ collapsed: true, fullscreen: false }),
                fullscreen: isHidden({ collapsed: false, fullscreen: true }),
                both: isHidden({ collapsed: true, fullscreen: true })
            }));
            """
        )
        self.assertEqual(
            result,
            {"shown": False, "chevron": True, "fullscreen": True, "both": True},
        )

    def test_pointer_focus_and_an_open_menu_each_hold_the_reveal(self):
        result = self._run_node(
            """
            const { isRetained } = peek.policy;
            process.stdout.write(JSON.stringify({
                idle: isRetained({}),
                pointer: isRetained({ pointerInside: true }),
                focus: isRetained({ focusInside: true }),
                menu: isRetained({ menuOpen: true })
            }));
            """
        )
        self.assertEqual(
            result,
            {"idle": False, "pointer": True, "focus": True, "menu": True},
        )


class TopbarPeekRevealTestCase(TopbarPeekNodeTestCase):
    def test_a_hidden_bar_is_revealed_by_the_top_edge_and_hides_again_on_leaving(self):
        result = self._run_node(
            """
            const page = harness(peek);
            page.controller.setCollapsed(true);
            const hidden = page.bodyClasses();
            page.hoverEdge();
            const revealed = page.bodyClasses();
            page.leaveBar();
            const stillUpDuringGrace = page.bodyClasses();
            page.clock.fire();
            process.stdout.write(JSON.stringify({
                hidden,
                revealed,
                stillUpDuringGrace,
                afterGrace: page.bodyClasses()
            }));
            """
        )
        self.assertEqual(result["hidden"], ["topbar-hidden"])
        self.assertEqual(result["revealed"], ["topbar-hidden", "topbar-peek"])
        self.assertEqual(result["stillUpDuringGrace"], ["topbar-hidden", "topbar-peek"])
        self.assertEqual(result["afterGrace"], ["topbar-hidden"])

    def test_a_pointer_that_comes_straight_back_cancels_the_pending_close(self):
        result = self._run_node(
            """
            const page = harness(peek);
            page.controller.setCollapsed(true);
            page.hoverEdge();
            page.leaveBar();
            page.el(peek.BAR_ID).fire('pointerenter');
            page.clock.fire();
            process.stdout.write(JSON.stringify({ classes: page.bodyClasses() }));
            """
        )
        self.assertEqual(result["classes"], ["topbar-hidden", "topbar-peek"])

    def test_a_reveal_is_an_overlay_and_never_reports_a_flow_change(self):
        """The refit only rides on hiddenChanged, so a trip to the top edge
        must not resize a single terminal."""
        result = self._run_node(
            """
            const page = harness(peek);
            page.controller.setCollapsed(true);
            const afterHide = page.refits();
            page.hoverEdge();
            page.leaveBar();
            page.clock.fire();
            page.hoverEdge();
            process.stdout.write(JSON.stringify({
                afterHide,
                afterThreeReveals: page.refits()
            }));
            """
        )
        self.assertEqual(result["afterHide"], 1)
        self.assertEqual(result["afterThreeReveals"], 1)

    def test_a_bar_that_is_showing_cannot_be_peeked(self):
        result = self._run_node(
            """
            const page = harness(peek);
            page.hoverEdge();
            process.stdout.write(JSON.stringify({
                classes: page.bodyClasses(),
                peeking: page.controller.state().peeking
            }));
            """
        )
        self.assertEqual(result["classes"], [])
        self.assertFalse(result["peeking"])

    def test_grazing_the_top_edge_without_reaching_the_bar_still_closes(self):
        """The bar never saw the pointer, so it can never report one leaving —
        the strip has to be what releases the reveal."""
        result = self._run_node(
            """
            const page = harness(peek);
            page.controller.setCollapsed(true);
            page.el(peek.ZONE_ID).fire('pointerenter');
            const revealed = page.bodyClasses();
            page.el(peek.ZONE_ID).fire('pointerleave');
            page.clock.fire();
            process.stdout.write(JSON.stringify({ revealed, after: page.bodyClasses() }));
            """
        )
        self.assertEqual(result["revealed"], ["topbar-hidden", "topbar-peek"])
        self.assertEqual(result["after"], ["topbar-hidden"])


class TopbarPeekRetentionTestCase(TopbarPeekNodeTestCase):
    def test_an_open_menu_holds_the_bar_open_after_the_pointer_leaves(self):
        """The whole point of the feature: Save Session lives in that menu, and
        a menu that vanished because the pointer wandered a few pixels off the
        bar would be the same bug in a new place."""
        result = self._run_node(
            """
            const page = harness(peek);
            page.controller.setCollapsed(true);
            page.hoverEdge();
            page.controller.setMenuOpen(true);
            page.leaveBar();
            page.clock.fire();
            const withMenuOpen = page.bodyClasses();
            page.controller.setMenuOpen(false);
            page.clock.fire();
            process.stdout.write(JSON.stringify({
                withMenuOpen,
                afterMenuClosed: page.bodyClasses()
            }));
            """
        )
        self.assertEqual(result["withMenuOpen"], ["topbar-hidden", "topbar-peek"])
        self.assertEqual(result["afterMenuClosed"], ["topbar-hidden"])

    def test_keyboard_focus_inside_the_bar_holds_it_open(self):
        result = self._run_node(
            """
            const page = harness(peek);
            page.controller.setCollapsed(true);
            page.el(peek.HANDLE_ID).fire('click');
            const revealed = page.bodyClasses();
            const focused = page.el(peek.BAR_FOCUS_ID).focusCount;
            page.el(peek.BAR_ID).fire('focusin');
            page.clock.fire();
            const held = page.bodyClasses();
            page.el(peek.BAR_ID).fire('focusout', { relatedTarget: null });
            page.clock.fire();
            process.stdout.write(JSON.stringify({
                revealed,
                focused,
                held,
                afterFocusLeft: page.bodyClasses()
            }));
            """
        )
        self.assertEqual(result["revealed"], ["topbar-hidden", "topbar-peek"])
        self.assertEqual(result["focused"], 1)
        self.assertEqual(result["held"], ["topbar-hidden", "topbar-peek"])
        self.assertEqual(result["afterFocusLeft"], ["topbar-hidden"])

    def test_focus_moving_into_an_open_menu_panel_is_not_leaving_the_bar(self):
        result = self._run_node(
            """
            const page = harness(peek);
            page.controller.setCollapsed(true);
            page.hoverEdge();
            page.el(peek.BAR_ID).fire('focusin');
            page.leaveBar();
            page.el(peek.BAR_ID).fire('focusout', {
                relatedTarget: page.el(peek.BAR_FOCUS_ID)
            });
            page.clock.fire();
            process.stdout.write(JSON.stringify({ classes: page.bodyClasses() }));
            """
        )
        self.assertEqual(result["classes"], ["topbar-hidden", "topbar-peek"])

    def test_escape_closes_the_reveal_outright(self):
        result = self._run_node(
            """
            const page = harness(peek);
            page.controller.setCollapsed(true);
            page.hoverEdge();
            page.controller.dismiss();
            process.stdout.write(JSON.stringify({
                classes: page.bodyClasses(),
                retained: page.controller.state().retained
            }));
            """
        )
        self.assertEqual(result["classes"], ["topbar-hidden"])
        self.assertFalse(result["retained"])

    def test_closing_drops_pointer_retention_so_the_next_reveal_can_close(self):
        """The bar disappears out from under the pointer, so its pointerleave
        may never arrive; a retained flag left behind would wedge the reveal
        after it."""
        result = self._run_node(
            """
            const page = harness(peek);
            page.controller.setCollapsed(true);
            page.hoverEdge();
            page.controller.dismiss();
            const afterDismiss = page.controller.state();
            page.hoverEdge();
            page.leaveBar();
            page.clock.fire();
            process.stdout.write(JSON.stringify({
                pointerInside: afterDismiss.pointerInside,
                classes: page.bodyClasses()
            }));
            """
        )
        self.assertFalse(result["pointerInside"])
        self.assertEqual(result["classes"], ["topbar-hidden"])


class TopbarPeekFullscreenTestCase(TopbarPeekNodeTestCase):
    def test_fullscreen_hides_the_bar_without_touching_the_stored_choice(self):
        result = self._run_node(
            """
            const page = harness(peek);
            page.controller.setFullscreen(true);
            const inFullscreen = page.controller.state();
            page.controller.setFullscreen(false);
            process.stdout.write(JSON.stringify({
                hidden: inFullscreen.hidden,
                collapsed: inFullscreen.collapsed,
                classes: page.bodyClasses()
            }));
            """
        )
        self.assertTrue(result["hidden"])
        self.assertFalse(result["collapsed"])
        self.assertEqual(result["classes"], [])

    def test_a_bar_hidden_by_fullscreen_is_revealed_by_the_same_top_edge(self):
        result = self._run_node(
            """
            const page = harness(peek);
            page.controller.setFullscreen(true);
            page.hoverEdge();
            process.stdout.write(JSON.stringify({ classes: page.bodyClasses() }));
            """
        )
        self.assertEqual(result["classes"], ["topbar-hidden", "topbar-peek"])

    def test_leaving_fullscreen_keeps_a_bar_the_chevron_had_hidden(self):
        result = self._run_node(
            """
            const page = harness(peek);
            page.controller.setCollapsed(true);
            page.controller.setFullscreen(true);
            const both = page.bodyClasses();
            page.controller.setFullscreen(false);
            process.stdout.write(JSON.stringify({
                both,
                after: page.bodyClasses(),
                refits: page.refits()
            }));
            """
        )
        self.assertEqual(result["both"], ["topbar-hidden"])
        self.assertEqual(result["after"], ["topbar-hidden"])
        # One flow change in, none out: the bar was never in the flow.
        self.assertEqual(result["refits"], 1)

    def test_a_bar_returning_to_the_flow_drops_the_reveal_immediately(self):
        result = self._run_node(
            """
            const page = harness(peek);
            page.controller.setFullscreen(true);
            page.hoverEdge();
            page.controller.setFullscreen(false);
            process.stdout.write(JSON.stringify({
                classes: page.bodyClasses(),
                state: page.controller.state()
            }));
            """
        )
        self.assertEqual(result["classes"], [])
        self.assertFalse(result["state"]["peeking"])
        self.assertFalse(result["state"]["pointerInside"])


if __name__ == "__main__":
    unittest.main()
