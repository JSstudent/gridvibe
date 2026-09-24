/* ─────────────────────────────────────────────
   Startup Mode picker — the launcher row's "what does this pane run" control.

   A native <select> cannot draw an image inside an <option>, and the agent
   list is the one place in the launcher that ought to look like the panes it
   produces: each agent under the mark and brand colour its pane header and
   dashboard row will wear, each plain mode under the glyph its pane header's
   toggle uses. So the select stays — hidden, but still the one source of
   truth every launcher reader, writer and saved draft already goes through —
   and this module draws a button and a listbox over it.

   Nothing is copied into a second model. The open list is rebuilt from the
   select's own options on every open, and the button is repainted from the
   select by `sync`, which the launcher calls wherever it changes the select
   programmatically (a reconciled mode, a preflight status). A pick writes
   `select.value` and dispatches the `change` event the launcher already
   listens for, so choosing a row is indistinguishable from choosing an
   option.

   The list is one element on <body>, positioned `fixed` off the button: the
   Terminal Setup card scrolls and clips, and a list that lived inside it would
   open cut off at the card's edge. It closes on anything that would leave it
   pointing at the wrong place — a scroll outside it, a resize, a press
   outside it, focus leaving the window.
───────────────────────────────────────────── */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeStartupModePicker = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* The status the launcher's preflight appended to the selected agent's
       option ("Claude Code · Installed"). The option keeps its bare name in
       data-base-label, which is what tells a suffix from a name that happens
       to contain a middle dot. */
    const STATUS_SEPARATOR = ' · ';

    function text(value) {
        return value === null || value === undefined ? '' : String(value);
    }

    function optionStatus(option) {
        const base = text(option && option.dataset && option.dataset.baseLabel);
        const shown = text(option && option.textContent).trim();
        if (!base || !shown.startsWith(base + STATUS_SEPARATOR)) {
            return '';
        }
        return shown.slice(base.length + STATUS_SEPARATOR.length).trim();
    }

    function optionEntry(option) {
        const dataset = (option && option.dataset) || {};
        return {
            kind: 'option',
            value: text(option.value),
            label: text(dataset.baseLabel) || text(option.textContent).trim(),
            hint: text(dataset.hint),
            icon: text(dataset.icon),
            agent: text(dataset.agent),
            status: optionStatus(option),
            disabled: Boolean(option.disabled),
            hidden: Boolean(option.hidden),
            selected: Boolean(option.selected)
        };
    }

    /* The select read as the rows the list shows, in order: a heading for each
       optgroup, then its options. A hidden option is never offered — it exists
       so a draft saved half-way through a choice still has something to be —
       but it can still be the selected one the button shows. */
    function startupPickerEntries(select) {
        const entries = [];
        Array.from((select && select.children) || []).forEach(child => {
            const tag = text(child.tagName).toUpperCase();
            if (tag === 'OPTGROUP') {
                const options = Array.from(child.children || [])
                    .map(optionEntry)
                    .filter(entry => !entry.hidden);
                if (options.length) {
                    entries.push({ kind: 'group', label: text(child.label) });
                    entries.push(...options);
                }
                return;
            }
            if (tag === 'OPTION') {
                const entry = optionEntry(child);
                if (!entry.hidden) entries.push(entry);
            }
        });
        return entries;
    }

    function startupPickerSelected(select) {
        const options = Array.from((select && select.options) || []);
        const option = options[select ? select.selectedIndex : -1] || options.find(item => item.selected);
        return option ? optionEntry(option) : null;
    }

    /* The next row a key press lands on, skipping headings and disabled rows,
       stopping (not wrapping) at either end — the native select's own rule. */
    function stepPickerIndex(entries, from, step) {
        let index = from;
        for (;;) {
            index += step;
            if (index < 0 || index >= entries.length) return from;
            const entry = entries[index];
            if (entry.kind === 'option' && !entry.disabled) return index;
        }
    }

    function edgePickerIndex(entries, fromEnd) {
        return stepPickerIndex(entries, fromEnd ? entries.length : -1, fromEnd ? -1 : 1);
    }

    /* ── DOM adapter ─────────────────────────── */

    const STATUS_CLASS_PREFIX = 'status-';
    const CARET_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><polyline points="6 9 12 15 18 9"></polyline></svg>';

    const pickers = new WeakMap();
    let open = null;
    let listElement = null;
    let listSeq = 0;

    function esc(value) {
        return text(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function entryInnerHtml(picker, entry) {
        const icon = picker.iconMarkup(entry) || '';
        const agent = entry.agent ? ` data-agent="${esc(entry.agent)}"` : '';
        return `<span class="startup-mode-icon">${icon}</span>`
            + `<span class="startup-mode-name"${agent}>${esc(entry.label)}</span>`;
    }

    function ensureListElement() {
        if (listElement && listElement.isConnected) return listElement;
        listElement = document.createElement('div');
        listElement.className = 'startup-mode-list';
        listElement.id = 'startupModeList';
        listElement.setAttribute('role', 'listbox');
        listElement.tabIndex = -1;
        listElement.hidden = true;
        listElement.addEventListener('mousedown', event => event.preventDefault());
        listElement.addEventListener('pointermove', event => {
            const row = event.target.closest('[data-picker-index]');
            if (row && open) setActive(Number(row.dataset.pickerIndex), false);
        });
        listElement.addEventListener('click', event => {
            const row = event.target.closest('[data-picker-index]');
            if (row && open) choose(Number(row.dataset.pickerIndex));
        });
        listElement.addEventListener('keydown', onListKeydown);
        document.body.appendChild(listElement);
        return listElement;
    }

    function renderList(picker) {
        const list = ensureListElement();
        listSeq += 1;
        const prefix = `startupModeOption${listSeq}-`;
        list.innerHTML = open.entries.map((entry, index) => {
            if (entry.kind === 'group') {
                return `<div class="startup-mode-group" role="presentation">${esc(entry.label)}</div>`;
            }
            return `<div
                class="startup-mode-option${entry.selected ? ' is-selected' : ''}${entry.disabled ? ' is-disabled' : ''}"
                id="${prefix}${index}"
                role="option"
                data-picker-index="${index}"
                aria-selected="${entry.selected ? 'true' : 'false'}"
                ${entry.disabled ? 'aria-disabled="true"' : ''}
            >${entryInnerHtml(picker, entry)}${entry.hint ? `<span class="startup-mode-hint">${esc(entry.hint)}</span>` : ''}</div>`;
        }).join('');
        open.idPrefix = prefix;
    }

    function setActive(index, reveal) {
        if (!open || !listElement) return;
        const previous = listElement.querySelector('.startup-mode-option.is-active');
        if (previous) previous.classList.remove('is-active');
        open.active = index;
        const row = listElement.querySelector(`[data-picker-index="${index}"]`);
        if (!row) {
            listElement.removeAttribute('aria-activedescendant');
            return;
        }
        row.classList.add('is-active');
        listElement.setAttribute('aria-activedescendant', row.id);
        if (reveal && typeof row.scrollIntoView === 'function') {
            row.scrollIntoView({ block: 'nearest' });
        }
    }

    /* Below the button when the list fits there, above it when there is more
       room above, and never wider than the window. */
    function positionList(picker) {
        const list = listElement;
        const rect = picker.trigger.getBoundingClientRect();
        const gap = 4;
        const margin = 8;
        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;
        list.style.minWidth = `${Math.round(rect.width)}px`;
        list.style.maxHeight = '';
        list.style.top = '';
        list.style.bottom = '';
        const natural = Math.min(list.scrollHeight, 360);
        const below = viewportHeight - rect.bottom - gap - margin;
        const above = rect.top - gap - margin;
        const flip = natural > below && above > below;
        const room = Math.max(120, Math.min(360, flip ? above : below));
        list.style.maxHeight = `${Math.round(room)}px`;
        if (flip) {
            list.style.bottom = `${Math.round(viewportHeight - rect.top + gap)}px`;
        } else {
            list.style.top = `${Math.round(rect.bottom + gap)}px`;
        }
        const width = list.offsetWidth;
        const left = Math.max(margin, Math.min(rect.left, viewportWidth - width - margin));
        list.style.left = `${Math.round(left)}px`;
        list.classList.toggle('is-above', flip);
    }

    function onDocumentPointerDown(event) {
        if (!open) return;
        const target = event.target;
        if (listElement && listElement.contains(target)) return;
        if (open.picker.trigger.contains(target)) return;
        close(false);
    }

    function onDocumentScroll(event) {
        if (!open) return;
        if (listElement && (event.target === listElement || listElement.contains(event.target))) return;
        close(false);
    }

    function onWindowChange() {
        if (open) close(false);
    }

    function listen(on) {
        const method = on ? 'addEventListener' : 'removeEventListener';
        document[method]('pointerdown', onDocumentPointerDown, true);
        document[method]('scroll', onDocumentScroll, true);
        window[method]('resize', onWindowChange);
        window[method]('blur', onWindowChange);
    }

    function openPicker(picker, fromEnd) {
        if (open && open.picker === picker) return;
        if (open) close(false);
        if (picker.select.disabled) return;
        const entries = startupPickerEntries(picker.select);
        const selected = entries.findIndex(entry => entry.kind === 'option' && entry.selected);
        open = { picker, entries, active: -1, idPrefix: '' };
        renderList(picker);
        const list = listElement;
        list.hidden = false;
        list.setAttribute('aria-label', picker.trigger.getAttribute('aria-label') || 'Startup mode');
        picker.trigger.setAttribute('aria-expanded', 'true');
        picker.trigger.setAttribute('aria-controls', list.id);
        positionList(picker);
        const start = selected >= 0 && !entries[selected].disabled
            ? selected
            : edgePickerIndex(entries, Boolean(fromEnd));
        setActive(start, true);
        list.focus({ preventScroll: true });
        listen(true);
    }

    function close(refocus) {
        if (!open) return;
        const picker = open.picker;
        open = null;
        listen(false);
        if (listElement) {
            listElement.hidden = true;
            listElement.innerHTML = '';
            listElement.removeAttribute('aria-activedescendant');
        }
        picker.trigger.setAttribute('aria-expanded', 'false');
        if (refocus) picker.trigger.focus({ preventScroll: true });
    }

    function choose(index) {
        if (!open) return;
        const entry = open.entries[index];
        if (!entry || entry.kind !== 'option' || entry.disabled) return;
        const picker = open.picker;
        close(true);
        /* The rows are rebuilt wholesale on a count change or an import; a
           list left open across one names a select that is no longer there. */
        if (!picker.select.isConnected) return;
        if (picker.select.value !== entry.value) {
            picker.select.value = entry.value;
            picker.select.dispatchEvent(new Event('change', { bubbles: true }));
        }
        sync(picker.select);
    }

    function onListKeydown(event) {
        if (!open) return;
        const entries = open.entries;
        switch (event.key) {
        case 'ArrowDown':
            event.preventDefault();
            setActive(stepPickerIndex(entries, open.active, 1), true);
            break;
        case 'ArrowUp':
            event.preventDefault();
            setActive(stepPickerIndex(entries, open.active, -1), true);
            break;
        case 'Home':
        case 'PageUp':
            event.preventDefault();
            setActive(edgePickerIndex(entries, false), true);
            break;
        case 'End':
        case 'PageDown':
            event.preventDefault();
            setActive(edgePickerIndex(entries, true), true);
            break;
        case 'Enter':
        case ' ':
            event.preventDefault();
            choose(open.active);
            break;
        case 'Escape':
            event.preventDefault();
            event.stopPropagation();
            close(true);
            break;
        case 'Tab':
            close(true);
            break;
        default:
            break;
        }
    }

    function onTriggerKeydown(picker, event) {
        if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
            event.preventDefault();
            openPicker(picker, event.key === 'ArrowUp');
        }
    }

    /* Repaint the button from the select: the selected row's mark, name and
       hint, the preflight's status word, and the preflight's status class and
       tooltip — which the launcher keeps writing to the select, so the frame
       colour follows without the launcher knowing the button exists. */
    function sync(select) {
        const picker = select ? pickers.get(select) : null;
        if (!picker) return;
        const entry = startupPickerSelected(select) || {
            kind: 'option', value: '', label: '', hint: '', icon: '', agent: '', status: ''
        };
        const status = entry.status
            ? `<span class="startup-mode-status">${esc(entry.status)}</span>`
            : '';
        const html = entryInnerHtml(picker, entry)
            + status
            + `<span class="startup-mode-caret">${CARET_ICON}</span>`;
        if (picker.trigger.innerHTML !== html) picker.trigger.innerHTML = html;
        Array.from(picker.trigger.classList)
            .filter(name => name.startsWith(STATUS_CLASS_PREFIX))
            .forEach(name => picker.trigger.classList.remove(name));
        Array.from(select.classList)
            .filter(name => name.startsWith(STATUS_CLASS_PREFIX))
            .forEach(name => picker.trigger.classList.add(name));
        picker.trigger.title = select.title || '';
        picker.trigger.disabled = Boolean(select.disabled);
    }

    /* Draw the picker over one select. `iconMarkup(entry)` is the page's: it
       decides which glyph a row wears, so this module holds no icon of its own
       beyond its caret. Idempotent — enhancing an enhanced select only syncs. */
    function enhance(select, options) {
        if (!select) return null;
        if (pickers.has(select)) {
            sync(select);
            return pickers.get(select).trigger;
        }
        const trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'startup-mode-trigger';
        trigger.setAttribute('aria-haspopup', 'listbox');
        trigger.setAttribute('aria-expanded', 'false');
        trigger.setAttribute('aria-label', (options && options.label) || 'Startup mode');
        const picker = {
            select,
            trigger,
            iconMarkup: (options && options.iconMarkup) || (() => '')
        };
        pickers.set(select, picker);
        trigger.addEventListener('click', () => {
            if (open && open.picker === picker) close(true);
            else openPicker(picker, false);
        });
        trigger.addEventListener('keydown', event => onTriggerKeydown(picker, event));
        select.hidden = true;
        select.tabIndex = -1;
        select.setAttribute('aria-hidden', 'true');
        select.insertAdjacentElement('afterend', trigger);
        sync(select);
        return trigger;
    }

    return {
        startupPickerEntries,
        startupPickerSelected,
        stepPickerIndex,
        edgePickerIndex,
        enhance,
        sync,
        close: () => close(false)
    };
}));
