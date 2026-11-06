"""Package the coming-soon page without the app or its backend."""

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING_START = '<section id="landing" class="screen landing">'


def build(destination):
    source = (ROOT / "index.html").read_text(encoding="utf-8")
    _, marker, remainder = source.partition(LANDING_START)
    landing, end, _ = remainder.partition("</section>")
    if not marker or not end:
        raise ValueError("The landing section is missing from index.html.")
    favicon = "assets/logo.svg"
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#111111">
  <meta name="description" content="A home for the designs you discover on X.">
  <title>Pluck — Your design inspiration, collected</title>
  <link rel="icon" href="{favicon}" type="image/svg+xml">
  <link rel="stylesheet" href="landing.css">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #111; }}
  </style>
</head>
<body>
  {marker}{landing}{end}
</body>
</html>
"""
    css = (ROOT / "landing.css").read_text(encoding="utf-8")
    assets = set(re.findall(r'assets/[a-zA-Z0-9_.-]+', html + css))
    assets.add("assets/geist-OFL.txt")

    # Require an empty output directory so stale app files cannot ship.
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("The landing build directory must be empty.")
    (destination / "assets").mkdir()
    for asset in sorted(assets):
        shutil.copyfile(ROOT / asset, destination / asset)
    (destination / "index.html").write_text(html, encoding="utf-8")
    (destination / "landing.css").write_text(css, encoding="utf-8")


if __name__ == "__main__":
    build(Path(sys.argv[1]))
