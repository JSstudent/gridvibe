/* GridVibeWindowIntent — the page half of "somebody please open this window".

   Creating a workspace over HTTP creates a record; it does not make anything
   appear on screen. In native mode nothing outside a page can open a pywebview
   window, so the MCP sidecar leaves an *intent* on the server and whichever
   GridVibe page is open picks it up.

   Two halves, the same split every other module here uses:

   - `policy` is pure. Which pending intent is worth acting on, what a claim
     attempt means, and what outcome to report. No DOM, no timers, no fetch.
   - `create(runtime)` is the adapter. Every request, timer and page-visibility
     read goes through the injected runtime, which is what lets Node execute
     the behaviour instead of tests asserting source text.

   Three rules the poll exists to keep:

   - **Claim before opening.** Two open pages see the same pending intent. The
     claim endpoint succeeds for exactly one of them, so the user gets one
     window rather than two. A page that loses the claim does nothing at all.
   - **Report once, honestly.** `opened` or `blocked` — the page never retries
     and never reports an outcome it did not observe.
   - **Suspend on a hidden page.** Established practice here: a background tab
     polls nothing. */
(function (root, factory) {
    const api = factory(root);
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeWindowIntent = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
    'use strict';

    /* Slow on purpose. This is a cost every page pays whether or not anyone
       uses the MCP, and an intent's own TTL is the thing that bounds how long
       a sidecar waits — not how fast a page notices. */
    const POLL_INTERVAL_MS = 2000;

    const OPENED = 'opened';
    const BLOCKED = 'blocked';

    const policy = {
        /* The intents this page should try to claim, in the order the server
           listed them. An entry with no workspace is not actionable, and a
           state other than `pending` has already been taken. */
        actionable(intents) {
            if (!Array.isArray(intents)) return [];
            return intents.filter(intent => (
                intent
                && typeof intent === 'object'
                && String(intent.workspace_id || '').trim()
                && String(intent.state || 'pending') === 'pending'
                && String(intent.intent_id || '').trim()
            ));
        },

        /* Whether a claim response means this page won the intent. Anything
           else — a 409 from a page that got there first, an expiry, a
           malformed body — means do nothing, which is not an error worth
           reporting to the reader. */
        claimed(response) {
            return Boolean(
                response
                && response.ok
                && String(response.state || '') === 'claimed'
            );
        },

        /* What the page reports back after asking the window to open. */
        outcome(opened) {
            return opened ? OPENED : BLOCKED;
        },

        detail(opened) {
            return opened
                ? ''
                : 'The GridVibe window could not be opened from this page.';
        }
    };

    function create(runtime) {
        const {
            listIntents,
            claimIntent,
            reportResult,
            openWorkspaceWindow,
            setInterval: schedule,
            clearInterval: unschedule,
            isVisible = () => true,
            claimant = '',
            intervalMs = POLL_INTERVAL_MS,
            onError = () => {}
        } = runtime || {};

        let timer = null;
        let running = false;
        /* An intent this page has already acted on. The poll runs again before
           the server has necessarily settled the record, and acting twice
           would open a second window. */
        const handled = new Set();

        async function deliver(intent) {
            const intentId = String(intent.intent_id || '');
            if (handled.has(intentId)) return false;
            handled.add(intentId);

            let claim = null;
            try {
                claim = await claimIntent(intentId, claimant);
            } catch (error) {
                onError(error);
                return false;
            }
            if (!policy.claimed(claim)) return false;

            let opened = false;
            try {
                opened = Boolean(await openWorkspaceWindow(
                    String(intent.workspace_id || ''),
                    { groupId: String(intent.group_id || '') }
                ));
            } catch (error) {
                onError(error);
                opened = false;
            }
            try {
                await reportResult(
                    intentId,
                    policy.outcome(opened),
                    policy.detail(opened)
                );
            } catch (error) {
                onError(error);
            }
            return opened;
        }

        async function tick() {
            /* A hidden page polls nothing, and a still-running pass is never
               overlapped: opening a window can take a second or more. */
            if (running || !isVisible()) return;
            running = true;
            try {
                const intents = policy.actionable(await listIntents());
                for (const intent of intents) {
                    await deliver(intent);
                }
            } catch (error) {
                onError(error);
            } finally {
                running = false;
            }
        }

        function start() {
            if (timer !== null) return;
            timer = schedule(tick, intervalMs);
        }

        function stop() {
            if (timer === null) return;
            unschedule(timer);
            timer = null;
        }

        return { start, stop, tick, handled };
    }

    /* ── The page's own bootstrap ──
       Here rather than in workspaces.js on purpose: that file is pinned
       poll-free by contract (workspace-list freshness is pushed, never
       polled), and this is a different poll for a different thing. The module
       that owns the intent owns its own timer.

       Native mode only, decided once per page load: in browser mode the
       sidecar hands the URL straight to the OS default browser and needs no
       page at all, so a browser tab must never pay for this. */
    async function readIntents() {
        const response = await fetch('/api/windows/intents');
        const payload = await response.json();
        return payload?.intents || [];
    }

    async function claim(intentId, claimant) {
        const response = await fetch(
            `/api/windows/intents/${encodeURIComponent(intentId)}/claim`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ claimant })
            }
        );
        const payload = await response.json().catch(() => ({}));
        return { ...payload, ok: response.ok };
    }

    async function report(intentId, outcome, detail) {
        await fetch(
            `/api/windows/intents/${encodeURIComponent(intentId)}/result`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ outcome, detail })
            }
        );
    }

    async function bootstrap(scope) {
        const host = scope || root;
        if (!host || typeof host.fetch !== 'function') return null;
        if (typeof host.openWorkspaceWindow !== 'function') return null;
        let mode = '';
        try {
            const response = await host.fetch('/api/health');
            mode = String((await response.json())?.window_mode || '');
        } catch (error) {
            return null;
        }
        if (mode !== 'native') return null;
        const poll = create({
            listIntents: readIntents,
            claimIntent: claim,
            reportResult: report,
            openWorkspaceWindow: (workspaceId, options) =>
                host.openWorkspaceWindow(workspaceId, options),
            setInterval: (handler, interval) => host.setInterval(handler, interval),
            clearInterval: handle => host.clearInterval(handle),
            isVisible: () => host.document?.visibilityState !== 'hidden',
            claimant: Math.random().toString(36).slice(2, 10),
            onError: error =>
                console.debug('[GridVibe WindowIntent]', error)
        });
        poll.start();
        return poll;
    }

    if (root && typeof root.document !== 'undefined' && typeof root.fetch === 'function') {
        root.document.addEventListener('DOMContentLoaded', () => bootstrap(root));
    }

    return { POLL_INTERVAL_MS, OPENED, BLOCKED, policy, create, bootstrap };
}));
