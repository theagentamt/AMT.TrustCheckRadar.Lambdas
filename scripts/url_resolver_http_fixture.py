#!/usr/bin/env python3
"""Temporary owned HTTP fixture; deploy only with source-restricted ingress."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time
import ipaddress
import os

PRIVATE_FIXTURE = os.environ.get("AMT_FIXTURE_PRIVATE_IP")
if PRIVATE_FIXTURE:
    address = ipaddress.ip_address(PRIVATE_FIXTURE)
    if address.version != 4 or not address.is_private:
        raise ValueError("Fixture private destination must be a private IPv4 address")
    PRIVATE_FIXTURE = str(address)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        # Static destinations only: this server is not a public open redirector.
        redirects = {
            "/redirect": "/terminal",
            "/nested": "/redirect",
            "/loop-a": "/loop-b",
            "/loop-b": "/loop-a",
            "/private": "http://169.254.169.254/latest/meta-data/",
            "/sensitive": "/reset/synthetic",
            "/empty-query?x=1": "?",
        }
        if PRIVATE_FIXTURE:
            redirects["/private-ip"] = "http://" + PRIVATE_FIXTURE + "/terminal"
        try:
            if self.path.startswith("/limit/") and self.path[7:].isdigit():
                redirects[self.path] = "/limit/" + str(int(self.path[7:]) + 1)
            if self.path in redirects:
                self.send_response(302)
                self.send_header("Location", redirects[self.path])
            elif self.path == "/slow":
                time.sleep(3)
                self.send_response(200)
            elif self.path == "/oversized":
                self.send_response(200)
                self.send_header("X-Fixture", "x" * 17000)
            elif self.path == "/duplicate-location":
                self.send_response(302)
                self.send_header("Location", "/terminal")
                self.send_header("Location", "/redirect")
            elif self.path == "/refresh":
                self.send_response(200)
                self.send_header("Refresh", "0;url=/terminal")
            elif self.path in ("/terminal", "/empty-query?"):
                self.send_response(200)
            else:
                self.send_response(404)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 80), Handler).serve_forever()
