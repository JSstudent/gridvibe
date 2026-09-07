const assert = require('node:assert/strict');
const focus = require('../web/static/js/dashboard-focus.js');

function environment(useChannel) {
    let now = 10000, serial = 0;
    Date.now = () => now;
    const tasks = new Map(), pages = [], channels = new Set(), storage = new Map();
    function advance(ms) {
        const target = now + ms;
        for (;;) {
            const next = [...tasks].sort((a, b) => a[1].at - b[1].at)[0];
            if (!next || next[1].at > target) break;
            const [id, task] = next;
            now = task.at;
            if (task.interval) task.at += task.interval;
            else tasks.delete(id);
            task.fn();
        }
        now = target;
    }
    function emitter() {
        const handlers = new Map();
        return {
            addEventListener(type, fn) {
                if (!handlers.has(type)) handlers.set(type, new Set());
                handlers.get(type).add(fn);
            },
            removeEventListener(type, fn) { handlers.get(type)?.delete(fn); },
            fire(type, event = {}) { [...(handlers.get(type) || [])].forEach(fn => fn(event)); },
            listenerCount() { return [...handlers.values()].reduce((n, entries) => n + entries.size, 0); }
        };
    }
    function page(dashboard, initiallyFocused = false) {
        let focused = initiallyFocused;
        const classes = new Set();
        const doc = { ...emitter(), hidden: false, hasFocus: () => focused, body: { classList: {
            toggle(key, value) { if (value) classes.add(key); else classes.delete(key); },
            remove(key) { classes.delete(key); }
        } } };
        const win = { ...emitter() };
        const schedule = (fn, ms, interval) => {
            const id = ++serial;
            tasks.set(id, { fn, at: now + ms, interval, win });
            return id;
        };
        Object.assign(win, {
            setTimeout: (fn, ms) => schedule(fn, ms, 0),
            setInterval: (fn, ms) => schedule(fn, ms, ms),
            clearTimeout: id => tasks.delete(id), clearInterval: id => tasks.delete(id),
            localStorage: {
                getItem: key => storage.get(key) || null,
                setItem(key, value) {
                    storage.set(key, value);
                    if (!useChannel) pages.filter(p => p !== win).forEach(p => p.fire('storage', { key, newValue: value }));
                }
            }
        });
        if (useChannel) win.BroadcastChannel = class {
            constructor() { channels.add(this); }
            postMessage(data) { channels.forEach(ch => { if (ch !== this) ch.onmessage?.({ data }); }); }
            close() { channels.delete(this); }
        };
        pages.push(win);
        const controller = focus.start({ dashboard, win, doc });
        return {
            win, doc, controller,
            blurred: () => classes.has('dashboard-background-blurred'),
            focus(value) { focused = value; win.fire(value ? 'focus' : 'blur'); },
            crash() { for (const [id, task] of tasks) if (task.win === win) tasks.delete(id); }
        };
    }
    return { page, advance, tasks };
}

for (const useChannel of [true, false]) {
    const env = environment(useChannel);
    const host = env.page(false);
    const other = env.page(false);
    const dashboard = env.page(true, true);
    assert(host.blurred());
    assert(other.blurred());
    assert(!dashboard.blurred());
    env.advance(2 * focus.LEASE_MS);
    assert(host.blurred(), 'a focused dashboard renews its lease');
    host.focus(true);
    assert(!host.blurred(), 'host focus removes the effect immediately');
    dashboard.focus(false);
    assert(!other.blurred(), 'leaving the dashboard releases every host');
    host.focus(false);
    dashboard.focus(true);
    const late = env.page(false);
    assert(late.blurred(), 'new host reads the active lease');
    dashboard.doc.hidden = true;
    dashboard.doc.fire('visibilitychange');
    assert(!host.blurred(), 'minimizing the dashboard releases the lease');
    dashboard.doc.hidden = false;
    dashboard.focus(true);
    dashboard.crash();
    env.advance(focus.LEASE_MS + 1);
    assert(!host.blurred(), 'a crash cannot leave a permanent blur');
    dashboard.focus(true);
    dashboard.win.fire('pagehide', { persisted: true });
    assert(!host.blurred());
    dashboard.win.fire('pageshow', { persisted: true });
    assert(host.blurred(), 'restoring from browser history resumes focus tracking');
    dashboard.controller.dispose();
    assert(!host.blurred());
    assert.equal(dashboard.win.listenerCount(), 0);
    assert.equal(dashboard.doc.listenerCount(), 0);
    for (const p of [host, other, late]) p.controller.dispose();
    assert.equal(env.tasks.size, 0);
}
