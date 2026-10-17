#!/usr/bin/env python3
"""
HDHR Dash — Local HTTPS Static Server
Serves HDHR Dash over HTTPS using a self-signed certificate.

Required for full modern Chrome & browser functionality (Clipboard API copy,
Service Worker caching, PWA installation, etc.) when testing locally or hosting
across a local home network.
"""

import argparse
import http.server
import ipaddress
import mimetypes
import os
import random
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Ensure correct MIME types for web assets
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('application/json', '.json')
mimetypes.add_type('application/wasm', '.wasm')
mimetypes.add_type('image/svg+xml', '.svg')
mimetypes.add_type('application/manifest+json', '.webmanifest')
mimetypes.add_type('audio/x-mpegurl', '.m3u')

MDNS_ADDR = ('224.0.0.251', 5353)
MDNS_TIMEOUT = 0.75
MDNS_CACHE_TTL = 60
_mdns_cache = {}
_mdns_lock = threading.Lock()

# Upstream response headers passed back to the browser by the device proxy
PROXY_RESPONSE_HEADERS = (
    'Content-Type',
    'Content-Length',
    'Content-Range',
    'Content-Disposition',
    'Accept-Ranges',
    'Last-Modified',
)


def is_local_host(hostname):
    """Return True for .local names and private / loopback IP addresses."""
    if not hostname:
        return False
    if hostname in ('localhost', '127.0.0.1') or hostname.endswith('.local'):
        return True
    try:
        ip = ipaddress.ip_address(hostname)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


def _skip_dns_name(data, offset):
    """Advance past a (possibly compressed) DNS name and return the new offset."""
    while offset < len(data):
        length = data[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            return offset + 2
        offset += length + 1
    raise ValueError("Truncated DNS name")


def mdns_resolve(hostname):
    """
    Resolve a .local hostname to an IPv4 address with a one-shot multicast DNS query.
    Only gets an answer when the container shares the host network; returns None otherwise.
    Results (including misses) are cached for MDNS_CACHE_TTL seconds.
    """
    now = time.monotonic()
    with _mdns_lock:
        cached = _mdns_cache.get(hostname)
        if cached and cached[1] > now:
            return cached[0]

    query_id = random.randint(0, 0xFFFF)
    labels = hostname.rstrip('.').split('.')
    question = b''.join(bytes([len(label)]) + label.encode('ascii') for label in labels) + b'\x00'
    packet = struct.pack('!HHHHHH', query_id, 0, 1, 0, 0, 0) + question + struct.pack('!HH', 1, 0x8001)

    result = None
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.settimeout(MDNS_TIMEOUT)
        sock.sendto(packet, MDNS_ADDR)
        deadline = now + MDNS_TIMEOUT
        while result is None and time.monotonic() < deadline:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                break
            if len(data) < 12:
                continue
            resp_id, flags, qdcount, ancount, nscount, arcount = struct.unpack('!HHHHHH', data[:12])
            if resp_id != query_id or not flags & 0x8000:
                continue
            offset = 12
            for _ in range(qdcount):
                offset = _skip_dns_name(data, offset) + 4
            for _ in range(ancount + nscount + arcount):
                offset = _skip_dns_name(data, offset)
                rtype, _, _, rdlength = struct.unpack('!HHIH', data[offset:offset + 10])
                offset += 10
                if rtype == 1 and rdlength == 4:
                    result = socket.inet_ntoa(data[offset:offset + 4])
                    break
                offset += rdlength
    except (OSError, ValueError, struct.error):
        result = None
    finally:
        sock.close()

    with _mdns_lock:
        _mdns_cache[hostname] = (result, time.monotonic() + MDNS_CACHE_TTL)
    return result

def get_lan_ip():
    """Attempt to detect the primary local LAN IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Does not actually transmit packets; connects to external route
        s.connect(('10.255.255.255', 1))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return '127.0.0.1'
    finally:
        s.close()


def generate_self_signed_cert(cert_path, key_path, lan_ip='127.0.0.1'):
    """Generate a self-signed certificate using openssl CLI."""
    openssl_bin = shutil.which('openssl')
    if not openssl_bin:
        print("❌ Error: 'openssl' command not found in PATH.")
        print("Please install OpenSSL or provide your own 'cert.pem' and 'key.pem'.")
        sys.exit(1)

    # Subject Alternative Names for localhost and LAN IP
    san_entries = ["DNS:localhost", "IP:127.0.0.1"]
    if lan_ip and lan_ip not in ("127.0.0.1", "0.0.0.0"):
        san_entries.append(f"IP:{lan_ip}")
    san_str = ",".join(san_entries)

    cmd_with_ext = [
        openssl_bin, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key_path),
        "-out", str(cert_path),
        "-days", "365",
        "-subj", "/CN=localhost",
        "-addext", f"subjectAltName={san_str}"
    ]

    try:
        subprocess.run(cmd_with_ext, check=True, capture_output=True)
    except subprocess.CalledProcessError:
        # Fallback for older OpenSSL/LibreSSL without -addext support
        cmd_fallback = [
            openssl_bin, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key_path),
            "-out", str(cert_path),
            "-days", "365",
            "-subj", "/CN=localhost"
        ]
        subprocess.run(cmd_fallback, check=True, capture_output=True)


class CustomHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom request handler that adds security/caching headers and stream proxying."""
    def end_headers(self):
        # Allow cross-origin requests for local API queries if needed
        self.send_header('Access-Control-Allow-Origin', '*')
        # Prevent aggressive browser caching during local development
        if self.path.endswith(('.html', '.js', '.css', '.json')):
            self.send_header('Cache-Control', 'no-cache, must-revalidate')
        super().end_headers()

    def do_GET(self):
        if self.path.startswith('/api/proxy?'):
            self.handle_device_proxy('GET')
            return
        if self.path.startswith('/api/stream?') or self.path == '/api/stream':
            self.handle_stream_proxy()
            return
        super().do_GET()

    def do_HEAD(self):
        if self.path.startswith('/api/proxy?'):
            self.handle_device_proxy('HEAD')
            return
        super().do_HEAD()

    def do_POST(self):
        if self.path.startswith('/api/proxy?'):
            self.handle_device_proxy('POST')
            return
        self.send_error(405, "Method not allowed")

    def handle_device_proxy(self, method):
        """Same-origin pass-through proxy for HDHomeRun device requests (JSON APIs, streams, commands)."""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        target_url = params.get('url', [None])[0]
        host_hint = params.get('host', [''])[0].strip().split(':')[0]

        if not target_url:
            self.send_error(400, "Missing 'url' query parameter")
            return

        target = urllib.parse.urlparse(target_url)
        if target.scheme != 'http' or not target.hostname:
            self.send_error(400, "Only plain 'http' device URLs are supported")
            return

        hostname = target.hostname
        if not is_local_host(hostname):
            self.send_error(403, f"Proxying forbidden for non-local host: {hostname}")
            return

        # .local names: resolve via mDNS (host networking), else fall back to the active device IP
        if hostname.endswith('.local'):
            resolved = mdns_resolve(hostname)
            if not resolved and host_hint and not host_hint.endswith('.local') and is_local_host(host_hint):
                resolved = host_hint
            if not resolved:
                self.send_error(502, f"Unable to resolve {hostname}")
                return
            netloc = f"{resolved}:{target.port}" if target.port else resolved
            target_url = urllib.parse.urlunparse(target._replace(netloc=netloc))

        body = None
        if method == 'POST':
            length = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(length) if length > 0 else b''

        req = urllib.request.Request(target_url, data=body, method=method, headers={'User-Agent': 'HDHR-Dash/2.0'})
        if body and self.headers.get('Content-Type'):
            req.add_header('Content-Type', self.headers.get('Content-Type'))
        if self.headers.get('Range'):
            req.add_header('Range', self.headers.get('Range'))

        try:
            upstream = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as e:
            upstream = e
        except Exception as e:
            self.send_error(502, f"Failed to reach device: {e}")
            return

        try:
            self.send_response(getattr(upstream, 'status', None) or upstream.code)
            for name in PROXY_RESPONSE_HEADERS:
                value = upstream.headers.get(name)
                if value:
                    self.send_header(name, value)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()

            if method != 'HEAD':
                while True:
                    chunk = upstream.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            upstream.close()

    def handle_stream_proxy(self):
        """Pass-through streaming proxy for local HDHomeRun tuner streams over HTTP."""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        target_url = params.get('url', [None])[0]

        if not target_url:
            self.send_error(400, "Missing 'url' query parameter")
            return

        target_parsed = urllib.parse.urlparse(target_url)
        if target_parsed.scheme != 'http':
            self.send_error(400, "Only plain 'http' URLs are supported for tuner streaming")
            return

        hostname = target_parsed.hostname
        if not hostname:
            self.send_error(400, "Invalid host in stream URL")
            return

        is_allowed = False
        if hostname in ('localhost', '127.0.0.1') or hostname.endswith('.local'):
            is_allowed = True
        else:
            try:
                ip = ipaddress.ip_address(hostname)
                if ip.is_private or ip.is_loopback:
                    is_allowed = True
            except ValueError:
                pass

        if not is_allowed:
            self.send_error(403, f"Streaming forbidden for non-local host: {hostname}")
            return

        try:
            req = urllib.request.Request(
                target_url,
                headers={'User-Agent': 'HDHR-Dash/2.0'}
            )
            upstream = urllib.request.urlopen(req, timeout=8)
        except Exception as e:
            self.send_error(502, f"Failed to connect to tuner stream: {e}")
            return

        try:
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp2t')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Connection', 'close')
            self.end_headers()

            while True:
                chunk = upstream.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            upstream.close()

    def log_message(self, format, *args):
        # Cleaner console logs
        sys.stderr.write(f"[{self.log_date_time_string()}] {args[0]} {args[1]}\n")


def main():
    parser = argparse.ArgumentParser(description="Serve HDHR Dash over local HTTPS with a self-signed certificate.")
    parser.add_argument("port", nargs="?", type=int, default=8443, help="Port to serve on (default: 8443)")
    parser.add_argument("--bind", "-b", default="0.0.0.0", help="Address to bind to (default: 0.0.0.0)")
    parser.add_argument("--temp", action="store_true", help="Use an ephemeral certificate that is deleted on exit")
    parser.add_argument("--cert", default=None, help="Custom certificate file path (default: cert.pem)")
    parser.add_argument("--key", default=None, help="Custom private key file path (default: key.pem)")
    parser.add_argument(
        "--http",
        action="store_true",
        default=os.environ.get("HDHR_HTTP", "").strip().lower() in ("1", "true", "yes", "on"),
        help="Serve plain HTTP, e.g. behind a TLS-terminating reverse proxy (env: HDHR_HTTP)"
    )
    args = parser.parse_args()

    # Determine project root (directory containing index.html)
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    os.chdir(project_root)

    lan_ip = get_lan_ip()
    temp_dir = None

    if args.http:
        cert_path = key_path = None
    elif args.cert and args.key:
        cert_path = Path(args.cert).resolve()
        key_path = Path(args.key).resolve()
        if not cert_path.exists() or not key_path.exists():
            print(f"🔐 Generating self-signed certificate ({cert_path.name} & {key_path.name})...")
            cert_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.parent.mkdir(parents=True, exist_ok=True)
            generate_self_signed_cert(cert_path, key_path, lan_ip)
    elif args.temp:
        temp_dir = tempfile.TemporaryDirectory()
        cert_path = Path(temp_dir.name) / "cert.pem"
        key_path = Path(temp_dir.name) / "key.pem"
        print("🔐 Generating temporary self-signed certificate...")
        generate_self_signed_cert(cert_path, key_path, lan_ip)
    else:
        cert_path = project_root / "cert.pem"
        key_path = project_root / "key.pem"
        if not cert_path.exists() or not key_path.exists():
            print("🔐 Generating local self-signed certificate (cert.pem & key.pem)...")
            generate_self_signed_cert(cert_path, key_path, lan_ip)

    # Initialize SSL context
    server_address = (args.bind, args.port)
    httpd = http.server.ThreadingHTTPServer(server_address, CustomHTTPRequestHandler)

    # Initialize SSL context
    if not args.http:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        httpd.socket = ssl_ctx.wrap_socket(httpd.socket, server_side=True)

    scheme = "http" if args.http else "https"

    print()
    print("=" * 68)
    print(f"  🚀 HDHR Dash — Local {scheme.upper()} Server")
    print("=" * 68)
    print(f"  Local:    {scheme}://localhost:{args.port}")
    if lan_ip and lan_ip != "127.0.0.1":
        print(f"  Network:  {scheme}://{lan_ip}:{args.port}")
    print("-" * 68)
    if args.http:
        print("  ℹ️  PLAIN HTTP MODE:")
        print("  Intended for use behind a TLS-terminating reverse proxy.")
        print("  Without HTTPS, browsers block Clipboard copy, Service Worker")
        print("  caching, and PWA installation on LAN addresses.")
    else:
        print("  ⚠️  BROWSER SECURITY OVERRIDE REQUIRED:")
        print("  Because this uses a self-signed certificate, your browser will")
        print("  display a warning (e.g., 'Your connection is not private').")
        print()
        print("  To bypass the warning:")
        print("  • Chrome / Edge:  Click 'Advanced' -> 'Proceed to ... (unsafe)'")
        print("    (If no link appears, click anywhere on the page and type 'thisisunsafe')")
        print("  • Safari:         Click 'Show Details' -> 'visit this website'")
        print("  • Firefox:        Click 'Advanced' -> 'Accept the Risk and Continue'")
        print("  • Android Chrome: Tap 'Advanced' -> 'Proceed to ... (unsafe)'")
        print("-" * 68)
        print("  💡 Why HTTPS?")
        print("  Chrome and mobile browsers require HTTPS for Clipboard copy,")
        print("  Service Worker offline caching, and PWA installation on LAN IPs.")
    print("=" * 68)
    print("  Press Ctrl+C to stop the server.\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Stopping server...")
    finally:
        httpd.server_close()
        if temp_dir:
            temp_dir.cleanup()
        print("✓ Server stopped.")


if __name__ == '__main__':
    main()
