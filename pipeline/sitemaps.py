# -*- coding: utf-8 -*-
"""站点地图精细化分层（快赢优化 #1）。

将全站 URL 拆分为三份独立 sitemap，按页面类型区分抓取优先级：
  - sitemap-vertical.xml ：垂直板块落地页 + 聚合 hub 页（priority 0.8）
  - sitemap-tools.xml   ：普通工具详情页（priority 0.7）
  - sitemap-community.xml：含社区引用（Reddit/HN）的页面（priority 0.4）

产物与 robots.txt 一并写入 site/static/，Hugo 构建时自动复制到根目录；
robots.txt 批量声明全部 sitemap 路径，便于 GSC 分组提交、细分赛道收录提速。

零付费、无 LLM、无外部依赖（仅标准库 + 项目内 config/utils）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple
from xml.sax.saxutils import escape as _x

from . import config, utils

LOG = utils.get_logger("sitemaps")

_VERTICAL_PRIORITY = "0.8"
_TOOLS_PRIORITY = "0.7"
_COMMUNITY_PRIORITY = "0.4"

# 社区引用块标记：front-matter 中以 community: 起头且后续存在 - 条目
_COMMUNITY_HEAD_RE = re.compile(r"^community:\s*$", re.M)
_COMMUNITY_ITEM_RE = re.compile(r"^\s*-\s+(title|url):", re.M)


def _has_community(front_matter: str) -> bool:
    return bool(_COMMUNITY_HEAD_RE.search(front_matter)
                and _COMMUNITY_ITEM_RE.search(front_matter))


def _read_front_matter(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    return text[3:end]


def _iter_pages() -> List[Tuple[str, str, str, bool]]:
    """遍历 content 下全部 md，返回 (lang, section, slug, has_community)。"""
    out: List[Tuple[str, str, str, bool]] = []
    lang_dir = config.CONTENT_DIR
    if not lang_dir.exists():
        return out
    for lang_path in sorted(lang_dir.iterdir()):
        if not lang_path.is_dir():
            continue
        lang = lang_path.name
        for section_path in sorted(lang_path.iterdir()):
            if not section_path.is_dir():
                continue
            section = section_path.name
            for md in sorted(section_path.glob("*.md")):
                if md.name == "_index.md":
                    # 落地页 slug 即板块名；落地页永不含社区引用
                    out.append((lang, section, section, False))
                    continue
                fm = _read_front_matter(md)
                out.append((lang, section, md.stem, _has_community(fm)))
    return out


def _url(base: str, lang: str, section: str, slug: str) -> str:
    return "%s%s/%s/%s/" % (base.rstrip("/"), lang, section, slug)


def _sitemap_xml(urls: List[Tuple[str, str]]) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, prio in urls:
        lines.append("  <url>")
        lines.append("    <loc>%s</loc>" % _x(loc))
        lines.append("    <changefreq>weekly</changefreq>")
        lines.append("    <priority>%s</priority>" % prio)
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def build() -> Dict[str, int]:
    """生成三份分层 sitemap + robots.txt（写入 site/static/）。返回各文件条数。"""
    base = str(config.get("site.base_url", "https://freeai.72tool.com/")).rstrip("/") + "/"
    pages = _iter_pages()

    vertical: List[Tuple[str, str]] = []
    tools: List[Tuple[str, str]] = []
    community: List[Tuple[str, str]] = []

    for lang, section, slug, has_comm in pages:
        url = _url(base, lang, section, slug)
        if slug == section:                       # _index 落地页
            vertical.append((url, _VERTICAL_PRIORITY))
        elif section == "hub":                    # 聚合榜单页，与落地页同级权重
            vertical.append((url, _VERTICAL_PRIORITY))
        elif has_comm:                            # 含 Reddit/HN 引用
            community.append((url, _COMMUNITY_PRIORITY))
        else:                                     # 普通工具详情页
            tools.append((url, _TOOLS_PRIORITY))

    static = config.SITE_DIR / "static"
    static.mkdir(parents=True, exist_ok=True)
    counts: Dict[str, int] = {}
    for name, items in (("sitemap-vertical", vertical),
                        ("sitemap-tools", tools),
                        ("sitemap-community", community)):
        path = static / ("%s.xml" % name)
        if items:
            path.write_text(_sitemap_xml(items), encoding="utf-8")
            counts[name] = len(items)
            LOG.info("生成 %s.xml（%d 条）", name, len(items))
        else:
            if path.exists():
                path.unlink()
            counts[name] = 0

    _write_robots(base, static, counts)
    return counts


def _write_robots(base: str, static: Path, counts: Dict[str, int]) -> None:
    """robots.txt 批量声明所有 sitemap 路径（原生分语种 + 本次分层），并补充精细化爬虫管控。"""
    base = base.rstrip("/")
    lines = ["User-agent: *", "Allow: /",
             # 屏蔽构建缓存/测试/查询缓存路径，减少无效抓取与 Cloudflare 带宽消耗
             "Disallow: /tmp/",
             "Disallow: /admin/",
             "Disallow: /*?cache=",
             # 平缓抓取频率，保护源站带宽（Bing 等支持，Google 以 Search Console 为准）
             "Crawl-delay: 2",
             ""]
    # 图片爬虫专项：放开封面/横幅 SVG 抓取，承接图片搜索流量
    lines += ["User-agent: Googlebot-Image", "Allow: /", ""]
    # 原生分语种 sitemap（Hugo 自动生成，覆盖完整）
    for path in ("sitemap.xml", "en/sitemap.xml", "es/sitemap.xml", "id/sitemap.xml"):
        lines.append("Sitemap: %s/%s" % (base, path))
    # 分层 sitemap（仅声明非空者，避免 GSC 报 404）
    for name in ("sitemap-vertical", "sitemap-tools", "sitemap-community"):
        if counts.get(name):
            lines.append("Sitemap: %s/%s.xml" % (base, name))
    lines.append("")
    (static / "robots.txt").write_text("\n".join(lines), encoding="utf-8")
    declared = sum(1 for n in counts if counts[n]) + 4
    LOG.info("生成 robots.txt（声明 %d 份 sitemap + 爬虫管控规则）", declared)


if __name__ == "__main__":
    build()
