(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeExplorerPersistence = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const VERSION = 2;
    const VIEW_MODES = new Set(['source', 'preview', 'diff']);
    const SCROLL_PANELS = ['source', 'preview', 'diff', 'directory'];

    function clampRatio(value) {
        const number = Number(value);
        return Number.isFinite(number) ? Math.max(0, Math.min(1, number)) : 0;
    }

    function scrollPoint(metrics) {
        if (!metrics || typeof metrics !== 'object') return null;
        return {
            x: clampRatio(metrics.scrollLeftRatio),
            y: metrics.wasAtBottom ? 1 : clampRatio(metrics.scrollTopRatio)
        };
    }

    function scrollMetrics(point) {
        if (!point || typeof point !== 'object') return null;
        const x = clampRatio(point.x);
        const y = clampRatio(point.y);
        return {
            scrollLeftRatio: x,
            scrollTopRatio: y,
            wasAtBottom: y >= 0.999
        };
    }

    function normalizeIntent(raw) {
        if (!raw || typeof raw !== 'object' || !VIEW_MODES.has(raw.mode)) return null;
        const intent = { mode: raw.mode };
        if (raw.mode === 'diff') {
            const commit = String(raw.diff_commit || raw.diffCommit || '');
            const mode = String(raw.diff_mode || raw.diffMode || '');
            if (/^[0-9a-f]{7,64}$/i.test(commit)) intent.diff_commit = commit;
            else if (['worktree', 'staged'].includes(mode)) intent.diff_mode = mode;
        }
        return intent;
    }

    function normalizeV2(raw) {
        const intent = normalizeIntent(raw && raw.intent);
        if (!intent || Number(raw.version) !== VERSION) return null;
        const normalized = { version: VERSION, intent };
        if (typeof raw.content_revision === 'string' && raw.content_revision) {
            normalized.content_revision = raw.content_revision;
        }
        if (raw.content_revisions && typeof raw.content_revisions === 'object') {
            normalized.content_revisions = {};
            SCROLL_PANELS.forEach(panel => {
                if (typeof raw.content_revisions[panel] === 'string' && raw.content_revisions[panel]) {
                    normalized.content_revisions[panel] = raw.content_revisions[panel];
                }
            });
            if (!Object.keys(normalized.content_revisions).length) delete normalized.content_revisions;
        }
        if (raw.scroll && typeof raw.scroll === 'object') {
            normalized.scroll = {};
            SCROLL_PANELS.forEach(panel => {
                const point = raw.scroll[panel];
                if (point && typeof point === 'object') {
                    normalized.scroll[panel] = {
                        x: clampRatio(point.x),
                        y: clampRatio(point.y)
                    };
                }
            });
            if (!Object.keys(normalized.scroll).length) delete normalized.scroll;
        }
        if (Number.isInteger(raw.font_size)) normalized.font_size = raw.font_size;
        if (raw.wrap && typeof raw.wrap === 'object') {
            normalized.wrap = {};
            ['source', 'preview', 'diff'].forEach(panel => {
                if (typeof raw.wrap[panel] === 'boolean') normalized.wrap[panel] = raw.wrap[panel];
            });
            if (!Object.keys(normalized.wrap).length) delete normalized.wrap;
        }
        if (Array.isArray(raw.folds)) normalized.folds = raw.folds.slice();
        if (typeof raw.fold_revision === 'string' && raw.fold_revision) {
            normalized.fold_revision = raw.fold_revision;
        }
        ['path', 'dir'].forEach(field => {
            if (typeof raw[field] === 'string') normalized[field] = raw[field];
        });
        return normalized;
    }

    function fromLegacy(raw) {
        if (!raw || typeof raw !== 'object' || !VIEW_MODES.has(raw.mode)) return null;
        const intent = normalizeIntent(raw);
        const fraction = clampRatio(raw.scroll);
        const normalized = {
            version: VERSION,
            intent,
            content_revision: typeof raw.identity === 'string' ? raw.identity : '',
            scroll: { [intent.mode]: { x: 0, y: fraction } }
        };
        if (Number.isInteger(raw.font_size)) normalized.font_size = raw.font_size;
        normalized.wrap = {
            source: raw.wrap_source !== false,
            preview: raw.wrap_preview !== false,
            diff: raw.wrap_diff !== false
        };
        if (Array.isArray(raw.folds)) normalized.folds = raw.folds.slice();
        if (typeof raw.fold_identity === 'string') normalized.fold_revision = raw.fold_identity;
        ['path', 'dir'].forEach(field => {
            if (typeof raw[field] === 'string') normalized[field] = raw[field];
        });
        return normalized;
    }

    function normalizeRecord(raw) {
        return raw && Number(raw.version) === VERSION ? normalizeV2(raw) : fromLegacy(raw);
    }

    function buildRecord(options) {
        const opts = options || {};
        const intent = normalizeIntent({
            mode: opts.mode,
            diffCommit: opts.diffCommit,
            diffMode: opts.diffMode
        });
        if (!intent) return null;
        const record = { version: VERSION, intent };
        const revisions = opts.revisions && typeof opts.revisions === 'object' ? opts.revisions : {};
        const activeRevision = String(revisions[intent.mode] || revisions.directory || '');
        if (activeRevision) record.content_revision = activeRevision;
        const contentRevisions = {};
        SCROLL_PANELS.forEach(panel => {
            if (typeof revisions[panel] === 'string' && revisions[panel]) {
                contentRevisions[panel] = revisions[panel];
            }
        });
        if (Object.keys(contentRevisions).length) record.content_revisions = contentRevisions;
        const liveScroll = opts.scroll || {};
        const points = {};
        Object.entries(liveScroll.panels || {}).forEach(([panel, metrics]) => {
            if (SCROLL_PANELS.includes(panel)) points[panel] = scrollPoint(metrics);
        });
        if (liveScroll.directory) points.directory = scrollPoint(liveScroll.directory);
        Object.keys(points).forEach(panel => { if (!points[panel]) delete points[panel]; });
        if (Object.keys(points).length) record.scroll = points;
        if (Number.isInteger(opts.fontSize)) record.font_size = opts.fontSize;
        if (opts.wrap && typeof opts.wrap === 'object') {
            record.wrap = {
                source: opts.wrap.source !== false,
                preview: opts.wrap.preview !== false,
                diff: opts.wrap.diff !== false
            };
        }
        if (Array.isArray(opts.folds) && opts.folds.length) record.folds = opts.folds.slice();
        if (typeof opts.foldRevision === 'string' && opts.foldRevision) {
            record.fold_revision = opts.foldRevision;
        }
        return record;
    }

    function resolveRecord(raw, currentRevisions) {
        const record = normalizeRecord(raw);
        if (!record) return null;
        const current = currentRevisions && typeof currentRevisions === 'object'
            ? currentRevisions
            : {};
        const storedRevisions = record.content_revisions || {};
        const resolvedScroll = { activeView: record.intent.mode, panels: {}, sidebar: {} };
        Object.entries(record.scroll || {}).forEach(([panel, point]) => {
            const storedRevision = storedRevisions[panel] || (
                panel === record.intent.mode || panel === 'directory'
                    ? record.content_revision
                    : ''
            );
            if (storedRevision && current[panel] === storedRevision) {
                const metrics = scrollMetrics(point);
                if (panel === 'directory') resolvedScroll.directory = metrics;
                else resolvedScroll.panels[panel] = metrics;
            }
        });
        const foldRevision = record.fold_revision || record.content_revision || '';
        const folds = foldRevision && current.source === foldRevision
            ? (record.folds || []).slice()
            : [];
        return {
            record,
            mode: record.intent.mode,
            diffCommit: record.intent.diff_commit || '',
            diffMode: record.intent.diff_mode || '',
            scroll: resolvedScroll,
            folds
        };
    }

    function hashText(text) {
        const value = String(text == null ? '' : text);
        let hash = 5381;
        for (let index = 0; index < value.length; index += 1) {
            hash = ((hash << 5) + hash + value.charCodeAt(index)) | 0;
        }
        return `djb2:${(hash >>> 0).toString(36)}`;
    }

    function diffContentRevision(options) {
        const opts = options || {};
        return hashText([
            opts.path || '',
            opts.diffCommit || '',
            opts.diffMode || '',
            opts.renderedDiff || ''
        ].join('\u0000'));
    }

    return {
        VERSION,
        buildRecord,
        normalizeRecord,
        resolveRecord,
        diffContentRevision,
        scrollPoint,
        scrollMetrics
    };
}));
