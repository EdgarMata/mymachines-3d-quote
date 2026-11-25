"""Native Nginx integration, opt-in; no customer models or external network.

prepare DIR emits a fixture derived from the production configuration.
run executes inside the Nginx container's network namespace, using a synthetic
HTTP upstream and real loopback clients/proxies. No Python rate limiter exists.
"""

import http.client
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def prepare(directory):
    root = Path(__file__).resolve().parents[2]
    config = (root / "frontend/nginx.conf").read_text()
    config = config.replace("http://backend:8000", "http://127.0.0.1:8000")
    maps, server = config.split("server {", 1)
    zones = "\n".join(line for line in maps.splitlines() if line.startswith("limit_"))
    fast_zones = re.sub(r"zone=([a-z_]+):", r"zone=fast_\1:", zones)
    fast_zones = re.sub(r"rate=\d+r/[ms]", "rate=1r/s", fast_zones)
    fast_zones = fast_zones.replace("zone=fast_global_api:1m rate=1r/s", "zone=fast_global_api:1m rate=1000r/s")
    fast = re.sub(r"zone=([a-z_]+)", r"zone=fast_\1", server)
    fast = fast.replace("limit_conn api_connections", "limit_conn fast_api_connections")
    fast = fast.replace("limit_conn global_connections", "limit_conn fast_global_connections")
    fast = fast.replace("listen 80;", "listen 18080;").replace("listen [::]:80;", "listen [::]:18080;")
    fast = fast.replace("client_body_timeout 30s", "client_body_timeout 2s")
    fast = fast.replace("client_header_timeout 10s", "client_header_timeout 2s")
    fast = fast.replace("keepalive_timeout 15s", "keepalive_timeout 2s")
    fast = fast.replace("proxy_read_timeout 90s", "proxy_read_timeout 300ms")
    proxies = ""
    for port, target, peer in ((8081, 80, "127.0.0.2"), (18081, 18080, "127.0.0.2"), (18082, 18080, "127.0.0.4")):
        proxies += f"""
server {{
    listen {port}; listen [::]:{port};
    client_max_body_size 51m;
    location / {{
        proxy_pass http://127.0.0.1:{target};
        proxy_bind {peer};
        proxy_request_buffering off;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }}
}}
"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "default.conf").write_text(config + "\n" + fast_zones + "\nserver {" + fast + proxies)
    (directory / "trusted.conf").write_text("set_real_ip_from 127.0.0.2;\n")


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        status = 503 if self.path.startswith("/fixture503") else 504 if self.path.startswith("/fixture504") else 200
        if self.path == "/estimate/":
            status = 307
        if self.path.startswith("/stall"):
            time.sleep(1)
        payload = {"client": self.headers.get("X-Real-IP"), "xff": self.headers.get("X-Forwarded-For"),
                   "forwarded": self.headers.get("Forwarded"), "path": self.path, "length": length}
        if status != 200:
            payload = {"detail": "Synthetic controlled upstream failure."}
        body = json.dumps(payload).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if status == 307:
                self.send_header("Location", "/api/estimate")
            if status == 503:
                self.send_header("Retry-After", "5")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = do_POST = respond


def request(path="options", *, ip="127.0.0.10", port=18080, headers=None, ipv6=False, method=None):
    connection = http.client.HTTPConnection("::1" if ipv6 else "127.0.0.1", port, timeout=6,
                                           source_address=("::1" if ipv6 else ip, 0))
    try:
        method = method or ("GET" if path == "health" else "POST")
        connection.putrequest(method, "/api/" + path)
        for key, value in (headers.items() if isinstance(headers, dict) else headers or []):
            connection.putheader(key, value)
        if method != "GET":
            connection.putheader("Content-Length", "1")
        connection.endheaders(None if method == "GET" else b"x")
        response = connection.getresponse()
        body = response.read()
        try:
            body = json.loads(body)
        except ValueError:
            body = body.decode()
        return response.status, dict(response.headers), body
    finally:
        connection.close()


class TestAbuseProtection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 8000), Upstream)
        cls.thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 5
        while True:
            try:
                if request("health")[0] == 200:
                    break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Nginx fixture did not become ready.")
            time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.thread.join()

    def assert_429(self, response):
        status, headers, value = response
        self.assertEqual(status, 429, response)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(value, {"detail": "Too many requests. Please wait a moment and try again."})
        self.assertNotIn("Retry-After", headers)

    def test_production_rates_bursts_independence_and_recovery(self):
        ip = "127.0.0.11"
        for path, allowed in (("estimate", 3), ("analyze", 6), ("options", 11)):
            for _ in range(allowed):
                self.assertEqual(request(path, ip=ip, port=80)[0], 200)
            self.assert_429(request(path, ip=ip, port=80))
        time.sleep(6.2)  # Actual production 10/minute estimate credit, not a mock.
        self.assertEqual(request("estimate", ip=ip, port=80)[0], 200)

    def test_fast_interval_recovery(self):
        for _ in range(3):
            self.assertEqual(request("estimate", ip="127.0.0.12")[0], 200)
        self.assert_429(request("estimate", ip="127.0.0.12"))
        time.sleep(1.1)
        self.assertEqual(request("estimate", ip="127.0.0.12")[0], 200)

    def test_direct_headers_cannot_rotate_identity(self):
        for index in range(4):
            result = request("estimate", ip="127.0.0.13", headers={"X-Real-IP": f"192.0.2.{index+1}",
                "X-Forwarded-For": f"2001:db8::{index+1}", "Forwarded": "for=192.0.2.99"})
            if index < 3:
                self.assertEqual(result[0], 200)
                self.assertEqual(result[2]["client"], "127.0.0.13")
                self.assertEqual(result[2]["xff"], "127.0.0.13")
                self.assertIsNone(result[2]["forwarded"])
            else:
                self.assert_429(result)

    def test_trusted_proxy_overwrites_spoofed_headers_and_separates_clients(self):
        for ip in ("127.0.0.14", "127.0.0.15"):
            for index in range(4):
                result = request("estimate", ip=ip, port=18081, headers={"X-Real-IP": f"192.0.2.{index+1}",
                    "X-Forwarded-For": f"2001:db8::{index+1}, 192.0.2.8"})
                if index < 3:
                    self.assertEqual(result[0], 200)
                    self.assertEqual(result[2]["client"], ip)
                    self.assertEqual(result[2]["xff"], ip)
                else:
                    self.assert_429(result)

    def test_untrusted_proxy_is_one_peer_not_a_trusted_docker_network(self):
        for index in range(4):
            result = request("estimate", ip=f"127.0.0.{20+index}", port=18082)
            if index < 3:
                self.assertEqual(result[0], 200)
                self.assertEqual(result[2]["client"], "127.0.0.4")
            else:
                self.assert_429(result)

    def test_ipv6_through_trusted_proxy(self):
        for index in range(4):
            result = request("estimate", port=18081, ipv6=True)
            if index < 3:
                self.assertEqual(result[0], 200)
                self.assertEqual(result[2]["client"], "::1")
            else:
                self.assert_429(result)

    def test_normalized_paths_and_legacy_routes_cannot_bypass_pool(self):
        for path in ("estimate?quantity=1", "%65stimate", "estimate/", "slice", "upload",
                     "/estimate", "x/../estimate", "estimate%2f", "estimate?new=2"):
            result = request(path, ip="127.0.0.30")
            if path not in ("estimate?quantity=1", "%65stimate", "estimate/"):
                self.assert_429(result)
            else:
                self.assertEqual(result[0], 307 if path == "estimate/" else 200)

    def test_ambiguous_and_duplicate_headers_are_rejected(self):
        for peer in ("127.0.0.2", "127.0.0.70"):
            for headers in ([('X-Real-IP', '192.0.2.1'), ('X-Real-IP', '192.0.2.2')],
                            [('X-Real-IP', '192.0.2.1, 192.0.2.2')],
                            [('X-Real-IP', 'garbage 192.0.2.3')],
                            [('X-Real-IP', ''), ('X-Real-IP', '192.0.2.4')]):
                result = request("estimate", ip=peer, headers=headers)
                self.assertEqual(result[0], 400, result)
                self.assertEqual(result[2], {"detail": "Invalid client address header."})

    def test_missing_and_invalid_literals_share_trusted_peer_budget(self):
        # Numeric-looking but invalid addresses cannot become rate-limit keys.
        for index, value in enumerate((None, '999.999.999.999', '::::', '999.1.1.1')):
            result = request("estimate", ip="127.0.0.2",
                             headers={} if value is None else {"X-Real-IP": value})
            if index < 3:
                self.assertEqual(result[0], 200, result)
                self.assertEqual(result[2]["client"], "127.0.0.2")
            else:
                self.assert_429(result)

    def test_equivalent_ip_spellings_share_one_budget(self):
        for values, canonical in ((('2001:db8::abcd', '2001:0db8:0:0:0:0:0:ABCD',
                                    '2001:db8::ABCD', '2001:db8::abcd'), '2001:db8::abcd'),
                                  (('192.0.2.190', '192.0.2.190:1234',
                                    '192.0.2.190:2345', '192.0.2.190'), '192.0.2.190')):
            for index, value in enumerate(values):
                result = request("estimate", ip="127.0.0.2", headers={"X-Real-IP": value})
                if index < 3:
                    self.assertEqual(result[0], 200, result)
                    self.assertEqual(result[2]["client"], canonical)
                else:
                    self.assert_429(result)

    def test_ipv6_direct_access_ignores_spoofed_headers(self):
        for index in range(4):
            result = request("analyze", ipv6=True, headers={"X-Real-IP": f"2001:db8::{index+1}"})
            self.assertEqual(result[0], 200)
            self.assertEqual(result[2]["client"], "::1")
            self.assertEqual(result[2]["xff"], "::1")

    def hold(self, ip):
        connection = socket.create_connection(("127.0.0.1", 18080), timeout=6, source_address=(ip, 0))
        self.addCleanup(connection.close)
        connection.sendall(b"POST /api/hold HTTP/1.1\r\nHost: local\r\nContent-Length: 1000\r\n"
                           b"Expect: 100-continue\r\n\r\n")
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = connection.recv(4096)
            self.assertTrue(chunk, response)
            response += chunk
        self.assertIn(b" 100 Continue\r\n", response)
        connection.sendall(b"x")
        return connection

    def test_five_active_connections_and_release_health_exemption(self):
        connections = [self.hold("127.0.0.31") for _ in range(5)]
        self.assert_429(request(ip="127.0.0.31"))
        self.assertEqual(request("health", ip="127.0.0.31")[0], 200)
        self.assert_429(request("health", ip="127.0.0.31", method="POST"))
        self.assert_429(request("health", ip="127.0.0.31", headers={"Content-Length": "1"}))
        self.assertEqual(request(ip="127.0.0.32")[0], 200)
        for connection in connections:
            connection.close()
        deadline = time.monotonic() + 3
        while request(ip="127.0.0.31")[0] != 200:
            self.assertLess(time.monotonic(), deadline, "Closed uploads did not release capacity.")
            time.sleep(0.05)

    def test_global_active_request_bound_across_clients(self):
        connections = [self.hold(f"127.0.0.{40+index//5}") for index in range(20)]
        self.assert_429(request(ip="127.0.0.45"))
        self.assertEqual(request("health", ip="127.0.0.45")[0], 200)
        for connection in connections:
            connection.close()

    def test_global_request_rate_across_independent_clients(self):
        try:
            results = [request(ip=f"127.0.1.{index+1}", port=80) for index in range(40)]
            self.assertIn(200, [result[0] for result in results])
            self.assertIn(429, [result[0] for result in results])
            for result in results:
                if result[0] == 429:
                    self.assert_429(result)
            self.assertEqual(request("health", port=80)[0], 200)
        finally:
            time.sleep(2.2)  # Drain the actual global 10/s burst before later tests.

    def test_idle_body_timeout_and_continuous_slow_body(self):
        connection = self.hold("127.0.0.50")
        self.assert_timeout_close(connection)
        slow = socket.create_connection(("127.0.0.1", 18080), timeout=6, source_address=("127.0.0.51", 0))
        self.addCleanup(slow.close)
        slow.sendall(b"POST /api/stream HTTP/1.1\r\nHost: local\r\nContent-Length: 5\r\n\r\nx")
        for _ in range(4):
            time.sleep(0.6)
            slow.sendall(b"x")
        self.assertIn(b"200", slow.recv(4096).split(b"\r\n", 1)[0])

    def test_idle_headers_and_keepalive_timeout(self):
        connection = socket.create_connection(("127.0.0.1", 18080), timeout=6)
        self.addCleanup(connection.close)
        connection.sendall(b"GET /api/options HTTP/1.1\r\nHost: local\r\n")
        self.assert_timeout_close(connection)
        client = http.client.HTTPConnection("127.0.0.1", 18080, timeout=6, source_address=("127.0.0.52", 0))
        self.addCleanup(client.close)
        client.request("GET", "/api/options")
        response = client.getresponse()
        response.read()
        self.assertEqual(response.status, 200)
        self.assertEqual(client.sock.recv(1), b"")

    def assert_timeout_close(self, connection):
        started = time.monotonic()
        data = connection.recv(4096)
        # Nginx may close a timed-out incomplete request without sending a body.
        self.assertTrue(not data or b"408" in data.split(b"\r\n", 1)[0], data)
        self.assertGreater(time.monotonic() - started, 1)
        self.assertLess(time.monotonic() - started, 5.5)

    def test_413_503_504_and_proxy_timeout_are_preserved(self):
        client = http.client.HTTPConnection("127.0.0.1", 18080, timeout=6)
        self.addCleanup(client.close)
        client.putrequest("POST", "/api/estimate")
        client.putheader("Content-Length", str(51 * 1024 * 1024 + 1))
        client.endheaders()
        self.assertEqual(client.getresponse().status, 413)
        for path, expected in (("fixture503", 503), ("fixture504", 504), ("stall", 504)):
            result = request(path, ip="127.0.0.53")
            self.assertEqual(result[0], expected)
            if expected == 503:
                self.assertEqual(result[1]["Retry-After"], "5")

    def test_production_trust_guard_fails_closed(self):
        path = Path("/etc/nginx/trusted-proxies.conf")  # Test runner's private filesystem.
        path.parent.mkdir(parents=True, exist_ok=True)
        for content, accepted in (("", False), ("# empty\n", False),
            ("set_real_ip_from 127.0.0.2;\n", True), ("set_real_ip_from ::1/128;\n", True),
            ("set_real_ip_from 0.0.0.0/0;\n", False), ("set_real_ip_from 172.18.0.0/16;\n", False),
            ("set_real_ip_from 2001:db8::/64;\n", False), ("set_real_ip_from frontend;\n", False),
            ("set_real_ip_from unix:;\n", False), ("real_ip_header X-Forwarded-For;\n", False),
            ("set_real_ip_from 127.0.0.2; include /tmp/injected;\n", False),
            ("set_real_ip_from 127.0.0.2;\ninclude /tmp/injected;\n", False),
            ("set_real_ip_from 127.0.0.2;include;\n", False),
            ("set_real_ip_from 127.0.0.2/32; # exact peer\n", True),
            ("set_real_ip_from [::1];\n", False),
            ("set_real_ip_from 999.0.0.1;\n", False)):
            path.write_text(content)
            result = subprocess.run(["sh", "/source/frontend/15-validate-trusted-proxies.sh"],
                                    env={**os.environ, "REQUIRE_TRUSTED_PROXY": "true"}, capture_output=True)
            self.assertEqual(result.returncode == 0, accepted, content)
        path.write_text("")
        result = subprocess.run(["sh", "/source/frontend/15-validate-trusted-proxies.sh"],
                                env={**os.environ, "REQUIRE_TRUSTED_PROXY": "false"}, capture_output=True)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    if sys.argv[1:2] == ["prepare"]:
        prepare(sys.argv[2])
    else:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
