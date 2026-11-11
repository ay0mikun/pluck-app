"""Package the coming-soon page without the app or its backend."""

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING_START = '<section id="landing" class="screen landing">'


def build(destination):
    source = (ROOT / "landing-section.html").read_text(encoding="utf-8")
    _, marker, remainder = source.partition(LANDING_START)
    landing, end, _ = remainder.partition("</section>")
    if not marker or not end:
        raise ValueError("The landing section is missing from landing-section.html.")
    title = "Pluck"
    description = "Your UI inspiration, organized"
    site_url = "https://pluck-app-three.vercel.app/"
    preview = f"{site_url}assets/pluck-social-card.png"
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#111111">
  <meta name="description" content="{description}">
  <title>{title}</title>
  <link rel="canonical" href="{site_url}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{site_url}">
  <meta property="og:site_name" content="{title}">
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{description}">
  <meta property="og:image" content="{preview}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{title}">
  <meta name="twitter:description" content="{description}">
  <meta name="twitter:image" content="{preview}">
  <link rel="icon" href="assets/pluck-logo.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="assets/pluck-logo.png">
  <link rel="stylesheet" href="fonts.css">
  <link rel="stylesheet" href="landing.css">
  <script src="landing.js" defer></script>
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
    fonts = (ROOT / "fonts.css").read_text(encoding="utf-8")
    assets = set(re.findall(r'assets/[a-zA-Z0-9_.-]+', html + css + fonts))
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
    (destination / "fonts.css").write_text(fonts, encoding="utf-8")
    shutil.copyfile(ROOT / "landing.js", destination / "landing.js")


if __name__ == "__main__":
    build(Path(sys.argv[1]))
