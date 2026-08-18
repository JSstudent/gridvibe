/* GridVibeExplorerWorkerCore — CPU-only transforms shared by the browser
   worker and its synchronous fallback.

   Highlight.js returns HTML. Sending that HTML (or the Source viewer's Map of
   arrays of run objects) back to the page would replace one main-thread cost
   with a large structured clone. The worker instead turns the markup into a
   compact row-major encoding: one class-name dictionary, parallel typed
   arrays for source offsets/lengths/class ids, and one run-offset per line.
   The page rebuilds the Map against the source string it already owns.

   The large Diff tier uses the same worker for its handwritten side-by-side
   parse. Only the DOM adapter stays in explorer-diff.js; this module returns
   the exact left/right cell model that adapter renders, so parsing never needs
   the document and is behaviorally testable in Node.

   No path, filename or content is logged or persisted here. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeExplorerWorkerCore = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const ENTITY_RE = /&(?:amp|lt|gt|quot|#x27|#39|#\d+|#x[0-9a-f]+);/gi;

    function decodeEntity(entity) {
        const lower = entity.toLowerCase();
        if (lower === '&amp;') return '&';
        if (lower === '&lt;') return '<';
        if (lower === '&gt;') return '>';
        if (lower === '&quot;') return '"';
        if (lower === '&#x27;' || lower === '&#39;') return "'";
        const numeric = lower.startsWith('&#x')
            ? Number.parseInt(lower.slice(3, -1), 16)
            : Number.parseInt(lower.slice(2, -1), 10);
        return Number.isFinite(numeric) ? String.fromCodePoint(numeric) : entity;
    }

    function decodeMarkupText(value) {
        return String(value || '').replace(ENTITY_RE, decodeEntity);
    }

    /* Highlight.js emits only escaped text and nested <span class="…"> nodes.
       Parsing that deliberately narrow language avoids DOMParser/template —
       neither exists in a dedicated worker — while preserving the viewer's
       existing "innermost class wins" rule. */
    function compactHighlightMarkup(markup, expectedSourceLength) {
        const text = String(markup || '');
        const classes = [''];
        const classIdsByName = new Map([['', 0]]);
        const starts = [];
        const lengths = [];
        const classIds = [];
        const lineRunStarts = [0];
        const classStack = [''];
        let currentClass = '';
        let offset = 0;
        let cursor = 0;

        const classId = className => {
            const name = String(className || '');
            if (classIdsByName.has(name)) {
                return classIdsByName.get(name);
            }
            if (classes.length >= 0xffff) {
                throw new Error('Highlight class dictionary is too large');
            }
            const id = classes.length;
            classes.push(name);
            classIdsByName.set(name, id);
            return id;
        };

        const pushRun = (className, start, length) => {
            if (length <= 0) {
                return;
            }
            const id = classId(className);
            const last = starts.length - 1;
            /* Span boundaries occasionally split adjacent text carrying the
               same class. Coalescing those runs makes the transfer smaller
               without changing any painted output. */
            if (last >= 0
                && classIds[last] === id
                && starts[last] + lengths[last] === start
                && lineRunStarts[lineRunStarts.length - 1] <= last) {
                lengths[last] += length;
                return;
            }
            starts.push(start);
            lengths.push(length);
            classIds.push(id);
        };

        const pushText = (className, value) => {
            const decoded = decodeMarkupText(value);
            let segmentStart = 0;
            for (let at = 0; at < decoded.length; at += 1) {
                if (decoded[at] !== '\n') {
                    continue;
                }
                const segment = decoded.slice(segmentStart, at);
                const rawLength = segment.length;
                const displayLength = segment.endsWith('\r') ? rawLength - 1 : rawLength;
                pushRun(className, offset, displayLength);
                offset += rawLength + 1;
                lineRunStarts.push(starts.length);
                segmentStart = at + 1;
            }
            const tailLength = decoded.length - segmentStart;
            pushRun(className, offset, tailLength);
            offset += tailLength;
        };

        while (cursor < text.length) {
            if (text.startsWith('<span class="', cursor)) {
                const nameStart = cursor + 13;
                const tagEnd = text.indexOf('">', nameStart);
                if (tagEnd === -1) {
                    throw new Error('Malformed Highlight.js span');
                }
                const nested = decodeMarkupText(text.slice(nameStart, tagEnd));
                currentClass = nested || currentClass;
                classStack.push(currentClass);
                cursor = tagEnd + 2;
                continue;
            }
            if (text.startsWith('</span>', cursor)) {
                if (classStack.length === 1) {
                    throw new Error('Unbalanced Highlight.js span');
                }
                classStack.pop();
                currentClass = classStack[classStack.length - 1];
                cursor += 7;
                continue;
            }
            const nextOpen = text.indexOf('<span class="', cursor);
            const nextClose = text.indexOf('</span>', cursor);
            const candidates = [nextOpen, nextClose].filter(at => at !== -1);
            const next = candidates.length ? Math.min(...candidates) : text.length;
            pushText(currentClass, text.slice(cursor, next));
            cursor = next;
        }

        if (classStack.length !== 1) {
            throw new Error('Unbalanced Highlight.js span');
        }
        if (Number.isFinite(expectedSourceLength) && offset !== expectedSourceLength) {
            throw new Error('Highlight.js output does not match the source length');
        }
        lineRunStarts.push(starts.length);
        return {
            classes,
            starts: Uint32Array.from(starts),
            lengths: Uint32Array.from(lengths),
            classIds: Uint16Array.from(classIds),
            lineRunStarts: Uint32Array.from(lineRunStarts)
        };
    }

    function highlightToCompact(source, grammar, engine) {
        const text = String(source || '');
        const language = String(grammar || '');
        if (!engine || typeof engine.highlight !== 'function'
            || typeof engine.getLanguage !== 'function' || !engine.getLanguage(language)) {
            throw new Error('Highlight.js grammar is unavailable');
        }
        const markup = engine.highlight(text, {
            language,
            ignoreIllegal: true
        }).value;
        return compactHighlightMarkup(markup, text.length);
    }

    function parseSideBySideDiff(diff) {
        const source = String(diff || '');
        if (!source.trim()) {
            return { rows: [] };
        }
        const rows = [];
        let oldLine = 0;
        let newLine = 0;
        let pendingDeletes = [];

        const flushDeletes = () => {
            pendingDeletes.forEach(left => rows.push({ left, right: null }));
            pendingDeletes = [];
        };

        source.split(/\r?\n/).forEach(line => {
            const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
            if (hunk) {
                flushDeletes();
                oldLine = Number(hunk[1]);
                newLine = Number(hunk[2]);
                rows.push({ left: { type: 'hunk', text: line }, right: null });
                return;
            }
            if (!oldLine && !newLine) {
                return;
            }
            if (line.startsWith('\\ No newline')) {
                return;
            }
            if (line.startsWith('-') && !line.startsWith('---')) {
                pendingDeletes.push({
                    type: 'delete',
                    number: oldLine,
                    text: line.slice(1)
                });
                oldLine += 1;
                return;
            }
            if (line.startsWith('+') && !line.startsWith('+++')) {
                const right = {
                    type: 'add',
                    number: newLine,
                    text: line.slice(1)
                };
                newLine += 1;
                rows.push({ left: pendingDeletes.shift() || null, right });
                return;
            }
            if (line.startsWith(' ')) {
                flushDeletes();
                rows.push({
                    left: { type: 'context', number: oldLine, text: line.slice(1) },
                    right: { type: 'context', number: newLine, text: line.slice(1) }
                });
                oldLine += 1;
                newLine += 1;
            }
        });
        flushDeletes();
        return { rows };
    }

    function highlightTransferList(result) {
        return [
            result.starts.buffer,
            result.lengths.buffer,
            result.classIds.buffer,
            result.lineRunStarts.buffer
        ];
    }

    return {
        compactHighlightMarkup,
        highlightToCompact,
        highlightTransferList,
        parseSideBySideDiff
    };
}));
