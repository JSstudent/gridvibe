"""Process-group ownership and bounded teardown for the subprocesses we spawn.

Guardrail 4 requires every subprocess to carry a bound, and a bound that covers
only the direct child is not one. `git fetch` hands our stdout/stderr pipes to
its own transport helpers, so killing git leaves a grandchild holding the write
end; the reap after the kill then waits on that handle, which turned a 30 s
bound into a measured 269 s wait against a remote that accepted the connection
and went quiet.

`web/selfupdate.py` fixed that first and is where this pattern was written. It
lives here so the explorer's Git runner reuses it rather than growing a weaker
variant beside it (Guardrail 6); both callers import from this module.
"""

import os
import shutil
import signal
import subprocess
from typing import Any, Dict

#: How long we are willing to wait for a killed command's pipes to drain, and
#: for the tree kill itself to report back. Callers reuse this for their own
#: post-kill reap so one number describes the whole give-up path.
PROCESS_REAP_TIMEOUT = 5.0


def new_process_group() -> Dict[str, Any]:
    """Popen kwargs that put a child and its helpers in a group we can kill as one."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def terminate_process_tree(
    process: "subprocess.Popen[Any]",
    *,
    reap_timeout: float = PROCESS_REAP_TIMEOUT,
) -> None:
    """Kill a timed-out child *and everything it spawned*.

    The tree kill is skipped once the child has been reaped: a dead parent's
    pid can no longer name its orphans, and on POSIX that pid is free to be
    reused, so `killpg` on it would signal an unrelated process group. The
    direct `kill()` below stays unconditional because it goes through the
    `Popen` handle, which knows the child is already gone and does nothing.
    """
    if process.poll() is None:
        if os.name == "nt":
            taskkill = shutil.which("taskkill")
            if taskkill:
                try:
                    subprocess.run(
                        [taskkill, "/F", "/T", "/PID", str(process.pid)],
                        capture_output=True,
                        timeout=reap_timeout,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except OSError:
                pass

    # Backstop, and a no-op if the tree kill already landed.
    try:
        process.kill()
    except OSError:
        pass
