"""Local server for T&D order + admin save (writes products files to this folder)."""
from __future__ import annotations

import json
import mimetypes
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        path = unquote(self.path.split("?", 1)[0])
        if path != "/api/save":
            self.send_error(404, "Not Found")
            return

        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json(400, {"ok": False, "error": "Invalid JSON"})
            return

        catalog = payload.get("catalog")
        if not isinstance(catalog, dict) or not isinstance(catalog.get("products"), list):
            self._json(400, {"ok": False, "error": "catalog.products required"})
            return

        # Guard against accidental empty overwrite
        if len(catalog["products"]) == 0:
            existing = ROOT / "products.json"
            if existing.exists():
                try:
                    prev = json.loads(existing.read_text(encoding="utf-8"))
                    if isinstance(prev.get("products"), list) and len(prev["products"]) > 0:
                        self._json(400, {"ok": False, "error": "Refusing to save empty catalog over existing data"})
                        return
                except Exception:
                    pass

        try:
            (ROOT / "products.json").write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (ROOT / "products-data.js").write_text(
                "window.PRODUCTS_DATA = " + json.dumps(catalog, ensure_ascii=False) + ";\n",
                encoding="utf-8",
            )

            image = payload.get("image")
            if isinstance(image, dict) and image.get("name") and image.get("dataUrl"):
                name = Path(str(image["name"]).replace("\\", "/")).name
                if not re.match(r"^[\w.\-]+\.(jpe?g|png|webp|gif)$", name, re.I):
                    raise ValueError("Invalid image name")
                data_url = str(image["dataUrl"])
                m = re.match(r"^data:image/[^;]+;base64,(.+)$", data_url, re.S)
                if not m:
                    raise ValueError("Invalid image dataUrl")
                import base64

                img_bytes = base64.b64decode(m.group(1))
                images_dir = ROOT / "images"
                images_dir.mkdir(exist_ok=True)
                (images_dir / name).write_bytes(img_bytes)

            self._json(200, {"ok": True, "count": len(catalog["products"])})
        except Exception as exc:
            self._json(500, {"ok": False, "error": str(exc)})

    def _json(self, status: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))


def main():
    mimetypes.add_type("application/javascript", ".js")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"T&D server running at http://{HOST}:{PORT}/")
    print(f"Admin: http://{HOST}:{PORT}/admin.html")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
