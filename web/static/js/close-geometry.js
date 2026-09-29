/* GridVibeCloseGeometry — close one or more panes without rebuilding the grid.

   The workspace page owns capture and paint because only it can see live DOM
   geometry. Everything between those points lives here: the exact absorb rule
   used by the pane X button, sequential reduction for rapid closes, and the
   group-keyed pending records consumed by a later refresh. DOM-free and
   require()-able so Node tests execute the policy directly. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeCloseGeometry = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    function cloneRect(rect) {
        const cloned = {
            ...rect,
            x: Number(rect?.x),
            y: Number(rect?.y),
            w: Number(rect?.w),
            h: Number(rect?.h),
        };
        if (rect?.parentRect) cloned.parentRect = cloneRect(rect.parentRect);
        if (Array.isArray(rect?.ancestors)) {
            cloned.ancestors = rect.ancestors.map(ancestor => ({
                ...ancestor,
                parentRect: ancestor.parentRect ? cloneRect(ancestor.parentRect) : null,
                branchRect: ancestor.branchRect ? cloneRect(ancestor.branchRect) : null,
            }));
        }
        return cloned;
    }

    function cloneWeights(weights) {
        return Array.isArray(weights) ? weights.map(value => Number(value)) : null;
    }

    function isRect(rect) {
        return ['x', 'y', 'w', 'h'].every(key => Number.isInteger(Number(rect?.[key])))
            && Number(rect.x) > 0 && Number(rect.y) > 0
            && Number(rect.w) > 0 && Number(rect.h) > 0;
    }

    function normalizeModel(snapshot) {
        if (!snapshot || !Array.isArray(snapshot.entries)) return null;
        const seen = new Set();
        const entries = [];
        for (let index = 0; index < snapshot.entries.length; index += 1) {
            const entry = snapshot.entries[index] || {};
            const sessionId = String(entry.sessionId || '');
            if (!sessionId || seen.has(sessionId) || !isRect(entry.rect)) return null;
            seen.add(sessionId);
            entries.push({
                ...entry,
                sessionId,
                visualIndex: Number.isInteger(Number(entry.visualIndex))
                    ? Number(entry.visualIndex) : index,
                rect: cloneRect(entry.rect),
            });
        }
        return {
            entries,
            originalSplitSlotCount: Math.max(
                0, Number(snapshot.originalSplitSlotCount || entries.length) || 0
            ),
            columnWeights: cloneWeights(snapshot.columnWeights),
            rowWeights: cloneWeights(snapshot.rowWeights),
        };
    }

    function splitRectArea(rect) {
        return Math.max(0, Number(rect?.w || 0)) * Math.max(0, Number(rect?.h || 0));
    }

    function splitRectUnion(left, right) {
        const x1 = Math.min(left.x, right.x);
        const y1 = Math.min(left.y, right.y);
        const x2 = Math.max(left.x + left.w, right.x + right.w);
        const y2 = Math.max(left.y + left.h, right.y + right.h);
        return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
    }

    function splitRectsOverlap(left, right) {
        return left.x < right.x + right.w
            && left.x + left.w > right.x
            && left.y < right.y + right.h
            && left.y + left.h > right.y;
    }

    function sharedBorderLength(left, right) {
        if (!left || !right) return 0;
        let longest = 0;
        if (left.x + left.w === right.x || right.x + right.w === left.x) {
            longest = Math.max(
                longest,
                Math.min(left.y + left.h, right.y + right.h) - Math.max(left.y, right.y)
            );
        }
        if (left.y + left.h === right.y || right.y + right.h === left.y) {
            longest = Math.max(
                longest,
                Math.min(left.x + left.w, right.x + right.w) - Math.max(left.x, right.x)
            );
        }
        return Math.max(0, longest);
    }

    function canAbsorbClosedRect(candidateRect, closedRect, otherRects) {
        const union = splitRectUnion(candidateRect, closedRect);
        if (splitRectArea(union) !== splitRectArea(candidateRect) + splitRectArea(closedRect)) {
            return false;
        }
        return !otherRects.some(rect => splitRectsOverlap(union, rect));
    }

    function coveredIntervalLength(intervals) {
        const sorted = intervals
            .map(interval => ({
                start: Math.min(interval.start, interval.end),
                end: Math.max(interval.start, interval.end),
            }))
            .filter(interval => interval.end > interval.start)
            .sort((left, right) => left.start - right.start || left.end - right.end);
        let covered = 0;
        let cursor = null;
        sorted.forEach(interval => {
            if (!cursor || interval.start > cursor.end) {
                covered += interval.end - interval.start;
                cursor = { ...interval };
                return;
            }
            if (interval.end > cursor.end) {
                covered += interval.end - cursor.end;
                cursor.end = interval.end;
            }
        });
        return covered;
    }

    function terminalCloseContacts(closedRect, entry) {
        const rect = entry.rect;
        const contacts = [];
        const yStart = Math.max(closedRect.y, rect.y);
        const yEnd = Math.min(closedRect.y + closedRect.h, rect.y + rect.h);
        const xStart = Math.max(closedRect.x, rect.x);
        const xEnd = Math.min(closedRect.x + closedRect.w, rect.x + rect.w);
        if (rect.x + rect.w === closedRect.x && yEnd > yStart) {
            contacts.push({ ...entry, side: 'left', sharedBorder: yEnd - yStart, start: yStart, end: yEnd });
        }
        if (rect.x === closedRect.x + closedRect.w && yEnd > yStart) {
            contacts.push({ ...entry, side: 'right', sharedBorder: yEnd - yStart, start: yStart, end: yEnd });
        }
        if (rect.y + rect.h === closedRect.y && xEnd > xStart) {
            contacts.push({ ...entry, side: 'top', sharedBorder: xEnd - xStart, start: xStart, end: xEnd });
        }
        if (rect.y === closedRect.y + closedRect.h && xEnd > xStart) {
            contacts.push({ ...entry, side: 'bottom', sharedBorder: xEnd - xStart, start: xStart, end: xEnd });
        }
        return contacts;
    }

    function findTerminalCloseNeighbor(closedRect, candidates) {
        return candidates
            .map(candidate => ({
                ...candidate,
                sharedBorder: sharedBorderLength(closedRect, candidate.rect),
            }))
            .filter(candidate => candidate.sharedBorder > 0)
            .sort((left, right) => (
                right.sharedBorder - left.sharedBorder
                || left.visualIndex - right.visualIndex
            ))[0] || null;
    }

    function terminalCloseSideGroups(closedRect, entries) {
        const sideLengths = {
            left: closedRect.h,
            right: closedRect.h,
            top: closedRect.w,
            bottom: closedRect.w,
        };
        const groupsBySide = new Map();
        entries.flatMap(entry => terminalCloseContacts(closedRect, entry)).forEach(contact => {
            if (!groupsBySide.has(contact.side)) groupsBySide.set(contact.side, []);
            groupsBySide.get(contact.side).push(contact);
        });
        return Array.from(groupsBySide.entries()).map(([side, contacts]) => ({
            side,
            entries: contacts,
            coverage: coveredIntervalLength(contacts),
            sideLength: sideLengths[side],
            totalSharedBorder: contacts.reduce((total, contact) => total + contact.sharedBorder, 0),
            firstVisualIndex: Math.min(...contacts.map(contact => contact.visualIndex)),
        }));
    }

    function expandRectIntoClosedSide(rect, closedRect, side) {
        if (side === 'left') {
            return { ...rect, w: (closedRect.x + closedRect.w) - rect.x, ancestors: [] };
        }
        if (side === 'right') {
            return { ...rect, x: closedRect.x, w: (rect.x + rect.w) - closedRect.x, ancestors: [] };
        }
        if (side === 'top') {
            return { ...rect, h: (closedRect.y + closedRect.h) - rect.y, ancestors: [] };
        }
        if (side === 'bottom') {
            return { ...rect, y: closedRect.y, h: (rect.y + rect.h) - closedRect.y, ancestors: [] };
        }
        return cloneRect(rect);
    }

    function terminalCloseRectsForExpandingContacts(plan, side, contactsToExpand) {
        const expandingSessionIds = new Set(contactsToExpand.map(entry => entry.sessionId));
        const nextEntries = plan.remainingEntries.map(entry => ({
            ...entry,
            rect: expandingSessionIds.has(entry.sessionId)
                ? expandRectIntoClosedSide(entry.rect, plan.closedRect, side)
                : cloneRect(entry.rect),
        }));
        for (let leftIndex = 0; leftIndex < nextEntries.length; leftIndex += 1) {
            for (let rightIndex = leftIndex + 1; rightIndex < nextEntries.length; rightIndex += 1) {
                if (splitRectsOverlap(nextEntries[leftIndex].rect, nextEntries[rightIndex].rect)) {
                    return null;
                }
            }
        }
        const previousArea = plan.remainingEntries.reduce(
            (total, entry) => total + splitRectArea(entry.rect), 0
        );
        const nextArea = nextEntries.reduce((total, entry) => total + splitRectArea(entry.rect), 0);
        if (nextArea !== previousArea + splitRectArea(plan.closedRect)) return null;
        return Object.fromEntries(nextEntries.map(entry => [entry.sessionId, cloneRect(entry.rect)]));
    }

    function buildTerminalCloseRectsForSideGroup(plan, sideGroup) {
        if (!sideGroup || sideGroup.coverage < sideGroup.sideLength) return null;
        const rankedContacts = [...sideGroup.entries].sort((left, right) => (
            right.sharedBorder - left.sharedBorder
            || left.visualIndex - right.visualIndex
        ));
        const singleContact = rankedContacts[0];
        if (singleContact && sideGroup.entries.length > 1) {
            const single = terminalCloseRectsForExpandingContacts(
                plan, sideGroup.side, [singleContact]
            );
            if (single) return single;
        }
        return terminalCloseRectsForExpandingContacts(plan, sideGroup.side, sideGroup.entries);
    }

    function buildTerminalCloseRectsBySessionId(plan) {
        const neighbor = findTerminalCloseNeighbor(plan.closedRect, plan.remainingEntries);
        if (neighbor) {
            const otherRects = plan.remainingEntries
                .filter(entry => entry.sessionId !== neighbor.sessionId)
                .map(entry => entry.rect);
            if (canAbsorbClosedRect(neighbor.rect, plan.closedRect, otherRects)) {
                return Object.fromEntries(plan.remainingEntries.map(entry => [
                    entry.sessionId,
                    cloneRect(entry.sessionId === neighbor.sessionId
                        ? { ...entry.rect, ...splitRectUnion(entry.rect, plan.closedRect), ancestors: [] }
                        : entry.rect),
                ]));
            }
        }
        const sideGroups = terminalCloseSideGroups(plan.closedRect, plan.remainingEntries)
            .map(sideGroup => ({
                ...sideGroup,
                rectsBySessionId: buildTerminalCloseRectsForSideGroup(plan, sideGroup),
            }))
            .filter(sideGroup => sideGroup.rectsBySessionId);
        sideGroups.sort((left, right) => (
            right.totalSharedBorder - left.totalSharedBorder
            || left.entries.length - right.entries.length
            || left.firstVisualIndex - right.firstVisualIndex
        ));
        return sideGroups[0]?.rectsBySessionId || null;
    }

    function reduceCloseGeometry(snapshot, closedSessionIds) {
        let model = normalizeModel(snapshot);
        if (!model) {
            return { ok: false, status: 'refused', reason: 'invalid-model', model: null };
        }
        const requested = Array.isArray(closedSessionIds)
            ? closedSessionIds.map(value => String(value || '')).filter(Boolean)
            : [];
        const appliedClosedSessionIds = [];
        const ignoredClosedSessionIds = [];
        for (const sessionId of requested) {
            const closedIndex = model.entries.findIndex(entry => entry.sessionId === sessionId);
            if (closedIndex < 0) {
                ignoredClosedSessionIds.push(sessionId);
                continue;
            }
            if (model.entries.length === 1) {
                model = { ...model, entries: [] };
                appliedClosedSessionIds.push(sessionId);
                continue;
            }
            const closed = model.entries[closedIndex];
            const remainingEntries = model.entries
                .filter((_entry, index) => index !== closedIndex)
                .map(entry => ({ ...entry, rect: cloneRect(entry.rect) }));
            const rectsBySessionId = buildTerminalCloseRectsBySessionId({
                closedRect: closed.rect,
                remainingEntries,
            });
            if (!rectsBySessionId) {
                return {
                    ok: false,
                    status: 'refused',
                    reason: 'no-absorber',
                    failedSessionId: sessionId,
                    model,
                    appliedClosedSessionIds,
                    ignoredClosedSessionIds,
                };
            }
            model = {
                ...model,
                entries: remainingEntries.map((entry, visualIndex) => ({
                    ...entry,
                    visualIndex,
                    rect: cloneRect(rectsBySessionId[entry.sessionId]),
                })),
            };
            appliedClosedSessionIds.push(sessionId);
        }
        return {
            ok: true,
            status: model.entries.length ? 'restore' : 'last-pane',
            model,
            appliedClosedSessionIds,
            ignoredClosedSessionIds,
        };
    }

    function closeResultApplied(result, sessionId) {
        const normalizedSessionId = String(sessionId || '');
        return Boolean(
            result?.ok
            && normalizedSessionId
            && result.appliedClosedSessionIds?.includes(normalizedSessionId)
        );
    }

    function createCoordinator() {
        const pending = new Map();
        const generations = new Map();
        let epoch = 0;

        function nextGeneration(groupId) {
            const generation = (generations.get(groupId) || 0) + 1;
            generations.set(groupId, generation);
            epoch += 1;
            return generation;
        }

        return {
            stage({ groupId, snapshot, closedSessionIds } = {}) {
                const normalizedGroupId = String(groupId || '');
                if (!normalizedGroupId) {
                    return { ok: false, status: 'refused', reason: 'missing-group' };
                }
                const previous = pending.get(normalizedGroupId) || null;
                const result = reduceCloseGeometry(previous?.model || snapshot, closedSessionIds);
                if (!result.ok) return result;
                if (result.appliedClosedSessionIds.length === 0) {
                    return { ...result, record: previous };
                }
                const generation = nextGeneration(normalizedGroupId);
                if (result.status === 'last-pane') {
                    pending.delete(normalizedGroupId);
                    return { ...result, generation, record: null };
                }
                const mergedClientStateBySessionId = {
                    ...(previous?.clientStateBySessionId || {}),
                    ...(snapshot?.clientStateBySessionId || {}),
                };
                const survivingSessionIds = new Set(
                    result.model.entries.map(entry => entry.sessionId)
                );
                const clientStateBySessionId = Object.fromEntries(
                    Object.entries(mergedClientStateBySessionId).filter(
                        ([sessionId]) => survivingSessionIds.has(sessionId)
                    )
                );
                const record = {
                    groupId: normalizedGroupId,
                    generation,
                    model: result.model,
                    clientStateBySessionId,
                };
                pending.set(normalizedGroupId, record);
                return { ...result, generation, record };
            },

            invalidate(groupId) {
                const normalizedGroupId = String(groupId || '');
                if (!normalizedGroupId) return 0;
                pending.delete(normalizedGroupId);
                return nextGeneration(normalizedGroupId);
            },

            peek(groupId) {
                return pending.get(String(groupId || '')) || null;
            },

            consume(groupId, generation) {
                const normalizedGroupId = String(groupId || '');
                const record = pending.get(normalizedGroupId);
                if (!record || record.generation !== Number(generation)) return null;
                pending.delete(normalizedGroupId);
                return record;
            },

            epoch(groupId = '') {
                const normalizedGroupId = String(groupId || '');
                return normalizedGroupId
                    ? (generations.get(normalizedGroupId) || 0)
                    : epoch;
            },

            pendingGroups() {
                return Array.from(pending.keys());
            },
        };
    }

    function planCloseDelta(message, groupIdForSession) {
        const closedGroupIds = Array.from(new Set(
            (Array.isArray(message?.closed_group_ids) ? message.closed_group_ids : [])
                .map(value => String(value || '')).filter(Boolean)
        ));
        const fullyClosed = new Set(closedGroupIds);
        const statedGroupId = String(message?.group_id || '');
        const byGroup = new Map();
        (Array.isArray(message?.closed_session_ids) ? message.closed_session_ids : [])
            .map(value => String(value || '')).filter(Boolean)
            .forEach(sessionId => {
                const groupId = statedGroupId || String(groupIdForSession?.(sessionId) || '');
                if (!groupId || fullyClosed.has(groupId)) return;
                if (!byGroup.has(groupId)) byGroup.set(groupId, []);
                const ids = byGroup.get(groupId);
                if (!ids.includes(sessionId)) ids.push(sessionId);
            });
        return {
            closedGroupIds,
            groups: Array.from(byGroup, ([groupId, closedSessionIds]) => ({
                groupId,
                closedSessionIds,
            })),
        };
    }

    return {
        buildTerminalCloseRectsBySessionId,
        closeResultApplied,
        createCoordinator,
        planCloseDelta,
        reduceCloseGeometry,
        sharedBorderLength,
        splitRectArea,
        splitRectsOverlap,
    };
}));
