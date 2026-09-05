"""Private stdin/stdout regex worker; no app imports, files or child processes."""

import json
import re
import sys
from itertools import islice


def main():
    matcher = re.compile(sys.argv[1], int(sys.argv[2]))
    for line in sys.stdin.buffer:
        text, limit = json.loads(line)
        spans = [match.span() for match in islice(matcher.finditer(text), limit)]
        sys.stdout.buffer.write(json.dumps(spans).encode('ascii') + b'\n')
        sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
