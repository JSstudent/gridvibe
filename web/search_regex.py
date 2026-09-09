"""Python regex semantics with a request deadline and bounded span output."""

import json
import queue
import subprocess
import sys
import threading
import time
from itertools import islice
from pathlib import Path

from web.process_bounds import new_process_group, terminate_process_tree


class SearchDeadlineExceeded(Exception):
    """A search exhausted its deadline, including time inside a matcher."""


class SearchMatcher:
    """Literals run locally; arbitrary patterns run in one disposable worker."""

    def __init__(self, pattern, regex, deadline):
        self.pattern = pattern
        self.regex = regex
        self.deadline = deadline
        self.process = None
        self.thread = None
        self.requests = queue.Queue()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def _exchange(self):
        process = self.process
        while True:
            request = self.requests.get()
            if request is None:
                return
            text, limit, answer = request
            try:
                process.stdin.write(json.dumps([text, limit]).encode('utf-8') + b'\n')
                process.stdin.flush()
                line = process.stdout.readline()
                if not line:
                    raise OSError('Search worker exited before returning a result')
                answer.put(json.loads(line))
            except Exception as exc:
                answer.put(exc)
                return

    def spans(self, text, limit=401):
        if time.monotonic() >= self.deadline:
            raise SearchDeadlineExceeded()
        if not self.regex:
            return [list(match.span()) for match in islice(self.pattern.finditer(text), limit)]
        if self.process is None:
            self.process = subprocess.Popen(
                [sys.executable, '-u', str(Path(__file__).with_name('search_regex_worker.py')),
                 self.pattern.pattern, str(self.pattern.flags)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                **new_process_group(),
            )
            self.thread = threading.Thread(target=self._exchange, name='explorer-regex', daemon=True)
            self.thread.start()
        answer = queue.Queue(maxsize=1)
        self.requests.put((text, limit, answer))
        try:
            result = answer.get(timeout=max(0, self.deadline - time.monotonic()))
        except queue.Empty as exc:
            self.close()
            raise SearchDeadlineExceeded() from exc
        if isinstance(result, Exception):
            raise result
        return result

    def search(self, text):
        return bool(self.spans(text, 1))

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        self.requests.put(None)
        terminate_process_tree(process, reap_timeout=0.2)
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        if self.thread:
            self.thread.join(timeout=0.5)
        # The worker thread owns pipe reads/writes; after it exits these closes
        # cannot block waiting for a Python buffered-I/O lock.
        if not self.thread or not self.thread.is_alive():
            process.stdin.close()
            process.stdout.close()
