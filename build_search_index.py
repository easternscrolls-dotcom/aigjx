#!/usr/bin/env python3
"""Generate site/static/search.json from content/tools front matter.

Run locally whenever tool content changes, then commit the resulting
site/static/search.json (Cloudflare Pages only runs `hugo --minify`, so the
static index must be committed). Zero external dependencies.
"""
import json
import os
import re
import glob

ROOT = os.path.dirname(os.path.abspath(__file__))
CONTENT = os.path.join(ROOT, "site", "content", "tools")
OUT = os.path.join(ROOT, "site", "static", "search.json")


def _strip_quotes(s):
    s = s.strip()
    if len(s) >= 2 and s[0] in "\"'":
        if s[-1] == s[0]:
            return s[1:-1]
    return s


def _parse_value(val):
    val = val.strip()
    # Inline YAML list: [a, b, c] or ["a", "b"]
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(p) for p in inner.split(",") if p.strip()]
    if not val:
        return []
    return _strip_quotes(val)


def parse_frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return {}, text
    fm = m.group(1)
    body = text[m.end():]
    data = {}
    key = None
    for line in fm.splitlines():
        if not line.strip():
            continue
        if re.match(r"^\s+-\s+", line):
            if key:
                data.setdefault(key, []).append(_strip_quotes(line.strip()[2:]))
            continue
        mm = re.match(r'^([A-Za-z_]+):\s*(.*)$', line)
        if mm:
            key = mm.group(1)
            data[key] = _parse_value(mm.group(2))
    return data, body


def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    items = []
    for path in sorted(glob.glob(os.path.join(CONTENT, "*.md"))):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        fm, _ = parse_frontmatter(text)
        slug = os.path.splitext(os.path.basename(path))[0]
        if str(fm.get("draft", "")).lower() in ("true", "1", "yes"):
            continue
        items.append({
            "title": fm.get("title", slug),
            "description": fm.get("description", ""),
            "url": "/tools/%s/" % slug,
            "categories": as_list(fm.get("categories")),
            "tags": as_list(fm.get("tags")),
        })
    items.sort(key=lambda x: x["title"].lower())
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print("Wrote %d entries to %s" % (len(items), OUT))


if __name__ == "__main__":
    main()
