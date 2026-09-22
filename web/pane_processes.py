"""What a pane is *actually* running, read from the OS's own process table.

GridVibe otherwise learns what a pane runs from what the reader typed at its
prompt, and that reading is a reconstruction rather than an observation:
``_track_current_terminal_agent_input`` strips escape sequences before it
rebuilds the line, so a command recalled from history (Up, Enter), completed
with Tab, or edited in place with the arrow keys is a command it never saw. The
pane then runs an agent nothing in GridVibe knows about -- no name in its
header, no row on the dashboard -- and because only a submitted command ever
promotes a pane, it never finds out. Waiting does not help; relaunching the pane
is the only way back.

This is the other reading, and the stronger one: the OS knows which processes
are running under the pane's own shell, whoever composed the command line and
however it reached the shell. It answers for history, completion, aliases,
wrappers and shell functions alike, and it can correct a pane that is already
labelled wrong -- which no reading of the input stream can do, because the
moment it would have read has passed.

Four properties keep it affordable and honest:

* **One table answers for every pane.** A snapshot costs single-digit
  milliseconds and each walk after it is dictionary lookups, so a reconcile
  pass takes one table and shares it across every pane rather than asking the
  OS once per pane.
* **The walk is scoped to the pane's own shell.** Agents run outside GridVibe
  too -- a desktop app ships one, and a reader has their own terminals -- so a
  machine-wide search for a binary name would find those and label a pane with
  somebody else's process. Only a descendant of *this pane's* shell counts.
* **The walk is bounded.** Depth and visited-node ceilings, so a deep or
  cyclic parent chain (pids are reused, and a reused pid can close a loop)
  costs a fixed amount rather than hanging the caller that asked.
* **It never answers for a pane it cannot see.** A WSL pane's processes live in
  another kernel's table and a remote pane's on another machine, so neither is
  answered here at all. An empty answer is "no idea", never "no agent" -- which
  is why the caller may only *promote* on it and must leave retirement to the
  pane's own prompt.

Pure functions over a pid table with no imports from ``web``, so
``tests/test_pane_processes.py`` executes the walk against a table of its own
rather than against whatever happens to be running on the machine.
"""

import os
import re
from typing import Dict, Iterable, List, Optional, Tuple

#: How far below a pane's shell an agent may be found. Codex is two levels down
#: on Windows (``cmd.exe`` -> ``node.exe`` -> ``codex.exe``) and a wrapper
#: script can add more, so this is generous -- but finite, because the table is
#: a parent map and a reused pid can make one cyclic.
PANE_PROCESS_MAX_DEPTH = 8

#: How many processes one pane's walk may visit. An agent sits within a few
#: nodes of the shell that started it; a walk that has visited this many has
#: found something other than what it was looking for.
PANE_PROCESS_MAX_VISITED = 512

#: How many processes a table may hold. Well past any real machine, and it
#: stops a pathological ``/proc`` from being read into memory unbounded.
PANE_PROCESS_MAX_ENTRIES = 8192

#: Suffixes Windows hangs off an executable name, dropped so ``codex.exe`` and
#: ``codex`` are one answer. The same normalization the submitted-command
#: reader applies, for the same reason.
_EXECUTABLE_SUFFIX_PATTERN = re.compile(r"\.(?:bat|cmd|com|exe|ps1)$")

#: `pid (comm) state ppid`, where `comm` may itself contain spaces and
#: parentheses -- so the split is anchored on the *last* `)`, never the first.
_POSIX_STAT_TAIL_PATTERN = re.compile(r"^\)\s+\S+\s+(\d+)")


def normalize_binary(name: object) -> str:
    """Return one executable name in the one spelling two can be compared in."""
    candidate = str(name or "").strip()
    if not candidate:
        return ""
    candidate = re.split(r"[/\\]", candidate)[-1].lower()
    return _EXECUTABLE_SUFFIX_PATTERN.sub("", candidate)


def process_table() -> Dict[int, Tuple[int, str]]:
    """Return ``{pid: (parent pid, executable name)}``, or ``{}`` for no answer.

    ``{}`` is what every failure returns -- an OS that will not say, a platform
    with no table to read, a snapshot that could not be taken. The caller's rule
    is the same for all of them: an answer that is not there promotes nothing.
    """
    try:
        if os.name == "nt":
            return _windows_process_table()
        return _posix_process_table()
    except Exception:
        # Reading the process table is an observation, and an observation that
        # fails costs its caller nothing. There is no partial answer worth
        # raising into a pump thread or a watcher loop over.
        return {}


def _windows_process_table() -> Dict[int, Tuple[int, str]]:
    """Walk the toolhelp32 snapshot, which needs no third-party dependency.

    GridVibe does not carry ``psutil`` -- the local cwd reader says so where it
    gives up on Windows for the same reason -- and this needs three calls and a
    structure rather than a package.
    """
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    snapshot_all_processes = 0x00000002
    invalid_handle = ctypes.c_void_p(-1).value

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.CreateToolhelp32Snapshot(snapshot_all_processes, 0)
    if not handle or handle == invalid_handle:
        return {}

    table: Dict[int, Tuple[int, str]] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        more = kernel32.Process32FirstW(handle, ctypes.byref(entry))
        while more and len(table) < PANE_PROCESS_MAX_ENTRIES:
            table[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID),
                normalize_binary(entry.szExeFile),
            )
            more = kernel32.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(handle)
    return table


def _posix_process_table() -> Dict[int, Tuple[int, str]]:
    """Read `/proc/<pid>/stat`, skipping whatever exits while it is being read."""
    table: Dict[int, Tuple[int, str]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return {}
    for entry in entries:
        if not entry.isdigit():
            continue
        if len(table) >= PANE_PROCESS_MAX_ENTRIES:
            break
        try:
            with open(f"/proc/{entry}/stat", encoding="utf-8", errors="replace") as handle:
                line = handle.read(4096)
        except OSError:
            # A process that exited between the listing and the read is not a
            # failure; it is simply not running.
            continue
        start = line.find("(")
        end = line.rfind(")")
        if start < 0 or end < start:
            continue
        tail = _POSIX_STAT_TAIL_PATTERN.match(line[end:])
        if tail is None:
            continue
        table[int(entry)] = (int(tail.group(1)), normalize_binary(line[start + 1:end]))
    return table


def children_index(table: Dict[int, Tuple[int, str]]) -> Dict[int, List[int]]:
    """Invert a parent map once, so each pane's walk is lookups rather than a scan."""
    children: Dict[int, List[int]] = {}
    for pid, (parent, _name) in table.items():
        children.setdefault(parent, []).append(pid)
    return children


def descendant_binary(
    table: Dict[int, Tuple[int, str]],
    children: Dict[int, List[int]],
    root_pid: Optional[int],
    binaries: Iterable[str],
) -> str:
    """Return the first wanted executable running below ``root_pid``, or "".

    The shell itself is deliberately not a candidate: a pane *is* its shell, and
    a pane whose shell is somehow named like an agent is still a pane running a
    shell. What is being asked is what the shell has started.
    """
    wanted = {normalize_binary(name) for name in binaries}
    wanted.discard("")
    if not wanted or not table or not root_pid or int(root_pid) not in table:
        return ""

    visited = {int(root_pid)}
    frontier = [(int(root_pid), 0)]
    while frontier and len(visited) < PANE_PROCESS_MAX_VISITED:
        pid, depth = frontier.pop()
        if depth >= PANE_PROCESS_MAX_DEPTH:
            continue
        for child in children.get(pid, ()):
            if len(visited) >= PANE_PROCESS_MAX_VISITED:
                # Checked here and not only around the level, or a process with
                # thousands of direct children is walked in full whatever the
                # ceiling says.
                return ""
            if child in visited:
                # Pids are reused, so a parent map can close a loop. Whatever
                # this edge means, it is not a new process.
                continue
            visited.add(child)
            name = table.get(child, (0, ""))[1]
            if name in wanted:
                return name
            frontier.append((child, depth + 1))
    return ""
