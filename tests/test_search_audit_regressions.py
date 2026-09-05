import builtins
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from web import explorer_search as search
from web.explorer import _LocalExplorerBackend, _SftpExplorerBackend
from web.search_regex import SearchMatcher


class _Channel:
    def __init__(self, output=b'', errors=b'', status=0, quiet=False):
        self.output, self.errors = io.BytesIO(output), io.BytesIO(errors)
        self.status, self.quiet, self.closed = status, quiet, False

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv_ready(self):
        return self.output.tell() < len(self.output.getvalue())

    def recv_stderr_ready(self):
        return self.errors.tell() < len(self.errors.getvalue())

    def recv(self, size):
        return self.output.read(size)

    def recv_stderr(self, size):
        return self.errors.read(size)

    def exit_status_ready(self):
        return not self.quiet

    def recv_exit_status(self):
        return self.status

    def close(self):
        self.closed = True


class SearchAuditRegressionsTestCase(unittest.TestCase):
    def test_broad_candidates_do_not_falsely_report_a_match_cap(self):
        limits = search.SearchLimits(max_matches=1)
        raw = iter([('a.txt', 1, b'123'), ('a.txt', 2, b'not a match')])
        payload = search.collect_search_payload(raw, search.SearchOptions(r'\d+', regex=True),
                                                limits, time.monotonic() + 3, 'git-grep', time.monotonic())
        self.assertEqual(payload['total_matches'], 1)
        self.assertFalse(payload['truncated']['matches'])

    def test_include_rejects_before_body_reads_and_growing_reads_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'excluded.log').write_text('hello')
            Path(tmp, 'included.py').write_text('hello')
            opened = []
            original = builtins.open

            def observed(path, *args, **kwargs):
                opened.append(os.path.basename(path))
                return original(path, *args, **kwargs)

            with patch('builtins.open', side_effect=observed):
                matches = list(search.walk_matches(tmp, tmp, search.SearchOptions('hello', include=('*.py',)), search.SearchLimits(), time.monotonic() + 3))
            self.assertEqual(opened, ['included.py'])
            self.assertEqual([match[0] for match in matches], ['included.py'])
            stream = io.BytesIO(b'hello' * 100)
            with patch('builtins.open', return_value=stream), patch.object(os.path, 'getsize', return_value=1):
                result = list(search.walk_matches(tmp, tmp, search.SearchOptions('hello', include=('*.py',)), search.SearchLimits(max_file_bytes=10), time.monotonic() + 3))
            self.assertEqual(result, [])

    @unittest.skipUnless(shutil.which('git'), 'Git required')
    def test_regex_and_unicode_semantics_agree_across_engines(self):
        corpus = '123\nhello world\nhelloworld\nİstanbul\nKelvin\nſample\n🚀 hit hit\nabc\n\n'
        queries = [('\\d+', True, False), ('^(hello|abc)', True, False),
                   ('hello', False, True), ('istanbul', False, False),
                   ('kelvin', False, False), ('sample', False, False),
                   ('[a-z]+', True, False), ('^$', True, False)]
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(['git', 'init', '-q', tmp], check=True, timeout=5, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
            Path(tmp, 'sample.txt').write_text(corpus, encoding='utf-8')
            backend = _LocalExplorerBackend(SimpleNamespace(startup_mode='explorer', mode='wsl', explorer_root=tmp, directory=tmp))
            for query, regex, word in queries:
                with self.subTest(query=query):
                    args = {'q': query, 'regex': str(int(regex)), 'word': str(int(word))}
                    git = search.run_explorer_search(backend, args)
                    walk = search.run_explorer_search(backend, {**args, 'ignored': '1'})
                    options = search.parse_search_options(args)
                    raw = (('sample.txt', i, line.encode()) for i, line in enumerate(corpus.splitlines(), 1))
                    remote = search.collect_search_payload(raw, options, search.SearchLimits(), time.monotonic() + 5, 'remote-grep', time.monotonic())
                    self.assertEqual(git['files'], walk['files'])
                    self.assertEqual(git['files'], remote['files'])

    def test_regex_deadlines_are_enforced_inside_worker_and_reaped(self):
        script = r'''
import time
import tests
from web import explorer_search as s
from web.search_regex import SearchMatcher, SearchDeadlineExceeded
for _ in range(3):
    started = time.monotonic()
    matcher = SearchMatcher(s.compile_search_matcher(s.SearchOptions('(a+)+$', regex=True)), True, started + 0.1)
    try:
        matcher.search('a' * 30 + '!')
        raise AssertionError('catastrophic match completed instead of timing out')
    except SearchDeadlineExceeded:
        assert matcher.process is None
        assert not matcher.thread.is_alive()
        assert time.monotonic() - started < 2
    finally:
        matcher.close()
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_zero_width_ranges_are_bounded_before_windowing(self):
        with SearchMatcher(search.compile_search_matcher(search.SearchOptions('(?=a)', regex=True)), True, time.monotonic() + 3) as matcher:
            spans = matcher.spans('a' * 1000000)
            self.assertEqual(len(spans), 401)
            process = matcher.process
        self.assertIsNotNone(process.poll())

    def test_name_and_content_match_timeouts_report_partial_deadline(self):
        options = search.SearchOptions('(a+)+$', regex=True)
        content = search.collect_search_payload(iter([('a.txt', 1, b'a' * 30 + b'!')]), options, search.SearchLimits(), time.monotonic() + 0.1, 'walk', time.monotonic())
        names = search.collect_find_payload(iter([('a' * 30 + '!', False)]), search.FindOptions('(a+)+$', regex=True), search.SearchLimits(), time.monotonic() + 0.1, 'walk', time.monotonic())
        self.assertTrue(content['truncated']['deadline'])
        self.assertTrue(names['truncated']['deadline'])

    def test_utf16_response_offsets_render_exact_matches(self):
        text = '🚀😀 é hit hit'
        payload = search.collect_search_payload(iter([('🚀hit.txt', 1, text.encode())]), search.SearchOptions('hit'), search.SearchLimits(), time.monotonic() + 3, 'walk', time.monotonic())
        match = payload['files'][0]['matches'][0]
        self.assertEqual(match['ranges'], [[8, 11], [12, 15]])
        self.assertEqual(match['line_length'], 15)
        names = search.collect_find_payload(iter([('🚀hit.txt', False)]), search.FindOptions('hit'), search.SearchLimits(), time.monotonic() + 3, 'walk', time.monotonic())
        self.assertEqual(names['entries'][0]['ranges'], [[2, 5]])
        long = '🚀' * 300 + 'hit' + 'a' * 300
        windowed = search.collect_search_payload(iter([('a.txt', 1, long.encode())]), search.SearchOptions('hit'), search.SearchLimits(), time.monotonic() + 3, 'walk', time.monotonic())['files'][0]['matches'][0]
        self.assertEqual(windowed['text_offset'], 200)
        self.assertEqual(windowed['ranges'], [[400, 403]])
        if shutil.which('node'):
            script = "const fs=require('fs'),vm=require('vm'),assert=require('assert'); const ctx=vm.createContext({escHtml:x=>x}); vm.runInContext(fs.readFileSync('web/static/js/explorer-search.js','utf8'),ctx); const html=ctx.explorerSearchHitTextHtml(JSON.parse(process.argv[1])); assert.equal((html.match(/>hit<\\/mark>/g)||[]).length,2);"
            result = subprocess.run([shutil.which('node'), '-e', script, json.dumps(match)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_remote_content_and_names_report_completion_and_caps(self):
        for names in [False, True]:
            complete = b'f /root/hello.txt\n' if names else b'/root/a.txt:1:hello\n'
            cases = [
                (complete, b'', 0, False, len(complete), False, False, False),
                (complete + b'x', b'', 0, False, len(complete), True, False, False),
                (complete, b'permission denied', 2, False, 1000, False, False, True),
                (b'', b'command not found', 127, False, 1000, False, False, True),
                (complete, b'', -1, False, 1000, False, False, True),
                (complete, b'', 0, True, 1000, False, True, False),
                (b'', b'', 0 if names else 1, False, 1000, False, False, False),
            ]
            for output, errors, status, quiet, limit, truncated, deadline, failed in cases:
                with self.subTest(names=names, status=status, quiet=quiet, limit=limit):
                    channel = _Channel(output, errors, status, quiet)
                    stream = SimpleNamespace(channel=channel, close=lambda: None)
                    client = SimpleNamespace(exec_command=lambda *a, **kw: (None, stream, stream))
                    backend = _SftpExplorerBackend(client=client)
                    limits = search.SearchLimits(timeout_seconds=0.04)
                    end = time.monotonic() + limits.timeout_seconds
                    constant = 'FIND_REMOTE_MAX_OUTPUT_BYTES' if names else 'SEARCH_REMOTE_MAX_OUTPUT_BYTES'
                    with patch.object(search, constant, limit):
                        if names:
                            engine, records = backend.find_names(root_path='/root', limits=limits, deadline=end)
                            payload = search.collect_find_payload(records, search.FindOptions('hello'), limits, end, engine, time.monotonic())
                        else:
                            options = search.SearchOptions('hello', include_ignored=True)
                            engine, records = backend.search_lines(options, root_path='/root', scope_path='/root', limits=limits, deadline=end)
                            payload = search.collect_search_payload(records, options, limits, end, engine, time.monotonic())
                    self.assertEqual(payload['truncated']['output'], truncated)
                    self.assertEqual(payload['truncated']['deadline'], deadline)
                    self.assertEqual(bool(payload['error']), failed)
                    self.assertTrue(channel.closed)
