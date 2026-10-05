import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Discovery may import the test modules as top-level modules, so the package
# bootstrap that redirects durable state away from the developer's real files
# is imported explicitly here — before any test module imports `web`.
import tests  # noqa: E402,F401


def _pretty_case_name(test_case) -> str:
    method_name = getattr(test_case, "_testMethodName", None)
    if method_name is None:
        # A setUpClass/setUpModule failure reports an `_ErrorHolder`, not a case.
        return test_case.id()

    class_name = test_case.__class__.__name__
    if class_name.endswith("TestCase"):
        class_name = class_name[:-8]

    if method_name.startswith("test_"):
        method_name = method_name[5:]

    return f"{class_name}: {method_name.replace('_', ' ')}"


class FriendlyTextTestResult(unittest.TextTestResult):
    def getDescription(self, test):
        return _pretty_case_name(test)


class FriendlyTextTestRunner(unittest.TextTestRunner):
    resultclass = FriendlyTextTestResult


def main() -> int:
    suite = unittest.defaultTestLoader.discover("tests", pattern="test_*.py")
    runner = FriendlyTextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
