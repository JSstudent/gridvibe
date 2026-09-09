/* GridVibeAgentGlyphs — the mark that says *which* agent a row is about.

   The dashboard already names the agent in words; a row of identical GridVibe
   icons in front of those words says only "this is a row", which is what the
   reader could already see. So each agent the registry knows gets a mark of
   its own, and the marks are the thing the eye sorts a long list by before it
   reads a single word.

   Three decisions worth stating:

     · **Drawn here, not fetched.** These are inline `currentColor` SVGs in the
       same stroke language as every other glyph in the app, so they take the
       theme, cost no request, and cannot leave a row iconless because a remote
       asset was slow or blocked. Nothing here loads a vendor's artwork: they
       are simplified geometric marks in GridVibe's own idiom, distinct from
       each other, which is the whole job an icon in a list has.
     · **The key is the registry's, and an unknown one is not an error.** The
       agent key comes from `agent-identity.js`'s `agentKeyForSession`, so a
       custom agent, a registry entry added later, or `other` all land on the
       shared fallback — the terminal-caret chip the dashboard used before —
       rather than on nothing.
     · **Colour is the page's, not this module's.** Each mark is emitted with
       its key on the wrapper, and the stylesheet decides what tint that key
       wears. A module that returned a hex value would have to know both
       themes, and would put a palette somewhere no palette belongs.

   DOM-free and require()-able from Node so the mapping is executed by tests
   rather than asserted as source text. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeAgentGlyphs = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* The key every agent with no mark of its own resolves to. It is a real
       key rather than an empty string so the wrapper always carries one and
       the stylesheet has exactly one selector shape to match. */
    const AGENT_GLYPH_DEFAULT_KEY = 'default';

    function glyph(body) {
        return '<svg class="dash-agent-glyph" viewBox="0 0 24 24" fill="none" '
            + 'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
            + 'stroke-linejoin="round" aria-hidden="true" focusable="false">'
            + body
            + '</svg>';
    }

    /* One mark per agent the registry ships, each recognisable at 18px and
       distinct from the others at a glance — which is the bar an icon in a
       list has to clear, and the only one. */
    const AGENT_GLYPH_BODIES = {
        /* Claude — the radiating burst. */
        claude: '<path d="M12 3.2v6"></path><path d="M12 14.8v6"></path>'
            + '<path d="M3.2 12h6"></path><path d="M14.8 12h6"></path>'
            + '<path d="m5.8 5.8 4.2 4.2"></path><path d="m14 14 4.2 4.2"></path>'
            + '<path d="m18.2 5.8-4.2 4.2"></path><path d="m10 14-4.2 4.2"></path>',
        /* Codex — a six-sided knot: the ring and the braid inside it. */
        codex: '<path d="M12 2.9 20 7.4v9.2L12 21.1 4 16.6V7.4z"></path>'
            + '<path d="M12 7.2v9.6"></path><path d="m7.9 9.6 8.2 4.8"></path>'
            + '<path d="m16.1 9.6-8.2 4.8"></path>',
        /* Copilot — the visor, and the two lights behind it. */
        copilot: '<path d="M3.4 13.5a8.6 8.6 0 0 1 17.2 0v3.1a1.8 1.8 0 0 1-.9 1.6'
            + 'A17 17 0 0 1 12 20.4a17 17 0 0 1-7.7-2.2 1.8 1.8 0 0 1-.9-1.6z"></path>'
            + '<path d="M7.6 13.6v1.6"></path><path d="M16.4 13.6v1.6"></path>'
            + '<path d="M12 5.1V9"></path>',
        /* Grok — the angular slash pair. */
        grok: '<path d="M4.2 20 15 4.4"></path><path d="M19.8 4.4 14 12.8"></path>'
            + '<path d="m11.4 16.2-2.6 3.8"></path>',
        /* OpenCode — the brackets it is named for. */
        opencode: '<path d="m8.6 8.2-4.4 3.8 4.4 3.8"></path>'
            + '<path d="m15.4 8.2 4.4 3.8-4.4 3.8"></path>'
            + '<path d="m13.6 5.2-3.2 13.6"></path>',
        /* Kilo — the ascending ladder. */
        kilo: '<path d="M5.4 19V5"></path><path d="M9.6 19v-4.6"></path>'
            + '<path d="M14.4 19V9.8"></path><path d="M19 19V5"></path>',
        /* Kimi — the crescent. */
        kimi: '<path d="M19.4 14.8A8.2 8.2 0 1 1 10 4.4a6.4 6.4 0 0 0 9.4 10.4z"></path>'
            + '<path d="M17.4 4.6v3.2"></path><path d="M15.8 6.2H19"></path>',
        /* Hermes — the winged staff. */
        hermes: '<path d="M12 6.6V20"></path><circle cx="12" cy="4.4" r="1.6"></circle>'
            + '<path d="M12 9.4C10.4 7.6 7.6 7 4.6 7.6c.6 3 2.6 5 5.2 5.6"></path>'
            + '<path d="M12 9.4c1.6-1.8 4.4-2.4 7.4-1.8-.6 3-2.6 5-5.2 5.6"></path>'
    };

    /* The fallback, and the only mark that says nothing about *which* agent
       this is: a machine that is being spoken to. It is what a custom agent,
       an `other` selection and a registry entry GridVibe has not drawn yet all
       wear, so an unknown agent still reads as an agent. */
    const AGENT_GLYPH_DEFAULT_BODY =
        '<rect x="2.5" y="4.5" width="19" height="15" rx="3.5"></rect>'
        + '<polyline points="7 10 9.5 12 7 14"></polyline>'
        + '<line x1="12.5" y1="14.5" x2="17" y2="14.5"></line>';

    const AGENT_GLYPH_KEYS = Object.keys(AGENT_GLYPH_BODIES);

    /* Which mark this agent key wears, always a key the stylesheet can match. */
    function agentGlyphKey(agentKey) {
        const candidate = String(agentKey === null || agentKey === undefined ? '' : agentKey)
            .trim()
            .toLowerCase();
        return Object.prototype.hasOwnProperty.call(AGENT_GLYPH_BODIES, candidate)
            ? candidate
            : AGENT_GLYPH_DEFAULT_KEY;
    }

    function agentGlyphMarkup(agentKey) {
        const key = agentGlyphKey(agentKey);
        return glyph(
            key === AGENT_GLYPH_DEFAULT_KEY
                ? AGENT_GLYPH_DEFAULT_BODY
                : AGENT_GLYPH_BODIES[key]
        );
    }

    return {
        AGENT_GLYPH_DEFAULT_KEY,
        AGENT_GLYPH_KEYS,
        agentGlyphKey,
        agentGlyphMarkup
    };
}));
