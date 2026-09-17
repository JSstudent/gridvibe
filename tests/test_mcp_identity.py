"""Which pane the sidecar thinks it is in, and how deep it is allowed to go.

The sidecar reads its whole identity out of a process environment it never
asked for -- so the two things worth pinning are that a *missing* environment
degrades instead of guessing, and that the depth budget refuses at the boundary
rather than one step past it.

The depth counter is a guardrail against a runaway loop, not against an
adversary: the agent it constrains can unset the variable. That is stated in
the sidecar README and is not something a test can close.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from gridvibe_mcp.identity import (  # noqa: E402
    DEFAULT_MAX_AGENT_DEPTH,
    IDENTITY_VARIABLES,
    depth_budget,
    read_identity,
)

INSIDE = {
    "GRIDVIBE_URL": "http://127.0.0.1:5051",
    "GRIDVIBE_SESSION_ID": "pane-1",
    "GRIDVIBE_GROUP_ID": "group-1",
    "GRIDVIBE_WORKSPACE_ID": "ws-1",
    "GRIDVIBE_AGENT_DEPTH": "1",
}


class PaneIdentityTestCase(unittest.TestCase):
    def test_a_gridvibe_pane_is_read_whole(self):
        identity = read_identity(INSIDE)

        self.assertTrue(identity.inside_gridvibe)
        self.assertEqual(identity.url, "http://127.0.0.1:5051")
        self.assertEqual(identity.session_id, "pane-1")
        self.assertEqual(identity.group_id, "group-1")
        self.assertEqual(identity.workspace_id, "ws-1")
        self.assertEqual(identity.agent_depth, 1)
        self.assertTrue(identity.depth_stated)
        # What a pane this agent creates would be stamped with.
        self.assertEqual(identity.child_depth, 2)

    def test_an_agent_started_by_hand_says_so_rather_than_guessing(self):
        identity = read_identity({}, default_url="http://127.0.0.1:5050")

        self.assertFalse(identity.inside_gridvibe)
        self.assertEqual(identity.session_id, "")
        self.assertEqual(identity.workspace_id, "")
        # The URL is the one fact an argument can supply, because the process
        # that owns the port bakes it into the generated config.
        self.assertEqual(identity.url, "http://127.0.0.1:5050")
        # Absent is not zero-stated: the reader can tell the difference.
        self.assertEqual(identity.agent_depth, 0)
        self.assertFalse(identity.depth_stated)

    def test_an_unreadable_depth_degrades_to_unstated(self):
        for value in ("", "  ", "deep", "-4"):
            with self.subTest(value=value):
                identity = read_identity({**INSIDE, "GRIDVIBE_AGENT_DEPTH": value})
                if value.strip() == "-4":
                    # A negative depth is a stated number, floored at 0.
                    self.assertEqual(identity.agent_depth, 0)
                    self.assertTrue(identity.depth_stated)
                else:
                    self.assertEqual(identity.agent_depth, 0)
                    self.assertFalse(identity.depth_stated)

    def test_the_published_variable_list_is_the_whole_environment_contract(self):
        # One list, read by the sidecar, written by the injector and forwarded
        # across the WSL boundary. A sixth variable added to one of the three
        # and not the others is the failure this pins. The injector's half and
        # the WSLENV half are pinned in `tests/test_mcp_launch.py`, which is the
        # only side that can import `web/`.
        self.assertEqual(set(IDENTITY_VARIABLES), set(INSIDE))

    def test_every_published_variable_is_one_the_reader_actually_reads(self):
        """Drift in the other direction: a name listed and never consumed.

        Dropping any one of them has to change the identity, or the tuple is
        advertising something the sidecar does not use.
        """
        whole = read_identity(INSIDE)

        for name in IDENTITY_VARIABLES:
            with self.subTest(variable=name):
                without = {key: value for key, value in INSIDE.items() if key != name}

                self.assertNotEqual(read_identity(without), whole)


class DepthBudgetTestCase(unittest.TestCase):
    def test_a_fresh_pane_may_launch(self):
        allowed, refusal = depth_budget(read_identity({**INSIDE, "GRIDVIBE_AGENT_DEPTH": "0"}))

        self.assertTrue(allowed)
        self.assertEqual(refusal, "")

    def test_the_refusal_fires_at_the_limit_not_past_it(self):
        at_limit = read_identity(
            {**INSIDE, "GRIDVIBE_AGENT_DEPTH": str(DEFAULT_MAX_AGENT_DEPTH)}
        )
        allowed, refusal = depth_budget(at_limit)

        self.assertFalse(allowed)
        # The sentence names the depth, because "refused" on its own leaves the
        # reader with nothing to tell the user.
        self.assertIn(str(DEFAULT_MAX_AGENT_DEPTH), refusal)

    def test_one_below_the_limit_still_launches(self):
        allowed, _ = depth_budget(
            read_identity({**INSIDE, "GRIDVIBE_AGENT_DEPTH": str(DEFAULT_MAX_AGENT_DEPTH - 1)})
        )

        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
