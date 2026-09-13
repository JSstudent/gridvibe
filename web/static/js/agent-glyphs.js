/* Shared agent artwork for terminal headers and the dashboard. Assets are
   bundled locally; unknown/custom agents retain the terminal SVG fallback.
   Brand colors live in tokens.css and are applied by agent-brand.css. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeAgentGlyphs = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const AGENT_GLYPH_DEFAULT_KEY = 'default';
    const AGENT_ARTWORK = {
        claude: 'claude-code.svg',
        codex: 'openai.svg',
        copilot: 'github-copilot.svg',
        grok: 'grok-ai-icon.svg',
        opencode: 'opencode.svg',
        kilo: 'kilocode.svg',
        kimi: 'kimi-code.svg',
        hermes: 'hermes.svg'
    };
    const AGENT_GLYPH_KEYS = Object.keys(AGENT_ARTWORK);

    function agentGlyphKey(agentKey) {
        const candidate = String(agentKey === null || agentKey === undefined ? '' : agentKey)
            .trim().toLowerCase();
        return Object.prototype.hasOwnProperty.call(AGENT_ARTWORK, candidate)
            ? candidate : AGENT_GLYPH_DEFAULT_KEY;
    }

    function agentGlyphMarkup(agentKey) {
        const key = agentGlyphKey(agentKey);
        if (key !== AGENT_GLYPH_DEFAULT_KEY) {
            return '<img class="dash-agent-glyph" src="/docs/images/agent/'
                + AGENT_ARTWORK[key] + '" width="18" height="18" alt="" aria-hidden="true" draggable="false">';
        }
        return '<svg class="dash-agent-glyph" viewBox="0 0 24 24" fill="none" '
            + 'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
            + 'stroke-linejoin="round" aria-hidden="true" focusable="false">'
            + '<rect x="2.5" y="4.5" width="19" height="15" rx="3.5"></rect>'
            + '<polyline points="7 10 9.5 12 7 14"></polyline>'
            + '<line x1="12.5" y1="14.5" x2="17" y2="14.5"></line></svg>';
    }

    return { AGENT_GLYPH_DEFAULT_KEY, AGENT_GLYPH_KEYS, agentGlyphKey, agentGlyphMarkup };
}));
