    /* ─────────────────────────────────────────────
       Explorer viewer — extracted from terminals.js by the move-only second
       phase of the terminals.js split.
       File-type classifier, syntax highlight, Files tree, source/markdown
       render, image/mermaid viewer, breadcrumb,
       directory listing and per-panel scroll.
       The tab strip itself — the tab records, the strip, its interactions,
       the per-tab view snapshot and saved-tab persistence — moved to
       explorer-tabs.js; the Git sidebar moved to explorer-git-sidebar.js and
       the Diff panel to explorer-diff.js. All are classic scripts sharing the
       same global scope.
       Loaded before terminals.js; all shared terminal state, the markdown
       preview key listener and generic helpers remain in terminals.js.
    ───────────────────────────────────────────── */
    function formatExplorerSize(value) {
        if (value === null || value === undefined) {
            return '';
        }
        const size = Number(value);
        if (!Number.isFinite(size)) {
            return '';
        }
        if (size < 1024) {
            return `${size} B`;
        }
        const units = ['KB', 'MB', 'GB', 'TB'];
        let next = size / 1024;
        for (const unit of units) {
            if (next < 1024) {
                return `${next.toFixed(next >= 10 ? 0 : 1)} ${unit}`;
            }
            next /= 1024;
        }
        return `${next.toFixed(1)} PB`;
    }

    function formatExplorerDate(value) {
        const timestamp = Number(value);
        if (!Number.isFinite(timestamp)) {
            return '';
        }
        return new Date(timestamp * 1000).toLocaleString();
    }

    /* OD-9: previews above ~2 MiB render as plain text (no syntax
       highlighting) so large files stay responsive; the backend cap itself
       is 10 MiB (EXPLORER_FILE_PREVIEW_MAX_BYTES). Compared against the
       decoded character count, which is a close proxy for the byte size. */
    const EXPLORER_PLAIN_PREVIEW_THRESHOLD = 2 * 1024 * 1024;

    /* One in-flight request per pane per named slot. Opening a second file
       while the first is still arriving used to leave both fetches, both JSON
       decodes and both renders to run to completion, so clicking down a tree
       queued up work for content nobody was looking at any more; the newer
       load now cancels the one it replaces.

       Superseding is deliberately *within* a slot only — a diff load does not
       cancel a file load — and abort stays an optimization, never a
       correctness mechanism: a request that already resolved cannot be called
       back, so every caller keeps its post-arrival identity check exactly as
       it was. Returns undefined where AbortController is unavailable, which
       fetch() reads as "no signal". */
    function explorerRequestSignal(pane, slot) {
        if (!pane || typeof AbortController !== 'function') {
            return undefined;
        }
        const slots = pane._explorerRequestAborters || (pane._explorerRequestAborters = {});
        slots[slot]?.abort();
        const controller = new AbortController();
        slots[slot] = controller;
        return controller.signal;
    }

    function cancelExplorerRequestSlot(pane, slot) {
        const slots = pane?._explorerRequestAborters;
        if (!slots || !slots[slot]) {
            return;
        }
        slots[slot].abort();
        delete slots[slot];
    }

    /* Every slot at once, for a pane that is going away. The two worker-backed
       slots (`highlight`, `editHighlight`) genuinely stop the thread — the
       pool terminates the worker running an aborted job — so this is not just
       a dropped callback. Every caller already guards `AbortError`, because
       supersession within a slot aborts the same way, so cancelling here can
       never surface as a console line (guardrail 9). */
    function cancelExplorerRequestSlots(pane) {
        const slots = pane?._explorerRequestAborters;
        if (!slots) {
            return;
        }
        Object.keys(slots).forEach(slot => {
            slots[slot]?.abort();
        });
        delete pane._explorerRequestAborters;
    }

    /* A deliberate abort is not a failure and must not reach the console
       (guardrail 9) — otherwise every fast file switch writes a red line.
       `AbortError` is the fetch contract; the legacy numeric ABORT_ERR code
       covers engines that still reject with a bare DOMException. */
    function explorerIsAbortError(error) {
        return Boolean(error) && (error.name === 'AbortError' || error.code === 20);
    }

    /* The presentation-tier policy (explorer-tiers.js, DOM-free and
       Node-tested). Looked up rather than captured so a page that somehow
       loaded without it degrades to today's behaviour instead of throwing. */
    function explorerTierPolicy() {
        return (typeof window !== 'undefined' && window.GridVibeExplorerTiers) || null;
    }

    /* One place decides how a buffer is presented, so the 2 MiB highlight
       threshold and the presentation tier can never end up computed from
       different content. Cached on the pane because counting lines is O(bytes)
       and the render path asks on every repaint. */
    function applyExplorerSourceTier(pane, content) {
        if (!pane) {
            return;
        }
        const text = typeof content === 'string' ? content : '';
        const policy = explorerTierPolicy();
        pane._explorerFilePlain = text.length > EXPLORER_PLAIN_PREVIEW_THRESHOLD;
        pane._explorerSourceMetrics = policy
            ? policy.sourceMetrics(text)
            : { bytes: text.length, lines: 0 };
        pane._explorerSourceTier = policy
            ? policy.sourceTier(pane._explorerSourceMetrics)
            : 'full';
    }

    function explorerPaneSourceTier(pane) {
        return pane?._explorerSourceTier || 'full';
    }

    /* Find is a capability of the tier, not of the view: the one input in the
       header serves Source, Preview and Diff, so leaving it live while the
       view it is pointed at cannot answer would hand the reader a control that
       silently does nothing. The tier's notice says so in as many words. */
    function explorerPaneAllowsFind(pane) {
        const policy = explorerTierPolicy();
        return policy
            ? policy.sourceTierAllows(explorerPaneSourceTier(pane), 'find')
            : true;
    }

    /* The tier's in-pane notice. Deliberately *not* showGridVibeNotice(): that
       is the launcher's one global banner and it reports events, while this
       describes a state that lasts as long as the file is open. Same shape as
       the diff truncation banner — one role="status" element inside the
       surface it describes, styled from tokens.css. */
    function explorerSourceTierNoticeHtml(pane) {
        const policy = explorerTierPolicy();
        const notice = policy ? policy.sourceTierNotice(pane?._explorerSourceMetrics || null) : null;
        if (!notice) {
            return '';
        }
        return '<div class="explorer-source-tier-notice" role="status">'
            + `<strong>${escHtml(notice.title)}</strong> `
            + `${escHtml(notice.detail)} `
            + `Turned off: ${escHtml(notice.disabled.join(', '))}. `
            + `<strong>${escHtml(notice.findNote)}</strong> `
            + escHtml(notice.retained)
            + '</div>';
    }

    /* The large tier's body: a bounded number of plain chunks instead of one
       row per line. Nothing here is per-line, so the gutter, section folding,
       the occurrence tint (which anchors on `.explorer-source-lines`), the
       change marks and the overview ruler are absent by construction rather
       than by a flag each of them has to remember to check.

       The leading newline is sacrificial and has to stay. sourceChunks() cuts
       *after* a newline, so a chunk begins with the next line's first
       character — and when that line is blank, the chunk begins with a
       newline. The HTML fragment parser drops a single U+000A immediately
       following a `<pre>` start tag (the same rule covers `listing` and
       `textarea`), and `innerHTML` and `insertAdjacentHTML` both run that
       algorithm, so the file's own blank line was eaten and the pane stopped
       being byte-faithful to it. Giving the parser a newline of ours leaves it
       something to swallow that is not the file's. */
    function explorerLargeSourceChunkHtml(chunk) {
        return `<pre class="explorer-source-chunk">\n${escHtml(chunk)}</pre>`;
    }

    /* An element built outside the document, so a rebuild costs no layout
       until the moment it is swapped in. Returns null for empty markup. */
    function explorerDetachedElement(html) {
        const template = document.createElement('template');
        template.innerHTML = String(html || '');
        return template.content.firstElementChild;
    }

    function explorerLargeSourceHost(code) {
        return code ? code.querySelector('.explorer-source-plain') : null;
    }

    /* Put the finished chunks on screen in place of the ones the reader has
       been looking at, holding their offset across the exchange. Both writes
       are in one task, so the collapsed intermediate state is never painted. */
    function explorerSwapLargeSourceHost(code, pane, previous, host) {
        const top = code.scrollTop;
        const left = code.scrollLeft;
        const notice = code.querySelector('.explorer-source-tier-notice');
        const nextNotice = explorerDetachedElement(explorerSourceTierNoticeHtml(pane));
        if (notice && nextNotice) {
            code.replaceChild(nextNotice, notice);
        }
        code.replaceChild(host, previous);
        code.scrollTop = top;
        code.scrollLeft = left;
    }

    /* The large tier's paint, over frames.

       Not building one row per line is what the tier is for, but escaping ten
       megabytes and handing the parser a single string that size is the same
       uninterruptible task wearing a different shape — and it lands on exactly
       the files the tier exists to make openable. The chunks the policy
       already cuts are the unit, emitted under the same frame budget the row
       build uses, so the window keeps answering clicks throughout.

       Where those chunks are assembled depends on whether there is anything
       to protect. The first paint has nothing on screen, so the notice and an
       empty host go in and the file fills in from the top. A *replacement* —
       a watcher refresh, a save — has the reader somewhere in the document,
       and emptying the scroller under them collapses the content, which the
       browser answers by putting them at the top for every frame the rebuild
       lasts before the restore snaps them home at the end. A log file gaining
       a line did that on every poll. So a replacement is assembled off-screen
       and swapped in whole.

       Readers queued through whenExplorerSourceRendered() wait for the last
       chunk, exactly as they wait for the last row: a scroll restore that ran
       against an empty host would restore nothing. */
    function renderExplorerLargeSource(index, code) {
        const pane = terminals[index];
        const policy = explorerTierPolicy();
        const content = pane?._explorerFileContent || '';
        /* Nothing this tier paints depends on anything a repaint moves: there
           is no find, no gutter and no fold set, so a second call for the same
           buffer would re-escape and re-parse megabytes to produce exactly the
           chunks already on screen. */
        const painted = pane._explorerLargeSourceRender;
        if (painted
            && painted.content === content
            && !pane._explorerSourceRenderJob
            && explorerLargeSourceHost(code) === painted.host) {
            explorerFlushSourceRenderCallbacks(pane);
            return;
        }
        const chunks = policy ? policy.sourceChunks(content) : [content];
        explorerCancelSourceRenderJob(pane);
        pane._explorerSourceRender = null;
        pane._explorerSourceModel = null;
        pane._explorerLargeSourceRender = null;
        // The host on screen, if this is a replacement rather than a first paint.
        const replacing = explorerLargeSourceHost(code);
        let host;
        if (replacing) {
            host = explorerDetachedElement('<div class="explorer-source-plain"></div>');
        } else {
            code.innerHTML = `${explorerSourceTierNoticeHtml(pane)}<div class="explorer-source-plain"></div>`;
            host = explorerLargeSourceHost(code);
        }
        if (!host) {
            code.innerHTML = `${explorerSourceTierNoticeHtml(pane)}`
                + `<div class="explorer-source-plain">${chunks.map(explorerLargeSourceChunkHtml).join('')}</div>`;
            pane._explorerLargeSourceRender = null;
            explorerFlushSourceRenderCallbacks(pane);
            return;
        }
        /* What the job must find on screen to know it is still wanted: the
           host it is filling, or the one it is going to replace. */
        const onScreen = replacing || host;
        const commit = () => {
            if (replacing) {
                explorerSwapLargeSourceHost(code, pane, replacing, host);
            }
            pane._explorerLargeSourceRender = { content, host };
        };
        if (chunks.length <= 1 || typeof window.requestAnimationFrame !== 'function') {
            host.innerHTML = chunks.map(explorerLargeSourceChunkHtml).join('');
            commit();
            explorerFlushSourceRenderCallbacks(pane);
            return;
        }
        const job = { frame: 0, at: 0, suspended: false, step: null };
        pane._explorerSourceRenderJob = job;
        const step = () => {
            job.frame = 0;
            if (job.suspended) {
                return;
            }
            if (pane._explorerSourceRenderJob !== job) {
                return;
            }
            if (explorerLargeSourceHost(code) !== onScreen) {
                // Same standing-down rule as the row build above: a job that
                // can never finish must not hold the pane's reader queue.
                explorerAbandonSourceRenderJob(pane);
                return;
            }
            const started = performance.now();
            while (job.at < chunks.length) {
                host.insertAdjacentHTML('beforeend', explorerLargeSourceChunkHtml(chunks[job.at]));
                job.at += 1;
                if (performance.now() - started >= EXPLORER_SOURCE_RENDER_BUDGET_MS) {
                    break;
                }
            }
            if (job.at < chunks.length) {
                job.frame = window.requestAnimationFrame(step);
                return;
            }
            pane._explorerSourceRenderJob = null;
            commit();
            explorerFlushSourceRenderCallbacks(pane);
        };
        job.step = step;
        job.frame = window.requestAnimationFrame(step);
    }

    const EXPLORER_LANGUAGE_BY_EXTENSION = Object.freeze({
        '.bash': 'shell',
        '.bat': 'batch',
        '.c': 'c',
        '.cc': 'cpp',
        '.cfg': 'config',
        '.cmd': 'batch',
        '.conf': 'config',
        '.cpp': 'cpp',
        '.cs': 'csharp',
        '.css': 'css',
        '.dockerfile': 'dockerfile',
        '.env': 'dotenv',
        '.example': 'config',
        '.go': 'go',
        '.gitattributes': 'config',
        '.gitignore': 'gitignore',
        '.gitkeep': 'text',
        '.h': 'c',
        '.hpp': 'cpp',
        '.html': 'html',
        '.ini': 'ini',
        '.java': 'java',
        '.js': 'javascript',
        '.jsx': 'javascript',
        '.json': 'json',
        '.jsonl': 'jsonl',
        '.kt': 'kotlin',
        '.kts': 'kotlin',
        '.log': 'log',
        '.lua': 'lua',
        '.md': 'markdown',
        '.markdown': 'markdown',
        '.mk': 'makefile',
        '.php': 'php',
        '.ps1': 'powershell',
        '.py': 'python',
        '.rb': 'ruby',
        '.rs': 'rust',
        '.sh': 'shell',
        '.sql': 'sql',
        '.spec': 'python',
        '.swift': 'swift',
        '.txt': 'text',
        '.toml': 'toml',
        '.ts': 'typescript',
        '.tsx': 'typescript',
        '.xml': 'xml',
        '.yaml': 'yaml',
        '.yml': 'yaml'
    });

    const EXPLORER_LANGUAGE_BY_FILENAME = Object.freeze({
        '.editorconfig': 'ini',
        '.env': 'dotenv',
        '.gitattributes': 'config',
        '.gitignore': 'gitignore',
        '.gitkeep': 'text',
        '.python-version': 'text',
        'dockerfile': 'dockerfile',
        'go.mod': 'go',
        'go.sum': 'text',
        'go.work': 'go',
        'go.work.sum': 'text',
        'makefile': 'makefile'
    });

    /* Conventional families that vary the name rather than the extension
       (Dockerfile_chss, Dockerfile.dev, Makefile.local, .env.local). Matched
       only after the exact-name and extension maps have both missed, so
       dockerfile_parser.py stays Python. Mirrors
       CODE_PREVIEW_FILENAME_PREFIXES in web/explorer.py. */
    const EXPLORER_LANGUAGE_BY_FILENAME_PREFIX = Object.freeze({
        '.env.': 'dotenv',
        'dockerfile': 'dockerfile',
        'makefile': 'makefile'
    });

    const EXPLORER_LANGUAGE_LABELS = Object.freeze({
        batch: 'Batch source',
        c: 'C source',
        config: 'Config file',
        cpp: 'C++ source',
        csharp: 'C# source',
        css: 'CSS source',
        dockerfile: 'Dockerfile',
        dotenv: 'Environment file',
        gitignore: 'Git ignore file',
        go: 'Go source',
        html: 'HTML source',
        ini: 'INI config',
        java: 'Java source',
        javascript: 'JavaScript source',
        json: 'JSON source',
        jsonl: 'JSON Lines source',
        kotlin: 'Kotlin source',
        log: 'Log file',
        lua: 'Lua source',
        makefile: 'Makefile',
        markdown: 'Markdown source',
        php: 'PHP source',
        powershell: 'PowerShell source',
        python: 'Python source',
        ruby: 'Ruby source',
        rust: 'Rust source',
        shell: 'Shell source',
        sql: 'SQL source',
        swift: 'Swift source',
        text: 'Text file',
        toml: 'TOML source',
        typescript: 'TypeScript source',
        xml: 'XML source',
        yaml: 'YAML source'
    });

    const EXPLORER_CODE_KEYWORDS = Object.freeze({
        batch: ['call', 'do', 'echo', 'else', 'errorlevel', 'exist', 'exit', 'for', 'goto', 'if', 'in', 'not', 'pause', 'rem', 'set', 'shift'],
        c: ['auto', 'break', 'case', 'char', 'const', 'continue', 'default', 'do', 'double', 'else', 'enum', 'extern', 'float', 'for', 'goto', 'if', 'inline', 'int', 'long', 'register', 'return', 'short', 'signed', 'sizeof', 'static', 'struct', 'switch', 'typedef', 'union', 'unsigned', 'void', 'volatile', 'while'],
        config: ['false', 'no', 'null', 'off', 'on', 'true', 'yes'],
        cpp: ['alignas', 'alignof', 'auto', 'bool', 'break', 'case', 'catch', 'class', 'const', 'constexpr', 'continue', 'decltype', 'default', 'delete', 'do', 'double', 'else', 'enum', 'explicit', 'export', 'extern', 'false', 'float', 'for', 'friend', 'if', 'inline', 'int', 'long', 'namespace', 'new', 'noexcept', 'nullptr', 'operator', 'private', 'protected', 'public', 'return', 'short', 'signed', 'sizeof', 'static', 'struct', 'switch', 'template', 'this', 'throw', 'true', 'try', 'typedef', 'typename', 'union', 'unsigned', 'using', 'virtual', 'void', 'volatile', 'while'],
        csharp: ['abstract', 'as', 'base', 'bool', 'break', 'case', 'catch', 'class', 'const', 'continue', 'decimal', 'default', 'delegate', 'do', 'double', 'else', 'enum', 'event', 'false', 'finally', 'fixed', 'float', 'for', 'foreach', 'if', 'in', 'int', 'interface', 'internal', 'is', 'lock', 'namespace', 'new', 'null', 'object', 'out', 'override', 'private', 'protected', 'public', 'readonly', 'ref', 'return', 'sealed', 'static', 'string', 'struct', 'switch', 'this', 'throw', 'true', 'try', 'typeof', 'using', 'var', 'virtual', 'void', 'while'],
        css: ['align-items', 'background', 'border', 'color', 'display', 'flex', 'font-family', 'font-size', 'grid', 'height', 'margin', 'padding', 'position', 'width'],
        dockerfile: ['ADD', 'ARG', 'CMD', 'COPY', 'ENTRYPOINT', 'ENV', 'EXPOSE', 'FROM', 'HEALTHCHECK', 'LABEL', 'RUN', 'USER', 'VOLUME', 'WORKDIR'],
        dotenv: ['false', 'no', 'off', 'on', 'true', 'yes'],
        gitignore: ['false', 'true'],
        go: ['break', 'case', 'chan', 'const', 'continue', 'default', 'defer', 'else', 'fallthrough', 'for', 'func', 'go', 'goto', 'if', 'import', 'interface', 'map', 'nil', 'package', 'range', 'return', 'select', 'struct', 'switch', 'type', 'var'],
        html: ['DOCTYPE', 'a', 'body', 'button', 'div', 'head', 'html', 'input', 'link', 'meta', 'script', 'span', 'style', 'template'],
        java: ['abstract', 'assert', 'boolean', 'break', 'case', 'catch', 'class', 'const', 'continue', 'default', 'do', 'double', 'else', 'enum', 'extends', 'false', 'final', 'finally', 'float', 'for', 'if', 'implements', 'import', 'instanceof', 'int', 'interface', 'long', 'native', 'new', 'null', 'package', 'private', 'protected', 'public', 'return', 'short', 'static', 'strictfp', 'super', 'switch', 'synchronized', 'this', 'throw', 'throws', 'true', 'try', 'void', 'volatile', 'while'],
        javascript: ['async', 'await', 'break', 'case', 'catch', 'class', 'const', 'continue', 'debugger', 'default', 'delete', 'do', 'else', 'export', 'extends', 'false', 'finally', 'for', 'from', 'function', 'if', 'import', 'in', 'instanceof', 'let', 'new', 'null', 'of', 'return', 'static', 'super', 'switch', 'this', 'throw', 'true', 'try', 'typeof', 'undefined', 'var', 'void', 'while', 'yield'],
        json: ['false', 'null', 'true'],
        jsonl: ['false', 'null', 'true'],
        kotlin: ['as', 'break', 'class', 'continue', 'data', 'do', 'else', 'false', 'for', 'fun', 'if', 'in', 'interface', 'is', 'null', 'object', 'package', 'return', 'super', 'this', 'throw', 'true', 'try', 'typealias', 'val', 'var', 'when', 'while'],
        lua: ['and', 'break', 'do', 'else', 'elseif', 'end', 'false', 'for', 'function', 'if', 'in', 'local', 'nil', 'not', 'or', 'repeat', 'return', 'then', 'true', 'until', 'while'],
        php: ['abstract', 'and', 'array', 'as', 'break', 'case', 'catch', 'class', 'clone', 'const', 'continue', 'declare', 'default', 'do', 'echo', 'else', 'elseif', 'extends', 'false', 'final', 'finally', 'for', 'foreach', 'function', 'global', 'if', 'implements', 'include', 'instanceof', 'interface', 'namespace', 'new', 'null', 'or', 'private', 'protected', 'public', 'require', 'return', 'static', 'switch', 'this', 'throw', 'trait', 'true', 'try', 'use', 'var', 'while'],
        powershell: ['begin', 'break', 'catch', 'class', 'continue', 'data', 'do', 'dynamicparam', 'else', 'elseif', 'end', 'false', 'filter', 'finally', 'for', 'foreach', 'from', 'function', 'if', 'in', 'param', 'process', 'return', 'switch', 'throw', 'trap', 'true', 'try', 'until', 'using', 'var', 'while'],
        python: ['and', 'as', 'assert', 'async', 'await', 'break', 'case', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except', 'False', 'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'match', 'None', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'True', 'try', 'while', 'with', 'yield'],
        ruby: ['BEGIN', 'END', 'alias', 'and', 'begin', 'break', 'case', 'class', 'def', 'defined?', 'do', 'else', 'elsif', 'end', 'ensure', 'false', 'for', 'if', 'in', 'module', 'next', 'nil', 'not', 'or', 'redo', 'rescue', 'retry', 'return', 'self', 'super', 'then', 'true', 'undef', 'unless', 'until', 'when', 'while', 'yield'],
        rust: ['as', 'async', 'await', 'break', 'const', 'continue', 'crate', 'dyn', 'else', 'enum', 'extern', 'false', 'fn', 'for', 'if', 'impl', 'in', 'let', 'loop', 'match', 'mod', 'move', 'mut', 'pub', 'ref', 'return', 'self', 'static', 'struct', 'super', 'trait', 'true', 'type', 'unsafe', 'use', 'where', 'while'],
        shell: ['case', 'do', 'done', 'elif', 'else', 'esac', 'export', 'fi', 'for', 'function', 'if', 'in', 'local', 'readonly', 'return', 'select', 'set', 'shift', 'then', 'until', 'while'],
        sql: ['ALTER', 'AND', 'AS', 'ASC', 'BEGIN', 'BY', 'CASE', 'CREATE', 'DELETE', 'DESC', 'DISTINCT', 'DROP', 'ELSE', 'END', 'FROM', 'GROUP', 'HAVING', 'IN', 'INSERT', 'INTO', 'IS', 'JOIN', 'LEFT', 'LIKE', 'LIMIT', 'NOT', 'NULL', 'ON', 'OR', 'ORDER', 'RIGHT', 'SELECT', 'SET', 'TABLE', 'THEN', 'UPDATE', 'VALUES', 'WHEN', 'WHERE'],
        swift: ['Any', 'as', 'associatedtype', 'break', 'case', 'catch', 'class', 'continue', 'default', 'defer', 'do', 'else', 'enum', 'extension', 'false', 'for', 'func', 'guard', 'if', 'import', 'in', 'init', 'inout', 'let', 'nil', 'operator', 'private', 'protocol', 'public', 'return', 'self', 'static', 'struct', 'subscript', 'switch', 'throw', 'true', 'try', 'typealias', 'var', 'where', 'while'],
        typescript: ['abstract', 'any', 'as', 'async', 'await', 'boolean', 'break', 'case', 'catch', 'class', 'const', 'continue', 'debugger', 'declare', 'default', 'delete', 'do', 'else', 'enum', 'export', 'extends', 'false', 'finally', 'for', 'from', 'function', 'if', 'implements', 'import', 'in', 'instanceof', 'interface', 'let', 'module', 'namespace', 'new', 'null', 'number', 'of', 'private', 'protected', 'public', 'readonly', 'return', 'static', 'string', 'super', 'switch', 'this', 'throw', 'true', 'try', 'type', 'typeof', 'undefined', 'var', 'void', 'while', 'yield'],
        xml: ['DOCTYPE'],
        yaml: ['false', 'null', 'true'],
        toml: ['false', 'true']
    });

    const EXPLORER_CODE_BUILTINS = Object.freeze({
        javascript: ['Array', 'Boolean', 'Date', 'Error', 'JSON', 'Map', 'Math', 'Number', 'Object', 'Promise', 'RegExp', 'Set', 'String', 'console', 'document', 'window'],
        python: ['bool', 'dict', 'enumerate', 'float', 'int', 'len', 'list', 'open', 'print', 'range', 'set', 'str', 'tuple'],
        go: ['append', 'bool', 'byte', 'cap', 'close', 'complex64', 'complex128', 'copy', 'delete', 'error', 'int', 'len', 'make', 'new', 'panic', 'print', 'println', 'recover', 'string'],
        shell: ['awk', 'cat', 'cd', 'cp', 'echo', 'grep', 'ls', 'mkdir', 'mv', 'printf', 'rm', 'sed', 'test'],
        powershell: ['Get-ChildItem', 'Get-Content', 'New-Item', 'Remove-Item', 'Select-Object', 'Set-Content', 'Where-Object', 'Write-Host']
    });

    /* The explorer's own normalized
       language name → the Highlight.js grammar name in the pinned custom build
       (web/static/vendor/highlight.min.js). Languages absent here — config,
       dotenv, gitignore, jsonl, log, markdown, text — keep the handwritten
       fallback lexer (highlightExplorerCode) and its special renderers. We
       always pass the grammar explicitly; Highlight.js auto-detection is never
       used (guardrail: no auto-detect). */
    const EXPLORER_HLJS_LANGUAGE = Object.freeze({
        batch: 'dos',
        c: 'c',
        cpp: 'cpp',
        csharp: 'csharp',
        css: 'css',
        dockerfile: 'dockerfile',
        go: 'go',
        html: 'xml',
        ini: 'ini',
        java: 'java',
        javascript: 'javascript',
        json: 'json',
        kotlin: 'kotlin',
        lua: 'lua',
        makefile: 'makefile',
        php: 'php',
        powershell: 'powershell',
        python: 'python',
        ruby: 'ruby',
        rust: 'rust',
        shell: 'bash',
        sql: 'sql',
        swift: 'swift',
        toml: 'ini',
        typescript: 'typescript',
        xml: 'xml',
        yaml: 'yaml'
    });

    const EXPLORER_C_LIKE_LANGUAGES = new Set(['c', 'cpp', 'csharp', 'css', 'go', 'java', 'javascript', 'kotlin', 'php', 'rust', 'swift', 'typescript']);
    const EXPLORER_HASH_COMMENT_LANGUAGES = new Set(['config', 'dockerfile', 'dotenv', 'gitignore', 'ini', 'makefile', 'python', 'ruby', 'shell', 'powershell', 'yaml', 'toml']);
    const EXPLORER_LOG_LEVELS = new Set(['TRACE', 'DEBUG', 'INFO', 'WARN', 'WARNING', 'ERROR', 'CRITICAL', 'FATAL']);
    const EXPLORER_EDITOR_FONT_MIN = 10;
    const EXPLORER_EDITOR_FONT_MAX = 24;
    const EXPLORER_EDITOR_FONT_DEFAULT = 13;
    const EXPLORER_EDITOR_FONT_STEP = 1;
    const EXPLORER_SEARCH_DEBOUNCE_MS = 160;
    const EXPLORER_SEARCH_MAX_MATCHES = 1000;
    /* Ctrl+scroll zoom bounds for image / mermaid views (notes 3). Minimum is
       the fitted (1×) size — shrinking below what fits isn't meaningful. */
    const EXPLORER_WHEEL_ZOOM_MAX = 8;
    const EXPLORER_WHEEL_ZOOM_STEP = 1.12;
    const EXPLORER_SEARCH_CHUNK_SIZE = 65536;
    const EXPLORER_SEARCH_YIELD_MS = 8;
    const EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT = 64;
    const EXPLORER_TREE_INDENT_PX = 12;
    const EXPLORER_GIT_GRAPH_LANE_COUNT = 6;

    const EXPLORER_GIT_STATUS_LABELS = Object.freeze({
        modified: 'M',
        added: 'A',
        deleted: 'D',
        renamed: 'R',
        conflicted: 'U',
        untracked: '?',
        ignored: '!',
        clean: ''
    });

    function explorerPathExtension(path) {
        const name = String(path || '').toLowerCase();
        const dotIndex = name.lastIndexOf('.');
        return dotIndex >= 0 ? name.slice(dotIndex) : '';
    }

    function explorerPathFilename(path) {
        const parts = String(path || '').toLowerCase().split(/[\\/]/);
        return parts[parts.length - 1] || '';
    }

    function normalizeExplorerLanguage(language) {
        const normalized = String(language || '').toLowerCase().replace(/[^a-z0-9+#-]/g, '');
        if (normalized === 'bat' || normalized === 'cmd') return 'batch';
        if (normalized === 'js') return 'javascript';
        if (normalized === 'ts') return 'typescript';
        if (normalized === 'py') return 'python';
        if (normalized === 'sh' || normalized === 'bash') return 'shell';
        if (normalized === 'ps1') return 'powershell';
        if (normalized === 'md') return 'markdown';
        if (normalized === 'c++') return 'cpp';
        if (normalized === 'c#') return 'csharp';
        return normalized;
    }

    function explorerCodeLanguage(path) {
        const filename = explorerPathFilename(path);
        if (filename && EXPLORER_LANGUAGE_BY_FILENAME[filename]) {
            return EXPLORER_LANGUAGE_BY_FILENAME[filename];
        }
        const byExtension = EXPLORER_LANGUAGE_BY_EXTENSION[explorerPathExtension(path)];
        if (byExtension) {
            return byExtension;
        }
        const prefixMatch = Object.keys(EXPLORER_LANGUAGE_BY_FILENAME_PREFIX)
            .find((prefix) => filename.startsWith(prefix));
        return prefixMatch ? EXPLORER_LANGUAGE_BY_FILENAME_PREFIX[prefixMatch] : '';
    }

    function explorerLanguageClass(language) {
        return normalizeExplorerLanguage(language).replace(/[^a-z0-9-]/g, '');
    }

    function explorerFileTypeLabel(path, language = '') {
        const detectedLanguage = normalizeExplorerLanguage(language) || explorerCodeLanguage(path);
        if (detectedLanguage && EXPLORER_LANGUAGE_LABELS[detectedLanguage]) {
            return EXPLORER_LANGUAGE_LABELS[detectedLanguage];
        }
        return explorerPathExtension(path) === '.txt' ? 'Text file' : 'Text file';
    }

    /* Reuse the existing language classifiers to pick a compact leading icon for
       tree and Git rows. Categories map to token-driven colors + a distinct
       stroke glyph so mixed file lists are quick to scan; unknown types fall
       back to the plain document glyph. */
    const EXPLORER_FILE_ICON_CATEGORY_BY_LANGUAGE = Object.freeze({
        javascript: 'code',
        typescript: 'code',
        python: 'code',
        ruby: 'code',
        go: 'code',
        rust: 'code',
        java: 'code',
        c: 'code',
        cpp: 'code',
        csharp: 'code',
        php: 'code',
        swift: 'code',
        kotlin: 'code',
        lua: 'code',
        shell: 'shell',
        powershell: 'shell',
        batch: 'shell',
        json: 'data',
        jsonl: 'data',
        yaml: 'data',
        toml: 'data',
        ini: 'data',
        html: 'markup',
        xml: 'markup',
        css: 'style',
        markdown: 'markdown',
        config: 'config',
        dotenv: 'config',
        gitignore: 'config',
        dockerfile: 'config',
        makefile: 'config',
        sql: 'sql',
        log: 'log',
        text: 'doc'
    });

    const EXPLORER_FILE_ICON_GLYPHS = Object.freeze({
        doc: '<path d="M6 3.5h7.2L18.5 8.8V19.5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z"/><path d="M12.8 3.6V8a1 1 0 0 0 1 1h4"/><path d="M8 13h6"/><path d="M8 16h6"/>',
        code: '<path d="M9.3 8.5 6 12l3.3 3.5"/><path d="M14.7 8.5 18 12l-3.3 3.5"/><path d="M13.2 6.5 10.8 17.5"/>',
        shell: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M7 9.8 9.6 12 7 14.2"/><path d="M12.5 14.5h4.5"/>',
        data: '<path d="M10.2 4.8c-2 0-2.4 1.2-2.4 3S7.4 11 6 12c1.4 1 1.8 1.4 1.8 3.2s.4 4 2.4 4"/><path d="M13.8 4.8c2 0 2.4 1.2 2.4 3S16.6 11 18 12c-1.4 1-1.8 1.4-1.8 3.2s-.4 4-2.4 4"/>',
        markup: '<path d="M9 8 4.5 12 9 16"/><path d="M15 8 19.5 12 15 16"/>',
        style: '<path d="M9.8 4.5 7.8 19.5"/><path d="M16.2 4.5 14.2 19.5"/><path d="M5.5 9.2h13"/><path d="M5 14.8h13"/>',
        markdown: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M6.5 15V9l2.6 3 2.6-3v6"/><path d="M15.6 9v4.4"/><path d="M13.9 12.1 15.6 14l1.7-1.9"/>',
        config: '<circle cx="12" cy="12" r="3"/><path d="M12 4v2.2M12 17.8V20M4 12h2.2M17.8 12H20M6.3 6.3l1.6 1.6M16.1 16.1l1.6 1.6M17.7 6.3l-1.6 1.6M7.9 16.1l-1.6 1.6"/>',
        sql: '<ellipse cx="12" cy="6" rx="6.3" ry="2.5"/><path d="M5.7 6v6c0 1.4 2.8 2.5 6.3 2.5s6.3-1.1 6.3-2.5V6"/><path d="M5.7 12v6c0 1.4 2.8 2.5 6.3 2.5s6.3-1.1 6.3-2.5v-6"/>',
        log: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M7.5 9h2M7.5 12h2M7.5 15h2"/><path d="M12 9h4.5M12 12h4.5M12 15h3"/>'
    });

    function explorerFileTypeCategory(path, language = '') {
        const detected = normalizeExplorerLanguage(language) || explorerCodeLanguage(path);
        return EXPLORER_FILE_ICON_CATEGORY_BY_LANGUAGE[detected] || 'doc';
    }

    function explorerFileTypeIconHtml(path, language = '') {
        const category = explorerFileTypeCategory(path, language);
        const glyph = EXPLORER_FILE_ICON_GLYPHS[category] || EXPLORER_FILE_ICON_GLYPHS.doc;
        return `<span class="explorer-icon file type-${category}" aria-hidden="true">`
            + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
            + `stroke-linecap="round" stroke-linejoin="round" focusable="false">${glyph}</svg></span>`;
    }

    /* A truncated preview keeps either the head or the tail of the file
       (ISSUE-2026-020): logs retain their newest bytes, everything else keeps
       its opening bytes. Report which end, and how much, was retained. */
    function explorerPreviewTruncationLabel(data) {
        if (!data || !data.truncated) {
            return '';
        }
        const start = Number(data.preview_start_byte);
        const end = Number(data.preview_end_byte);
        const retained = (Number.isFinite(start) && Number.isFinite(end))
            ? Math.max(0, end - start)
            : NaN;
        const total = Number(data.total_size);
        const edge = data.preview_mode === 'tail' ? 'last' : 'first';
        const retainedLabel = Number.isFinite(retained) ? formatExplorerSize(retained) : '';
        const totalLabel = Number.isFinite(total) ? formatExplorerSize(total) : '';
        if (retainedLabel && totalLabel) {
            return `Showing the ${edge} ${retainedLabel} of ${totalLabel}`;
        }
        if (retainedLabel) {
            return `Showing the ${edge} ${retainedLabel}`;
        }
        return 'Preview truncated';
    }

    function explorerFileMetaParts(data, fileType) {
        const size = formatExplorerSize(data.size) || 'Unknown size';
        const modified = formatExplorerDate(data.modified);
        const metaParts = [fileType, size, data.encoding || 'utf-8'];
        if (modified) {
            metaParts.push(modified);
        }
        const truncationLabel = explorerPreviewTruncationLabel(data);
        if (truncationLabel) {
            metaParts.push(truncationLabel);
        }
        return metaParts;
    }

    async function explorerFindRangesAsync(content, query, token, maxMatches = EXPLORER_SEARCH_MAX_MATCHES) {
        const source = String(content || '');
        const needle = String(query || '');
        if (!source || !needle) {
            return { ranges: [], capped: false, cancelled: Boolean(token?.cancelled) };
        }

        const ranges = [];
        const normalizedNeedle = needle.toLowerCase();
        const stride = Math.max(EXPLORER_SEARCH_CHUNK_SIZE, normalizedNeedle.length);
        let lastYield = performance.now();

        for (let offset = 0; offset < source.length && ranges.length < maxMatches; offset += stride) {
            if (token?.cancelled) {
                return { ranges, capped: false, cancelled: true };
            }

            const chunkEnd = Math.min(source.length, offset + stride + normalizedNeedle.length - 1);
            const normalizedChunk = source.slice(offset, chunkEnd).toLowerCase();
            let localCursor = 0;
            while (localCursor < normalizedChunk.length && ranges.length < maxMatches) {
                const matchIndex = normalizedChunk.indexOf(normalizedNeedle, localCursor);
                if (matchIndex === -1) {
                    break;
                }
                const absoluteIndex = offset + matchIndex;
                if (absoluteIndex >= offset + stride && chunkEnd < source.length) {
                    break;
                }
                ranges.push({ start: absoluteIndex, end: absoluteIndex + needle.length });
                localCursor = matchIndex + Math.max(needle.length, 1);
            }

            if (performance.now() - lastYield >= EXPLORER_SEARCH_YIELD_MS) {
                await new Promise(resolve => window.setTimeout(resolve, 0));
                lastYield = performance.now();
            }
        }

        return {
            ranges,
            capped: ranges.length >= maxMatches,
            cancelled: Boolean(token?.cancelled)
        };
    }

    function explorerMarkedEscHtml(text, absoluteStart = 0, searchRanges = []) {
        const value = String(text || '');
        if (!value || !searchRanges.length) {
            return escHtml(value);
        }

        const absoluteEnd = absoluteStart + value.length;
        let output = '';
        let cursor = 0;
        searchRanges.forEach(range => {
            const start = Math.max(Number(range.start), absoluteStart);
            const end = Math.min(Number(range.end), absoluteEnd);
            if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
                return;
            }
            const localStart = start - absoluteStart;
            const localEnd = end - absoluteStart;
            if (localStart > cursor) {
                output += escHtml(value.slice(cursor, localStart));
            }
            const className = range.active ? 'explorer-search-match active' : 'explorer-search-match';
            output += `<mark class="${className}">${escHtml(value.slice(localStart, localEnd))}</mark>`;
            cursor = localEnd;
        });
        if (cursor < value.length) {
            output += escHtml(value.slice(cursor));
        }
        return output;
    }

    function explorerCodeSpan(className, text, absoluteStart = 0, searchRanges = []) {
        return `<span class="${className}">${explorerMarkedEscHtml(text, absoluteStart, searchRanges)}</span>`;
    }

    function explorerReadStringToken(content, start) {
        const quote = content[start];
        let index = start + 1;
        if (
            (quote === '"' || quote === "'")
            && content.slice(start, start + 3) === quote.repeat(3)
        ) {
            index = start + 3;
            while (index < content.length && content.slice(index, index + 3) !== quote.repeat(3)) {
                index += content[index] === '\\' ? 2 : 1;
            }
            return content.slice(start, Math.min(index + 3, content.length));
        }
        while (index < content.length) {
            if (content[index] === '\\') {
                index += 2;
                continue;
            }
            index += 1;
            if (content[index - 1] === quote) {
                break;
            }
        }
        return content.slice(start, index);
    }

    function explorerLogLevelClass(level) {
        const normalized = String(level || '').toUpperCase();
        if (normalized === 'TRACE' || normalized === 'DEBUG') {
            return normalized.toLowerCase();
        }
        if (normalized === 'WARN' || normalized === 'WARNING') {
            return 'warn';
        }
        if (normalized === 'ERROR' || normalized === 'CRITICAL' || normalized === 'FATAL') {
            return 'error';
        }
        return 'info';
    }

    function highlightExplorerLogLine(line, absoluteStart, searchRanges = []) {
        const timestampPattern = /^(\s*(?:\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?|\d{2}:\d{2}:\d{2}(?:[.,]\d+)?|\[[^\]\n]*(?:\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2})[^\]\n]*\]))/;
        const levelPattern = /\b(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL|FATAL)\b/i;
        const timestampMatch = line.match(timestampPattern);
        let output = '';
        let cursor = 0;

        if (timestampMatch) {
            const token = timestampMatch[1];
            output += explorerCodeSpan('explorer-log-timestamp', token, absoluteStart, searchRanges);
            cursor = token.length;
        }

        const levelMatch = line.slice(cursor).match(levelPattern);
        if (levelMatch && EXPLORER_LOG_LEVELS.has(levelMatch[1].toUpperCase())) {
            const levelStart = cursor + Number(levelMatch.index || 0);
            if (levelStart > cursor) {
                output += explorerMarkedEscHtml(line.slice(cursor, levelStart), absoluteStart + cursor, searchRanges);
            }
            const level = levelMatch[1];
            output += explorerCodeSpan(
                `explorer-log-level ${explorerLogLevelClass(level)}`,
                level,
                absoluteStart + levelStart,
                searchRanges
            );
            cursor = levelStart + level.length;
        }

        if (cursor < line.length) {
            output += explorerMarkedEscHtml(line.slice(cursor), absoluteStart + cursor, searchRanges);
        }
        return output;
    }

    function highlightExplorerLog(content, searchRanges = []) {
        const source = String(content || '');
        const absoluteStart = Number(arguments[2] || 0);
        let output = '';
        let index = 0;
        while (index < source.length) {
            const newlineIndex = source.indexOf('\n', index);
            const lineEnd = newlineIndex === -1 ? source.length : newlineIndex;
            output += highlightExplorerLogLine(source.slice(index, lineEnd), absoluteStart + index, searchRanges);
            if (newlineIndex === -1) {
                break;
            }
            output += explorerMarkedEscHtml('\n', absoluteStart + lineEnd, searchRanges);
            index = lineEnd + 1;
        }
        return output;
    }

    function highlightExplorerCode(content, language, searchRanges = []) {
        const normalizedLanguage = normalizeExplorerLanguage(language);
        const absoluteStart = Number(arguments[3] || 0);
        if (!normalizedLanguage) {
            return explorerMarkedEscHtml(content, absoluteStart, searchRanges);
        }

        if (normalizedLanguage === 'log') {
            return highlightExplorerLog(content, searchRanges, absoluteStart);
        }

        const keywords = new Set(EXPLORER_CODE_KEYWORDS[normalizedLanguage] || []);
        const builtins = new Set(EXPLORER_CODE_BUILTINS[normalizedLanguage] || []);
        const caseInsensitiveKeywords = normalizedLanguage === 'sql';
        let output = '';
        let index = 0;

        while (index < content.length) {
            const current = content[index];
            const next = content[index + 1] || '';

            if ((normalizedLanguage === 'html' || normalizedLanguage === 'xml') && content.startsWith('<!--', index)) {
                const endIndex = content.indexOf('-->', index + 4);
                const token = content.slice(index, endIndex === -1 ? content.length : endIndex + 3);
                output += explorerCodeSpan('explorer-code-comment', token, absoluteStart + index, searchRanges);
                index += token.length;
                continue;
            }

            if (EXPLORER_C_LIKE_LANGUAGES.has(normalizedLanguage) && current === '/' && next === '/') {
                const endIndex = content.indexOf('\n', index + 2);
                const token = content.slice(index, endIndex === -1 ? content.length : endIndex);
                output += explorerCodeSpan('explorer-code-comment', token, absoluteStart + index, searchRanges);
                index += token.length;
                continue;
            }

            if (EXPLORER_C_LIKE_LANGUAGES.has(normalizedLanguage) && current === '/' && next === '*') {
                const endIndex = content.indexOf('*/', index + 2);
                const token = content.slice(index, endIndex === -1 ? content.length : endIndex + 2);
                output += explorerCodeSpan('explorer-code-comment', token, absoluteStart + index, searchRanges);
                index += token.length;
                continue;
            }

            if (EXPLORER_HASH_COMMENT_LANGUAGES.has(normalizedLanguage) && current === '#') {
                const endIndex = content.indexOf('\n', index + 1);
                const token = content.slice(index, endIndex === -1 ? content.length : endIndex);
                output += explorerCodeSpan('explorer-code-comment', token, absoluteStart + index, searchRanges);
                index += token.length;
                continue;
            }

            if (current === '"' || current === "'" || (current === '`' && !['json', 'jsonl', 'yaml', 'toml'].includes(normalizedLanguage))) {
                const token = explorerReadStringToken(content, index);
                output += explorerCodeSpan('explorer-code-string', token, absoluteStart + index, searchRanges);
                index += token.length;
                continue;
            }

            if (/[0-9]/.test(current)) {
                const match = content.slice(index).match(/^(0x[\da-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/);
                if (match) {
                    output += explorerCodeSpan('explorer-code-number', match[0], absoluteStart + index, searchRanges);
                    index += match[0].length;
                    continue;
                }
            }

            if (/[A-Za-z_$]/.test(current)) {
                const match = content.slice(index).match(/^[A-Za-z_$][\w$-]*/);
                if (match) {
                    const token = match[0];
                    const keywordToken = caseInsensitiveKeywords ? token.toUpperCase() : token;
                    if (keywords.has(keywordToken)) {
                        output += explorerCodeSpan('explorer-code-keyword', token, absoluteStart + index, searchRanges);
                    } else if (builtins.has(token)) {
                        output += explorerCodeSpan('explorer-code-builtin', token, absoluteStart + index, searchRanges);
                    } else {
                        output += explorerMarkedEscHtml(token, absoluteStart + index, searchRanges);
                    }
                    index += token.length;
                    continue;
                }
            }

            output += explorerMarkedEscHtml(current, absoluteStart + index, searchRanges);
            index += 1;
        }

        return output;
    }

    /* Tokenize the whole document
       once with Highlight.js so multiline constructs (block comments, triple-
       quoted / template strings, embedded languages) keep their state across
       newlines — the per-line highlightExplorerCode lexer above cannot. Returns
       a Map keyed by 1-based line number, each value an array of styled runs
       ({ className, text, start }) whose `start` is the absolute character
       offset into `content` (so the existing offset-based search-mark machinery
       keeps working). Returns null — and the caller falls back to the
       handwritten lexer — when Highlight.js is unavailable, the language is not
       in the pinned build, the file is above the plain-preview threshold, or
       Highlight.js throws. */
    function explorerHighlightDocumentLines(content, normalizedLanguage) {
        const grammar = EXPLORER_HLJS_LANGUAGE[normalizedLanguage];
        if (!grammar) {
            return null;
        }
        const source = String(content || '');
        if (source.length > EXPLORER_PLAIN_PREVIEW_THRESHOLD) {
            return null;
        }
        const engine = typeof window !== 'undefined' ? window.hljs : null;
        if (!engine || typeof engine.highlight !== 'function'
            || typeof engine.getLanguage !== 'function' || !engine.getLanguage(grammar)) {
            return null;
        }

        let markup;
        try {
            markup = engine.highlight(source, { language: grammar, ignoreIllegal: true }).value;
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer syntax highlight failed:', error);
            return null;
        }

        const template = document.createElement('template');
        template.innerHTML = markup;

        const lines = new Map();
        let lineNumber = 1;
        let offset = 0;
        let current = [];
        lines.set(lineNumber, current);

        const pushText = (className, text) => {
            let segmentStart = 0;
            for (let i = 0; i < text.length; i += 1) {
                if (text[i] !== '\n') {
                    continue;
                }
                let segment = text.slice(segmentStart, i);
                const rawLength = segment.length;
                if (segment.endsWith('\r')) {
                    // Records strip the trailing CR of CRLF lines; match that for
                    // display while still counting it toward the raw offset.
                    segment = segment.slice(0, -1);
                }
                if (segment) {
                    current.push({ className, text: segment, start: offset });
                }
                offset += rawLength + 1;
                lineNumber += 1;
                current = [];
                lines.set(lineNumber, current);
                segmentStart = i + 1;
            }
            const tail = text.slice(segmentStart);
            if (tail) {
                current.push({ className, text: tail, start: offset });
                offset += tail.length;
            }
        };

        const walk = (node, className) => {
            node.childNodes.forEach(child => {
                if (child.nodeType === Node.TEXT_NODE) {
                    pushText(className, child.nodeValue || '');
                } else if (child.nodeType === Node.ELEMENT_NODE) {
                    // Innermost Highlight.js class wins the colour; the decoded
                    // text length equals the raw source so offsets stay aligned.
                    walk(child, child.getAttribute('class') || className);
                }
            });
        };
        walk(template.content, '');
        return lines;
    }

    /* Whole-document tokenization is the expensive part of a Source re-render,
       and re-renders that leave the content untouched are frequent: every
       search keystroke, wrap toggle and Markdown fold rebuilds the rows. Cache
       the token map on the pane, keyed by content + normalized language, so
       those re-renders reuse it; a real content change (file refresh, tab
       switch, editor save) misses the key and re-tokenizes. Cached `null`
       (unsupported language, oversized file, Highlight.js failure) is a valid
       hit — it must not trigger a re-tokenize on every render either. */
    function explorerHighlightDocumentLinesCached(pane, content, normalizedLanguage) {
        const cache = pane ? pane._explorerHighlightCache : null;
        if (cache && cache.content === content && cache.language === normalizedLanguage) {
            return cache.lines;
        }
        const lines = explorerHighlightDocumentLines(content, normalizedLanguage);
        if (pane) {
            pane._explorerHighlightCache = { content, language: normalizedLanguage, lines };
        }
        return lines;
    }

    /* An explicit third state beside a token Map and the cached-null fallback:
       a worker job is in flight for this buffer, so the first paint is plain
       escaped text. It must not fall through to the handwritten lexer — on a
       1.5 MiB minified line that lexer is another long main-thread task, which
       would defeat moving Highlight.js away in the first place.

       Strictly a *pending* state, never a resting one. A job that fails
       resolves to real tokens on this thread (see the catch below) rather than
       leaving the sentinel standing, because a sentinel that never lifts is a
       file the reader watches stay grey for as long as it is open. */
    const EXPLORER_HIGHLIGHT_PENDING = Symbol('explorer-highlight-pending');

    function explorerWorkerClient() {
        return (typeof window !== 'undefined' && window.GridVibeExplorerWorkers) || null;
    }

    /* Search ranges are absolute offsets into one exact string, so they are
       only meaningful against that string. Keying them on the query alone let
       a range set outlive the buffer it was resolved against — and the two
       buffers in play here differ by their line endings, because the in-place
       editor normalizes CRLF to LF for its draft while the file keeps its own.
       Painting one on the other put every mark a line-count of characters away
       from its match, walking further across each row and wrapping at the row
       length: a highlight that drifted diagonally down the file.

       The content is compared by reference-or-value against the buffer the
       rows are about to be built from, which is the only thing that makes the
       offsets mean what they say. Holding the string costs nothing — it is the
       same reference the pane already owns, exactly as the highlight cache
       holds its key. */
    function explorerSearchRangesMatchContent(state, pane) {
        return state.resultContent === (pane?._explorerFileContent || '');
    }

    function explorerSourceSearchRangesOnScreen(index, pane) {
        if (activeExplorerFileView(index) !== 'source') {
            return [];
        }
        const state = ensureExplorerSearchState(pane);
        if (!state.query
            || state.resultQuery !== state.query
            || !Array.isArray(state.ranges)
            || !explorerSearchRangesMatchContent(state, pane)) {
            return [];
        }
        return decorateExplorerSearchRanges(state.ranges, state.activeIndex || 0);
    }

    /* Small files keep the zero-startup-cost synchronous path. Above the
       worker client's measured floor, a cache miss starts one shared-pool job
       and returns the pending sentinel immediately. Repaints while it runs
       reuse that job; a different content/language identity aborts it through
       the same per-pane request slots file/diff loads use.

       The answer is committed only if it still describes the pane. A winning
       answer invalidates the render token and rebuilds with the current find
       decorations; a superseded answer never paints stale content. */
    function explorerHighlightLinesForRender(index, pane, content, normalizedLanguage) {
        const cache = pane ? pane._explorerHighlightCache : null;
        if (cache && cache.content === content && cache.language === normalizedLanguage) {
            // One cache shape, one meaning: a hit is the answer for this
            // buffer, whether the worker produced it or this thread did. The
            // failure path used to store a third state here — a "plain miss"
            // that read back as the pending sentinel forever.
            return cache.lines;
        }
        const previous = pane?._explorerHighlightPending;
        const grammar = EXPLORER_HLJS_LANGUAGE[normalizedLanguage];
        const workers = explorerWorkerClient();
        const source = String(content || '');
        if (!pane || !grammar || source.length > EXPLORER_PLAIN_PREVIEW_THRESHOLD
            || !workers?.canHighlight?.(source)) {
            if (previous) {
                pane._explorerHighlightPending = null;
                cancelExplorerRequestSlot(pane, 'highlight');
            }
            return explorerHighlightDocumentLinesCached(pane, content, normalizedLanguage);
        }

        if (previous && previous.content === content && previous.language === normalizedLanguage) {
            return EXPLORER_HIGHLIGHT_PENDING;
        }

        const pending = { content, language: normalizedLanguage };
        pane._explorerHighlightPending = pending;
        workers.highlight(source, grammar, {
            signal: explorerRequestSignal(pane, 'highlight')
        }).then(lines => {
            if (pane._explorerHighlightPending !== pending) {
                return;
            }
            pane._explorerHighlightPending = null;
            const currentLanguage = normalizeExplorerLanguage(
                pane._explorerFilePlain ? '' : (pane._explorerFileLanguage || '')
            );
            if (pane._explorerFileContent !== content || currentLanguage !== normalizedLanguage) {
                return;
            }
            pane._explorerHighlightCache = { content, language: normalizedLanguage, lines };
            /* The rows on screen are the deliberately plain first paint. The
               content did not move, so the normal repaint policy would skip;
               marking the record stale makes the syntax-coloured pass a
               rebuild. The record itself is *kept* — discarding it is what
               made the recolour look like a new document and drop the reader
               back to line 1 a beat after a large file finished opening. */
            if (pane._explorerSourceRender) {
                pane._explorerSourceRender.stale = true;
            }
            renderExplorerSource(index, explorerSourceSearchRangesOnScreen(index, pane));
        }).catch(error => {
            if (pane._explorerHighlightPending !== pending) {
                return;
            }
            pane._explorerHighlightPending = null;
            if (explorerIsAbortError(error)) {
                return;
            }
            console.error('[GridVibe Sessions] Explorer highlight worker failed:', error);
            const currentLanguage = normalizeExplorerLanguage(
                pane._explorerFilePlain ? '' : (pane._explorerFileLanguage || '')
            );
            if (pane._explorerFileContent !== content || currentLanguage !== normalizedLanguage) {
                return;
            }
            /* Fall back to exactly what a page with no worker support does:
               tokenize here, on this thread. Caching the miss instead left the
               *open* buffer permanently uncoloured — the pending sentinel
               renders plain escaped text and deliberately does not fall
               through to the per-line lexer, and nothing re-rendered — while
               the very next file recovered, because _failWorker() disables the
               pool and canHighlight() then routes it down this same
               synchronous path. One failure, two different answers for the
               same file depending on when it was opened.

               The size argument the pending sentinel is built on cannot fire
               here: the gate above only offers a buffer to the worker when it
               is *under* EXPLORER_PLAIN_PREVIEW_THRESHOLD, so anything
               reaching this catch is already a file the synchronous path is
               allowed to tokenize. The result is cached on the pane by the
               shared helper, so a disabled worker still does not turn every
               repaint into another pass. */
            explorerHighlightDocumentLinesCached(pane, content, normalizedLanguage);
            // Same staleness handshake as the success path: the content did
            // not move, so the repaint policy would otherwise skip, and the
            // record is kept rather than discarded so the recolour is not
            // mistaken for a new document.
            if (pane._explorerSourceRender) {
                pane._explorerSourceRender.stale = true;
            }
            renderExplorerSource(index, explorerSourceSearchRangesOnScreen(index, pane));
        });
        return EXPLORER_HIGHLIGHT_PENDING;
    }

    /* Render one line's worth of Highlight.js runs, reusing the shared
       escape+search-mark helpers so search marks coexist with syntax spans. */
    function explorerRenderHighlightedRuns(runs, searchRanges = []) {
        if (!runs || !runs.length) {
            return '';
        }
        let html = '';
        runs.forEach(run => {
            html += run.className
                ? explorerCodeSpan(run.className, run.text, run.start, searchRanges)
                : explorerMarkedEscHtml(run.text, run.start, searchRanges);
        });
        return html;
    }

    function renderExplorerMessage(index, message) {
        const pane = terminals[index];
        const list = document.getElementById(`explorer-list-${index}`);
        const viewer = explorerEnsureViewerShell(index);
        if (list && viewer) {
            // The placeholder replaces the tab's content: the DOM no longer
            // belongs to any tab, so view captures must skip until re-render.
            if (pane) {
                pane._explorerRenderedTabId = '';
            }
            list.classList.remove('file-view');
            viewer.innerHTML = `<div class="explorer-message">${escHtml(message)}</div>`;
            renderExplorerTabStrip(index);
        }
    }

    function renderExplorerDirectoryOpenError(index, message) {
        const pane = terminals[index];
        const viewer = explorerEnsureViewerShell(index);
        if (!pane || !viewer || pane._explorerMode !== 'directory') {
            renderExplorerMessage(index, message);
            return;
        }
        pane._explorerRenderedTabId = EXPLORER_PREVIEW_TAB_ID;
        renderExplorerDirectorySearchControls(index);
        renderExplorerDirectoryRows(index);
        const notice = document.createElement('div');
        notice.className = 'explorer-message explorer-file-open-error';
        notice.textContent = message;
        viewer.prepend(notice);
    }

    function explorerRootDirectory(index) {
        const session = terminals[index]?._session || {};
        return session.explorer_root_directory || session.directory || '';
    }

    function explorerJoinRootPath(root, relativePath) {
        const rel = String(relativePath || '').replace(/^[\\/]+/, '');
        const base = String(root || '');
        if (!base) {
            return rel;
        }
        // Match the root's separator style so a copied Windows path is not
        // mangled with forward slashes (and remote/POSIX roots stay POSIX).
        const usesBackslash = base.includes('\\') && !base.includes('/');
        const separator = usesBackslash ? '\\' : '/';
        const trimmedBase = base.replace(/[\\/]+$/, '') || base;
        if (!rel) {
            return trimmedBase;
        }
        const nativeRel = usesBackslash ? rel.replace(/\//g, '\\') : rel.replace(/\\/g, '/');
        return `${trimmedBase}${separator}${nativeRel}`;
    }

    /* ── Multi-entry selection: the DOM half ────────────────────────────────
       The rules live in explorer-selection.js (DOM-free, Node-tested). This
       side only reads rows out of the DOM, hands them to the model, and paints
       the answer back. Selections are per session id and deliberately not
       persisted — they are a pointer gesture, not pane state. */

    const explorerSelections = new Map();

    function explorerSelectionScope(index, surface) {
        return {
            sessionId: sessionIds[index] || '',
            rootRevision: terminals[index]?._explorerRootRevision || '',
            surface
        };
    }

    function storeExplorerSelection(index, selection) {
        const sessionId = sessionIds[index] || '';
        if (!sessionId) {
            return;
        }
        if (GridVibeExplorerSelection.isEmpty(selection)) {
            explorerSelections.delete(sessionId);
        } else {
            explorerSelections.set(sessionId, selection);
        }
        refreshExplorerSelectionHighlight(index);
    }

    function clearExplorerSelection(sessionId) {
        explorerSelections.delete(String(sessionId || ''));
    }

    /* Drop entries a completed mutation removed (deleted, or moved away),
       including anything that was beneath a removed directory. */
    function dropExplorerSelectionPaths(index, removedPaths) {
        const sessionId = sessionIds[index] || '';
        const selection = sessionId ? explorerSelections.get(sessionId) : null;
        if (GridVibeExplorerSelection.isEmpty(selection)) {
            return;
        }
        const next = GridVibeExplorerSelection.dropPaths(selection, removedPaths);
        if (GridVibeExplorerSelection.isEmpty(next)) {
            explorerSelections.delete(sessionId);
        } else {
            explorerSelections.set(sessionId, next);
        }
    }

    function explorerRowEntry(row) {
        return row ? {
            path: row.dataset.explorerContextPath || '',
            kind: row.dataset.explorerContextKind || '',
            revision: row.dataset.explorerContextRevision || ''
        } : null;
    }

    function explorerSurfaceContainer(index, surface) {
        return surface === 'tree'
            ? document.getElementById(`explorer-tree-panel-${index}`)
            : document.getElementById(`explorer-viewer-${index}`);
    }

    /* Rows in render order — the ordering a shift+click range is taken over, so
       it must be what the user actually sees (filtered, folded) rather than the
       underlying entry list. */
    function explorerOrderedRows(index, surface) {
        const container = explorerSurfaceContainer(index, surface);
        if (!container) {
            return [];
        }
        return Array.from(container.querySelectorAll('[data-explorer-context-path]'))
            .map(explorerRowEntry)
            .filter(entry => entry && entry.path);
    }

    /* Paint the stored selection onto whatever rows exist right now. Called
       after every render, so a reload, a filter keystroke, or a folded branch
       re-applies it without the selection itself knowing about the DOM. */
    function refreshExplorerSelectionHighlight(index) {
        const card = document.getElementById(`tc-${index}`);
        if (!card) {
            return;
        }
        /* Styling only, no `aria-selected`: these rows are plain buttons and
           divs, and that attribute is only valid on option/row/treeitem-style
           roles. Claiming one would mean also owning the listbox/tree keyboard
           model, which this does not implement — the menu names the count
           instead ("Delete 3 files…"), so the action is never ambiguous. */
        card.querySelectorAll('.explorer-selected').forEach(node => {
            node.classList.remove('explorer-selected');
        });
        const sessionId = sessionIds[index] || '';
        const selection = sessionId ? explorerSelections.get(sessionId) : null;
        if (GridVibeExplorerSelection.isEmpty(selection)) {
            return;
        }
        const scoped = GridVibeExplorerSelection.scopedSelection(
            selection,
            explorerSelectionScope(index, selection.surface)
        );
        if (!scoped) {
            // The root revision moved on: drop it rather than paint stale rows.
            explorerSelections.delete(sessionId);
            return;
        }
        const container = explorerSurfaceContainer(index, scoped.surface);
        if (!container) {
            return;
        }
        const selected = new Set(GridVibeExplorerSelection.selectionPaths(scoped));
        container.querySelectorAll('[data-explorer-context-path]').forEach(node => {
            if (selected.has(node.dataset.explorerContextPath)) {
                node.classList.add('explorer-selected');
            }
        });
    }

    /* Resolve a modifier click on a row. Returns true when the click was a
       selection gesture and the row's normal open/navigate action must not
       run. A plain click always returns false, so an explorer with nothing
       selected behaves exactly as it did before multi-select existed. */
    function handleExplorerRowSelectionClick(event, index, surface, row) {
        const entry = explorerRowEntry(row);
        if (!entry) {
            return false;
        }
        const { selection, activate } = GridVibeExplorerSelection.applyPointerSelection(
            explorerSelections.get(sessionIds[index] || '') || null,
            Object.assign(explorerSelectionScope(index, surface), {
                entry,
                ctrlKey: event.ctrlKey,
                metaKey: event.metaKey,
                shiftKey: event.shiftKey
            }),
            explorerOrderedRows(index, surface)
        );
        storeExplorerSelection(index, selection);
        return !activate;
    }

    /* Escape clears the selection.

       Which pane it means comes from findExplorerShortcutTargetIndex() — the
       same pointer-first resolver Ctrl+Shift+F and the explorer's other
       shortcuts already use. Focus alone is not enough: most explorer controls
       suppress mousedown focus so a toolbar click cannot steal a selection, and
       clicking blank space in the listing leaves focus on nothing at all, so a
       focus-scoped Escape stopped working the moment the user clicked anywhere
       but a row. Pointer interaction is what the user actually means by "this
       pane", and it survives all of that.

       These are the dialogs and menus that close on Escape without marking the
       event handled; the editor, the find bars and the context menu all
       preventDefault, which the model reads separately. */
    const EXPLORER_ESCAPE_CLAIM_SELECTOR = [
        '.modal-shell.visible',
        '.terminal-container.actions-open',
        '.pane-shell-menu:not([hidden])',
        '#sessionsMenuRoot.open',
        '#workspaceMenuRoot.open',
        '#workspaceContextMenu:not([hidden])'
    ].join(', ');

    function handleExplorerSelectionEscape(event) {
        /* Cheap gate before any DOM work: this listener sees every keystroke in
           the window, and the pane lookup and claim query below must not run
           once per character typed into a terminal or the editor. The model
           still owns the decision — this only says "not our key at all". */
        if (event.key !== 'Escape' || typeof findExplorerShortcutTargetIndex !== 'function') {
            return;
        }
        const index = findExplorerShortcutTargetIndex(event.target);
        if (index === -1) {
            return;
        }
        const sessionId = sessionIds[index] || '';
        const target = event.target;
        const decision = GridVibeExplorerSelection.shouldClearOnEscape({
            key: event.key,
            defaultPrevented: event.defaultPrevented,
            claimedElsewhere: Boolean(
                document.querySelector(EXPLORER_ESCAPE_CLAIM_SELECTOR)
            ),
            altKey: event.altKey,
            ctrlKey: event.ctrlKey,
            metaKey: event.metaKey,
            shiftKey: event.shiftKey,
            editableTarget: Boolean(
                target?.isContentEditable
                || target?.matches?.('input, textarea, select')
            ),
            hasSelection: !GridVibeExplorerSelection.isEmpty(
                sessionId ? explorerSelections.get(sessionId) : null
            )
        });
        if (!decision) {
            return;
        }
        event.preventDefault();
        storeExplorerSelection(index, null);
        releaseExplorerRowFocus(index);
    }

    /* A row keeps DOM focus after a Ctrl+click, and `.explorer-row:focus-visible`
       paints the same `--explorer-row-active` fill the selection does — which
       Chrome turns on the moment a key is pressed. So clearing with Escape left
       exactly one row still looking selected. Drop the focus, then re-assert the
       pane as the active explorer so the *next* Escape still resolves here. */
    function releaseExplorerRowFocus(index) {
        const active = document.activeElement;
        const card = document.getElementById(`tc-${index}`);
        if (active?.blur && card?.contains(active) && active.closest('[data-explorer-context-path]')) {
            active.blur();
        }
        if (typeof markActiveExplorerPane === 'function') {
            markActiveExplorerPane(index);
        }
    }

    function installExplorerSelectionEscape() {
        document.addEventListener('keydown', handleExplorerSelectionEscape);
    }

    installExplorerSelectionEscape();

    let _explorerContextMenuInvoker = null;

    function dismissExplorerContextMenu() {
        const { restoreFocus = true } = arguments[0] || {};
        document.getElementById('explorer-ctx-menu')?.remove();
        document.removeEventListener('keydown', _explorerContextMenuKeydown, true);
        document.removeEventListener('mousedown', _explorerContextMenuOutside, true);
        document.querySelectorAll('.explorer-context-target')
            .forEach(node => node.classList.remove('explorer-context-target'));
        const invoker = _explorerContextMenuInvoker;
        _explorerContextMenuInvoker = null;
        if (restoreFocus && invoker?.isConnected) {
            invoker.focus({ preventScroll: true });
        }
    }

    function _explorerContextMenuOutside(event) {
        const menu = document.getElementById('explorer-ctx-menu');
        if (!menu || !menu.contains(event.target)) {
            dismissExplorerContextMenu();
        }
    }

    function _explorerContextMenuKeydown(event) {
        const menu = document.getElementById('explorer-ctx-menu');
        if (!menu) {
            return;
        }
        const items = Array.from(menu.querySelectorAll('button:not(:disabled)'));
        if (!items.length) {
            return;
        }
        const currentIndex = items.indexOf(document.activeElement);
        if (event.key === 'Escape') {
            event.preventDefault();
            dismissExplorerContextMenu();
        } else if (event.key === 'ArrowDown') {
            event.preventDefault();
            items[(currentIndex + 1) % items.length].focus();
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            items[(currentIndex - 1 + items.length) % items.length].focus();
        } else if (event.key === 'Tab') {
            event.preventDefault();
        }
    }

    function showExplorerContextMenu(x, y, items) {
        dismissExplorerContextMenu();
        const menu = document.createElement('div');
        menu.id = 'explorer-ctx-menu';
        menu.setAttribute('role', 'menu');
        items.forEach(({
            label,
            action,
            disabled = false,
            danger = false,
            separatorBefore = false,
            title = ''
        }) => {
            if (separatorBefore) {
                const separator = document.createElement('div');
                separator.className = 'explorer-ctx-separator';
                separator.setAttribute('role', 'separator');
                menu.appendChild(separator);
            }
            const button = document.createElement('button');
            button.type = 'button';
            button.setAttribute('role', 'menuitem');
            button.textContent = label;
            button.disabled = Boolean(disabled);
            button.setAttribute('aria-disabled', disabled ? 'true' : 'false');
            if (danger) {
                button.classList.add('danger');
            }
            if (title) {
                button.title = title;
            }
            if (!disabled && typeof action === 'function') {
                button.addEventListener('click', () => {
                    const result = action();
                    dismissExplorerContextMenu();
                    Promise.resolve(result).catch(error => {
                        console.error('[GridVibe Sessions] Explorer menu action failed:', error);
                    });
                });
            }
            menu.appendChild(button);
        });
        menu.style.visibility = 'hidden';
        document.body.appendChild(menu);

        const rect = menu.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        menu.style.left = `${Math.max(8, Math.min(x, vw - rect.width - 8))}px`;
        menu.style.top = `${Math.max(8, Math.min(y, vh - rect.height - 8))}px`;
        menu.style.visibility = 'visible';
        const initialItem = Array.from(menu.querySelectorAll('button:not(:disabled)'))
            .find(button => !button.classList.contains('danger'));
        initialItem?.focus();

        // Defer the outside-dismiss listener so the opening interaction does not
        // immediately close the menu.
        window.setTimeout(() => {
            document.addEventListener('mousedown', _explorerContextMenuOutside, true);
        }, 0);
        document.addEventListener('keydown', _explorerContextMenuKeydown, true);
    }

    /* A commit row names a repository object, not a path under the explorer
       root, so it takes its own branch: no selection, no filesystem entries,
       and no path copies. The expanded file rows below a commit are siblings
       of this button rather than children, so they still reach the entry
       menu below. */
    function handleExplorerCommitContextMenu(event, commitRow) {
        event.preventDefault();
        document.querySelectorAll('.explorer-context-target')
            .forEach(node => node.classList.remove('explorer-context-target'));
        commitRow.classList.add('explorer-context-target');
        _explorerContextMenuInvoker = commitRow;
        const items = window.GridVibeExplorerGitMenu.commitMenuItems({
            hash: commitRow.dataset.explorerGitCommitToggle || '',
            fullHash: commitRow.dataset.explorerGitCommitFull || '',
            message: commitRow.dataset.explorerGitCommitMessage || ''
        }, _copyText);
        let x = event.clientX;
        let y = event.clientY;
        if (x <= 0 && y <= 0) {
            const rect = commitRow.getBoundingClientRect();
            x = rect.left + Math.min(24, rect.width);
            y = rect.top + Math.min(rect.height, 24);
        }
        showExplorerContextMenu(x, y, items);
    }

    function handleExplorerContextMenu(event, index) {
        const commitRow = event.target.closest('[data-explorer-git-commit-toggle]');
        if (commitRow) {
            handleExplorerCommitContextMenu(event, commitRow);
            return;
        }
        const row = event.target.closest('[data-explorer-copy-path]');
        const pane = terminals[index];
        let blankContext = null;
        let blankTarget = null;
        if (!row) {
            const treePanel = event.target.closest(`#explorer-tree-panel-${index}`);
            const viewer = event.target.closest(`#explorer-viewer-${index}`);
            if (treePanel) {
                blankTarget = treePanel;
                blankContext = {
                    path: '',
                    kind: 'directory',
                    revision: '',
                    surface: 'tree-blank'
                };
            } else if (viewer && pane?._explorerMode === 'directory') {
                blankTarget = viewer;
                blankContext = {
                    path: pane._explorerPath || '',
                    kind: 'directory',
                    revision: '',
                    surface: 'preview-blank'
                };
            }
        }
        if (!row && !blankContext) {
            return;
        }
        event.preventDefault();
        document.querySelectorAll('.explorer-context-target')
            .forEach(node => node.classList.remove('explorer-context-target'));
        row?.classList.add('explorer-context-target');
        const target = row || blankTarget;
        _explorerContextMenuInvoker = row
            ? (row.matches('button') ? row : (row.querySelector('button') || row))
            : blankTarget;
        const relativePath = row
            ? (row.dataset.explorerCopyPath || '')
            : blankContext.path;
        const absolutePath = explorerJoinRootPath(explorerRootDirectory(index), relativePath);
        /* Right-clicking a row that is part of the live selection acts on the
           whole selection; anything else collapses to that row alone, so a
           forgotten selection elsewhere can never be swept into a delete the
           user aimed at one file. The model owns that rule. */
        const rowSurface = row?.dataset.explorerContextSurface || '';
        const contextSurface = rowSurface === 'tree' ? 'tree' : 'preview';
        const resolved = GridVibeExplorerSelection.resolveContextTargets(
            explorerSelections.get(sessionIds[index] || '') || null,
            Object.assign(explorerSelectionScope(index, contextSurface), {
                entry: explorerRowEntry(row)
            })
        );
        storeExplorerSelection(index, resolved.selection);
        const selectedTargets = resolved.targets;
        let filesystemItems = [];
        if (typeof explorerFilesystemMenuItems === 'function') {
            if (row?.dataset.explorerContextKind) {
                filesystemItems = explorerFilesystemMenuItems(index, {
                    path: row.dataset.explorerContextPath || relativePath,
                    kind: row.dataset.explorerContextKind || '',
                    revision: row.dataset.explorerContextRevision || '',
                    surface: rowSurface
                }, selectedTargets);
            } else if (blankContext) {
                filesystemItems = explorerFilesystemMenuItems(index, blankContext);
            }
        }
        const beforePath = filesystemItems.filter(item => item.placement !== 'after-path');
        const afterPath = filesystemItems.filter(item => item.placement === 'after-path');
        /* With several rows selected the path entries copy the whole set, one
           path per line — the same read the single-row entries perform. */
        const multiTarget = selectedTargets.length > 1;
        const targetRoot = explorerRootDirectory(index);
        const pathItems = multiTarget
            ? [{
                label: `Copy ${selectedTargets.length} paths`,
                action: () => _copyText(selectedTargets
                    .map(entry => explorerJoinRootPath(targetRoot, entry.path) || entry.path)
                    .join('\n'))
            }, {
                label: `Copy ${selectedTargets.length} relative paths`,
                action: () => _copyText(selectedTargets.map(entry => entry.path).join('\n'))
            }]
            : [{ label: 'Copy path', action: () => _copyText(absolutePath || relativePath) }];
        if (!multiTarget && relativePath) {
            pathItems.push({ label: 'Copy relative path', action: () => _copyText(relativePath) });
        }
        /* Downloading is a read, so it belongs with the copy entries. It is
           offered per row (not only for the open file) because a format the
           viewer can't render never reaches editor mode and its toolbar
           download button. Folders have no download endpoint, so a mixed
           selection offers only the files in it. */
        const downloadTargets = multiTarget
            ? selectedTargets.filter(entry => entry.kind === 'file')
            : (row?.dataset.explorerDownloadPath
                ? [{ path: row.dataset.explorerDownloadPath, kind: 'file' }]
                : []);
        if (downloadTargets.length === 1) {
            pathItems.push({
                label: 'Download file',
                title: `Download ${downloadTargets[0].path}`,
                action: () => downloadExplorerFile(index, { path: downloadTargets[0].path })
            });
        } else if (downloadTargets.length > 1) {
            pathItems.push({
                label: `Download ${downloadTargets.length} files`,
                title: downloadTargets.length === selectedTargets.length
                    ? `Download the ${downloadTargets.length} selected files`
                    : `Download the ${downloadTargets.length} files in the selection; folders are skipped`,
                action: () => downloadExplorerFiles(index, downloadTargets)
            });
        }
        if (beforePath.length) {
            pathItems[0].separatorBefore = true;
        }
        if (afterPath.length) {
            afterPath[0].separatorBefore = true;
        }
        const items = [...beforePath, ...pathItems, ...afterPath];
        let x = event.clientX;
        let y = event.clientY;
        if (x <= 0 && y <= 0) {
            const rect = target.getBoundingClientRect();
            x = rect.left + Math.min(24, rect.width);
            y = rect.top + Math.min(rect.height, 24);
        }
        showExplorerContextMenu(x, y, items);
    }

    function wireExplorerContextMenu(panel, index) {
        if (!panel || panel.dataset.contextMenuWired === 'true') {
            return;
        }
        panel.dataset.contextMenuWired = 'true';
        panel.addEventListener('contextmenu', event => handleExplorerContextMenu(event, index));
    }

    function wireExplorerCopyPathMenu(panel, index) {
        wireExplorerContextMenu(panel, index);
    }

    /* ── Commit-message find (Graph section) ──
       The Source find's smaller twin: one query over the loaded commit
       subjects, the same counter, the same ↑/↓/× controls, the same
       Enter/Shift+Enter keys, and the same <mark> paint. The matching itself
       lives in the DOM-free explorer-git-search.js; what is here only paints
       rows already on screen. State is per pane and runtime-only — like the
       Source find's, it is never persisted.

       The bar is folded away behind the Graph header's magnifier, so it costs
       the sidebar nothing until it is asked for; the same button and Escape
       close it again. It is always rendered and hidden by attribute rather
       than added and removed, for the reason the panel paints instead of
       re-rendering: revealing the bar must not take the caret out of the
       commit-message textarea above it. */
    /* Ordered sidebar panel registry. The sidebar stacks any subset of these
       in registry order with a 6px splitter between each adjacent open pair.
       Every open panel except the last can carry a pinned pixel height in
       pane._explorerSidebarPanelHeights (panel key → px); unpinned panels and
       the last open one share the remaining space as flex tracks. */
    const EXPLORER_SIDEBAR_PANELS = [
        {
            key: 'tree',
            openFlag: '_explorerTreeSidebarOpen',
            panelId: index => `explorer-tree-panel-${index}`,
            buttonId: index => `explorer-tree-toggle-${index}`,
            mainClass: 'tree-open',
            onOpen: index => loadExplorerTree(index)
        },
        {
            key: 'git',
            openFlag: '_explorerGitSidebarOpen',
            panelId: index => `explorer-git-panel-${index}`,
            buttonId: index => `explorer-git-toggle-${index}`,
            mainClass: 'git-open',
            onOpen: index => loadExplorerGitRepo(index)
        },
        {
            key: 'search',
            openFlag: '_explorerSearchSidebarOpen',
            panelId: index => `explorer-search-panel-${index}`,
            buttonId: index => `explorer-search-toggle-${index}`,
            mainClass: 'search-open',
            /* Search results are pane-scoped and never persisted; opening only
               (re)builds the panel chrome (see explorer-search.js, loaded
               before terminals.js — the reference resolves at call time). */
            onOpen: index => (typeof renderExplorerSearchPanel === 'function'
                ? renderExplorerSearchPanel(index)
                : null)
        }
    ];

    function explorerOpenSidebarPanels(pane) {
        return EXPLORER_SIDEBAR_PANELS.filter(panel => Boolean(pane?.[panel.openFlag]));
    }

    /* Splitters sit at fixed DOM slots between adjacent registry panels, so
       slot n is only usable when panel n is open and something below it is
       open too. With Files closed, the Git/Search pair must use slot 1, not
       slot 0 — otherwise the divider renders above the first visible panel
       and the grid tracks land on the wrong elements. Each entry also carries
       the position, within the open set, of the panel the splitter drags. */
    function explorerSidebarSplitterSlots(pane) {
        const slots = [];
        let openSoFar = 0;
        EXPLORER_SIDEBAR_PANELS.forEach((panel, n) => {
            if (!pane?.[panel.openFlag]) {
                return;
            }
            openSoFar += 1;
            const hasOpenBelow = EXPLORER_SIDEBAR_PANELS
                .slice(n + 1)
                .some(entry => Boolean(pane?.[entry.openFlag]));
            if (hasOpenBelow) {
                slots.push({ slot: n, aboveIndex: openSoFar - 1 });
            }
        });
        return slots;
    }

    function ensureExplorerSidebarPanelHeights(pane) {
        if (!(pane._explorerSidebarPanelHeights instanceof Map)) {
            pane._explorerSidebarPanelHeights = new Map();
        }
        return pane._explorerSidebarPanelHeights;
    }

    function syncExplorerSidebar(index) {
        const pane = terminals[index];
        const main = document.getElementById(`explorer-main-${index}`);
        const sidebar = document.getElementById(`explorer-sidebar-${index}`);
        if (!pane || !main || !sidebar) {
            return;
        }

        const openPanels = explorerOpenSidebarPanels(pane);
        const openCount = openPanels.length;
        EXPLORER_SIDEBAR_PANELS.forEach(panel => {
            const isOpen = openPanels.includes(panel);
            const el = document.getElementById(panel.panelId(index));
            if (el) {
                el.hidden = !isOpen;
            }
            const button = document.getElementById(panel.buttonId(index));
            if (button) {
                button.setAttribute('aria-pressed', isOpen ? 'true' : 'false');
            }
            main.classList.toggle(panel.mainClass, isOpen);
        });
        sidebar.hidden = openCount === 0;
        sidebar.dataset.panels = String(openCount);
        const visibleSlots = new Set(explorerSidebarSplitterSlots(pane).map(entry => entry.slot));
        for (let n = 0; n < EXPLORER_SIDEBAR_PANELS.length - 1; n += 1) {
            const splitter = document.getElementById(`explorer-sidebar-splitter-${index}-${n}`);
            if (splitter) {
                splitter.hidden = !visibleSlots.has(n);
            }
        }
        const handle = document.getElementById(`explorer-sidebar-resizer-${index}`);
        if (handle) {
            handle.hidden = openCount === 0;
        }

        if (openCount > 0) {
            applyExplorerSidebarWidth(index);
            wireExplorerSidebarResize(index);
            wireExplorerSidebarPresentation(index);
        }
        /* Always re-apply, including below two panels: closing one back down
           to a single panel has to clear the inline grid-template-rows, or
           the survivor keeps the pixel height it held in the split and the
           rest of the sidebar stays blank and unresizable. */
        applyExplorerSidebarSplit(index);
        wireExplorerSidebarSplitter(index);
    }

    function restoreExplorerSidebarState(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        syncExplorerSidebar(index);
        explorerOpenSidebarPanels(pane).forEach(panel => {
            panel.onOpen?.(index);
        });
        restoreExplorerSidebarPresentation(index);
    }

    /* Returns whatever the panel's onOpen hook returns (a promise for the
       panels that load), so a caller that needs the panel populated — the
       tab strip's locate-in-tree gesture — can await it. */
    function setExplorerSidebarPanelOpen(index, key, open) {
        const panel = EXPLORER_SIDEBAR_PANELS.find(entry => entry.key === key);
        const pane = terminals[index];
        if (!panel || !pane) {
            return undefined;
        }
        pane[panel.openFlag] = Boolean(open);
        syncExplorerSidebar(index);
        notePanePresentationChanged(index);
        return pane[panel.openFlag] ? panel.onOpen?.(index) : undefined;
    }

    function setExplorerGitSidebarOpen(index, open) {
        return setExplorerSidebarPanelOpen(index, 'git', open);
    }

    function toggleExplorerGitSidebar(index) {
        const pane = terminals[index];
        setExplorerGitSidebarOpen(index, !pane?._explorerGitSidebarOpen);
    }

    function setExplorerTreeSidebarOpen(index, open) {
        return setExplorerSidebarPanelOpen(index, 'tree', open);
    }

    function toggleExplorerTreeSidebar(index) {
        const pane = terminals[index];
        setExplorerTreeSidebarOpen(index, !pane?._explorerTreeSidebarOpen);
    }

    function applyExplorerSidebarWidth(index) {
        const pane = terminals[index];
        const main = document.getElementById(`explorer-main-${index}`);
        if (!pane || !main) {
            return;
        }
        const width = Math.max(180, Math.min(Number(pane._explorerSidebarWidth || 260), 520));
        pane._explorerSidebarWidth = width;
        main.style.setProperty('--explorer-sidebar-width', `${width}px`);
    }

    /* `pane` is passed explicitly by callers that describe a group which is not
       the one mounted in the grid: a cached group's panes are detached, so
       `terminals.indexOf()` answers -1 for them and resolving by slot alone
       reported this whole function's defaults — collapsing every background
       workspace tab's tree and Git expansion on the next save. The index is
       still used, but only for the live scroll read, which correctly finds
       nothing for a detached pane and leaves the stored point in place. */
    function explorerSidebarPresentation(index, pane = terminals[index]) {
        if (!pane) return { width: 260, scroll: {}, expanded: [], gitExpanded: [] };
        ensureExplorerTreeState(pane);
        const scroll = { ...(pane._explorerSidebarScroll || {}) };
        ['tree', 'git'].forEach(panel => {
            const metrics = captureScrollMetrics(
                document.getElementById(`explorer-${panel}-panel-${index}`)
            );
            const point = window.GridVibeExplorerPersistence?.scrollPoint(metrics);
            if (point) scroll[panel] = point;
        });
        pane._explorerSidebarScroll = scroll;
        return {
            width: Math.max(180, Math.min(Number(pane._explorerSidebarWidth || 260), 520)),
            scroll,
            expanded: Array.from(pane._explorerTreeExpanded).slice(0, 128),
            gitExpanded: Array.from(ensureExplorerDiffExpandedCommits(pane)).slice(0, 128)
        };
    }

    function restoreExplorerSidebarPresentation(index) {
        const pane = terminals[index];
        if (!pane) return;
        applyExplorerSidebarWidth(index);
        const apply = () => {
            ['tree', 'git'].forEach(panel => {
                const point = pane._explorerSidebarScroll?.[panel];
                const metrics = window.GridVibeExplorerPersistence?.scrollMetrics(point);
                applyScrollMetrics(
                    document.getElementById(`explorer-${panel}-panel-${index}`),
                    metrics
                );
            });
        };
        apply();
        requestAnimationFrame(apply);
        window.setTimeout(apply, 80);
    }

    function wireExplorerSidebarPresentation(index) {
        ['tree', 'git'].forEach(panel => {
            const element = document.getElementById(`explorer-${panel}-panel-${index}`);
            if (!element || element.dataset.presentationBound) return;
            element.dataset.presentationBound = 'true';
            element.addEventListener('scroll', () => {
                explorerSidebarPresentation(index);
                notePanePresentationChanged(index, { continuous: true });
            }, { passive: true });
        });
    }

    function wireExplorerSidebarResize(index) {
        const pane = terminals[index];
        const main = document.getElementById(`explorer-main-${index}`);
        const handle = document.getElementById(`explorer-sidebar-resizer-${index}`);
        if (!pane || !main || !handle) {
            return;
        }
        applyExplorerSidebarWidth(index);
        if (handle.dataset.bound) {
            return;
        }

        handle.dataset.bound = 'true';
        handle.addEventListener('pointerdown', event => {
            event.preventDefault();
            handle.classList.add('dragging');
            handle.setPointerCapture?.(event.pointerId);
            const onMove = moveEvent => {
                const rect = main.getBoundingClientRect();
                const maxWidth = Math.min(520, Math.max(180, rect.width - 280));
                const nextWidth = Math.max(180, Math.min(Math.round(moveEvent.clientX - rect.left), maxWidth));
                pane._explorerSidebarWidth = nextWidth;
                main.style.setProperty('--explorer-sidebar-width', `${nextWidth}px`);
            };
            const onEnd = endEvent => {
                handle.classList.remove('dragging');
                handle.releasePointerCapture?.(endEvent.pointerId);
                notePanePresentationChanged(index, { continuous: true });
                window.removeEventListener('pointermove', onMove);
                window.removeEventListener('pointerup', onEnd);
                window.removeEventListener('pointercancel', onEnd);
            };
            window.addEventListener('pointermove', onMove);
            window.addEventListener('pointerup', onEnd, { once: true });
            window.addEventListener('pointercancel', onEnd, { once: true });
        });
    }

    /* Writes grid-template-rows from the open panel set: pinned panels get
       their stored pixel height (clamped so the panels below keep the
       minimum), unpinned panels and the last open one stay flex. A sidebar
       too short for the minimums degrades to an even flex split and each
       panel scrolls internally. */
    function applyExplorerSidebarSplit(index) {
        const pane = terminals[index];
        const sidebar = document.getElementById(`explorer-sidebar-${index}`);
        if (!pane || !sidebar) {
            return;
        }
        const openPanels = explorerOpenSidebarPanels(pane);
        if (openPanels.length < 2) {
            sidebar.style.removeProperty('grid-template-rows');
            return;
        }
        const heights = ensureExplorerSidebarPanelHeights(pane);
        const height = sidebar.getBoundingClientRect().height;
        const available = height - 6 * (openPanels.length - 1);
        const minTotal = EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT * openPanels.length;
        const tracks = [];
        if (!(available >= minTotal)) {
            for (let n = 0; n < openPanels.length; n += 1) {
                if (n > 0) {
                    tracks.push('6px');
                }
                tracks.push('minmax(0,1fr)');
            }
            sidebar.style.gridTemplateRows = tracks.join(' ');
            return;
        }
        let remaining = available;
        let panelsBelow = openPanels.length;
        for (let n = 0; n < openPanels.length; n += 1) {
            if (n > 0) {
                tracks.push('6px');
            }
            panelsBelow -= 1;
            const stored = Number(heights.get(openPanels[n].key) || 0);
            if (stored <= 0 || panelsBelow === 0) {
                tracks.push('minmax(0,1fr)');
                continue;
            }
            const maxForPanel = remaining - EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT * panelsBelow;
            const clamped = Math.max(EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT, Math.min(stored, maxForPanel));
            heights.set(openPanels[n].key, clamped);
            remaining -= clamped;
            tracks.push(`${clamped}px`);
        }
        sidebar.style.gridTemplateRows = tracks.join(' ');
    }

    function wireExplorerSidebarSplitter(index) {
        const pane = terminals[index];
        const sidebar = document.getElementById(`explorer-sidebar-${index}`);
        if (!pane || !sidebar) {
            return;
        }
        for (let n = 0; n < EXPLORER_SIDEBAR_PANELS.length - 1; n += 1) {
            const splitter = document.getElementById(`explorer-sidebar-splitter-${index}-${n}`);
            if (!splitter || splitter.dataset.bound) {
                continue;
            }
            splitter.dataset.bound = 'true';
            splitter.addEventListener('pointerdown', event => {
                const openPanels = explorerOpenSidebarPanels(pane);
                /* The DOM slot is not the position in the open set — resolve
                   which open panel actually sits above this splitter. */
                const slot = explorerSidebarSplitterSlots(pane).find(entry => entry.slot === n);
                if (!slot) {
                    return;
                }
                const above = slot.aboveIndex;
                event.preventDefault();
                splitter.classList.add('dragging');
                splitter.setPointerCapture?.(event.pointerId);
                const heights = ensureExplorerSidebarPanelHeights(pane);
                const panelEl = document.getElementById(openPanels[above].panelId(index));
                const startHeight = panelEl ? panelEl.getBoundingClientRect().height : 0;
                const startClientY = event.clientY;
                const panelsBelow = openPanels.length - 1 - above;
                const onMove = moveEvent => {
                    const rect = sidebar.getBoundingClientRect();
                    let prefix = 0;
                    for (let k = 0; k < above; k += 1) {
                        const aboveEl = document.getElementById(openPanels[k].panelId(index));
                        prefix += aboveEl ? aboveEl.getBoundingClientRect().height : 0;
                    }
                    const maxNext = rect.height - 6 * (openPanels.length - 1) - prefix
                        - EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT * panelsBelow;
                    const next = Math.max(
                        EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT,
                        Math.min(Math.round(startHeight + (moveEvent.clientY - startClientY)), maxNext)
                    );
                    heights.set(openPanels[above].key, next);
                    applyExplorerSidebarSplit(index);
                };
                const onEnd = endEvent => {
                    splitter.classList.remove('dragging');
                    splitter.releasePointerCapture?.(endEvent.pointerId);
                    window.removeEventListener('pointermove', onMove);
                    window.removeEventListener('pointerup', onEnd);
                    window.removeEventListener('pointercancel', onEnd);
                };
                window.addEventListener('pointermove', onMove);
                window.addEventListener('pointerup', onEnd, { once: true });
                window.addEventListener('pointercancel', onEnd, { once: true });
            });
        }
    }

    function ensureExplorerTreeState(pane) {
        if (!(pane._explorerTreeExpanded instanceof Set)) {
            pane._explorerTreeExpanded = new Set();
        }
        if (!(pane._explorerTreeChildren instanceof Map)) {
            pane._explorerTreeChildren = new Map();
        }
        if (!(pane._explorerTreeErrors instanceof Map)) {
            pane._explorerTreeErrors = new Map();
        }
        if (!(pane._explorerTreeLoading instanceof Set)) {
            pane._explorerTreeLoading = new Set();
        }
        return pane;
    }

    /* `refresh` re-reads a directory the tree has already cached. The cached
       rows stay on screen for the whole round trip — a re-read the reader did
       not ask for must not blank the folder they are looking at — so the
       loading placeholder is only rendered when there is nothing to show. */
    async function loadExplorerTreeChildren(index, path, { refresh = false } = {}) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId) {
            return [];
        }

        ensureExplorerTreeState(pane);
        const key = String(path || '');
        const cached = pane._explorerTreeChildren.get(key);
        if (cached && !refresh) {
            return cached;
        }
        if (pane._explorerTreeLoading.has(key)) {
            return [];
        }

        pane._explorerTreeLoading.add(key);
        pane._explorerTreeErrors.delete(key);
        if (!cached) {
            renderExplorerTreePanel(index);
        }
        try {
            const entriesUrl = `/api/explorer/${encodeURIComponent(sessionId)}/entries`;
            // Always send an explicit path (empty === the explorer root) so the tree stays
            // anchored to the configured root. Omitting it makes the backend fall back to the
            // session's current directory, which strands the tree on a subdirectory after the
            // pane re-enters explorer mode from a deeper terminal cwd.
            const response = await fetch(`${entriesUrl}?path=${encodeURIComponent(key)}`);
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to load directory');
            }
            updateExplorerFilesystemRootRevision(index, data.root_revision || '');
            const entries = (Array.isArray(data.entries) ? data.entries : []).filter(entry => !entry.deleted);
            pane._explorerTreeChildren.set(key, entries);
            return entries;
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer tree load failed:', error);
            pane._explorerTreeErrors.set(key, error.message || 'Failed to load directory.');
            return [];
        } finally {
            pane._explorerTreeLoading.delete(key);
            renderExplorerTreePanel(index);
        }
    }

    function explorerTreeRowIsActive(pane, entry) {
        const path = entry.path || '';
        if (entry.type === 'directory') {
            return pane._explorerMode !== 'file' && (pane._explorerPath || '') === path;
        }
        return pane._explorerMode === 'file' && (pane._explorerFilePath || '') === path;
    }

    /* Find a tree row's entry record by path in the loaded children of its
       parent directory. Used to carry the row's Git status onto a tab the row
       opens; null whenever that directory is not loaded. */
    function explorerTreeEntryForPath(pane, path) {
        const value = String(path || '');
        if (!value || !(pane?._explorerTreeChildren instanceof Map)) {
            return null;
        }
        const entries = pane._explorerTreeChildren.get(explorerTreeParentPath(value));
        if (!Array.isArray(entries)) {
            return null;
        }
        return entries.find(entry => (entry.path || '') === value) || null;
    }

    /* The directory a tree path sits in; '' for a root-level entry, which is
       also the key its children are cached under. */
    function explorerTreeParentPath(path) {
        const value = String(path || '');
        const separator = value.lastIndexOf('/');
        return separator === -1 ? '' : value.slice(0, separator);
    }

    /* Every directory sharing this path's parent, itself included — the set an
       Alt+click fans a fold out over. Empty whenever the parent's listing is
       not loaded, which leaves the gesture a no-op rather than a guess. */
    function explorerTreeSiblingDirectories(pane, path) {
        const entries = pane._explorerTreeChildren.get(explorerTreeParentPath(path));
        if (!Array.isArray(entries)) {
            return [];
        }
        return entries
            .filter(entry => entry.type === 'directory' && entry.path)
            .map(entry => entry.path);
    }

    /* One tree row. `options.nameHtml` supplies already-escaped markup for the
       name (the filter's match highlight); `options.staticChevron` drops the
       fold control, which is what a filtered result tree wants — its folders
       are always expanded, so an arrow there would toggle nothing. */
    function explorerTreeRowHtml(pane, entry, depth, options = {}) {
        const isDirectory = entry.type === 'directory';
        const path = entry.path || '';
        const expanded = isDirectory && pane._explorerTreeExpanded.has(path);
        const active = explorerTreeRowIsActive(pane, entry);
        const action = isDirectory
            ? `data-explorer-tree-dir="${escHtml(path)}"`
            : `data-explorer-tree-file="${escHtml(path)}"`;
        /* The fold arrow is its own control: it expands/collapses in place and
           never navigates, so browsing the tree can't evict whatever the
           Preview tab is showing. Only the name button opens the target. */
        const indent = `style="padding-left:${7 + depth * EXPLORER_TREE_INDENT_PX}px"`;
        const chevron = isDirectory && !options.staticChevron
            ? `<button
                type="button"
                class="explorer-tree-chevron-btn"
                data-explorer-tree-chevron="${escHtml(path)}"
                aria-expanded="${expanded ? 'true' : 'false'}"
                title="${expanded ? 'Collapse folder (Alt: collapse all at this level)' : 'Expand folder (Alt: expand all at this level)'}"
                aria-label="${expanded ? 'Collapse' : 'Expand'} ${escHtml(entry.name || path)}"
                ${indent}
            >${expanded ? UI_CHEVRON_DOWN_ICON : UI_CHEVRON_RIGHT_ICON}</button>`
            : `<span class="explorer-tree-chevron" aria-hidden="true" ${indent}></span>`;
        const badge = explorerGitStatusLabel(entry.git) ? explorerGitBadgeHtml(entry.git) : '';
        const openFolder = isDirectory
            ? `<button type="button" class="explorer-search-btn explorer-open-folder-btn" data-explorer-tree-open-folder="${escHtml(path)}" title="Open folder in the explorer list" aria-label="Open folder in the explorer list">${EXPLORER_OPEN_FOLDER_ICON}</button>`
            : '';
        const openTab = isDirectory
            ? ''
            : `<button type="button" class="explorer-search-btn explorer-open-tab-btn" data-explorer-tree-open-tab="${escHtml(path)}" title="Open in a new tab" aria-label="Open ${escHtml(entry.name || path)} in a new tab">${EXPLORER_OPEN_TAB_ICON}</button>`;

        return `
            <div
                class="explorer-tree-row${active ? ' active' : ''}"
                data-explorer-copy-path="${escHtml(path)}"
                data-explorer-context-path="${escHtml(path)}"
                data-explorer-context-kind="${escHtml(entry.entry_kind || '')}"
                data-explorer-context-revision="${escHtml(entry.revision || '')}"
                data-explorer-context-surface="tree"
                ${isDirectory ? '' : `data-explorer-download-path="${escHtml(path)}"`}
            >
                ${chevron}
                <button type="button" class="explorer-tree-main" ${action} title="${escHtml(path)}">
                    ${isDirectory ? EXPLORER_FOLDER_ICON : explorerFileTypeIconHtml(entry.name || path)}
                    <span class="explorer-tree-name">${options.nameHtml || escHtml(entry.name || path)}</span>
                </button>
                ${badge}
                ${openFolder}
                ${openTab}
            </div>
        `;
    }

    function renderExplorerTreeNodes(pane, path, depth) {
        const indent = `style="padding-left:${10 + depth * EXPLORER_TREE_INDENT_PX}px"`;
        const error = pane._explorerTreeErrors.get(path);
        if (error) {
            return `<div class="explorer-tree-error" ${indent}>${escHtml(error)}</div>`;
        }

        const entries = pane._explorerTreeChildren.get(path);
        // A folder being re-read keeps showing what it has; only a folder with
        // nothing cached yet is worth a placeholder.
        if (!entries) {
            return pane._explorerTreeLoading.has(path)
                ? `<div class="explorer-tree-loading" ${indent}>Loading...</div>`
                : '';
        }
        if (!entries.length) {
            return `<div class="explorer-tree-empty" ${indent}>Empty folder.</div>`;
        }

        return entries.map(entry => {
            const row = explorerTreeRowHtml(pane, entry, depth);
            if (entry.type !== 'directory' || !pane._explorerTreeExpanded.has(entry.path || '')) {
                return row;
            }
            const children = renderExplorerTreeNodes(pane, entry.path || '', depth + 1);
            return `${row}<div class="explorer-tree-children">${children}</div>`;
        }).join('');
    }

    function renderExplorerTreePanel(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-tree-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        wireExplorerCopyPathMenu(panel, index);

        ensureExplorerTreeState(pane);
        /* The head — "FILES" plus the name filter — is built once and left
           alone: rebuilding it on every render would drop the caret out of the
           filter box on the keystroke that triggered the render. */
        if (!panel.querySelector('.explorer-tree-section')) {
            panel.innerHTML = `
                <div class="explorer-tree-section">
                    <div class="explorer-tree-head">
                        <div class="explorer-tree-title">Files</div>
                        ${typeof explorerTreeSearchHeadHtml === 'function'
                            ? explorerTreeSearchHeadHtml(index)
                            : ''}
                    </div>
                    <div class="explorer-tree-children" data-explorer-tree-body></div>
                </div>
            `;
            if (typeof wireExplorerTreeSearchControls === 'function') {
                wireExplorerTreeSearchControls(index);
            }
        }
        if (typeof syncExplorerTreeSearchControls === 'function') {
            syncExplorerTreeSearchControls(index);
        }
        const body = panel.querySelector('[data-explorer-tree-body]');
        if (!body) {
            return;
        }
        /* With a filter query typed, the body is the filtered result tree
           instead of the browsable one — same row markup, same click targets. */
        body.innerHTML = (typeof explorerTreeSearchActive === 'function'
            && explorerTreeSearchActive(pane))
            ? renderExplorerTreeSearchNodes(index)
            : renderExplorerTreeNodes(pane, '', 0);
        panel.querySelectorAll('[data-explorer-tree-chevron]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                const path = button.dataset.explorerTreeChevron || '';
                if (event.altKey) {
                    toggleExplorerTreeLevel(index, path);
                } else {
                    toggleExplorerTreeDirectory(index, path);
                }
            });
        });
        panel.querySelectorAll('.explorer-tree-main').forEach(button => {
            button.addEventListener('mousedown', event => {
                if (event.shiftKey) {
                    event.preventDefault();
                }
            });
        });
        panel.querySelectorAll('[data-explorer-tree-dir]').forEach(button => {
            button.addEventListener('click', event => {
                const row = button.closest('.explorer-tree-row');
                if (handleExplorerRowSelectionClick(event, index, 'tree', row)) {
                    return;
                }
                openExplorerTreeDirectory(index, button.dataset.explorerTreeDir || '');
            });
        });
        panel.querySelectorAll('[data-explorer-tree-file]').forEach(button => {
            button.addEventListener('click', event => {
                const row = button.closest('.explorer-tree-row');
                if (handleExplorerRowSelectionClick(event, index, 'tree', row)) {
                    return;
                }
                openExplorerFile(index, button.dataset.explorerTreeFile || '');
            });
        });
        panel.querySelectorAll('[data-explorer-tree-open-folder]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                loadExplorerPane(index, button.dataset.explorerTreeOpenFolder || '');
            });
        });
        panel.querySelectorAll('[data-explorer-tree-open-tab]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                const path = button.dataset.explorerTreeOpenTab || '';
                openExplorerFileInBackgroundTab(index, path, {
                    git: explorerTreeEntryForPath(terminals[index], path)?.git || null
                });
            });
        });
        if (typeof refreshExplorerFilesystemCutSource === 'function') {
            refreshExplorerFilesystemCutSource(index);
        }
        refreshExplorerSelectionHighlight(index);
    }

    /* Fold arrow only: expand or collapse in place. It never touches the
       Preview tab, so the tree can be browsed without losing the open file. */
    async function toggleExplorerTreeDirectory(index, path) {
        const pane = terminals[index];
        if (!pane || !path) {
            return;
        }

        ensureExplorerTreeState(pane);
        if (pane._explorerTreeExpanded.has(path)) {
            pane._explorerTreeExpanded.delete(path);
            renderExplorerTreePanel(index);
            notePanePresentationChanged(index);
            return;
        }

        pane._explorerTreeExpanded.add(path);
        pane._explorerTreeErrors.delete(path);
        renderExplorerTreePanel(index);
        await loadExplorerTreeChildren(index, path);
        notePanePresentationChanged(index);
    }

    /* Expanding a whole level is one directory listing per folder, so run a few
       at a time: a wide level over SFTP should not fire a request per folder at
       once. Already-visited folders come back from the children cache free. */
    const EXPLORER_TREE_LEVEL_LOAD_CONCURRENCY = 4;

    async function loadExplorerTreeLevelChildren(index, paths) {
        const queue = paths.slice();
        const workers = [];
        const width = Math.min(EXPLORER_TREE_LEVEL_LOAD_CONCURRENCY, queue.length);
        for (let worker = 0; worker < width; worker += 1) {
            workers.push((async () => {
                while (queue.length) {
                    await loadExplorerTreeChildren(index, queue.shift());
                }
            })());
        }
        await Promise.all(workers);
    }

    /* Drop a folder and everything expanded beneath it, so re-opening it later
       gives a collapsed folder instead of restoring the old subtree. */
    function collapseExplorerTreeSubtree(pane, path) {
        const prefix = `${path}/`;
        pane._explorerTreeExpanded.forEach(value => {
            if (value === path || value.startsWith(prefix)) {
                pane._explorerTreeExpanded.delete(value);
            }
        });
    }

    /* Alt+click on a fold arrow fans the toggle out to every directory sharing
       the clicked one's parent — the Files tree's answer to the Markdown source
       view's fold-all-at-this-level. The new state mirrors the clicked row, so
       Alt+clicking an open root-level folder folds the whole tree in one
       gesture. Collapsing forgets the level's deeper expansions rather than
       just hiding them: "fold everything I opened" should hand back a clean
       tree, not spring the old subtree back on the next click. */
    async function toggleExplorerTreeLevel(index, path) {
        const pane = terminals[index];
        if (!pane || !path) {
            return;
        }

        ensureExplorerTreeState(pane);
        const siblings = explorerTreeSiblingDirectories(pane, path);
        if (!siblings.length) {
            return;
        }

        if (pane._explorerTreeExpanded.has(path)) {
            siblings.forEach(sibling => collapseExplorerTreeSubtree(pane, sibling));
            renderExplorerTreePanel(index);
            /* Folding a level removes most of the rows under the scroll
               position, and the browser answers a shrunken scroll height by
               clamping scrollTop to the new bottom — so the tree lands
               somewhere unrelated to the folder that was just clicked. The
               clicked row is the one thing the gesture is about, so it becomes
               the anchor. */
            scrollExplorerTreeRowIntoView(index, path);
            notePanePresentationChanged(index);
            return;
        }

        siblings.forEach(sibling => {
            pane._explorerTreeExpanded.add(sibling);
            pane._explorerTreeErrors.delete(sibling);
        });
        renderExplorerTreePanel(index);
        scrollExplorerTreeRowIntoView(index, path);
        await loadExplorerTreeLevelChildren(index, siblings);
        /* Siblings listed above the clicked one insert their children between
           it and the top of the panel, so re-anchor once the level has filled
           in. Both calls leave a row that is already visible alone. */
        scrollExplorerTreeRowIntoView(index, path);
        notePanePresentationChanged(index);
    }

    /* Directory name click: browse it in the Preview tab, and expand it so the
       tree matches what the pane now shows. Never collapses — collapsing is
       the fold arrow's job, so an open directory can be re-opened safely. */
    async function openExplorerTreeDirectory(index, path) {
        const pane = terminals[index];
        if (!pane || !path) {
            return;
        }

        ensureExplorerTreeState(pane);
        let childrenLoading = Promise.resolve();
        if (!pane._explorerTreeExpanded.has(path)) {
            pane._explorerTreeExpanded.add(path);
            pane._explorerTreeErrors.delete(path);
            renderExplorerTreePanel(index);
            childrenLoading = loadExplorerTreeChildren(index, path);
        }
        await loadExplorerPane(index, path);
        await childrenLoading;
    }

    /* Expand every ancestor of the pane's current directory or open file — or
       of an explicit path, which the tab strip's locate-in-tree gesture uses
       to point at a tab's file without depending on what the viewer shows. */
    async function revealExplorerTreePath(index, targetPath = '') {
        const pane = terminals[index];
        if (!pane?._explorerTreeSidebarOpen) {
            return;
        }

        ensureExplorerTreeState(pane);
        const target = targetPath || (pane._explorerMode === 'file'
            ? (pane._explorerFilePath || '')
            : (pane._explorerPath || ''));
        const segments = String(target).split('/').filter(Boolean);
        /* Expand ancestors so the target's own row becomes visible; whether
           the target directory itself expands stays a tree-click decision —
           otherwise navigating on click (2.d) would undo a collapse. */
        segments.pop();

        await loadExplorerTreeChildren(index, '');
        let current = '';
        for (const segment of segments) {
            current = current ? `${current}/${segment}` : segment;
            pane._explorerTreeExpanded.add(current);
            await loadExplorerTreeChildren(index, current);
        }
        renderExplorerTreePanel(index);
        /* Expanding the ancestors is only half the reveal: in a long tree the
           target's row can still sit outside the panel's scrolled viewport,
           which leaves its `.active` highlight off screen. */
        scrollExplorerTreeRowIntoView(index, target);
    }

    /* The tree row for a path (file or directory), or the row marked `.active`
       when no path is given. Matched by iterating the rendered buttons rather
       than with an attribute selector, because paths carry quotes and
       brackets. Null whenever the row is not rendered — a collapsed or still
       loading branch. */
    function explorerTreeRowElement(panel, path) {
        if (!panel) {
            return null;
        }
        if (!path) {
            return panel.querySelector('.explorer-tree-row.active');
        }
        const button = Array
            .from(panel.querySelectorAll('[data-explorer-tree-file], [data-explorer-tree-dir]'))
            .find(entry => (entry.dataset.explorerTreeFile ?? entry.dataset.explorerTreeDir) === path);
        return button?.closest('.explorer-tree-row') || null;
    }

    /* Scroll the tree panel — and only it, which is why this does the maths
       instead of calling `scrollIntoView`, whose `nearest` also scrolls every
       other ancestor — by the minimum needed to show a row, leaving one row of
       margin so the target never lands flush against an edge. A row already in
       view is left alone, so clicking around inside the tree never jumps.
       Returns the row so callers can decorate it. */
    function scrollExplorerTreeRowIntoView(index, path = '') {
        const panel = document.getElementById(`explorer-tree-panel-${index}`);
        const row = panel && !panel.hidden ? explorerTreeRowElement(panel, path) : null;
        if (!row) {
            return null;
        }
        const panelBox = panel.getBoundingClientRect();
        const rowBox = row.getBoundingClientRect();
        const margin = Math.min(rowBox.height, Math.max(0, (panelBox.height - rowBox.height) / 2));
        if (rowBox.top < panelBox.top + margin) {
            panel.scrollTop -= (panelBox.top + margin) - rowBox.top;
        } else if (rowBox.bottom > panelBox.bottom - margin) {
            panel.scrollTop += rowBox.bottom - (panelBox.bottom - margin);
        }
        return row;
    }

    /* Scroll a file's tree row into view and flash it. The row's own `.active`
       styling still marks the open file; this only draws the eye to it after
       the tree scrolls. A row that is not rendered (a collapsed or still
       loading branch) is left alone — the expansion above is the visible
       part of the reveal. */
    function focusExplorerTreeRow(index, path) {
        const row = path ? scrollExplorerTreeRowIntoView(index, path) : null;
        if (!row) {
            return false;
        }
        row.classList.add('explorer-tree-located');
        window.setTimeout(() => row.classList.remove('explorer-tree-located'), 1200);
        return true;
    }

    async function loadExplorerTree(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        ensureExplorerTreeState(pane);
        renderExplorerTreePanel(index);
        await revealExplorerTreePath(index);
    }

    /* Drop cached children but keep expansion state, then refetch what is visible. */
    async function reloadExplorerTree(index) {
        const pane = terminals[index];
        if (!pane?._explorerTreeSidebarOpen) {
            return;
        }

        ensureExplorerTreeState(pane);
        pane._explorerTreeChildren.clear();
        pane._explorerTreeErrors.clear();
        renderExplorerTreePanel(index);

        const expanded = [...pane._explorerTreeExpanded]
            .sort((left, right) => left.split('/').length - right.split('/').length);
        await loadExplorerTreeChildren(index, '');
        for (const path of expanded) {
            await loadExplorerTreeChildren(index, path);
        }
        resetExplorerFsWatchBaseline(pane);
        renderExplorerTreePanel(index);
        /* A reload means the tree on disk moved under us (a create, a delete, a
           rename). With a filter typed, its result set is what the panel is
           showing, so it has to be re-read too — once, on the same explicit
           trigger, never on a timer. */
        if (typeof explorerTreeSearchActive === 'function' && explorerTreeSearchActive(pane)) {
            await runExplorerTreeSearch(index);
        }
    }

    /* One file's row, re-read in place. Saving changes a file's *contents*, so
       the set of paths the tree draws cannot have moved — only that one row
       can (its Git badge turns a clean file modified, and its filesystem
       revision is what the delete/move guards check). Running the full
       reloadExplorerTree() for that dropped every cached directory, flashed a
       near-empty panel, refetched one request per expanded folder and left the
       reader scrolled back to the top of a tree they had navigated by hand.

       So only the file's own directory is re-read, its rows stay on screen for
       the round trip, every other folder keeps its cache and its expansion,
       and the panel's scroll is put back afterwards — the rebuild that follows
       the response resets it, the same way it resets on any tree render. A
       file whose directory the tree has not loaded has no row to refresh. */
    async function refreshExplorerTreeFileEntry(index, path) {
        const pane = terminals[index];
        const target = String(path || '');
        if (!pane?._explorerTreeSidebarOpen || !target) {
            return;
        }
        ensureExplorerTreeState(pane);
        const cut = target.lastIndexOf('/');
        const parent = cut === -1 ? '' : target.slice(0, cut);
        if (!pane._explorerTreeChildren.has(parent)) {
            return;
        }
        const panel = document.getElementById(`explorer-tree-panel-${index}`);
        const viewport = captureScrollMetrics(panel);
        await loadExplorerTreeChildren(index, parent, { refresh: true });
        applyScrollMetrics(document.getElementById(`explorer-tree-panel-${index}`), viewport);
    }

    /* Baseline for the open-file change listener (explorer-git-watch.js), set
       from every file load and save. A non-empty value is what arms the watch,
       so clearing it (directory listing, image viewer, commit diff, empty
       viewer) is also how those views opt out. Any fresh baseline re-arms a
       watch that suspended itself after repeated failures. */
    function setExplorerFileWatchBaseline(pane, revision) {
        if (!pane) {
            return;
        }
        pane._explorerFileStateRevision = typeof revision === 'string' ? revision : '';
        pane._explorerFileWatchPending = null;
        if (pane._explorerFileStateRevision) {
            pane._explorerFileWatchFailures = 0;
            pane._explorerFileWatchSuspended = false;
        }
    }

    /* Baseline for the filesystem-surface listener (explorer-git-watch.js).
       Cleared by every user-initiated listing/tree load, because the surfaces
       are then current by construction: the next poll re-bootstraps silently
       instead of repainting what was just fetched. Also re-arms a watch that
       suspended itself after repeated `/entries` failures. */
    function resetExplorerFsWatchBaseline(pane) {
        if (!pane) {
            return;
        }
        pane._explorerFsWatchRevision = '';
        pane._explorerFsWatchPending = null;
        pane._explorerFsWatchFailures = 0;
        pane._explorerFsWatchSuspended = false;
    }

    /* Background re-read of the file the viewer is showing, for the open-file
       change listener. Identity-checked at every await, never runs against an
       open editor buffer (the editor owns the file and has its own
       save-conflict flow), and prefers the in-place update so scroll, view
       mode, search query and the tab strip survive. Returns false — leaving
       the last good view exactly as it was — on any failure or staleness. */
    async function refreshExplorerOpenFileQuiet(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const path = pane?._explorerFilePath || '';
        if (!pane || !sessionId || !path || pane._explorerFileWatchRefreshing) {
            return false;
        }
        if (explorerEditState(pane) || pane._explorerMode !== 'file') {
            return false;
        }
        pane._explorerFileWatchRefreshing = true;
        try {
            const response = await fetch(
                `/api/explorer/${encodeURIComponent(sessionId)}/file?path=${encodeURIComponent(path)}`,
                { cache: 'no-store' }
            );
            const data = await response.json();
            if (!response.ok) {
                return false;
            }
            if (
                terminals[index] !== pane
                || sessionIds[index] !== sessionId
                || pane._explorerMode !== 'file'
                || pane._explorerFilePath !== path
                || explorerEditState(pane)
            ) {
                return false;
            }
            const diffMode = pane._explorerDiffMode || '';
            const scrollState = captureExplorerFileScroll(index);
            if (!updateExplorerFileInPlace(index, data, scrollState)) {
                // The available panels changed (a file that lost or gained its
                // Diff panel); a full rebuild is the only way to reflect that.
                renderExplorerFile(index, data, {
                    scrollState,
                    openDiff: scrollState?.activeView === 'diff' && explorerHasGitDiff(data.git),
                    diffMode,
                    tab: pane._explorerActiveTabId
                });
            }
            return true;
        } catch (error) {
            return false;
        } finally {
            pane._explorerFileWatchRefreshing = false;
        }
    }

    /* ── Quiet filesystem-surface refresh (change-listener plan §15) ────────
       The directory listing and the Files tree carry the same Git badges the
       sidebar does, so a file created or edited outside GridVibe has to land
       there too — that is what the plan's D7 asymmetry deferred and what these
       helpers close. They are driven by the *same* `/git/state` poll the
       sidebar uses (explorer-git-watch.js): no new endpoint, no second signal.

       Quiet means: no `Loading directory...` placeholder, no tab switch, no
       scroll reset, no search reset, and — when the re-fetched entries are
       byte-for-byte the same listing — no DOM write at all. They never touch
       the file viewer, the editor buffer, or the tab strip; a pane showing a
       file only refreshes its tree sidebar. */

    const EXPLORER_FS_WATCH_MAX_TREE_NODES = 16;

    /* Everything the listing and tree rows actually render, hashed. A poll
       that fires for a change outside the browsed directory (or outside the
       expanded tree) therefore costs one fetch and zero repaints. */
    function explorerEntriesSignature(entries) {
        if (!Array.isArray(entries)) {
            return '';
        }
        return explorerHashText(entries.map(entry => [
            entry.path || '',
            entry.name || '',
            entry.type || '',
            entry.entry_kind || '',
            entry.deleted ? '1' : '',
            entry.size == null ? '' : String(entry.size),
            entry.modified == null ? '' : String(entry.modified),
            entry.revision || '',
            entry.git?.status || '',
            entry.git?.index_status || '',
            entry.git?.worktree_status || ''
        ].join('')).join(''));
    }

    async function explorerFetchEntriesQuiet(index, path) {
        const sessionId = sessionIds[index];
        if (!sessionId) {
            return null;
        }
        try {
            const response = await fetch(
                `/api/explorer/${encodeURIComponent(sessionId)}/entries?path=${encodeURIComponent(path || '')}`,
                { cache: 'no-store' }
            );
            const data = await response.json();
            return response.ok ? data : null;
        } catch (error) {
            return null;
        }
    }

    /* Re-list the browsed directory in place. Only `_explorerEntries` and the
       rows change; the Preview tab, its Find query, and the list scroll offset
       are all preserved, so a new file simply appears where it belongs. */
    async function refreshExplorerDirectoryQuiet(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId || pane._explorerMode !== 'directory') {
            return true; // Nothing to re-list is not a failure.
        }
        const path = pane._explorerPath || '';
        const data = await explorerFetchEntriesQuiet(index, path);
        if (!data) {
            return false;
        }
        if (
            terminals[index] !== pane
            || sessionIds[index] !== sessionId
            || pane._explorerMode !== 'directory'
            || (pane._explorerPath || '') !== path
        ) {
            return false;
        }
        const entries = Array.isArray(data.entries) ? data.entries : [];
        if (explorerEntriesSignature(entries) === explorerEntriesSignature(pane._explorerEntries)) {
            return true;
        }
        const list = document.getElementById(`explorer-list-${index}`);
        const scrollTop = list ? list.scrollTop : 0;
        const scrollLeft = list ? list.scrollLeft : 0;
        updateExplorerFilesystemRootRevision(index, data.root_revision || '');
        pane._explorerEntries = entries;
        pane._explorerParentPath = data.parent_path || '';
        pane._explorerGitContext = data.git || null;
        updateExplorerGitSummary(index, data.git || null);
        renderExplorerDirectoryRows(index);
        if (list) {
            list.scrollTop = Math.min(scrollTop, Math.max(0, list.scrollHeight - list.clientHeight));
            list.scrollLeft = scrollLeft;
        }
        return true;
    }

    /* The tree nodes a re-render would actually paint: the root plus every
       expanded directory whose children are cached, shallowest first. Bounded
       because each node costs one `/entries` (one `git status` on a subtree) —
       a deeply expanded tree refreshes its visible top and leaves the rest to
       the manual Refresh, which is strictly better than today's fully stale
       tree. */
    function explorerTreeQuietRefreshKeys(pane) {
        const keys = [''];
        [...pane._explorerTreeExpanded]
            .filter(path => path && pane._explorerTreeChildren.has(path))
            .sort((left, right) => left.split('/').length - right.split('/').length)
            .forEach(path => keys.push(path));
        return keys.slice(0, EXPLORER_FS_WATCH_MAX_TREE_NODES);
    }

    /* Refetch the visible tree nodes into a scratch map, then swap them in one
       render — unlike reloadExplorerTree, the panel never empties, never shows
       `Loading...`, and keeps its scroll offset and expansion state. */
    async function refreshExplorerTreeQuiet(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId || !pane._explorerTreeSidebarOpen) {
            return true;
        }
        ensureExplorerTreeState(pane);
        if (!pane._explorerTreeChildren.size) {
            return true; // Never loaded: the watcher does not bootstrap it.
        }
        const fetched = new Map();
        let changed = false;
        for (const key of explorerTreeQuietRefreshKeys(pane)) {
            const data = await explorerFetchEntriesQuiet(index, key);
            if (terminals[index] !== pane || sessionIds[index] !== sessionId) {
                return false;
            }
            if (!data) {
                return false;
            }
            const entries = (Array.isArray(data.entries) ? data.entries : [])
                .filter(entry => !entry.deleted);
            fetched.set(key, entries);
            if (explorerEntriesSignature(entries)
                !== explorerEntriesSignature(pane._explorerTreeChildren.get(key))) {
                changed = true;
            }
        }
        if (!changed) {
            return true;
        }
        const metrics = captureScrollMetrics(document.getElementById(`explorer-tree-panel-${index}`));
        fetched.forEach((entries, key) => {
            pane._explorerTreeChildren.set(key, entries);
            pane._explorerTreeErrors.delete(key);
        });
        renderExplorerTreePanel(index);
        applyScrollMetrics(document.getElementById(`explorer-tree-panel-${index}`), metrics);
        return true;
    }

    /* One entry point for the change listener. Returns false on any failure or
       staleness so the watcher can back off without advancing its baseline —
       the surfaces keep their last good contents either way. */
    async function refreshExplorerFilesystemSurfacesQuiet(index) {
        const pane = terminals[index];
        if (!pane || pane._explorerFsWatchRefreshing) {
            return false;
        }
        pane._explorerFsWatchRefreshing = true;
        try {
            const listing = await refreshExplorerDirectoryQuiet(index);
            const tree = await refreshExplorerTreeQuiet(index);
            return listing && tree;
        } catch (error) {
            return false;
        } finally {
            pane._explorerFsWatchRefreshing = false;
        }
    }

    /* Resolve a requested view onto a panel that actually exists. A stored or
       restored 'diff' mode routinely outlives its panel — discarding a file's
       changes rebuilds the viewer without one, and the captured scroll state
       still asks for 'diff' — and selecting a missing panel hides *every*
       panel, leaving an empty viewer with no way back (ISSUE: empty file view
       after undoing all changes). Fall back to the sticky source/preview
       preference, then to whatever panel is left. */
    function explorerResolveFileView(index, mode) {
        const pane = terminals[index];
        const exists = view => Boolean(
            document.querySelector(`#explorer-list-${index} [data-explorer-file-panel="${view}"]`)
        );
        const candidates = [mode, pane?._explorerLastFileView, 'source', 'preview'];
        return candidates.find(view => view && exists(view)) || mode;
    }

    /* `scroll` is forwarded to the find that gets re-applied at the bottom.
       A reader switching panels wants the view to land on the active match —
       Preview and Diff are rebuilt from scratch and would otherwise open at the
       top. Entering the in-place editor does not: it is pinning the view to
       Source on its way to mounting the editor over it, and the position it is
       about to carry into the textarea is the one the reader left. */
    function setExplorerFileView(index, mode, { scroll = true, captureScroll = true } = {}) {
        const normalizedMode =
            mode === 'preview' ? 'preview'
            : mode === 'diff' ? 'diff'
            : 'source';
        const pane = terminals[index];
        const list = document.getElementById(`explorer-list-${index}`);
        if (!list) {
            return;
        }

        const body = list.querySelector('.explorer-editor-body');
        const diffPanel = document.getElementById(`explorer-diff-panel-${index}`);
        const selectedMode = explorerResolveFileView(index, normalizedMode);
        const isDiffMode = selectedMode === 'diff';
        const outgoingMode = activeExplorerFileView(index);
        if (captureScroll && outgoingMode !== selectedMode) {
            rememberExplorerPanelScroll(index, outgoingMode);
        }
        if (pane) {
            pane._explorerDiffSplit = isDiffMode;
            if (selectedMode === 'source' || selectedMode === 'preview') {
                pane._explorerLastFileView = selectedMode;
                // Sticky per-tab source/preview preference: the Preview tab
                // carries it across different files (2.e). Diff stays an
                // explicit per-view action, mirroring _explorerLastFileView.
                explorerActiveTab(pane).preferredMode = selectedMode;
            }
        }
        if (body) {
            body.classList.toggle('split-diff', isDiffMode);
        }
        if (diffPanel) {
            diffPanel.hidden = !isDiffMode;
        }
        list.querySelectorAll('[data-explorer-file-view]').forEach(button => {
            const isSelected = button.dataset.explorerFileView === selectedMode;
            button.setAttribute('aria-selected', isSelected ? 'true' : 'false');
            if (button.dataset.explorerDiffToggle) {
                button.setAttribute('aria-pressed', isDiffMode ? 'true' : 'false');
            }
        });
        list.querySelectorAll('[data-explorer-file-panel]').forEach(panel => {
            panel.hidden = panel.dataset.explorerFilePanel !== selectedMode;
        });
        applyExplorerLineWrapState(index, selectedMode);
        // First visit to Preview is where the render cost now lands; later
        // visits reuse the pane's cached HTML and are instant.
        if (selectedMode === 'preview') {
            ensureExplorerPreviewLoaded(index);
        }
        if (isDiffMode) {
            loadExplorerDiff(index);
            const state = pane ? ensureExplorerSearchState(pane, 'file') : null;
            if (state?.query) {
                applyExplorerSearch(index, { scroll });
            }
        } else {
            applyExplorerSearch(index, { scroll });
        }
        requestExplorerPanelScrollRestore(index, selectedMode);
    }

    function findExplorerMarkdownPreviewTargetIndex() {
        const activePane = document.activeElement?.closest?.('.explorer-pane');
        const candidates = [Number(activePane?.dataset.slot), _focusedTerminalIndex];
        for (let index = 0; index < terminals.length; index += 1) {
            candidates.push(index);
        }
        const seen = new Set();
        for (const index of candidates) {
            if (!Number.isInteger(index) || index < 0 || seen.has(index)) {
                continue;
            }
            seen.add(index);
            if (terminals[index]?._explorerMode === 'file'
                && document.getElementById(`explorer-preview-${index}`)) {
                return index;
            }
        }
        return -1;
    }
    function isExplorerSearchablePane(pane) {
        return pane?._explorerMode === 'file' || pane?._explorerMode === 'directory';
    }

    function ensureExplorerSearchState(pane, mode = pane?._explorerMode) {
        const key = mode === 'directory' ? '_explorerDirectorySearch' : '_explorerSearch';
        if (!pane[key]) {
            pane[key] = {
                query: '',
                activeIndex: 0,
                matchCount: 0,
                matchCapped: false,
                ranges: [],
                resultQuery: '',
                seekOffset: null
            };
        }
        return pane[key];
    }

    function clampExplorerEditorFontSize(value) {
        const fontSize = Number(value);
        if (!Number.isFinite(fontSize)) {
            return EXPLORER_EDITOR_FONT_DEFAULT;
        }
        return Math.min(
            EXPLORER_EDITOR_FONT_MAX,
            Math.max(EXPLORER_EDITOR_FONT_MIN, Math.round(fontSize))
        );
    }

    /* Editor zoom is per explorer tab (2.e): each tab record keeps its own
       font size instead of sharing one pane-global value, so swapping tabs
       restores the zoom each tab was left at. */
    function ensureExplorerEditorFontSize(pane) {
        if (!pane) {
            return EXPLORER_EDITOR_FONT_DEFAULT;
        }
        const tab = explorerActiveTab(pane);
        tab.fontSize = clampExplorerEditorFontSize(
            tab.fontSize || EXPLORER_EDITOR_FONT_DEFAULT
        );
        return tab.fontSize;
    }

    function applyExplorerEditorFontSize(index) {
        const pane = terminals[index];
        const list = document.getElementById(`explorer-list-${index}`);
        if (!pane || !list) {
            return;
        }

        const fontSize = ensureExplorerEditorFontSize(pane);
        list.style.setProperty('--explorer-editor-font-size', `${fontSize}px`);
        scheduleExplorerDiffScrollbarSync(list.querySelector('.explorer-diff2html'));

        const value = list.querySelector(`[data-explorer-zoom-value="${index}"]`);
        if (value) {
            value.textContent = `${fontSize}px`;
        }
        const decrease = list.querySelector(`[data-explorer-zoom-decrease="${index}"]`);
        const increase = list.querySelector(`[data-explorer-zoom-increase="${index}"]`);
        if (decrease) {
            decrease.disabled = fontSize <= EXPLORER_EDITOR_FONT_MIN;
        }
        if (increase) {
            increase.disabled = fontSize >= EXPLORER_EDITOR_FONT_MAX;
        }
    }

    function stepExplorerEditorFontSize(index, delta) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const tab = explorerActiveTab(pane);
        const current = ensureExplorerEditorFontSize(pane);
        tab.fontSize = clampExplorerEditorFontSize(
            current + (Number(delta) || 0)
        );
        applyExplorerEditorFontSize(index);
    }

    function wireExplorerEditorZoomControls(index) {
        const list = document.getElementById(`explorer-list-${index}`);
        if (!list) {
            return;
        }
        const decrease = list.querySelector(`[data-explorer-zoom-decrease="${index}"]`);
        const increase = list.querySelector(`[data-explorer-zoom-increase="${index}"]`);
        if (decrease && !decrease.dataset.bound) {
            decrease.dataset.bound = 'true';
            decrease.addEventListener('click', () => {
                stepExplorerEditorFontSize(index, -EXPLORER_EDITOR_FONT_STEP);
            });
        }
        if (increase && !increase.dataset.bound) {
            increase.dataset.bound = 'true';
            increase.addEventListener('click', () => {
                stepExplorerEditorFontSize(index, EXPLORER_EDITOR_FONT_STEP);
            });
        }
        applyExplorerEditorFontSize(index);
    }

    // ── Viewer appearance (ISSUE-2026-030) ───────────────────────────────────
    // Three orthogonal axes: the preview's reading-surface preset, the preview
    // font family, and the Source view's font family. All are bounded
    // allowlists owned by the workspace presentation record. The legacy
    // localStorage keys remain only as a first-paint/migration cache and are
    // overwritten whenever the server workspace snapshot is applied. Values
    // are applied idempotently to every open panel via classes + CSS custom
    // properties (defined from tokens in terminals.css), so no palette literals
    // live in JS.
    const EXPLORER_MD_PRESETS = ['default', 'paper', 'contrast', 'vscode'];
    const EXPLORER_MD_FONTS = [
        'system', 'serif', 'cascadia-code', 'jetbrains-mono', 'courier-new'
    ];
    /* Source is code: monospace only, with `default` keeping the stack the view
       has always used. */
    const EXPLORER_SOURCE_FONTS = [
        'default', 'cascadia-code', 'jetbrains-mono', 'courier-new'
    ];
    /* Retired options mapped onto their nearest survivor, so a stored (or saved
       session) value keeps its intent instead of snapping back to the default.
       `consolas` was dropped when it rendered identically to JetBrains Mono —
       that stack fell back to it before the faces were vendored (tokens.css). */
    const EXPLORER_FONT_ALIASES = { consolas: 'jetbrains-mono' };
    const EXPLORER_MD_PRESET_DEFAULT = 'default';
    const EXPLORER_MD_FONT_DEFAULT = 'system';
    const EXPLORER_SOURCE_FONT_DEFAULT = 'default';
    const EXPLORER_MD_PRESET_KEY = 'gridvibe.mdPreviewPreset';
    const EXPLORER_MD_FONT_KEY = 'gridvibe.mdPreviewFont';
    const EXPLORER_SOURCE_FONT_KEY = 'gridvibe.sourceViewFont';
    let workspaceExplorerAppearance = null;
    /* Line wrapping is per explorer tab, like the editor zoom above: each tab
       record carries its own source/preview/diff flags instead of one
       workspace-global preference, so every tab keeps the wrapping it was left
       at and the flags ride along in the saved session's per-tab views. All
       three default to ON — a wrapped view never hides content off to the
       right — so it is the *opt-out* that is tracked and persisted, and an
       absent flag means wrapped. */
    const EXPLORER_LINE_WRAP_MODES = Object.freeze(['source', 'preview', 'diff']);
    const EXPLORER_MD_PRESET_LABELS = {
        default: 'Default',
        paper: 'Paper',
        contrast: 'High contrast',
        vscode: 'Slate',
    };
    const EXPLORER_MD_FONT_LABELS = {
        system: 'System',
        serif: 'Serif',
        'cascadia-code': 'Cascadia Code',
        'jetbrains-mono': 'JetBrains Mono',
        'courier-new': 'Courier New',
    };
    const EXPLORER_SOURCE_FONT_LABELS = {
        default: 'Default',
        'cascadia-code': 'Cascadia Code',
        'jetbrains-mono': 'JetBrains Mono',
        'courier-new': 'Courier New',
    };

    function explorerLineWrapPreference(index, mode) {
        const pane = terminals[index];
        if (!pane || !EXPLORER_LINE_WRAP_MODES.includes(mode)) {
            return false;
        }
        return ensureExplorerTabLineWrap(explorerActiveTab(pane))[mode];
    }

    function explorerLineWrapControlText(mode, enabled) {
        const viewLabel = mode === 'preview'
            ? 'Markdown preview'
            : (mode === 'source' ? 'source view' : 'diff');
        return `${enabled ? 'Disable' : 'Enable'} line wrapping in ${viewLabel}`;
    }

    function explorerLineWrapControlHtml(index, initialMode = '') {
        const mode = EXPLORER_LINE_WRAP_MODES.includes(initialMode) ? initialMode : '';
        const enabled = mode ? explorerLineWrapPreference(index, mode) : false;
        const label = mode ? explorerLineWrapControlText(mode, enabled) : 'Wrap lines';
        return `
            <button
                type="button"
                class="explorer-line-wrap-btn"
                data-explorer-line-wrap="${index}"
                data-explorer-wrap-mode="${mode}"
                title="${label}"
                aria-label="${label}"
                aria-pressed="${enabled ? 'true' : 'false'}"
                ${mode ? '' : 'hidden'}
            >${EXPLORER_LINE_WRAP_ICON}</button>
        `;
    }

    function applyExplorerLineWrapState(index, selectedMode = activeExplorerFileView(index)) {
        const panels = {
            source: document.getElementById(`explorer-code-${index}`),
            preview: document.getElementById(`explorer-preview-${index}`),
            diff: document.getElementById(`explorer-diff-code-${index}`),
        };
        const wraps = {};
        EXPLORER_LINE_WRAP_MODES.forEach(mode => {
            wraps[mode] = explorerLineWrapPreference(index, mode);
            panels[mode]?.classList.toggle('wrap-lines', wraps[mode]);
        });
        /* The in-place editor replaces the Source panel's contents, so the same
           per-tab flag drives the textarea: the CSS class above styles it and
           the `wrap` attribute keeps the control's own soft wrapping in step
           (never `hard`, which would inject newlines into the saved value). */
        const textarea = panels.source?.querySelector('.explorer-source-editor');
        if (textarea) {
            textarea.wrap = wraps.source ? 'soft' : 'off';
        }

        const button = document.querySelector(`[data-explorer-line-wrap="${index}"]`);
        if (!button) {
            return;
        }
        const modeAvailable = EXPLORER_LINE_WRAP_MODES.includes(selectedMode)
            && Boolean(panels[selectedMode]);
        button.hidden = !modeAvailable;
        if (!modeAvailable) {
            button.dataset.explorerWrapMode = '';
            button.setAttribute('aria-pressed', 'false');
            return;
        }
        const enabled = wraps[selectedMode];
        const label = explorerLineWrapControlText(selectedMode, enabled);
        button.dataset.explorerWrapMode = selectedMode;
        button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
        button.setAttribute('title', label);
        button.setAttribute('aria-label', label);
    }

    function setExplorerLineWrapPreference(index, mode, enabled) {
        const pane = terminals[index];
        if (!pane || !EXPLORER_LINE_WRAP_MODES.includes(mode)) {
            return;
        }
        ensureExplorerTabLineWrap(explorerActiveTab(pane))[mode] = Boolean(enabled);
        applyExplorerLineWrapState(index);
        if (mode === 'diff' && pane._explorerDiffLoaded) {
            applyExplorerSearch(index);
        }
        persistExplorerTabsToSession(index);
    }

    function wireExplorerLineWrapControl(index) {
        const button = document.querySelector(`[data-explorer-line-wrap="${index}"]`);
        if (!button || button.dataset.bound) {
            applyExplorerLineWrapState(index);
            return;
        }
        button.dataset.bound = 'true';
        button.addEventListener('click', () => {
            const mode = button.dataset.explorerWrapMode;
            if (mode === 'preview' || mode === 'diff') {
                setExplorerLineWrapPreference(index, mode, !explorerLineWrapPreference(index, mode));
            }
        });
        applyExplorerLineWrapState(index);
    }

    function normalizeExplorerAppearanceChoice(value, allowed, fallback) {
        const text = String(value || '');
        const aliased = EXPLORER_FONT_ALIASES[text] || text;
        return allowed.includes(aliased) ? aliased : fallback;
    }

    function readExplorerMarkdownPref(key, allowed, fallback) {
        let stored = '';
        try {
            stored = window.localStorage.getItem(key) || '';
        } catch (err) {
            stored = '';
        }
        return normalizeExplorerAppearanceChoice(stored, allowed, fallback);
    }

    function explorerMarkdownAppearance() {
        if (workspaceExplorerAppearance) {
            return { ...workspaceExplorerAppearance };
        }
        return {
            preset: readExplorerMarkdownPref(
                EXPLORER_MD_PRESET_KEY, EXPLORER_MD_PRESETS, EXPLORER_MD_PRESET_DEFAULT
            ),
            font: readExplorerMarkdownPref(
                EXPLORER_MD_FONT_KEY, EXPLORER_MD_FONTS, EXPLORER_MD_FONT_DEFAULT
            ),
            sourceFont: readExplorerMarkdownPref(
                EXPLORER_SOURCE_FONT_KEY, EXPLORER_SOURCE_FONTS, EXPLORER_SOURCE_FONT_DEFAULT
            ),
        };
    }

    function cacheExplorerMarkdownAppearance(appearance) {
        try {
            window.localStorage.setItem(EXPLORER_MD_PRESET_KEY, appearance.preset);
            window.localStorage.setItem(EXPLORER_MD_FONT_KEY, appearance.font);
            window.localStorage.setItem(EXPLORER_SOURCE_FONT_KEY, appearance.sourceFont);
        } catch (err) {
            // Non-fatal cache: the manager remains the durable authority.
        }
    }

    function setExplorerWorkspaceAppearance(appearance, { cache = true } = {}) {
        const current = explorerMarkdownAppearance();
        workspaceExplorerAppearance = {
            preset: normalizeExplorerAppearanceChoice(
                appearance?.preset, EXPLORER_MD_PRESETS, current.preset
            ),
            font: normalizeExplorerAppearanceChoice(
                appearance?.font, EXPLORER_MD_FONTS, current.font
            ),
            sourceFont: normalizeExplorerAppearanceChoice(
                appearance?.sourceFont, EXPLORER_SOURCE_FONTS, current.sourceFont
            ),
        };
        if (cache) cacheExplorerMarkdownAppearance(workspaceExplorerAppearance);
        applyExplorerMarkdownAppearanceToAll();
        refreshExplorerMarkdownAppearanceMenu();
        return { ...workspaceExplorerAppearance };
    }

    function applyExplorerMarkdownAppearanceToElement(preview, appearance) {
        if (!preview) {
            return;
        }
        const { preset, font } = appearance || explorerMarkdownAppearance();
        EXPLORER_MD_PRESETS.forEach(name => preview.classList.remove(`md-preset-${name}`));
        EXPLORER_MD_FONTS.forEach(name => preview.classList.remove(`md-font-${name}`));
        preview.classList.add(`md-preset-${preset}`);
        preview.classList.add(`md-font-${font}`);
        preview.dataset.mdPreset = preset;
        preview.dataset.mdFont = font;
    }

    /* Same shape as the preview above: the class carries a `--source-view-font`
       custom property that the edit textarea, the per-row code cells inside the
       panel, and the diff renderers all read. Applied to the source view and to
       the diff panel separately — they are siblings, so the diff panel cannot
       inherit the property from the source view. */
    function applyExplorerSourceFontToElement(view, appearance) {
        if (!view) {
            return;
        }
        const { sourceFont } = appearance || explorerMarkdownAppearance();
        EXPLORER_SOURCE_FONTS.forEach(name => view.classList.remove(`source-font-${name}`));
        view.classList.add(`source-font-${sourceFont}`);
        view.dataset.sourceFont = sourceFont;
    }

    function applyExplorerMarkdownAppearanceToAll() {
        const appearance = explorerMarkdownAppearance();
        const applyToRoot = root => {
            root.querySelectorAll('.explorer-markdown-preview').forEach(preview => {
                applyExplorerMarkdownAppearanceToElement(preview, appearance);
            });
            root.querySelectorAll('.explorer-source-view, .explorer-diff-content').forEach(view => {
                applyExplorerSourceFontToElement(view, appearance);
            });
        };
        applyToRoot(document);
        /* Hidden session tabs keep their panes in detached cached fragments
           (terminals.js `cachedGroupViews`), which a document query cannot
           reach — restyle them in place too, so switching to another tab
           shows the new appearance instantly instead of the stale classes
           until a rebuild. Same contract as the cached-group restyle in
           applyAppConfigTerminalFont. */
        cachedGroupViews.forEach(cached => {
            if (cached?.fragment) {
                applyToRoot(cached.fragment);
            }
        });
    }

    function setExplorerMarkdownAppearance(patch) {
        const current = explorerMarkdownAppearance();
        const next = {
            preset: normalizeExplorerAppearanceChoice(
                patch?.preset, EXPLORER_MD_PRESETS, current.preset
            ),
            font: normalizeExplorerAppearanceChoice(
                patch?.font, EXPLORER_MD_FONTS, current.font
            ),
            sourceFont: normalizeExplorerAppearanceChoice(
                patch?.sourceFont, EXPLORER_SOURCE_FONTS, current.sourceFont
            ),
        };
        setExplorerWorkspaceAppearance(next);
        noteExplorerAppearanceChanged();
        return next;
    }

    function dismissExplorerMarkdownAppearanceMenu() {
        const menu = document.getElementById('explorer-md-menu');
        if (menu) {
            const anchor = document.querySelector('[data-explorer-md-appearance][aria-expanded="true"]');
            anchor?.setAttribute('aria-expanded', 'false');
            menu.remove();
        }
        document.removeEventListener('keydown', _explorerMarkdownMenuKeydown, true);
        document.removeEventListener('mousedown', _explorerMarkdownMenuOutside, true);
    }

    function _explorerMarkdownMenuOutside(event) {
        const menu = document.getElementById('explorer-md-menu');
        const anchor = document.querySelector('[data-explorer-md-appearance][aria-expanded="true"]');
        if (menu && !menu.contains(event.target) && !anchor?.contains(event.target)) {
            dismissExplorerMarkdownAppearanceMenu();
        }
    }

    function _explorerMarkdownMenuKeydown(event) {
        const menu = document.getElementById('explorer-md-menu');
        if (!menu) {
            return;
        }
        const items = Array.from(menu.querySelectorAll('button'));
        if (!items.length) {
            return;
        }
        const currentIndex = items.indexOf(document.activeElement);
        if (event.key === 'Escape') {
            event.preventDefault();
            dismissExplorerMarkdownAppearanceMenu();
        } else if (event.key === 'ArrowDown') {
            event.preventDefault();
            items[(currentIndex + 1) % items.length].focus();
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            items[(currentIndex - 1 + items.length) % items.length].focus();
        } else if (event.key === 'Tab') {
            event.preventDefault();
        }
    }

    function refreshExplorerMarkdownAppearanceMenu() {
        const menu = document.getElementById('explorer-md-menu');
        if (!menu) {
            return;
        }
        const appearance = explorerMarkdownAppearance();
        menu.querySelectorAll('[data-md-preset]').forEach(button => {
            button.setAttribute('aria-checked', button.dataset.mdPreset === appearance.preset ? 'true' : 'false');
        });
        menu.querySelectorAll('[data-md-font]').forEach(button => {
            button.setAttribute('aria-checked', button.dataset.mdFont === appearance.font ? 'true' : 'false');
        });
        menu.querySelectorAll('[data-source-font]').forEach(button => {
            button.setAttribute(
                'aria-checked',
                button.dataset.sourceFont === appearance.sourceFont ? 'true' : 'false'
            );
        });
    }

    function buildExplorerMarkdownMenuGroup(labelText, options, activeValue, datasetKey, onSelect) {
        const group = document.createElement('div');
        group.className = 'explorer-md-menu-group';
        group.setAttribute('role', 'group');
        group.setAttribute('aria-label', labelText);
        const label = document.createElement('span');
        label.className = 'explorer-md-menu-label';
        label.textContent = labelText;
        group.appendChild(label);
        options.forEach(({ value, label: optionLabel }) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.setAttribute('role', 'menuitemradio');
            button.dataset[datasetKey] = value;
            button.setAttribute('aria-checked', value === activeValue ? 'true' : 'false');
            const text = document.createElement('span');
            text.textContent = optionLabel;
            button.appendChild(text);
            button.addEventListener('click', () => onSelect(value));
            group.appendChild(button);
        });
        return group;
    }

    function showExplorerMarkdownAppearanceMenu(anchor, options = {}) {
        dismissExplorerMarkdownAppearanceMenu();
        if (!anchor) {
            return;
        }
        /* The preview groups are dropped for a file with no preview: the
           settings are workspace-global, so offering them here would be
           offering controls that change nothing the pane can show. */
        const includeMarkdown = options.includeMarkdown !== false;
        const appearance = explorerMarkdownAppearance();
        const menu = document.createElement('div');
        menu.id = 'explorer-md-menu';
        menu.setAttribute('role', 'menu');
        menu.setAttribute('aria-label', 'Viewer appearance');
        if (includeMarkdown) {
            menu.appendChild(buildExplorerMarkdownMenuGroup(
                'Preview theme',
                EXPLORER_MD_PRESETS.map(value => ({ value, label: EXPLORER_MD_PRESET_LABELS[value] })),
                appearance.preset,
                'mdPreset',
                value => setExplorerMarkdownAppearance({ preset: value })
            ));
            menu.appendChild(buildExplorerMarkdownMenuGroup(
                'Preview font',
                EXPLORER_MD_FONTS.map(value => ({ value, label: EXPLORER_MD_FONT_LABELS[value] })),
                appearance.font,
                'mdFont',
                value => setExplorerMarkdownAppearance({ font: value })
            ));
        }
        menu.appendChild(buildExplorerMarkdownMenuGroup(
            'Source font',
            EXPLORER_SOURCE_FONTS.map(value => ({ value, label: EXPLORER_SOURCE_FONT_LABELS[value] })),
            appearance.sourceFont,
            'sourceFont',
            value => setExplorerMarkdownAppearance({ sourceFont: value })
        ));

        menu.style.visibility = 'hidden';
        document.body.appendChild(menu);
        const anchorRect = anchor.getBoundingClientRect();
        const menuRect = menu.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        let left = anchorRect.right - menuRect.width;
        let top = anchorRect.bottom + 4;
        if (top + menuRect.height > vh - 8) {
            top = Math.max(8, anchorRect.top - menuRect.height - 4);
        }
        menu.style.left = `${Math.max(8, Math.min(left, vw - menuRect.width - 8))}px`;
        menu.style.top = `${Math.max(8, top)}px`;
        menu.style.visibility = 'visible';
        anchor.setAttribute('aria-expanded', 'true');
        menu.querySelector('button[aria-checked="true"]')?.focus();

        window.setTimeout(() => {
            document.addEventListener('mousedown', _explorerMarkdownMenuOutside, true);
        }, 0);
        document.addEventListener('keydown', _explorerMarkdownMenuKeydown, true);
    }

    function activeExplorerFileView(index) {
        const list = document.getElementById(`explorer-list-${index}`);
        const activeButton = list?.querySelector('[data-explorer-file-view][aria-selected="true"]');
        return activeButton?.dataset.explorerFileView || 'source';
    }

    function decorateExplorerSearchRanges(ranges, activeIndex) {
        return ranges.map((range, rangeIndex) => ({
            ...range,
            active: rangeIndex === activeIndex
        }));
    }

    function ensureExplorerMarkdownCollapsedLines(pane) {
        if (!pane) {
            return new Set();
        }
        const tab = explorerActiveTab(pane);
        if (!(tab.collapsedLines instanceof Set)) {
            tab.collapsedLines = new Set();
        }
        return tab.collapsedLines;
    }

    function explorerMarkdownHeadingLevel(line) {
        const match = String(line || '').match(/^(#{1,6})(?:\s+|$)/);
        return match ? match[1].length : 0;
    }

    function explorerMarkdownFenceMarker(line) {
        const match = String(line || '').match(/^ {0,3}(`{3,}|~{3,})/);
        if (!match) {
            return null;
        }
        return {
            char: match[1][0],
            length: match[1].length
        };
    }

    /* One document's records, kept for as long as that exact string is the one
       being asked about. The records are read-only to every caller, and the
       whole point of the cache is that they are asked for repeatedly against
       the *same* buffer: a single find keystroke used to walk the document
       three times over — once for the decoration maps and twice more inside
       the two row models — allocating a fresh record per line each pass.

       Sized against the live panes, not against a fixed 2. Two is the right
       number *per pane* — the Source rows and the editor's draft are both live
       during an edit and they are different strings — but the cache is
       module-level and shared, so a flat 2 meant a workspace with three
       explorer file panes evicted on every cross-pane call: the optimisation
       stopped applying in exactly the configuration whose total cost is
       highest. The ceiling keeps a pathological split from pinning a dozen
       documents at once.

       Emptied outright when the last explorer pane goes away (terminals.js).
       Records are heavy — a 4 MiB file is ~100k objects, plus the string —
       and an LRU only evicts on insert, so with nothing left to ask a question
       the final entries were pinned for the life of the page. While panes are
       open no such sweep is needed: a document nobody is looking at any more
       falls out of an LRU sized to the panes that are, which is what an LRU is
       for. */
    const EXPLORER_LINE_RECORD_CACHE_PER_PANE = 2;
    const EXPLORER_LINE_RECORD_CACHE_MAX = 8;
    const _explorerLineRecordCache = [];

    function explorerReleaseLineRecordCache() {
        _explorerLineRecordCache.length = 0;
    }

    function explorerLineRecordCacheLimit() {
        let panes = 0;
        for (const pane of terminals) {
            if (pane?._explorerMode === 'file') {
                panes += 1;
            }
        }
        return Math.min(
            Math.max(panes, 1) * EXPLORER_LINE_RECORD_CACHE_PER_PANE,
            EXPLORER_LINE_RECORD_CACHE_MAX
        );
    }

    /* `cache` is an optional caller-owned single slot, for content that has no
       business in the page-wide LRU. The in-place editor's draft is the case:
       it moves on every keystroke so it never *hits*, but it did `unshift` —
       and with one explorer pane open the limit is two slots, so every typing
       frame evicted both the previous draft and that pane's own file records.
       A pane-local slot keeps the draft's records for the two passes that want
       them (the splice frame and the settle frame) and leaves the shared cache
       to the panes that are reading files. */
    function explorerSourceLineRecords(content, cache) {
        const source = String(content || '');
        if (cache) {
            if (cache.records && cache.source === source) {
                return cache.records;
            }
            cache.source = source;
            cache.records = explorerBuildSourceLineRecords(source);
            return cache.records;
        }
        for (let at = 0; at < _explorerLineRecordCache.length; at += 1) {
            if (_explorerLineRecordCache[at].source === source) {
                return _explorerLineRecordCache[at].records;
            }
        }
        const records = explorerBuildSourceLineRecords(source);
        _explorerLineRecordCache.unshift({ source, records });
        _explorerLineRecordCache.length = Math.min(
            _explorerLineRecordCache.length, explorerLineRecordCacheLimit()
        );
        return records;
    }

    function explorerBuildSourceLineRecords(source) {
        const records = [];
        let lineNumber = 1;
        let index = 0;

        while (index <= source.length) {
            const newlineIndex = source.indexOf('\n', index);
            const lineEnd = newlineIndex === -1 ? source.length : newlineIndex;
            const rawLine = source.slice(index, lineEnd);
            records.push({
                number: lineNumber,
                start: index,
                text: rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
            });
            if (newlineIndex === -1) {
                break;
            }
            lineNumber += 1;
            index = newlineIndex + 1;
        }

        return records;
    }

    function explorerMarkdownHeadingLevels(records) {
        const levels = new Map();
        let fence = null;
        records.forEach(record => {
            const marker = explorerMarkdownFenceMarker(record.text);
            if (marker) {
                if (fence && marker.char === fence.char && marker.length >= fence.length) {
                    fence = null;
                } else if (!fence) {
                    fence = marker;
                }
                return;
            }
            if (fence) {
                return;
            }
            const level = explorerMarkdownHeadingLevel(record.text);
            if (level) {
                levels.set(record.number, level);
            }
        });
        return levels;
    }

    function explorerSourceLineNumberHtml(record, headingLevel, collapsed) {
        if (!headingLevel) {
            return `<span class="explorer-source-line-number">${record.number}</span>`;
        }
        return `
            <button
                type="button"
                class="explorer-source-line-number"
                data-explorer-markdown-section="${record.number}"
                aria-expanded="${collapsed ? 'false' : 'true'}"
                title="${collapsed ? 'Expand Markdown section (Alt: expand all at this level)' : 'Collapse Markdown section (Alt: collapse all at this level)'}"
            >
                <span class="explorer-source-chevron" aria-hidden="true">${collapsed ? UI_CHEVRON_RIGHT_ICON : UI_CHEVRON_DOWN_ICON}</span>
                <span>${record.number}</span>
            </button>
        `;
    }

    /* One gutter width for the whole document, published as a custom property
       on the lines block. Each row is its own grid container, so the per-row
       `minmax(42px, auto)` track this replaced sized every gutter from *that
       row's own* number: under 1000 lines every number fit the 42px floor and
       the columns agreed, but past it the four-digit rows started their code
       column further right than their three-digit neighbours.

       The width is arithmetic on the line count — no layout read, no observer,
       nothing that needs the element to be in the document — so it is equally
       safe to compute for a detached card. */
    function explorerSourceGutterWidthCss(lineCount, foldable) {
        const digits = String(Math.max(1, Number(lineCount) || 1)).length;
        /* 9px of cell padding either side plus the 1px separator, and on a
           foldable document the fold chevron (10px) and its 5px gap, which
           share the cell with the number on every heading row. */
        const fixed = 19 + (foldable ? 15 : 0);
        return `max(42px, calc(${digits}ch + ${fixed}px))`;
    }

    /* `options.foldControls: false` renders the Markdown heading rows without
       their fold <button>s — the in-place editor's underlay needs the rows for
       their geometry and their colour, but a focusable control beneath a
       covering textarea is an unclickable tab trap, and folding a buffer being
       typed into is incoherent anyway. The gutter still reserves the chevron's
       width, so the code column sits exactly where the read-only view put it
       and entering edit mode moves no glyph. */
    /* The rows a document renders, resolved once: which records survive the
       fold set, what heading level each carries, and the token map, gutter
       width and language class the whole build shares. Everything downstream —
       the one-string build below, the frame-sliced build, and the single-row
       repaints the find and the editor's underlay make — emits rows from this
       same model, so a row built one at a time is byte-identical to the same
       row built in bulk. */
    function explorerSourceRowModel(content, language, collapsedLines = new Set(), highlightedLines, options = {}) {
        const normalizedLanguage = normalizeExplorerLanguage(language);
        const records = explorerSourceLineRecords(content, options && options.recordCache);
        const languageClass = explorerLanguageClass(language);
        const codeClass = languageClass ? ` language-${languageClass}` : '';
        const markdownDocument = normalizedLanguage === 'markdown';
        const foldControls = !options || options.foldControls !== false;
        const markdownHeadings = markdownDocument
            ? explorerMarkdownHeadingLevels(records)
            : new Map();
        /* Folds survive a find: a search used to unfold the whole document so
           no match could hide inside a collapsed section, which threw away the
           reader's fold state on every Ctrl+F. Only the sections a match
           actually lands in are opened now, by
           explorerRevealMarkdownSearchMatches() before this renders. */
        const allowMarkdownCollapse = markdownDocument && foldControls;
        // Whole-document Highlight.js pass (Phase 1); null for unsupported
        // languages, the log/markdown special renderers, oversized files, or any
        // Highlight.js failure, in which case each line uses the fallback lexer.
        // Callers with a pane pass the pane-cached map in (undefined here means
        // "tokenize now"); a passed-in null is a legitimate cached miss.
        const highlightPending = highlightedLines === EXPLORER_HIGHLIGHT_PENDING;
        const runs = highlightedLines !== undefined
            ? (highlightPending ? null : highlightedLines)
            : explorerHighlightDocumentLines(content, normalizedLanguage);
        const rows = [];
        let hiddenUntilHeadingLevel = 0;

        records.forEach(record => {
            const headingLevel = markdownHeadings.get(record.number) || 0;
            if (allowMarkdownCollapse && hiddenUntilHeadingLevel) {
                if (!headingLevel || headingLevel > hiddenUntilHeadingLevel) {
                    return;
                }
                hiddenUntilHeadingLevel = 0;
            }

            const collapsed = allowMarkdownCollapse && headingLevel && collapsedLines.has(record.number);
            rows.push({ record, headingLevel, collapsed: Boolean(collapsed) });

            if (collapsed) {
                hiddenUntilHeadingLevel = headingLevel;
            }
        });

        return {
            records,
            rows,
            runs,
            highlightPending,
            language,
            codeClass,
            foldControls,
            // Width follows the document, not the controls: a Markdown underlay
            // with its buttons suppressed still keeps the read-only gutter, so the
            // code column does not shift under the caret on entering edit mode.
            gutterWidth: explorerSourceGutterWidthCss(records.length, markdownDocument)
        };
    }

    /* One row's code cell — the only part of a row a decoration change can
       move. The find repaints exactly this, leaving the row <div> (and with it
       the change-mark attribute and its marker button) standing. */
    function explorerSourceRowCodeHtml(model, row, searchRanges) {
        const { record, headingLevel } = row;
        const lineHtml = model.runs
            ? explorerRenderHighlightedRuns(model.runs.get(record.number), searchRanges)
            : (model.highlightPending
                ? explorerMarkedEscHtml(record.text, record.start, searchRanges)
                : highlightExplorerCode(record.text, model.language, searchRanges, record.start));
        // Heading-only Markdown tokeniser (OD-8): the fence-aware heading map
        // already computed for section collapse doubles as the highlighter,
        // so heading lines get a distinct token colour without a full grammar.
        const contentHtml = headingLevel
            ? `<span class="explorer-md-source-heading explorer-md-source-heading-${headingLevel}">${lineHtml}</span>`
            : lineHtml;
        return contentHtml || '&nbsp;';
    }

    function explorerSourceRowHtml(model, row, searchRanges) {
        return `
                <div class="explorer-source-line" data-explorer-line="${row.record.number}">
                    ${explorerSourceLineNumberHtml(row.record, model.foldControls ? row.headingLevel : 0, row.collapsed)}
                    <code class="explorer-source-line-code${model.codeClass}">${explorerSourceRowCodeHtml(model, row, searchRanges)}</code>
                </div>
            `;
    }

    function explorerSourceLinesOpenTag(model) {
        return `<div class="explorer-source-lines" style="--explorer-source-gutter-width: ${model.gutterWidth};">`;
    }

    function renderExplorerSourceLines(content, language, searchRanges = [], collapsedLines = new Set(), highlightedLines, options = {}) {
        const model = explorerSourceRowModel(content, language, collapsedLines, highlightedLines, options);
        const rows = model.rows.map(row => explorerSourceRowHtml(model, row, searchRanges));
        return `${explorerSourceLinesOpenTag(model)}${rows.join('')}</div>`;
    }

    /* A match hidden inside a collapsed Markdown section has no row to
       highlight and nothing for Enter to scroll to, so the sections that
       contain matches are opened — and only those, leaving every other fold as
       the reader left it. Called once per freshly computed result set (not on
       every re-render), so collapsing a section during a live find sticks
       instead of springing straight back open. */
    function explorerRevealMarkdownSearchMatches(index, searchRanges) {
        const pane = terminals[index];
        if (!pane
            || !searchRanges.length
            || normalizeExplorerLanguage(pane._explorerFileLanguage || '') !== 'markdown') {
            return;
        }
        const collapsedLines = ensureExplorerMarkdownCollapsedLines(pane);
        if (!collapsedLines.size) {
            return;
        }

        const content = pane._explorerFileContent || '';
        const records = explorerSourceLineRecords(content);
        const headings = explorerMarkdownHeadingLevels(records);
        const ordered = Array.from(headings.entries())
            .map(([number, level]) => ({ number, level }))
            .sort((a, b) => a.number - b.number);
        let revealed = false;

        Array.from(collapsedLines).forEach(lineNumber => {
            const level = headings.get(lineNumber);
            // Records are 0-based, so records[lineNumber] is the line *after*
            // the heading — where the hidden body starts.
            const bodyStart = records[lineNumber]?.start;
            if (!level || bodyStart === undefined) {
                return;
            }
            const headingIndex = ordered.findIndex(entry => entry.number === lineNumber);
            const next = ordered.slice(headingIndex + 1).find(entry => entry.level <= level);
            const bodyEnd = next ? records[next.number - 1].start : content.length;
            if (searchRanges.some(range => range.start >= bodyStart && range.start < bodyEnd)) {
                collapsedLines.delete(lineNumber);
                revealed = true;
            }
        });

        if (revealed) {
            persistExplorerTabsToSession(index);
        }
    }

    function toggleExplorerMarkdownSection(index, lineNumber, { allSameLevel = false } = {}) {
        const pane = terminals[index];
        if (!pane || normalizeExplorerLanguage(pane._explorerFileLanguage || '') !== 'markdown') {
            return;
        }
        const collapsedLines = ensureExplorerMarkdownCollapsedLines(pane);
        if (allSameLevel) {
            // Alt+click fans the toggle out to every heading sharing the clicked
            // heading's level. The new state mirrors the clicked heading: if it
            // was expanded we collapse the whole level, and vice versa.
            const levels = explorerMarkdownHeadingLevels(
                explorerSourceLineRecords(pane._explorerFileContent || '')
            );
            const targetLevel = levels.get(lineNumber);
            if (!targetLevel) {
                return;
            }
            const collapse = !collapsedLines.has(lineNumber);
            levels.forEach((level, number) => {
                if (level !== targetLevel) {
                    return;
                }
                if (collapse) {
                    collapsedLines.add(number);
                } else {
                    collapsedLines.delete(number);
                }
            });
        } else if (collapsedLines.has(lineNumber)) {
            collapsedLines.delete(lineNumber);
        } else {
            collapsedLines.add(lineNumber);
        }
        const tab = explorerActiveTab(pane);
        tab.collapsedIdentity = explorerFileContentIdentity(
            pane._explorerFilePath,
            pane._explorerFileContent,
            pane._explorerDiffCommit,
            pane._explorerDiffMode
        );
        persistExplorerTabsToSession(index);
        const state = ensureExplorerSearchState(pane, 'file');
        if (state.query && activeExplorerFileView(index) === 'source') {
            applyExplorerSearch(index);
        } else {
            renderExplorerSource(index);
        }
    }

    function wireExplorerMarkdownSectionControls(index) {
        const code = document.getElementById(`explorer-code-${index}`);
        if (!code) {
            return;
        }
        code.querySelectorAll('[data-explorer-markdown-section]').forEach(button => {
            if (button.dataset.bound) {
                return;
            }
            button.dataset.bound = 'true';
            button.addEventListener('click', (event) => {
                toggleExplorerMarkdownSection(index, Number(button.dataset.explorerMarkdownSection || 0), {
                    allSameLevel: event.altKey
                });
            });
        });
    }

    function renderExplorerSource(index, searchRanges = []) {
        const pane = terminals[index];
        const code = document.getElementById(`explorer-code-${index}`);
        if (!pane || !code) {
            return;
        }
        /* While an in-place edit is open the Source panel holds the editor's
           textarea, not rendered rows — the editor owns this element until it
           is torn down. Rebuilding it here would drop the live draft on the
           floor and leave the Save/Cancel chrome pointing at nothing (the
           user's only way out being Cancel). Every caller that legitimately
           leaves edit mode clears the state first, so this costs them nothing;
           the one that made it reachable was a group switch, whose cached-view
           restore re-applies the Source view through applyExplorerSearch. */
        if (pane._explorerEdit) {
            explorerAbandonSourceRenderJob(pane);
            return;
        }

        /* Above the tier's ceiling the per-line renderer is the freeze, not a
           step towards it: 200k lines is 600k elements before a single
           highlight token is counted. Render the bounded plain chunks and
           stop — no fold wiring, no occurrence tint, no change marks, none of
           which have rows to attach to. */
        if (explorerPaneSourceTier(pane) === 'large') {
            renderExplorerLargeSource(index, code);
            return;
        }

        const content = pane._explorerFileContent || '';
        const language = pane._explorerFilePlain ? '' : (pane._explorerFileLanguage || '');
        const collapsedLines = ensureExplorerMarkdownCollapsedLines(pane);
        const collapsedKey = explorerSourceCollapsedKey(collapsedLines);

        if (explorerReuseRenderedSource(index, code, {
            content, language, collapsedKey, searchRanges
        })) {
            return;
        }

        const highlightedLines = explorerHighlightLinesForRender(
            index, pane, content, normalizeExplorerLanguage(language)
        );
        const model = explorerCachedSourceRowModel(
            pane, content, language, collapsedLines, collapsedKey, highlightedLines
        );
        const chunking = explorerRepaintPolicy()?.chunkPlan(model.rows.length, {
            async: typeof window.requestAnimationFrame === 'function'
        });
        explorerCancelSourceRenderJob(pane);
        /* A rebuild of the *same document* is a presentation change, not a
           navigation: the worker's syntax colours arriving, or a find whose
           marks moved too widely to repaint in place. `code` is the scroller
           itself, so replacing its rows sends the reader back to line 1 —
           which is what made a large file jump to the top a beat after it
           opened, and again on every wide find. Same content, same rows, same
           geometry: hold the offset across the build. */
        const keepScroll = pane._explorerSourceRender
            && pane._explorerSourceRender.content === content
            ? { top: code.scrollTop, left: code.scrollLeft }
            : null;
        const chunked = Boolean(chunking && chunking.chunked);
        /* A frame-sliced rebuild assembles its rows off-screen when there are
           rows on screen to protect, for the same reason the large tier does:
           emptying the scroller first collapses the document under the reader,
           so the browser parks them at the top for every frame the build lasts
           and the offset is only handed back at the end. That is the flash the
           syntax colours arriving used to cause on a big file, and the one a
           save or a watcher refresh causes on any of them. A one-pass build
           has no frames to flash across, and a first paint has nothing to keep
           on screen, so both still go straight into the panel. */
        const replacing = chunked ? explorerRenderedSourceContainer(code) : null;
        /* One string and one parse for a document that can afford it — which
           is nearly all of them, and is cheaper than any number of appends. */
        const markup = chunked
            ? `${explorerSourceLinesOpenTag(model)}</div>`
            : `${explorerSourceLinesOpenTag(model)}${model.rows
                .map(row => explorerSourceRowHtml(model, row, searchRanges)).join('')}</div>`;
        let container;
        if (replacing) {
            container = explorerDetachedElement(markup);
        } else {
            code.innerHTML = markup;
            container = explorerRenderedSourceContainer(code);
        }
        /* The rows are identified by a token stamped on them rather than by a
           reference to the element: a pane that leaves file view would keep
           the whole detached row tree alive for as long as it held that
           reference, which on a large file is the biggest thing in the pane. */
        _explorerSourceRenderToken += 1;
        const token = String(_explorerSourceRenderToken);
        if (container) {
            container.dataset.explorerRender = token;
        }
        pane._explorerSourceRender = {
            token, content, language, collapsedKey, ranges: searchRanges, keepScroll,
            /* While a swap build runs, the rows on screen still carry the
               *previous* token — so "is this still my surface?" is asked about
               the container this build is going to replace, not about the one
               it is filling. A panel the editor or a tab switch took over
               fails that check exactly as before. */
            replacing: replacing || null
        };

        if (!chunked) {
            explorerFinishSourceRender(index);
            return;
        }
        /* Frame-sliced build: the rest of the app keeps painting throughout.
           Everything that reads the rows the moment a render "returns" goes
           through whenExplorerSourceRendered(), which is immediate for the
           synchronous build above and queued for this one. */
        explorerRunSourceRenderJob(index, code, container, model, searchRanges, chunking.size);
    }

    /* Put the finished rows on screen in place of the ones the reader has been
       looking at, holding their offset across the exchange. One task, so the
       collapsed intermediate state is never painted. */
    function explorerSwapRenderedSourceContainer(code, previous, container) {
        const top = code.scrollTop;
        const left = code.scrollLeft;
        code.replaceChild(container, previous);
        code.scrollTop = top;
        code.scrollLeft = left;
    }

    function explorerSourceCollapsedKey(collapsedLines) {
        return Array.from(collapsedLines || []).sort((a, b) => a - b).join(',');
    }

    /* The row model the Source view renders from, kept for as long as every
       input to it is unchanged. A find keystroke asks for it twice — once to
       decide which rows moved and once to emit them — and neither pass changes
       the document, the language, the fold set or the token map. Rebuilding it
       each time meant re-deriving every record and, on Markdown, re-running
       the fence-aware heading scan over the whole file per keypress.

       Identity comparison throughout, including on `highlightedLines`: the
       highlight map is a stable reference held on the pane, and the pending
       sentinel is a Symbol, so `===` distinguishes "still plain" from "the
       worker answered" without inspecting either. */
    function explorerCachedSourceRowModel(pane, content, language, collapsedLines, collapsedKey, highlightedLines) {
        const cached = pane?._explorerSourceModel;
        if (cached
            && cached.content === content
            && cached.language === language
            && cached.collapsedKey === collapsedKey
            && cached.highlightedLines === highlightedLines) {
            return cached.model;
        }
        const model = explorerSourceRowModel(content, language, collapsedLines, highlightedLines);
        if (pane) {
            pane._explorerSourceModel = {
                content, language, collapsedKey, highlightedLines, model
            };
        }
        return model;
    }

    /* The DOM-free repaint policy (explorer-repaint.js). Looked up rather than
       captured so a page that somehow loaded without it falls back to a full
       rebuild every time — today's behaviour — instead of throwing. */
    function explorerRepaintPolicy() {
        return (typeof window !== 'undefined' && window.GridVibeExplorerRepaint) || null;
    }

    let _explorerSourceRenderToken = 0;

    function explorerRenderedSourceContainer(code) {
        return code ? code.querySelector(':scope > .explorer-source-lines') : null;
    }

    function explorerAppendSourceRows(container, model, searchRanges, from, to) {
        if (!container) {
            return;
        }
        const rows = [];
        for (let at = from; at < to; at += 1) {
            rows.push(explorerSourceRowHtml(model, model.rows[at], searchRanges));
        }
        container.insertAdjacentHTML('beforeend', rows.join(''));
    }

    /* Everything that used to sit at the tail of a rebuild. It runs once per
       completed build — after the last slice of a chunked one — and never
       after a skipped or decorated render, because a render that destroyed no
       rows has nothing to re-attach. */
    function explorerFinishSourceRender(index) {
        explorerRestoreHeldSourceScroll(index);
        wireExplorerMarkdownSectionControls(index);
        // The rebuilt rows dropped the nodes the occurrence tint was anchored
        // to; re-derive it from whatever selection survived the render.
        scheduleExplorerOccurrenceHighlight();
        // Re-paint the cached HEAD change marks onto the fresh rows (cheap;
        // no fetch — loads are triggered by the change signals only).
        applyExplorerChangeMarks(index);
        explorerFlushSourceRenderCallbacks(terminals[index]);
    }

    /* One-use: the offset belongs to the build that captured it, and a later
       render that legitimately moves the reader (a new file, a jump to a
       match) must not be pulled back to it. */
    function explorerRestoreHeldSourceScroll(index) {
        const pane = terminals[index];
        const held = pane?._explorerSourceRender?.keepScroll;
        if (!held) {
            return;
        }
        pane._explorerSourceRender.keepScroll = null;
        const code = document.getElementById(`explorer-code-${index}`);
        if (!code) {
            return;
        }
        code.scrollTop = held.top;
        code.scrollLeft = held.left;
    }

    function explorerFlushSourceRenderCallbacks(pane) {
        const pending = pane?._explorerSourceRenderCallbacks;
        if (!pending || !pending.length) {
            return;
        }
        pane._explorerSourceRenderCallbacks = [];
        pending.forEach(callback => {
            try {
                callback();
            } catch (err) {
                console.error('Explorer source render callback failed', err);
            }
        });
    }

    /* Read the rows once they exist. Immediate when no build is in flight —
       which is every file small enough to render in one pass, so the ordering
       those callers have always relied on is unchanged — and queued onto the
       running build otherwise. A superseded build hands its queue to the build
       that replaced it, so a scroll restore is never dropped on the floor. */
    function whenExplorerSourceRendered(index, callback) {
        const pane = terminals[index];
        if (typeof callback !== 'function') {
            return;
        }
        if (!pane || !pane._explorerSourceRenderJob) {
            callback();
            return;
        }
        (pane._explorerSourceRenderCallbacks || (pane._explorerSourceRenderCallbacks = []))
            .push(callback);
    }

    function explorerCancelSourceRenderJob(pane) {
        if (!pane || !pane._explorerSourceRenderJob) {
            return;
        }
        if (typeof window.cancelAnimationFrame === 'function') {
            window.cancelAnimationFrame(pane._explorerSourceRenderJob.frame);
        }
        pane._explorerSourceRenderJob = null;
    }

    /* A card that left the document is a build nobody can see.

       Neither sliced job stops on being detached: the row build stops on a
       newer render token or on the panel it was filling being replaced, and
       the large tier's chunk pacer on the same two things. So opening a large
       file and switching groups left a job spending its whole frame budget
       appending rows into a detached tree, in competition with the incoming
       group's attach, fit and paint.

       Suspension is not cancellation. The rows are still wanted and so are the
       readers queued behind them, so the job keeps its position and resumes
       when the card comes back — and if the group is closed while suspended,
       explorerAbandonSourceRenderJob() still flushes that queue. */
    function explorerSuspendSourceRenderJob(pane) {
        const job = pane?._explorerSourceRenderJob;
        if (!job || job.suspended || typeof job.step !== 'function') {
            return;
        }
        if (job.frame && typeof window.cancelAnimationFrame === 'function') {
            window.cancelAnimationFrame(job.frame);
        }
        job.frame = 0;
        job.suspended = true;
    }

    function explorerResumeSourceRenderJob(pane) {
        const job = pane?._explorerSourceRenderJob;
        if (!job || !job.suspended) {
            return;
        }
        job.suspended = false;
        job.frame = window.requestAnimationFrame(job.step);
    }

    /* The panel stopped being rows — the editor took it, or the large tier
       replaced them with plain chunks — so no further slice may land. The
       queued readers still run: they were waiting on "the rows are final",
       and they are, just not as rows. Leaving them queued would strand a
       scroll restore on a pane the reader is still looking at. */
    function explorerAbandonSourceRenderJob(pane) {
        explorerCancelSourceRenderJob(pane);
        explorerFlushSourceRenderCallbacks(pane);
    }

    /* The whole pane is being discarded — closed with its grid, replaced by
       another mode in place, or dropped with its cached group.

       This is deliberately *not* explorerAbandonSourceRenderJob(): that one
       belongs to a pane that stays live, where the rows are final by some
       other route and the queued readers are still owed an answer. Here there
       is no reader left to satisfy, and running the queue would be actively
       wrong — the callbacks close over `index` and re-read global
       `terminals[index]`, which by then holds the pane that replaced this one
       (or, for a cached-group close, another group's pane in the same slot).

       The frame stops, the queue is dropped unexecuted, and every in-flight
       request goes with it so a pane nobody can see stops holding a fetch or
       a worker. */
    function explorerReleasePaneWork(pane) {
        if (!pane) {
            return;
        }
        explorerCancelSourceRenderJob(pane);
        pane._explorerSourceRenderCallbacks = [];
        cancelExplorerRequestSlots(pane);
    }

    /* The unit a frame emits rows in, and how long a frame may spend emitting
       them. The chunk plan's slice size is a per-frame *ceiling*; this budget
       is what actually ends a frame, because "rows" is not a unit of time —
       2,000 rows of a minified bundle and 2,000 rows of a config file are two
       orders of magnitude apart, and a frame that overruns is a frame the
       window does not paint and a click the window does not answer. Filling in
       from the top is only an improvement over freezing if the frames in
       between are short enough to be interrupted. */
    const EXPLORER_SOURCE_RENDER_BATCH_ROWS = 250;
    const EXPLORER_SOURCE_RENDER_BUDGET_MS = 8;

    function explorerRunSourceRenderJob(index, code, container, model, searchRanges, size) {
        const pane = terminals[index];
        const job = { frame: 0, at: 0, suspended: false, step: null };
        pane._explorerSourceRenderJob = job;
        /* Null for a first paint, which fills the panel directly; otherwise
           the rows on screen that this build will replace when it finishes. */
        const replacing = pane._explorerSourceRender?.replacing || null;
        const onScreen = replacing || container;
        const step = () => {
            job.frame = 0;
            /* Two ways this build stops being the one that should finish: a
               newer render replaced it (identity, not a flag), or the panel it
               was filling — or the one it is going to swap itself into — is no
               longer the panel on screen. Either way the remaining rows are
               rows nobody asked for. A suspended job is neither — it holds its
               position until the card is back. */
            if (job.suspended) {
                return;
            }
            if (pane._explorerSourceRenderJob !== job) {
                // A newer build owns the pane and the queue with it.
                return;
            }
            if (explorerRenderedSourceContainer(code) !== onScreen) {
                /* The panel this was filling was replaced by something that is
                   not a newer build — the in-place editor's textarea, the
                   large tier's chunks, a tab switch. No further slice may
                   land, and this job stays `_explorerSourceRenderJob` forever
                   unless it stands down here: every later
                   whenExplorerSourceRendered() would queue behind a build that
                   can never finish, which silently kills the pane's scroll
                   restores, its selection restore and its Git-active sync. */
                explorerAbandonSourceRenderJob(pane);
                return;
            }
            const started = performance.now();
            const frameEnd = Math.min(model.rows.length, job.at + size);
            while (job.at < frameEnd) {
                const to = Math.min(frameEnd, job.at + EXPLORER_SOURCE_RENDER_BATCH_ROWS);
                explorerAppendSourceRows(container, model, searchRanges, job.at, to);
                job.at = to;
                if (performance.now() - started >= EXPLORER_SOURCE_RENDER_BUDGET_MS) {
                    break;
                }
            }
            if (job.at < model.rows.length) {
                job.frame = window.requestAnimationFrame(step);
                return;
            }
            pane._explorerSourceRenderJob = null;
            if (replacing) {
                explorerSwapRenderedSourceContainer(code, replacing, container);
                if (pane._explorerSourceRender) {
                    pane._explorerSourceRender.replacing = null;
                }
            }
            explorerFinishSourceRender(index);
        };
        job.step = step;
        job.frame = window.requestAnimationFrame(step);
    }

    /* The rows already on screen, kept. `true` means this render is done —
       either because nothing it would paint differs from what is there
       (a file open renders the rows and then applyExplorerSearch renders them
       again; with no query the second pass has nothing to say), or because
       only the search marks moved and the rows carrying them have been
       repainted in place.

       Row <div>s survive a decoration repaint, and with them the change-mark
       attribute, its marker button, the open change peek and the fold
       controls' bound listeners — which is why none of those are re-applied
       here. Only the occurrence tint is, because its ranges point at the text
       nodes the repaint replaced. */
    function explorerReuseRenderedSource(index, code, next) {
        const pane = terminals[index];
        const policy = explorerRepaintPolicy();
        const previous = pane._explorerSourceRender;
        if (!policy || !previous) {
            return false;
        }
        if (previous.stale) {
            // The syntax colours arrived: every row's content changed even
            // though the document did not.
            return false;
        }
        const container = explorerRenderedSourceContainer(code);
        /* While a swap build is running, the rows on screen are the ones it is
           about to replace and still carry the previous render's token. The
           surface is theirs until the swap lands, so identity is asked about
           that element; only once the swap has happened does the token on the
           rendered rows answer for it again. Either way the question is the
           same one — is what is on screen still this render's? — so a panel
           the editor or a tab switch took over still fails it and rebuilds. */
        const sameSurface = previous.replacing
            ? container === previous.replacing
            : Boolean(container) && container.dataset.explorerRender === previous.token;
        const previousRanges = previous.ranges || [];
        /* The overwhelmingly common repaint — no query before, no query now —
           needs no records, no maps and no plan: there is nothing a search
           mark could have moved. */
        const decorationsPossible = Boolean(previousRanges.length || next.searchRanges.length);
        const records = (sameSurface && decorationsPossible)
            ? explorerSourceLineRecords(next.content)
            : [];
        const plan = policy.sourceRenderPlan({
            sameSurface,
            pending: Boolean(pane._explorerSourceRenderJob),
            contentChanged: previous.content !== next.content,
            languageChanged: previous.language !== next.language,
            foldsChanged: previous.collapsedKey !== next.collapsedKey,
            // What a rebuild would cost, so the ceiling on what a repaint may
            // touch is read against the document rather than as an absolute.
            rowCount: records.length,
            previousDecorations: policy.decorationMap(records, previousRanges),
            nextDecorations: policy.decorationMap(records, next.searchRanges)
        });
        if (plan.mode === 'full') {
            return false;
        }
        previous.ranges = next.searchRanges;
        if (plan.mode === 'skip') {
            return true;
        }

        const highlightedLines = explorerHighlightLinesForRender(
            index, pane, next.content, normalizeExplorerLanguage(next.language)
        );
        const model = explorerCachedSourceRowModel(
            pane,
            next.content,
            next.language,
            ensureExplorerMarkdownCollapsedLines(pane),
            next.collapsedKey,
            highlightedLines
        );
        const byLine = new Map();
        model.rows.forEach(row => byLine.set(row.record.number, row));
        const cells = explorerRenderedSourceCells(container, plan.lines);
        plan.lines.forEach(line => {
            const row = byLine.get(line);
            const cell = cells.get(line);
            if (row && cell) {
                cell.innerHTML = explorerSourceRowCodeHtml(model, row, next.searchRanges);
            }
        });
        scheduleExplorerOccurrenceHighlight();
        return true;
    }

    /* Past this many rows, finding them one at a time costs more than walking
       the container once. Each `querySelector('[data-explorer-line="N"]')` is
       a fresh scan of the whole row list, so a find matching a thousand lines
       in a twenty-thousand-row file walked twenty million nodes to repaint a
       thousand cells — that, and not the parsing, is what made typing in Find
       lag behind the keyboard. Below the threshold the walk is the more
       expensive of the two, and the commonest repaint of all — stepping from
       one match to the next — touches exactly two rows. */
    const EXPLORER_SOURCE_CELL_WALK_MIN_ROWS = 16;

    /* Line number → that row's code cell, for the lines about to be repainted. */
    function explorerRenderedSourceCells(container, lines) {
        const cells = new Map();
        const wanted = Array.isArray(lines) ? lines : [];
        if (!container || !wanted.length) {
            return cells;
        }
        if (wanted.length < EXPLORER_SOURCE_CELL_WALK_MIN_ROWS) {
            wanted.forEach(line => {
                const cell = container.querySelector(
                    `.explorer-source-line[data-explorer-line="${line}"] > code`
                );
                if (cell) {
                    cells.set(line, cell);
                }
            });
            return cells;
        }
        const rows = container.children;
        const needed = new Set(wanted);
        for (let at = 0; at < rows.length; at += 1) {
            const row = rows[at];
            const line = Number(row.dataset?.explorerLine);
            if (!needed.has(line)) {
                continue;
            }
            /* Not `lastElementChild`: a changed row also carries the change
               marker button, appended after the code cell. */
            const cell = row.querySelector(':scope > code');
            if (cell) {
                cells.set(line, cell);
            }
        }
        return cells;
    }

    function explorerPreviewBlockLanguage(code) {
        const match = String(code.className || '').match(/(?:^|\s)language-([\w+#.-]+)/i);
        return match ? normalizeExplorerLanguage(match[1]) : '';
    }

    function highlightExplorerPreviewCode(root) {
        if (!root) {
            return;
        }
        root.querySelectorAll('pre > code').forEach(code => {
            const language = explorerPreviewBlockLanguage(code);
            if (!language) {
                return;
            }
            if (language === 'mermaid') {
                return;
            }
            const pre = code.parentElement;
            pre.classList.add('explorer-preview-code');
            pre.dataset.lang = language.toUpperCase();
            // Plain text/markdown blocks stay unstyled; string/number rules would mislead there.
            if (language === 'text' || language === 'markdown') {
                return;
            }
            code.innerHTML = highlightExplorerCode(code.textContent, language);
        });
    }

    let explorerMermaidRenderId = 0;

    /* Render each diagram as it comes into view rather than all of them up
       front. A README with a dozen diagrams used to render every one in a
       sequential await loop the moment the file opened — before the reader had
       even chosen the Preview tab — which is seconds of frozen pane for
       pictures mostly below the fold. Where IntersectionObserver is missing the
       eager loop is still correct, so it simply runs.

       The observer is stored on the preview element and disconnected when the
       panel is rebuilt, so a pane switching files does not accumulate them. */
    function renderExplorerMermaidLazily(preview, blocks) {
        if (typeof window.IntersectionObserver !== 'function') {
            return false;
        }
        preview._explorerMermaidObserver?.disconnect();
        const pending = new Set(blocks.map(code => code.parentElement).filter(Boolean));
        const observer = new window.IntersectionObserver(entries => {
            entries.forEach(entry => {
                if (!entry.isIntersecting || !pending.has(entry.target)) {
                    return;
                }
                pending.delete(entry.target);
                observer.unobserve(entry.target);
                const code = entry.target.querySelector('code.language-mermaid');
                if (code) {
                    const blockOffset = entry.target.offsetTop;
                    renderExplorerMermaidBlock(preview, code).then(() => {
                        reapplyExplorerPreviewScrollAfterMermaid(preview, blockOffset);
                    });
                }
            });
        }, { root: preview, rootMargin: '200px' });
        pending.forEach(block => observer.observe(block));
        preview._explorerMermaidObserver = observer;
        return true;
    }

    async function renderExplorerMermaid(preview) {
        if (!preview || !window.mermaid) {
            return;
        }
        const blocks = Array.from(preview.querySelectorAll('pre > code.language-mermaid'));
        if (!blocks.length) {
            preview._explorerMermaidObserver?.disconnect();
            preview._explorerMermaidObserver = null;
            return;
        }
        window.mermaid.initialize({
            startOnLoad: false,
            securityLevel: 'strict',
            theme: currentResolvedTheme() === 'dark' ? 'dark' : 'default',
            suppressErrorRendering: true
        });
        if (renderExplorerMermaidLazily(preview, blocks)) {
            return;
        }
        for (const code of blocks) {
            const blockOffset = code.parentElement?.offsetTop;
            await renderExplorerMermaidBlock(preview, code);
            reapplyExplorerPreviewScrollAfterMermaid(preview, blockOffset);
        }
    }

    async function renderExplorerMermaidBlock(preview, code) {
        const source = code.textContent || '';
        const pre = code.parentElement;
        if (!pre) {
            return;
        }
        const diagram = document.createElement('div');
        diagram.className = 'explorer-mermaid';
        pre.replaceWith(diagram);
        try {
            explorerMermaidRenderId += 1;
            const rendered = await window.mermaid.render(
                `explorer-mermaid-${explorerMermaidRenderId}`,
                source
            );
            if (!preview.contains(diagram)) {
                return;
            }
            diagram.innerHTML = rendered.svg;
            rendered.bindFunctions?.(diagram);
        } catch (error) {
            diagram.classList.add('explorer-mermaid-error');
            const message = String(error?.message || 'Invalid diagram').split('\n')[0];
            diagram.textContent = `Mermaid diagram error: ${message}`;
            return;
        }
        /* Ctrl+scroll zooms the rendered diagram (notes 3); double-click
           resets it. Bound on the diagram box so the page-zoom default is
           suppressed only while the pointer is over the diagram. */
        enableExplorerWheelZoom(diagram, diagram.querySelector('svg'));
    }

    /* Ctrl+scroll zoom for a scrollable view (container) around a scalable
       target (an <img> or mermaid <svg>). Double-click restores 1×; while
       zoomed the surface shows a hand cursor and can be dragged to pan.

       Zoom resizes the target's LAYOUT box (explicit px width/height) rather
       than applying a CSS transform. A transform only overflows *visually*, so
       the scroll container never gained a real scroll region and the top/left
       corners stayed unreachable no matter the alignment. A real size change
       gives overflow:auto a true region, so scrollbars and drag-pan reach every
       edge (notes 3 redo). */
    function enableExplorerWheelZoom(container, target) {
        if (!container || !target || container._wheelZoomBound) {
            return;
        }
        container._wheelZoomBound = true;
        let scale = 1;
        let baseW = 0;
        let baseH = 0;

        const applyZoom = () => {
            if (scale <= 1) {
                target.style.width = '';
                target.style.height = '';
                target.style.maxWidth = '';
                target.style.maxHeight = '';
                container.classList.remove('explorer-zoomable');
                return;
            }
            target.style.maxWidth = 'none';
            target.style.maxHeight = 'none';
            target.style.width = `${Math.round(baseW * scale)}px`;
            target.style.height = `${Math.round(baseH * scale)}px`;
            container.classList.add('explorer-zoomable');
        };

        const zoomBy = factor => {
            // Capture the fitted (scale-1) size the first time we grow, so the
            // scale stays relative to what the user actually sees on screen.
            if (scale === 1) {
                const rect = target.getBoundingClientRect();
                baseW = rect.width;
                baseH = rect.height;
            }
            if (!baseW || !baseH) {
                return;
            }
            scale = Math.min(
                EXPLORER_WHEEL_ZOOM_MAX,
                Math.max(1, scale * factor)
            );
            applyZoom();
        };

        container.addEventListener('wheel', event => {
            if (!event.ctrlKey) {
                return;
            }
            event.preventDefault();
            zoomBy(event.deltaY < 0 ? EXPLORER_WHEEL_ZOOM_STEP : 1 / EXPLORER_WHEEL_ZOOM_STEP);
        }, { passive: false });
        container.addEventListener('dblclick', event => {
            if (scale === 1) {
                return;
            }
            event.preventDefault();
            scale = 1;
            applyZoom();
        });

        let dragging = false;
        let startX = 0;
        let startY = 0;
        let startLeft = 0;
        let startTop = 0;
        container.addEventListener('pointerdown', event => {
            if (scale === 1 || event.button !== 0) {
                return;
            }
            dragging = true;
            startX = event.clientX;
            startY = event.clientY;
            startLeft = container.scrollLeft;
            startTop = container.scrollTop;
            container.classList.add('explorer-grabbing');
            container.setPointerCapture?.(event.pointerId);
            event.preventDefault();
        });
        container.addEventListener('pointermove', event => {
            if (!dragging) {
                return;
            }
            container.scrollLeft = startLeft - (event.clientX - startX);
            container.scrollTop = startTop - (event.clientY - startY);
        });
        const endDrag = event => {
            if (!dragging) {
                return;
            }
            dragging = false;
            container.classList.remove('explorer-grabbing');
            container.releasePointerCapture?.(event.pointerId);
        };
        container.addEventListener('pointerup', endDrag);
        container.addEventListener('pointercancel', endDrag);
    }

    /* Paint whatever preview HTML the pane already holds. Split out of
       restoreExplorerPreview() so the fetch path and the restore path share
       one insertion, one highlight pass and one Mermaid pass. */
    /* Hashed once per rendered document, not once per call: this is consulted
       on every view switch and every find keystroke, and the string it hashes
       is the whole rendered preview. */
    function explorerPreviewRenderToken(pane) {
        const path = pane._explorerFilePath || '';
        const html = pane._explorerPreviewHtml || '';
        const cached = pane._explorerPreviewToken;
        if (cached && cached.path === path && cached.html === html) {
            return cached.token;
        }
        const token = explorerHashText([path, html].join(String.fromCharCode(0)));
        pane._explorerPreviewToken = { path, html, token };
        return token;
    }

    /* A repaint of the Preview panel is not a re-visit of it.

       Every path that shows the panel used to run this: selecting the tab,
       switching Source/Preview/Diff, and every repaint of the find. Each one
       replaced the panel's whole subtree, which puts the reader back at the
       top — and since the Mermaid diagrams draw as they come into view, the
       panel it lands on is also *shorter* than the one it replaced, so the
       proportional restore that follows cannot find the way back either. On a
       long document that reads as being thrown to the top for no reason.

       So a panel already showing this exact render is left alone, and only the
       appearance (a few custom properties on the element itself) is re-applied.
       The token is the path and the rendered HTML, not the element: a panel the
       viewer rebuilt carries no token and repaints, exactly as it must. */
    function paintExplorerPreview(index) {
        const pane = terminals[index];
        const preview = document.getElementById(`explorer-preview-${index}`);
        if (!pane || !preview) {
            return null;
        }
        const token = explorerPreviewRenderToken(pane);
        const stale = preview.dataset.explorerPreviewRender !== token;
        if (stale) {
            preview._explorerMermaidObserver?.disconnect();
            preview._explorerMermaidObserver = null;
            preview.innerHTML = pane._explorerPreviewHtml || '';
            preview.dataset.explorerPreviewRender = token;
            if (!pane._explorerFilePlain) {
                highlightExplorerPreviewCode(preview);
            }
            wireExplorerMarkdownLinks(index, preview);
        }
        /* Outside the guard, and still the one call site: appearance is a few
           custom properties on this element, so it costs nothing to re-apply
           and every path into the panel keeps getting it without restating it.
           The diagrams stay inside, after it, because they are drawn against
           the appearance that is on the element. */
        applyExplorerMarkdownAppearanceToElement(preview, explorerMarkdownAppearance());
        if (stale) {
            renderExplorerMermaid(preview);
        }
        return preview;
    }

    /* The find's <mark> wrappers, taken out without touching anything else.

       A repaint used to drop them with the rest of the subtree; a reused panel
       has to have them removed explicitly, or the next query would paint its
       marks alongside the previous query's. Parents are normalized once each
       rather than once per mark, because a paragraph with fifty hits in it
       would otherwise re-walk its own children fifty times. */
    function explorerClearSearchMarks(root) {
        const marks = root?.querySelectorAll?.('mark.explorer-search-match');
        if (!marks || !marks.length) {
            return;
        }
        const parents = new Set();
        marks.forEach(mark => {
            const parent = mark.parentNode;
            if (!parent) {
                return;
            }
            parents.add(parent);
            parent.replaceChild(document.createTextNode(mark.textContent || ''), mark);
        });
        parents.forEach(parent => parent.normalize());
    }

    /* Fetch the rendered Markdown the first time the Preview panel is shown,
       and paint it. The file GET no longer carries `preview_html`: rendering
       and Bleach-sanitizing it on every open and every save, for a panel the
       reader may never select, was one of the two costs of opening a large
       Markdown file. `preview_type` is an independent field now, so the panel
       still *exists* from the moment the file loads — only its content is
       deferred.

       Reuses the pane's cached HTML on every later visit, so the pause lands
       once. Failures paint the message in the panel rather than anywhere
       global: this is one panel's content, not an app-level event. */
    async function ensureExplorerPreviewLoaded(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const preview = document.getElementById(`explorer-preview-${index}`);
        if (!pane || !preview || !sessionId) {
            return null;
        }
        if (pane._explorerPreviewLoaded) {
            return paintExplorerPreview(index);
        }
        const path = pane._explorerFilePath || '';
        const content = pane._explorerFileContent;
        if (!path) {
            return preview;
        }
        /* The same in-flight join loadExplorerDiff() carries, for the same
           reason. `_explorerPreviewLoaded` is set only once the response has
           landed, so it cannot answer for a load still in the air — and every
           first entry into the Preview panel asks twice inside one frame: the
           caller starts the fetch, then applyExplorerSearch() runs
           synchronously into restoreExplorerPreview(), which sees an unloaded
           panel and asks again. The second ask aborted the first and refetched
           the identical URL, so every first visit cost two requests and two
           server-side Markdown renders — Flask does not cancel on client
           abort, so the abandoned one still ran to completion. An identical
           in-flight load is joined; a load for different bytes still
           supersedes, which is what the abort slot is for. The identity is the
           path *and* the buffer, matching the staleness check inside: a save
           lands as the same path with different content, and joining that load
           would hand the reader a render of the bytes they just replaced. */
        const inFlight = pane._explorerPreviewLoadInFlight;
        if (inFlight && inFlight.path === path && inFlight.content === content) {
            await inFlight.promise;
            return document.getElementById(`explorer-preview-${index}`) === preview
                ? preview
                : null;
        }
        preview.textContent = 'Rendering preview...';
        const load = (async () => {
            try {
                const response = await fetch(
                    `/api/explorer/${encodeURIComponent(sessionId)}/file/preview?path=${encodeURIComponent(path)}`,
                    { signal: explorerRequestSignal(pane, 'preview') }
                );
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || 'Failed to render preview');
                }
                // The viewer may have moved on during the flight; the response
                // describes whatever was open when it started.
                if (terminals[index] !== pane
                    || sessionIds[index] !== sessionId
                    || pane._explorerFilePath !== path
                    || pane._explorerFileContent !== content
                    || document.getElementById(`explorer-preview-${index}`) !== preview) {
                    return null;
                }
                /* …and the *file* may have moved on, which the checks above
                   cannot see: they compare the viewer against itself. Source
                   and Preview are two reads now, so a write landing between
                   them would put a render of the newer bytes beside Source's
                   older ones. Left unloaded rather than painted or refetched:
                   the open-file change listener is already going to notice the
                   same revision move and reload the file, and this panel
                   paints from that. A response with no token (an older server)
                   is accepted as before. */
                const previewRevision = data.state_revision || '';
                const baseRevision = pane._explorerFileStateRevision || '';
                if (previewRevision && baseRevision && previewRevision !== baseRevision) {
                    return null;
                }
                pane._explorerPreviewHtml = data.preview_html || '';
                pane._explorerPreviewLoaded = true;
            } catch (error) {
                if (explorerIsAbortError(error)) {
                    return null;
                }
                console.error('[GridVibe Sessions] Explorer preview render failed:', error);
                preview.textContent = error.message || 'Failed to render preview.';
                return preview;
            }
            const painted = paintExplorerPreview(index);
            requestExplorerPanelScrollRestore(index, 'preview');
            /* The find that ran while this was in the air had nothing but the
               loader's placeholder to mark, so it counted 0 and painted
               nothing. paintExplorerPreview() has just replaced that subtree,
               so the query is applied to the document that actually arrived —
               the arrival hook loadExplorerDiff() already carries. Only while
               the reader is still on Preview: a find pointed at Source or Diff
               owns those panels, and re-running it from here would repaint
               them on behalf of a panel nobody is looking at. */
            if (painted && pane._explorerSearch?.query
                && activeExplorerFileView(index) === 'preview') {
                applyExplorerSearch(index, { scroll: false });
            }
            return painted;
        })();
        pane._explorerPreviewLoadInFlight = { path, content, promise: load };
        try {
            return await load;
        } finally {
            if (pane._explorerPreviewLoadInFlight?.promise === load) {
                pane._explorerPreviewLoadInFlight = null;
            }
        }
    }

    function restoreExplorerPreview(index) {
        const preview = document.getElementById(`explorer-preview-${index}`);
        const pane = terminals[index];
        /* Repainting before the lazy render has answered would wipe the
           loader's placeholder and leave a blank panel until the fetch lands.
           Hand the panel to the loader instead — it paints when it has
           something to paint. */
        if (pane && preview && !pane._explorerPreviewLoaded) {
            ensureExplorerPreviewLoaded(index);
            return preview;
        }
        const painted = paintExplorerPreview(index) || preview;
        /* "Restore" means the panel as the file renders it, so the find's
           marks come off here. A repaint drops them with the subtree; a reused
           panel keeps them until they are taken out. */
        explorerClearSearchMarks(painted);
        return painted;
    }

    function markExplorerSearchInElement(root, query, activeIndex = 0, maxMatches = EXPLORER_SEARCH_MAX_MATCHES) {
        if (!root || !query) {
            return [];
        }

        const textNodes = [];
        const walker = document.createTreeWalker(
            root,
            NodeFilter.SHOW_TEXT,
            {
                acceptNode(node) {
                    if (!node.nodeValue) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    if (node.parentElement?.closest('mark.explorer-search-match')) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    return NodeFilter.FILTER_ACCEPT;
                }
            }
        );
        while (walker.nextNode()) {
            textNodes.push(walker.currentNode);
        }

        const marks = [];
        const normalizedQuery = query.toLowerCase();
        let capped = false;
        textNodes.forEach(node => {
            if (marks.length >= maxMatches) {
                capped = true;
                return;
            }
            const value = node.nodeValue || '';
            const normalizedValue = value.toLowerCase();
            const localMatches = [];
            let cursor = 0;
            while (cursor < normalizedValue.length && marks.length + localMatches.length < maxMatches) {
                const matchIndex = normalizedValue.indexOf(normalizedQuery, cursor);
                if (matchIndex === -1) {
                    break;
                }
                localMatches.push({
                    start: matchIndex,
                    end: matchIndex + query.length
                });
                cursor = matchIndex + Math.max(query.length, 1);
            }
            capped = capped || marks.length + localMatches.length >= maxMatches;
            if (!localMatches.length) {
                return;
            }

            const fragment = document.createDocumentFragment();
            let localCursor = 0;
            localMatches.forEach(match => {
                if (match.start > localCursor) {
                    fragment.appendChild(document.createTextNode(value.slice(localCursor, match.start)));
                }
                const mark = document.createElement('mark');
                mark.className = marks.length === activeIndex
                    ? 'explorer-search-match active'
                    : 'explorer-search-match';
                mark.textContent = value.slice(match.start, match.end);
                fragment.appendChild(mark);
                marks.push(mark);
                localCursor = match.end;
            });
            if (localCursor < value.length) {
                fragment.appendChild(document.createTextNode(value.slice(localCursor)));
            }
            node.replaceWith(fragment);
        });

        marks.capped = capped;
        return marks;
    }

    function renderExplorerDirectorySearchControls(index) {
        const container = document.getElementById(`explorer-directory-search-${index}`);
        if (!container) {
            return;
        }

        container.classList.add('active');
        container.innerHTML = `
            <input
                type="search"
                class="explorer-search-input"
                data-explorer-search-input="${index}"
                placeholder="Find file"
                autocomplete="off"
                spellcheck="false"
                aria-label="Find files and folders"
            >
            <span class="explorer-search-count" data-explorer-search-count="${index}"></span>
            <button type="button" class="explorer-search-btn" data-explorer-search-prev="${index}" title="Previous match" aria-label="Previous match">↑</button>
            <button type="button" class="explorer-search-btn" data-explorer-search-next="${index}" title="Next match" aria-label="Next match">↓</button>
            <button type="button" class="explorer-search-btn" data-explorer-search-clear="${index}" title="Clear search" aria-label="Clear search">×</button>
        `;
    }

    function clearExplorerDirectorySearchControls(index) {
        const container = document.getElementById(`explorer-directory-search-${index}`);
        if (!container) {
            return;
        }
        container.classList.remove('active');
        container.innerHTML = '';
    }

    function resetExplorerDirectorySearch(pane) {
        const state = ensureExplorerSearchState(pane, 'directory');
        state.query = '';
        state.activeIndex = 0;
        state.matchCount = 0;
        state.matchCapped = false;
    }

    function explorerDirectoryRowHtml(entry, query = '', active = false) {
        const isDirectory = entry.type === 'directory';
        const isDeleted = Boolean(entry.deleted);
        const size = isDirectory ? 'Folder' : (isDeleted ? 'Deleted' : formatExplorerSize(entry.size));
        const modified = formatExplorerDate(entry.modified);
        const name = entry.name || '';
        const normalizedName = name.toLowerCase();
        const normalizedQuery = String(query || '').toLowerCase();
        const matchIndex = normalizedQuery ? normalizedName.indexOf(normalizedQuery) : -1;
        const nameRanges = matchIndex === -1 ? [] : [{
            start: matchIndex,
            end: matchIndex + String(query).length,
            active
        }];
        const contextAttributes = isDeleted ? '' : `
                data-explorer-copy-path="${escHtml(entry.path || '')}"
                data-explorer-context-path="${escHtml(entry.path || '')}"
                data-explorer-context-kind="${escHtml(entry.entry_kind || '')}"
                data-explorer-context-revision="${escHtml(entry.revision || '')}"
                data-explorer-context-surface="preview"
                ${isDirectory ? '' : `data-explorer-download-path="${escHtml(entry.path || '')}"`}`;

        return `
            <button
                type="button"
                class="explorer-row ${isDirectory ? 'directory' : 'file'}${isDeleted ? ' deleted' : ''}"
                data-explorer-path="${escHtml(entry.path || '')}"
                ${contextAttributes}
                ${isDeleted ? 'disabled' : ''}
            >
                ${isDirectory ? EXPLORER_FOLDER_ICON : explorerFileTypeIconHtml(name || entry.path)}
                ${explorerGitBadgeHtml(entry.git)}
                <span class="explorer-name">${explorerMarkedEscHtml(name, 0, nameRanges)}</span>
                <span class="explorer-meta">${escHtml(size)}</span>
                <span class="explorer-meta">${escHtml(modified)}</span>
            </button>
        `;
    }

    function wireExplorerDirectoryRows(index) {
        const viewer = document.getElementById(`explorer-viewer-${index}`);
        if (!viewer) {
            return;
        }

        /* Shift+click would otherwise extend the browser's text selection
           across the rows it spans, leaving the listing highlighted blue under
           our own selection styling. */
        viewer.querySelectorAll('.explorer-row').forEach(button => {
            button.addEventListener('mousedown', event => {
                if (event.shiftKey) {
                    event.preventDefault();
                }
            });
        });
        viewer.querySelectorAll('.explorer-row.directory').forEach(button => {
            button.addEventListener('click', event => {
                if (handleExplorerRowSelectionClick(event, index, 'preview', button)) {
                    return;
                }
                loadExplorerPane(index, button.dataset.explorerPath || '');
            });
        });
        viewer.querySelectorAll('.explorer-row.file').forEach(button => {
            button.addEventListener('click', event => {
                if (handleExplorerRowSelectionClick(event, index, 'preview', button)) {
                    return;
                }
                openExplorerFile(index, button.dataset.explorerPath || '');
            });
        });
    }

    function renderExplorerDirectoryRows(index) {
        const pane = terminals[index];
        const viewer = explorerEnsureViewerShell(index);
        if (!pane || !viewer) {
            return;
        }

        const entries = Array.isArray(pane._explorerEntries) ? pane._explorerEntries : [];
        const state = ensureExplorerSearchState(pane, 'directory');
        const query = state.query || '';
        const normalizedQuery = String(query).toLowerCase();
        let visibleEntries = entries;
        if (normalizedQuery) {
            visibleEntries = entries.filter(entry => String(entry.name || '').toLowerCase().includes(normalizedQuery));
        }

        const matchCount = normalizedQuery ? visibleEntries.length : 0;
        state.matchCount = matchCount;
        state.activeIndex = matchCount ? Math.min(Number(state.activeIndex || 0), matchCount - 1) : 0;

        if (!entries.length && !query) {
            viewer.innerHTML = '<div class="explorer-message">Directory is empty.</div>';
        } else if (!visibleEntries.length) {
            viewer.innerHTML = `<div class="explorer-message">No files or folders match "${escHtml(query)}".</div>`;
        } else {
            viewer.innerHTML = visibleEntries
                .map((entry, entryIndex) => explorerDirectoryRowHtml(entry, query, Boolean(normalizedQuery) && entryIndex === state.activeIndex))
                .join('');
            wireExplorerDirectoryRows(index);
        }

        updateExplorerSearchControls(index, query, state.activeIndex || 0, matchCount);
        if (normalizedQuery && matchCount) {
            scrollExplorerSearchMatch(index);
        }
        if (typeof refreshExplorerFilesystemCutSource === 'function') {
            refreshExplorerFilesystemCutSource(index);
        }
        refreshExplorerSelectionHighlight(index);
    }

    function updateExplorerSearchControls(index, query, activeIndex, matchCount, capped = false) {
        const input = document.querySelector(`[data-explorer-search-input="${index}"]`);
        const count = document.querySelector(`[data-explorer-search-count="${index}"]`);
        const buttons = document.querySelectorAll(
            `[data-explorer-search-prev="${index}"], [data-explorer-search-next="${index}"]`
        );
        if (input && input.value !== query) {
            input.value = query;
        }
        if (count) {
            count.textContent = query ? `${matchCount ? activeIndex + 1 : 0}/${matchCount}${capped ? '+' : ''}` : '';
            count.title = capped ? `Showing first ${matchCount} matches` : '';
        }
        buttons.forEach(button => {
            button.disabled = matchCount === 0;
        });
    }

    function scrollExplorerSearchMatch(index) {
        const active = document
            .getElementById(`explorer-list-${index}`)
            ?.querySelector('.explorer-search-match.active');
        if (!active) {
            return;
        }
        requestAnimationFrame(() => {
            active.scrollIntoView({ block: 'center', inline: 'nearest' });
        });
    }

    function cancelExplorerSearch(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        if (pane._explorerSearchTimer) {
            window.clearTimeout(pane._explorerSearchTimer);
            pane._explorerSearchTimer = null;
        }
        if (pane._explorerSearchToken) {
            pane._explorerSearchToken.cancelled = true;
            pane._explorerSearchToken = null;
        }
    }

    function scheduleExplorerSearch(index, { resetActive = false, delay = EXPLORER_SEARCH_DEBOUNCE_MS, scroll = true } = {}) {
        const pane = terminals[index];
        if (!pane || !isExplorerSearchablePane(pane)) {
            return;
        }
        if (pane._explorerMode === 'directory') {
            applyExplorerSearch(index, { resetActive, scroll });
            return;
        }

        cancelExplorerSearch(index);
        pane._explorerSearchTimer = window.setTimeout(() => {
            pane._explorerSearchTimer = null;
            applyExplorerSearch(index, { resetActive, scroll });
        }, delay);
    }

    /* `scroll` is what separates a find the reader is *navigating* from one
       that is merely being repainted. Typing a query, stepping with
       Enter/prev/next and seeding from Ctrl+F all move the view to the active
       match — that is the point of them. Everything else here is a repaint: the
       surface was rebuilt (entering or leaving the in-place editor, a keystroke
       moving the draft under the overlay, a group re-attach, the post-save
       in-place refresh) and the find is only being re-derived onto it. Those
       callers pass `scroll: false`, because a repaint that yanks the view to
       match 5 of 8 is exactly the "thrown across the file" jolt swapping modes
       used to produce — and every one of them either preserves the reader's
       scroll position or restores it explicitly right afterwards. */
    async function applyExplorerSearch(index, { resetActive = false, scroll = true } = {}) {
        const pane = terminals[index];
        if (!pane || !isExplorerSearchablePane(pane)) {
            return;
        }

        /* The tier that removed the rows removed the find with them, and the
           header renders no search bar — so there is no query to apply and
           nothing to repaint. Returning here rather than falling through keeps
           a restored search state (a tab reopened at a now-larger file) from
           driving a repaint against rows that do not exist. */
        if (pane._explorerMode === 'file' && !explorerPaneAllowsFind(pane)) {
            return;
        }

        const state = ensureExplorerSearchState(pane);
        if (resetActive) {
            state.activeIndex = 0;
        }

        if (pane._explorerMode === 'directory') {
            renderExplorerDirectoryRows(index);
            return;
        }

        /* An open in-place editor owns the Source panel: the rows under the
           caret are the highlight overlay's underlay, painted from the live
           draft. A find there has to search that draft rather than the file on
           disk, and paint without rewriting the rows — rewriting them is what
           would move the caret's geometry out from under it.
           explorer-edit-find.js owns both, using this same search state, so
           the input, the counter and Enter/Shift+Enter are unchanged. Every
           other view behaves exactly as it does with no editor open. */
        if (pane._explorerEdit && typeof window.applyExplorerEditFind === 'function') {
            return window.applyExplorerEditFind(index, { resetActive, scroll });
        }

        const query = state.query || '';
        const view = activeExplorerFileView(index);
        let matchCount = 0;
        let capped = false;
        if (query && view === 'source') {
            const cachedRanges = state.resultQuery === query
                && Array.isArray(state.ranges)
                && explorerSearchRangesMatchContent(state, pane)
                ? state.ranges
                : null;
            const ranges = cachedRanges || [];
            if (!cachedRanges) {
                cancelExplorerSearch(index);
                const token = { cancelled: false };
                pane._explorerSearchToken = token;
                updateExplorerSearchControls(index, query, 0, 0);
                // The buffer that is actually scanned, held across the await:
                // the offsets below address this string and no other.
                const scanned = pane._explorerFileContent || '';
                const result = await explorerFindRangesAsync(scanned, query, token);
                if (token.cancelled || pane._explorerSearchToken !== token) {
                    return;
                }
                pane._explorerSearchToken = null;
                ranges.splice(0, ranges.length, ...result.ranges);
                ranges.capped = result.capped;
                state.ranges = ranges;
                state.resultQuery = query;
                // Stamped with the exact buffer these offsets address.
                state.resultContent = scanned;
                explorerRevealMarkdownSearchMatches(index, ranges);
            }
            matchCount = ranges.length;
            capped = Boolean(ranges.capped);
            state.activeIndex = explorerResolveSearchActiveIndex(state, ranges);
            renderExplorerSource(index, decorateExplorerSearchRanges(ranges, state.activeIndex));
        } else if (query && view === 'preview') {
            cancelExplorerSearch(index);
            renderExplorerSource(index);
            if (pane._explorerDiffLoaded) {
                renderExplorerDiff(index);
            }
            const preview = restoreExplorerPreview(index);
            if (!preview) {
                state.activeIndex = 0;
                state.matchCount = 0;
                state.matchCapped = false;
                updateExplorerSearchControls(index, query, 0, 0);
                return;
            }
            const previewMarks = markExplorerSearchInElement(preview, query, state.activeIndex || 0);
            matchCount = previewMarks.length;
            capped = Boolean(previewMarks.capped);
            state.activeIndex = matchCount ? Math.min(state.activeIndex || 0, matchCount - 1) : 0;
            if (matchCount && !previewMarks[state.activeIndex]?.classList.contains('active')) {
                previewMarks.forEach((mark, markIndex) => {
                    mark.classList.toggle('active', markIndex === state.activeIndex);
                });
            }
        } else if (query && view === 'diff') {
            cancelExplorerSearch(index);
            renderExplorerSource(index);
            restoreExplorerPreview(index);
            if (pane._explorerDiffLoaded) {
                await renderExplorerDiff(index);
            }
            const diff = document.getElementById(`explorer-diff-code-${index}`);
            if (!diff) {
                state.activeIndex = 0;
                state.matchCount = 0;
                state.matchCapped = false;
                updateExplorerSearchControls(index, query, 0, 0);
                return;
            }
            const diffMarks = markExplorerSearchInElement(diff, query, state.activeIndex || 0);
            scheduleExplorerDiffScrollbarSync(diff.querySelector('.explorer-diff2html'));
            matchCount = diffMarks.length;
            capped = Boolean(diffMarks.capped);
            state.activeIndex = matchCount ? Math.min(state.activeIndex || 0, matchCount - 1) : 0;
            if (matchCount && !diffMarks[state.activeIndex]?.classList.contains('active')) {
                diffMarks.forEach((mark, markIndex) => {
                    mark.classList.toggle('active', markIndex === state.activeIndex);
                });
            }
        } else {
            cancelExplorerSearch(index);
            state.ranges = [];
            state.resultQuery = '';
            renderExplorerSource(index);
            restoreExplorerPreview(index);
            if (pane._explorerDiffLoaded) {
                renderExplorerDiff(index);
            }
            state.activeIndex = 0;
        }

        state.matchCount = matchCount;
        state.matchCapped = capped;
        updateExplorerSearchControls(index, query, state.activeIndex || 0, matchCount, capped);
        if (query && matchCount && scroll) {
            // The active match may still be a row a frame-sliced build has not
            // reached; scroll to it once the rows it is in exist.
            whenExplorerSourceRendered(index, () => scrollExplorerSearchMatch(index));
        }
    }

    /* Which match a freshly computed result set opens on. A seeded find (Ctrl+F
       over a selection, a repo-search hit) opens on the first match at or after
       the spot it was seeded from — without this every seeded find snapped the
       Source view back to the file's first match. Everything else keeps its
       current position, clamped to the new result count. */
    function explorerResolveSearchActiveIndex(state, ranges) {
        const seekOffset = state.seekOffset;
        state.seekOffset = null;
        if (!ranges.length) {
            return 0;
        }
        if (Number.isFinite(seekOffset)) {
            const seekIndex = ranges.findIndex(range => range.start >= seekOffset);
            return seekIndex === -1 ? 0 : seekIndex;
        }
        return Math.min(state.activeIndex || 0, ranges.length - 1);
    }

    function explorerLineStartOffset(pane, line) {
        const record = explorerSourceLineRecords(pane?._explorerFileContent || '')[Number(line) - 1];
        return record ? record.start : null;
    }

    /* Content offset of the line the current selection starts on, so a find
       seeded from that selection can open on the match under it. */
    function explorerSelectionContentOffset(pane) {
        const selection = window.getSelection?.();
        if (!pane || !selection || !selection.rangeCount) {
            return null;
        }
        const row = explorerElementForNode(selection.getRangeAt(0).startContainer)
            ?.closest('[data-explorer-line]');
        return row ? explorerLineStartOffset(pane, Number(row.dataset.explorerLine || 0)) : null;
    }

    function stepExplorerSearch(index, delta) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const state = ensureExplorerSearchState(pane);
        const matchCount = Number(state.matchCount || 0);
        if (!matchCount) {
            return;
        }
        state.activeIndex = (Number(state.activeIndex || 0) + delta + matchCount) % matchCount;
        applyExplorerSearch(index);
    }

    function clearExplorerSearch(index, { focus = true } = {}) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const state = ensureExplorerSearchState(pane);
        state.query = '';
        state.activeIndex = 0;
        state.matchCount = 0;
        state.matchCapped = false;
        state.ranges = [];
        state.resultQuery = '';
        state.seekOffset = null;
        applyExplorerSearch(index);
        if (focus) {
            document.querySelector(`[data-explorer-search-input="${index}"]`)?.focus();
        }
    }

    function focusExplorerSearch(index, seedQuery = '') {
        const pane = terminals[index];
        if (!pane || !isExplorerSearchablePane(pane)) {
            return false;
        }
        const input = document.querySelector(`[data-explorer-search-input="${index}"]`);
        if (!input) {
            return false;
        }
        /* Seeding the query with the current editor selection mirrors the
           copy → find → paste sequence (notes 1): highlight text, hit Ctrl+F,
           and it immediately looks that text up instead of reopening the last
           search. */
        if (seedQuery) {
            const state = ensureExplorerSearchState(pane);
            state.query = seedQuery;
            state.activeIndex = 0;
            /* Open on the match the reader is already looking at instead of
               snapping the Source view back to the file's first match. With an
               editor open the spot comes from the textarea's own selection —
               the document selection this otherwise reads is empty inside one. */
            const editSeed = window.explorerEditSelectionSeed?.(index) || null;
            state.seekOffset = activeExplorerFileView(index) !== 'source'
                ? null
                : (editSeed ? editSeed.offset : explorerSelectionContentOffset(pane));
            state.ranges = [];
            state.resultQuery = '';
            state.matchCapped = false;
            input.value = seedQuery;
            scheduleExplorerSearch(index, { resetActive: true, delay: 0 });
        }
        input.focus();
        input.select();
        return true;
    }

    function wireExplorerSearchControls(index) {
        const pane = terminals[index];
        const input = document.querySelector(`[data-explorer-search-input="${index}"]`);
        if (!pane || !input || input.dataset.bound) {
            return;
        }

        input.dataset.bound = 'true';
        const state = ensureExplorerSearchState(pane);
        input.value = state.query || '';
        input.addEventListener('input', () => {
            const nextState = ensureExplorerSearchState(pane);
            nextState.query = input.value;
            nextState.activeIndex = 0;
            nextState.ranges = [];
            nextState.resultQuery = '';
            nextState.matchCapped = false;
            // Typing is its own starting point; only a seeded find seeks.
            nextState.seekOffset = null;
            scheduleExplorerSearch(index, { resetActive: true });
        });
        input.addEventListener('keydown', event => {
            if (event.key === 'Enter') {
                event.preventDefault();
                stepExplorerSearch(index, event.shiftKey ? -1 : 1);
            } else if (event.key === 'Escape') {
                event.preventDefault();
                clearExplorerSearch(index);
            }
        });

        document.querySelector(`[data-explorer-search-prev="${index}"]`)?.addEventListener('click', () => {
            stepExplorerSearch(index, -1);
        });
        document.querySelector(`[data-explorer-search-next="${index}"]`)?.addEventListener('click', () => {
            stepExplorerSearch(index, 1);
        });
        document.querySelector(`[data-explorer-search-clear="${index}"]`)?.addEventListener('click', () => {
            clearExplorerSearch(index);
        });
    }

    /* ── Selection occurrence highlight (Source view) ──
       Double-clicking a word — or selecting any single-line snippet — tints
       every other occurrence of it in the same file, the way an editor does.
       It is painted through the CSS Custom Highlight API instead of <mark>
       wrappers because the Source DOM must not be rewritten: rewriting it is
       what makes the find widget re-run the Markdown fold pass, lose the
       scroll position and drop the live selection. Browsers without the API
       just get no tint; the find widget stays the explicit fallback. */
    const EXPLORER_OCCURRENCE_HIGHLIGHT = 'explorer-occurrence';
    const EXPLORER_OCCURRENCE_MAX_MATCHES = 500;
    const EXPLORER_OCCURRENCE_MAX_QUERY = 200;
    const EXPLORER_OCCURRENCE_DEBOUNCE_MS = 90;
    const EXPLORER_OCCURRENCE_WORD_RE = /^[\w$]+$/;
    let _explorerOccurrenceTimer = null;

    function explorerElementForNode(node) {
        if (!node) {
            return null;
        }
        return node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    }

    /* Registry entry for one named Custom Highlight, created on first use.
       Shared with the repository-search hit paint in explorer-search.js —
       both need the same "does this browser have the API" guard. */
    function explorerNamedHighlight(name) {
        if (typeof window.Highlight !== 'function' || !window.CSS?.highlights) {
            return null;
        }
        let highlight = window.CSS.highlights.get(name);
        if (!highlight) {
            highlight = new window.Highlight();
            window.CSS.highlights.set(name, highlight);
        }
        return highlight;
    }

    function explorerOccurrenceHighlight() {
        return explorerNamedHighlight(EXPLORER_OCCURRENCE_HIGHLIGHT);
    }

    /* The Source view the selection sits in, or null when there is nothing to
       highlight: a collapsed selection, one spanning lines or panes, or one
       outside a Source view (Preview, Diff and the edit textarea are not it). */
    function explorerOccurrenceTarget() {
        const selection = window.getSelection?.();
        if (!selection || selection.isCollapsed || !selection.rangeCount) {
            return null;
        }
        const query = (selection.toString() || '').trim();
        if (!query || query.length > EXPLORER_OCCURRENCE_MAX_QUERY || /[\r\n]/.test(query)) {
            return null;
        }
        const range = selection.getRangeAt(0);
        const root = explorerElementForNode(range.startContainer)?.closest('.explorer-source-lines');
        if (!root || !root.contains(range.endContainer)) {
            return null;
        }
        return { root, range, query };
    }

    /* A selection that is a bare identifier matches whole words only, so
       double-clicking `id` does not light up every `width` in the file. */
    function explorerOccurrenceIsWholeWord(value, start, end) {
        const before = start > 0 ? value[start - 1] : '';
        const after = end < value.length ? value[end] : '';
        return !EXPLORER_OCCURRENCE_WORD_RE.test(before) && !EXPLORER_OCCURRENCE_WORD_RE.test(after);
    }

    function explorerOccurrenceRanges(root, query, selectionRange) {
        const needle = query.toLowerCase();
        const wholeWord = EXPLORER_OCCURRENCE_WORD_RE.test(query);
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        const ranges = [];

        while (ranges.length < EXPLORER_OCCURRENCE_MAX_MATCHES && walker.nextNode()) {
            const node = walker.currentNode;
            const value = node.nodeValue || '';
            // The selected text already carries the browser's own selection
            // paint, and skipping its nodes keeps the tint off a partially
            // selected token.
            if (!value || selectionRange.intersectsNode(node)) {
                continue;
            }
            const haystack = value.toLowerCase();
            let cursor = 0;
            while (ranges.length < EXPLORER_OCCURRENCE_MAX_MATCHES) {
                const start = haystack.indexOf(needle, cursor);
                if (start === -1) {
                    break;
                }
                const end = start + query.length;
                cursor = end;
                if (wholeWord && !explorerOccurrenceIsWholeWord(value, start, end)) {
                    continue;
                }
                const range = document.createRange();
                range.setStart(node, start);
                range.setEnd(node, end);
                ranges.push(range);
            }
        }

        return ranges;
    }

    function refreshExplorerOccurrenceHighlight() {
        const highlight = explorerOccurrenceHighlight();
        if (!highlight) {
            return;
        }
        highlight.clear();
        const target = explorerOccurrenceTarget();
        if (!target) {
            return;
        }
        explorerOccurrenceRanges(target.root, target.query, target.range)
            .forEach(range => highlight.add(range));
    }

    function scheduleExplorerOccurrenceHighlight() {
        if (typeof window.Highlight !== 'function' || !window.CSS?.highlights) {
            return;
        }
        window.clearTimeout(_explorerOccurrenceTimer);
        // Debounced: a drag-select fires selectionchange on every mouse move.
        _explorerOccurrenceTimer = window.setTimeout(() => {
            _explorerOccurrenceTimer = null;
            refreshExplorerOccurrenceHighlight();
        }, EXPLORER_OCCURRENCE_DEBOUNCE_MS);
    }

    function installExplorerOccurrenceHighlight() {
        document.addEventListener('selectionchange', scheduleExplorerOccurrenceHighlight);
    }

    installExplorerOccurrenceHighlight();

    function explorerPanelScrollTarget(panel) {
        if (!panel) {
            return null;
        }
        /* The source frame (.explorer-source-frame) is overflow:hidden — a
           fixed frame has to be, so anything docked beside the text never
           scrolls away — and the element that actually scrolls is the inner
           .explorer-source-view, exactly as the diff panel's inner
           .explorer-diff-content scrolls inside its own fixed wrapper. */
        if (panel.dataset.explorerFilePanel === 'source') {
            const view = panel.querySelector('.explorer-source-view');
            if (!view) {
                return panel;
            }
            /* The in-place editor's highlight overlay keeps that same view as
               the scroller: its textarea is `overflow: hidden` and exactly as
               tall as its own content, so both layers scroll together. Only
               the bare fallback textarea — the overlay stood down — is a
               scroller of its own, and its inner viewport is what Save needs
               to restore onto the rebuilt read-only panel. */
            if (view.querySelector('.explorer-edit-stack')) {
                return view;
            }
            const editor = view.querySelector('.explorer-source-editor');
            return editor || view;
        }
        // The diff panel wrapper (.explorer-diff-split) is overflow:hidden; the
        // element that actually scrolls is the inner .explorer-diff-content.
        if (panel.dataset.explorerFilePanel === 'diff') {
            return panel.querySelector('.explorer-diff-content') || panel;
        }
        return panel;
    }

    function captureScrollMetrics(el) {
        if (!el) {
            return null;
        }
        const maxScrollTop = Math.max(0, el.scrollHeight - el.clientHeight);
        const maxScrollLeft = Math.max(0, el.scrollWidth - el.clientWidth);
        return {
            scrollLeft: el.scrollLeft,
            scrollLeftRatio: maxScrollLeft > 0 ? el.scrollLeft / maxScrollLeft : 0,
            scrollTop: el.scrollTop,
            scrollTopRatio: maxScrollTop > 0 ? el.scrollTop / maxScrollTop : 0,
            wasAtBottom: maxScrollTop > 0 && el.scrollTop >= maxScrollTop - 2
        };
    }

    /* Restore an offset the reader actually had, and fall back to the fraction
       only when that is all there is.

       A capture taken in this session carries both — the exact offset and its
       ratio — and inside a session the offset is the truthful one: a tab
       switch, a re-render, a rebuild of the same document all return to the
       same content, and a fraction of a scroll extent that moved by a few
       pixels puts the reader somewhere they never were. Horizontally that is
       not even approximately right: a code view's horizontal position is a
       column, and the extent it would be a fraction of is the length of the
       single longest line, which one folded section or one row a frame-sliced
       build has not emitted yet is enough to change. Scaling by it threw the
       view sideways on every restore, which is what made scrolling a large
       file feel like it was fighting back.

       The ratios are what survive a restart: a persisted record stores only
       `{x, y}` (explorer-persistence.js), so a restored workspace has no
       offset to return to and a proportional position is the best available
       answer. `wasAtBottom` still wins outright — "the end of the file" is an
       intent, not a coordinate. */
    function applyScrollMetrics(el, metrics, dimensions) {
        if (!el || !metrics) {
            return;
        }
        /* A caller restoring several scrollers at once reads every one of
           their extents first and hands them back here, so the write below
           cannot invalidate the layout the next target's read needs. Read
           straight off the element when there is only one of it. */
        const box = dimensions || el;
        const maxScrollTop = Math.max(0, box.scrollHeight - box.clientHeight);
        const maxScrollLeft = Math.max(0, box.scrollWidth - box.clientWidth);
        el.scrollLeft = Math.min(
            maxScrollLeft,
            Number.isFinite(metrics.scrollLeft)
                ? metrics.scrollLeft
                : Math.round(maxScrollLeft * (metrics.scrollLeftRatio || 0))
        );
        el.scrollTop = metrics.wasAtBottom
            ? maxScrollTop
            : Math.min(
                maxScrollTop,
                Number.isFinite(metrics.scrollTop)
                    ? metrics.scrollTop
                    : Math.round(maxScrollTop * (metrics.scrollTopRatio || 0))
            );
    }

    function captureExplorerFileScroll(index) {
        const list = document.getElementById(`explorer-list-${index}`);
        if (!list) {
            return null;
        }

        const activeButton = list.querySelector('[data-explorer-file-view][aria-selected="true"]');
        const listMaxScrollTop = Math.max(0, list.scrollHeight - list.clientHeight);
        const listMaxScrollLeft = Math.max(0, list.scrollWidth - list.clientWidth);
        const state = {
            activeView: activeButton?.dataset.explorerFileView || 'source',
            listScrollLeft: list.scrollLeft,
            listScrollTop: list.scrollTop,
            directory: {
                scrollLeftRatio: listMaxScrollLeft > 0 ? list.scrollLeft / listMaxScrollLeft : 0,
                scrollTopRatio: listMaxScrollTop > 0 ? list.scrollTop / listMaxScrollTop : 0,
                wasAtBottom: listMaxScrollTop > 0 && list.scrollTop >= listMaxScrollTop - 2
            },
            panels: {},
            // File tree / Git sidebar panels sit outside the list and are their own
            // overflow:auto scrollers, so capture them too (they reset on reattach).
            sidebar: {
                tree: captureScrollMetrics(document.getElementById(`explorer-tree-panel-${index}`)),
                git: captureScrollMetrics(document.getElementById(`explorer-git-panel-${index}`)),
                search: captureScrollMetrics(document.getElementById(`explorer-search-panel-${index}`))
            }
        };
        list.querySelectorAll('[data-explorer-file-panel]').forEach(panel => {
            const scrollEl = explorerPanelScrollTarget(panel);
            if (!scrollEl) {
                return;
            }
            const mode = panel.dataset.explorerFilePanel || 'source';
            const pane = terminals[index];
            const store = pane?._explorerPanelScrollStore;
            const stored = explorerPanelScrollStoreMatches(pane, store)
                ? store.panels?.[mode]?.metrics
                : null;
            /* A panel that is still filling has no reader position to read.

               Preview can be showing only its loader: capturing that tiny box
               during the render's presentation snapshot would write 0 (or its
               small clamp) over the offset waiting for the lazy Markdown
               response.

               Source has exactly the same hazard and it is not hypothetical.
               A frame-sliced build empties the scroller before its first
               slice lands, so the browser clamps the offset to 0 and — a task
               later, while the remaining frames are still emitting — fires a
               `scroll` event for it. The capture-phase listener in
               explorerEnsureViewerShell() answers that event with this very
               function, which would then store the clamp as the reader's
               position and hand it to the restore queued behind the same
               build. That is the whole of "a large file jumps to the top
               after a save, a tab swap, or a watcher refresh": the position
               was captured correctly and then overwritten by the rebuild's
               own side effect a moment before it was due to be applied. A
               synchronous build never showed it, because there the restore
               has already run by the time the event is dispatched.

               Hidden panels, an unloaded Preview and a Source build in flight
               all retain the content-bound value already in the pane store.
               The accepted cost is the same one the restore already carries:
               a build owns where the file opens, so a reader who scrolls
               inside the few frames a build lasts is returned to the offset
               that build was restoring. */
            const contentPending = mode === 'preview'
                ? !pane?._explorerPreviewLoaded
                : mode === 'source' && Boolean(pane?._explorerSourceRenderJob);
            const metrics = (panel.hidden || contentPending) && stored
                ? { ...stored }
                : captureScrollMetrics(scrollEl);
            state.panels[mode] = metrics;
            if (!panel.hidden && !contentPending && metrics) {
                storeExplorerPanelMetrics(index, mode, metrics);
            }
        });
        return state;
    }

    function restoreExplorerFileScroll(index, state) {
        if (!state) {
            return;
        }

        setExplorerPanelScrollState(index, state);

        /* Directory listings have no file-view panels; switching modes there
           would clobber stale diff state for no visual effect. */
        const listEl = document.getElementById(`explorer-list-${index}`);
        let restoredMode = state.activeView || 'source';
        if (listEl && listEl.querySelector('[data-explorer-file-panel]')) {
            setExplorerFileView(index, restoredMode, { captureScroll: false });
            restoredMode = activeExplorerFileView(index);
        }

        /* The listing and the three sidebar panels are restored in one read
           pass and one write pass, never element by element: reading an
           extent after writing another element's offset forces the layout
           again, and this runs for every pane of an incoming group.

           They are also restored until they *land*, not a fixed number of
           times. The four passes this replaces — immediate, two nested
           frames, then an 80 ms timer — existed because nobody knew when the
           content would be final, so a 20,000-row document paid three
           layouts it had no use for. The panel policy already answers "can
           this target hold that offset yet?"; the same bounded answer ends
           the sequence as soon as it can. */
        const applyScroll = (attempt = 0) => {
            const list = document.getElementById(`explorer-list-${index}`);
            if (!list) {
                return;
            }
            const targets = [
                {
                    el: list,
                    metrics: state.directory || {
                        scrollLeft: state.listScrollLeft || 0,
                        scrollTop: state.listScrollTop || 0
                    }
                },
                {
                    el: document.getElementById(`explorer-tree-panel-${index}`),
                    metrics: state.sidebar?.tree
                },
                {
                    el: document.getElementById(`explorer-git-panel-${index}`),
                    metrics: state.sidebar?.git
                },
                {
                    el: document.getElementById(`explorer-search-panel-${index}`),
                    metrics: state.sidebar?.search
                }
            ].filter(target => target.el && target.metrics);
            const policy = explorerScrollPolicy();
            const reads = targets.map(target => ({
                scrollHeight: target.el.scrollHeight,
                clientHeight: target.el.clientHeight,
                scrollWidth: target.el.scrollWidth,
                clientWidth: target.el.clientWidth
            }));
            const plans = targets.map((target, at) => (policy
                ? policy.restorePlan(target.metrics, reads[at], attempt)
                : { apply: true, retry: false, nextAttempt: attempt + 1 }));
            targets.forEach((target, at) => {
                if (plans[at].apply) {
                    applyScrollMetrics(target.el, target.metrics, reads[at]);
                }
            });
            const pending = plans.find(plan => plan.retry);
            if (pending) {
                requestAnimationFrame(() => applyScroll(pending.nextAttempt));
            }
        };

        applyScroll();
        requestExplorerPanelScrollRestore(index, restoredMode);
    }

    /* ── Per-tab view mode + scroll state (2.e) ──
       Each tab record may carry a `view` snapshot: { mode, identity, scroll }.
       The snapshot is captured when leaving a tab and restored when the tab is
       shown again — but only while the content identity still matches (OD-4:
       scroll is stored as fractions of scroll height, clamped on restore, and
       skipped entirely once the content changed). */

    /* Cheap stable string hash (djb2) for content-identity comparison. */
    function explorerHashText(text) {
        const value = String(text == null ? '' : text);
        let hash = 5381;
        for (let i = 0; i < value.length; i += 1) {
            hash = ((hash << 5) + hash + value.charCodeAt(i)) | 0;
        }
        return (hash >>> 0).toString(36);
    }

    /* Source/Preview state follows file bytes only. Diff has its own rendered
       revision below, so an index-only change cannot masquerade as unchanged. */
    function explorerFileContentIdentity(path, content) {
        return `file:${explorerHashText([path || '', content || ''].join('\u0000'))}`;
    }

    function explorerDirectoryContentIdentity(path, entries) {
        return `dir:${explorerHashText(JSON.stringify([
            path || '',
            (Array.isArray(entries) ? entries : []).map(entry => [
                entry?.path || '', entry?.type || '', entry?.size || 0, entry?.modified || 0
            ])
        ]))}`;
    }

    /* One hash per document, not one per call.

       `_explorerFileContent` is a stable string reference that changes
       whenever the bytes do, so a cache keyed on the identity of the inputs is
       exact rather than approximate. The alternative is what this used to be:
       a djb2 pass over every character of the open file — preceded by a join
       that materializes a full second copy of the buffer — run again for every
       tab switch, every group switch and every presentation capture, in both
       directions. explorerPreviewRenderToken() above caches on the same terms
       for the same reason. The answer is handed back as a fresh object, so a
       tab view holding it can never alias the next caller's. */
    function explorerContentRevisionKey(pane) {
        if (pane._explorerMode === 'directory') {
            return {
                mode: 'directory',
                path: pane._explorerPath,
                entries: pane._explorerEntries,
                revision: pane._explorerDirectoryRevision
            };
        }
        return {
            mode: 'file',
            path: pane._explorerFilePath,
            content: pane._explorerFileContent,
            diffLoaded: Boolean(pane._explorerDiffLoaded),
            diffCommit: pane._explorerDiffCommit,
            diffMode: pane._explorerDiffMode,
            diffContent: pane._explorerDiffContent
        };
    }

    function explorerContentRevisionKeyMatches(previous, next) {
        return Boolean(previous)
            && previous.mode === next.mode
            && Object.keys(next).every(name => previous[name] === next[name]);
    }

    function explorerCurrentContentRevisions(pane) {
        if (!pane) return {};
        const key = explorerContentRevisionKey(pane);
        const cached = pane._explorerContentRevisions;
        if (cached && explorerContentRevisionKeyMatches(cached.key, key)) {
            return { ...cached.revisions };
        }
        const revisions = explorerComputeContentRevisions(pane);
        pane._explorerContentRevisions = { key, revisions };
        return { ...revisions };
    }

    function explorerComputeContentRevisions(pane) {
        if (pane._explorerMode === 'directory') {
            return {
                directory: String(
                    pane._explorerDirectoryRevision
                    || explorerDirectoryContentIdentity(pane._explorerPath, pane._explorerEntries)
                )
            };
        }
        const fileRevision = explorerFileContentIdentity(
            pane._explorerFilePath,
            pane._explorerFileContent
        );
        const revisions = { source: fileRevision, preview: fileRevision };
        if (pane._explorerDiffLoaded) {
            revisions.diff = window.GridVibeExplorerPersistence?.diffContentRevision({
                path: pane._explorerFilePath,
                diffCommit: pane._explorerDiffCommit,
                diffMode: pane._explorerDiffMode,
                renderedDiff: pane._explorerDiffContent
            }) || '';
        }
        return revisions;
    }

    /* Diff content loads asynchronously, after restoreExplorerFileScroll has
       already run; re-apply a stashed diff-panel scroll once it arrives. */
    function applyExplorerPendingDiffScroll(index) {
        const pane = terminals[index];
        const pending = pane ? pane._explorerPendingDiffScroll : null;
        if (!pane) {
            return;
        }
        pane._explorerPendingDiffScroll = null;
        let metrics = pending;
        if (pending?.persistedRecord) {
            const resolved = window.GridVibeExplorerPersistence?.resolveRecord(
                pending.persistedRecord,
                explorerCurrentContentRevisions(pane)
            );
            metrics = resolved?.scroll?.panels?.diff || null;
        }
        if (metrics) {
            storeExplorerPanelMetrics(index, 'diff', metrics);
        }
        requestExplorerPanelScrollRestore(index, 'diff');
    }

    const EXPLORER_FOLDER_ICON = `
        <span class="explorer-icon folder" aria-hidden="true">
            <svg viewBox="0 0 24 24" focusable="false">
                <path fill="currentColor" d="M3 6.75A2.75 2.75 0 0 1 5.75 4h4.02c.73 0 1.43.29 1.94.8l1.2 1.2h5.34A2.75 2.75 0 0 1 21 8.75v8.5A2.75 2.75 0 0 1 18.25 20H5.75A2.75 2.75 0 0 1 3 17.25V6.75Zm2.75-1.25c-.69 0-1.25.56-1.25 1.25V8h15v-.25c0-.69-.56-1.25-1.25-1.25h-5.65a.75.75 0 0 1-.53-.22l-1.42-1.42a1.25 1.25 0 0 0-.88-.36H5.75ZM4.5 9.5v7.75c0 .69.56 1.25 1.25 1.25h12.5c.69 0 1.25-.56 1.25-1.25V9.5h-15Z"/>
            </svg>
        </span>
    `;

    const EXPLORER_TREE_TOGGLE_ICON = `
        <svg class="explorer-toggle-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"
            fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="7" height="5" rx="1.5"/>
            <rect x="14" y="9.5" width="7" height="5" rx="1.5"/>
            <rect x="14" y="16.5" width="7" height="5" rx="1.5"/>
            <path d="M6.5 8v11"/>
            <path d="M6.5 12h7.5"/>
            <path d="M6.5 19h7.5"/>
        </svg>
    `;

    const EXPLORER_GIT_TOGGLE_ICON = `
        <svg class="explorer-toggle-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"
            fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="5.5" cy="5" r="2.25"/>
            <circle cx="5.5" cy="19" r="2.25"/>
            <circle cx="18.5" cy="5" r="2.25"/>
            <path d="M5.5 7.25v9.5"/>
            <path d="M18.5 7.25v1.5a4 4 0 0 1-4 4h-5a4 4 0 0 0-4 4"/>
        </svg>
    `;

    const EXPLORER_OS_OPEN_ICON = `
        <svg class="explorer-toggle-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"
            fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
            <polyline points="15 3 21 3 21 9"/>
            <line x1="10" y1="14" x2="21" y2="3"/>
        </svg>
    `;

    /* Reveals the Graph section's commit find. Sized like the stage/discard
       icons beside it in the sections above, so the three section headers'
       controls sit on one baseline. */
    const EXPLORER_GIT_SEARCH_ICON = `
        <svg class="explorer-btn-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"
            fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="6"/>
            <line x1="15.5" y1="15.5" x2="20" y2="20"/>
        </svg>
    `;

    const EXPLORER_GIT_REVERT_ICON = `
        <svg class="explorer-btn-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"
            fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M5 5.5v4.5h4.5"/>
            <path d="M5.4 13.5a7 7 0 1 0 1.7-6.4L5 10"/>
        </svg>
    `;

    /* Bodies at or under this are read into memory so the *status* is
       observable; anything larger is handed to the browser to stream, which
       costs the outcome but never the machine's memory. The server caps a
       download at 100 MB, so this only ever splits the top quarter of the
       range off. */
    const EXPLORER_DOWNLOAD_BUFFER_MAX_BYTES = 25 * 1024 * 1024;
    /* An object URL has to outlive the click that consumes it; revoking in the
       same task can race the browser's own read of it. */
    const EXPLORER_DOWNLOAD_OBJECT_URL_TTL_MS = 60000;

    function triggerExplorerDownloadAnchor(href, fileName) {
        const link = document.createElement('a');
        link.href = href;
        link.download = fileName;
        document.body.appendChild(link);
        link.click();
        link.remove();
    }

    /* `options.path` downloads a specific file instead of whatever the viewer
       has open — the context-menu entry point, so files GridVibe can't render
       (and therefore never open in the editor) are still reachable.
       `options.quiet` suppresses the per-file toast so a batch can report once
       instead of N times; the outcome is returned either way as
       `{ok, cancelled, fileName, error}`. */
    async function downloadExplorerFile(index, options = {}) {
        const quiet = options.quiet === true;
        /* One reporting door for both transports, so a failure can never leave
           through a success-shaped one. A cancelled save is the user's answer,
           not a failure, and says nothing. */
        const report = (result) => {
            if (!quiet && !result.cancelled) {
                showTerminalToast(
                    result.ok
                        ? result.message
                        : `Download failed: ${result.error || 'unknown error'}`,
                    result.ok ? 'success' : 'error'
                );
            }
            return result;
        };

        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId) {
            return { ok: false, cancelled: true, fileName: '' };
        }
        const explicitPath = String(options.path || '');
        if (!explicitPath && pane._explorerMode !== 'file') {
            return { ok: false, cancelled: true, fileName: '' };
        }
        const path = explicitPath || pane._explorerFilePath || '';
        const fileName = explicitPath
            ? (getDownloadBaseName(explicitPath) || 'download')
            : (pane._explorerFileName || 'download');
        const url = `/api/explorer/${encodeURIComponent(sessionId)}/download?path=${encodeURIComponent(path)}`;

        /* WebView2 silently ignores programmatic <a download> clicks, so in the
           native window route the save through the pywebview bridge (native
           Save dialog + server-side fetch). */
        if (isPywebviewAvailable() && window.pywebview.api.save_download) {
            try {
                const result = await window.pywebview.api.save_download(
                    url,
                    fileName,
                    typeof CURRENT_WORKSPACE_ID === 'undefined'
                        ? 'default'
                        : CURRENT_WORKSPACE_ID
                );
                if (result?.ok) {
                    return report({
                        ok: true,
                        fileName,
                        message: `Saved ${getDownloadBaseName(result.path) || fileName}`
                    });
                }
                if (result?.cancelled) {
                    return report({ ok: false, cancelled: true, fileName });
                }
                return report({ ok: false, fileName, error: result?.error || 'unknown error' });
            } catch (error) {
                return report({ ok: false, fileName, error: error?.message || String(error) });
            }
        }

        /* Browser mode. A programmatic <a download> click cannot observe the
           response, so a stale row's 404, a 403, or the server's size refusal
           all landed as a green success toast and no file — and once a
           selection could download N rows at once, as N of them. Fetch first so
           the status is real; only a body too large to hold goes to the anchor,
           and by then the status is already known. */
        const aborter = typeof AbortController === 'function' ? new AbortController() : null;
        let response;
        try {
            response = await fetch(url, aborter ? { signal: aborter.signal } : undefined);
        } catch (error) {
            return report({ ok: false, fileName, error: error?.message || String(error) });
        }
        if (!response.ok) {
            let reason = `HTTP ${response.status}`;
            try {
                const data = await response.json();
                if (data?.error) {
                    reason = data.error;
                }
            } catch (error) {
                // A body that is not the API's JSON error says nothing the
                // status code does not; keep the status.
            }
            return report({ ok: false, fileName, error: reason });
        }
        const declaredLength = Number(response.headers.get('Content-Length'));
        if (Number.isFinite(declaredLength) && declaredLength > EXPLORER_DOWNLOAD_BUFFER_MAX_BYTES) {
            // Drop this response unread — the anchor re-requests and streams it.
            aborter?.abort();
            triggerExplorerDownloadAnchor(url, fileName);
            return report({ ok: true, fileName, message: `Downloading ${fileName}…` });
        }
        try {
            const objectUrl = URL.createObjectURL(await response.blob());
            triggerExplorerDownloadAnchor(objectUrl, fileName);
            setTimeout(() => URL.revokeObjectURL(objectUrl), EXPLORER_DOWNLOAD_OBJECT_URL_TTL_MS);
            return report({ ok: true, fileName, message: `Downloaded ${fileName}` });
        } catch (error) {
            return report({ ok: false, fileName, error: error?.message || String(error) });
        }
    }

    /* One outcome for a whole batch, never one per file: nine stale rows used
       to produce nine green toasts and nothing on disk. Cancelled saves are the
       user's answer and drop out of both the count and the denominator. */
    function reportExplorerDownloadBatch(results) {
        const attempted = results.filter(result => result && !result.cancelled);
        if (!attempted.length) {
            return;
        }
        const failed = attempted.filter(result => !result.ok);
        if (!failed.length) {
            showTerminalToast(`Downloaded ${attempted.length} files`, 'success');
            return;
        }
        const saved = attempted.length - failed.length;
        showTerminalToast(
            `Downloaded ${saved} of ${attempted.length} files — `
            + `${failed.length} failed: ${failed[0].error || 'unknown error'}`,
            'error'
        );
    }

    /* Download several selected files as N sequential single-file downloads.

       There is no archive endpoint and this must not become one: each transfer
       stays the existing root-confined, size-capped read. Sequential because in
       the native window every file opens its own Save dialog through the
       pywebview bridge, and firing those concurrently would stack modal dialogs
       over each other. Past a threshold it asks first — N transfers (and in the
       native window, N dialogs) is not what a mis-click should cost. */
    async function downloadExplorerFiles(index, targets) {
        const sessionId = sessionIds[index];
        const files = (targets || []).filter(entry => entry && entry.path);
        if (!sessionId || !files.length) {
            return;
        }
        if (files.length === 1) {
            await downloadExplorerFile(index, { path: files[0].path });
            return;
        }
        const confirmCopy = GridVibeExplorerSelection.downloadConfirmCopy(files);
        if (confirmCopy) {
            const confirmed = await openGenericConfirmModal({
                title: confirmCopy.title,
                copy: GridVibeExplorerSelection.targetsCopyLine(files),
                note: 'Each file downloads separately.',
                confirmLabel: confirmCopy.confirmLabel,
                owner: `explorer-download:${sessionId}`
            });
            if (!confirmed) {
                return;
            }
        }
        const results = [];
        for (const entry of files) {
            // The pane can be closed or restarted mid-run; stop rather than
            // keep pulling files for a session that is gone.
            if (sessionIds[index] !== sessionId) {
                break;
            }
            results.push(await downloadExplorerFile(index, { path: entry.path, quiet: true }));
        }
        reportExplorerDownloadBatch(results);
    }

    function getDownloadBaseName(fullPath) {
        return String(fullPath || '').split(/[\\/]/).pop() || '';
    }

    /* Open the host OS file manager at whatever path the explorer bar currently
       shows (the open file for a file tab, otherwise the listed directory).
       Fully isolated from the explorer's browsing state — it only asks the
       backend to launch the local file manager and never mutates panes. */
    async function revealExplorerInOs(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId) {
            return;
        }
        const path = pane._explorerMode === 'file'
            ? (pane._explorerFilePath || '')
            : (pane._explorerPath || '');
        try {
            const response = await fetch(
                `/api/explorer/${encodeURIComponent(sessionId)}/reveal`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path }),
                }
            );
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                showTerminalToast(
                    `Could not open file manager: ${payload.error || response.statusText}`,
                    'error'
                );
                return;
            }
            showTerminalToast('Opening file location…', 'success');
        } catch (error) {
            showTerminalToast(`Could not open file manager: ${error?.message || error}`, 'error');
        }
    }

    function explorerEnsureViewerShell(index) {
        const list = document.getElementById(`explorer-list-${index}`);
        if (!list) {
            return null;
        }
        wireExplorerContextMenu(list, index);
        if (!list.dataset.presentationScrollBound) {
            list.dataset.presentationScrollBound = 'true';
            list.addEventListener('scroll', () => {
                explorerCaptureActiveTabView(index);
                notePanePresentationChanged(index, { continuous: true });
            }, { capture: true, passive: true });
        }
        let viewer = document.getElementById(`explorer-viewer-${index}`);
        if (!viewer) {
            list.innerHTML =
                `<div class="explorer-tab-strip" id="explorer-tabs-${index}" role="tablist" aria-label="Open files"></div>`
                + `<div class="explorer-viewer" id="explorer-viewer-${index}"></div>`;
            viewer = document.getElementById(`explorer-viewer-${index}`);
        }
        return viewer;
    }

    /* ── Preview-header breadcrumb (2.d, OD-3) ──
       Replaces the removed Back button: the explorer bar's path label becomes
       a trail of clickable ancestor segments (root included), each browsing
       that directory in the Preview tab. The final segment — the shown
       directory or file itself — is inert. */
    function renderExplorerPathBreadcrumb(index, path, { root = '', fallbackText = '' } = {}) {
        const label = document.getElementById(`explorer-path-${index}`);
        if (!label) {
            return;
        }
        const segments = String(path || '').replace(/\\/g, '/').split('/').filter(Boolean);
        if (!segments.length && fallbackText) {
            label.textContent = fallbackText;
            label.title = root || '';
            return;
        }
        const rootName = String(root || '').replace(/\\/g, '/').split('/').filter(Boolean).pop() || '/';
        const crumbs = [];
        if (segments.length) {
            crumbs.push(`<button type="button" class="explorer-crumb" data-explorer-crumb="" title="${escHtml(root || '/')}">${escHtml(rootName)}</button>`);
        } else {
            crumbs.push(`<span class="explorer-crumb current" title="${escHtml(root || '/')}">${escHtml(rootName)}</span>`);
        }
        let current = '';
        segments.forEach((segment, position) => {
            current = current ? `${current}/${segment}` : segment;
            if (position === segments.length - 1) {
                crumbs.push(`<span class="explorer-crumb current" title="${escHtml(current)}">${escHtml(segment)}</span>`);
            } else {
                crumbs.push(`<button type="button" class="explorer-crumb" data-explorer-crumb="${escHtml(current)}" title="${escHtml(current)}">${escHtml(segment)}</button>`);
            }
        });
        label.innerHTML = crumbs.join('<span class="explorer-crumb-sep" aria-hidden="true">/</span>');
        label.title = root || '';
        label.querySelectorAll('button[data-explorer-crumb]').forEach(button => {
            button.addEventListener('click', () => {
                loadExplorerPane(index, button.dataset.explorerCrumb || '');
            });
        });
    }

    /* Backward-compatible migration read for an older server/preset that has
       only per-pane aliases. A workspace value received from the server always
       wins, so render order can no longer choose the workspace appearance. */
    const appliedExplorerMdSessions = new Set();
    function applyExplorerSessionMarkdownAppearance(index) {
        const sessionId = sessionIds[index];
        const session = terminals[index]?._session || {};
        const preset = session.explorer_md_preset || '';
        const font = session.explorer_md_font || '';
        const sourceFont = session.explorer_source_font || '';
        const hasAny = Boolean(preset || font || sourceFont);
        if (
            workspaceExplorerAppearance
            || !sessionId
            || appliedExplorerMdSessions.has(sessionId)
            || !hasAny
        ) {
            return;
        }
        appliedExplorerMdSessions.add(sessionId);
        setExplorerMarkdownAppearance({ preset, font, sourceFont });
    }

    /* Called only where a session is genuinely gone — a closed pane, a closed
       session group, or a group this window no longer owns. Switching groups
       must *not* reach this: the sessions live on in another window or behind a
       cached view, and forgetting them would re-apply a saved appearance over a
       change the user made since launch, which is the exact thing the set
       exists to prevent. */
    function forgetExplorerSessionMarkdownAppearance(sessionId) {
        appliedExplorerMdSessions.delete(String(sessionId || ''));
    }

    /* Entry point when an explorer pane first shows: empty read-only viewer with
       the Files tree opened for navigation, plus any persisted tabs restored. */
    function openExplorerViewer(index) {
        const pane = terminals[index];
        if (!pane) {
            return false;
        }
        ensureExplorerTabState(pane);
        pane._attached = true;
        document.getElementById(`ph-${index}`)?.remove();
        renderExplorerViewerEmpty(index);
        if (explorerOpenSidebarPanels(pane).length === 0) {
            setExplorerTreeSidebarOpen(index, true);
        }
        applyExplorerSessionMarkdownAppearance(index);
        restoreExplorerPersistedTabs(index);
        restoreExplorerSidebarPresentation(index);
        return true;
    }

    /* ── Markdown preview link navigation (ISSUE-2026-016) ──
       Relative Markdown links resolve against the current file and open as a
       pinned tab; fragments scroll to the heading; external links open in an
       isolated window without navigating the session page away. */
    function explorerClassifyLink(href) {
        const trimmed = String(href == null ? '' : href).trim();
        if (!trimmed) {
            return { type: 'ignore' };
        }
        if (trimmed.startsWith('#')) {
            return { type: 'fragment', fragment: trimmed.slice(1) };
        }
        if (/^\/\//.test(trimmed)) {
            return { type: 'external', href: `https:${trimmed}` };
        }
        if (/^[a-z][a-z0-9+.-]*:/i.test(trimmed)) {
            if (/^https?:/i.test(trimmed)) {
                return { type: 'external', href: trimmed };
            }
            if (/^mailto:/i.test(trimmed)) {
                return { type: 'mailto' };
            }
            return { type: 'unsupported' };
        }
        return { type: 'relative', href: trimmed };
    }

    /* Resolve a relative link against the source file, rejecting anything that
       escapes the Explorer root. Returns { path, fragment } or null. */
    function explorerResolveRelativePath(baseFilePath, href) {
        const hashIndex = href.indexOf('#');
        const fragment = hashIndex >= 0 ? href.slice(hashIndex + 1) : '';
        let rel = hashIndex >= 0 ? href.slice(0, hashIndex) : href;
        if (!rel) {
            return { path: explorerNormalizeTabPath(baseFilePath), fragment };
        }
        try {
            rel = decodeURIComponent(rel);
        } catch (_) {
            return null;
        }
        rel = rel.replace(/\\/g, '/');
        const absolute = rel.startsWith('/');
        const baseSegments = absolute
            ? []
            : String(baseFilePath || '').replace(/\\/g, '/').split('/').slice(0, -1);
        const segments = baseSegments.filter(Boolean);
        for (const segment of rel.split('/')) {
            if (segment === '' || segment === '.') {
                continue;
            }
            if (segment === '..') {
                if (!segments.length) {
                    return null;
                }
                segments.pop();
                continue;
            }
            if (segment.includes(':')) {
                return null;
            }
            segments.push(segment);
        }
        const path = segments.join('/');
        return path ? { path, fragment } : null;
    }

    function explorerHeadingSlug(text) {
        return String(text || '')
            .toLowerCase()
            .trim()
            .replace(/[^\w\s-]/g, '')
            .replace(/\s+/g, '-');
    }

    function explorerScrollPreviewToHeading(preview, fragment) {
        if (!preview || !fragment) {
            return;
        }
        let target = null;
        try {
            target = preview.querySelector(`#${CSS.escape(fragment)}`);
        } catch (_) {
            target = null;
        }
        if (!target) {
            const slug = explorerHeadingSlug(fragment);
            target = Array.from(preview.querySelectorAll('h1, h2, h3, h4, h5, h6'))
                .find(heading => explorerHeadingSlug(heading.textContent) === slug) || null;
        }
        if (target) {
            target.scrollIntoView({ block: 'start' });
        }
    }

    function wireExplorerMarkdownLinks(index, preview) {
        if (!preview || preview.dataset.mdLinksBound) {
            return;
        }
        preview.dataset.mdLinksBound = 'true';
        preview.addEventListener('click', event => {
            const anchor = event.target.closest('a[href]');
            if (!anchor || !preview.contains(anchor)) {
                return;
            }
            const info = explorerClassifyLink(anchor.getAttribute('href') || '');
            if (info.type === 'fragment') {
                event.preventDefault();
                explorerScrollPreviewToHeading(preview, info.fragment);
                return;
            }
            if (info.type === 'external') {
                event.preventDefault();
                window.open(info.href, '_blank', 'noopener,noreferrer');
                return;
            }
            if (info.type === 'mailto') {
                return;
            }
            if (info.type !== 'relative') {
                event.preventDefault();
                return;
            }
            event.preventDefault();
            const pane = terminals[index];
            const resolved = explorerResolveRelativePath(pane?._explorerFilePath || '', info.href);
            if (!resolved || !resolved.path) {
                return;
            }
            Promise.resolve(openExplorerFile(index, resolved.path, { pinned: true })).then(opened => {
                if (opened && resolved.fragment) {
                    requestAnimationFrame(() => {
                        const nextPreview = document.getElementById(`explorer-preview-${index}`);
                        if (nextPreview) {
                            explorerScrollPreviewToHeading(nextPreview, resolved.fragment);
                        }
                    });
                }
            });
        });
    }

    /* Read-only inline image viewer (ISSUE-2026 image support). The backend
       returns preview_type "image" with no content; the bytes stream from the
       dedicated /image route. Shares the tab strip, breadcrumb, and download
       button with the text viewer so tabs/refresh/persistence are unchanged. */
    function renderExplorerImage(index, data, { assignedTab = null } = {}) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const list = document.getElementById(`explorer-list-${index}`);
        const viewer = explorerEnsureViewerShell(index);
        if (!pane || !sessionId || !list || !viewer) {
            return false;
        }

        const path = data.path || '';
        const fileName = data.name || path || 'Image';
        if (assignedTab) {
            pane._explorerRenderedTabId = assignedTab.id;
        }

        cancelExplorerSearch(index);
        const searchState = ensureExplorerSearchState(pane, 'file');
        searchState.ranges = [];
        searchState.resultQuery = '';
        searchState.matchCount = 0;
        searchState.matchCapped = false;
        clearExplorerDirectorySearchControls(index);

        pane._attached = true;
        pane._explorerMode = 'file';
        pane._explorerFilePath = path;
        pane._explorerFileName = fileName;
        pane._explorerFileContent = '';
        pane._explorerFileLanguage = '';
        applyExplorerSourceTier(pane, '');
        pane._explorerPreviewHtml = '';
        pane._explorerPreviewLoaded = false;
        pane._explorerGit = null;
        /* `_explorerGitContext` describes the repository the *pane* is rooted
           in, not the shown file, so the image viewer leaves it alone — the
           image payload simply carries none, and clearing it would silently
           stop the filesystem-surface listener for a pane whose Files tree is
           still on screen. */
        // Images render through the separate byte route; nothing here for the
        // open-file change listener to refresh in place.
        setExplorerFileWatchBaseline(pane, '');
        pane._explorerDiffLoaded = false;
        pane._explorerDiffCacheKey = '';
        pane._explorerDiffContent = '';
        pane._explorerDiffSplit = false;
        pane._explorerDiffCommit = '';
        pane._explorerDiffMode = '';
        // No Source rows on the image viewer; drop any marks the pane held
        // for the previous file.
        teardownExplorerOverview(index);
        pane._explorerLastFileView = 'source';
        pane._explorerPendingDiffScroll = null;

        const metaParts = ['Image'];
        const sizeLabel = formatExplorerSize(data.size);
        if (sizeLabel) {
            metaParts.push(sizeLabel);
        }
        const modifiedLabel = formatExplorerDate(data.modified);
        if (modifiedLabel) {
            metaParts.push(modifiedLabel);
        }
        const baseMeta = metaParts.join(' - ');
        const imageUrl = `/api/explorer/${encodeURIComponent(sessionId)}/image?path=${encodeURIComponent(path)}`;

        document.getElementById(`ph-${index}`)?.remove();
        list.classList.add('file-view');
        updateExplorerGitSummary(index, null);
        renderExplorerPathBreadcrumb(index, path, { root: data.root || '', fallbackText: fileName });
        const upButton = document.getElementById(`explorer-up-${index}`);
        if (upButton) {
            upButton.disabled = false;
        }

        viewer.innerHTML = `
            <div class="explorer-editor explorer-image-editor">
                <div class="explorer-editor-header">
                    <div class="explorer-editor-title">
                        <div class="explorer-editor-name" title="${escHtml(path || fileName)}">${escHtml(fileName)}</div>
                        <div class="explorer-editor-meta" data-explorer-image-meta="${index}">${escHtml(baseMeta)}</div>
                    </div>
                    <button type="button" class="explorer-download-btn" data-explorer-download="${index}" title="Download file" aria-label="Download file">${EXPLORER_DOWNLOAD_ICON}</button>
                </div>
                <div class="explorer-editor-body">
                    <div class="explorer-image-view" id="explorer-image-${index}">
                        <img class="explorer-image" alt="${escHtml(fileName)}" src="${escHtml(imageUrl)}">
                    </div>
                </div>
            </div>
        `;

        const downloadButton = list.querySelector(`[data-explorer-download="${index}"]`);
        if (downloadButton) {
            downloadButton.addEventListener('click', () => downloadExplorerFile(index));
        }
        const image = viewer.querySelector('.explorer-image');
        if (image) {
            /* Ctrl+scroll zooms the image (notes 3); double-click resets it. */
            enableExplorerWheelZoom(document.getElementById(`explorer-image-${index}`), image);
            image.addEventListener('load', () => {
                const metaEl = viewer.querySelector(`[data-explorer-image-meta="${index}"]`);
                if (metaEl && image.naturalWidth) {
                    metaEl.textContent = `${baseMeta} - ${image.naturalWidth} × ${image.naturalHeight}`;
                }
            });
            image.addEventListener('error', () => {
                const view = document.getElementById(`explorer-image-${index}`);
                if (view) {
                    view.innerHTML = '<div class="explorer-image-error">Unable to display this image.</div>';
                }
            });
        }

        renderExplorerTabStrip(index);
        persistExplorerTabsToSession(index);
        syncExplorerGitActiveRows(index);
        return true;
    }

    function renderExplorerFile(index, data, { scrollState = null, openDiff = false, diffCommit = '', diffMode = '', tab = '', pinned = false, restoreTabView = true } = {}) {
        const pane = terminals[index];
        const list = document.getElementById(`explorer-list-${index}`);
        const viewer = explorerEnsureViewerShell(index);
        if (!pane || !list || !viewer) {
            return false;
        }
        const assignedTab = explorerAssignOpenTab(pane, data.path || '', { pinned, tab });
        assignedTab.git = data.git || null;
        pane._explorerRenderedTabId = assignedTab.id;

        const path = data.path || '';
        const fileName = data.name || path || 'File';
        // Images render in a dedicated read-only viewer (no source/preview/diff/
        // search/zoom), reusing the surrounding tab, breadcrumb, and download
        // plumbing so tabs, refresh, and persistence keep working unchanged.
        if (data.preview_type === 'image') {
            return renderExplorerImage(index, data, { assignedTab });
        }
        const codeLanguage = normalizeExplorerLanguage(data.language) || explorerCodeLanguage(path || fileName);
        const fileType = explorerFileTypeLabel(path || fileName, codeLanguage);
        /* Read `preview_type` and never the HTML string. The panel's existence
           is a property of the file; the HTML now arrives lazily, so deriving
           it from `typeof data.preview_html === 'string'` would make the panel
           blink out of existence on every load — and, since a save answers with
           the same payload shape, would make every save on a Markdown file
           bail updateExplorerFileInPlace() into a full pane rebuild. */
        const hasPreview = data.preview_type === 'markdown';
        const requestedDiffCommit = String(diffCommit || '');
        const requestedDiffMode = requestedDiffCommit ? '' : String(diffMode || '');
        const hasGitDiff = explorerHasGitDiff(data.git) || Boolean(requestedDiffCommit);
        const metaParts = explorerFileMetaParts(data, fileType);
        const previousPath = pane._explorerFilePath || '';
        const contentIdentity = explorerFileContentIdentity(path, data.content);
        const currentContentRevisions = {
            source: contentIdentity,
            preview: contentIdentity
        };
        if (assignedTab.collapsedIdentity !== contentIdentity) {
            assignedTab.collapsedLines = new Set();
            assignedTab.collapsedIdentity = contentIdentity;
        }
        /* 2.e: restore the tab's stored view mode + scroll when the content is
           still what the snapshot was taken from (OD-4 identity check). */
        const restoredTabView = !restoreTabView || scrollState
            ? null
            : explorerMatchingTabView(
                assignedTab,
                currentContentRevisions
            );
        const restoredMode = restoredTabView ? restoredTabView.mode : '';
        /* The Preview tab also keeps its sticky source/preview preference
           across *different* files (explicit diff requests and an
           identity-matched snapshot win); files without a Markdown preview
           fall back to source. Scroll does not carry across files. */
        const carriedMode = !restoredTabView
            && assignedTab
            && assignedTab.id === EXPLORER_PREVIEW_TAB_ID
            && !openDiff && !requestedDiffCommit && !requestedDiffMode
            ? assignedTab.preferredMode || ''
            : '';
        const preferredFileView = restoredMode || carriedMode;
        const keepDiffSplit = hasGitDiff && (
            Boolean(openDiff)
            || Boolean(requestedDiffCommit)
            || restoredMode === 'diff'
            || (
                previousPath === path
                && pane._explorerDiffSplit
                && pane._explorerDiffCommit === requestedDiffCommit
                && (pane._explorerDiffMode || '') === requestedDiffMode
            )
        );
        const initialFileView = keepDiffSplit
            ? 'diff'
            : (preferredFileView === 'preview' && hasPreview ? 'preview' : 'source');
        // An explicit scrollState (in-place refresh) wins; otherwise fall back
        // to the tab's stored snapshot, aligned with the restored view mode.
        const effectiveScrollState = scrollState || (restoredTabView
            ? { ...restoredTabView.scroll, activeView: initialFileView }
            : null);
        const searchState = ensureExplorerSearchState(pane, 'file');
        /* The query the incoming surface gets is the one captured against
           *this* tab and *this* path (explorerCaptureActiveTabView), not
           whatever the pane was last searching. A pane-wide query is what made
           a tab switch paint the previous file's find over the new one and
           then scroll it to the first hit. */
        const tabFind = assignedTab?.find;
        const restoredQuery = tabFind
            && explorerNormalizeTabPath(tabFind.path) === explorerNormalizeTabPath(path)
            ? String(tabFind.query || '')
            : '';
        if (restoredQuery !== searchState.query || (previousPath && previousPath !== path)) {
            cancelExplorerSearch(index);
            searchState.query = restoredQuery;
            searchState.activeIndex = 0;
            searchState.matchCount = 0;
            searchState.matchCapped = false;
            searchState.ranges = [];
            searchState.resultQuery = '';
        }
        clearExplorerDirectorySearchControls(index);

        pane._attached = true;
        pane._explorerMode = 'file';
        // A full rebuild tears down any active in-place editor; deliberate
        // teardown paths guard the dirty buffer before reaching here.
        pane._explorerEdit = null;
        pane._explorerFilePath = path;
        pane._explorerFileName = fileName;
        pane._explorerFileContent = data.content || '';
        pane._explorerFileEditable = Boolean(data.editable);
        pane._explorerFileEditBlockReason = data.edit_block_reason || '';
        pane._explorerFileRevision = data.revision || '';
        setExplorerFileWatchBaseline(pane, data.state_revision || '');
        pane._explorerFileLineEnding = data.line_ending || '';
        pane._explorerFileUtf8Bom = Boolean(data.utf8_bom);
        pane._explorerFileTruncated = Boolean(data.truncated);
        pane._explorerFileLanguage = codeLanguage;
        applyExplorerSourceTier(pane, pane._explorerFileContent);
        /* Read *after* the tier is recomputed for the incoming content, never
           before: the large tier renders no per-line rows for a find to
           address, so the header renders no find bar, and deciding that from
           the outgoing file's tier put one on a large file opened after a small
           one. updateExplorerFileInPlace() treats a change in this value the
           way it treats a change in `hasPreview` — the header is not the same
           header, so it hands back to a full rebuild. */
        const findAvailable = explorerPaneAllowsFind(pane);
        // Not fetched yet — the panel exists from here, its content arrives the
        // first time it is shown. The flag, not the string, is what says so:
        // an empty Markdown file renders an empty preview, legitimately.
        pane._explorerPreviewHtml = '';
        pane._explorerPreviewLoaded = false;
        pane._explorerGit = data.git || null;
        pane._explorerGitContext = data.git_context || null;
        pane._explorerDiffLoaded = false;
        pane._explorerDiffCacheKey = '';
        pane._explorerDiffContent = '';
        pane._explorerDiffSplit = keepDiffSplit;
        pane._explorerDiffCommit = requestedDiffCommit;
        pane._explorerDiffMode = requestedDiffMode;
        // The change-mark cache is keyed by path + HEAD, so a rebuild of the
        // same file could legitimately hit it — but renderExplorerFile is
        // also how saves/undos with a changed panel set land, and those move
        // the diff. Drop the key and let the load below refetch.
        pane._explorerChangeMarksKey = '';
        pane._explorerLastFileView = initialFileView === 'preview'
            ? 'preview'
            : (pane._explorerLastFileView === 'preview' && hasPreview ? 'preview' : 'source');
        // Diff content loads async; stash the restored diff scroll until then.
        pane._explorerPendingDiffScroll = initialFileView === 'diff'
            ? (restoredTabView && restoredTabView.scroll.panels
                ? restoredTabView.scroll.panels.diff
                    || (restoredTabView.record
                        ? { persistedRecord: restoredTabView.record }
                        : null)
                : null)
            : null;
        /* Install every panel's offset before an active Preview/Diff can start
           its async load. Source still waits on whenExplorerSourceRendered()
           below; each async panel reapplies from its own arrival hook. */
        setExplorerPanelScrollState(index, effectiveScrollState);
        document.getElementById(`ph-${index}`)?.remove();
        list.classList.add('file-view');
        updateExplorerGitSummary(index, data.git_context || null);

        renderExplorerPathBreadcrumb(index, path, { root: data.root || '', fallbackText: fileName });
        const upButton = document.getElementById(`explorer-up-${index}`);
        if (upButton) {
            upButton.disabled = false;
        }

        viewer.innerHTML = `
            <div class="explorer-editor">
                <div class="explorer-editor-header">
                    <div class="explorer-editor-title">
                        <div class="explorer-editor-name" title="${escHtml(path || fileName)}">${escHtml(fileName)}</div>
                        <div class="explorer-editor-meta">${escHtml(metaParts.join(' - '))}</div>
                    </div>
                    ${(hasPreview || hasGitDiff) ? `
                        <div class="explorer-editor-tabs" role="tablist" aria-label="File view">
                            <button type="button" class="explorer-editor-tab" data-explorer-file-view="source" role="tab" aria-selected="${initialFileView === 'source' ? 'true' : 'false'}">Source</button>
                            ${hasPreview ? `<button type="button" class="explorer-editor-tab" data-explorer-file-view="preview" role="tab" aria-selected="${initialFileView === 'preview' ? 'true' : 'false'}" aria-keyshortcuts="Control+Shift+V Meta+Shift+V" title="Preview (Ctrl+Shift+V)">Preview</button>` : ''}
                            ${hasGitDiff ? `<button type="button" class="explorer-editor-tab" data-explorer-file-view="diff" data-explorer-diff-toggle="${index}" role="tab" aria-selected="${initialFileView === 'diff' ? 'true' : 'false'}" aria-pressed="${keepDiffSplit ? 'true' : 'false'}">Diff</button>` : ''}
                        </div>
                    ` : ''}
                    <div class="explorer-editor-zoom" aria-label="Editor font size controls">
                        <button type="button" class="explorer-zoom-btn" data-explorer-zoom-decrease="${index}" title="Decrease font size" aria-label="Decrease editor font size">${UI_MINUS_ICON}</button>
                        <span class="explorer-zoom-value" data-explorer-zoom-value="${index}"></span>
                        <button type="button" class="explorer-zoom-btn" data-explorer-zoom-increase="${index}" title="Increase font size" aria-label="Increase editor font size">${UI_PLUS_ICON}</button>
                    </div>
                    ${explorerLineWrapControlHtml(index, initialFileView)}
                    <button type="button" class="explorer-md-appearance-btn" data-explorer-md-appearance="${index}" title="Appearance" aria-label="Viewer appearance" aria-haspopup="menu" aria-expanded="false">${EXPLORER_MD_APPEARANCE_ICON}</button>
                    ${explorerEditorControlsHtml(index)}
                    <button type="button" class="explorer-download-btn" data-explorer-download="${index}" title="Download file" aria-label="Download file">${EXPLORER_DOWNLOAD_ICON}</button>
                    ${findAvailable ? `<div class="explorer-editor-search" data-explorer-search="${index}">
                        <input
                            type="search"
                            class="explorer-search-input"
                            data-explorer-search-input="${index}"
                            placeholder="Find"
                            autocomplete="off"
                            spellcheck="false"
                            aria-label="Find in file"
                        >
                        <span class="explorer-search-count" data-explorer-search-count="${index}"></span>
                        <button type="button" class="explorer-search-btn" data-explorer-search-prev="${index}" title="Previous match" aria-label="Previous match">↑</button>
                        <button type="button" class="explorer-search-btn" data-explorer-search-next="${index}" title="Next match" aria-label="Next match">↓</button>
                        <button type="button" class="explorer-search-btn" data-explorer-search-clear="${index}" title="Clear search" aria-label="Clear search">×</button>
                    </div>` : ''}
                </div>
                <div class="explorer-editor-body${keepDiffSplit ? ' split-diff' : ''}">
                    <div class="explorer-editor-main">
                        <div class="explorer-source-frame explorer-editor-panel" data-explorer-file-panel="source" ${initialFileView === 'source' ? '' : 'hidden'}><div class="explorer-source-view" id="explorer-code-${index}"></div>${explorerOverviewHtml(index)}</div>
                        ${hasPreview ? `<div class="explorer-markdown-preview explorer-editor-panel" id="explorer-preview-${index}" data-explorer-file-panel="preview" ${initialFileView === 'preview' ? '' : 'hidden'}></div>` : ''}
                    </div>
                    ${hasGitDiff ? `<aside class="explorer-diff-split" id="explorer-diff-panel-${index}" data-explorer-file-panel="diff" ${keepDiffSplit ? '' : 'hidden'}><div class="explorer-diff-content" id="explorer-diff-code-${index}"></div></aside>` : ''}
                </div>
            </div>
        `;

        renderExplorerSource(index);
        // Kick off the HEAD change-mark load for the Source gutter (async;
        // paints itself when the diff arrives). Gated internally on a Git
        // worktree, a non-commit view and no active editor.
        loadExplorerChangeMarks(index);
        const sourceFontAppearance = explorerMarkdownAppearance();
        applyExplorerSourceFontToElement(
            document.getElementById(`explorer-code-${index}`), sourceFontAppearance
        );
        applyExplorerSourceFontToElement(
            document.getElementById(`explorer-diff-code-${index}`), sourceFontAppearance
        );
        // Only the panel the reader is actually looking at pays for its
        // content; selecting Preview later goes through the same loader.
        if (hasPreview && initialFileView === 'preview') {
            ensureExplorerPreviewLoaded(index);
        }

        const appearanceButton = list.querySelector(`[data-explorer-md-appearance="${index}"]`);
        if (appearanceButton) {
            appearanceButton.addEventListener('click', () => {
                if (document.getElementById('explorer-md-menu')) {
                    dismissExplorerMarkdownAppearanceMenu();
                } else {
                    showExplorerMarkdownAppearanceMenu(appearanceButton, { includeMarkdown: hasPreview });
                }
            });
        }
        wireExplorerLineWrapControl(index);

        const downloadButton = list.querySelector(`[data-explorer-download="${index}"]`);
        if (downloadButton) {
            downloadButton.addEventListener('click', () => {
                downloadExplorerFile(index);
            });
        }
        list.querySelectorAll('[data-explorer-file-view]').forEach(button => {
            button.addEventListener('click', () => {
                if (button.dataset.explorerFileView === 'diff') {
                    toggleExplorerDiffSplit(index);
                } else {
                    setExplorerFileView(index, button.dataset.explorerFileView || 'source');
                }
            });
        });
        if (keepDiffSplit) {
            loadExplorerDiff(index);
        }
        wireExplorerEditorZoomControls(index);
        wireExplorerSearchControls(index);
        refreshExplorerEditControls(index);
        /* A rebuild is a repaint, not a navigation: the restore on the next
           line owns where this file opens. Letting the find scroll here put
           the reader at match 1 of a query they had not retyped, a frame
           before the stored offset was applied — and the last writer won. */
        applyExplorerSearch(index, { scroll: false });
        whenExplorerSourceRendered(index, () => restoreExplorerFileScroll(index, effectiveScrollState));
        renderExplorerTabStrip(index);
        persistExplorerTabsToSession(index);
        syncExplorerGitActiveRows(index);
        return true;
    }

    function updateExplorerFileInPlace(index, data, scrollState = null) {
        const pane = terminals[index];
        const list = document.getElementById(`explorer-list-${index}`);
        const code = document.getElementById(`explorer-code-${index}`);
        if (!pane || !list || !code) {
            return false;
        }

        const path = data.path || '';
        if (path && pane._explorerFilePath && path !== pane._explorerFilePath) {
            return false;
        }

        /* Read `preview_type` and never the HTML string. The panel's existence
           is a property of the file; the HTML now arrives lazily, so deriving
           it from `typeof data.preview_html === 'string'` would make the panel
           blink out of existence on every load — and, since a save answers with
           the same payload shape, would make every save on a Markdown file
           bail updateExplorerFileInPlace() into a full pane rebuild. */
        const hasPreview = data.preview_type === 'markdown';
        const hasGitDiff = explorerHasGitDiff(data.git);
        const preview = document.getElementById(`explorer-preview-${index}`);
        const diffPanel = document.getElementById(`explorer-diff-code-${index}`);
        if (hasPreview !== Boolean(preview) || hasGitDiff !== Boolean(diffPanel)) {
            return false;
        }
        /* A save (or an external write) that pushes a file across the tier
           boundary changes the header, not just the body: the find bar appears
           or disappears with the tier. That is the same class of shape change
           as the preview panel flipping, and takes the same answer — hand back
           to a full rebuild rather than update in place around a control that
           is no longer the one standing there. */
        const policy = explorerTierPolicy();
        const nextTier = policy
            ? policy.sourceTierForContent(data.content || '')
            : explorerPaneSourceTier(pane);
        if (nextTier !== explorerPaneSourceTier(pane)) {
            return false;
        }

        const fileName = data.name || path || 'File';
        const codeLanguage = normalizeExplorerLanguage(data.language) || explorerCodeLanguage(path || fileName);
        const fileType = explorerFileTypeLabel(path || fileName, codeLanguage);
        cancelExplorerSearch(index);
        const searchState = ensureExplorerSearchState(pane, 'file');
        searchState.ranges = [];
        searchState.resultQuery = '';
        searchState.matchCapped = false;
        pane._explorerFileContent = data.content || '';
        pane._explorerFileEditable = Boolean(data.editable);
        pane._explorerFileEditBlockReason = data.edit_block_reason || '';
        pane._explorerFileRevision = data.revision || '';
        setExplorerFileWatchBaseline(pane, data.state_revision || '');
        pane._explorerFileLineEnding = data.line_ending || '';
        pane._explorerFileUtf8Bom = Boolean(data.utf8_bom);
        pane._explorerFileTruncated = Boolean(data.truncated);
        pane._explorerFileLanguage = codeLanguage;
        applyExplorerSourceTier(pane, pane._explorerFileContent);
        /* The file moved on disk, so any preview the pane is holding describes
           the old bytes. Drop it; the panel refills below if it is the one on
           screen, and otherwise on the next visit to it. */
        pane._explorerPreviewHtml = '';
        pane._explorerPreviewLoaded = false;
        pane._explorerGit = data.git || null;
        pane._explorerGitContext = data.git_context || null;
        const renderedTab = explorerFindTab(
            pane,
            pane._explorerRenderedTabId || pane._explorerActiveTabId
        );
        if (renderedTab) {
            renderedTab.git = data.git || null;
        }
        pane._explorerDiffLoaded = false;
        pane._explorerDiffCacheKey = '';
        pane._explorerDiffContent = '';
        /* The old viewport still describes where the reader was in this file,
           but it must be associated with the replacement bytes before a lazy
           Preview request can answer. */
        setExplorerPanelScrollState(index, scrollState);
        renderExplorerSource(index);
        // An in-place refresh means the file moved on disk (save, undo,
        // watcher) while HEAD usually did not, so the path + HEAD cache key
        // would serve stale marks: force the refetch.
        loadExplorerChangeMarks(index, { force: true });
        if (preview && hasPreview && activeExplorerFileView(index) === 'preview') {
            ensureExplorerPreviewLoaded(index);
        }
        if (diffPanel && hasGitDiff) {
            // The header survives an in-place refresh, so a Diff toggle hidden
            // by an earlier empty-diff fallback has to be re-exposed here once
            // the file is reported as changed again.
            setExplorerDiffToggleHidden(index, false);
            renderExplorerDiff(index);
            if (pane._explorerDiffSplit) {
                loadExplorerDiff(index);
            }
        }
        applyExplorerEditorFontSize(index);
        updateExplorerGitSummary(index, data.git_context || null);

        const nameLabel = list.querySelector('.explorer-editor-name');
        if (nameLabel) {
            nameLabel.textContent = fileName;
            nameLabel.title = path || fileName;
        }
        const metaLabel = list.querySelector('.explorer-editor-meta');
        if (metaLabel) {
            metaLabel.textContent = explorerFileMetaParts(data, fileType).join(' - ');
        }
        renderExplorerPathBreadcrumb(index, path || pane._explorerFilePath, {
            root: data.root || '',
            fallbackText: fileName
        });

        pane._explorerMode = 'file';
        pane._explorerFilePath = path || pane._explorerFilePath;
        // An in-place refresh always lands on the read-only view: drop any stale
        // editor chrome and refresh the Edit button's enabled state + revision.
        setExplorerEditChromeDisabled(index, false);
        refreshExplorerEditControls(index);
        // The captured position is restored on the next line; a find repainted
        // onto the refreshed rows must not undo that from its own frame.
        applyExplorerSearch(index, { scroll: false });
        whenExplorerSourceRendered(index, () => restoreExplorerFileScroll(index, scrollState));
        renderExplorerTabStrip(index);
        return true;
    }

    function renderExplorerCommitDiffFile(index, path, commit) {
        const pane = terminals[index];
        const list = document.getElementById(`explorer-list-${index}`);
        const viewer = explorerEnsureViewerShell(index);
        if (!pane || !list || !viewer || !path || !commit) {
            return false;
        }

        const fileName = path.split(/[\\/]/).filter(Boolean).pop() || path;
        const codeLanguage = explorerCodeLanguage(path);
        clearExplorerDirectorySearchControls(index);
        cancelExplorerSearch(index);
        explorerCaptureActiveTabView(index);
        const commitTab = explorerAssignOpenTab(pane, path, {});
        // The worktree status of this path is not part of a commit-diff
        // payload; drop the outgoing file's badge rather than mislabel it, and
        // let the next Git sidebar sync fill it back in.
        commitTab.git = null;
        pane._explorerRenderedTabId = commitTab.id;

        pane._attached = true;
        pane._explorerMode = 'file';
        pane._explorerFilePath = path;
        pane._explorerFileContent = '';
        pane._explorerFileLanguage = codeLanguage;
        /* The tier is a pane field and this view is `file` mode with no buffer
           behind it, so it has to be restated here like every other path that
           repoints a pane at new content. Inheriting the outgoing file's
           `large` tier left the commit diff showing a Find bar that
           applyExplorerSearch() then refused to serve — and, because the input
           existed, focusExplorerSearch() still claimed Ctrl+F, so the reader
           lost the browser's own find as well. */
        applyExplorerSourceTier(pane, '');
        pane._explorerPreviewHtml = '';
        pane._explorerPreviewLoaded = false;
        pane._explorerGit = null;
        // A commit diff is history: nothing on disk can change what it shows.
        setExplorerFileWatchBaseline(pane, '');
        pane._explorerDiffLoaded = false;
        pane._explorerDiffCacheKey = '';
        pane._explorerDiffContent = '';
        pane._explorerDiffSplit = true;
        pane._explorerDiffCommit = commit;
        pane._explorerDiffMode = '';
        pane._explorerLastFileView = 'source';
        pane._explorerPendingDiffScroll = null;
        document.getElementById(`ph-${index}`)?.remove();
        list.classList.add('file-view');

        renderExplorerPathBreadcrumb(index, path, { fallbackText: fileName });
        const upButton = document.getElementById(`explorer-up-${index}`);
        if (upButton) {
            upButton.disabled = false;
        }

        viewer.innerHTML = `
            <div class="explorer-editor">
                <div class="explorer-editor-header">
                    <div class="explorer-editor-title">
                        <div class="explorer-editor-name" title="${escHtml(path)}">${escHtml(fileName)}</div>
                        <div class="explorer-editor-meta">${escHtml(`Git commit diff - ${commit.slice(0, 7)}`)}</div>
                    </div>
                    <div class="explorer-editor-tabs" role="tablist" aria-label="File view">
                        <button type="button" class="explorer-editor-tab" data-explorer-file-view="diff" data-explorer-diff-toggle="${index}" role="tab" aria-selected="true" aria-pressed="true">Diff</button>
                    </div>
                    ${explorerLineWrapControlHtml(index, 'diff')}
                    <div class="explorer-editor-search" data-explorer-search="${index}">
                        <input
                            type="search"
                            class="explorer-search-input"
                            data-explorer-search-input="${index}"
                            placeholder="Find"
                            autocomplete="off"
                            spellcheck="false"
                            aria-label="Find in file"
                        >
                        <span class="explorer-search-count" data-explorer-search-count="${index}"></span>
                        <button type="button" class="explorer-search-btn" data-explorer-search-prev="${index}" title="Previous match" aria-label="Previous match">↑</button>
                        <button type="button" class="explorer-search-btn" data-explorer-search-next="${index}" title="Next match" aria-label="Next match">↓</button>
                        <button type="button" class="explorer-search-btn" data-explorer-search-clear="${index}" title="Clear search" aria-label="Clear search">×</button>
                    </div>
                </div>
                <div class="explorer-editor-body split-diff">
                    <aside class="explorer-diff-split" id="explorer-diff-panel-${index}" data-explorer-file-panel="diff"><div class="explorer-diff-content" id="explorer-diff-code-${index}"></div></aside>
                </div>
            </div>
        `;

        list.querySelectorAll('[data-explorer-file-view]').forEach(button => {
            button.addEventListener('click', () => {
                if (button.dataset.explorerFileView === 'diff') {
                    setExplorerFileView(index, 'diff');
                }
            });
        });
        wireExplorerLineWrapControl(index);
        wireExplorerSearchControls(index);
        applyExplorerEditorFontSize(index);
        applyExplorerSourceFontToElement(
            document.getElementById(`explorer-diff-code-${index}`), explorerMarkdownAppearance()
        );
        loadExplorerDiff(index);
        renderExplorerTabStrip(index);
        syncExplorerGitActiveRows(index);
        return true;
    }

    async function openExplorerFile(index, path, { showLoading = true, preserveScroll = false, openDiff = false, diffCommit = '', diffMode = '', pinned = false, tab = '', restoreTabView = true } = {}) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !isExplorerSession(pane._session) || !sessionId || !path) {
            return false;
        }
        /* Cleared before any early return and read back by the tab restore,
           which prunes a tab only when the backend says the path is gone — a
           connection hiccup, or a caller that never got as far as a request,
           must not throw away a tab whose file is still there. */
        pane._explorerOpenErrorCode = '';
        // Replacing the viewer with another file discards any active edit.
        if (!(await confirmDiscardExplorerEdit(index, 'Opening another file'))) {
            return false;
        }

        const wasDirectoryOpen = pane._explorerMode === 'directory';
        const hasDiffTarget = Boolean(openDiff || diffCommit);
        const scrollState = preserveScroll && !hasDiffTarget ? captureExplorerFileScroll(index) : null;
        // Opening another file swaps the shown tab implicitly; keep the
        // outgoing tab's mode + scroll before the loading placeholder renders.
        explorerCaptureActiveTabView(index);
        if (showLoading && !wasDirectoryOpen) {
            renderExplorerMessage(index, 'Opening file...');
        }
        try {
            const response = await fetch(
                `/api/explorer/${encodeURIComponent(sessionId)}/file?path=${encodeURIComponent(path)}`,
                { signal: explorerRequestSignal(pane, 'file') }
            );
            const data = await response.json();
            if (!response.ok) {
                pane._explorerOpenErrorCode = String(data.code || '');
                throw new Error(data.error || 'Failed to open file');
            }
            if (
                preserveScroll
                && !hasDiffTarget
                && pane._explorerMode === 'file'
                && pane._explorerFilePath === (data.path || path)
                && updateExplorerFileInPlace(index, data, scrollState)
            ) {
                return true;
            }
            const rendered = renderExplorerFile(index, data, {
                scrollState,
                openDiff,
                diffCommit,
                diffMode,
                pinned,
                tab,
                restoreTabView
            });
            if (rendered) {
                revealExplorerTreePath(index);
            }
            return rendered;
        } catch (error) {
            if (explorerIsAbortError(error)) {
                // A newer open superseded this one and owns the viewer now;
                // reporting a failure here would paint an error over it.
                return false;
            }
            console.error('[GridVibe Sessions] Explorer file open failed:', error);
            renderExplorerDirectoryOpenError(index, error.message || 'Failed to open file.');
            return false;
        }
    }

    async function refreshExplorerPane(index) {
        // A manual refresh reloads (and would replace) the edited file.
        if (!(await confirmDiscardExplorerEdit(index, 'Refreshing'))) {
            return false;
        }
        const pane = terminals[index];
        const refreshGitSidebar = Boolean(pane?._explorerGitSidebarOpen);
        const refreshTreeSidebar = Boolean(pane?._explorerTreeSidebarOpen);
        if (refreshGitSidebar) {
            invalidateExplorerGitRepo(index);
        }
        let refreshed = false;
        if (pane?._explorerMode === 'file' && pane._explorerFilePath) {
            refreshed = await openExplorerFile(index, pane._explorerFilePath, {
                showLoading: false,
                preserveScroll: true,
                tab: pane._explorerActiveTabId
            });
        } else if (pane?._explorerMode === 'viewer') {
            /* Empty Preview tab: nothing to reload in the viewer body; the tree
               and Git sidebars refresh below. */
            refreshed = true;
        } else {
            refreshed = await loadExplorerPane(index, null, { force: true });
        }
        if (refreshGitSidebar) {
            await loadExplorerGitRepo(index);
        }
        if (refreshTreeSidebar) {
            await reloadExplorerTree(index);
        }
        return refreshed;
    }

    async function syncExplorerPane(index) {
        const pane = terminals[index];
        if (pane?._explorerMode === 'file' && pane._explorerFilePath) {
            return true;
        }
        if (pane?._attached) {
            return true;
        }
        /* First show: the read-only tabbed viewer with an empty Preview tab and
           the Files tree opened for navigation (ISSUE-2026-014). */
        return openExplorerViewer(index);
    }

    async function loadExplorerPane(index, path = null, { force = false, showLoading = true } = {}) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !isExplorerSession(pane._session) || !sessionId) {
            return false;
        }
        // Directory navigation (tree, breadcrumb, open-folder) replaces the
        // viewer, so a dirty in-place edit must be confirmed first.
        if (!(await confirmDiscardExplorerEdit(index, 'Leaving this file'))) {
            return false;
        }

        const isNavigation = path !== null;
        if (pane._attached && !force && !isNavigation) {
            return true;
        }

        const nextPath = path === null ? (pane._explorerPath || null) : path;
        // Navigation swaps the shown tab to Preview (tree click, breadcrumb,
        // open-folder); keep the outgoing tab's mode + scroll before the
        // loading placeholder guts the viewer (2.e).
        explorerCaptureActiveTabView(index);
        if (showLoading) {
            renderExplorerMessage(index, 'Loading directory...');
        }

        try {
            const entriesUrl = `/api/explorer/${encodeURIComponent(sessionId)}/entries`;
            const response = await fetch(
                nextPath === null ? entriesUrl : `${entriesUrl}?path=${encodeURIComponent(nextPath)}`
            );
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to load directory');
            }

            updateExplorerFilesystemRootRevision(index, data.root_revision || '');
            pane._attached = true;
            pane._explorerMode = 'directory';
            pane._explorerFilePath = '';
            setExplorerFileWatchBaseline(pane, '');
            resetExplorerFsWatchBaseline(pane);
            pane._explorerFileContent = '';
            pane._explorerFileLanguage = '';
            // Costs nothing today — the find guard checks the mode first — but
            // the tier describes the buffer, and the buffer is now empty.
            applyExplorerSourceTier(pane, '');
            pane._explorerPreviewHtml = '';
            pane._explorerPreviewLoaded = false;
            pane._explorerGit = null;
            pane._explorerGitContext = data.git || null;
            pane._explorerDiffLoaded = false;
            pane._explorerDiffContent = '';
            teardownExplorerOverview(index);
            pane._explorerPath = data.path || '';
            pane._explorerParentPath = data.parent_path || '';
            pane._explorerEntries = Array.isArray(data.entries) ? data.entries : [];
            pane._explorerDirectoryRevision = String(
                data.revision
                || explorerDirectoryContentIdentity(pane._explorerPath, pane._explorerEntries)
            );
            cancelExplorerSearch(index);
            if (isNavigation) {
                resetExplorerDirectorySearch(pane);
            }
            /* Directory browsing lives in the permanent Preview tab; pinned file
               tabs are untouched by navigation (ISSUE-2026-014). The browsed
               path is recorded on the tab itself (`dirPath`) so swapping to a
               pinned tab and back cannot lose it — the pane-global
               `_explorerPath`/`_explorerMode` fields follow whatever tab was
               rendered last. */
            ensureExplorerTabState(pane);
            pane._explorerActiveTabId = EXPLORER_PREVIEW_TAB_ID;
            pane._explorerRenderedTabId = EXPLORER_PREVIEW_TAB_ID;
            const previewTab = explorerPreviewTab(pane);
            previewTab.path = '';
            previewTab.name = '';
            // Back on a directory listing the tab shows no file, so the Git
            // badge of the file it last showed must go with it.
            previewTab.git = null;
            previewTab.dirPath = pane._explorerPath;
            document.getElementById(`ph-${index}`)?.remove();
            updateExplorerGitSummary(index, data.git || null);
            renderExplorerDirectorySearchControls(index);
            revealExplorerTreePath(index);

            renderExplorerPathBreadcrumb(index, data.path || '', { root: data.root || '' });
            const upButton = document.getElementById(`explorer-up-${index}`);
            const list = document.getElementById(`explorer-list-${index}`);
            if (upButton) {
                upButton.disabled = !data.parent_path && !data.path;
            }
            if (!list) {
                return true;
            }
            list.classList.remove('file-view');
            wireExplorerSearchControls(index);
            applyExplorerSearch(index, { resetActive: true });
            /* Returning to a directory the Preview tab already showed (after a
               pinned tab was active) restores its captured scroll when the
               listing identity still matches (OD-4); a genuinely new directory
               never matches, so navigation always starts at the top. */
            const restoredDirView = explorerMatchingTabView(
                previewTab,
                explorerCurrentContentRevisions(pane)
            );
            if (restoredDirView) {
                restoreExplorerFileScroll(index, restoredDirView.scroll);
            }
            renderExplorerTabStrip(index);
            // A listing is not a diff, so the Git sidebar's highlight goes out
            // with the file the viewer just left.
            paintExplorerGitActiveRows(index);
            if (pane._explorerGitSidebarOpen) {
                loadExplorerGitRepo(index);
            }
            return true;
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer load failed:', error);
            renderExplorerMessage(index, error.message || 'Failed to load directory.');
            return false;
        }
    }
