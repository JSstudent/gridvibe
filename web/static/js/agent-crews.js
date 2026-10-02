/* GridVibeAgentCrews — which agent handed a task to which, as a tree and as wires.

   `GET /api/dashboard` carries a top-level `links` list: one row per
   assignment, oldest-handed first, straight from the results store. The store
   keeps what happened, not a picture of it: one pair of panes can hold two
   rounds at once, a worker re-tasked by someone else keeps its old link until
   that is collected, and `set_pane_agent` can hand a task back to the pane that
   asked. The sidebar and the dialog both have to draw the same crews from that,
   so the one reading of it lives here rather than twice in two renderers.

     · **One edge per pair.** Links collapse by (requester, worker); the last in
       snapshot order is the current round. Wires, nodes and DOM keys are named
       by the pair and never by `link_id`, which changes every round, so a
       follow-up redraws a wire's colour and never rebuilds it.
     · **One parent per worker,** the requester of its newest link, and **no
       cycles**: a loop is broken at its earliest-handed link. So the index is
       always a forest and every walk over it terminates.
     · **One drawn state per link.** `linkPhase` is the only place the five
       mockup states are decided; the chip, the wire and the board pill all ask
       it.
     · **Wires are a decoration.** The layer owns one `<svg>` inside the
       scroller and nothing else: a phase change rewrites that SVG and never a
       row, so scroll, focus and the input-target ring stay where they were.

   Two halves, as in `dashboard-sidebar.js`: the model and the geometry are pure
   and run in Node; `createWireLayer` is the only part that touches a DOM, and
   it takes its document and observer from the container it is given.

   Loaded after `session-colour.js` and `agent-identity.js` and before
   `dashboard-dialog.js`, on both pages that host the dashboard. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeAgentCrews = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const PHASES = Object.freeze(['handed', 'working', 'done', 'failed', 'blocked', 'collected', 'ended']);
    const REPORTED_PHASES = Object.freeze(['done', 'failed', 'blocked', 'collected']);

    /* Past this many parallel lanes the gutter would eat the column, so every
       wire shares one trunk and enters its row as a tick. */
    const MAX_LANES = 4;
    /* The innermost lane's distance from the card edge, and each further
       lane's step beyond it. */
    const LANE_INSET = 8;
    const LANE_STEP = 5;
    const CORNER_RADIUS = 4;
    /* A fan wire's control points sit half the horizontal gap out, but never
       closer than this, so two nodes almost touching still read as a curve. */
    const FAN_MIN_PULL = 16;
    /* Kept off the row edges so a wire leaving a row and one entering it at
       the same height never share a lane. */
    const LANE_SPAN_PAD = 3;

    const WAITING_WORDS = Object.freeze({
        crew: 'Waiting on its crew',
        task: 'Standing by for its next task'
    });

    function text(value) {
        return value === null || value === undefined ? '' : String(value);
    }

    function pairKey(link) {
        return `${text(link?.requester_session_id)}>${text(link?.worker_session_id)}`;
    }

    /* The drawn state of one assignment. Collected keeps no colour of its own
       here: a collected blocked or failed report stays amber or red, faint,
       which is `linkTone`'s answer and the stylesheet's business. */
    function linkPhase(link) {
        const state = text(link?.state);
        if (state === 'working') {
            return link?.read ? 'working' : 'handed';
        }
        if (state === 'reported') {
            if (link?.collected) return 'collected';
            const status = text(link?.status);
            return status === 'failed' || status === 'blocked' ? status : 'done';
        }
        return 'ended';
    }

    /* The hue a settled report keeps once it fades: success, warning or
       danger. Empty while nothing has been reported. */
    function linkTone(link) {
        if (text(link?.state) !== 'reported') return '';
        const status = text(link?.status);
        return status === 'failed' || status === 'blocked' ? status : 'done';
    }

    function isReportedPhase(phase) {
        return REPORTED_PHASES.includes(phase);
    }

    function waitingWord(waiting) {
        return WAITING_WORDS[text(waiting)] || '';
    }

    /* Where each pane sits in the dashboard's own list. */
    function listOrder(snapshot) {
        const order = new Map();
        (snapshot?.workspaces || []).forEach(workspace => {
            (workspace?.groups || []).forEach(group => {
                (group?.panes || []).forEach(pane => {
                    const id = text(pane?.session_id);
                    if (id && !order.has(id)) order.set(id, order.size);
                });
            });
        });
        return order;
    }

    /* The crew forest.

       Siblings and crews are ordered by where their panes sit in the
       dashboard's list, which a new round does not change. Snapshot order
       would not hold still: the store drops a collected round before it adds
       the follow-up, so the pair moves to the end of the reading. A worker with
       no row (a ghost) follows the live ones, in the order its pair first
       appeared.

       `byRequester`: requester id → its workers' current links, in that order.
       `byWorker`: worker id → the one link that places it in the tree.
       `rootOf`: every member id → its crew's root.
       `roots`: root ids, in that order.
       `edges`: every placed link, in that order. */
    function indexCrews(snapshot) {
        const rows = Array.isArray(snapshot?.links) ? snapshot.links : [];
        const pairs = new Map();
        rows.forEach((link, position) => {
            const requester = text(link?.requester_session_id);
            const worker = text(link?.worker_session_id);
            if (!requester || !worker || requester === worker) return;
            const key = pairKey(link);
            const seen = pairs.get(key);
            if (seen) {
                seen.link = link;
                seen.last = position;
            } else {
                pairs.set(key, { key, requester, worker, link, first: position, last: position });
            }
        });

        /* One parent per worker: the requester of its newest link. */
        const parentEdge = new Map();
        pairs.forEach(edge => {
            const held = parentEdge.get(edge.worker);
            if (!held || edge.last > held.last) parentEdge.set(edge.worker, edge);
        });

        /* Each worker now has at most one parent, so every loop is a simple
           cycle reachable by walking parents. Break each at its earliest-handed
           edge; the worker below it becomes a root. */
        const settled = new Set();
        parentEdge.forEach((ignored, start) => {
            const trail = [];
            const onTrail = new Set();
            let node = start;
            while (parentEdge.has(node) && !settled.has(node) && !onTrail.has(node)) {
                onTrail.add(node);
                trail.push(node);
                node = parentEdge.get(node).requester;
            }
            if (onTrail.has(node)) {
                const loop = trail.slice(trail.indexOf(node));
                let earliest = parentEdge.get(loop[0]);
                loop.forEach(member => {
                    const edge = parentEdge.get(member);
                    if (edge.last < earliest.last) earliest = edge;
                });
                parentEdge.delete(earliest.worker);
            }
            trail.forEach(member => settled.add(member));
        });

        const listed = listOrder(snapshot);
        const rank = id => (listed.has(id) ? listed.get(id) : Infinity);
        const firstSeen = new Map();
        pairs.forEach(edge => {
            [edge.requester, edge.worker].forEach(id => {
                if (!firstSeen.has(id) || edge.first < firstSeen.get(id)) firstSeen.set(id, edge.first);
            });
        });
        const byPlace = (a, b) => (rank(a) - rank(b)) || (firstSeen.get(a) - firstSeen.get(b));
        const edges = Array.from(parentEdge.values()).sort((a, b) => byPlace(a.worker, b.worker));
        const byRequester = new Map();
        const byWorker = new Map();
        edges.forEach(edge => {
            if (!byRequester.has(edge.requester)) byRequester.set(edge.requester, []);
            byRequester.get(edge.requester).push(edge.link);
            byWorker.set(edge.worker, edge.link);
        });

        const rootOf = new Map();
        const roots = [];
        const findRoot = id => {
            let node = id;
            while (byWorker.has(node)) node = text(byWorker.get(node).requester_session_id);
            return node;
        };
        edges.forEach(edge => {
            [edge.requester, edge.worker].forEach(id => {
                if (rootOf.has(id)) return;
                const crew = findRoot(id);
                rootOf.set(id, crew);
                if (!roots.includes(crew)) roots.push(crew);
            });
        });
        roots.sort(byPlace);

        return { byRequester, byWorker, rootOf, roots, edges: edges.map(edge => edge.link) };
    }

    /* The crew under `rootId`, the root first and then depth-first in sibling
       order: the order a board lays it out and a highlight collects it. */
    function crewMembers(crews, rootId) {
        const start = text(rootId);
        if (!start || crews?.rootOf?.get(start) !== start) return [];
        const out = [];
        const visit = id => {
            out.push(id);
            (crews.byRequester.get(id) || []).forEach(link => visit(text(link.worker_session_id)));
        };
        visit(start);
        return out;
    }

    /* The chip's count: the requester's own workers, one each, by the phase
       of their current round. */
    function crewSummary(crews, requesterId) {
        const links = crews?.byRequester?.get(text(requesterId)) || [];
        return {
            total: links.length,
            reported: links.filter(link => isReportedPhase(linkPhase(link))).length
        };
    }

    /* Requesters a `working` wire should glow for: the orchestrators sitting in
       `wait_for_results` right now. */
    function awaitingRequesters(snapshot) {
        const out = new Set();
        (snapshot?.workspaces || []).forEach(workspace => {
            (workspace?.groups || []).forEach(group => {
                (group?.panes || []).forEach(pane => {
                    if (text(pane?.waiting) === 'crew') out.add(text(pane.session_id));
                });
            });
        });
        return out;
    }

    /* Greedy interval packing. `spans` are `{ lo, hi }`; the answer is one lane
       per span, in input order, shortest spans nearest the cards, so a short
       wire's ticks never cross a longer one. Spans that touch share a point,
       and therefore a lane would join them into one line, so touching counts
       as overlapping. Past `MAX_LANES` every span is put on one trunk. */
    function assignLanes(spans) {
        const list = (Array.isArray(spans) ? spans : []).map((span, index) => {
            const a = Number(span?.lo);
            const b = Number(span?.hi);
            return { index, lo: Math.min(a, b), hi: Math.max(a, b) };
        });
        const order = list.slice().sort((x, y) => ((x.hi - x.lo) - (y.hi - y.lo)) || (x.index - y.index));
        const taken = [];
        const lanes = new Array(list.length).fill(0);
        order.forEach(span => {
            let lane = 0;
            while (taken[lane] && taken[lane].some(other => span.lo <= other.hi && span.hi >= other.lo)) {
                lane += 1;
            }
            if (!taken[lane]) taken[lane] = [];
            taken[lane].push(span);
            lanes[span.index] = lane;
        });
        const trunk = taken.length > MAX_LANES;
        return { lanes: trunk ? lanes.map(() => 0) : lanes, trunk };
    }

    function num(value) {
        return String(Math.round(Number(value) * 10) / 10);
    }

    /* The path one wire draws. `from` and `to` are `{ x, y }` in the layer's
       coordinates.

       `lane`: out of the orchestrator's card edge, left to its lane, down (or
       up) the lane, and back right into the worker's card edge, with a rounded
       corner at both bends.
       `fan`: an S-curve from the parent's right edge to the child's left edge,
       both control points half the gap out. */
    function wirePath(mode, from, to, lane) {
        const x0 = Number(from?.x) || 0;
        const y0 = Number(from?.y) || 0;
        const x1 = Number(to?.x) || 0;
        const y1 = Number(to?.y) || 0;
        if (mode === 'fan') {
            const pull = Math.max(FAN_MIN_PULL, (x1 - x0) / 2);
            return `M${num(x0)} ${num(y0)} C${num(x0 + pull)} ${num(y0)} ${num(x1 - pull)} ${num(y1)} ${num(x1)} ${num(y1)}`;
        }
        const x = Math.min(x0, x1) - LANE_INSET - Math.max(0, Number(lane) || 0) * LANE_STEP;
        const sign = y1 >= y0 ? 1 : -1;
        const r = Math.min(CORNER_RADIUS, Math.abs(y1 - y0) / 2);
        return `M${num(x0)} ${num(y0)} H${num(x + r)} Q${num(x)} ${num(y0)} ${num(x)} ${num(y0 + r * sign)}`
            + ` V${num(y1 - r * sign)} Q${num(x)} ${num(y1)} ${num(x + r)} ${num(y1)} H${num(x1)}`;
    }

    /* The classes one wire wears. Awaited only means anything while working:
       a report already in hand has nothing left to wait on. */
    function wireClasses(link, options) {
        const phase = linkPhase(link);
        const classes = ['dash-wire', `is-${phase}`];
        const tone = linkTone(link);
        if (phase === 'collected' && tone !== 'done') classes.push(`is-tone-${tone}`);
        if (phase === 'working' && options?.awaited) classes.push('is-awaited');
        return classes.join(' ');
    }

    /* The worker-end dot wears the wire's colour, faded tone included. */
    function endClasses(link) {
        const phase = linkPhase(link);
        const tone = linkTone(link);
        return phase === 'collected' && tone !== 'done'
            ? `dash-wire-end is-collected is-tone-${tone}`
            : `dash-wire-end is-${phase}`;
    }

    function escAttr(value) {
        return text(value).replace(/[&<>"']/g, ch => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        })[ch]);
    }

    const SVG_NS = 'http://www.w3.org/2000/svg';
    let layerCount = 0;

    /* The wire layer: one `<svg class="dash-wires">` inside `container`, the
       scrolling element, so a scroll moves the wires with the rows and costs no
       repaint.

       `paint(snapshot)` measures only the rows that are link endpoints (found by
       `data-session-id`, or `options.endpoint(container, id)`) and rewrites the
       SVG only when the markup it would write has changed, so a poll with
       nothing new does not restart a flowing dash. `highlight(crewId)` dims the
       other crews' wires in place; '' clears it. `setPaused(on)` holds every
       wire still while the document is hidden. `dispose()` removes the SVG and
       stops observing.

       Lane mode runs each wire from the edge of the card that holds its row
       (`options.card`, default `.dash-session`); fan mode runs from the
       endpoint's own edges. */
    function createWireLayer(options) {
        const container = options?.container;
        const mode = options?.mode === 'fan' ? 'fan' : 'lane';
        const cardSelector = options?.card === undefined ? '.dash-session' : options.card;
        const doc = container?.ownerDocument || null;
        const view = doc?.defaultView || null;
        const prefix = `dash-wire-${++layerCount}`;
        const findEndpoint = typeof options?.endpoint === 'function'
            ? id => options.endpoint(container, id)
            : id => {
                const escape = view?.CSS?.escape || (value => value.replace(/["\\]/g, '\\$&'));
                return container.querySelector(`[data-session-id="${escape(id)}"]`);
            };

        let svg = null;
        /* What the SVG holds; null forces the next paint to write. */
        let markup = null;
        const pairIds = new Map();
        let lastSnapshot = null;
        let highlighted = '';
        let paused = false;
        let disposed = false;
        let observer = null;

        function ensureSvg() {
            if (!svg) {
                svg = doc.createElementNS(SVG_NS, 'svg');
                svg.setAttribute('class', 'dash-wires');
                svg.setAttribute('aria-hidden', 'true');
                svg.setAttribute('focusable', 'false');
            }
            /* A tree paint replaces the scroller's contents, and this with it. */
            if (svg.parentNode !== container) {
                container.insertBefore(svg, container.firstChild);
                markup = null;
            }
            container.classList?.add('has-dash-wires');
            svg.classList?.toggle('dash-wires-paused', paused);
            return svg;
        }

        function applyHighlight() {
            if (!svg) return;
            svg.querySelectorAll('[data-crew]').forEach(group => {
                const dim = Boolean(highlighted) && group.getAttribute('data-crew') !== highlighted;
                group.classList.toggle('is-dimmed', dim);
            });
        }

        function measure(crews, awaited) {
            const box = container.getBoundingClientRect();
            const offsetX = (container.scrollLeft || 0) - box.left;
            const offsetY = (container.scrollTop || 0) - box.top;
            const rel = rect => ({
                left: rect.left + offsetX,
                right: rect.right + offsetX,
                cy: (rect.top + rect.bottom) / 2 + offsetY
            });
            const rows = new Map();
            const locate = id => {
                if (rows.has(id)) return rows.get(id);
                const el = findEndpoint(id);
                let found = null;
                if (el) {
                    const row = rel(el.getBoundingClientRect());
                    const card = mode === 'lane' && cardSelector ? el.closest?.(cardSelector) : null;
                    const edge = card ? rel(card.getBoundingClientRect()) : row;
                    found = { left: edge.left, right: row.right, rowLeft: row.left, cy: row.cy };
                }
                rows.set(id, found);
                return found;
            };
            const wires = [];
            crews.edges.forEach(link => {
                const requester = text(link.requester_session_id);
                const a = locate(requester);
                const b = locate(text(link.worker_session_id));
                if (!a || !b) return;
                wires.push({
                    link,
                    crew: crews.rootOf.get(requester) || requester,
                    awaited: awaited.has(requester),
                    from: mode === 'fan' ? { x: a.right, y: a.cy } : { x: a.left, y: a.cy },
                    to: mode === 'fan' ? { x: b.rowLeft, y: b.cy } : { x: b.left, y: b.cy }
                });
            });
            return wires;
        }

        /* A path's id names its pair for the layer's lifetime, so a wire keeps
           its id however the list around it moves. */
        function pathId(link) {
            const key = pairKey(link);
            if (!pairIds.has(key)) pairIds.set(key, `${prefix}-${pairIds.size + 1}`);
            return pairIds.get(key);
        }

        function render(wires) {
            if (mode === 'lane') {
                const packed = assignLanes(wires.map(wire => ({
                    lo: Math.min(wire.from.y, wire.to.y) - LANE_SPAN_PAD,
                    hi: Math.max(wire.from.y, wire.to.y) + LANE_SPAN_PAD
                })));
                wires.forEach((wire, index) => { wire.lane = packed.lanes[index]; });
            }
            let paths = '';
            let pulses = '';
            wires.forEach(wire => {
                const phase = linkPhase(wire.link);
                const id = pathId(wire.link);
                const d = wirePath(mode, wire.from, wire.to, wire.lane || 0);
                paths += `<g class="dash-wire-group" data-crew="${escAttr(wire.crew)}">`
                    + `<path id="${id}" class="${wireClasses(wire.link, { awaited: wire.awaited })}" d="${d}"/>`
                    + `<circle class="dash-wire-end is-from" r="2.6" cx="${num(wire.from.x)}" cy="${num(wire.from.y)}"/>`
                    + `<circle class="${endClasses(wire.link)}" r="2.6" cx="${num(wire.to.x)}" cy="${num(wire.to.y)}"/>`
                    + '</g>';
                if (phase === 'done') {
                    pulses += `<circle class="dash-wire-pulse" data-crew="${escAttr(wire.crew)}" r="3.2">`
                        + '<animateMotion dur="2.4s" repeatCount="indefinite" keyPoints="1;0;0" keyTimes="0;0.55;1" calcMode="linear">'
                        + `<mpath href="#${id}"/></animateMotion></circle>`;
                }
            });
            return paths + pulses;
        }

        function paint(snapshot) {
            if (disposed || !container || !doc) return;
            lastSnapshot = snapshot;
            const crews = indexCrews(snapshot);
            const layer = ensureSvg();
            const next = crews.edges.length ? render(measure(crews, awaitingRequesters(snapshot))) : '';
            if (next !== markup) {
                layer.innerHTML = next;
                markup = next;
                applyHighlight();
                if (paused) layer.pauseAnimations?.();
            }
        }

        function highlight(crewId) {
            highlighted = text(crewId);
            applyHighlight();
        }

        function setPaused(on) {
            paused = Boolean(on);
            if (!svg) return;
            svg.classList?.toggle('dash-wires-paused', paused);
            if (paused) svg.pauseAnimations?.();
            else svg.unpauseAnimations?.();
        }

        function dispose() {
            disposed = true;
            observer?.disconnect();
            observer = null;
            svg?.parentNode?.removeChild(svg);
            container?.classList?.remove('has-dash-wires');
            svg = null;
            markup = null;
            lastSnapshot = null;
        }

        /* A resize moves every row, so it re-measures from the last reading. It
           covers the sidebar drag as well as the window. */
        const Observer = view?.ResizeObserver;
        if (container && typeof Observer === 'function') {
            observer = new Observer(() => {
                if (lastSnapshot) paint(lastSnapshot);
            });
            observer.observe(container);
        }

        return { paint, highlight, setPaused, dispose };
    }

    return {
        PHASES,
        MAX_LANES,
        LANE_INSET,
        LANE_STEP,
        CORNER_RADIUS,
        WAITING_WORDS,
        pairKey,
        linkPhase,
        linkTone,
        isReportedPhase,
        waitingWord,
        indexCrews,
        crewMembers,
        crewSummary,
        awaitingRequesters,
        assignLanes,
        wirePath,
        wireClasses,
        endClasses,
        createWireLayer
    };
}));
