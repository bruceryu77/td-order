"""Local server for T&D order + admin save (writes files, then pushes to GitHub)."""
from __future__ import annotations

import json
import mimetypes
import re
import subprocess
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765

GIT_AUTHOR_NAME = "bruceryu77"
GIT_AUTHOR_EMAIL = "bruceryu77@users.noreply.github.com"
PUBLISH_PATHS = ("products.json", "products-data.js", "images")


def _git(args: list[str], timeout: int = 90) -> subprocess.CompletedProcess[str]:
    kwargs: dict = {
        "args": ["git", *args],
        "cwd": str(ROOT),
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
    }
    # Hide extra console windows on Windows
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(**kwargs)


def publish_to_github() -> dict:
    """Stage catalog files, commit if needed, push to origin."""
    if not (ROOT / ".git").is_dir():
        return {"ok": False, "pushed": False, "error": "Not a git repository"}

    try:
        add = _git(["add", "--", *PUBLISH_PATHS])
        if add.returncode != 0:
            return {
                "ok": False,
                "pushed": False,
                "error": (add.stderr or add.stdout or "git add failed").strip(),
            }

        status = _git(["status", "--porcelain", "--", *PUBLISH_PATHS])
        if status.returncode != 0:
            return {
                "ok": False,
                "pushed": False,
                "error": (status.stderr or "git status failed").strip(),
            }
        if not status.stdout.strip():
            return {
                "ok": True,
                "pushed": False,
                "message": "No catalog changes to push",
            }

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        commit = _git(
            [
                "-c", f"user.name={GIT_AUTHOR_NAME}",
                "-c", f"user.email={GIT_AUTHOR_EMAIL}",
                "commit",
                "-m", f"Admin catalog update ({stamp})",
            ]
        )
        if commit.returncode != 0:
            return {
                "ok": False,
                "pushed": False,
                "error": (commit.stderr or commit.stdout or "git commit failed").strip(),
            }

        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
        ref = (branch.stdout or "main").strip() or "main"

        push = _git(["push", "origin", "HEAD"], timeout=120)
        if push.returncode != 0:
            return {
                "ok": False,
                "pushed": False,
                "error": (push.stderr or push.stdout or "git push failed").strip(),
            }

        return {
            "ok": True,
            "pushed": True,
            "branch": ref,
            "message": f"Pushed to origin/{ref}",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "pushed": False, "error": "Git push timed out"}
    except Exception as exc:
        return {"ok": False, "pushed": False, "error": str(exc)}


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

            github = None
            if payload.get("pushGithub", True):
                print("Publishing catalog to GitHub...")
                github = publish_to_github()
                if github.get("ok") and github.get("pushed"):
                    print("GitHub push OK:", github.get("message"))
                elif github.get("ok"):
                    print("GitHub:", github.get("message") or "nothing to push")
                else:
                    print("GitHub push failed:", github.get("error"))

            self._json(
                200,
                {
                    "ok": True,
                    "count": len(catalog["products"]),
                    "serverVersion": 2,
                    "github": github if github is not None else {
                        "ok": False,
                        "pushed": False,
                        "error": "GitHub publish skipped",
                    },
                },
            )
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
    print("Save in Admin → writes files + pushes to GitHub automatically.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
