"""Real HTTP regressions using an isolated database and loopback port."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

class HttpBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); cls.port = sock.getsockname()[1]
        cls.token = 'test-only-' + 'x' * 40
        cls.process = subprocess.Popen([sys.executable, 'server.py'], cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'HOST':'127.0.0.1', 'PORT':str(cls.port), 'PLATFORM_DB':cls.tmp.name+'/test.db',
                 'PLATFORM_API_TOKEN':cls.token, 'MODEL_BACKEND':'demo'}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', cls.port), .1): return
            except OSError: time.sleep(.05)
        cls.process.terminate(); raise RuntimeError('Server did not start')
    @classmethod
    def tearDownClass(cls):
        cls.process.terminate(); cls.process.wait(timeout=10); cls.tmp.cleanup()
    def request(self, path, token=None, method='GET', body=None):
        con=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        headers={'Authorization':'Bearer '+token} if token else {}
        con.request(method,path,body=body,headers=headers); response=con.getresponse()
        status=response.status; response.read(); con.close(); return status
    def test_reads_and_writes_require_operator(self):
        for path,method in [('/api/overview','GET'),('/api/approvals/missing/approve','POST')]:
            self.assertEqual(self.request(path,method=method,body='{}' if method=='POST' else None),401)
        self.assertEqual(self.request('/api/overview',self.token),200)
        self.assertEqual(self.request('/api/overview','wrong'),401)
    def test_invalid_lengths_reject_without_waiting_for_body(self):
        for header in ['Content-Length: -1','Content-Length: nope','Content-Length: 1000001',
                       'Content-Length: 2\r\nContent-Length: 2','Transfer-Encoding: chunked']:
            with self.subTest(header=header), socket.create_connection(('127.0.0.1',self.port),2) as sock:
                sock.settimeout(2)
                sock.sendall(('POST /api/inference HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer '+self.token+'\r\n'+header+'\r\n\r\n').encode())
                self.assertIn(b'400',sock.recv(4096).split(b'\r\n')[0])
    def test_authorized_validation(self):
        self.assertEqual(self.request('/api/inference',self.token,'POST','{"prompt":""}'),400)
        self.assertEqual(self.request('/api/inference',self.token,'POST','{"prompt":"hello"}'),202)
    def test_cross_origin_denial(self):
        con=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        con.request('POST','/api/inference','{}',{'Origin':'https://untrusted.example','Authorization':'Bearer '+self.token})
        response=con.getresponse(); self.assertEqual(response.status,403);response.read();con.close()
if __name__ == '__main__': unittest.main()
