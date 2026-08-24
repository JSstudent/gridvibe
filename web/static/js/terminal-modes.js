/* GridVibeTerminalModes — giving a pane its input back after a TUI died in it.

   Mouse reporting is owned by whatever runs in the pane, never by GridVibe: a
   TUI arms it on start (`\x1b[?1003h\x1b[?1006h`) and disarms it on the way
   out. A program that dies without unwinding — a crash, a `SIGKILL`, a dropped
   connection — never sends the teardown, and the connector keeps the same
   shell and the same xterm.js instance alive across that death. The shell that
   inherits the prompt is a plain line editor with no interest in mouse
   reports, so every pointer movement over the pane is encoded and typed at it
   (`35;43;24M35;43;19M…`) until the user notices, and Enter submits the lot.

   The cure is a client-side write. `\x1b[?1003l` and its siblings go through
   `term.write()`, which feeds the pane's own parser and sends nothing to the
   shell — so this stays inside the pane's existing behaviour and touches no
   route.

   The ordering is the whole difficulty, and it is why this module exists
   instead of two inline writes:

   - **Clear** resets the pane *and* purges the server's rolling replay buffer,
     so nothing can re-arm what the reset cleared. A plain write is enough.
   - **Reset view** resets the pane and then deliberately rejoins the session
     room to replay that buffer. The replay is the raw stream the pane already
     received, so the dead program's `\x1b[?1003h` is still inside it and gets
     re-delivered — re-arming the mode microseconds after the reset that
     cleared it. Writing the teardown before the rejoin is therefore useless;
     it has to land after the replayed bytes.

   `rejoinAndResetAfterReplay` sequences that without a guessed delay. The
   `join_session` emit carries an acknowledgement callback: the server replays
   the buffer inside the handler and the ack packet is written after it, over
   the same ordered connection, so the ack fires on a client that has already
   processed the replay. A bounded fallback covers the socket that never
   answers — the pane must come back either way.

   One deliberate consequence: Reset view now really is a reset. A *live*
   full-screen TUI in the pane loses mouse reporting until it re-arms it, the
   same as pressing Reset in any terminal emulator. That is the trade the
   control's name already promised, and the pane this feature exists to rescue
   has no program left to re-arm anything. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeTerminalModes = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* Every DECSET a TUI may have used to arm pointer reporting, disarmed.
       The encodings (1005/1006/1015) are listed beside the trackers
       (1000/1002/1003) because a program is free to set them independently,
       and a stale encoding left on its own still changes what the next
       program's reports look like. */
    const MOUSE_REPORTING_MODES = [1000, 1002, 1003, 1005, 1006, 1015];

    const MOUSE_REPORTING_RESET = MOUSE_REPORTING_MODES
        .map(mode => `\x1b[?${mode}l`)
        .join('');

    /* Only ever reached when the socket does not answer the rejoin at all.
       Long enough that a loopback round trip is never cut short, short enough
       that the pane is not left typing mouse reports while it waits. */
    const REPLAY_SETTLE_TIMEOUT_MS = 1500;

    /* A grid slot is not an identity. Reset view is asynchronous — the
       teardown lands only when the rejoin is acknowledged, or when the
       fallback fires — and a group switch in between puts a *different* pane
       in `terminals[index]`. Resolving the slot at completion time therefore
       wrote a dead TUI's teardown into whichever pane had since taken the
       slot, and left the pane that asked for it still typing mouse reports.

       So the reset is captured before the wait: the pane object and the
       session id it was asked for. The two halves are deliberately unequal.
       The *write* follows the captured pane wherever it now lives — cached
       and off-screen included — because that pane asked for its input back
       and the answer is owed to it. The *slot* work (the redraw, the busy
       state on `trefresh-<index>`) is skipped when that pane is no longer the
       one on screen, because those touch a card that now belongs to somebody
       else. */
    function captureResetTarget(io) {
        const target = io || {};
        const pane = target.pane || null;
        const sessionId = target.sessionId;
        const write = typeof target.write === 'function' ? target.write : null;
        const flush = typeof target.flush === 'function' ? target.flush : null;
        const currentPane = typeof target.currentPane === 'function' ? target.currentPane : null;
        const currentSessionId = typeof target.currentSessionId === 'function'
            ? target.currentSessionId
            : null;

        return {
            pane,
            sessionId,
            /* Bound to the captured pane, and flushing *its* queue first:
               anything held behind a not-yet-fitted pane — the replay
               included — has to be applied before the teardown that exists to
               undo it. */
            write(data) {
                if (!pane || !write) {
                    return false;
                }
                if (flush) {
                    flush(pane);
                }
                write(pane, data);
                return true;
            },
            /* Slot-level work only. Never gates the write above. */
            isCurrent() {
                if (!pane) {
                    return false;
                }
                if (currentPane && currentPane() !== pane) {
                    return false;
                }
                if (currentSessionId && currentSessionId() !== sessionId) {
                    return false;
                }
                return true;
            }
        };
    }

    /* `write` is the pane's own `term.write` — client-side only. Returns
       whether the teardown was actually written, so a caller can tell "the
       pane was reset" from "there was no pane". */
    function resetMouseReporting(write) {
        if (typeof write !== 'function') {
            return false;
        }
        write(MOUSE_REPORTING_RESET);
        return true;
    }

    /* Leave the session room, rejoin it so the server replays its buffer, and
       reset mouse reporting once those replayed bytes have landed.

       Resolves with how the wait ended: `ack` (the server answered, the normal
       path), `timeout` (it did not, and the pane was reset anyway), or `none`
       (there was nothing to rejoin, so the reset happened immediately). */
    function rejoinAndResetAfterReplay(runtime) {
        const io = runtime || {};
        const emit = typeof io.emit === 'function' ? io.emit : null;
        const write = typeof io.write === 'function' ? io.write : null;
        const sessionId = io.sessionId;

        if (!emit || !sessionId) {
            resetMouseReporting(write);
            return Promise.resolve({ settledBy: 'none' });
        }

        const schedule = typeof io.setTimeout === 'function' ? io.setTimeout : null;
        const cancel = typeof io.clearTimeout === 'function' ? io.clearTimeout : () => {};
        const timeoutMs = Number.isFinite(io.timeoutMs) && io.timeoutMs >= 0
            ? io.timeoutMs
            : REPLAY_SETTLE_TIMEOUT_MS;

        return new Promise(resolve => {
            let settled = false;
            let timer = null;

            /* The ack and the fallback race, and exactly one of them may write
               the teardown: a second write after a live program had already
               re-armed the mode would disarm it again for no reason. */
            function settle(settledBy) {
                if (settled) {
                    return;
                }
                settled = true;
                if (timer !== null) {
                    cancel(timer);
                    timer = null;
                }
                resetMouseReporting(write);
                resolve({ settledBy });
            }

            emit('leave_session', { session_id: sessionId });
            emit('join_session', { session_id: sessionId }, () => settle('ack'));

            /* No scheduler means no fallback — waiting for the ack forever is
               still better than resetting before the replay, which is the bug
               this module exists to fix. The page always supplies one. */
            if (schedule) {
                timer = schedule(() => {
                    timer = null;
                    settle('timeout');
                }, timeoutMs);
            }
        });
    }

    return {
        MOUSE_REPORTING_MODES,
        MOUSE_REPORTING_RESET,
        REPLAY_SETTLE_TIMEOUT_MS,
        captureResetTarget,
        resetMouseReporting,
        rejoinAndResetAfterReplay
    };
}));
