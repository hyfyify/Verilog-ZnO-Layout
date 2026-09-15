from __future__ import annotations

import argparse
import json
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .cli import compile_design


WEB_ROOT = Path(__file__).with_name("web")
MAX_SOURCE_BYTES = 1_000_000


class MobileHandler(BaseHTTPRequestHandler):
    server_version = "ZnOMobile/0.1"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in {"/", "/index.html"}:
            body = (WEB_ROOT / "index.html").read_bytes()
            self._send(200, body, "text/html; charset=utf-8")
        elif self.path == "/api/health":
            self._send(200, b'{"ok":true}', "application/json")
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        if self.path != "/api/compile":
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_SOURCE_BYTES:
                raise ValueError("Verilog must be between 1 byte and 1 MB")
            request = json.loads(self.rfile.read(length))
            source_text = str(request.get("source", ""))
            top = request.get("top") or None
            if not source_text.strip():
                raise ValueError("Verilog source is empty")
            with tempfile.TemporaryDirectory(prefix="zno-mobile-") as temporary:
                work = Path(temporary)
                source = work / "design.txt"
                output = work / "build"
                source.write_text(source_text, encoding="utf-8")
                root = Path.cwd()
                pdk = root / "pdk" / "default.json"
                if not pdk.exists():
                    pdk = Path(__file__).parents[2] / "pdk" / "default.json"
                exit_code = compile_design(source, output, pdk, top)
                report = (output / "drc_report.txt").read_text(encoding="utf-8")
                netlist = (output / "netlist.json").read_text(encoding="utf-8")
                svg_file = output / "layout.svg"
                response = {
                    "ok": exit_code == 0,
                    "exit_code": exit_code,
                    "report": report,
                    "netlist": json.loads(netlist),
                    "svg": svg_file.read_text(encoding="utf-8") if svg_file.exists() else "",
                }
            payload = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self._send(200, payload, "application/json; charset=utf-8")
        except Exception as error:
            payload = json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False).encode("utf-8")
            self._send(400, payload, "application/json; charset=utf-8")

    def log_message(self, format: str, *args: object) -> None:
        print(f"[mobile] {self.address_string()} {format % args}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mobile web UI for the ZnO layout compiler")
    parser.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 for phones on the same LAN")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), MobileHandler)
    print(f"ZnO mobile UI: http://{args.host}:{args.port}")
    if args.host == "0.0.0.0":
        print("Open http://<this-PC-LAN-IP>:%d on your phone" % args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
