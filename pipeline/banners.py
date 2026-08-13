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

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PIL = True
except Exception:  # pragma: no cover - Pillow 缺省时仅跳过 PNG，不影响 SVG/构建
    _HAS_PIL = False

BANNER_W, BANNER_H = 800, 450
# Open Graph / 社交分享图标准尺寸
OG_W, OG_H = 1200, 630


def _hue(s: str) -> int:
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return int(h[:7], 16) % 360


def _hsl_to_rgb(h: int, s: int, l: int):
    """HSL(0-360, 0-100, 0-100) -> (r,g,b) 0-255，用于渐变背景绘制。"""
    c = (1 - abs(2 * l / 100 - 1)) * (s / 100)
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l / 100 - c / 2
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return int(round((r + m) * 255)), int(round((g + m) * 255)), int(round((b + m) * 255))


def _load_font(size: int, bold: bool = False):
    """跨平台加载无衬线字体；找不到则用 PIL 默认字体（仍可用）。"""
    if not _HAS_PIL:
        return None
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def ensure_banner_png(name: str, lang: str, slug: str, category: str = "") -> str:
    """生成 1200x630 社交分享 PNG（纯 Pillow，无系统级 cairo 依赖），
    返回站点绝对路径；已存在则直接复用。与 SVG 横幅共用同一确定性配色与文案。"""
    if not _HAS_PIL:
        return ""
    out_dir = config.SITE_DIR / "static" / "banners" / lang
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("%s.png" % slug)
    rel = "/banners/%s/%s.png" % (lang, slug)
    if path.exists():
        return rel

    hue = _hue(name + lang)
    hue2 = (hue + 38) % 360
    title = utils.clean_text(name, 60)
    cat = utils.clean_text(category or "Free AI Tools", 36)
    region = config.locale_by_code(lang).get("region", "")
    sub = "%s · %s" % (cat, region)

    img = Image.new("RGB", (OG_W, OG_H))
    draw = ImageDraw.Draw(img)
    # 竖直渐变背景
    top = _hsl_to_rgb(hue, 55, 32)
    bot = _hsl_to_rgb(hue2, 60, 16)
    for y in range(OG_H):
        t = y / (OG_H - 1)
        r = int(top[0] + (bot[0] - top[0]) * t)
        g = int(top[1] + (bot[1] - top[1]) * t)
        b = int(top[2] + (bot[2] - top[2]) * t)
        draw.line([(0, y), (OG_W, y)], fill=(r, g, b))
    # 右上柔光圆
    draw.ellipse([OG_W - 360, -160, OG_W + 120, 320],
                 fill=_hsl_to_rgb(hue, 60, 45), outline=None)
    # 用半透明叠加模拟 opacity（Pillow 直接画即近似）
    overlay = Image.new("RGB", (OG_W, OG_H), _hsl_to_rgb(hue, 60, 45))
    img = Image.blend(img, overlay, 0.12)
    draw = ImageDraw.Draw(img)

    font_title = _load_font(52, bold=True)
    font_sub = _load_font(26)
    font_brand = _load_font(24, bold=True)
    pad = 70
    # 标题自动换行（按宽度）
    max_w = OG_W - pad * 2
    words = title.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font_title) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    lines = lines[:3]
    ty = OG_H // 2 - len(lines) * 30 - 10
    for ln in lines:
        draw.text((pad, ty), ln, fill="#ffffff", font=font_title)
        ty += 62
    # 副标题
    draw.text((pad, OG_H // 2 + 78), sub, fill="#cbd5e1", font=font_sub)
    # 品牌行
    draw.text((pad, OG_H - 70), "AutoGuide", fill="#ffffff", font=font_brand)
    img.save(path, "PNG", optimize=True)
    return rel


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
