"""BUILD 832: downloads retry, resume and split big files into pieces.

A local HTTP server that misbehaves on purpose: it drops connections part
way, can ignore byte ranges, and can answer 403 as an expired mirror link
would. Every download must still come out byte-identical, and one that
never completes must fail with a readable error instead of installing.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""
import os as _nx_os, sys as _nx_sys  # find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)

import os, tempfile, threading, unittest  # noqa: E402
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler  # noqa: E402

import ff7nx_download as dl  # noqa: E402

BIG = os.urandom(dl.SEGMENT_MIN + (3 << 20))      # split into pieces
SMALL = os.urandom(3 << 20)                       # one stream
CFG = {}
LOCK = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):
        data = BIG if 'big' in self.path else SMALL
        rng = self.headers.get('Range')
        probe = bool(rng) and rng.endswith('=0-0')
        with LOCK:
            expire = CFG['expire'] > 0 and rng and not probe
            if expire:
                CFG['expire'] -= 1
            drop = CFG['drops'] > 0 and not probe
            if drop:
                CFG['drops'] -= 1
        if expire:
            self.send_response(403)
            self.end_headers()
            return
        start, end = 0, len(data) - 1
        if rng and CFG['ranges']:
            a, b = rng.split('=')[1].split('-')
            start, end = int(a), (int(b) if b else len(data) - 1)
            self.send_response(206)
        else:
            self.send_response(200)
            if CFG['ranges']:
                self.send_header('Accept-Ranges', 'bytes')
        body = data[start:end + 1]
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            if drop and len(body) > CFG['drop_at']:
                self.wfile.write(body[:CFG['drop_at']])
                self.wfile.flush()
                self.connection.shutdown(2)
                return
            self.wfile.write(body)
        except OSError:
            pass


class Downloads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._backoff = dl.BACKOFF
        dl.BACKOFF = (0.05,) * len(dl.BACKOFF)
        cls.srv = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        dl.BACKOFF = cls._backoff
        cls.srv.shutdown()

    def fetch(self, which, **cfg):
        CFG.update(ranges=True, drops=0, expire=0, drop_at=1 << 20)
        CFG.update(cfg)
        out = os.path.join(self.tmp, which)
        seen, status = [0], []
        link = 'iros://Url/http$127.0.0.1:%d/%s' % (
            self.srv.server_address[1], which)
        dl._fetch(link, out, lambda d, n: seen.__setitem__(0, d),
                  lambda: False, 0, lambda *_: None, status.append)
        with open(out, 'rb') as f:
            got = f.read()
        return got, seen[0], status

    def test_big_file_in_pieces(self):
        got, seen, _ = self.fetch('big')
        self.assertEqual(got, BIG)
        self.assertEqual(seen, len(BIG))

    def test_pieces_resume_after_drops(self):
        got, seen, status = self.fetch('big', drops=3)
        self.assertEqual(got, BIG)
        self.assertEqual(seen, len(BIG))      # no byte counted twice
        self.assertTrue(status and status[0].startswith('Retrying'))

    def test_expired_link_is_reopened(self):
        got, _, _ = self.fetch('big', expire=2)
        self.assertEqual(got, BIG)

    def test_stream_resumes_after_drops(self):
        got, seen, _ = self.fetch('small', drops=2, drop_at=1 << 20)
        self.assertEqual(got, SMALL)
        self.assertEqual(seen, len(SMALL))

    def test_no_ranges_starts_over(self):
        got, _, _ = self.fetch('small', ranges=False, drops=1,
                               drop_at=1 << 20)
        self.assertEqual(got, SMALL)

    def test_never_completes_fails_readably(self):
        with self.assertRaises(dl.DownloadError) as cm:
            self.fetch('small', drops=999, drop_at=128 << 10)
        self.assertIn('gave up after', str(cm.exception))

    def test_cancel(self):
        CFG.update(ranges=True, drops=0, expire=0, drop_at=1 << 20)
        n = [0]

        def cancel():
            n[0] += 1
            return n[0] > 5
        link = 'iros://Url/http$127.0.0.1:%d/big' % self.srv.server_address[1]
        with self.assertRaises(dl.Cancelled):
            dl._fetch(link, os.path.join(self.tmp, 'c'), lambda *_: None,
                      cancel)


if __name__ == '__main__':
    unittest.main()
