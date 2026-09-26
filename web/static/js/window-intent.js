/* GridVibeWindowIntent — the page half of "somebody with a page please do this".

   Two things GridVibe cannot do from outside a page, and one poll answers both:

   - **Open a window.** Creating a workspace over HTTP creates a record; it
     does not make anything appear on screen, and in native mode nothing
     outside a page can open a pywebview window.
   - **Split a pane.** The split axis never reaches the server. The page
     computes the new rectangles, and its refusals — the minimum columns and
     rows below a terminal header, the narrow-viewport rule, the pane cap — are
     measured off the live terminal. A process that cannot measure a pane
     cannot place one.
   - **Show a session tab.** Raising a window does not change which tab it
     shows. Only the workspace page holding the group can switch to it, under
     its own refusals (an unsaved editor, a copy in flight), and focus the
     pane that was named.

   So the MCP sidecar leaves an *intent* on the server and whichever GridVibe
   page can act on it picks it up.

   Two halves, the same split every other module here uses:

   - `policy` is pure. Which pending intent is worth acting on, what a claim
     attempt means, and what outcome to report. No DOM, no timers, no fetch.
   - `create(runtime)` is the adapter. Every request, timer and page-visibility
     read goes through the injected runtime, which is what lets Node execute
     the behaviour instead of tests asserting source text.

   Four rules the poll exists to keep:

   - **Claim before acting.** Two open pages see the same pending intent. The
     claim endpoint succeeds for exactly one of them, so the user gets one
     window (or one new pane) rather than two. A page that loses the claim does
     nothing at all.
   - **Only the page that can act, claims.** A split intent names a pane, and
     only the window holding that pane may take it. An activation names a
     group, and only the workspace page holding that group may take it. The
     launcher holds neither and therefore never claims either.
   - **Report once, honestly.** `opened`/`blocked` for a window, `split`/
     `refused` for a split, `activated`/`blocked` for a tab — the page never
     retries, never reports an outcome it did not observe, never silently
     splits the other way when the asked-for axis will not fit, and never
     discards unsaved work to switch a tab a tool asked for.
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
    const SPLIT = 'split';
    const REFUSED = 'refused';
    const ACTIVATED = 'activated';

    const WINDOW_KIND = 'window';
    const SPLIT_KIND = 'split';
    const ACTIVATE_KIND = 'activate';

    /* What a stacked or side-by-side split is called in a sentence, so a
       refusal can name the axis that *would* have worked in the words the
       button uses rather than in the wire's. */
    const AXIS_WORDS = {
        vertical: 'side-by-side',
        horizontal: 'stacked'
    };

    const policy = {
        /* The kind of thing an intent asks for. Defaults to a window, because
           that is the only kind that existed before splits and an older
           server's records carry no kind at all. */
        kind(intent) {
            return String(intent?.kind || WINDOW_KIND);
        },

        /* The intents this page should try to claim, in the order the server
           listed them.

           A window intent with no workspace is not actionable. A *split*
           intent is only actionable on the page holding its pane: the launcher
           holds none, and a second workspace window holding a different group
           holds not this one. `owns` is the page's own answer to that, and a
           page with no way to split at all passes none. */
        actionable(intents, owns = null, holdsGroup = null) {
            if (!Array.isArray(intents)) return [];
            return intents.filter(intent => {
                if (!intent || typeof intent !== 'object') return false;
                if (String(intent.state || 'pending') !== 'pending') return false;
                if (!String(intent.intent_id || '').trim()) return false;
                if (policy.kind(intent) === SPLIT_KIND) {
                    const sessionId = String(intent.session_id || '').trim();
                    return Boolean(sessionId && owns && owns(sessionId));
                }
                if (policy.kind(intent) === ACTIVATE_KIND) {
                    /* Only the workspace page that holds the group. A page
                       whose group list has not loaded yet does not hold it
                       yet, and claims it on a later tick instead. */
                    const workspaceId = String(intent.workspace_id || '').trim();
                    const groupId = String(intent.group_id || '').trim();
                    return Boolean(
                        workspaceId && groupId && holdsGroup
                        && holdsGroup(workspaceId, groupId)
                    );
                }
                return Boolean(String(intent.workspace_id || '').trim());
            });
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
        },

        /* Why an axis was refused, in GridVibe's own words, plus whether the
           other axis would have worked.

           Naming the other axis is the whole point: an agent told only
           "refused" calls the same thing again, and an agent told "stacked
           would work" can offer that instead. Saying it is not doing it — the
           page never splits on an axis nobody asked for. */
        splitRefusal(axis, candidates, reason) {
            const other = axis === 'vertical' ? 'horizontal' : 'vertical';
            const available = Array.isArray(candidates) ? candidates : [];
            const sentence = String(reason || 'This pane cannot be split.').trim();
            const tail = available.includes(other)
                ? `A ${AXIS_WORDS[other]} split would work on this pane.`
                : 'Neither axis would work on this pane.';
            return `${sentence.replace(/\.$/, '')}. ${tail}`;
        },

        /* What a tab switch reports back, from what the page says it now
           shows rather than what it was asked to show. Activated only when
           the group asked for is the one on screen and — when a pane was
           named — that pane is in it. Whether the pane also took keyboard
           focus is reported as the page read it back, never assumed: an
           explorer or browser pane is shown but cannot hold focus. */
        activation(requested, shown) {
            const groupId = String(requested?.group_id || '');
            const sessionId = String(requested?.session_id || '');
            const activeGroupId = String(shown?.activeGroupId || '');
            const paneVisible = Boolean(sessionId && shown?.paneVisible);
            const focused = Boolean(paneVisible && shown?.focused);
            const onTab = Boolean(groupId && activeGroupId === groupId);
            const ok = Boolean(shown?.ok) && onTab && (!sessionId || paneVisible);
            let detail = '';
            if (!ok) {
                detail = String(shown?.error || '').trim()
                    || (onTab
                        ? 'The session is showing, but that pane is not in it.'
                        : 'This window did not switch to that session.');
            }
            return {
                outcome: ok ? ACTIVATED : BLOCKED,
                detail,
                result: {
                    active_group_id: activeGroupId,
                    session_id: sessionId,
                    pane_visible: paneVisible,
                    focused
                }
            };
        },

        /* The pane a settled split reports back, as a field list rather than
           whatever the session payload happened to carry. */
        splitResult(result) {
            const session = result?.session || {};
            return {
                session_id: String(session.session_id || ''),
                group_id: String(session.group_id || ''),
                title: String(session.title || ''),
                startup_mode: String(session.startup_mode || ''),
                agent_selection: String(session.agent_selection || ''),
                index: Number.isInteger(result?.index) ? result.index : null
            };
        }
    };

    function create(runtime) {
        const {
            listIntents,
            claimIntent,
            reportResult,
            openWorkspaceWindow,
            /* The page's split half, absent on the launcher — which is exactly
               what stops the launcher from claiming a split it cannot do. */
            splitBridge = null,
            /* The page's tab-switch half, present only on a workspace page. */
            focusBridge = null,
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

            const kind = policy.kind(intent);
            if (kind === SPLIT_KIND) return deliverSplit(intentId, intent);
            if (kind === ACTIVATE_KIND) return deliverActivation(intentId, intent);
            return deliverWindow(intentId, intent);
        }

        async function deliverActivation(intentId, intent) {
            let shown = null;
            try {
                shown = await focusBridge.activate(
                    String(intent.group_id || ''),
                    String(intent.session_id || '')
                );
            } catch (error) {
                onError(error);
                shown = {
                    ok: false,
                    activeGroupId: focusBridge?.activeGroupId?.() || '',
                    error: `The tab switch failed in this window: ${error.message}`
                };
            }
            const settled = policy.activation(intent, shown);
            try {
                await reportResult(intentId, settled.outcome, settled.detail, settled.result);
            } catch (error) {
                onError(error);
            }
            return settled.outcome === ACTIVATED;
        }

        async function deliverWindow(intentId, intent) {
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

        async function deliverSplit(intentId, intent) {
            const sessionId = String(intent.session_id || '');
            const axis = String(intent.axis || '');
            let outcome = REFUSED;
            let detail = 'This window could not split that pane.';
            let result = null;
            try {
                /* Asked before the split rather than inferred after it: a pane
                   too small to halve is a refusal with a reason, not a failed
                   request. The candidates are what the header button reads to
                   decide whether its own arrow is enabled. */
                const candidates = splitBridge.candidates(sessionId);
                if (!Array.isArray(candidates) || !candidates.includes(axis)) {
                    detail = policy.splitRefusal(
                        axis,
                        candidates,
                        splitBridge.disabledReason(axis, sessionId)
                    );
                } else {
                    const performed = await splitBridge.perform(
                        sessionId,
                        axis,
                        intent.split_request || null
                    );
                    if (performed?.ok) {
                        outcome = SPLIT;
                        detail = '';
                        result = policy.splitResult(performed);
                    } else {
                        detail = String(performed?.error || detail);
                    }
                }
            } catch (error) {
                onError(error);
                detail = `The split failed in this window: ${error.message}`;
            }
            try {
                await reportResult(intentId, outcome, detail, result);
            } catch (error) {
                onError(error);
            }
            return outcome === SPLIT;
        }

        async function tick() {
            /* A hidden page polls nothing, and a still-running pass is never
               overlapped: opening a window or splitting a pane can take a
               second or more. */
            if (running || !isVisible()) return;
            running = true;
            try {
                const intents = policy.actionable(
                    await listIntents(),
                    splitBridge ? sessionId => splitBridge.owns(sessionId) : null,
                    focusBridge
                        ? (workspaceId, groupId) => focusBridge.holds(workspaceId, groupId)
                        : null
                );
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

    async function report(intentId, outcome, detail, result) {
        await fetch(
            `/api/windows/intents/${encodeURIComponent(intentId)}/result`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(
                    result ? { outcome, detail, result } : { outcome, detail }
                )
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
            /* Present on the workspace page and absent on the launcher, which
               is the whole ownership rule: a page with no panes never claims a
               split intent. */
            splitBridge: host.GridVibeSplitBridge || null,
            /* Same rule: the launcher holds no tabs, so it never claims one. */
            focusBridge: host.GridVibeFocusBridge || null,
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

    return {
        POLL_INTERVAL_MS,
        OPENED,
        BLOCKED,
        SPLIT,
        REFUSED,
        ACTIVATED,
        WINDOW_KIND,
        SPLIT_KIND,
        ACTIVATE_KIND,
        policy,
        create,
        bootstrap
    };
}));
