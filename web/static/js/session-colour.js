/* GridVibeSessionColour — the hue a session group wears, wherever it is drawn.

   A session tab in the workspace window has had a colour of its own for a long
   time: a deterministic pick out of ten, keyed on the group id, so the tab a
   reader is looking for is found by its hue before its name is read. The agent
   dashboard lists those same sessions from another page entirely, and a card
   that named a session in the dialog's own accent said nothing about *which*
   session — the reader had to match it by name against a strip of coloured
   tabs in another window.

   So the rule moves here, out of `terminals.js`, for the reason every other
   two-surface answer in this app is its own module: a hash copied into a
   second file is two palettes waiting to drift apart, and the drift would be
   invisible until a card and its tab happened to disagree.

   Three things are deliberate:

     · **The key is the group id and nothing else.** Not an index, not a
       position in the tab strip — a session keeps its colour when the tabs are
       reordered, when it moves to another workspace, and across a restart,
       because none of those change the id. It is also what lets a page that
       has never rendered that tab (the launcher) answer the same as the page
       that has.
     · **Palette, not computation.** Ten hand-picked hues that stay legible on
       both grounds, rather than an HSL wheel keyed on the hash: a generated
       hue lands in the muddy part of the wheel often enough to matter, and
       these are the ten the workspace window has always used.
     · **Hex plus an alpha helper, never a token.** The value is written into
       an inline custom property by the caller, so it has to be a real colour;
       `tokens.css` has nothing to say about a per-session hue, and a module
       that returned a token name would leave both callers doing the same
       lookup anyway.

   DOM-free and require()-able from Node, so the mapping is executed by tests
   rather than asserted as source text. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeSessionColour = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* The ten. Order is load-bearing — it is what the hash indexes into — so a
       hue is replaced in place and never reordered, or every open session
       changes colour on the next release. */
    const SESSION_COLOUR_PALETTE = [
        '#ff6b6b', '#ff922b', '#ffd43b', '#69db7c',
        '#38d9a9', '#4dabf7', '#748ffc', '#da77f2',
        '#f783ac', '#a9e34b',
    ];

    /* The same 32-bit rolling hash the session tabs have always used. Kept
       exactly as it was, including the signed truncation: changing it would
       recolour every session that already exists, which is the one thing a
       stable identity colour must not do. */
    function sessionColour(groupId) {
        const value = String(groupId === null || groupId === undefined ? '' : groupId);
        let hash = 0;
        for (let i = 0; i < value.length; i++) {
            hash = (hash * 31 + value.charCodeAt(i)) & 0xffffffff;
        }
        return SESSION_COLOUR_PALETTE[Math.abs(hash) % SESSION_COLOUR_PALETTE.length];
    }

    /* A palette entry at some transparency. Every caller wants the hue twice —
       once solid for a border or a name, once faint for a fill behind it — and
       a second copy of this three-line parse is how one of them comes to be
       written `rgba(255,107,107,.14)` by hand. */
    function sessionColourRgba(groupId, alpha) {
        return hexToRgba(sessionColour(groupId), alpha);
    }

    function hexToRgba(hex, alpha) {
        const value = String(hex || '');
        const r = parseInt(value.slice(1, 3), 16);
        const g = parseInt(value.slice(3, 5), 16);
        const b = parseInt(value.slice(5, 7), 16);
        return `rgba(${r},${g},${b},${alpha})`;
    }

    return {
        SESSION_COLOUR_PALETTE,
        sessionColour,
        sessionColourRgba,
        hexToRgba
    };
}));
