# -*- coding: utf-8 -*-
"""本地确定性 SVG 头图生成（零付费、无 LLM、无外部热链）。

每张工具页按 slug 生成一张 800x450 的渐变横幅，颜色由工具名哈希决定，
文字仅含工具名 + 分类 + 地区（均已清洗，无源站机器字段）。
生成后落到 site/static/banners/<lang>/<slug>.svg，由 publish 步骤一并提交，
彻底摆脱对 Reddit 外链预览图的 hotlink 依赖。
"""

from __future__ import annotations

import hashlib
import xml.sax.saxutils as _sx
from pathlib import Path

from . import config, utils

BANNER_W, BANNER_H = 800, 450


def _hue(s: str) -> int:
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return int(h[:7], 16) % 360


def ensure_banner(name: str, lang: str, slug: str, category: str = "") -> str:
    """生成横幅并返回站点绝对路径（以 / 开头）；已存在则直接复用。"""
    out_dir = config.SITE_DIR / "static" / "banners" / lang
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("%s.svg" % slug)
    rel = "/banners/%s/%s.svg" % (lang, slug)
    if path.exists():
        return rel

    hue = _hue(name + lang)
    hue2 = (hue + 38) % 360
    title = _sx.escape(utils.clean_text(name, 48))
    cat = _sx.escape(utils.clean_text(category or "Free AI Tools", 28))
    region = _sx.escape(config.locale_by_code(lang).get("region", ""))
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        'viewBox="0 0 {w} {h}">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0%" stop-color="hsl({a},55%,32%)"/>'
        '<stop offset="100%" stop-color="hsl({b},60%,16%)"/>'
        '</linearGradient></defs>'
        '<rect width="{w}" height="{h}" fill="url(#g)"/>'
        '<circle cx="680" cy="90" r="120" fill="hsl({a},60%,45%)" opacity="0.18"/>'
        '<text x="40" y="{ty}" fill="#ffffff" font-family="Segoe UI, Arial, sans-serif" '
        'font-size="34" font-weight="700">{title}</text>'
        '<text x="40" y="{cy}" fill="#cbd5e1" font-family="Segoe UI, Arial, sans-serif" '
        'font-size="18">{cat} · {region}</text>'
        '</svg>'
    ).format(w=BANNER_W, h=BANNER_H, a=hue, b=hue2, ty=BANNER_H // 2 - 8,
             cy=BANNER_H // 2 + 26, title=title, cat=cat, region=region)
    path.write_text(svg, encoding="utf-8")
    return rel
