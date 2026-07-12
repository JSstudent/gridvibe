# Vendored frontend libraries

Pinned copies of the third-party browser libraries the terminals page depends on,
served locally so GridVibe works fully offline (see deep-dive finding 3.6).

| File | Package | Version | Source | License |
|------|---------|---------|--------|---------|
| `xterm.css` | xterm | 5.3.0 | https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css | MIT |
| `xterm.min.js` | xterm | 5.3.0 | https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js | MIT |
| `xterm-addon-fit.min.js` | xterm-addon-fit | 0.8.0 | https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js | MIT |
| `socket.io.min.js` | socket.io-client | 4.7.2 | https://cdn.jsdelivr.net/npm/socket.io-client@4.7.2/dist/socket.io.min.js | MIT |

To upgrade, download the new pinned version from the same URL pattern, replace the
file, and update this table plus any version references in `templates/terminals.html`.
