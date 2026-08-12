# -*- coding: utf-8 -*-
"""模块6：全自动 SEO 增强（模板内置，无需手动改）。

能力：
1. 标题 / URL / Meta 描述自动差异化生成（多套句式池 × 长尾词，杜绝重复标题与堆砌）；
2. 自动识别页面类型并注入 Schema：FAQPage / Review / ItemList（由 Hugo partial 渲染）；
3. 全站目录层级 ≤ 2 层：/<lang>/<section>/<slug>/ ；sitemap 分语种由 Hugo 模板输出；
4. 每篇自动随机插入 3~5 条同题材内链，底部生成相关推荐聚合；
5. 自动生成榜单聚合页（ItemList），搭建主题集群提升站点权重。

注意：本模块只做“结构化元数据与链接关系”的机械计算，不生成任何解读文案。
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import config, utils

LOG = utils.get_logger("seo")

# 标题句式池：{name} 素材名，{kw} 长尾词，{year} 年份，{region} 地区
TITLE_PATTERNS = {
    "en": [
        "{name} Guide: {kw}",
        "{name} — {kw} ({year})",
        "{kw}: What {name} Offers",
        "{name} Overview for {region}",
        "{kw} | {name} Quick Look",
        "{name}: {kw} Checklist",
    ],
    "es": [
        "{name}: guía sobre {kw}",
        "{name} — {kw} ({year})",
        "{kw}: qué ofrece {name}",
        "{name} en {region}: resumen",
        "{kw} | {name} de un vistazo",
        "{name}: lista de {kw}",
    ],
    "id": [
        "{name}: panduan {kw}",
        "{name} — {kw} ({year})",
        "{kw}: apa yang ditawarkan {name}",
        "Ulasan {name} untuk {region}",
        "{kw} | {name} sekilas",
        "{name}: daftar {kw}",
    ],
}

# Meta 描述句式池（纯结构化拼接，字段来自素材与词库，无 AI 生成）
DESC_PATTERNS = {
    "en": [
        "{summary} Key points on {kw} for readers in {region}.",
        "Quick facts about {name}: {summary} Includes {kw} notes.",
        "{name} at a glance — {summary} Updated {month} {year}.",
    ],
    "es": [
        "{summary} Puntos clave sobre {kw} para lectores de {region}.",
        "Datos rápidos de {name}: {summary} Incluye notas de {kw}.",
        "{name} de un vistazo — {summary} Actualizado en {month} {year}.",
    ],
    "id": [
        "{summary} Poin penting tentang {kw} untuk pembaca di {region}.",
        "Fakta singkat {name}: {summary} Termasuk catatan {kw}.",
        "{name} sekilas — {summary} Diperbarui {month} {year}.",
    ],
}

MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
           "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    "id": ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
           "Agustus", "September", "Oktober", "November", "Desember"],
}

SLUG_SHAPES = ["{name}", "{name}-{kwshort}", "{kwshort}-{name}", "{name}-{region}"]


def month_name(lang: str) -> str:
    return MONTHS.get(lang, MONTHS["en"])[utils.utc_now().month - 1]


def build_title(rng: random.Random, lang: str, name: str, keyword: str,
                region: str) -> str:
    pattern = rng.choice(TITLE_PATTERNS.get(lang, TITLE_PATTERNS["en"]))
    title = pattern.format(name=name, kw=utils.title_case(keyword),
                           year=utils.utc_now().year, region=region)
    return utils.clean_text(title, 68)


def build_description(rng: random.Random, lang: str, name: str, summary: str,
                      keyword: str, region: str) -> str:
    pattern = rng.choice(DESC_PATTERNS.get(lang, DESC_PATTERNS["en"]))
    desc = pattern.format(name=name, summary=summary.rstrip("."), kw=keyword,
                          region=region, month=month_name(lang), year=utils.utc_now().year)
    return utils.clean_text(desc, 158)


def build_slug(rng: random.Random, name: str, keyword: str, region: str) -> str:
    shape = rng.choice(SLUG_SHAPES)
    kwshort = "-".join(utils.slugify(keyword).split("-")[:3])
    raw = shape.format(name=utils.slugify(name), kwshort=kwshort,
                       region=utils.slugify(region))
    return utils.slugify(raw, 72)


def decide_schema(rng: random.Random, has_faq: bool, has_table: bool) -> str:
    """页面类型自动识别 → 对应 Schema 类型。

    有 FAQ 模块 → FAQPage（优先，富摘要收益最高）
    有对比表格 → ItemList
    其余 → Review
    """
    if has_faq and (not has_table or rng.random() < 0.65):
        return "FAQPage"
    if has_table:
        return "ItemList"
    return "Review"


def pick_keywords(rng: random.Random, pools: Dict[str, List[str]], count: int) -> List[str]:
    """跨分类均衡抽取不重复长尾词：问答/对比/人群/支付各取一些。"""
    order = ["question", "comparison", "audience", "payment", "general"]
    picked: List[str] = []
    pool_copy = {k: list(v) for k, v in pools.items()}
    for key in order:
        items = pool_copy.get(key, [])
        if items:
            rng.shuffle(items)
            picked.append(items.pop(0))
    flat = [w for key in order for w in pool_copy.get(key, [])]
    rng.shuffle(flat)
    picked.extend(flat)
    return utils.unique_keep_order(picked)[:count]


# ---------------------------------------------------------------- 内链
class LinkPool:
    """同题材内链池。扫描已存在的 md 文件，供随机内链与相关推荐使用。"""

    def __init__(self, lang: str, section: str) -> None:
        self.lang = lang
        self.section = section
        self.entries: List[Dict[str, str]] = []
        self._scan()

    def _scan(self) -> None:
        folder = config.CONTENT_DIR / self.lang / self.section
        if not folder.exists():
            return
        for path in folder.glob("*.md"):
            title = ""
            try:
                with path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        if line.startswith("title:"):
                            title = line.split(":", 1)[1].strip().strip('"')
                            break
            except OSError:
                continue
            if title:
                self.entries.append({
                    "title": title,
                    "url": "/%s/%s/%s/" % (self.lang, self.section, path.stem),
                })
        LOG.info("[%s] 内链池载入 %d 条已有页面", self.lang, len(self.entries))

    def add(self, title: str, slug: str) -> None:
        self.entries.append({"title": title,
                             "url": "/%s/%s/%s/" % (self.lang, self.section, slug)})

    def sample(self, rng: random.Random, count: int, exclude_slug: str,
               anchors: Sequence[str]) -> List[Dict[str, str]]:
        """随机取 count 条内链；锚文本随机使用长尾词（而非重复标题），更白帽。"""
        pool = [e for e in self.entries if not e["url"].endswith("/%s/" % exclude_slug)]
        if not pool:
            return []
        rng.shuffle(pool)
        chosen = pool[:max(0, count)]
        anchor_list = list(anchors) or [e["title"] for e in chosen]
        out = []
        for idx, entry in enumerate(chosen):
            anchor = anchor_list[idx % len(anchor_list)] if anchor_list else entry["title"]
            out.append({"title": entry["title"], "url": entry["url"],
                        "anchor": utils.title_case(anchor)[:60]})
        return out


# ---------------------------------------------------------------- 聚合页
def build_hub_pages(lang: str, section: str, pages: List[Dict[str, Any]],
                    env, rng: random.Random) -> List[Path]:
    """生成榜单聚合页（ItemList Schema），构建主题集群。

    分组依据：页面主长尾词的分类，天然形成 “问答集合 / 对比集合 / 人群集合” 等集群页。
    """
    if not pages:
        return []
    from . import keywords as kwmod  # 延迟导入，避免循环依赖

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for page in pages:
        grouped.setdefault(page.get("group", "general"), []).append(page)

    template = env.get_template("hub.md.j2")
    written: List[Path] = []
    hub_dir = config.CONTENT_DIR / lang / "hub"
    hub_dir.mkdir(parents=True, exist_ok=True)
    pools = kwmod.load_keywords(lang)
    locale = config.locale_by_code(lang)

    for group, items in grouped.items():
        if len(items) < 2:      # 单条不成榜，避免低质聚合页
            continue
        anchor_kw = (pools.get(group) or pools.get("general") or [group])
        headline = utils.title_case(rng.choice(anchor_kw)[:60])
        slug = utils.slugify("%s-%s-%s" % (group, headline, utils.utc_now().strftime("%Y%m")))
        content = template.render(
            title=utils.yaml_escape("%s (%s)" % (headline, utils.utc_now().year)),
            description=utils.yaml_escape(
                "%s — %d entries updated %s %d." % (headline, len(items),
                                                    month_name(lang), utils.utc_now().year)),
            slug=slug, lang=lang, hugo_lang=locale["hugo_lang"],
            date=utils.iso_now(), group=group, region=locale.get("region", ""),
            items=items, keyword=headline,
        )
        path = hub_dir / ("%s.md" % slug)
        path.write_text(content, encoding="utf-8")
        written.append(path)
        LOG.info("[%s] 生成聚合页 %s（%d 条）", lang, path.name, len(items))
    return written


def verify_sitemap_layout() -> Dict[str, bool]:
    """巡检用：确认 sitemap 相关模板齐备（缺失由 selfheal 兜底生成）。"""
    checks = {
        "sitemap.xml": (config.SITE_DIR / "layouts" / "sitemap.xml").exists(),
        "sitemapindex.xml": (config.SITE_DIR / "layouts" / "sitemapindex.xml").exists(),
        "robots.txt": (config.SITE_DIR / "layouts" / "robots.txt").exists(),
    }
    return checks
