# Session Tab Numbering Research

## Todo Item / Goal

Todo 1: number session tabs like `1`, `2`, `3`, ... so the app can support a keybind option for switching tabs, for example `Alt+X` where `X` is the tab number.

The smallest useful feature is visible, stable numbering for the currently displayed session-tab order. The keybind can then use the same number-to-tab mapping.

## Implementation Status

Implemented on 2026-07-01 using the recommended safest / simplest approach:

- `templates/terminals.html` derives visible tab numbers from the current frontend `sessionGroups` order.
- `+ New Session` is excluded from numbering.
- Session tabs expose `Alt+1` through `Alt+9` shortcut hints in the title and accessible label.
- A document-level `Alt+1` through `Alt+9` keydown handler switches to `sessionGroups[number - 1]`.
- The shortcut handler ignores editable targets, including inputs, textareas, selects, contenteditable elements, and voice push-to-talk keybind fields.
- No backend `tab_number` field, persisted tab numbers, or settings-backed shortcut system was added.
- `tests/test_api.py` includes HTML assertions for the tab number markup, helper, and safe shortcut listener.

## Current Repo Observations

- `templates/terminals.html:1025` renders a single tab strip container: `<div class="session-tabs" id="sessionTabs"></div>`.
- `templates/terminals.html:1123-1124` keeps tab state in frontend globals: `activeGroupId` and `sessionGroups`.
- `templates/terminals.html:1395-1451` builds the tab strip in `renderSessionTabs()`. It prepends a `+ New Session` button, then loops over `sessionGroups` to render each launched session group.
- `templates/terminals.html:1417-1424` already makes each tab button switch groups by calling `switchGroup(group.group_id)`.
- `templates/terminals.html:1453-1521` and `1605-1620` support drag reorder and persist the reordered group ids through `/api/session-groups/order`.
- `templates/terminals.html:3921-3955` loads `/api/session-groups`, updates `sessionGroups`, chooses the active/newest group, syncs the URL, and calls `renderSessionTabs()`.
- `templates/terminals.html:4083-4092` defines `switchGroup(groupId)`, which updates `activeGroupId`, syncs the URL, redraws tabs, and runs `initialLoad()`.
- `sessions/manager.py:71-92` defines `SessionGroup` with `display_order` and `to_dict()`.
- `sessions/manager.py:264-270` returns groups sorted by `(display_order, created_at)`.
- `sessions/manager.py:272-295` persists a new display order in memory.
- `web/api.py:2749-2765` exposes `/api/session-groups` and `/api/session-groups/order` for reading and reordering session tabs.
- There is no general keybinding settings model today. `web/api.py:256-319` only exposes editable app config for appearance and voice input. The existing shortcut parsing is local to voice push-to-talk in `templates/terminals.html:3550-3583` and its keydown listener at `3631-3652`.

## Implementation Possibilities

1. Frontend-only numbering from `sessionGroups` order.
   - Add the number in `renderSessionTabs()` using the `forEach((group, index) => ...)` index.
   - Add a small `.session-tab-number` span before `.session-tab-label`.
   - Later, add one document-level keydown handler that maps `Alt+1` to `sessionGroups[0]`, `Alt+2` to `sessionGroups[1]`, etc.
   - Tradeoff: numbering is per currently loaded browser tab/window and follows the already-persisted group order. This is probably what users expect for tab switching.

2. Backend-provided tab number.
   - Add a `tab_number` field to `SessionGroup.to_dict()` or the `/api/session-groups` response.
   - Tradeoff: duplicates derived UI state on the server. The number still changes after reorder, so the backend would need to compute response-order indexes anyway. More code for little gain.

3. Persist explicit per-group tab numbers.
   - Store a tab number on `SessionGroup`.
   - Tradeoff: not recommended. Tab numbers are positional, not identity. Persisting them risks stale or duplicate numbers after close, create, and drag reorder.

4. Full settings-backed shortcut system first.
   - Add a `keyboard_shortcuts` config section, launcher UI controls, validation, API normalization, and terminal-page consumption.
   - Tradeoff: useful eventually, but too broad for the first tab-numbering change. It touches settings, config persistence, validation, tests, and UX choices.

## Recommended Safest / Simplest Approach

Implement tab numbers as frontend-derived display state in `templates/terminals.html`, based on the current `sessionGroups` order after the settings button.

This matches existing behavior because `sessionGroups` is already sorted by backend display order and updated after drag reorder. The number should be `index + 1` for each real session tab, excluding `+ New Session`.

For the first keybind implementation, add `Alt+1` through `Alt+9` directly in `templates/terminals.html` behind a small helper, still using `sessionGroups[index]`. If a settings option is needed later, introduce it as a separate config value that enables/disables or changes the modifier, without changing the numbering model.

## Implementation Outline

1. Update tab markup in `renderSessionTabs()`.
   - Change `sessionGroups.forEach(group => { ... })` to `sessionGroups.forEach((group, index) => { ... })`.
   - Create `const number = document.createElement('span')`.
   - Set `number.className = 'session-tab-number'`.
   - Set `number.textContent = String(index + 1)`.
   - Append it before `.session-tab-label`.
   - Update the tab `aria-label` and/or `title` to include the shortcut hint, for example `1. Session Name` or `Alt+1: Session Name`.

2. Add minimal CSS near the existing session tab styles.
   - `.session-tab-number` should be compact and fixed-size enough to avoid tab label jitter.
   - Keep it low contrast but readable, and ensure it works in light theme.
   - Do not change the `.session-tab.settings` button; it should not receive a number.

3. Add a reusable index-to-group helper.
   - Example: `function getSessionGroupByNumber(number) { return sessionGroups[number - 1] || null; }`.
   - This avoids duplicating array indexing in future keybind/settings work.

4. Add optional first-pass keybind behavior.
   - Add a document-level `keydown` handler near other global key handlers.
   - Ignore events when focus is in editable inputs, textareas, selects, contenteditable elements, or `.voice-ptt-keybind`.
   - Match `event.altKey` with no `ctrlKey`, `metaKey`, or `shiftKey`.
   - Convert `event.key` to a number. Support `1-9`; consider whether `0` should mean tab 10 only if more than 9 tabs can exist.
   - If a target group exists and differs from `activeGroupId`, `event.preventDefault()` and call `switchGroup(target.group_id)`.

5. Add settings later only if required.
   - Add a `keyboard_shortcuts` or `session_tabs` section to `default_config.json`.
   - Extend `_public_app_config()` and `_normalize_app_config_update()` in `web/api.py`.
   - Add launcher UI in `templates/index.html` under app settings.
   - The terminal page can fetch the setting from `/api/app-config` or render it into the template like voice settings.

## Risks / Tests To Consider

- Reorder correctness: after dragging tabs, numbers must immediately update and keybind mapping must follow the new order.
- Active group changes: `switchGroup()` calls `renderSessionTabs()`, so numbers should stay stable while only active styling changes.
- Closed tabs: after closing a group and reloading `sessionGroups`, numbers should compact without gaps.
- `+ New Session` must not count as tab `1`.
- Keyboard conflicts: browsers and WebView may reserve some `Alt+number` combinations. Verify in pywebview/WebView2 and normal browser mode.
- Terminal focus: xterm may consume or expect modifier keys. A document-level capture may need testing while a terminal has focus.
- Text entry safety: do not trigger tab switching while editing launcher/modal inputs, voice keybind fields, or other editable controls.
- Scale limit: `max_sessions` is currently `8` in config, but session groups can exceed terminal panes over time. Decide whether to support only `1-9`, use `0` as `10`, or leave tabs above 9 mouse-only.
- Tests: add `tests/test_api.py` HTML assertions for `.session-tab-number`, `sessionGroups.forEach((group, index)`, and the keydown helper/listener if implemented. Manual browser testing should cover create, reorder, close, and `Alt+number` switching.
