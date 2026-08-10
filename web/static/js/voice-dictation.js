/* GridVibeVoiceDictation — the two decisions behind dictating into the
   explorer's in-place editor.

   One recorder serves the whole page, so a transcript arriving over the socket
   has to be routed: into the edit buffer that started the capture, into the
   recording pane's terminal, into the partial-preview bubble, or nowhere at
   all. `resolveVoiceDelivery` is that routing rule, and `composeDictationInsert`
   is the string math for placing the words at the caret.

   Both are pure and DOM-free so tests execute the rules in Node rather than
   asserting on source text. The page consumes them through the global; nothing
   here touches the network, the DOM, or storage. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeVoiceDictation = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* A dictated block never opens with a space when the buffer already ends in
       whitespace or an opener, and never when the transcript supplies its own
       leading whitespace or leads with punctuation that closes the previous
       word. */
    const OPENING_BEFORE = new Set(['(', '[', '{', '<', '"', '\'', '`', '@', '#', '$', '/', '\\', '-', '_', '=', '+', '*', '~']);
    const CLOSING_LEAD = new Set(['.', ',', ';', ':', '!', '?', ')', ']', '}', '"', '\'', '%']);

    function isWhitespace(character) {
        return character !== '' && /\s/.test(character);
    }

    /* Where does this transcript go?

       `edit` is the recording pane's live edit binding — `{ epoch, saving }`
       while a capture is bound to an open edit session, otherwise null.
       `epoch` is the epoch the arriving capture was bound to, or null when the
       capture was never bound to an editor at all. A non-null `epoch` (or a
       binding) means the transcript belongs to an editor and must never reach
       a terminal, whatever else is true. */
    function resolveVoiceDelivery({ hasTerm, edit, epoch, isFinal, hasText } = {}) {
        if (!hasText) {
            return { target: 'drop', reason: 'no-text' };
        }
        if (!isFinal) {
            return { target: 'preview', reason: '' };
        }
        const boundToEditor = Boolean(edit) || (epoch !== null && epoch !== undefined);
        if (boundToEditor) {
            if (!edit) {
                return { target: 'drop', reason: 'no-edit' };
            }
            if (edit.epoch !== epoch) {
                return { target: 'drop', reason: 'epoch-mismatch' };
            }
            if (edit.saving) {
                return { target: 'drop', reason: 'saving' };
            }
            return { target: 'editor', reason: '' };
        }
        if (hasTerm) {
            return { target: 'terminal', reason: '' };
        }
        return { target: 'drop', reason: 'no-target' };
    }

    /* What exactly gets inserted at the caret?

       `before` is the buffer up to the insertion point (the text preceding the
       selection when one is being replaced). The transcript is flattened to a
       single line — dictation inserts words, never structure — and gains one
       leading space when it would otherwise run into the preceding word. The
       surrounding buffer is never trimmed or otherwise rewritten. */
    function composeDictationInsert({ before, text } = {}) {
        const flattened = String(text == null ? '' : text).replace(/[\r\n]+/g, ' ');
        if (!flattened) {
            return { text: '', spacedBefore: false };
        }
        const previous = String(before == null ? '' : before).slice(-1);
        const lead = flattened.charAt(0);
        const spacedBefore = Boolean(previous)
            && !isWhitespace(previous)
            && !OPENING_BEFORE.has(previous)
            && !isWhitespace(lead)
            && !CLOSING_LEAD.has(lead);
        return {
            text: spacedBefore ? ` ${flattened}` : flattened,
            spacedBefore
        };
    }

    return {
        resolveVoiceDelivery,
        composeDictationInsert
    };
}));
