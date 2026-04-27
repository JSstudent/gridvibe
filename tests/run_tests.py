import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _pretty_case_name(test_case) -> str:
    class_name = test_case.__class__.__name__
    if class_name.endswith("TestCase"):
        class_name = class_name[:-8]

    method_name = test_case._testMethodName
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

