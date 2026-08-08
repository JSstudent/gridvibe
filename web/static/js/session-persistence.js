(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    } else {
        root.GridVibeSessionPersistence = api;
    }
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const CONTINUOUS_UPDATE_FLOOR_MS = 1000;

    function cloneSnapshot(value) {
        if (value === undefined) return undefined;
        return JSON.parse(JSON.stringify(value));
    }

    function createPresentationQueue(options) {
        if (!options || typeof options.send !== 'function') {
            throw new TypeError('createPresentationQueue requires send(payload)');
        }

        const groups = new Map();

        function stateFor(groupId) {
            const key = String(groupId || '').trim();
            if (!key) throw new TypeError('groupId is required');
            if (!groups.has(key)) {
                groups.set(key, {
                    latest: undefined,
                    pending: undefined,
                    inFlight: null,
                    scheduled: false,
                    timer: null,
                    revision: null,
                    waiters: []
                });
            }
            return [key, groups.get(key)];
        }

        function isIdle(state) {
            return !state.scheduled && !state.timer && !state.inFlight && state.pending === undefined;
        }

        function settleWaiters(state, error) {
            if (!isIdle(state)) return;
            const waiters = state.waiters.splice(0);
            waiters.forEach((waiter) => {
                if (error) waiter.reject(error);
                else waiter.resolve(cloneSnapshot(state.latest));
            });
        }

        async function responsePayload(response) {
            if (response && typeof response.json === 'function') {
                const body = await response.json();
                return { status: response.status, body };
            }
            return {
                status: response && Number.isInteger(response.status) ? response.status : 200,
                body: response || {}
            };
        }

        function schedule(groupId, state, continuous) {
            if (state.inFlight || state.scheduled) return;
            if (continuous) {
                if (state.timer) return;
                const delay = Math.max(
                    CONTINUOUS_UPDATE_FLOOR_MS,
                    Number(options.continuousDelayMs) || CONTINUOUS_UPDATE_FLOOR_MS
                );
                state.timer = setTimeout(() => {
                    state.timer = null;
                    void drain(groupId, state);
                }, delay);
                return;
            }
            if (state.timer) {
                clearTimeout(state.timer);
                state.timer = null;
            }
            state.scheduled = true;
            Promise.resolve().then(() => {
                state.scheduled = false;
                void drain(groupId, state);
            });
        }

        async function handleConflict(groupId, state, requestSnapshot, response) {
            const currentRevision = response.body && response.body.presentation_revision;
            if (Number.isInteger(currentRevision) && currentRevision >= 0) {
                state.revision = currentRevision;
            }
            const serverSnapshot = typeof options.fetchCurrent === 'function'
                ? await options.fetchCurrent(groupId, cloneSnapshot(response.body))
                : response.body;
            let rebased = cloneSnapshot(state.latest);
            if (typeof options.reconcile === 'function') {
                rebased = await options.reconcile({
                    groupId,
                    local: cloneSnapshot(state.latest),
                    sent: cloneSnapshot(requestSnapshot),
                    server: cloneSnapshot(serverSnapshot)
                });
            }
            if (rebased !== undefined && rebased !== null) {
                if (state.revision !== null && Object.prototype.hasOwnProperty.call(rebased, 'expected_revision')) {
                    rebased.expected_revision = state.revision;
                }
                state.latest = cloneSnapshot(rebased);
                state.pending = cloneSnapshot(rebased);
                if (typeof options.onReconciled === 'function') {
                    options.onReconciled(groupId, cloneSnapshot(rebased), cloneSnapshot(serverSnapshot));
                }
            }
        }

        async function drain(groupId, state) {
            if (state.inFlight || state.pending === undefined) {
                settleWaiters(state);
                return;
            }
            const requestSnapshot = cloneSnapshot(state.pending);
            state.pending = undefined;
            if (
                state.revision !== null
                && requestSnapshot
                && Object.prototype.hasOwnProperty.call(requestSnapshot, 'expected_revision')
            ) {
                requestSnapshot.expected_revision = state.revision;
            }

            const task = Promise.resolve().then(() => options.send(requestSnapshot, groupId));
            state.inFlight = task;
            let failure = null;
            try {
                const response = await responsePayload(await task);
                if (response.status === 409) {
                    await handleConflict(groupId, state, requestSnapshot, response);
                } else if (response.status >= 400) {
                    const message = response.body && response.body.error;
                    throw new Error(message || `Presentation update failed (${response.status})`);
                } else if (
                    response.body
                    && Number.isInteger(response.body.presentation_revision)
                    && response.body.presentation_revision >= 0
                ) {
                    state.revision = response.body.presentation_revision;
                }
            } catch (error) {
                failure = error;
                if (typeof options.onError === 'function') {
                    options.onError(groupId, error, cloneSnapshot(requestSnapshot));
                }
            } finally {
                state.inFlight = null;
            }

            if (state.pending !== undefined) {
                schedule(groupId, state, false);
            }
            settleWaiters(state, failure);
        }

        function enqueue(groupId, snapshot, enqueueOptions) {
            const [key, state] = stateFor(groupId);
            state.latest = cloneSnapshot(snapshot);
            state.pending = cloneSnapshot(snapshot);
            const continuous = Boolean(enqueueOptions && enqueueOptions.continuous);
            schedule(key, state, continuous);
            return cloneSnapshot(state.latest);
        }

        function flush(groupId) {
            const [key, state] = stateFor(groupId);
            if (state.timer) {
                clearTimeout(state.timer);
                state.timer = null;
                schedule(key, state, false);
            } else if (state.pending !== undefined) {
                schedule(key, state, false);
            }
            if (isIdle(state)) return Promise.resolve(cloneSnapshot(state.latest));
            return new Promise((resolve, reject) => state.waiters.push({ resolve, reject }));
        }

        function latest(groupId) {
            const [, state] = stateFor(groupId);
            return cloneSnapshot(state.latest);
        }

        function revision(groupId) {
            const [, state] = stateFor(groupId);
            return state.revision;
        }

        return { enqueue, flush, latest, revision };
    }

    return { createPresentationQueue, CONTINUOUS_UPDATE_FLOOR_MS };
}));
