# -*- coding: utf-8 -*-
"""一次性回填：为已有 SVG 横幅补齐同款社交分享 PNG（og:image）。

现有页面 front-matter 暂无 image.og 字段，head.html 会回退到 SVG（社交平台不渲染）。
本脚本扫描全站内容，依据 title/group/slug 调用 banners.ensure_banner_png 生成 PNG，
幂等（已存在则跳过）。新页面由 generate.py 在生成时直接产出 PNG，无需本脚本。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline import banners  # noqa: E402

CONTENT = ROOT / "site" / "content"


def _get(block: str, key: str) -> str:
    m = re.search(r'^\s*%s:\s*"?(.*?)"?\s*$' % re.escape(key), block, re.M)
    return m.group(1) if m else ""


def _patch_og_field(md: Path, png_rel: str) -> bool:
    """在 front-matter 的 image 块补 og: 字段，指向 PNG（幂等）。"""
    text = md.read_text(encoding="utf-8")
    fm = text.split("---", 2)
    if len(fm) < 3:
        return False
    block = fm[1]
    if "og:" in block or "image:" not in block:
        return False
    lines = text.split("\n")
    out, added = [], False
    for line in lines:
        out.append(line)
        if not added and line.strip().startswith("src:") and "/banners/" in line and line.strip().endswith('.svg"'):
            out.append('  og: "%s"' % png_rel)
            added = True
    if not added:
        return False
    md.write_text("\n".join(out), encoding="utf-8")
    return True


def main() -> int:
    if not banners._HAS_PIL:
        print("Pillow 不可用，跳过 PNG 回填")
        return 0
    done = patched = 0
    for md in sorted(CONTENT.rglob("*.md")):
        parts = md.relative_to(CONTENT).parts
        if len(parts) < 2:
            continue
        lang = parts[0]
        text = md.read_text(encoding="utf-8")
        segs = text.split("---", 2)
        if len(segs) < 3:
            continue
        block = segs[1]
        if "image:" not in block or "og:" in block:
            continue
        src = _get(block, "src")
        if not src.endswith(".svg"):
            continue
        png_rel = src[:-4] + ".png"          # 与 SVG 同名兄弟文件
        png_disk = banners.config.SITE_DIR / "static" / png_rel.lstrip("/")
        if not png_disk.exists():
            title = _get(block, "title")
            group = _get(block, "group")
            slug = _get(block, "slug") or (parts[1] if len(parts) >= 2 else "")
            if not (title and slug):
                continue
            rel = banners.ensure_banner_png(title, lang, slug, group)
            if not rel:
                continue
            done += 1
        if _patch_og_field(md, png_rel):
            patched += 1
    print("generated PNG:", done, "| patched og field:", patched)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
