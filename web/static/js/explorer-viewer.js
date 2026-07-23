    /* ─────────────────────────────────────────────
       Explorer viewer — extracted from terminals.js per
       docs/terminals_js_split_plan_2026-07-23.md (Phase 1, move-only).
       File-type classifier, syntax highlight, Git diff, source/markdown
       render, image/mermaid viewer, tabbed viewer, breadcrumb, per-tab
       view/scroll state and saved-tab persistence.
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

    /* Phase 1 (docs/source_diff_analysis.md): the explorer's own normalized
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
        if (filename.startsWith('.env.')) {
            return 'dotenv';
        }
        return EXPLORER_LANGUAGE_BY_EXTENSION[explorerPathExtension(path)] || '';
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

    function explorerFindRanges(content, query, maxMatches = EXPLORER_SEARCH_MAX_MATCHES) {
        const source = String(content || '');
        const needle = String(query || '');
        if (!source || !needle) {
            return [];
        }

        const ranges = [];
        const normalizedNeedle = needle.toLowerCase();
        const stride = Math.max(EXPLORER_SEARCH_CHUNK_SIZE, normalizedNeedle.length);
        for (let offset = 0; offset < source.length && ranges.length < maxMatches; offset += stride) {
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
        }
        ranges.capped = ranges.length >= maxMatches;
        return ranges;
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

    /* Phase 1 (docs/source_diff_analysis.md §5.2): tokenize the whole document
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

    /* Render one line's worth of Highlight.js runs, reusing the shared
       escape+search-mark helpers so search marks coexist with syntax spans
       (docs/source_diff_analysis.md §5.4). */
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

    function explorerGitStatusLabel(git) {
        if (!git || typeof git !== 'object') {
            return '';
        }
        const status = git.status || 'clean';
        if (status === 'clean' && git.has_descendant_changes) {
            return '*';
        }
        return EXPLORER_GIT_STATUS_LABELS[status] || '';
    }

    function explorerGitStatusTitle(git) {
        if (!git || typeof git !== 'object') {
            return 'Git status unavailable';
        }
        const status = git.status || 'clean';
        if (git.has_descendant_changes && git.descendant_status) {
            return `Directory contains ${git.descendant_status} Git changes`;
        }
        if (status === 'clean' && git.has_descendant_changes) {
            return 'Directory contains Git changes';
        }
        return `Git status: ${status}`;
    }

    function explorerGitBadgeHtml(git) {
        const label = explorerGitStatusLabel(git);
        const status = git?.status || 'clean';
        const className = label ? status : 'clean';
        return `<span class="explorer-git-badge ${escHtml(className)}" title="${escHtml(explorerGitStatusTitle(git))}">${escHtml(label)}</span>`;
    }

    function explorerHasGitDiff(git) {
        if (!git || typeof git !== 'object') {
            return false;
        }
        return ['modified', 'added', 'deleted', 'renamed', 'conflicted'].includes(git.status || '');
    }

    function explorerGitSummaryText(git) {
        if (!git || typeof git !== 'object') {
            return '';
        }
        if (!git.available) {
            return git.error ? 'Git unavailable' : 'No Git repo';
        }
        const parts = [git.branch || (git.head ? git.head.slice(0, 7) : 'Git')];
        if (Number(git.ahead || 0) > 0) {
            parts.push(`↑${git.ahead}`);
        }
        if (Number(git.behind || 0) > 0) {
            parts.push(`↓${git.behind}`);
        }
        if (git.dirty) {
            parts.push('*');
        }
        return parts.join(' ');
    }

    function updateExplorerGitSummary(index, git) {
        const summary = document.getElementById(`explorer-git-${index}`);
        if (!summary) {
            return;
        }
        const text = explorerGitSummaryText(git);
        summary.textContent = text;
        summary.title = git?.error || (git?.repo_root || text);
    }

    function explorerDiffCacheKey(path, commit, mode = '') {
        return `${String(path || '')}\n${String(commit || '')}\n${String(mode || '')}`;
    }

    function explorerDiffSidebarStatusHtml(git) {
        return explorerGitBadgeHtml(git || { status: 'clean' });
    }

    function explorerParentDirectory(path) {
        const cleaned = String(path || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
        const slashIndex = cleaned.lastIndexOf('/');
        return slashIndex > 0 ? cleaned.slice(0, slashIndex) : '';
    }

    function explorerGitOpenFile(index, path, diffMode = 'worktree') {
        if (!path) {
            return;
        }
        // Changed-file rows jump straight to the diff view (ISSUE-2026-023).
        // 'worktree' shows unstaged hunks, 'staged' shows the indexed hunks, so
        // a partially staged file never surfaces the other section's changes.
        const mode = diffMode === 'staged' ? 'staged' : 'worktree';
        openExplorerFile(index, path, { openDiff: true, diffMode: mode });
    }

    async function explorerGitOpenCommitDiff(index, path, commit) {
        if (!path || !commit) {
            return;
        }
        const opened = await openExplorerFile(index, path, {
            showLoading: true,
            openDiff: true,
            diffCommit: commit
        });
        if (!opened) {
            renderExplorerCommitDiffFile(index, path, commit);
        }
    }

    function explorerGitOpenFolder(index, path) {
        loadExplorerPane(index, explorerParentDirectory(path));
    }

    function explorerGitGraphLane(character, position) {
        // `git log --graph` gives each branch a two-character column, and a diagonal
        // always belongs to the column it is reaching towards, not the one it starts in.
        const column = character === '/' || character === '\\' ? (position + 1) / 2 : position / 2;
        return Math.floor(column) % EXPLORER_GIT_GRAPH_LANE_COUNT;
    }

    function explorerGitGraphHtml(graph) {
        const characters = Array.from(typeof graph === 'string' && graph ? graph : '*');
        return characters.map((character, position) => {
            if (character === ' ') {
                return ' ';
            }
            const lane = explorerGitGraphLane(character, position);
            const nodeClass = character === '*' ? ' node' : '';
            return `<span class="explorer-diff-commit-graph-lane${nodeClass}" data-git-lane="${lane}">${escHtml(character)}</span>`;
        }).join('');
    }

    function explorerGitStatusFromCode(code) {
        switch (code) {
            case 'M':
            case 'T':
                return 'modified';
            case 'A':
                return 'added';
            case 'D':
                return 'deleted';
            case 'R':
                return 'renamed';
            case 'C':
                return 'added';
            case 'U':
                return 'conflicted';
            case '?':
                return 'untracked';
            case '!':
                return 'ignored';
            default:
                return 'clean';
        }
    }

    function explorerGitCodeUnmodified(code) {
        // Porcelain v2 uses '.' for an unchanged index/worktree position; clean rows use ' '.
        return !code || code === ' ' || code === '.';
    }

    function splitExplorerGitChanges(changes) {
        const staged = [];
        const unstaged = [];
        (Array.isArray(changes) ? changes : []).forEach(file => {
            const git = file.git || {};
            const indexCode = git.index_status || ' ';
            const worktreeCode = git.worktree_status || ' ';
            if (git.status === 'conflicted') {
                unstaged.push({ ...file, git: { ...git, status: 'conflicted' } });
                return;
            }
            if (git.status === 'untracked' || indexCode === '?') {
                unstaged.push({ ...file, git: { ...git, status: 'untracked' } });
                return;
            }
            if (!explorerGitCodeUnmodified(indexCode)) {
                staged.push({ ...file, git: { ...git, status: explorerGitStatusFromCode(indexCode) } });
            }
            if (!explorerGitCodeUnmodified(worktreeCode)) {
                unstaged.push({ ...file, git: { ...git, status: explorerGitStatusFromCode(worktreeCode) } });
            }
        });
        return { staged, unstaged };
    }

    function explorerGitCanRevert(status) {
        // Tracked changes are restored from the index. A single explicitly
        // selected untracked file can also be discarded after confirmation.
        return ['modified', 'deleted', 'renamed', 'untracked'].includes(status || '');
    }

    function explorerGitCanBulkDiscard(status) {
        // Bulk discard deliberately keeps untracked files. Removing a new file
        // requires the narrower per-row action and its explicit warning.
        return ['modified', 'deleted', 'renamed'].includes(status || '');
    }

    function renderExplorerGitFileRows(index, files, options = {}) {
        const entries = Array.isArray(files) ? files : [];
        if (!entries.length) {
            return `<div class="explorer-diff-sidebar-empty">${escHtml(options.emptyText || 'No files.')}</div>`;
        }
        const commitHash = options.commitHash || '';
        const action = options.action || '';
        // Staged rows diff against HEAD (index hunks); everything else shows the
        // worktree hunks so a partially staged file never leaks the wrong side.
        const diffMode = action === 'unstage' ? 'staged' : 'worktree';
        return entries.map(file => {
            const path = file.path || file.repo_path || '';
            const status = (file.git && file.git.status) || '';
            const pathAction = commitHash
                ? `data-explorer-git-open-commit-diff="${escHtml(path)}" data-explorer-git-commit="${escHtml(commitHash)}"`
                : `data-explorer-git-open-file="${escHtml(path)}" data-explorer-git-diff-mode="${escHtml(diffMode)}"`;
            let actionButton = '';
            if (action === 'stage') {
                actionButton = `<button type="button" class="explorer-search-btn explorer-git-stage-btn" data-explorer-git-stage="${escHtml(path)}" title="Stage changes" aria-label="Stage changes">+</button>`;
            } else if (action === 'unstage') {
                actionButton = `<button type="button" class="explorer-search-btn explorer-git-unstage-btn" data-explorer-git-unstage="${escHtml(path)}" title="Unstage changes" aria-label="Unstage changes">−</button>`;
            }
            const discardLabel = status === 'untracked'
                ? 'Delete untracked file'
                : 'Discard changes (revert)';
            const revertButton = (action === 'stage' && explorerGitCanRevert(status))
                ? `<button type="button" class="explorer-search-btn explorer-git-revert-btn" data-explorer-git-revert="${escHtml(path)}" data-explorer-git-revert-status="${escHtml(status)}" title="${discardLabel}" aria-label="${discardLabel}">${EXPLORER_GIT_REVERT_ICON}</button>`
                : '';
            return `
                <div class="explorer-diff-commit-file" title="${escHtml(path)}" data-explorer-copy-path="${escHtml(path)}">
                    ${explorerDiffSidebarStatusHtml(file.git)}
                    ${explorerFileTypeIconHtml(path)}
                    <button type="button" class="explorer-diff-commit-file-path" ${pathAction}>${escHtml(path || file.name || 'Changed file')}</button>
                    <span class="explorer-diff-commit-file-actions">
                        ${revertButton}
                        ${actionButton}
                        <button type="button" class="explorer-search-btn explorer-open-folder-btn" data-explorer-git-open-folder="${escHtml(path)}" title="Open containing folder" aria-label="Open containing folder">↪</button>
                    </span>
                </div>
            `;
        }).join('');
    }

    /* ─────────────────────────────────────────────
       Explorer copy-path context menu (ISSUE-2026-028)
       Delegated on the tree + Git panels so right-clicking any file row offers
       an in-page (WebView2-safe) Copy path / Copy relative path menu. Copy is a
       read, so this stays inside the read-only explorer contract.
    ───────────────────────────────────────────── */
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

    function dismissExplorerContextMenu() {
        document.getElementById('explorer-ctx-menu')?.remove();
        document.removeEventListener('keydown', _explorerContextMenuKeydown, true);
        document.removeEventListener('mousedown', _explorerContextMenuOutside, true);
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
        const items = Array.from(menu.querySelectorAll('button'));
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
        items.forEach(({ label, action }) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.setAttribute('role', 'menuitem');
            button.textContent = label;
            button.addEventListener('click', () => {
                action();
                dismissExplorerContextMenu();
            });
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
        menu.querySelector('button')?.focus();

        // Defer the outside-dismiss listener so the opening interaction does not
        // immediately close the menu.
        window.setTimeout(() => {
            document.addEventListener('mousedown', _explorerContextMenuOutside, true);
        }, 0);
        document.addEventListener('keydown', _explorerContextMenuKeydown, true);
    }

    function handleExplorerCopyPathMenu(event, index) {
        const row = event.target.closest('[data-explorer-copy-path]');
        if (!row) {
            return;
        }
        event.preventDefault();
        const relativePath = row.dataset.explorerCopyPath || '';
        const absolutePath = explorerJoinRootPath(explorerRootDirectory(index), relativePath);
        const items = [
            { label: 'Copy path', action: () => _copyText(absolutePath || relativePath) },
        ];
        if (relativePath) {
            items.push({ label: 'Copy relative path', action: () => _copyText(relativePath) });
        }
        showExplorerContextMenu(event.clientX, event.clientY, items);
    }

    function wireExplorerCopyPathMenu(panel, index) {
        if (!panel || panel.dataset.copyPathMenuWired === 'true') {
            return;
        }
        panel.dataset.copyPathMenuWired = 'true';
        panel.addEventListener('contextmenu', event => handleExplorerCopyPathMenu(event, index));
    }

    function renderExplorerGitPanel(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        wireExplorerCopyPathMenu(panel, index);
        if (pane._explorerGitRepoLoading) {
            panel.innerHTML = '<div class="explorer-diff-sidebar-empty">Loading repository...</div>';
            return;
        }
        if (pane._explorerGitRepoError && !pane._explorerGitRepo) {
            panel.innerHTML = `<div class="explorer-diff-sidebar-error">${escHtml(pane._explorerGitRepoError)}</div>`;
            return;
        }

        const repo = pane._explorerGitRepo || {};
        const git = repo.git || {};
        const errorBanner = pane._explorerGitRepoError
            ? `<div class="explorer-diff-sidebar-error">${escHtml(pane._explorerGitRepoError)}</div>`
            : '';
        const changes = Array.isArray(repo.changes) ? repo.changes : [];
        const { staged, unstaged } = splitExplorerGitChanges(changes);
        /* Discard All remains tracked-only even though a confirmed per-row
           action may now remove one explicitly selected untracked file. */
        const discardable = unstaged.filter(file => explorerGitCanBulkDiscard(file.git && file.git.status));
        const commits = Array.isArray(repo.commits) ? repo.commits : [];
        const expandedCommits = ensureExplorerDiffExpandedCommits(pane);
        const busy = Boolean(pane._explorerGitActionBusy);
        const commitMessage = typeof pane._explorerGitCommitMessage === 'string' ? pane._explorerGitCommitMessage : '';
        const hasUpstream = git.ahead !== null && git.ahead !== undefined;
        const publishLabel = hasUpstream ? 'Push' : 'Publish branch';
        const branchText = explorerGitSummaryText(git) || 'Git';
        const commitRows = commits.length
            ? commits.map(commit => {
                const hash = commit.hash || '';
                const expanded = hash && expandedCommits.has(`explorer:${hash}`);
                return `
                    <button type="button" class="explorer-diff-commit" data-explorer-git-commit-toggle="${escHtml(hash)}" ${hash ? '' : 'disabled'} title="${escHtml(commit.line || '')}" aria-expanded="${expanded ? 'true' : 'false'}">
                        <span class="explorer-diff-commit-graph">${explorerGitGraphHtml(commit.graph)}</span>
                        <span class="explorer-diff-commit-toggle" aria-hidden="true">${expanded ? '▾' : '▸'}</span>
                        <span class="explorer-diff-commit-subject"><span class="explorer-diff-commit-hash">${escHtml(hash ? hash.slice(0, 7) : '')}</span> ${escHtml(commit.subject || commit.line || '')}</span>
                    </button>
                    ${expanded ? `<div class="explorer-diff-commit-files">${renderExplorerGitFileRows(index, commit.files, { emptyText: 'No files recorded for this commit.', commitHash: hash })}</div>` : ''}
                `;
            }).join('')
            : '<div class="explorer-diff-sidebar-empty">No commits in this scope.</div>';

        panel.innerHTML = `
            ${errorBanner}
            <div class="explorer-diff-sidebar-section explorer-git-repo-bar">
                <span class="explorer-git-repo-branch" title="${escHtml(git.repo_root || branchText)}">${escHtml(branchText)}</span>
                <button type="button" class="explorer-git-publish-btn" data-explorer-git-publish ${busy ? 'disabled' : ''} title="Push the current branch to its remote">${escHtml(publishLabel)}</button>
            </div>
            <div class="explorer-diff-sidebar-section">
                <div class="explorer-diff-sidebar-title">Staged Changes</div>
                <div class="explorer-diff-commit-files">
                    ${renderExplorerGitFileRows(index, staged, { emptyText: 'No staged changes.', action: 'unstage' })}
                </div>
                <div class="explorer-git-commit-box">
                    <textarea class="explorer-git-commit-message" id="explorer-git-commit-message-${index}" rows="2" placeholder="Message (commits staged changes)" ${busy ? 'disabled' : ''}>${escHtml(commitMessage)}</textarea>
                    <button type="button" class="explorer-git-commit-btn" data-explorer-git-commit ${(busy || !staged.length) ? 'disabled' : ''} title="Commit staged changes">Commit</button>
                </div>
            </div>
            <div class="explorer-diff-sidebar-section">
                <div class="explorer-diff-sidebar-title explorer-git-section-title">
                    <span>Changes</span>
                    <span class="explorer-git-section-actions">
                        <button type="button" class="explorer-search-btn explorer-git-revert-btn explorer-git-discard-all-btn" data-explorer-git-discard-all ${(busy || !discardable.length) ? 'disabled' : ''} title="Discard all changes" aria-label="Discard all changes">${EXPLORER_GIT_REVERT_ICON}</button>
                        <button type="button" class="explorer-search-btn explorer-git-stage-btn explorer-git-stage-all-btn" data-explorer-git-stage-all ${(busy || !unstaged.length) ? 'disabled' : ''} title="Stage all changes" aria-label="Stage all changes">+</button>
                    </span>
                </div>
                <div class="explorer-diff-commit-files">
                    ${renderExplorerGitFileRows(index, unstaged, { emptyText: 'No unstaged changes.', action: 'stage' })}
                </div>
            </div>
            <div class="explorer-diff-sidebar-section">
                <div class="explorer-diff-sidebar-title">Graph</div>
                ${commitRows}
            </div>
        `;
        const commitMessageInput = panel.querySelector(`#explorer-git-commit-message-${index}`);
        if (commitMessageInput) {
            commitMessageInput.addEventListener('input', () => {
                pane._explorerGitCommitMessage = commitMessageInput.value;
            });
        }
        panel.querySelectorAll('[data-explorer-git-stage]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                explorerGitStageFile(index, button.dataset.explorerGitStage || '');
            });
        });
        panel.querySelectorAll('[data-explorer-git-unstage]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                explorerGitUnstageFile(index, button.dataset.explorerGitUnstage || '');
            });
        });
        panel.querySelectorAll('[data-explorer-git-revert]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                explorerGitRevertFile(
                    index,
                    button.dataset.explorerGitRevert || '',
                    button.dataset.explorerGitRevertStatus || '',
                );
            });
        });
        const stageAllButton = panel.querySelector('[data-explorer-git-stage-all]');
        if (stageAllButton) {
            stageAllButton.addEventListener('click', () => explorerGitStageAll(index));
        }
        const discardAllButton = panel.querySelector('[data-explorer-git-discard-all]');
        if (discardAllButton) {
            discardAllButton.addEventListener('click', () => explorerGitDiscardAll(index));
        }
        const publishButton = panel.querySelector('[data-explorer-git-publish]');
        if (publishButton) {
            publishButton.addEventListener('click', () => explorerGitPublish(index));
        }
        const commitButton = panel.querySelector('[data-explorer-git-commit]');
        if (commitButton) {
            commitButton.addEventListener('click', () => explorerGitCommit(index));
        }
        panel.querySelectorAll('[data-explorer-git-open-file]').forEach(button => {
            button.addEventListener('click', () => {
                explorerGitOpenFile(
                    index,
                    button.dataset.explorerGitOpenFile || '',
                    button.dataset.explorerGitDiffMode || 'worktree',
                );
            });
        });
        panel.querySelectorAll('[data-explorer-git-open-commit-diff]').forEach(button => {
            button.addEventListener('click', () => {
                explorerGitOpenCommitDiff(
                    index,
                    button.dataset.explorerGitOpenCommitDiff || '',
                    button.dataset.explorerGitCommit || '',
                );
            });
        });
        panel.querySelectorAll('[data-explorer-git-open-folder]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                explorerGitOpenFolder(index, button.dataset.explorerGitOpenFolder || '');
            });
        });
        panel.querySelectorAll('[data-explorer-git-commit-toggle]').forEach(button => {
            button.addEventListener('click', () => {
                const commit = button.dataset.explorerGitCommitToggle || '';
                if (!commit) {
                    return;
                }
                const expanded = ensureExplorerDiffExpandedCommits(pane);
                const key = `explorer:${commit}`;
                if (expanded.has(key)) {
                    expanded.delete(key);
                } else {
                    expanded.add(key);
                }
                renderExplorerGitPanel(index);
            });
        });
    }

    function renderExplorerGitPanels(index) {
        renderExplorerGitPanel(index);
    }

    function invalidateExplorerGitRepo(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        pane._explorerGitRepoLoaded = false;
        pane._explorerGitRepoLoading = false;
        pane._explorerGitRepoError = '';
        pane._explorerGitRepo = null;
        renderExplorerGitPanels(index);
    }

    function syncExplorerSidebar(index) {
        const pane = terminals[index];
        const main = document.getElementById(`explorer-main-${index}`);
        const sidebar = document.getElementById(`explorer-sidebar-${index}`);
        if (!pane || !main || !sidebar) {
            return;
        }

        const treeOpen = Boolean(pane._explorerTreeSidebarOpen);
        const gitOpen = Boolean(pane._explorerGitSidebarOpen);
        const anyOpen = treeOpen || gitOpen;
        const treePanel = document.getElementById(`explorer-tree-panel-${index}`);
        const gitPanel = document.getElementById(`explorer-git-panel-${index}`);
        const splitter = document.getElementById(`explorer-sidebar-splitter-${index}`);
        const handle = document.getElementById(`explorer-sidebar-resizer-${index}`);
        const treeButton = document.getElementById(`explorer-tree-toggle-${index}`);
        const gitButton = document.getElementById(`explorer-git-toggle-${index}`);

        main.classList.toggle('tree-open', treeOpen);
        main.classList.toggle('git-open', gitOpen);
        sidebar.hidden = !anyOpen;
        sidebar.classList.toggle('split', treeOpen && gitOpen);
        if (treePanel) {
            treePanel.hidden = !treeOpen;
        }
        if (gitPanel) {
            gitPanel.hidden = !gitOpen;
        }
        if (splitter) {
            splitter.hidden = !(treeOpen && gitOpen);
        }
        if (handle) {
            handle.hidden = !anyOpen;
        }
        if (treeButton) {
            treeButton.setAttribute('aria-pressed', treeOpen ? 'true' : 'false');
        }
        if (gitButton) {
            gitButton.setAttribute('aria-pressed', gitOpen ? 'true' : 'false');
        }

        if (anyOpen) {
            applyExplorerSidebarWidth(index);
            wireExplorerSidebarResize(index);
        }
        if (treeOpen && gitOpen) {
            applyExplorerSidebarSplit(index);
            wireExplorerSidebarSplitter(index);
        }
    }

    function restoreExplorerSidebarState(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        syncExplorerSidebar(index);
        if (pane._explorerTreeSidebarOpen) {
            loadExplorerTree(index);
        }
        if (pane._explorerGitSidebarOpen) {
            loadExplorerGitRepo(index);
        }
    }

    function setExplorerGitSidebarOpen(index, open) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        pane._explorerGitSidebarOpen = Boolean(open);
        syncExplorerSidebar(index);
        if (pane._explorerGitSidebarOpen) {
            loadExplorerGitRepo(index);
        }
    }

    function toggleExplorerGitSidebar(index) {
        const pane = terminals[index];
        setExplorerGitSidebarOpen(index, !pane?._explorerGitSidebarOpen);
    }

    function setExplorerTreeSidebarOpen(index, open) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        pane._explorerTreeSidebarOpen = Boolean(open);
        syncExplorerSidebar(index);
        if (pane._explorerTreeSidebarOpen) {
            loadExplorerTree(index);
        }
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
                window.removeEventListener('pointermove', onMove);
                window.removeEventListener('pointerup', onEnd);
                window.removeEventListener('pointercancel', onEnd);
            };
            window.addEventListener('pointermove', onMove);
            window.addEventListener('pointerup', onEnd, { once: true });
            window.addEventListener('pointercancel', onEnd, { once: true });
        });
    }

    /* Unset tree height means the two stacked panels share the sidebar evenly. */
    function applyExplorerSidebarSplit(index) {
        const pane = terminals[index];
        const sidebar = document.getElementById(`explorer-sidebar-${index}`);
        if (!pane || !sidebar) {
            return;
        }
        const stored = Number(pane._explorerSidebarTreeHeight || 0);
        if (!stored) {
            sidebar.style.removeProperty('--explorer-sidebar-tree-height');
            return;
        }
        const height = sidebar.getBoundingClientRect().height;
        const maxTop = Math.max(EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT, height - EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT - 6);
        const top = Math.max(EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT, Math.min(stored, maxTop));
        pane._explorerSidebarTreeHeight = top;
        sidebar.style.setProperty('--explorer-sidebar-tree-height', `${top}px`);
    }

    function wireExplorerSidebarSplitter(index) {
        const pane = terminals[index];
        const sidebar = document.getElementById(`explorer-sidebar-${index}`);
        const splitter = document.getElementById(`explorer-sidebar-splitter-${index}`);
        if (!pane || !sidebar || !splitter || splitter.dataset.bound) {
            return;
        }

        splitter.dataset.bound = 'true';
        splitter.addEventListener('pointerdown', event => {
            event.preventDefault();
            splitter.classList.add('dragging');
            splitter.setPointerCapture?.(event.pointerId);
            const onMove = moveEvent => {
                const rect = sidebar.getBoundingClientRect();
                const maxTop = Math.max(
                    EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT,
                    rect.height - EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT - 6
                );
                const nextTop = Math.max(
                    EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT,
                    Math.min(Math.round(moveEvent.clientY - rect.top), maxTop)
                );
                pane._explorerSidebarTreeHeight = nextTop;
                sidebar.style.setProperty('--explorer-sidebar-tree-height', `${nextTop}px`);
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

    async function loadExplorerTreeChildren(index, path) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId) {
            return [];
        }

        ensureExplorerTreeState(pane);
        const key = String(path || '');
        if (pane._explorerTreeChildren.has(key)) {
            return pane._explorerTreeChildren.get(key);
        }
        if (pane._explorerTreeLoading.has(key)) {
            return [];
        }

        pane._explorerTreeLoading.add(key);
        pane._explorerTreeErrors.delete(key);
        renderExplorerTreePanel(index);
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

    function explorerTreeRowHtml(pane, entry, depth) {
        const isDirectory = entry.type === 'directory';
        const path = entry.path || '';
        const expanded = isDirectory && pane._explorerTreeExpanded.has(path);
        const active = explorerTreeRowIsActive(pane, entry);
        const action = isDirectory
            ? `data-explorer-tree-dir="${escHtml(path)}" aria-expanded="${expanded ? 'true' : 'false'}"`
            : `data-explorer-tree-file="${escHtml(path)}"`;
        const chevron = isDirectory ? (expanded ? '▾' : '▸') : '';
        const badge = explorerGitStatusLabel(entry.git) ? explorerGitBadgeHtml(entry.git) : '';
        const openFolder = isDirectory
            ? `<button type="button" class="explorer-search-btn explorer-open-folder-btn" data-explorer-tree-open-folder="${escHtml(path)}" title="Open folder in the explorer list" aria-label="Open folder in the explorer list">↪</button>`
            : '';
        const openTab = isDirectory
            ? ''
            : `<button type="button" class="explorer-search-btn explorer-open-tab-btn" data-explorer-tree-open-tab="${escHtml(path)}" title="Open in a new tab" aria-label="Open ${escHtml(entry.name || path)} in a new tab">↗</button>`;

        return `
            <div class="explorer-tree-row${active ? ' active' : ''}" data-explorer-copy-path="${escHtml(path)}">
                <button type="button" class="explorer-tree-main" ${action} style="padding-left:${7 + depth * EXPLORER_TREE_INDENT_PX}px" title="${escHtml(path)}">
                    <span class="explorer-tree-chevron" aria-hidden="true">${chevron}</span>
                    ${isDirectory ? EXPLORER_FOLDER_ICON : explorerFileTypeIconHtml(entry.name || path)}
                    <span class="explorer-tree-name">${escHtml(entry.name || path)}</span>
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
        if (pane._explorerTreeLoading.has(path)) {
            return `<div class="explorer-tree-loading" ${indent}>Loading...</div>`;
        }

        const entries = pane._explorerTreeChildren.get(path);
        if (!entries) {
            return '';
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
        panel.innerHTML = `
            <div class="explorer-tree-section">
                <div class="explorer-tree-title">Files</div>
                <div class="explorer-tree-children">${renderExplorerTreeNodes(pane, '', 0)}</div>
            </div>
        `;
        panel.querySelectorAll('[data-explorer-tree-dir]').forEach(button => {
            button.addEventListener('click', () => {
                toggleExplorerTreeDirectory(index, button.dataset.explorerTreeDir || '');
            });
        });
        panel.querySelectorAll('[data-explorer-tree-file]').forEach(button => {
            button.addEventListener('click', () => {
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
                openExplorerFile(index, button.dataset.explorerTreeOpenTab || '', { pinned: true });
            });
        });
    }

    async function toggleExplorerTreeDirectory(index, path) {
        const pane = terminals[index];
        if (!pane || !path) {
            return;
        }

        ensureExplorerTreeState(pane);
        /* 2.d: a directory click keeps its expand/collapse toggle AND browses
           the directory in the Preview tab so the user can drill in from the
           main pane. Clicking the directory the Preview tab already shows
           only toggles its expansion. */
        const alreadyShown = pane._explorerMode === 'directory'
            && (pane._explorerPath || '') === path;
        if (pane._explorerTreeExpanded.has(path)) {
            pane._explorerTreeExpanded.delete(path);
            renderExplorerTreePanel(index);
            if (!alreadyShown) {
                await loadExplorerPane(index, path);
            }
            return;
        }

        pane._explorerTreeExpanded.add(path);
        pane._explorerTreeErrors.delete(path);
        renderExplorerTreePanel(index);
        const childrenLoading = loadExplorerTreeChildren(index, path);
        if (!alreadyShown) {
            await loadExplorerPane(index, path);
        }
        await childrenLoading;
    }

    /* Expand every ancestor of the pane's current directory or open file. */
    async function revealExplorerTreePath(index) {
        const pane = terminals[index];
        if (!pane?._explorerTreeSidebarOpen) {
            return;
        }

        ensureExplorerTreeState(pane);
        const target = pane._explorerMode === 'file'
            ? (pane._explorerFilePath || '')
            : (pane._explorerPath || '');
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
        renderExplorerTreePanel(index);
    }

    function ensureExplorerDiffExpandedCommits(pane) {
        if (!(pane?._explorerDiffExpandedCommits instanceof Set)) {
            pane._explorerDiffExpandedCommits = new Set();
        }
        return pane._explorerDiffExpandedCommits;
    }

    async function loadExplorerGitRepo(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId || pane._explorerGitRepoLoaded || pane._explorerGitRepoLoading) {
            renderExplorerGitPanels(index);
            return;
        }

        pane._explorerGitRepoLoading = true;
        pane._explorerGitRepoError = '';
        renderExplorerGitPanels(index);
        try {
            const response = await fetch(`/api/explorer/${encodeURIComponent(sessionId)}/git/repo`);
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to load Git repository');
            }
            pane._explorerGitRepoLoaded = true;
            pane._explorerGitRepo = data;
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer Git repository failed:', error);
            pane._explorerGitRepoError = error.message || 'Failed to load Git repository.';
        } finally {
            pane._explorerGitRepoLoading = false;
            renderExplorerGitPanels(index);
        }
    }

    async function performExplorerGitAction(index, endpoint, body) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId || pane._explorerGitActionBusy) {
            return false;
        }
        pane._explorerGitActionBusy = true;
        pane._explorerGitRepoError = '';
        renderExplorerGitPanels(index);
        let succeeded = false;
        try {
            const response = await fetch(`/api/explorer/${encodeURIComponent(sessionId)}/git/${endpoint}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body || {}),
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Git action failed');
            }
            pane._explorerGitRepo = data;
            pane._explorerGitRepoLoaded = true;
            succeeded = true;
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer Git action failed:', error);
            pane._explorerGitRepoError = error.message || 'Git action failed.';
        } finally {
            pane._explorerGitActionBusy = false;
            renderExplorerGitPanels(index);
        }
        if (succeeded && pane._explorerMode === 'directory') {
            loadExplorerPane(index, null, { force: true, showLoading: false });
        }
        if (succeeded && EXPLORER_GIT_WORKTREE_ENDPOINTS.has(endpoint)) {
            await refreshExplorerAfterGitAction(index, body && body.path ? String(body.path) : '');
        }
        return succeeded;
    }

    /* Git actions that change working-tree or index state — publish only talks
       to the remote, so it never needs the ISSUE-2026-034 refresh below. */
    const EXPLORER_GIT_WORKTREE_ENDPOINTS = new Set([
        'stage', 'unstage', 'revert', 'commit', 'stage-all', 'discard-all',
    ]);

    /* ISSUE-2026-034: after a mutating Git action the Files tree and the open
       file/diff must not go stale. The tree reload guards internally on
       pane._explorerTreeSidebarOpen; the open file re-fetches in place (which
       drops the cached diff and re-pulls it when a diff view is showing).
       Single-path actions only refresh the file they touched — bulk actions
       (commit / stage-all / discard-all) can affect any open path. */
    async function refreshExplorerAfterGitAction(index, actionPath) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        reloadExplorerTree(index);
        if (pane._explorerMode !== 'file' || !pane._explorerFilePath) {
            return;
        }
        if (actionPath && actionPath !== pane._explorerFilePath) {
            return;
        }
        pane._explorerDiffLoaded = false;
        pane._explorerDiffCacheKey = '';
        await openExplorerFile(index, pane._explorerFilePath, {
            showLoading: false,
            preserveScroll: true,
            tab: pane._explorerActiveTabId
        });
    }

    function explorerGitStageFile(index, path) {
        if (!path) {
            return;
        }
        performExplorerGitAction(index, 'stage', { path });
    }

    function explorerGitStageAll(index) {
        performExplorerGitAction(index, 'stage-all', {});
    }

    /* Bulk form of the per-row Revert (OD-1): worktree restore of tracked
       files only — staged content is preserved and untracked files are never
       deleted (no git clean). Irreversible, so it goes through the in-page
       confirm shell. */
    async function explorerGitDiscardAll(index) {
        const confirmed = await openGenericConfirmModal({
            title: 'Discard all changes?',
            copy: 'Discard the unstaged changes in every tracked file?',
            note: 'Unstaged edits will be lost. Staged versions and untracked files are kept.',
            confirmLabel: 'Discard all',
            danger: true,
        });
        if (!confirmed) {
            return;
        }
        performExplorerGitAction(index, 'discard-all', {});
    }

    function explorerGitUnstageFile(index, path) {
        if (!path) {
            return;
        }
        performExplorerGitAction(index, 'unstage', { path });
    }

    /* Discarding working-tree edits is irreversible, so it goes through the
       in-page confirm shell (WebView2 blocks window.confirm) before the
       narrow discard route runs; a reverted open file reloads in place. */
    async function explorerGitRevertFile(index, path, status = '') {
        if (!path) {
            return;
        }
        const untracked = status === 'untracked';
        const confirmed = await openGenericConfirmModal({
            title: untracked ? 'Delete untracked file?' : 'Discard changes?',
            copy: untracked
                ? `Permanently delete the untracked file "${path}"?`
                : `Discard the unstaged changes in "${path}"?`,
            note: untracked
                ? 'This new file is not tracked by Git and cannot be restored after deletion.'
                : 'This unstaged edit will be lost. Any staged version of the file is kept.',
            confirmLabel: untracked ? 'Delete file' : 'Discard changes',
            danger: true,
        });
        if (!confirmed) {
            return;
        }
        /* A reverted open file reloads in place via the shared post-action
           refresh (ISSUE-2026-034). */
        performExplorerGitAction(index, 'revert', { path });
    }

    async function explorerGitCommit(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const message = String(pane._explorerGitCommitMessage || '').trim();
        if (!message) {
            pane._explorerGitRepoError = 'Commit message is required.';
            renderExplorerGitPanels(index);
            const input = document.getElementById(`explorer-git-commit-message-${index}`);
            if (input) {
                input.focus();
            }
            return;
        }
        const committed = await performExplorerGitAction(index, 'commit', { message });
        if (committed) {
            pane._explorerGitCommitMessage = '';
            renderExplorerGitPanels(index);
        }
    }

    /* Publishing is outward-facing, so it confirms through the in-page shell
       (WebView2 blocks window.confirm — Regression Guardrail 4). */
    async function explorerGitPublish(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const git = (pane._explorerGitRepo && pane._explorerGitRepo.git) || {};
        const branch = git.branch || 'this branch';
        const confirmed = await openGenericConfirmModal({
            title: 'Publish branch?',
            copy: `Publish ${branch} to its remote?`,
            confirmLabel: 'Publish',
        });
        if (!confirmed) {
            return;
        }
        performExplorerGitAction(index, 'publish', {});
    }

    /* Phase 2 (docs/source_diff_analysis.md §5.3): Diff2HtmlUI configuration.
       `matching: 'words'` + `diffStyle: 'char'` give character-level intraline
       emphasis and LCS-based line matching instead of the fallback renderer's
       FIFO pairing; the comparison limits are explicit so pathological diffs
       stay responsive (Diff2Html documents line matching as the main cost). */
    function explorerDiff2HtmlConfig() {
        return {
            outputFormat: 'side-by-side',
            drawFileList: false,
            fileContentToggle: false,
            matching: 'words',
            diffStyle: 'char',
            highlight: true,
            synchronisedScroll: false,
            matchingMaxComparisons: 1500,
            maxLineLengthHighlight: 2000
        };
    }

    function explorerDiffTruncationBannerHtml(pane) {
        if (!pane || !pane._explorerDiffTruncated) {
            return '';
        }
        return '<div class="explorer-diff-truncated" role="status">'
            + 'Diff truncated to 256 KiB / 4,000 lines — the change shown is incomplete.'
            + '</div>';
    }

    function synchroniseExplorerDiffScrollbars(host) {
        const sides = host?._explorerDiffSides || [];
        const spacers = host?._explorerDiffScrollSpacers || [];
        sides.forEach((side, sideIndex) => {
            const spacer = spacers[sideIndex];
            if (spacer) {
                spacer.style.width = `${Math.max(side.clientWidth, side.scrollWidth)}px`;
            }
        });
    }

    function scheduleExplorerDiffScrollbarSync(host) {
        if (!host || host._explorerDiffScrollbarFrame) {
            return;
        }
        const sync = () => {
            host._explorerDiffScrollbarFrame = null;
            if (host.isConnected) {
                synchroniseExplorerDiffScrollbars(host);
            }
        };
        if (typeof window.requestAnimationFrame === 'function') {
            host._explorerDiffScrollbarFrame = window.requestAnimationFrame(sync);
        } else {
            sync();
        }
    }

    function observeExplorerDiffLayout(host) {
        const filesDiff = host?.querySelector('.d2h-files-diff');
        const sides = filesDiff
            ? [...filesDiff.querySelectorAll(':scope > .d2h-file-side-diff')]
            : [];
        if (sides.length !== 2) {
            return;
        }

        const scrollbars = document.createElement('div');
        scrollbars.className = 'explorer-diff-horizontal-scrollbars';
        scrollbars.setAttribute('aria-hidden', 'true');
        const tracks = sides.map((side, sideIndex) => {
            const track = document.createElement('div');
            track.className = 'explorer-diff-horizontal-scroll';
            track.dataset.explorerDiffSide = sideIndex === 0 ? 'left' : 'right';
            const spacer = document.createElement('div');
            spacer.className = 'explorer-diff-horizontal-scroll-spacer';
            track.appendChild(spacer);
            track.addEventListener('scroll', () => {
                side.scrollLeft = track.scrollLeft;
            });
            scrollbars.appendChild(track);
            return { track, spacer };
        });
        host.appendChild(scrollbars);
        host._explorerDiffSides = sides;
        host._explorerDiffScrollTracks = tracks.map(item => item.track);
        host._explorerDiffScrollSpacers = tracks.map(item => item.spacer);
        scheduleExplorerDiffScrollbarSync(host);

        sides.forEach((side, sideIndex) => {
            side.addEventListener('wheel', event => {
                const horizontalDelta = event.deltaX || (event.shiftKey ? event.deltaY : 0);
                if (!horizontalDelta) {
                    return;
                }
                event.preventDefault();
                tracks[sideIndex].track.scrollLeft += horizontalDelta;
            }, { passive: false });
        });

        if (typeof window.ResizeObserver === 'function') {
            const observer = new window.ResizeObserver(entries => {
                if (entries.length) {
                    scheduleExplorerDiffScrollbarSync(host);
                }
            });
            observer.observe(filesDiff);
            host._explorerDiffResizeObserver = observer;
        }
    }

    function disconnectExplorerDiffLayout(host) {
        host?._explorerDiffResizeObserver?.disconnect();
        if (host?._explorerDiffScrollbarFrame && typeof window.cancelAnimationFrame === 'function') {
            window.cancelAnimationFrame(host._explorerDiffScrollbarFrame);
        }
    }

    /* Render the patch with the pinned Diff2Html build, reusing the pinned
       Highlight.js instance for syntax colour. Returns false — so the caller
       falls back to the tolerant handwritten side-by-side renderer — when the
       assets are missing, Diff2Html throws, or it parses the patch to nothing
       (e.g. a partial patch without a file header). */
    function renderExplorerDiffWithDiff2Html(index, code, diff, banner) {
        if (typeof window === 'undefined' || !window.Diff2HtmlUI || !window.hljs) {
            return false;
        }
        try {
            const host = document.createElement('div');
            host.className = 'explorer-diff2html';
            const ui = new window.Diff2HtmlUI(host, diff, explorerDiff2HtmlConfig(), window.hljs);
            ui.draw();
            ui.highlightCode();
            if (!host.querySelector('.d2h-diff-table, .d2h-code-line, .d2h-file-wrapper')) {
                return false;
            }
            code.innerHTML = banner;
            code.appendChild(host);
            observeExplorerDiffLayout(host);
            return true;
        } catch (error) {
            console.error('[GridVibe Sessions] Diff2Html render failed:', error);
            return false;
        }
    }

    function renderExplorerDiff(index) {
        const pane = terminals[index];
        const code = document.getElementById(`explorer-diff-code-${index}`);
        if (!pane || !code) {
            return;
        }
        disconnectExplorerDiffLayout(code.querySelector('.explorer-diff2html'));
        const diff = pane._explorerDiffContent || '';
        if (!diff) {
            code.innerHTML = '<span class="explorer-diff-empty">No Git diff for selected file.</span>';
            return;
        }
        const banner = explorerDiffTruncationBannerHtml(pane);
        if (!renderExplorerDiffWithDiff2Html(index, code, diff, banner)) {
            code.innerHTML = banner + renderExplorerSideBySideDiff(index, diff);
        }
    }

    function explorerDiffLanguage(index) {
        const pane = terminals[index];
        const filePath = pane?._explorerFilePath || '';
        return normalizeExplorerLanguage(pane?._explorerFileLanguage || '') || explorerCodeLanguage(filePath);
    }

    function explorerDiffLineCodeHtml(index, text) {
        return highlightExplorerCode(String(text || ''), explorerDiffLanguage(index)) || '&nbsp;';
    }

    function explorerDiffCellHtml(index, cell, side) {
        if (!cell) {
            return `
                <div class="explorer-diff-cell empty ${side}">
                    <span class="explorer-diff-line-number"></span>
                    <span class="explorer-diff-line-code"></span>
                </div>
            `;
        }
        return `
            <div class="explorer-diff-cell ${escHtml(cell.type || 'context')} ${side}">
                <span class="explorer-diff-line-number">${cell.number ? escHtml(String(cell.number)) : ''}</span>
                <span class="explorer-diff-line-code">${explorerDiffLineCodeHtml(index, cell.text || '')}</span>
            </div>
        `;
    }

    function explorerDiffRowHtml(index, left, right) {
        if (left?.type === 'hunk') {
            return `
                <div class="explorer-diff-row">
                    <div class="explorer-diff-cell hunk">${escHtml(left.text || '')}</div>
                </div>
            `;
        }
        return `
            <div class="explorer-diff-row">
                ${explorerDiffCellHtml(index, left, 'old')}
                ${explorerDiffCellHtml(index, right, 'new')}
            </div>
        `;
    }

    function renderExplorerSideBySideDiff(index, diff) {
        const source = String(diff || '');
        if (!source.trim()) {
            return '<span class="explorer-diff-empty">No Git diff for selected file.</span>';
        }

        const lines = source.split(/\r?\n/);
        const rows = [];
        let oldLine = 0;
        let newLine = 0;
        const pendingDeletes = [];

        const flushDeletes = () => {
            while (pendingDeletes.length) {
                rows.push(explorerDiffRowHtml(index, pendingDeletes.shift(), null));
            }
        };

        lines.forEach(line => {
            const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
            if (hunk) {
                flushDeletes();
                oldLine = Number(hunk[1]);
                newLine = Number(hunk[2]);
                rows.push(explorerDiffRowHtml(index, { type: 'hunk', text: line }, null));
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
                rows.push(explorerDiffRowHtml(index, pendingDeletes.shift() || null, right));
                return;
            }
            if (line.startsWith(' ')) {
                flushDeletes();
                rows.push(explorerDiffRowHtml(index,
                    { type: 'context', number: oldLine, text: line.slice(1) },
                    { type: 'context', number: newLine, text: line.slice(1) }
                ));
                oldLine += 1;
                newLine += 1;
            }
        });

        flushDeletes();
        return `<div class="explorer-side-by-side-diff">${rows.join('')}</div>`;
    }

    function setExplorerDiffSplit(index, open) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        setExplorerFileView(index, open ? 'diff' : (pane._explorerLastFileView || 'source'));
    }

    function toggleExplorerDiffSplit(index) {
        const pane = terminals[index];
        setExplorerDiffSplit(index, !pane?._explorerDiffSplit);
    }

    async function loadExplorerDiff(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const code = document.getElementById(`explorer-diff-code-${index}`);
        const diffPath = pane?._explorerFilePath || '';
        const commit = pane?._explorerDiffCommit || '';
        // Changed-file rows request a section-specific diff (worktree vs staged)
        // so a partially staged file never shows the other section's hunks;
        // commit-history rows and legacy callers fall back to the HEAD diff.
        const diffMode = commit ? 'commit' : (pane?._explorerDiffMode || 'head');
        const cacheKey = explorerDiffCacheKey(diffPath, commit, diffMode);
        if (!pane || !sessionId || !diffPath || !code) {
            renderExplorerDiff(index);
            return;
        }
        if (pane._explorerDiffLoaded && pane._explorerDiffCacheKey === cacheKey) {
            renderExplorerDiff(index);
            applyExplorerPendingDiffScroll(index);
            return;
        }

        code.textContent = 'Loading diff...';
        try {
            const params = new URLSearchParams({
                path: diffPath,
                mode: diffMode
            });
            if (commit) {
                params.set('commit', commit);
            }
            const response = await fetch(
                `/api/explorer/${encodeURIComponent(sessionId)}/git/diff?${params.toString()}`
            );
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to load Git diff');
            }
            pane._explorerDiffLoaded = true;
            pane._explorerDiffCacheKey = cacheKey;
            pane._explorerDiffContent = data.diff || '';
            // Phase 2 (docs/source_diff_analysis.md §5.3): the backend already
            // bounds diffs to 256 KiB / 4,000 lines and reports truncation; keep
            // the flag so the rendered patch is never mistaken for the whole change.
            pane._explorerDiffTruncated = Boolean(data.truncated);
            renderExplorerDiff(index);
            applyExplorerPendingDiffScroll(index);
            if (activeExplorerFileView(index) === 'diff') {
                applyExplorerSearch(index);
            }
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer Git diff failed:', error);
            code.innerHTML = `<span class="explorer-diff-empty">${escHtml(error.message || 'Failed to load Git diff.')}</span>`;
        }
    }

    function setExplorerFileView(index, mode) {
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
        const selectedMode = normalizedMode === 'diff' && diffPanel ? 'diff' : normalizedMode;
        const isDiffMode = selectedMode === 'diff';
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
        if (isDiffMode) {
            loadExplorerDiff(index);
            const state = pane ? ensureExplorerSearchState(pane, 'file') : null;
            if (state?.query) {
                applyExplorerSearch(index);
            }
        } else {
            applyExplorerSearch(index);
        }
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
            pane[key] = { query: '', activeIndex: 0, matchCount: 0, matchCapped: false, ranges: [], resultQuery: '' };
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

    // ── Markdown preview appearance (ISSUE-2026-030) ─────────────────────────
    // Two orthogonal axes: a reading-surface preset and a font family. Both are
    // bounded allowlists persisted in localStorage and applied idempotently to
    // every open preview via preset classes + CSS custom properties (defined
    // from tokens in terminals.css), so no palette literals live in JS.
    const EXPLORER_MD_PRESETS = ['default', 'paper', 'contrast', 'vscode'];
    const EXPLORER_MD_FONTS = [
        'system', 'serif', 'consolas', 'cascadia-code', 'jetbrains-mono', 'courier-new'
    ];
    const EXPLORER_MD_PRESET_DEFAULT = 'default';
    const EXPLORER_MD_FONT_DEFAULT = 'system';
    const EXPLORER_MD_PRESET_KEY = 'gridvibe.mdPreviewPreset';
    const EXPLORER_MD_FONT_KEY = 'gridvibe.mdPreviewFont';
    const EXPLORER_MD_PRESET_LABELS = {
        default: 'Default',
        paper: 'Paper',
        contrast: 'High contrast',
        vscode: 'Slate',
    };
    const EXPLORER_MD_FONT_LABELS = {
        system: 'System',
        serif: 'Serif',
        consolas: 'Consolas',
        'cascadia-code': 'Cascadia Code',
        'jetbrains-mono': 'JetBrains Mono',
        'courier-new': 'Courier New',
    };

    function readExplorerMarkdownPref(key, allowed, fallback) {
        let stored = '';
        try {
            stored = window.localStorage.getItem(key) || '';
        } catch (err) {
            stored = '';
        }
        return allowed.includes(stored) ? stored : fallback;
    }

    function explorerMarkdownAppearance() {
        return {
            preset: readExplorerMarkdownPref(
                EXPLORER_MD_PRESET_KEY, EXPLORER_MD_PRESETS, EXPLORER_MD_PRESET_DEFAULT
            ),
            font: readExplorerMarkdownPref(
                EXPLORER_MD_FONT_KEY, EXPLORER_MD_FONTS, EXPLORER_MD_FONT_DEFAULT
            ),
        };
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

    function applyExplorerMarkdownAppearanceToAll() {
        const appearance = explorerMarkdownAppearance();
        document.querySelectorAll('.explorer-markdown-preview').forEach(preview => {
            applyExplorerMarkdownAppearanceToElement(preview, appearance);
        });
    }

    function setExplorerMarkdownAppearance(patch) {
        const current = explorerMarkdownAppearance();
        const next = {
            preset: EXPLORER_MD_PRESETS.includes(patch?.preset) ? patch.preset : current.preset,
            font: EXPLORER_MD_FONTS.includes(patch?.font) ? patch.font : current.font,
        };
        try {
            window.localStorage.setItem(EXPLORER_MD_PRESET_KEY, next.preset);
            window.localStorage.setItem(EXPLORER_MD_FONT_KEY, next.font);
        } catch (err) {
            // Non-fatal: appearance still applies to the live DOM this session.
        }
        applyExplorerMarkdownAppearanceToAll();
        refreshExplorerMarkdownAppearanceMenu();
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

    function showExplorerMarkdownAppearanceMenu(anchor) {
        dismissExplorerMarkdownAppearanceMenu();
        if (!anchor) {
            return;
        }
        const appearance = explorerMarkdownAppearance();
        const menu = document.createElement('div');
        menu.id = 'explorer-md-menu';
        menu.setAttribute('role', 'menu');
        menu.setAttribute('aria-label', 'Markdown preview appearance');
        menu.appendChild(buildExplorerMarkdownMenuGroup(
            'Theme',
            EXPLORER_MD_PRESETS.map(value => ({ value, label: EXPLORER_MD_PRESET_LABELS[value] })),
            appearance.preset,
            'mdPreset',
            value => setExplorerMarkdownAppearance({ preset: value })
        ));
        menu.appendChild(buildExplorerMarkdownMenuGroup(
            'Font',
            EXPLORER_MD_FONTS.map(value => ({ value, label: EXPLORER_MD_FONT_LABELS[value] })),
            appearance.font,
            'mdFont',
            value => setExplorerMarkdownAppearance({ font: value })
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

    function explorerSourceLineRecords(content) {
        const source = String(content || '');
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
                <span class="explorer-source-chevron" aria-hidden="true">${collapsed ? '▸' : '▾'}</span>
                <span>${record.number}</span>
            </button>
        `;
    }

    function renderExplorerSourceLines(content, language, searchRanges = [], collapsedLines = new Set()) {
        const normalizedLanguage = normalizeExplorerLanguage(language);
        const records = explorerSourceLineRecords(content);
        const languageClass = explorerLanguageClass(language);
        const codeClass = languageClass ? ` language-${languageClass}` : '';
        const markdownHeadings = normalizedLanguage === 'markdown'
            ? explorerMarkdownHeadingLevels(records)
            : new Map();
        const allowMarkdownCollapse = normalizedLanguage === 'markdown' && !searchRanges.length;
        // Whole-document Highlight.js pass (Phase 1); null for unsupported
        // languages, the log/markdown special renderers, oversized files, or any
        // Highlight.js failure, in which case each line uses the fallback lexer.
        const highlightedLines = explorerHighlightDocumentLines(content, normalizedLanguage);
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
            const lineHtml = highlightedLines
                ? explorerRenderHighlightedRuns(highlightedLines.get(record.number), searchRanges)
                : highlightExplorerCode(record.text, language, searchRanges, record.start);
            // Heading-only Markdown tokeniser (OD-8): the fence-aware heading map
            // already computed for section collapse doubles as the highlighter,
            // so heading lines get a distinct token colour without a full grammar.
            const contentHtml = headingLevel
                ? `<span class="explorer-md-source-heading explorer-md-source-heading-${headingLevel}">${lineHtml}</span>`
                : lineHtml;
            rows.push(`
                <div class="explorer-source-line">
                    ${explorerSourceLineNumberHtml(record, headingLevel, collapsed)}
                    <code class="explorer-source-line-code${codeClass}">${contentHtml || '&nbsp;'}</code>
                </div>
            `);

            if (collapsed) {
                hiddenUntilHeadingLevel = headingLevel;
            }
        });

        return `<div class="explorer-source-lines">${rows.join('')}</div>`;
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

        const language = pane._explorerFilePlain ? '' : (pane._explorerFileLanguage || '');
        code.innerHTML = renderExplorerSourceLines(
            pane._explorerFileContent || '',
            language,
            searchRanges,
            ensureExplorerMarkdownCollapsedLines(pane)
        );
        wireExplorerMarkdownSectionControls(index);
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

    async function renderExplorerMermaid(preview) {
        if (!preview || !window.mermaid) {
            return;
        }
        const blocks = Array.from(preview.querySelectorAll('pre > code.language-mermaid'));
        if (!blocks.length) {
            return;
        }
        window.mermaid.initialize({
            startOnLoad: false,
            securityLevel: 'strict',
            theme: currentResolvedTheme() === 'dark' ? 'dark' : 'default',
            suppressErrorRendering: true
        });
        for (const code of blocks) {
            const source = code.textContent || '';
            const pre = code.parentElement;
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
                    continue;
                }
                diagram.innerHTML = rendered.svg;
                rendered.bindFunctions?.(diagram);
            } catch (error) {
                diagram.classList.add('explorer-mermaid-error');
                const message = String(error?.message || 'Invalid diagram').split('\n')[0];
                diagram.textContent = `Mermaid diagram error: ${message}`;
                continue;
            }
            /* Ctrl+scroll zooms the rendered diagram (notes 3); double-click
               resets it. Bound on the diagram box so the page-zoom default is
               suppressed only while the pointer is over the diagram. */
            enableExplorerWheelZoom(diagram, diagram.querySelector('svg'));
        }
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

    function restoreExplorerPreview(index) {
        const pane = terminals[index];
        const preview = document.getElementById(`explorer-preview-${index}`);
        if (pane && preview) {
            preview.innerHTML = pane._explorerPreviewHtml || '';
            if (!pane._explorerFilePlain) {
                highlightExplorerPreviewCode(preview);
            }
            renderExplorerMermaid(preview);
        }
        return preview;
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

        return `
            <button
                type="button"
                class="explorer-row ${isDirectory ? 'directory' : 'file'}${isDeleted ? ' deleted' : ''}"
                data-explorer-path="${escHtml(entry.path || '')}"
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

        viewer.querySelectorAll('.explorer-row.directory').forEach(button => {
            button.addEventListener('click', () => {
                loadExplorerPane(index, button.dataset.explorerPath || '');
            });
        });
        viewer.querySelectorAll('.explorer-row.file').forEach(button => {
            button.addEventListener('click', () => {
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

    function scheduleExplorerSearch(index, { resetActive = false, delay = EXPLORER_SEARCH_DEBOUNCE_MS } = {}) {
        const pane = terminals[index];
        if (!pane || !isExplorerSearchablePane(pane)) {
            return;
        }
        if (pane._explorerMode === 'directory') {
            applyExplorerSearch(index, { resetActive });
            return;
        }

        cancelExplorerSearch(index);
        pane._explorerSearchTimer = window.setTimeout(() => {
            pane._explorerSearchTimer = null;
            applyExplorerSearch(index, { resetActive });
        }, delay);
    }

    async function applyExplorerSearch(index, { resetActive = false } = {}) {
        const pane = terminals[index];
        if (!pane || !isExplorerSearchablePane(pane)) {
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

        const query = state.query || '';
        const view = activeExplorerFileView(index);
        let matchCount = 0;
        let capped = false;
        if (query && view === 'source') {
            const cachedRanges = state.resultQuery === query && Array.isArray(state.ranges)
                ? state.ranges
                : null;
            const ranges = cachedRanges || [];
            if (!cachedRanges) {
                cancelExplorerSearch(index);
                const token = { cancelled: false };
                pane._explorerSearchToken = token;
                updateExplorerSearchControls(index, query, 0, 0);
                const result = await explorerFindRangesAsync(pane._explorerFileContent || '', query, token);
                if (token.cancelled || pane._explorerSearchToken !== token) {
                    return;
                }
                pane._explorerSearchToken = null;
                ranges.splice(0, ranges.length, ...result.ranges);
                ranges.capped = result.capped;
                state.ranges = ranges;
                state.resultQuery = query;
            }
            matchCount = ranges.length;
            capped = Boolean(ranges.capped);
            state.activeIndex = matchCount ? Math.min(state.activeIndex || 0, matchCount - 1) : 0;
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
                renderExplorerDiff(index);
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
        if (query && matchCount) {
            scrollExplorerSearchMatch(index);
        }
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

    function clearExplorerSearch(index) {
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
        applyExplorerSearch(index);
        document.querySelector(`[data-explorer-search-input="${index}"]`)?.focus();
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

    function explorerPanelScrollTarget(panel) {
        if (!panel) {
            return null;
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

    function applyScrollMetrics(el, metrics) {
        if (!el || !metrics) {
            return;
        }
        const maxScrollTop = Math.max(0, el.scrollHeight - el.clientHeight);
        const maxScrollLeft = Math.max(0, el.scrollWidth - el.clientWidth);
        el.scrollLeft = Math.min(
            maxScrollLeft,
            maxScrollLeft > 0
                ? Math.round(maxScrollLeft * (metrics.scrollLeftRatio || 0))
                : (metrics.scrollLeft || 0)
        );
        el.scrollTop = metrics.wasAtBottom
            ? maxScrollTop
            : Math.min(
                maxScrollTop,
                maxScrollTop > 0
                    ? Math.round(maxScrollTop * (metrics.scrollTopRatio || 0))
                    : (metrics.scrollTop || 0)
            );
    }

    function captureExplorerFileScroll(index) {
        const list = document.getElementById(`explorer-list-${index}`);
        if (!list) {
            return null;
        }

        const activeButton = list.querySelector('[data-explorer-file-view][aria-selected="true"]');
        const state = {
            activeView: activeButton?.dataset.explorerFileView || 'source',
            listScrollLeft: list.scrollLeft,
            listScrollTop: list.scrollTop,
            panels: {},
            // File tree / Git sidebar panels sit outside the list and are their own
            // overflow:auto scrollers, so capture them too (they reset on reattach).
            sidebar: {
                tree: captureScrollMetrics(document.getElementById(`explorer-tree-panel-${index}`)),
                git: captureScrollMetrics(document.getElementById(`explorer-git-panel-${index}`))
            }
        };
        list.querySelectorAll('[data-explorer-file-panel]').forEach(panel => {
            const scrollEl = explorerPanelScrollTarget(panel);
            if (!scrollEl) {
                return;
            }
            const maxScrollTop = Math.max(0, scrollEl.scrollHeight - scrollEl.clientHeight);
            const maxScrollLeft = Math.max(0, scrollEl.scrollWidth - scrollEl.clientWidth);
            state.panels[panel.dataset.explorerFilePanel || 'source'] = {
                scrollLeft: scrollEl.scrollLeft,
                scrollLeftRatio: maxScrollLeft > 0 ? scrollEl.scrollLeft / maxScrollLeft : 0,
                scrollTop: scrollEl.scrollTop,
                scrollTopRatio: maxScrollTop > 0 ? scrollEl.scrollTop / maxScrollTop : 0,
                wasAtBottom: maxScrollTop > 0 && scrollEl.scrollTop >= maxScrollTop - 2
            };
        });
        return state;
    }

    function restoreExplorerFileScroll(index, state) {
        if (!state) {
            return;
        }

        /* Directory listings have no file-view panels; switching modes there
           would clobber stale diff state for no visual effect. */
        const listEl = document.getElementById(`explorer-list-${index}`);
        if (listEl && listEl.querySelector('[data-explorer-file-panel]')) {
            setExplorerFileView(index, state.activeView || 'source');
        }

        const applyScroll = () => {
            const list = document.getElementById(`explorer-list-${index}`);
            if (!list) {
                return;
            }
            list.scrollLeft = state.listScrollLeft || 0;
            list.scrollTop = state.listScrollTop || 0;
            applyScrollMetrics(document.getElementById(`explorer-tree-panel-${index}`), state.sidebar?.tree);
            applyScrollMetrics(document.getElementById(`explorer-git-panel-${index}`), state.sidebar?.git);
            list.querySelectorAll('[data-explorer-file-panel]').forEach(panel => {
                const panelState = state.panels?.[panel.dataset.explorerFilePanel || 'source'];
                const scrollEl = explorerPanelScrollTarget(panel);
                if (panelState && scrollEl) {
                    const maxScrollTop = Math.max(0, scrollEl.scrollHeight - scrollEl.clientHeight);
                    const maxScrollLeft = Math.max(0, scrollEl.scrollWidth - scrollEl.clientWidth);
                    scrollEl.scrollLeft = Math.min(
                        maxScrollLeft,
                        maxScrollLeft > 0
                            ? Math.round(maxScrollLeft * (panelState.scrollLeftRatio || 0))
                            : (panelState.scrollLeft || 0)
                    );
                    scrollEl.scrollTop = panelState.wasAtBottom
                        ? maxScrollTop
                        : Math.min(
                            maxScrollTop,
                            maxScrollTop > 0
                                ? Math.round(maxScrollTop * (panelState.scrollTopRatio || 0))
                                : (panelState.scrollTop || 0)
                        );
                }
            });
        };

        applyScroll();
        requestAnimationFrame(() => {
            applyScroll();
            requestAnimationFrame(applyScroll);
        });
        window.setTimeout(applyScroll, 80);
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

    /* Identity of a rendered file view: same path, same content, same diff
       target. Any change (tail-updated log, re-fetch with new bytes) produces
       a different identity, which suppresses scroll restore. */
    function explorerFileContentIdentity(path, content, diffCommit, diffMode) {
        return explorerHashText(
            [path || '', content || '', diffCommit || '', diffMode || ''].join('\u0000')
        );
    }

    function explorerDirectoryContentIdentity(path, entries) {
        return explorerHashText(
            `${path || ''}\u0000${Array.isArray(entries) ? entries.length : 0}`
        );
    }

    /* Snapshot the currently shown tab's view mode + scroll onto its tab
       record. Must run while the tab's content is still in the DOM, i.e.
       before the active tab id changes or a loading placeholder replaces the
       viewer. The `_explorerRenderedTabId` guard records which tab the viewer
       DOM actually belongs to — with Preview isolation two tabs can show the
       same path, so a path match alone cannot prove the DOM is the active
       tab's (it may be the Preview tab showing the same file in diff mode). */
    function explorerCaptureActiveTabView(index) {
        const pane = terminals[index];
        if (!pane || (pane._explorerMode !== 'file' && pane._explorerMode !== 'directory')) {
            return;
        }
        if (pane._explorerRenderedTabId !== pane._explorerActiveTabId) {
            return;
        }
        const tab = explorerFindTab(pane, pane._explorerActiveTabId);
        if (!tab) {
            return;
        }
        const isFile = pane._explorerMode === 'file';
        if (isFile) {
            if (explorerNormalizeTabPath(tab.path) !== explorerNormalizeTabPath(pane._explorerFilePath)) {
                return;
            }
        } else if (tab.path) {
            return;
        }
        const scroll = captureExplorerFileScroll(index);
        if (!scroll) {
            return;
        }
        tab.view = {
            mode: isFile ? (scroll.activeView || 'source') : '',
            identity: isFile
                ? explorerFileContentIdentity(
                    pane._explorerFilePath,
                    pane._explorerFileContent,
                    pane._explorerDiffCommit,
                    pane._explorerDiffMode
                )
                : explorerDirectoryContentIdentity(pane._explorerPath, pane._explorerEntries),
            scroll
        };
    }

    /* Return the tab's stored view snapshot when its content identity still
       matches what is about to render, otherwise null (OD-4 skip rule). */
    function explorerMatchingTabView(tab, identity) {
        const view = tab && tab.view;
        if (!view || !view.identity || !view.scroll || view.identity !== identity) {
            return null;
        }
        return view;
    }

    /* Diff content loads asynchronously, after restoreExplorerFileScroll has
       already run; re-apply a stashed diff-panel scroll once it arrives. */
    function applyExplorerPendingDiffScroll(index) {
        const pane = terminals[index];
        const metrics = pane ? pane._explorerPendingDiffScroll : null;
        if (!pane || !metrics) {
            return;
        }
        pane._explorerPendingDiffScroll = null;
        const panel = document.getElementById(`explorer-diff-panel-${index}`);
        applyScrollMetrics(explorerPanelScrollTarget(panel), metrics);
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

    const EXPLORER_GIT_REVERT_ICON = `
        <svg class="explorer-btn-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"
            fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M5 5.5v4.5h4.5"/>
            <path d="M5.4 13.5a7 7 0 1 0 1.7-6.4L5 10"/>
        </svg>
    `;
    async function downloadExplorerFile(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId || pane._explorerMode !== 'file') {
            return;
        }
        const path = pane._explorerFilePath || '';
        const fileName = pane._explorerFileName || 'download';
        const url = `/api/explorer/${encodeURIComponent(sessionId)}/download?path=${encodeURIComponent(path)}`;

        /* WebView2 silently ignores programmatic <a download> clicks, so in the
           native window route the save through the pywebview bridge (native
           Save dialog + server-side fetch). In the browser the anchor works. */
        if (isPywebviewAvailable() && window.pywebview.api.save_download) {
            try {
                const result = await window.pywebview.api.save_download(url, fileName);
                if (result?.ok) {
                    showTerminalToast(`Saved ${getDownloadBaseName(result.path) || fileName}`, 'success');
                } else if (!result?.cancelled) {
                    showTerminalToast(`Download failed: ${result?.error || 'unknown error'}`, 'error');
                }
            } catch (error) {
                showTerminalToast(`Download failed: ${error?.message || error}`, 'error');
            }
            return;
        }

        const link = document.createElement('a');
        link.href = url;
        link.download = fileName;
        document.body.appendChild(link);
        link.click();
        link.remove();
        showTerminalToast(`Downloading ${fileName}…`, 'success');
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

    /* ─────────────────────────────────────────────
       Explorer tabbed viewer (ISSUE-2026-014)
       The main pane is always a read-only viewer with a persistent tab strip:
       one permanent dynamic "Preview" tab plus deduplicated pinned tabs keyed
       by normalized path. The Files tree is the navigation surface.
    ───────────────────────────────────────────── */
    const EXPLORER_PREVIEW_TAB_ID = '__preview__';
    const EXPLORER_MAX_PINNED_TABS = 12;
    const EXPLORER_MAX_TAB_PATH_LENGTH = 4096;

    function explorerBaseName(path) {
        return String(path || '').replace(/\\/g, '/').split('/').filter(Boolean).pop() || '';
    }

    /* Normalize a path into a stable dedup key: forward slashes, no leading or
       trailing slash, collapsed separators. Empty for unusable input. */
    function explorerNormalizeTabPath(path) {
        const value = String(path == null ? '' : path).replace(/\\/g, '/').trim();
        if (!value || value.length > EXPLORER_MAX_TAB_PATH_LENGTH) {
            return '';
        }
        return value.replace(/\/{2,}/g, '/').replace(/^\/+/, '').replace(/\/+$/, '');
    }

    function ensureExplorerTabState(pane) {
        if (!Array.isArray(pane._explorerTabs) || !pane._explorerTabs.length) {
            pane._explorerTabs = [{ id: EXPLORER_PREVIEW_TAB_ID, pinned: false, path: '', name: '' }];
        }
        if (!pane._explorerActiveTabId || !pane._explorerTabs.some(tab => tab.id === pane._explorerActiveTabId)) {
            pane._explorerActiveTabId = EXPLORER_PREVIEW_TAB_ID;
        }
        return pane._explorerTabs;
    }

    function explorerPreviewTab(pane) {
        ensureExplorerTabState(pane);
        return pane._explorerTabs.find(tab => tab.id === EXPLORER_PREVIEW_TAB_ID) || pane._explorerTabs[0];
    }

    function explorerFindTab(pane, id) {
        ensureExplorerTabState(pane);
        return pane._explorerTabs.find(tab => tab.id === id) || null;
    }

    function explorerActiveTab(pane) {
        ensureExplorerTabState(pane);
        return explorerFindTab(pane, pane._explorerActiveTabId) || explorerPreviewTab(pane);
    }

    function explorerTabLabel(tab) {
        if (!tab) {
            return 'Preview';
        }
        if (tab.id === EXPLORER_PREVIEW_TAB_ID) {
            return tab.path ? (explorerBaseName(tab.path) || 'Preview') : 'Preview';
        }
        return tab.name || explorerBaseName(tab.path) || 'File';
    }

    /* Choose (and if needed create) the tab a file should load into. A `+`
       action or Markdown link pins a deduplicated tab; an explicit `tab`
       re-renders that tab; every other plain click (Files tree, Git sidebar)
       loads into the permanent Preview tab — pinned tabs are never hijacked,
       even when they already show the same path. */
    function explorerAssignOpenTab(pane, path, { pinned = false, tab = '' } = {}) {
        ensureExplorerTabState(pane);
        const key = explorerNormalizeTabPath(path);
        const name = explorerBaseName(path);

        if (tab) {
            const existing = explorerFindTab(pane, tab);
            if (existing) {
                existing.path = path;
                existing.name = name;
                pane._explorerActiveTabId = existing.id;
                return existing;
            }
        }

        if (pinned && key) {
            let pinnedTab = pane._explorerTabs.find(entry => entry.pinned && explorerNormalizeTabPath(entry.path) === key);
            if (!pinnedTab) {
                const pinnedCount = pane._explorerTabs.filter(entry => entry.pinned).length;
                if (pinnedCount >= EXPLORER_MAX_PINNED_TABS) {
                    const oldest = pane._explorerTabs.findIndex(entry => entry.pinned && entry.id !== pane._explorerActiveTabId);
                    if (oldest !== -1) {
                        pane._explorerTabs.splice(oldest, 1);
                    }
                }
                pinnedTab = { id: key, pinned: true, path, name };
                pane._explorerTabs.push(pinnedTab);
            } else {
                pinnedTab.path = path;
                pinnedTab.name = name;
            }
            pane._explorerActiveTabId = pinnedTab.id;
            return pinnedTab;
        }

        const preview = explorerPreviewTab(pane);
        preview.path = path;
        preview.name = name;
        pane._explorerActiveTabId = preview.id;
        return preview;
    }

    function explorerEnsureViewerShell(index) {
        const list = document.getElementById(`explorer-list-${index}`);
        if (!list) {
            return null;
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

    function explorerViewerEl(index) {
        return explorerEnsureViewerShell(index);
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

    function renderExplorerTabStrip(index) {
        const pane = terminals[index];
        const strip = document.getElementById(`explorer-tabs-${index}`);
        if (!pane || !strip) {
            return;
        }
        const tabs = ensureExplorerTabState(pane);
        const activeId = pane._explorerActiveTabId;
        strip.innerHTML = tabs.map(tab => {
            const active = tab.id === activeId;
            const isPreview = tab.id === EXPLORER_PREVIEW_TAB_ID;
            const label = explorerTabLabel(tab);
            const icon = (!isPreview || tab.path) ? explorerFileTypeIconHtml(tab.path || label) : '';
            const closeButton = isPreview
                ? ''
                : `<button type="button" class="explorer-tab-close" data-explorer-tab-close="${escHtml(tab.id)}" title="Close tab" aria-label="Close ${escHtml(label)}">×</button>`;
            return `
                <div class="explorer-tab${active ? ' active' : ''}${isPreview ? ' preview' : ''}" role="tab" aria-selected="${active ? 'true' : 'false'}" data-explorer-tab="${escHtml(tab.id)}"${isPreview ? '' : ' draggable="true"'} title="${escHtml(tab.path || label)}">
                    <button type="button" class="explorer-tab-main" data-explorer-tab-open="${escHtml(tab.id)}">
                        ${icon}
                        <span class="explorer-tab-name">${escHtml(label)}</span>
                    </button>
                    ${closeButton}
                </div>
            `;
        }).join('');

        strip.querySelectorAll('[data-explorer-tab-open]').forEach(button => {
            button.addEventListener('click', () => activateExplorerTab(index, button.dataset.explorerTabOpen || ''));
        });
        strip.querySelectorAll('[data-explorer-tab-close]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                closeExplorerTab(index, button.dataset.explorerTabClose || '');
            });
        });
        strip.querySelectorAll('[data-explorer-tab]').forEach(tabEl => {
            wireExplorerTabStripInteractions(index, tabEl);
        });
    }

    /* 2.g tab-strip affordances: middle-click closes a pinned tab (same
       guard as the ×), pinned tabs drag-reorder among themselves (OD-6: the
       permanent Preview tab keeps the first slot and is not draggable), and
       double-clicking the Preview tab promotes its shown file to a pinned
       tab in the same view mode. */
    function wireExplorerTabStripInteractions(index, tabEl) {
        const id = tabEl.dataset.explorerTab || '';
        if (id === EXPLORER_PREVIEW_TAB_ID) {
            tabEl.querySelector('.explorer-tab-main')?.addEventListener('dblclick', () => {
                promoteExplorerPreviewTab(index);
            });
            return;
        }
        tabEl.addEventListener('mousedown', event => {
            if (event.button === 1) {
                event.preventDefault(); // suppress middle-click autoscroll
            }
        });
        tabEl.addEventListener('auxclick', event => {
            if (event.button === 1) {
                event.preventDefault();
                closeExplorerTab(index, id);
            }
        });
        tabEl.addEventListener('dragstart', event => {
            const pane = terminals[index];
            if (pane) {
                pane._explorerDraggedTabId = id;
            }
            event.dataTransfer.effectAllowed = 'move';
            try {
                event.dataTransfer.setData('text/plain', id);
            } catch (_) {
                /* setData can throw in some embedded WebViews; the drag
                   still works off the pane-held id. */
            }
            tabEl.classList.add('dragging');
        });
        tabEl.addEventListener('dragend', () => {
            const pane = terminals[index];
            if (pane) {
                pane._explorerDraggedTabId = '';
            }
            clearExplorerTabDragMarkers(index);
        });
        tabEl.addEventListener('dragover', event => {
            const draggedId = terminals[index]?._explorerDraggedTabId || '';
            if (!draggedId || draggedId === id) {
                return;
            }
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
            const rect = tabEl.getBoundingClientRect();
            const before = event.clientX < rect.left + rect.width / 2;
            tabEl.classList.toggle('drag-before', before);
            tabEl.classList.toggle('drag-after', !before);
        });
        tabEl.addEventListener('dragleave', () => {
            tabEl.classList.remove('drag-before', 'drag-after');
        });
        tabEl.addEventListener('drop', event => {
            const draggedId = terminals[index]?._explorerDraggedTabId || '';
            if (!draggedId || draggedId === id) {
                return;
            }
            event.preventDefault();
            const rect = tabEl.getBoundingClientRect();
            const before = event.clientX < rect.left + rect.width / 2;
            reorderExplorerPinnedTab(index, draggedId, id, before);
        });
    }

    function clearExplorerTabDragMarkers(index) {
        document.getElementById(`explorer-tabs-${index}`)
            ?.querySelectorAll('.explorer-tab')
            .forEach(el => el.classList.remove('dragging', 'drag-before', 'drag-after'));
    }

    /* 2.g (OD-6): move a pinned tab before/after another pinned tab. Only
       pinned tabs reorder, and the insertion point is clamped behind the
       permanent Preview tab so nothing can land ahead of it. The persisted
       tab order (2.f) follows automatically because explorerSerializeTabs
       reads the array in order. */
    function reorderExplorerPinnedTab(index, draggedId, targetId, before) {
        const pane = terminals[index];
        if (!pane || !draggedId || draggedId === targetId) {
            return;
        }
        ensureExplorerTabState(pane);
        const tabs = pane._explorerTabs;
        const from = tabs.findIndex(tab => tab.pinned && tab.id === draggedId);
        if (from === -1 || !tabs.some(tab => tab.pinned && tab.id === targetId)) {
            return;
        }
        const [dragged] = tabs.splice(from, 1);
        let insertAt = tabs.findIndex(tab => tab.id === targetId) + (before ? 0 : 1);
        const previewPosition = tabs.findIndex(tab => tab.id === EXPLORER_PREVIEW_TAB_ID);
        insertAt = Math.max(insertAt, previewPosition + 1);
        tabs.splice(insertAt, 0, dragged);
        renderExplorerTabStrip(index);
        persistExplorerTabsToSession(index);
    }

    /* 2.g: double-clicking the Preview tab keeps its transient file — the
       shown file becomes a pinned tab carrying the same view mode, scroll,
       and zoom. The already-rendered viewer DOM is handed to the pinned tab
       (nothing is re-fetched); an existing pinned tab for the path is just
       activated, never clobbered. */
    function promoteExplorerPreviewTab(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const preview = explorerPreviewTab(pane);
        const path = preview.path || '';
        if (
            !path
            || pane._explorerMode !== 'file'
            || pane._explorerRenderedTabId !== EXPLORER_PREVIEW_TAB_ID
        ) {
            return; // Preview shows a directory or is still loading
        }
        const key = explorerNormalizeTabPath(path);
        const existing = pane._explorerTabs.find(tab => tab.pinned && explorerNormalizeTabPath(tab.path) === key);
        if (existing) {
            activateExplorerTab(index, existing.id);
            return;
        }
        // Fold the live mode + scroll into the Preview record, then copy the
        // full per-tab state onto the new pinned tab.
        explorerCaptureActiveTabView(index);
        const pinnedTab = explorerAssignOpenTab(pane, path, { pinned: true });
        if (preview.view) {
            pinnedTab.view = { ...preview.view };
        }
        if (preview.fontSize) {
            pinnedTab.fontSize = preview.fontSize;
        }
        if (preview.preferredMode) {
            pinnedTab.preferredMode = preview.preferredMode;
        }
        pane._explorerRenderedTabId = pinnedTab.id;
        renderExplorerTabStrip(index);
        persistExplorerTabsToSession(index);
    }

    function renderExplorerViewerEmpty(index) {
        const pane = terminals[index];
        const viewer = explorerEnsureViewerShell(index);
        const list = document.getElementById(`explorer-list-${index}`);
        if (!pane || !viewer) {
            return;
        }
        list?.classList.remove('file-view');
        clearExplorerDirectorySearchControls(index);
        const preview = explorerPreviewTab(pane);
        preview.path = '';
        preview.name = '';
        preview.dirPath = '';
        pane._explorerActiveTabId = EXPLORER_PREVIEW_TAB_ID;
        pane._explorerRenderedTabId = EXPLORER_PREVIEW_TAB_ID;
        pane._explorerMode = 'viewer';
        pane._explorerFilePath = '';
        viewer.innerHTML = '<div class="explorer-empty-viewer"><span>Select a file to view</span></div>';
        renderExplorerTabStrip(index);
    }

    /* Render whatever the active tab should show: its file, the browsed
       directory listing (Preview tab), or the empty state. */
    function renderExplorerActiveTab(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const tab = explorerActiveTab(pane);
        if (tab.path) {
            openExplorerFile(index, tab.path, { tab: tab.id });
            return;
        }
        if (
            pane._explorerMode === 'directory'
            && Array.isArray(pane._explorerEntries)
            && (!tab.dirPath || tab.dirPath === pane._explorerPath)
        ) {
            /* The in-memory listing still belongs to this tab — render it
               without a re-fetch and backfill the tab's own directory path. */
            tab.dirPath = pane._explorerPath;
            pane._explorerActiveTabId = EXPLORER_PREVIEW_TAB_ID;
            pane._explorerRenderedTabId = EXPLORER_PREVIEW_TAB_ID;
            renderExplorerDirectorySearchControls(index);
            renderExplorerDirectoryRows(index);
            const restoredView = explorerMatchingTabView(
                tab,
                explorerDirectoryContentIdentity(pane._explorerPath, pane._explorerEntries)
            );
            if (restoredView) {
                restoreExplorerFileScroll(index, restoredView.scroll);
            }
            renderExplorerTabStrip(index);
            return;
        }
        if (tab.id === EXPLORER_PREVIEW_TAB_ID && tab.dirPath) {
            /* The viewer last rendered another tab, so the pane-global
               directory state no longer describes the Preview tab — re-browse
               the tab's own directory instead of falling through to empty. */
            loadExplorerPane(index, tab.dirPath);
            return;
        }
        renderExplorerViewerEmpty(index);
    }

    function activateExplorerTab(index, id) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const tab = explorerFindTab(pane, id);
        if (!tab) {
            return;
        }
        if (pane._explorerActiveTabId === tab.id && pane._explorerRenderedTabId === tab.id) {
            // Already shown and its DOM is current: re-rendering would only
            // re-fetch, and would race a Preview-tab double-click promotion.
            return;
        }
        // Capture the outgoing tab's mode + scroll while its DOM is intact.
        explorerCaptureActiveTabView(index);
        pane._explorerActiveTabId = tab.id;
        renderExplorerActiveTab(index);
        renderExplorerTabStrip(index);
        persistExplorerTabsToSession(index);
    }

    function closeExplorerTab(index, id) {
        const pane = terminals[index];
        if (!pane || id === EXPLORER_PREVIEW_TAB_ID) {
            return;
        }
        ensureExplorerTabState(pane);
        const position = pane._explorerTabs.findIndex(tab => tab.id === id);
        if (position === -1) {
            return;
        }
        const wasActive = pane._explorerActiveTabId === id;
        pane._explorerTabs.splice(position, 1);
        if (wasActive) {
            const fallback = pane._explorerTabs[Math.max(0, position - 1)] || explorerPreviewTab(pane);
            activateExplorerTab(index, fallback.id);
        } else {
            renderExplorerTabStrip(index);
            persistExplorerTabsToSession(index);
        }
    }

    /* One-shot per session id: re-apply the Markdown appearance a saved
       session or restart snapshot carries (ISSUE-2026-033). The set keeps a
       close-driven rebuild of the same session from clobbering an appearance
       the user changed since launch; setExplorerMarkdownAppearance validates
       the values and syncs the shared localStorage keys. */
    const appliedExplorerMdSessions = new Set();
    function applyExplorerSessionMarkdownAppearance(index) {
        const sessionId = sessionIds[index];
        const session = terminals[index]?._session || {};
        const preset = session.explorer_md_preset || '';
        const font = session.explorer_md_font || '';
        if (!sessionId || appliedExplorerMdSessions.has(sessionId) || (!preset && !font)) {
            return;
        }
        appliedExplorerMdSessions.add(sessionId);
        setExplorerMarkdownAppearance({ preset, font });
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
        if (!pane._explorerTreeSidebarOpen && !pane._explorerGitSidebarOpen) {
            setExplorerTreeSidebarOpen(index, true);
        }
        applyExplorerSessionMarkdownAppearance(index);
        restoreExplorerPersistedTabs(index);
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

    /* ── Saved-session tab persistence (ISSUE-2026-015, per-tab views 2.f) ── */

    /* Reduce a tab's live view snapshot to the persisted shape (OD-5, amended
       per user feedback to include zoom): view mode, the primary panel's
       scroll as a fraction of scroll height (OD-4), the content-identity hash
       the restore-side skip rule compares, and the tab's editor font size
       (omitted at the default so unzoomed tabs persist nothing). */
    function explorerPersistableTabView(tab) {
        if (!tab) {
            return null;
        }
        const record = {};
        const view = tab.view;
        if (view && view.mode && view.identity) {
            const panel = view.scroll && view.scroll.panels ? view.scroll.panels[view.mode] : null;
            record.mode = view.mode;
            record.scroll = panel
                ? (panel.wasAtBottom ? 1 : Math.max(0, Math.min(1, panel.scrollTopRatio || 0)))
                : 0;
            record.identity = view.identity;
        }
        const fontSize = tab.fontSize ? clampExplorerEditorFontSize(tab.fontSize) : 0;
        if (fontSize && fontSize !== EXPLORER_EDITOR_FONT_DEFAULT) {
            record.font_size = fontSize;
        }
        if (tab.collapsedLines instanceof Set && tab.collapsedLines.size) {
            record.folds = Array.from(tab.collapsedLines)
                .filter(line => Number.isInteger(line) && line > 0)
                .sort((left, right) => left - right)
                .slice(0, 256);
            if (tab.collapsedIdentity) {
                record.fold_identity = tab.collapsedIdentity;
            }
        }
        return Object.keys(record).length ? record : null;
    }

    /* Clamped editor font size from one persisted tab view record; 0 = unset. */
    function explorerPersistedTabFontSize(raw) {
        const fontSize = Number(raw && typeof raw === 'object' ? raw.font_size : 0);
        if (!Number.isFinite(fontSize) || fontSize <= 0) {
            return 0;
        }
        return clampExplorerEditorFontSize(fontSize);
    }

    function explorerPersistedMarkdownFolds(raw) {
        if (!raw || typeof raw !== 'object' || !Array.isArray(raw.folds)) {
            return new Set();
        }
        return new Set(raw.folds
            .map(Number)
            .filter(line => Number.isInteger(line) && line > 0)
            .slice(0, 256));
    }

    function explorerPersistedMarkdownFoldIdentity(raw) {
        return raw && typeof raw === 'object' && typeof raw.fold_identity === 'string'
            ? raw.fold_identity
            : '';
    }

    /* Inflate one persisted tab view back into the in-memory `tab.view`
       snapshot shape 2.e restores from (clamped fraction-based metrics). */
    function explorerInflatePersistedTabView(raw) {
        if (!raw || typeof raw !== 'object') {
            return null;
        }
        const mode = ['source', 'preview', 'diff'].includes(raw.mode) ? raw.mode : '';
        const identity = typeof raw.identity === 'string' ? raw.identity : '';
        if (!mode || !identity) {
            return null;
        }
        const fraction = Math.max(0, Math.min(1, Number(raw.scroll) || 0));
        return {
            mode,
            identity,
            scroll: {
                activeView: mode,
                panels: { [mode]: { scrollTopRatio: fraction, wasAtBottom: fraction >= 0.999 } },
                sidebar: {}
            }
        };
    }

    function explorerSerializeTabs(pane) {
        ensureExplorerTabState(pane);
        const openTabs = [];
        const tabViews = {};
        const seen = new Set();
        pane._explorerTabs.forEach(tab => {
            if (!tab.pinned || openTabs.length >= EXPLORER_MAX_PINNED_TABS) {
                return;
            }
            const key = explorerNormalizeTabPath(tab.path);
            if (!key || seen.has(key)) {
                return;
            }
            seen.add(key);
            openTabs.push(tab.path);
            const view = explorerPersistableTabView(tab);
            if (view) {
                tabViews[key] = view;
            }
        });
        /* The Preview tab keeps its own separated path (shown file or browsed
           directory) plus its zoom across saves — stored under the reserved
           tab id, keyed as `path`/`dir` next to `font_size`. */
        const preview = explorerPreviewTab(pane);
        const previewRecord = explorerPersistableTabView(preview) || {};
        const previewPath = explorerNormalizeTabPath(preview.path);
        const previewDir = explorerNormalizeTabPath(preview.dirPath);
        if (previewPath) {
            previewRecord.path = previewPath;
        }
        if (previewDir) {
            previewRecord.dir = previewDir;
        }
        if (Object.keys(previewRecord).length) {
            tabViews[EXPLORER_PREVIEW_TAB_ID] = previewRecord;
        }
        const active = explorerActiveTab(pane);
        const activeTab = active && active.pinned ? explorerNormalizeTabPath(active.path) : '';
        return {
            open_tabs: openTabs,
            active_tab: activeTab,
            tab_views: tabViews
        };
    }

    function persistExplorerTabsToSession(index) {
        const pane = terminals[index];
        if (!pane || !pane._session) {
            return;
        }
        const serialized = explorerSerializeTabs(pane);
        pane._session.explorer_open_tabs = serialized.open_tabs;
        pane._session.explorer_active_tab = serialized.active_tab;
        pane._session.explorer_tab_views = serialized.tab_views;
    }

    function restoreExplorerPersistedTabs(index) {
        const pane = terminals[index];
        if (!pane || pane._explorerTabsRestored) {
            return;
        }
        pane._explorerTabsRestored = true;
        const session = pane._session || {};
        const rawTabs = Array.isArray(session.explorer_open_tabs) ? session.explorer_open_tabs : [];
        const rawViews = session.explorer_tab_views && typeof session.explorer_tab_views === 'object'
            ? session.explorer_tab_views
            : {};
        ensureExplorerTabState(pane);
        /* The Preview tab's zoom, shown file, and browsed directory persist
           under its reserved id even when no pinned tabs were saved. */
        const rawPreviewView = rawViews[EXPLORER_PREVIEW_TAB_ID];
        const previewTab = explorerPreviewTab(pane);
        const previewFont = explorerPersistedTabFontSize(rawPreviewView);
        if (previewFont) {
            previewTab.fontSize = previewFont;
        }
        previewTab.collapsedLines = explorerPersistedMarkdownFolds(rawPreviewView);
        previewTab.collapsedIdentity = explorerPersistedMarkdownFoldIdentity(rawPreviewView);
        const savedPreviewPath = explorerNormalizeTabPath(
            rawPreviewView && typeof rawPreviewView === 'object' ? rawPreviewView.path : ''
        );
        const savedPreviewDir = explorerNormalizeTabPath(
            rawPreviewView && typeof rawPreviewView === 'object' ? rawPreviewView.dir : ''
        );
        if (savedPreviewDir) {
            previewTab.dirPath = savedPreviewDir;
        }
        /* Reopen the Preview tab's own content only when no pinned tab was
           saved as active — an active pinned tab wins the viewer, and the
           seeded path/dirPath above brings the Preview content back whenever
           the user returns to the tab. */
        const restorePreviewContent = () => {
            if (savedPreviewPath) {
                openExplorerFile(index, savedPreviewPath);
            } else if (savedPreviewDir) {
                loadExplorerPane(index, savedPreviewDir);
            } else {
                renderExplorerTabStrip(index);
            }
        };
        if (savedPreviewPath) {
            previewTab.path = savedPreviewPath;
            previewTab.name = explorerBaseName(savedPreviewPath);
        }
        if (!rawTabs.length) {
            restorePreviewContent();
            return;
        }
        const seen = new Set();
        rawTabs.forEach(raw => {
            const path = String(raw == null ? '' : raw);
            const key = explorerNormalizeTabPath(path);
            if (!key || seen.has(key)) {
                return;
            }
            if (pane._explorerTabs.filter(tab => tab.pinned).length >= EXPLORER_MAX_PINNED_TABS) {
                return;
            }
            seen.add(key);
            const record = { id: key, pinned: true, path, name: explorerBaseName(path) };
            /* 2.f: seed the persisted view mode + scroll fraction and zoom;
               the OD-4 identity check decides on render whether mode/scroll
               still apply (the zoom always does). */
            const view = explorerInflatePersistedTabView(rawViews[key]);
            if (view) {
                record.view = view;
            }
            const fontSize = explorerPersistedTabFontSize(rawViews[key]);
            if (fontSize) {
                record.fontSize = fontSize;
            }
            record.collapsedLines = explorerPersistedMarkdownFolds(rawViews[key]);
            record.collapsedIdentity = explorerPersistedMarkdownFoldIdentity(rawViews[key]);
            pane._explorerTabs.push(record);
        });
        const activeKey = explorerNormalizeTabPath(session.explorer_active_tab || '');
        const activeTab = activeKey
            ? pane._explorerTabs.find(tab => tab.pinned && explorerNormalizeTabPath(tab.path) === activeKey)
            : null;
        if (activeTab) {
            activateExplorerTab(index, activeTab.id);
        } else {
            restorePreviewContent();
        }
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
        pane._explorerFilePlain = false;
        pane._explorerPreviewHtml = '';
        pane._explorerGit = null;
        pane._explorerGitContext = null;
        pane._explorerDiffLoaded = false;
        pane._explorerDiffCacheKey = '';
        pane._explorerDiffContent = '';
        pane._explorerDiffSplit = false;
        pane._explorerDiffCommit = '';
        pane._explorerDiffMode = '';
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
        return true;
    }

    function renderExplorerFile(index, data, { scrollState = null, openDiff = false, diffCommit = '', diffMode = '', tab = '', pinned = false } = {}) {
        const pane = terminals[index];
        const list = document.getElementById(`explorer-list-${index}`);
        const viewer = explorerEnsureViewerShell(index);
        if (!pane || !list || !viewer) {
            return false;
        }
        const assignedTab = explorerAssignOpenTab(pane, data.path || '', { pinned, tab });
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
        const hasPreview = data.preview_type === 'markdown' && typeof data.preview_html === 'string';
        const requestedDiffCommit = String(diffCommit || '');
        const requestedDiffMode = requestedDiffCommit ? '' : String(diffMode || '');
        const hasGitDiff = explorerHasGitDiff(data.git) || Boolean(requestedDiffCommit);
        const metaParts = explorerFileMetaParts(data, fileType);
        const previousPath = pane._explorerFilePath || '';
        const contentIdentity = explorerFileContentIdentity(
            path, data.content, requestedDiffCommit, requestedDiffMode
        );
        if (assignedTab.collapsedIdentity !== contentIdentity) {
            assignedTab.collapsedLines = new Set();
            assignedTab.collapsedIdentity = contentIdentity;
        }
        /* 2.e: restore the tab's stored view mode + scroll when the content is
           still what the snapshot was taken from (OD-4 identity check). */
        const restoredTabView = scrollState
            ? null
            : explorerMatchingTabView(
                assignedTab,
                contentIdentity
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
        const searchState = ensureExplorerSearchState(pane, 'file');
        if (previousPath && previousPath !== path) {
            cancelExplorerSearch(index);
            searchState.activeIndex = 0;
            searchState.matchCount = 0;
            searchState.matchCapped = false;
            searchState.ranges = [];
            searchState.resultQuery = '';
        }
        clearExplorerDirectorySearchControls(index);

        pane._attached = true;
        pane._explorerMode = 'file';
        pane._explorerFilePath = path;
        pane._explorerFileName = fileName;
        pane._explorerFileContent = data.content || '';
        pane._explorerFileLanguage = codeLanguage;
        pane._explorerFilePlain = pane._explorerFileContent.length > EXPLORER_PLAIN_PREVIEW_THRESHOLD;
        pane._explorerPreviewHtml = hasPreview ? (data.preview_html || '') : '';
        pane._explorerGit = data.git || null;
        pane._explorerGitContext = data.git_context || null;
        pane._explorerDiffLoaded = false;
        pane._explorerDiffCacheKey = '';
        pane._explorerDiffContent = '';
        pane._explorerDiffSplit = keepDiffSplit;
        pane._explorerDiffCommit = requestedDiffCommit;
        pane._explorerDiffMode = requestedDiffMode;
        pane._explorerLastFileView = initialFileView === 'preview'
            ? 'preview'
            : (pane._explorerLastFileView === 'preview' && hasPreview ? 'preview' : 'source');
        // Diff content loads async; stash the restored diff scroll until then.
        pane._explorerPendingDiffScroll = initialFileView === 'diff'
            ? (restoredTabView && restoredTabView.scroll.panels
                ? restoredTabView.scroll.panels.diff || null
                : null)
            : null;
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
                        <button type="button" class="explorer-zoom-btn" data-explorer-zoom-decrease="${index}" title="Decrease font size" aria-label="Decrease editor font size">-</button>
                        <span class="explorer-zoom-value" data-explorer-zoom-value="${index}"></span>
                        <button type="button" class="explorer-zoom-btn" data-explorer-zoom-increase="${index}" title="Increase font size" aria-label="Increase editor font size">+</button>
                    </div>
                    ${hasPreview ? `<button type="button" class="explorer-md-appearance-btn" data-explorer-md-appearance="${index}" title="Markdown appearance" aria-label="Markdown preview appearance" aria-haspopup="menu" aria-expanded="false">${EXPLORER_MD_APPEARANCE_ICON}</button>` : ''}
                    <button type="button" class="explorer-download-btn" data-explorer-download="${index}" title="Download file" aria-label="Download file">${EXPLORER_DOWNLOAD_ICON}</button>
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
                <div class="explorer-editor-body${keepDiffSplit ? ' split-diff' : ''}">
                    <div class="explorer-editor-main">
                        <div class="explorer-source-view explorer-editor-panel" id="explorer-code-${index}" data-explorer-file-panel="source" ${initialFileView === 'source' ? '' : 'hidden'}></div>
                        ${hasPreview ? `<div class="explorer-markdown-preview explorer-editor-panel" id="explorer-preview-${index}" data-explorer-file-panel="preview" ${initialFileView === 'preview' ? '' : 'hidden'}></div>` : ''}
                    </div>
                    ${hasGitDiff ? `<aside class="explorer-diff-split" id="explorer-diff-panel-${index}" data-explorer-file-panel="diff" ${keepDiffSplit ? '' : 'hidden'}><div class="explorer-diff-content" id="explorer-diff-code-${index}"></div></aside>` : ''}
                </div>
            </div>
        `;

        renderExplorerSource(index);
        const preview = document.getElementById(`explorer-preview-${index}`);
        if (preview && hasPreview) {
            preview.innerHTML = pane._explorerPreviewHtml;
            if (!pane._explorerFilePlain) {
                highlightExplorerPreviewCode(preview);
            }
            wireExplorerMarkdownLinks(index, preview);
            applyExplorerMarkdownAppearanceToElement(preview, explorerMarkdownAppearance());
            renderExplorerMermaid(preview);
        }

        const appearanceButton = list.querySelector(`[data-explorer-md-appearance="${index}"]`);
        if (appearanceButton) {
            appearanceButton.addEventListener('click', () => {
                if (document.getElementById('explorer-md-menu')) {
                    dismissExplorerMarkdownAppearanceMenu();
                } else {
                    showExplorerMarkdownAppearanceMenu(appearanceButton);
                }
            });
        }

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
        applyExplorerSearch(index);
        // An explicit scrollState (in-place refresh) wins; otherwise fall back
        // to the tab's stored snapshot, aligned with the restored view mode.
        const effectiveScrollState = scrollState || (restoredTabView
            ? { ...restoredTabView.scroll, activeView: initialFileView }
            : null);
        restoreExplorerFileScroll(index, effectiveScrollState);
        renderExplorerTabStrip(index);
        persistExplorerTabsToSession(index);
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

        const hasPreview = data.preview_type === 'markdown' && typeof data.preview_html === 'string';
        const hasGitDiff = explorerHasGitDiff(data.git);
        const preview = document.getElementById(`explorer-preview-${index}`);
        const diffPanel = document.getElementById(`explorer-diff-code-${index}`);
        if (hasPreview !== Boolean(preview) || hasGitDiff !== Boolean(diffPanel)) {
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
        pane._explorerFileLanguage = codeLanguage;
        pane._explorerFilePlain = pane._explorerFileContent.length > EXPLORER_PLAIN_PREVIEW_THRESHOLD;
        pane._explorerPreviewHtml = hasPreview ? (data.preview_html || '') : '';
        pane._explorerGit = data.git || null;
        pane._explorerGitContext = data.git_context || null;
        pane._explorerDiffLoaded = false;
        pane._explorerDiffCacheKey = '';
        pane._explorerDiffContent = '';
        renderExplorerSource(index);
        if (preview && hasPreview) {
            preview.innerHTML = pane._explorerPreviewHtml;
            if (!pane._explorerFilePlain) {
                highlightExplorerPreviewCode(preview);
            }
            wireExplorerMarkdownLinks(index, preview);
            applyExplorerMarkdownAppearanceToElement(preview, explorerMarkdownAppearance());
            renderExplorerMermaid(preview);
        }
        if (diffPanel && hasGitDiff) {
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
        applyExplorerSearch(index);
        restoreExplorerFileScroll(index, scrollState);
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
        pane._explorerRenderedTabId = explorerAssignOpenTab(pane, path, {}).id;

        pane._attached = true;
        pane._explorerMode = 'file';
        pane._explorerFilePath = path;
        pane._explorerFileContent = '';
        pane._explorerFileLanguage = codeLanguage;
        pane._explorerPreviewHtml = '';
        pane._explorerGit = null;
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
        wireExplorerSearchControls(index);
        applyExplorerEditorFontSize(index);
        loadExplorerDiff(index);
        renderExplorerTabStrip(index);
        return true;
    }

    async function openExplorerFile(index, path, { showLoading = true, preserveScroll = false, openDiff = false, diffCommit = '', diffMode = '', pinned = false, tab = '' } = {}) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !isExplorerSession(pane._session) || !sessionId || !path) {
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
            const response = await fetch(`/api/explorer/${encodeURIComponent(sessionId)}/file?path=${encodeURIComponent(path)}`);
            const data = await response.json();
            if (!response.ok) {
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
            const rendered = renderExplorerFile(index, data, { scrollState, openDiff, diffCommit, diffMode, pinned, tab });
            if (rendered) {
                revealExplorerTreePath(index);
            }
            return rendered;
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer file open failed:', error);
            renderExplorerDirectoryOpenError(index, error.message || 'Failed to open file.');
            return false;
        }
    }

    async function refreshExplorerPane(index) {
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

            pane._attached = true;
            pane._explorerMode = 'directory';
            pane._explorerFilePath = '';
            pane._explorerFileContent = '';
            pane._explorerFileLanguage = '';
            pane._explorerPreviewHtml = '';
            pane._explorerGit = null;
            pane._explorerGitContext = data.git || null;
            pane._explorerDiffLoaded = false;
            pane._explorerDiffContent = '';
            pane._explorerPath = data.path || '';
            pane._explorerParentPath = data.parent_path || '';
            pane._explorerEntries = Array.isArray(data.entries) ? data.entries : [];
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
                explorerDirectoryContentIdentity(pane._explorerPath, pane._explorerEntries)
            );
            if (restoredDirView) {
                restoreExplorerFileScroll(index, restoredDirView.scroll);
            }
            renderExplorerTabStrip(index);
            return true;
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer load failed:', error);
            renderExplorerMessage(index, error.message || 'Failed to load directory.');
            return false;
        }
    }
