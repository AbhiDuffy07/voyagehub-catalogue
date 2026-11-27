"""What the built site folder must look like (shared by the build and by deploy)."""

import hashlib
import re

# Data files are revalidated on every load (the browser asks, Cloudflare answers 304 from the ETag
# when nothing changed), so a new deploy reaches the app at once. Only the small index (and its
# per-country shards in a world run) and the manifest may be served from cache, for a minute.
HEADERS = """/v1/index.json
  Cache-Control: public, max-age=60
/v1/index/*
  Cache-Control: public, max-age=60
/v1/manifest.json
  Cache-Control: public, max-age=60
/v1/attributions.json
  Cache-Control: public, max-age=0, must-revalidate
/v1/attributions/*
  Cache-Control: public, max-age=0, must-revalidate
/v1/cities/*
  Cache-Control: public, max-age=0, must-revalidate
"""
# The page at the site root: what this site is, and where the credits are. Plain HTML, no script
# and no external file, so it works offline and cannot break. It is not in the manifest (the data
# version); the build writes it exactly as it is here and deploy refuses any other content.
LANDING_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>VoyageHub catalogue</title>
<style>
body { font: 1rem/1.5 system-ui, sans-serif; max-width: 36rem; margin: 3rem auto; padding: 0 1rem; }
a { color: LinkText; }
</style>
</head>
<body>
<h1>VoyageHub catalogue</h1>
<p>VoyageHub catalogue: city data for the VoyageHub app, with credits.</p>
<p><a href="/v1/attributions.json">Credits and licences for every photo and text</a></p>
</body>
</html>
"""
# The answer for any path the site does not have. Without a top-level 404.html, Cloudflare Pages
# treats the site as a single-page app and answers every unknown path with index.html and status
# 200, so a missing city file would look like a page. With it, unknown paths get this page and 404.
# Same rules as LANDING_PAGE: plain HTML, no script, written exactly as here, checked by deploy.
NOT_FOUND_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Not found - VoyageHub catalogue</title>
<style>
body { font: 1rem/1.5 system-ui, sans-serif; max-width: 36rem; margin: 3rem auto; padding: 0 1rem; }
a { color: LinkText; }
</style>
</head>
<body>
<h1>Not found</h1>
<p>This file is not in the VoyageHub catalogue.</p>
<p><a href="/">VoyageHub catalogue home</a></p>
</body>
</html>
"""
# The files at the site root beside v1/, each written exactly as here by the build and checked by
# deploy.
FIXED_FILES = {"_headers": HEADERS, "index.html": LANDING_PAGE, "404.html": NOT_FOUND_PAGE}
# A published file name: lowercase, digits, and - _ . /; no leading dot or slash, no ".." part.
FILE_NAME = re.compile(
    r"^[a-z0-9][a-z0-9_-]*(\.[a-z0-9_-]+)*(/[a-z0-9][a-z0-9_-]*(\.[a-z0-9_-]+)*)*$"
)


def version_of(hashes: dict[str, str]) -> str:
    """The site version: a hash over every file name and content hash (manifest excluded)."""
    text = "".join(f"{name}:{digest}\n" for name, digest in sorted(hashes.items()))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
