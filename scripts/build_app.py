"""Build the static app and same-origin backend proxy for Vercel."""

import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = ("index.html", "app.js", "styles.css", "fonts.css", "manifest.webmanifest")


def build(destination, api_origin):
    origin = urlsplit(api_origin)
    if (origin.scheme != "https" or not origin.hostname or origin.username
            or origin.password or origin.path not in ("", "/")
            or origin.query or origin.fragment):
        raise ValueError("Set PLUCK_API_ORIGIN to the production backend HTTPS origin.")
    api_origin = api_origin.rstrip("/")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("The app build directory must be empty.")
    static = destination / "static"
    (static / "assets").mkdir(parents=True)
    sources = ""
    for name in PUBLIC_FILES:
        sources += (ROOT / name).read_text(encoding="utf-8")
        shutil.copyfile(ROOT / name, static / name)
    assets = set(re.findall(r"assets/[a-zA-Z0-9_.-]+\.[a-zA-Z0-9]+", sources))
    # Preview icons are selected by name at runtime.
    assets.update(str(path.relative_to(ROOT)) for path in (ROOT / "assets").glob("preview-*.svg"))
    assets.add("assets/geist-OFL.txt")
    for asset in sorted(assets):
        shutil.copyfile(ROOT / asset, static / asset)
    config = {
        "version": 3,
        "routes": [
            {"src": "/(api|uploads|thumbnails|avatars|previews)/(.*)",
             "dest": api_origin + "/$1/$2",
             "headers": {"Cache-Control": "private, no-store"}},
            {"src": "/(?:index\\.html)?", "headers": {"Cache-Control": "no-cache"}, "continue": True},
            {"handle": "filesystem"},
        ],
    }
    (destination / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build(sys.argv[1], os.environ.get("PLUCK_API_ORIGIN", ""))
