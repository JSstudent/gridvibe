/* Minimal DOM for the real shared list controller. Rows and slots retain
   identity across decoration updates; serializing reads their current values. */
const listShell = fakeElement('agentDashboardList');
const listBody = fakeElement('agentDashboardListBody');
byId.set(listShell.id, listShell);
byId.set(listBody.id, listBody);
listBody.rebuilds = 0;
listBody.style = { setProperty() {}, removeProperty() {} };
let listMarkup = '';
let listRows = [];
const decodeListText = value => String(value).replace(/&quot;/g, '"').replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>').replace(/&amp;/g, '&');

Object.defineProperty(listBody, 'innerHTML', {
    get() { return listMarkup; },
    set(html) {
        listMarkup = html;
        listBody.rebuilds += 1;
        listRows = [...html.matchAll(/<button\b([^>]*class="dash-agent"[^>]*)>([\s\S]*?)<\/button>/g)]
            .map(match => {
                const row = fakeElement('');
                row.title = decodeListText(/title="([^"]*)"/.exec(match[1])?.[1] || '');
                for (const attr of match[1].matchAll(/data-([a-z-]+)="([^"]*)"/g)) {
                    row.dataset[attr[1].replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = attr[2];
                }
                row.slots = {};
                for (const name of ['line', 'who', 'reading', 'progress', 'crew', 'flags', 'selection']) {
                    const slot = { innerHTML: '', textContent: '' };
                    const pattern = new RegExp(`<span class="dash-agent-${name}">([\\s\\S]*?)<\\/span>(?=\\s*(?:<span class="dash-agent-|<\\/button>|$))`);
                    const content = pattern.exec(match[2])?.[1] || '';
                    slot.innerHTML = content;
                    slot.textContent = decodeListText(content);
                    row.slots[`.dash-agent-${name}`] = slot;
                }
                row.icon = fakeElement('');
                row.icon.removeAttribute = name => delete row.icon.attributes[name];
                row.querySelector = selector => selector === '.dash-agent-icon' ? row.icon : row.slots[selector];
                row.removeAttribute = name => delete row.attributes[name];
                row.closest = selector => selector.includes('dash-agent') || selector === '[data-dashboard-action]' ? row : null;
                return row;
            });
    }
});
listBody.querySelectorAll = () => listRows;
listBody.querySelector = selector => {
    const key = /data-dashboard-key="([^"]*)"/.exec(selector)?.[1];
    if (typeof focusIsInside !== 'undefined' && focusIsInside && focusTarget) { if (typeof focusQueries !== 'undefined') focusQueries.push(selector); return focusTarget; }
    return listRows.find(row => row.dataset.dashboardKey === key) || null;
};
listBody.contains = target => listRows.includes(target)
    || (typeof focusIsInside !== 'undefined' && focusIsInside);
function listRow(id) { return listRows.find(row => row.dataset.sessionId === id); }

function renderedListHtml() {
    return listMarkup.replace(/<button\b([^>]*class="dash-agent"[^>]*)>([\s\S]*?)<\/button>/g,
        (whole, attrs, inner) => {
            const id = /data-session-id="([^"]*)"/.exec(attrs)?.[1];
            const row = listRow(id);
            if (!row) return whole;
            attrs = attrs.replace(/title="[^"]*"/, `title="${escHtml(row.title)}"`);
            for (const name of ['line', 'reading', 'progress', 'crew', 'flags', 'selection']) {
                const slot = row.slots[`.dash-agent-${name}`];
                const pattern = new RegExp(`(<span class="dash-agent-${name}">)[\\s\\S]*?(<\\/span>)(?=\\s*(?:<span class="dash-agent-|$))`);
                inner = inner.replace(pattern, (_, open, close) => open
                    + (['line', 'flags', 'selection'].includes(name) ? escHtml(slot.textContent) : slot.innerHTML) + close);
            }
            return `<button ${attrs}>${inner}</button>`;
        });
}
