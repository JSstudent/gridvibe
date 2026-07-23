# Vendored frontend libraries

Pinned copies of the third-party browser libraries the terminals page depends on,
served locally so GridVibe works fully offline (see deep-dive finding 3.6).

| File | Package | Version | Source | License |
|------|---------|---------|--------|---------|
| `xterm.css` | xterm | 5.3.0 | https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css | MIT |
| `xterm.min.js` | xterm | 5.3.0 | https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js | MIT |
| `xterm-addon-fit.min.js` | xterm-addon-fit | 0.8.0 | https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js | MIT |
| `xterm-addon-search.min.js` | xterm-addon-search | 0.13.0 | https://cdn.jsdelivr.net/npm/xterm-addon-search@0.13.0/lib/xterm-addon-search.min.js | MIT |
| `xterm-addon-web-links.min.js` | xterm-addon-web-links | 0.9.0 | https://cdn.jsdelivr.net/npm/xterm-addon-web-links@0.9.0/lib/xterm-addon-web-links.min.js | MIT |
| `socket.io.min.js` | socket.io-client | 4.7.2 | https://cdn.jsdelivr.net/npm/socket.io-client@4.7.2/dist/socket.io.min.js | MIT |
| `mermaid.min.js` | Mermaid | 11.15.0 | https://cdn.jsdelivr.net/npm/mermaid@11.15.0/dist/mermaid.min.js | MIT |
| `highlight.min.js` | Highlight.js | 11.9.0 | https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js (common build) + `languages/{dos,powershell,dockerfile}.min.js` | BSD-3-Clause |
| `diff2html-ui-base.min.js` | diff2html | 3.4.48 | https://cdn.jsdelivr.net/npm/diff2html@3.4.48/bundles/js/diff2html-ui-base.min.js | MIT |
| `diff2html.min.css` | diff2html | 3.4.48 | https://cdn.jsdelivr.net/npm/diff2html@3.4.48/bundles/css/diff2html.min.css | MIT |

`highlight.min.js` is the stock Highlight.js "common" build with three extra
language grammars (`dos`, `powershell`, `dockerfile`) appended so the explorer
Source viewer covers every language in `EXPLORER_HLJS_LANGUAGE`
(`web/static/js/explorer-viewer.js`). GridVibe always passes the grammar
explicitly — Highlight.js auto-detection is never used. To rebuild:

```
curl -sSL -o highlight.min.js https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js
for L in dos powershell dockerfile; do
  curl -sSL https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/$L.min.js >> highlight.min.js
done
```

`diff2html-ui-base.min.js` is the UI bundle that accepts a caller-provided
Highlight.js instance (the explorer passes the pinned `hljs`); GridVibe theme
overrides live in `web/static/css/terminals.css`, not in the vendored CSS, so
these files can be replaced verbatim on upgrade.

To upgrade, download the new pinned version from the same URL pattern, replace the
file, and update this table plus any version references in `templates/terminals.html`.
