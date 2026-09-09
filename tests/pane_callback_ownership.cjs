'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = name => fs.readFileSync(path.join(__dirname, '../web/static/js', name), 'utf8');
const document = { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
const pane = id => ({ _session: { session_id: id, group_id: id, mode: 'ssh', startup_mode: 'terminal', initial_command: `http://localhost/${id}` } });

async function shellCallbacks() {
    for (const ok of [true, false]) for (const returnToOwner of [true, false]) {
        let complete;
        let resets = 0;
        let placeholders = 0;
        const a = pane('A'), b = pane('B');
        a.term = { reset() { resets++; } };
        b.term = { reset() { throw Error('Reset wrong pane'); } };
        const ctx = vm.createContext({ console: { error() {} }, terminals: [a], sessionIds: ['A'],
            isExplorerSession: () => false, isBrowserSession: () => false, document,
            syncPaneIdentityChrome() {},
            showPlaceholderConnecting() { placeholders++; }, showTerminalToast() {},
            fetch: () => new Promise(resolve => { complete = resolve; }) });
        vm.runInContext(source('terminal-shell.js'), ctx);
        const pending = ctx.relaunchSessionShell(0, { agent: 'claude' });
        ctx.terminals[0] = b; ctx.sessionIds[0] = 'B';
        assert.equal(vm.runInContext('_pendingShellSwitchPanes.has(terminals[0])', ctx), false);
        if (returnToOwner) { ctx.terminals[0] = a; ctx.sessionIds[0] = 'A'; }
        complete({ ok, json: async () => ({ session_id: 'A', mode: 'ssh', startup_mode: 'agent', agent_selection: 'claude' }) });
        await pending;
        assert.equal(b._session.session_id, 'B');
        assert.equal(resets, ok && returnToOwner ? 1 : 0);
        assert.equal(placeholders, resets);
        assert.equal(a._session.startup_mode, ok ? 'agent' : 'terminal');
        assert.equal(vm.runInContext('_pendingShellSwitchPanes.size', ctx), 0);
    }
}

function browserCallbacks() {
    const a = pane('A'), b = pane('B');
    const writes = [], popups = [];
    const ctx = vm.createContext({ console, URL, terminals: [a], sessionIds: ['A'],
        isBrowserPaneInstance: () => true, isSessionModeSwitchPending: () => false,
        notePanePresentationChanged: index => writes.push(ctx.sessionIds[index]),
        noteGroupPresentationChanged: id => writes.push(id), document, window: {} });
    vm.runInContext(source('browser-pane.js'), ctx);
    ctx.browserEnsureTabState(a); ctx.browserEnsureTabState(b);
    const listeners = {};
    const frameDocument = { title: 'Owner title', addEventListener(name, fn) { listeners[name] = fn; },
        removeEventListener(name, fn) { if (listeners[name] === fn) delete listeners[name]; } };
    const nativeOpen = () => {};
    const frame = { dataset: { browserTabId: 'btab-0' }, tagName: 'IFRAME',
        contentWindow: { location: { href: 'http://localhost/redirect' }, document: frameDocument, open: nativeOpen },
        addEventListener(name, fn) { listeners[name] = fn; },
        removeEventListener(name, fn) { if (listeners[name] === fn) delete listeners[name]; } };
    ctx.browserWireFrame(0, frame);
    const queuedLoad = listeners.load;
    ctx.terminals[0] = b; ctx.sessionIds[0] = 'B';
    queuedLoad();
    assert.equal(b._session.initial_command, 'http://localhost/B');
    assert.equal(a._session.initial_command, 'http://localhost/redirect');
    assert.deepEqual(writes, ['A']);
    ctx.browserOpenTab = (index, url) => popups.push([ctx.sessionIds[index], url]);
    const queuedOpen = frame.contentWindow.open;
    const queuedClick = listeners.click;
    queuedOpen('http://localhost/new', '_blank');
    assert.deepEqual(popups, []);
    ctx.terminals.push(a); ctx.sessionIds.push('A');
    queuedOpen('http://localhost/new', '_blank');
    assert.deepEqual(popups, [['A', 'http://localhost/new']]);
    a._browserTabs.unshift(ctx.browserMakeTab('http://localhost/first', 'first'));
    frame.contentWindow.location.href = 'http://localhost/reordered';
    queuedLoad();
    assert.equal(a._browserTabs[1].url, 'http://localhost/reordered');
    a._browserTabs.splice(1, 1);
    const count = writes.length;
    queuedLoad();
    assert.equal(writes.length, count);
    ctx.browserDisposePane(a);
    queuedLoad(); queuedOpen('http://localhost/disposed', '_blank');
    assert.equal(popups.length, 1);
    assert.equal(frame.contentWindow.open, nativeOpen);
    assert.equal(Object.keys(listeners).length, 0);
    ctx.browserWireFrame(0, frame);
    listeners.load();
    queuedLoad(); queuedOpen('http://localhost/stale', '_blank');
    queuedClick({ target: { closest: () => ({ getAttribute: () => 'http://localhost/stale' }) },
        preventDefault() { throw Error('Disposed handler still active'); } });
    assert.equal(popups.length, 1);
    assert.equal(b._session.initial_command, 'http://localhost/reordered');
}

async function searchCallbacks() {
    let timer, complete;
    const a = pane('A'), b = pane('B');
    const requests = [], paints = [];
    const ctx = vm.createContext({ console, URLSearchParams, AbortController, terminals: [a], sessionIds: ['A'],
        clearTimeout() {}, setTimeout(fn) { timer = fn; return 1; },
        fetch(url, options) { requests.push([url, options.signal]); return new Promise(resolve => { complete = resolve; }); } });
    vm.runInContext(source('explorer-search.js'), ctx);
    ctx.renderExplorerSearchResults = index => paints.push(ctx.sessionIds[index]);
    const state = ctx.ensureExplorerRepoSearchState(a); state.query = 'hello';
    ctx.scheduleExplorerRepoSearch(0);
    const queuedTimer = timer;
    ctx.terminals[0] = b; ctx.sessionIds[0] = 'B';
    queuedTimer();
    assert.equal(requests.length, 0);
    ctx.terminals[0] = a; ctx.sessionIds[0] = 'A';
    const pending = ctx.runExplorerRepoSearch(0);
    ctx.terminals[0] = b; ctx.sessionIds[0] = 'B';
    complete({ ok: true, json: async () => ({ files: [{ path: 'a.txt' }] }) });
    await pending;
    assert.equal(state.payload.files[0].path, 'a.txt');
    assert.deepEqual(paints, ['A']);
    ctx.terminals[0] = a; ctx.sessionIds[0] = 'A';
    const discarded = ctx.runExplorerRepoSearch(0);
    const currentRequest = requests.at(-1);
    ctx.releaseExplorerRepoSearch(a);
    queuedTimer();
    assert.equal(currentRequest[1].aborted, true);
    complete({ ok: true, json: async () => ({ files: [{ path: 'stale.txt' }] }) });
    await discarded;
    assert.equal(state.payload.files[0].path, 'a.txt');
    assert.equal(state.loading, false);
    ctx.releaseExplorerRepoSearch(a, { dispose: true });
    assert.equal(a._explorerRepoSearch, null);
}

shellCallbacks().then(browserCallbacks).then(searchCallbacks).catch(error => { console.error(error); process.exitCode = 1; });
