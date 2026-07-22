# Richer Source and Diff Views — Proposal

Last updated: 2026-07-22

Status: Proposed

## 1. Summary

GridVibe's explorer already provides line-numbered, syntax-colored source files
and side-by-side Git diffs. The current implementation is deliberately small,
but it lacks the language awareness and intraline change highlighting expected
from a modern code viewer.

This proposal recommends:

1. A locally vendored, custom Highlight.js build for richer Source syntax
   highlighting.
2. Diff2Html's base browser renderer, using the same Highlight.js instance, for
   better line alignment and character-level change highlighting.
3. Keeping the existing explorer shell, tabs, search, line numbers, Markdown
   section folding, font zoom, scroll restoration, theme behavior, and
   large-file safeguards.

The change is entirely browser-side in its first version. The existing bounded
Git-diff API remains the source of diff data, and the explorer remains
read-only.

## 2. Goals

- Make common languages—notably Go, Python, HTML, CSS, JavaScript, and
  TypeScript—substantially easier to scan.
- Preserve multiline syntax state for comments, strings, template literals,
  and embedded languages.
- Show exactly which text changed inside a modified diff line.
- Align related deleted and added lines more intelligently.
- Retain GridVibe's offline operation by serving pinned assets locally.
- Keep large files and unusually expensive diffs responsive.
- Preserve current explorer navigation and per-tab state.

## 3. Non-goals

- Turning Source into an IDE or editable code surface.
- Language-server features such as completion, diagnostics, definitions, or
  semantic tokens.
- Replacing the existing Git backend or returning complete old/new file
  snapshots.
- Rendering binary diffs.
- Automatically detecting languages from file contents when the existing
  path-based classifier has no match.

## 4. Current implementation

### 4.1 Source view

Language detection is extension- and filename-based in
`web/static/js/terminals.js` (`EXPLORER_LANGUAGE_BY_EXTENSION` and
`EXPLORER_LANGUAGE_BY_FILENAME`). This is a good fit for an explorer because it
is deterministic and avoids expensive language auto-detection.

`highlightExplorerCode` is a handwritten lexer with five primary token types:

- keyword
- string
- comment
- number
- built-in

`renderExplorerSourceLines` splits the document into line records and calls the
highlighter separately for each line. This creates several visible limitations:

- Multiline comments and strings lose their state at every newline.
- Python triple-quoted strings, decorators, function/class names, parameters,
  f-strings, and type annotations receive limited distinction.
- Go declarations, types, package references, constants, and function names
  are mostly rendered as plain text.
- HTML attributes and embedded `<script>`/`<style>` content are not properly
  tokenized.
- JavaScript regular expressions, template interpolation, JSX/TSX, properties,
  and declaration names receive limited distinction.

Source previews above approximately 2 MiB already fall back to plain text. That
guard should remain.

### 4.2 Diff view

The frontend parses the unified Git patch in
`renderExplorerSideBySideDiff`. Within each hunk, it queues deleted lines and
pairs them with added lines in arrival order. Each resulting line receives
syntax highlighting and a full-line red or green background.

The renderer does not compute intraline differences. For example:

```diff
-timeout_ms = 5000
+timeout_ms = 8000
```

Both lines are colored in full, even though only one character changed. FIFO
pairing can also align unrelated lines when a change block contains unequal or
reordered additions and deletions.

The backend already bounds diff output to 256 KiB and 4,000 lines in
`web/explorer.py`. It also reports whether the result was truncated, but the
frontend currently retains only the diff text and does not present that state
to the user.

## 5. Recommended design

### 5.1 Dependency strategy

Add pinned browser assets under `web/static/vendor/` and document their exact
versions, sources, and licenses in `web/static/vendor/README.md`:

- A custom Highlight.js browser build containing only GridVibe's supported
  languages.
- `diff2html-ui-base`, which accepts a caller-provided Highlight.js instance.
- Diff2Html's CSS, adapted through GridVibe overrides rather than edited in the
  vendored file.

The assets must be loaded locally from `templates/terminals.html`; no runtime
CDN access should be required. This follows the existing xterm, Socket.IO, and
Mermaid asset policy.

The Highlight.js custom build should initially include:

- Bash/shell, Batch, and PowerShell
- C, C++, C#, Java, Kotlin, Swift, and Rust
- CSS, HTML/XML, JavaScript, TypeScript, JSON, and Markdown
- Dockerfile, Makefile, INI, TOML, YAML, and diff
- Go, Lua, PHP, Python, Ruby, and SQL

GridVibe should continue supplying the language explicitly. Highlight.js
auto-detection is unnecessary and would add cost and occasional false matches.

References:

- [Highlight.js custom builds](https://highlightjs.org/download)
- [Diff2Html repository and browser distributions](https://github.com/rtfpessoa/diff2html)

### 5.2 Source rendering

The full source document must be tokenized in one pass. Calling Highlight.js
once per line would preserve the current multiline-state defect.

The recommended rendering pipeline is:

1. Determine the language using the existing filename/extension classifier.
2. Highlight the complete document once with an explicit language.
3. Parse the returned markup into a detached DOM fragment.
4. Walk the fragment and divide text at newline boundaries while carrying the
   active token-class stack into the next line.
5. Render the resulting per-line fragments through the existing line-numbered
   Source DOM.
6. Apply search marks without destroying syntax spans.

This retains the existing Source structure required by:

- line numbers
- Markdown heading collapse
- search-result navigation
- scroll restoration
- selection and copy behavior
- current font-size and wrapping preferences

The rendered token palette should distinguish at least:

- keywords and literals
- strings and interpolation
- comments and documentation comments
- function, class, and type names
- parameters and variables
- built-ins
- attributes and properties
- tags and selectors
- operators and punctuation
- decorators and metadata
- numbers and constants

Light and dark colors should be expressed through explorer-scoped CSS variables
or selectors. They must maintain sufficient contrast without overriding the
surrounding explorer theme.

If Highlight.js is unavailable, rejects a language, or throws while processing
a file, Source should fall back to safely escaped plain text. Files above the
existing plain-preview threshold should bypass Highlight.js entirely.

### 5.3 Diff rendering

Pass the unified patch already returned by `/api/explorer/<session>/git/diff`
to `diff2html-ui-base`, supplying the custom Highlight.js instance.

The initial configuration should be equivalent to:

```javascript
{
    outputFormat: 'side-by-side',
    drawFileList: false,
    fileContentToggle: false,
    matching: 'words',
    diffStyle: 'char',
    highlight: true,
    matchingMaxComparisons: 1500,
    maxLineLengthHighlight: 2000
}
```

The exact comparison limits should be confirmed with representative large
diffs, but they must remain explicit. Diff2Html documents line matching as the
main expensive operation for large or pathological changes.

The visual hierarchy should use:

- a low-opacity red/green background for the whole affected line
- a stronger red/green background for the precise changed span
- `-` and `+` gutter markers
- distinct, readable hunk headers
- visible empty-side cells for pure insertions and deletions
- a clear treatment for whitespace-only changes

GridVibe should store and display the API's `truncated` flag. A truncated diff
must show a non-blocking banner such as "Diff truncated to 256 KiB / 4,000
lines" so the displayed patch is never mistaken for the complete change.

If Diff2Html cannot parse a partial or unusual patch, the UI should fall back to
the current tolerant renderer or an escaped unified-diff view rather than show
an empty panel.

### 5.4 Search and state integration

Existing behavior must remain intact:

- `Ctrl+F` searches the active Source or Diff panel.
- Search marks coexist with syntax and intraline-change spans.
- The active match remains visually stronger than ordinary matches.
- Async diff loading reapplies the saved scroll position after rendering.
- Per-tab Source/Preview/Diff selection remains unchanged.
- Font zoom applies consistently to Source and Diff.

Search marking should operate on text nodes and avoid nesting or replacing
vendor-generated structural elements in a way that breaks later redraws.

## 6. Performance and security guardrails

- Keep the existing approximately 2 MiB Source highlighting threshold.
- Keep the backend's 256 KiB / 4,000-line Git-diff limits.
- Set explicit Diff2Html line-comparison and line-length limits.
- Use extension-based explicit languages; do not run auto-detection.
- Never load executable assets from a CDN at runtime.
- Pin dependency versions and record their source URLs and licenses.
- Treat highlighted HTML as output from the pinned highlighter only; raw file
  content and error messages must continue to be escaped.
- Preserve the read-only filesystem contract.
- Avoid performing expensive redraws for every search keystroke when the
  existing debounced search can mark the already-rendered DOM.

## 7. Alternatives considered

### 7.1 Prism plus a custom differ

Prism provides a compact custom build and a useful token-array API, which makes
full-document-to-line rendering attractive. Its language components are small
and it would be a strong Source-only choice.

It does not directly integrate with Diff2Html, however. Combining Prism syntax
tokens with a separate intraline differ would require custom interval merging,
line similarity matching, HTML composition, and more algorithm-specific tests.
The Highlight.js/Diff2Html pairing offers a shorter, more cohesive path.

Reference: [Prism custom download](https://prismjs.com/download)

### 7.2 CodeMirror 6 MergeView

CodeMirror's MergeView can align two documents, highlight inserted/deleted
spans, provide gutters, and collapse unchanged sections. It is the strongest
candidate if GridVibe later adopts a full in-app editor.

It is not a drop-in replacement here because MergeView expects complete old
and new documents. GridVibe currently returns a bounded unified patch, often
containing only changed hunks and nearby context. Adopting MergeView would
therefore require new backend snapshot endpoints plus substantially more
component lifecycle, state, search, and styling work.

Reference: [CodeMirror MergeView documentation](https://codemirror.net/docs/ref/#merge.MergeView)

### 7.3 Shiki

Shiki offers excellent TextMate-grammar fidelity. Its browser integration is
asynchronous and introduces a WASM engine; the published web preset is several
megabytes minified before compression. Fine-grained bundles can reduce that,
but they still introduce more build and runtime complexity than this viewer
needs.

Reference: [Shiki bundles](https://shiki.style/guide/bundles)

### 7.4 Monaco

Monaco provides the richest editor and diff surface but carries editor workers,
language services, a much larger asset footprint, and significant lifecycle
complexity. It should be considered only as part of a deliberate IDE-style
editor project, not this viewer enhancement.

## 8. Proposed rollout

### Phase 1 — Source highlighting

- Vendor and document the custom Highlight.js build.
- Add complete-document tokenization and safe per-line rendering.
- Define explorer-scoped light/dark token colors.
- Preserve the log-specific renderer and Markdown collapse behavior.
- Retain plain-text fallback for large and unsupported files.

### Phase 2 — Precise diffs

- Vendor and document Diff2Html UI base and CSS.
- Replace the default diff rendering path.
- Add line matching and intraline character emphasis.
- Surface diff truncation.
- Preserve search, zoom, scroll restoration, and error fallbacks.

### Phase 3 — Optional polish

- Unified/split diff toggle.
- Show-whitespace toggle and explicit whitespace-only markers.
- Collapse/expand unchanged context where sufficient context is available.
- Copy one side or selected changed lines.
- User preference for word-level versus character-level emphasis.

## 9. Testing strategy

### Source fixtures

Cover at minimum:

- Python triple-quoted strings, decorators, f-strings, annotations, and comments
- Go raw strings, block comments, declarations, types, and package selectors
- HTML attributes plus embedded JavaScript and CSS
- JavaScript/TypeScript template strings, regexes, JSX/TSX, and multiline
  comments
- JSON, YAML, shell, PowerShell, Markdown, and unknown plain text
- content containing `<script>`, entities, and other escaping-sensitive text
- files immediately below and above the plain-preview threshold

### Diff fixtures

Cover at minimum:

- a one-character numeric or identifier change
- whitespace-only modifications
- unequal delete/add blocks
- inserted-only and deleted-only hunks
- long lines beyond the intraline-highlight limit
- multiple hunks with correct line numbers
- a truncated patch
- a malformed or incomplete patch that exercises the fallback
- Source and Diff search marks after syntax/intraline rendering

Existing API and HTML contract tests should be updated to assert the new pinned
assets, rendering entry points, fallback behavior, and truncation message.

## 10. Acceptance criteria

- Go, Python, HTML, CSS, JavaScript, and TypeScript visibly distinguish richer
  syntax categories than the current five-token lexer.
- Multiline syntax constructs retain correct coloring across line boundaries.
- Modified diff lines emphasize the exact changed characters, not only the
  entire red/green line.
- Related old/new lines are aligned more accurately than FIFO pairing.
- Source/Diff search, theme switching, font zoom, scroll restoration, and tab
  persistence continue to work.
- Large Source files and expensive diff blocks fall back gracefully without
  freezing the UI.
- Truncated diffs are visibly identified.
- All frontend dependencies are pinned, licensed, documented, and served
  locally.
- `make check` passes.

