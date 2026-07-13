#!/usr/bin/env python3
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / 'index.html'
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from dashboard.data_builder import load_dashboard_payload


class DashboardHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/api/dashboard':
            payload = load_dashboard_payload()
            self._send_json(payload)
            return

        if self.path in ('/', '/index.html'):
            html = INDEX_PATH.read_text(encoding='utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html.encode('utf-8'))))
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
            return

        self.send_error(404)

    def log_message(self, format, *args):
        return


if __name__ == '__main__':
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '8000'))
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f'Starting dashboard on http://{host}:{port}')
    server.serve_forever()
