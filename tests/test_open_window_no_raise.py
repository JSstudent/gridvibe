"""`open_window` never brings a window forward.

An agent's `open_window` makes sure a workspace has a window and nothing more:
an open window is left exactly where it is, and a new one is created minimized
in the taskbar, so the person's tab, focus and window order stay as they were.
The focus tools (`focus_session`, `focus_pane`, `move_session show:true`) go
through the same open step and keep the raise, because bringing the window
forward is what they were asked for. So "don't raise" is a parameter of that
one step, carried end to end:

- **The intent** records `raise` (only an explicit `false` from the route
  clears it) and forwards how the window was left: `reused`, `raised`,
  `minimized`.
- **The page** passes `raise: false` to the bridge, skips the arrival pulse,
  and reports that result.
- **The launcher** leaves a reused window alone (no show, no zoom), and
  creates a new one with `minimized=True`, tracked minimized from before it
  exists so its own `minimized` event can never start the minimize cascade.
- **The tool** answers `already_open`, `raised: false`, and `minimized` with
  a note, and its description says the focus tools are for bringing forward.
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from gridvibe_mcp.identity import read_identity  # noqa: E402
from gridvibe_mcp.server import dispatch, tool_specs  # noqa: E402
from gridvibe_mcp.windows import OPENED, open_window  # noqa: E402
from tests.test_mcp_client import StubOpener, client_for  # noqa: E402
from tests.test_mcp_tools import INSIDE_PANE  # noqa: E402
from tests.test_multi_workspace import _js_function_source  # noqa: E402
from web import api, webview_launcher  # noqa: E402
from web.window_intents import window_intents  # noqa: E402

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
NODE = shutil.which("node")


def _bodies(opener):
    return [
        json.loads(request.data) if request.data else None
        for request in opener.requests
    ]


class _Events:
    def __init__(self):
        self.loaded = _Event()
        self.shown = _Event()


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _Window:
    def __init__(self):
        self.show_calls = 0
        self.on_top = False
        self.events = _Events()

    def show(self):
        self.show_calls += 1


class OpenIntentRaiseTestCase(unittest.TestCase):
    """The route and store carry `raise`, and the page's report of the window."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        window_intents.reset()
        self.addCleanup(window_intents.reset)

    def _open(self, body):
        return self.client.post("/api/windows/open", json=body).get_json()

    def test_an_intent_raises_unless_the_body_says_false(self):
        self.assertIs(self._open({"workspace_id": "ws-1"})["raise"], True)
        self.assertIs(self._open({"workspace_id": "ws-1", "raise": False})["raise"], False)
        # Only the boolean: a string is not a request to stay put.
        self.assertIs(self._open({"workspace_id": "ws-1", "raise": "false"})["raise"], True)

    def test_the_pending_list_hands_the_page_the_flag(self):
        self._open({"workspace_id": "ws-1", "raise": False})

        pending = self.client.get("/api/windows/intents").get_json()["intents"]

        self.assertEqual([item["raise"] for item in pending], [False])

    def test_the_window_result_is_forwarded_through_its_field_list(self):
        intent_id = self._open({"workspace_id": "ws-1", "raise": False})["intent_id"]
        self.client.post(f"/api/windows/intents/{intent_id}/claim", json={})

        self.client.post(
            f"/api/windows/intents/{intent_id}/result",
            json={
                "outcome": "opened",
                "result": {"reused": False, "raised": False, "minimized": True, "hwnd": 7},
            },
        )
        read = self.client.get(f"/api/windows/intents/{intent_id}").get_json()

        self.assertEqual(read["state"], "opened")
        self.assertEqual(read["result"], {"reused": False, "raised": False, "minimized": True})

    def test_a_window_report_without_a_result_stays_bare(self):
        intent_id = self._open({"workspace_id": "ws-1"})["intent_id"]
        self.client.post(f"/api/windows/intents/{intent_id}/claim", json={})
        self.client.post(f"/api/windows/intents/{intent_id}/result", json={"outcome": "opened"})

        read = self.client.get(f"/api/windows/intents/{intent_id}").get_json()

        self.assertNotIn("result", read)


class OpenWindowToolTestCase(unittest.TestCase):
    """The sidecar asks not to raise, and says how the window was left."""

    def _open(self, page_result, **kwargs):
        opener = StubOpener([
            {"window_mode": "native"},
            {"intent_id": "w-1", "state": "pending"},
            {"intent_id": "w-1", "state": "opened", **({"result": page_result} if page_result else {})},
        ])
        result = open_window(
            client_for(opener),
            "ws-1",
            sleep=lambda _seconds: None,
            monotonic=lambda: 0.0,
            **kwargs,
        )
        return result, opener

    def test_an_open_window_is_reported_already_open_and_not_raised(self):
        result, opener = self._open(
            {"reused": True, "raised": False, "minimized": False}, raise_window=False
        )

        self.assertEqual(_bodies(opener)[1], {"workspace_id": "ws-1", "raise": False})
        self.assertEqual(result["status"], OPENED)
        self.assertIs(result["already_open"], True)
        self.assertIs(result["raised"], False)
        self.assertNotIn("minimized", result)
        self.assertNotIn("note", result)

    def test_a_new_window_is_reported_minimized_with_what_that_means(self):
        result, _ = self._open(
            {"reused": False, "raised": False, "minimized": True}, raise_window=False
        )

        self.assertIs(result["already_open"], False)
        self.assertIs(result["raised"], False)
        self.assertIs(result["minimized"], True)
        self.assertIn("created minimized", result["note"])
        self.assertIn("ask the person to restore it", result["note"])

    def test_an_open_window_that_is_minimized_says_so(self):
        result, _ = self._open(
            {"reused": True, "raised": False, "minimized": True}, raise_window=False
        )

        self.assertIs(result["minimized"], True)
        self.assertIn("open but minimized", result["note"])

    def test_a_new_window_that_kept_the_keyboard_says_so(self):
        result, _ = self._open(
            {"reused": False, "raised": False, "minimized": True, "focus_moved": True},
            raise_window=False,
        )

        self.assertIs(result["focus_moved"], True)
        self.assertIs(result["minimized"], True)
        self.assertIn("kept the keyboard focus", result["note"])

    def test_a_page_that_did_not_say_how_it_left_the_window_is_not_answered_for(self):
        result, _ = self._open(None, raise_window=False)

        self.assertEqual(result["status"], OPENED)
        self.assertNotIn("raised", result)
        self.assertNotIn("already_open", result)
        self.assertIn("not known", result["note"])

    def test_the_focus_path_still_raises_and_answers_as_before(self):
        result, opener = self._open(None)

        self.assertEqual(_bodies(opener)[1], {"workspace_id": "ws-1"})
        self.assertEqual(result, {"status": OPENED, "window_mode": "native", "intent_id": "w-1"})

    def test_the_tool_asks_the_opener_not_to_raise(self):
        seen = []

        def opener_fn(client, workspace_id, group_id="", **kwargs):
            seen.append((workspace_id, group_id, kwargs))
            return {"status": OPENED}

        dispatch(
            "open_window",
            {"workspace_id": "ws-1"},
            client=client_for(StubOpener([])),
            identity=read_identity(INSIDE_PANE),
            window_opener=opener_fn,
        )

        self.assertEqual(seen, [("ws-1", "", {"raise_window": False})])

    def test_the_description_sends_bringing_forward_to_the_focus_tools(self):
        spec = next(item for item in tool_specs() if item["name"] == "open_window")

        self.assertIn("never brings a window forward", spec["description"])
        self.assertIn("focus_session", spec["description"])
        self.assertIn("focus_pane", spec["description"])


class LauncherNoRaiseTestCase(unittest.TestCase):
    """The bridge leaves an open window alone and creates a new one minimized."""

    def setUp(self):
        self.bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")

    def test_an_open_window_is_neither_shown_nor_zoomed(self):
        window = _Window()
        self.bridge._attach_workspace_window("aaaaaaaaaaaa", window, "g-1")

        with patch.object(webview_launcher, "_set_native_window_zoom") as zoom:
            result = self.bridge.open_workspace_window("aaaaaaaaaaaa", "g-2", 1.5, False)

        self.assertEqual(
            result, {"ok": True, "reused": True, "raised": False, "minimized": False}
        )
        self.assertEqual(window.show_calls, 0)
        zoom.assert_not_called()

    def test_an_open_window_reports_its_tracked_minimized_state(self):
        self.bridge._attach_workspace_window("aaaaaaaaaaaa", _Window(), "g-1")
        self.bridge._set_window_minimized("workspace:aaaaaaaaaaaa", True)

        result = self.bridge.open_workspace_window("aaaaaaaaaaaa", "", None, False)

        self.assertIs(result["minimized"], True)

    def test_only_an_explicit_false_skips_the_raise(self):
        window = _Window()
        self.bridge._attach_workspace_window("aaaaaaaaaaaa", window, "g-1")

        result = self.bridge.open_workspace_window("aaaaaaaaaaaa", "g-1", None, None)

        self.assertEqual(result, {"ok": True, "reused": True})
        self.assertEqual(window.show_calls, 1)

    def test_a_new_window_is_created_minimized_and_registered_as_minimized(self):
        window = _Window()
        fake_webview = Mock()
        tracked_at_create = []

        def create_window(*_args, **_kwargs):
            tracked_at_create.append(
                self.bridge._is_window_minimized("workspace:aaaaaaaaaaaa")
            )
            return window

        fake_webview.create_window.side_effect = create_window
        register = Mock()
        self.bridge._set_register_window(register)

        with patch.object(webview_launcher, "webview", fake_webview), patch.object(
            webview_launcher, "_foreground_window_handle", return_value=4242
        ), patch.object(webview_launcher, "_hand_foreground_back") as hand_back:
            result = self.bridge.open_workspace_window("aaaaaaaaaaaa", "g-1", None, False)
            self.assertEqual(len(window.events.shown.handlers), 1)
            window.events.shown.handlers[0]()

        self.assertEqual(
            result, {"ok": True, "reused": False, "raised": False, "minimized": True}
        )
        self.assertIs(fake_webview.create_window.call_args.kwargs["minimized"], True)
        # Minimized before the window existed, so its own event is an echo.
        self.assertEqual(tracked_at_create, [True])
        register.assert_called_once_with(window, "workspace:aaaaaaaaaaaa", minimized=True)
        self.assertTrue(self.bridge._is_window_minimized("workspace:aaaaaaaaaaaa"))
        # Once after creating, once more when WinForms reports it shown.
        self.assertEqual(
            [entry.args for entry in hand_back.call_args_list],
            [(4242, window), (4242, window)],
        )

    def test_a_new_window_that_kept_the_keyboard_reports_it(self):
        fake_webview = Mock()
        fake_webview.create_window.return_value = _Window()

        with patch.object(webview_launcher, "webview", fake_webview), patch.object(
            webview_launcher, "_foreground_window_handle", return_value=4242
        ), patch.object(webview_launcher, "_hand_foreground_back", return_value=False):
            result = self.bridge.open_workspace_window("aaaaaaaaaaaa", "", None, False)

        self.assertIs(result["focus_moved"], True)

    def test_a_raised_new_window_is_created_as_before(self):
        fake_webview = Mock()
        fake_webview.create_window.return_value = _Window()
        register = Mock()
        self.bridge._set_register_window(register)

        with patch.object(webview_launcher, "webview", fake_webview), patch.object(
            webview_launcher, "_hand_foreground_back"
        ) as hand_back:
            result = self.bridge.open_workspace_window("aaaaaaaaaaaa", "g-1")

        self.assertEqual(result, {"ok": True, "reused": False})
        self.assertNotIn("minimized", fake_webview.create_window.call_args.kwargs)
        register.assert_called_once_with(
            fake_webview.create_window.return_value, "workspace:aaaaaaaaaaaa"
        )
        hand_back.assert_not_called()

    def test_a_window_that_was_not_created_is_not_left_tracked_minimized(self):
        fake_webview = Mock()
        fake_webview.create_window.return_value = None

        with patch.object(webview_launcher, "webview", fake_webview), patch.object(
            webview_launcher, "_foreground_window_handle", return_value=None
        ), self.assertLogs(webview_launcher.logger, level="ERROR"):
            result = self.bridge.open_workspace_window("aaaaaaaaaaaa", "", None, False)

        self.assertFalse(result["ok"])
        self.assertFalse(self.bridge._is_window_minimized("workspace:aaaaaaaaaaaa"))

    def test_a_window_whose_creation_threw_is_not_left_tracked_minimized(self):
        fake_webview = Mock()
        fake_webview.create_window.side_effect = RuntimeError("no GUI")

        with patch.object(webview_launcher, "webview", fake_webview), patch.object(
            webview_launcher, "_foreground_window_handle", return_value=None
        ), self.assertLogs(webview_launcher.logger, level="ERROR"):
            result = self.bridge.open_workspace_window("aaaaaaaaaaaa", "", None, False)

        self.assertFalse(result["ok"])
        self.assertFalse(self.bridge._is_window_minimized("workspace:aaaaaaaaaaaa"))


class _User32:
    def __init__(self, foreground, live=True):
        self.foreground = foreground
        self.live = live
        self.set_calls = []

    def GetForegroundWindow(self):
        return self.foreground

    def IsWindow(self, _hwnd):
        return self.live

    def SetForegroundWindow(self, hwnd):
        self.set_calls.append(hwnd.value)
        return self.grants

    grants = 1


class HandForegroundBackTestCase(unittest.TestCase):
    """A window created minimized never keeps the keyboard it took."""

    def _hand_back(self, user32, previous=100, platform="win32"):
        with patch.object(webview_launcher.sys, "platform", platform), patch.object(
            webview_launcher, "_windows_user32", return_value=user32
        ), patch.object(webview_launcher, "_resolve_native_window_handle", return_value=200):
            return webview_launcher._hand_foreground_back(previous, object())

    def test_the_foreground_goes_back_when_the_new_window_took_it(self):
        user32 = _User32(foreground=200)

        self.assertIs(self._hand_back(user32), True)
        self.assertEqual(user32.set_calls, [100])

    def test_a_refused_hand_back_is_reported_as_failed(self):
        user32 = _User32(foreground=200)
        user32.grants = 0

        with self.assertLogs(webview_launcher.logger, level="WARNING"):
            self.assertIs(self._hand_back(user32), False)

    def test_a_window_the_person_picked_meanwhile_is_left_alone(self):
        user32 = _User32(foreground=300)

        self.assertIsNone(self._hand_back(user32))
        self.assertEqual(user32.set_calls, [])

    def test_a_previous_window_that_closed_is_not_brought_back(self):
        user32 = _User32(foreground=200, live=False)

        self.assertIsNone(self._hand_back(user32))
        self.assertEqual(user32.set_calls, [])

    def test_nothing_to_hand_back_off_windows_or_without_a_previous_window(self):
        user32 = _User32(foreground=200)

        self.assertIsNone(self._hand_back(user32, platform="linux"))
        self.assertIsNone(self._hand_back(user32, previous=None))
        self.assertEqual(user32.set_calls, [])


PAGE_HARNESS = """
const intentModule = require(MODULE_PATH);

async function deliver(intent, answer) {
    const opens = [];
    const results = [];
    const poll = intentModule.create({
        listIntents: async () => [intent],
        claimIntent: async id => ({ ok: true, state: 'claimed', intent_id: id }),
        reportResult: async (intentId, outcome, detail, result) => {
            results.push({ outcome, result: result === undefined ? 'none' : result });
        },
        openWorkspaceWindow: async (workspaceId, options) => {
            opens.push(options);
            return answer;
        },
        setInterval: () => 1,
        clearInterval: () => {},
        isVisible: () => true,
        claimant: 'page',
        onError: () => {}
    });
    await poll.tick();
    return { opens, results };
}

const windowIntent = extra => ({
    intent_id: 'w-1', kind: 'window', workspace_id: 'ws-1', group_id: '',
    state: 'pending', ...extra
});

(async () => {
    const out = {};
    out.noRaise = await deliver(
        windowIntent({ raise: false }),
        { ok: true, reused: false, raised: false, minimized: true }
    );
    out.raise = await deliver(
        windowIntent({ raise: true }),
        { ok: true, reused: true, raised: true, minimized: false }
    );
    out.legacy = await deliver(windowIntent({}), true);
    out.bareBoolean = await deliver(windowIntent({ raise: false }), true);
    out.refused = await deliver(windowIntent({ raise: false }), { ok: false });
    out.focusMoved = await deliver(
        windowIntent({ raise: false }),
        { ok: true, reused: false, raised: false, minimized: true, focus_moved: true }
    );

    /* bootstrap prefers the host's result form, so the report can say how
       the window was left. */
    const hostCalls = [];
    const reports = [];
    globalThis.fetch = async (url, init) => {
        if (url === '/api/windows/intents') {
            return { json: async () => ({ intents: [windowIntent({ raise: false })] }) };
        }
        if (url.endsWith('/claim')) {
            return { ok: true, json: async () => ({ state: 'claimed' }) };
        }
        reports.push(JSON.parse(init.body));
        return { ok: true, json: async () => ({}) };
    };
    const host = {
        fetch: async () => ({ json: async () => ({ window_mode: 'native' }) }),
        openWorkspaceWindow: async () => { hostCalls.push('boolean'); return true; },
        openWorkspaceWindowResult: async (workspaceId, options) => {
            hostCalls.push({ form: 'result', raise: options.raise });
            return { ok: true, reused: true, raised: false, minimized: false };
        },
        setInterval: () => 1,
        clearInterval: () => {},
        document: { visibilityState: 'visible' }
    };
    const poll = await intentModule.bootstrap(host);
    await poll.tick();
    out.bootstrap = { hostCalls, reports };
    process.stdout.write(JSON.stringify(out));
})().catch(error => { console.error(error); process.exit(1); });
"""


def _run_node(script):
    with TemporaryDirectory() as script_dir:
        path = Path(script_dir) / "harness.js"
        path.write_text(script, encoding="utf-8")
        completed = subprocess.run(
            [NODE, str(path)], capture_output=True, text=True, check=False
        )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


@unittest.skipUnless(NODE, "node is not installed")
class PageNoRaiseTestCase(unittest.TestCase):
    """`window-intent.js` passes the flag on and reports the bridge's answer."""

    @classmethod
    def setUpClass(cls):
        module_path = json.dumps(str(STATIC_JS / "window-intent.js"))
        cls.out = _run_node(PAGE_HARNESS.replace("MODULE_PATH", module_path))

    def test_an_intent_that_may_not_raise_says_so_to_the_host_and_reports_the_window(self):
        self.assertEqual(self.out["noRaise"]["opens"], [{"groupId": "", "raise": False}])
        self.assertEqual(
            self.out["noRaise"]["results"],
            [{"outcome": "opened", "result": {"reused": False, "raised": False, "minimized": True}}],
        )

    def test_a_raising_intent_and_an_older_record_open_as_before(self):
        for key in ("raise", "legacy"):
            with self.subTest(key):
                self.assertEqual(self.out[key]["opens"], [{"groupId": ""}])
                self.assertEqual(
                    self.out[key]["results"], [{"outcome": "opened", "result": "none"}]
                )

    def test_a_host_answering_a_bare_boolean_reports_no_window_detail(self):
        self.assertEqual(
            self.out["bareBoolean"]["results"], [{"outcome": "opened", "result": "none"}]
        )

    def test_a_refused_open_is_blocked_without_a_result(self):
        self.assertEqual(
            [item["outcome"] for item in self.out["refused"]["results"]], ["blocked"]
        )
        self.assertEqual(self.out["refused"]["results"][0]["result"], "none")

    def test_a_window_that_kept_the_keyboard_is_reported(self):
        self.assertEqual(
            self.out["focusMoved"]["results"][0]["result"],
            {"reused": False, "raised": False, "minimized": True, "focus_moved": True},
        )

    def test_bootstrap_opens_through_the_result_form_and_reports_it(self):
        bootstrap = self.out["bootstrap"]

        self.assertEqual(bootstrap["hostCalls"], [{"form": "result", "raise": False}])
        self.assertEqual(
            bootstrap["reports"],
            [{
                "outcome": "opened",
                "detail": "",
                "result": {"reused": True, "raised": False, "minimized": False},
            }],
        )


WORKSPACES_HARNESS = """
const bridgeCalls = [];
const pulses = [];
const tabs = [];
const window = { open: (...args) => { tabs.push(args); return {}; } };
function workspaceUrl(workspaceId) { return `/terminals?workspace=${workspaceId}`; }
function workspaceWindowName(workspaceId) { return `gridvibe-${workspaceId}`; }
let bridgeAnswer = null;
function normalizeWorkspaceId(value) { return String(value || 'default'); }
function normalizeNativeZoomFactor(value) { return value === undefined ? null : value; }
function requestWorkspaceArrivalPulse(workspaceId) { pulses.push(workspaceId); }
function nativeWorkspaceApi() {
    return {
        open_workspace_window: async (...args) => {
            bridgeCalls.push(args);
            return bridgeAnswer;
        }
    };
}
SOURCE
(async () => {
    const out = {};
    bridgeAnswer = { ok: true, reused: false, raised: false, minimized: true };
    out.noRaise = await openWorkspaceWindowResult('ws-1', { raise: false });
    out.noRaisePulses = pulses.length;
    bridgeAnswer = { ok: true, reused: true };
    out.raise = await openWorkspaceWindowResult('ws-1', { groupId: 'g-1' });
    out.boolean = await openWorkspaceWindow('ws-1', { groupId: 'g-1' });
    bridgeAnswer = { ok: false, error: 'no GUI' };
    out.refusedNoRaise = await openWorkspaceWindowResult('ws-1', { raise: false });
    out.tabsAfterNoRaise = tabs.length;
    out.refusedRaise = await openWorkspaceWindowResult('ws-1', {});
    out.tabsAfterRaise = tabs.length;
    out.pulses = pulses;
    out.bridgeCalls = bridgeCalls;
    process.stdout.write(JSON.stringify(out));
})().catch(error => { console.error(error); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class WorkspacesOpenResultTestCase(unittest.TestCase):
    """`workspaces.js` hands `raise` to the bridge and returns its answer."""

    @classmethod
    def setUpClass(cls):
        workspaces_js = (STATIC_JS / "workspaces.js").read_text(encoding="utf-8")
        source = "\n".join(
            _js_function_source(workspaces_js, name)
            for name in ("openWorkspaceWindow", "openWorkspaceWindowResult")
        )
        cls.out = _run_node(WORKSPACES_HARNESS.replace("SOURCE", source))

    def test_an_open_that_may_not_raise_asks_the_bridge_and_skips_the_pulse(self):
        self.assertEqual(self.out["bridgeCalls"][0], ["ws-1", "", None, False])
        self.assertEqual(self.out["noRaisePulses"], 0)
        self.assertEqual(
            self.out["noRaise"],
            {"ok": True, "reused": False, "raised": False, "minimized": True,
             "focus_moved": False},
        )

    def test_a_refused_native_open_that_may_not_raise_opens_no_tab(self):
        self.assertEqual(self.out["refusedNoRaise"], {"ok": False})
        self.assertEqual(self.out["tabsAfterNoRaise"], 0)
        # A raising open keeps its browser fallback.
        self.assertEqual(self.out["refusedRaise"], {"ok": True})
        self.assertEqual(self.out["tabsAfterRaise"], 1)

    def test_every_other_open_raises_and_pulses_as_before(self):
        self.assertEqual(self.out["bridgeCalls"][1], ["ws-1", "g-1", None, True])
        self.assertEqual(
            self.out["raise"],
            {"ok": True, "reused": True, "raised": True, "minimized": False,
             "focus_moved": False},
        )
        self.assertIs(self.out["boolean"], True)
        self.assertEqual(self.out["pulses"], ["ws-1", "ws-1", "ws-1"])


if __name__ == "__main__":
    unittest.main()
