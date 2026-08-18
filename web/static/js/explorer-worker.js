/* Dedicated worker entry point for the explorer's CPU-heavy transforms.
   First-party modules and the pinned Highlight.js build are same-origin and
   carry the page script's cache-busting query string. */
'use strict';

const version = self.location?.search || '';
importScripts(
    `/static/vendor/highlight.min.js${version}`,
    `/static/js/explorer-worker-core.js${version}`
);

self.onmessage = event => {
    const message = event?.data || {};
    const id = message.id;
    try {
        if (message.type === 'highlight') {
            const result = self.GridVibeExplorerWorkerCore.highlightToCompact(
                message.source,
                message.grammar,
                self.hljs
            );
            self.postMessage(
                { id, ok: true, result },
                self.GridVibeExplorerWorkerCore.highlightTransferList(result)
            );
            return;
        }
        if (message.type === 'diff') {
            const result = self.GridVibeExplorerWorkerCore.parseSideBySideDiff(message.diff);
            self.postMessage({ id, ok: true, result });
            return;
        }
        throw new Error('Unknown explorer worker task');
    } catch (error) {
        self.postMessage({
            id,
            ok: false,
            error: error instanceof Error ? error.message : 'Explorer worker task failed'
        });
    }
};
