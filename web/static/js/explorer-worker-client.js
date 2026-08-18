/* GridVibe explorer worker pool — the page-side half of Source tokenization
   and large-Diff parsing.

   Workers are created lazily and shared by every pane. The ceiling leaves one
   logical processor for the browser and never creates more than four workers:
   min(max(hardwareConcurrency - 1, 1), 4). Aborting a running job terminates
   that worker, because ignoring its eventual answer would still leave the CPU
   busy on content the user has already replaced.

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
            /* A worker load/runtime failure is normally a missing first-party
               asset or an unsupported engine. Retrying it for every pane would
               add console noise and repeated startup work, so this run falls
               back to the synchronous/simplified render. */
            this._disable(error);
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
        const lines = new Map();
        for (let line = 0; line < lineRunStarts.length - 1; line += 1) {
            const runs = [];
            const from = Number(lineRunStarts[line]);
            const to = Number(lineRunStarts[line + 1]);
            if (from < 0 || to < from || to > starts.length) {
                throw new Error('Explorer worker returned invalid line offsets');
            }
            for (let at = from; at < to; at += 1) {
                const start = Number(starts[at]);
                const length = Number(lengths[at]);
                const className = classes[Number(classIds[at])];
                if (start < 0 || length < 0 || start + length > text.length
                    || typeof className !== 'string') {
                    throw new Error('Explorer worker returned an invalid highlight run');
                }
                runs.push({
                    className,
                    text: text.slice(start, start + length),
                    start
                });
            }
            lines.set(line + 1, runs);
        }
        return lines;
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
            size: pool.limit,
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
        WorkerPool,
        poolSizeFor,
        decodeHighlightResult,
        workerUrlFrom,
        createBrowserClient
    };
}));
