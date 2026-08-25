/* GridVibe explorer worker pool — the page-side half of Source tokenization
   and large-Diff parsing.

   Workers are created lazily and shared by every pane. The ceiling leaves one
   logical processor for the browser and never creates more than four workers:
   min(max(hardwareConcurrency - 1, 1), 4). Aborting a running job terminates
   that worker, because ignoring its eventual answer would still leave the CPU
   busy on content the user has already replaced.

   Lazily created, and given back: terminals.js calls terminate() once no
   explorer pane is left anywhere, so the threads and their resident
   Highlight.js builds do not outlive the panes that wanted them. Termination
   is not disablement — the next request respawns.

   DOM-free and require()-able. The browser instance is published as
   GridVibeExplorerWorkers; tests construct pools with fake workers. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) {
        root.GridVibeExplorerWorkerClient = api;
        if (root.document) {
            root.GridVibeExplorerWorkers = api.createBrowserClient(root);
        }
    }
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const HIGHLIGHT_WORKER_MIN_CHARS = 64 * 1024;

    /* See WorkerPool's constructor. One, deliberately: the pool is page-wide
       and shared by every pane, so the cost of being wrong in the tolerant
       direction is paid on every later job. */
    const WORKER_FAILURES_TOLERATED = 1;

    function poolSizeFor(hardwareConcurrency) {
        const reported = Number(hardwareConcurrency);
        const processors = Number.isFinite(reported) && reported > 0
            ? Math.floor(reported)
            : 2;
        return Math.min(Math.max(processors - 1, 1), 4);
    }

    function abortError() {
        const error = new Error('Worker task was superseded');
        error.name = 'AbortError';
        return error;
    }

    class WorkerPool {
        constructor(options = {}) {
            this.limit = Math.max(1, Number(options.size) || 1);
            this.createWorker = typeof options.createWorker === 'function'
                ? options.createWorker
                : null;
            this.workers = [];
            this.queue = [];
            this.nextId = 1;
            this.disabled = !this.createWorker;
            /* Worker failures tolerated before the pool gives up for good. A
               worker that cannot be *constructed* is a missing first-party
               asset and disables the pool outright; a worker that dies while
               running one job might have died of that job. One retry
               distinguishes them without turning a permanently broken
               environment into an endless respawn loop. */
            this.failuresLeft = WORKER_FAILURES_TOLERATED;
        }

        available() {
            return !this.disabled;
        }

        request(type, payload, options = {}) {
            const signal = options.signal;
            if (!this.available()) {
                return Promise.reject(new Error('Explorer workers are unavailable'));
            }
            if (signal?.aborted) {
                return Promise.reject(abortError());
            }
            return new Promise((resolve, reject) => {
                const job = {
                    id: this.nextId++,
                    type: String(type || ''),
                    payload: payload || {},
                    signal,
                    resolve,
                    reject,
                    workerRecord: null,
                    abortListener: null,
                    settled: false
                };
                if (signal && typeof signal.addEventListener === 'function') {
                    job.abortListener = () => this._abort(job);
                    signal.addEventListener('abort', job.abortListener, { once: true });
                }
                this.queue.push(job);
                this._dispatch();
            });
        }

        terminate() {
            const error = abortError();
            this.queue.splice(0).forEach(job => this._settle(job, 'reject', error));
            this.workers.splice(0).forEach(record => {
                if (record.job) this._settle(record.job, 'reject', error);
                record.worker.terminate?.();
            });
        }

        _spawn() {
            let worker;
            try {
                worker = this.createWorker();
            } catch (error) {
                this._disable(error);
                return null;
            }
            const record = { worker, job: null };
            worker.onmessage = event => this._message(record, event?.data || {});
            worker.onerror = event => {
                const message = event?.message || 'Explorer worker failed';
                this._failWorker(record, new Error(message));
            };
            worker.onmessageerror = () => {
                this._failWorker(record, new Error('Explorer worker returned an unreadable result'));
            };
            this.workers.push(record);
            return record;
        }

        _dispatch() {
            while (this.queue.length && this.available()) {
                let record = this.workers.find(candidate => !candidate.job);
                if (!record && this.workers.length < this.limit) {
                    record = this._spawn();
                }
                if (!record) {
                    return;
                }
                const job = this.queue.shift();
                if (job.signal?.aborted) {
                    this._settle(job, 'reject', abortError());
                    continue;
                }
                record.job = job;
                job.workerRecord = record;
                try {
                    record.worker.postMessage({
                        id: job.id,
                        type: job.type,
                        ...job.payload
                    });
                } catch (error) {
                    this._failWorker(record, error);
                }
            }
        }

        _message(record, data) {
            const job = record.job;
            if (!job || data.id !== job.id) {
                return;
            }
            record.job = null;
            job.workerRecord = null;
            if (data.ok === false) {
                this._settle(job, 'reject', new Error(data.error || 'Explorer worker task failed'));
            } else {
                this._settle(job, 'resolve', data.result);
            }
            this._dispatch();
        }

        _abort(job) {
            if (job.settled) {
                return;
            }
            const queuedAt = this.queue.indexOf(job);
            if (queuedAt !== -1) {
                this.queue.splice(queuedAt, 1);
                this._settle(job, 'reject', abortError());
                return;
            }
            const record = job.workerRecord;
            if (record) {
                record.worker.terminate?.();
                this.workers = this.workers.filter(candidate => candidate !== record);
                record.job = null;
                job.workerRecord = null;
            }
            this._settle(job, 'reject', abortError());
            this._dispatch();
        }

        _failWorker(record, error) {
            record.worker.terminate?.();
            this.workers = this.workers.filter(candidate => candidate !== record);
            const job = record.job;
            record.job = null;
            if (job) {
                job.workerRecord = null;
                this._settle(job, 'reject', error);
            }
            /* A worker load failure is normally a missing first-party asset or
               an unsupported engine, and retrying that for every pane would
               add console noise and repeated startup work — so the pool gives
               up and this run falls back to the synchronous/simplified render.

               But this path also catches a postMessage throw and a worker that
               died on one particular job, which are per-job conditions, and
               disabling on the first of those cost every pane its offloading
               for the rest of the session with no way back. So the first
               failure only spends a life: the record is already gone from
               this.workers, and _dispatch() below respawns to serve whatever
               is still queued. The second failure disables, which is what
               keeps a genuinely broken environment from respawning forever. */
            this.failuresLeft -= 1;
            if (this.failuresLeft < 0) {
                this._disable(error);
                return;
            }
            this._dispatch();
        }

        _disable(error) {
            this.disabled = true;
            this.queue.splice(0).forEach(job => this._settle(job, 'reject', error));
        }

        _settle(job, method, value) {
            if (!job || job.settled) {
                return;
            }
            job.settled = true;
            if (job.signal && job.abortListener
                && typeof job.signal.removeEventListener === 'function') {
                job.signal.removeEventListener('abort', job.abortListener);
            }
            job[method](value);
        }
    }

    /* The worker's answer, as the Map the row renderer reads — but materialized
       one line at a time.

       Building every run object and every substring up front is the whole
       document's worth of allocation in one synchronous task, landing at
       exactly the moment the worker was introduced to protect: a 300 KiB
       source file is well over a hundred thousand runs, and the freeze the
       page had while Highlight.js ran simply moved to the line after it. The
       compact arrays are already the answer; a line's runs are a bounded slice
       of them, and the frame-sliced build asks for at most a few hundred lines
       per frame.

       Every structural invariant is still checked eagerly, because a bad shape
       must fail where the answer is accepted and not halfway through a paint.
       That pass is arithmetic over the typed arrays and allocates nothing, so
       it costs a fraction of what it replaces. Map's read surface is kept
       whole (`get`/`has`/`size`/`keys`/`values`/`entries`/`forEach` and
       iteration) so that callers cannot tell the difference. */
    class HighlightLines {
        constructor(text, starts, lengths, classIds, lineRunStarts, classes) {
            this._text = text;
            this._starts = starts;
            this._lengths = lengths;
            this._classIds = classIds;
            this._lineRunStarts = lineRunStarts;
            this._classes = classes;
            this._cache = new Map();
            this.size = lineRunStarts.length - 1;
        }

        /* How much of the document has actually been built. Not decoration:
           "the runs are materialized on demand" is otherwise invisible from
           the outside — every other observation a caller can make is identical
           either way — and it is the property this class exists for, so it is
           the property its test reads. */
        get materialized() {
            return this._cache.size;
        }

        has(line) {
            const at = Number(line);
            return Number.isInteger(at) && at >= 1 && at <= this.size;
        }

        /* This line's runs as a value that compares across two answers, read
           straight off the typed arrays: no run objects, no substrings and
           nothing added to the cache, so `materialized` is unmoved and the
           one-line-at-a-time contract survives being asked for every key.

           The class *name* goes in, never the numeric id.
           compactHighlightMarkup() assigns ids in first-encounter order, so an
           edit that changes which class appears first renumbers them all: two
           answers may give the same name different ids, or the same id
           different names. */
        lineKey(line) {
            if (!this.has(line)) {
                return '';
            }
            const at = Number(line);
            const from = Number(this._lineRunStarts[at - 1]);
            const to = Number(this._lineRunStarts[at]);
            let key = '';
            for (let run = from; run < to; run += 1) {
                key += `${Number(this._lengths[run])}:${this._classes[Number(this._classIds[run])]}|`;
            }
            return key;
        }

        get(line) {
            if (!this.has(line)) {
                return undefined;
            }
            const at = Number(line);
            const cached = this._cache.get(at);
            if (cached) {
                return cached;
            }
            const from = Number(this._lineRunStarts[at - 1]);
            const to = Number(this._lineRunStarts[at]);
            const runs = [];
            for (let run = from; run < to; run += 1) {
                const start = Number(this._starts[run]);
                const length = Number(this._lengths[run]);
                runs.push({
                    className: this._classes[Number(this._classIds[run])],
                    text: this._text.slice(start, start + length),
                    start
                });
            }
            this._cache.set(at, runs);
            return runs;
        }

        * keys() {
            for (let line = 1; line <= this.size; line += 1) {
                yield line;
            }
        }

        * values() {
            for (let line = 1; line <= this.size; line += 1) {
                yield this.get(line);
            }
        }

        * entries() {
            for (let line = 1; line <= this.size; line += 1) {
                yield [line, this.get(line)];
            }
        }

        [Symbol.iterator]() {
            return this.entries();
        }

        forEach(callback, thisArg) {
            for (let line = 1; line <= this.size; line += 1) {
                callback.call(thisArg, this.get(line), line, this);
            }
        }
    }

    function decodeHighlightResult(source, result) {
        const text = String(source || '');
        const compact = result || {};
        const classes = Array.isArray(compact.classes) ? compact.classes : [];
        const starts = compact.starts || [];
        const lengths = compact.lengths || [];
        const classIds = compact.classIds || [];
        const lineRunStarts = compact.lineRunStarts || [];
        if (starts.length !== lengths.length || starts.length !== classIds.length
            || lineRunStarts.length < 2) {
            throw new Error('Explorer worker returned an invalid highlight map');
        }
        for (let line = 0; line < lineRunStarts.length - 1; line += 1) {
            const from = Number(lineRunStarts[line]);
            const to = Number(lineRunStarts[line + 1]);
            if (from < 0 || to < from || to > starts.length) {
                throw new Error('Explorer worker returned invalid line offsets');
            }
        }
        for (let run = 0; run < starts.length; run += 1) {
            const start = Number(starts[run]);
            const length = Number(lengths[run]);
            if (start < 0 || length < 0 || start + length > text.length
                || typeof classes[Number(classIds[run])] !== 'string') {
                throw new Error('Explorer worker returned an invalid highlight run');
            }
        }
        return new HighlightLines(text, starts, lengths, classIds, lineRunStarts, classes);
    }

    function workerUrlFrom(scriptUrl, baseUrl) {
        const parsed = new URL(scriptUrl || '/static/js/explorer-worker-client.js', baseUrl);
        const search = parsed.search;
        parsed.pathname = parsed.pathname.replace(/[^/]*$/, 'explorer-worker.js');
        parsed.search = search;
        return parsed.href;
    }

    function createBrowserClient(environment) {
        const root = environment || {};
        const WorkerCtor = root.Worker;
        const currentScript = root.document?.currentScript?.src || '/static/js/explorer-worker-client.js';
        const baseUrl = root.location?.href || 'http://localhost/';
        let workerUrl = '';
        try {
            workerUrl = workerUrlFrom(currentScript, baseUrl);
        } catch (_error) {
            workerUrl = '/static/js/explorer-worker.js';
        }
        const pool = new WorkerPool({
            size: poolSizeFor(root.navigator?.hardwareConcurrency),
            createWorker: typeof WorkerCtor === 'function'
                ? () => new WorkerCtor(workerUrl)
                : null
        });
        return {
            available: () => pool.available(),
            canHighlight: source => (
                pool.available() && String(source || '').length >= HIGHLIGHT_WORKER_MIN_CHARS
            ),
            highlight(source, grammar, options = {}) {
                const text = String(source || '');
                return pool.request('highlight', {
                    source: text,
                    grammar: String(grammar || '')
                }, options).then(result => decodeHighlightResult(text, result));
            },
            parseDiff(diff, options = {}) {
                return pool.request('diff', { diff: String(diff || '') }, options);
            },
            terminate: () => pool.terminate()
        };
    }

    return {
        HIGHLIGHT_WORKER_MIN_CHARS,
        HighlightLines,
        WorkerPool,
        poolSizeFor,
        decodeHighlightResult,
        workerUrlFrom,
        createBrowserClient
    };
}));
