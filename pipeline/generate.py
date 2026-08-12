# -*- coding: utf-8 -*-
"""模块4：无 AI 模块化内容生成（核心）。

**严格红线：本模块不调用任何生成式大模型。** 全部正文由以下机械手段拼装：
  - Jinja2 五大可复用组件（开篇 / 优势清单 / 避雷缺点 / 对比表格 / FAQ）+ 结尾组件；
  - 随机重组引擎：随机打乱组件顺序、随机取舍可选组件、随机图片布局 → 千页千面；
  - 固定短句库 CSV（小标题 / 过渡句 / 优点条目 / 缺点条目 / 结尾引导）随机抽取；
  - FAQ 库随机抽 5~8 条，内链锚文本随机匹配长尾词；
  - 页面字数在 300~900 词之间随机浮动，低于 300 词直接丢弃；
  - 全局正则后置处理：地区词汇替换、货币/支付渠道本地化、违规极限词替换。

用法：
    python -m pipeline.generate
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote as _url_quote

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import banners, compliance, config, keywords as kwmod, seo, utils

LOG = utils.get_logger("generate")


# ---------------------------------------------------------------- 资源加载
def jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(config.TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,   # 变量缺失立即报错，避免线上出现空白占位
        autoescape=False,            # 输出 Markdown，不做 HTML 转义
    )
    env.filters["yaml"] = utils.yaml_escape
    env.filters["titlecase"] = utils.title_case
    return env


def load_snippets(lang: str) -> Dict[str, List[str]]:
    """短句库：slot -> 句子列表。lang=all 的条目对所有语种生效。"""
    out: Dict[str, List[str]] = {}
    for row in utils.read_csv_rows(config.SNIPPETS_CSV):
        if row.get("lang") not in (lang, "all"):
            continue
        slot = row.get("slot", "")
        text = row.get("text", "")
        if slot and text:
            out.setdefault(slot, []).append(text)
    return out


def load_faqs(lang: str) -> List[Dict[str, str]]:
    return [r for r in utils.read_csv_rows(config.FAQ_CSV) if r.get("lang") == lang]


def load_replacements(lang: str) -> List[Tuple[re.Pattern, str]]:
    """地区/货币/支付渠道本地化替换规则（CSV 驱动，全局正则后置处理）。"""
    rules: List[Tuple[re.Pattern, str]] = []
    for row in utils.read_csv_rows(config.LOCALIZATION_CSV):
        if row.get("lang") not in (lang, "all"):
            continue
        pattern, replacement = row.get("pattern", ""), row.get("replacement", "")
        if not pattern:
            continue
        try:
            flags = re.IGNORECASE if row.get("kind", "regex") != "case" else 0
            rules.append((re.compile(pattern, flags), replacement))
        except re.error as exc:
            LOG.warning("本地化正则非法已跳过 [%s]: %s", pattern, exc)
    return rules


# ---------------------------------------------------------------- 变量插值
def interpolate(text: str, ctx: Dict[str, str]) -> str:
    """短句/FAQ 中的 {name} {region} {currency} {payments} {kw} 占位符替换。

    使用 str.format_map + 容错字典，缺字段不报错（避免流水线中断）。
    """
    class _Safe(dict):
        def __missing__(self, key: str) -> str:  # noqa: D105
            return ""
    try:
        return str(text).format_map(_Safe(ctx))
    except Exception:  # noqa: BLE001 —— 极端格式串直接原样返回
        return str(text)


def pick(rng, pool: Sequence[str], fallback: str = "") -> str:
    return rng.choice(list(pool)) if pool else fallback


def pick_many(rng, pool: Sequence[str], count: int) -> List[str]:
    items = list(pool)
    rng.shuffle(items)
    return items[:max(0, count)]


# ---------------------------------------------------------------- 组件渲染
COMPONENT_TEMPLATES = {
    "intro": "components/intro.md.j2",
    "pros": "components/pros.md.j2",
    "cons": "components/cons.md.j2",
    "table": "components/table.md.j2",
    "faq": "components/faq.md.j2",
    "outro": "components/outro.md.j2",
}


def build_component_order(rng) -> List[str]:
    """随机重组引擎（含 6 个月权重轮换）：决定本页出现哪些组件、以什么顺序。

    规则：开篇固定首位；FAQ 必出但位置随机；其余可选组件按当前月份选中的
    weight_preset 做加权取舍与排序，实现周期性结构轮换、降低模板撞车。
    """
    required = list(config.get("generate.required_components", ["intro", "faq"]))
    optional = list(config.get("generate.optional_components", ["pros", "cons", "table", "outro"]))
    presets = config.get("generate.weight_presets", None)
    rotation = int(config.get("generate.rotation_months", 0) or 0)
    weights = None
    if presets and rotation:
        idx = ((utils.utc_now().year * 12 + utils.utc_now().month) // rotation) % len(presets)
        weights = presets[idx % len(presets)]

    take = rng.randint(2, len(optional)) if optional else 0
    if weights:
        chosen = _weighted_sample(rng, optional, weights, take)
    else:
        chosen = pick_many(rng, optional, take)
    body_parts = [c for c in required if c != "intro"] + chosen
    if weights:
        # 按权重降序排，同权重随机打散
        body_parts.sort(key=lambda c: (-float(weights.get(c, 1)), rng.random()))
    else:
        rng.shuffle(body_parts)
    order = (["intro"] if "intro" in required else []) + body_parts
    # outro 若入选则强制置尾，保证结构自然
    if "outro" in order:
        order = [c for c in order if c != "outro"] + ["outro"]
    return order


def _weighted_sample(rng, items: Sequence[str], weights: Dict[str, float], k: int) -> List[str]:
    """按权重不放回抽取 k 个（缺省权重 1）。"""
    pool = [(it, max(0.01, float(weights.get(it, 1)))) for it in items]
    chosen: List[str] = []
    while len(chosen) < k and pool:
        total = sum(w for _, w in pool)
        r = rng.random() * total
        acc = 0.0
        pick = 0
        for i, (it, w) in enumerate(pool):
            acc += w
            if r <= acc:
                pick = i
                break
        chosen.append(pool.pop(pick)[0])
    return chosen


def render_body(env: Environment, order: Sequence[str], ctx: Dict[str, Any],
                target_words: int) -> str:
    """按顺序渲染组件并做字数控制：达标即停，避免无意义堆字。"""
    chunks: List[str] = []
    total = 0
    for name in order:
        template = env.get_template(COMPONENT_TEMPLATES[name])
        piece = template.render(**ctx).strip()
        if not piece:
            continue
        chunks.append(piece)
        total += utils.word_count(piece)
        # outro 之外的组件达到目标字数即停止追加（outro 仍会因置尾而保留）
        if total >= target_words and name not in ("intro", "faq"):
            break
    return "\n\n".join(chunks).strip() + "\n"


# ---------------------------------------------------------------- 单页生成
def _unique_path(folder: Path, slug: str) -> Tuple[Path, str]:
    """同名文件自动重命名（slug、slug-2、slug-3...），防止覆盖已有页面。"""
    candidate = slug
    idx = 2
    while (folder / ("%s.md" % candidate)).exists():
        candidate = "%s-%d" % (slug, idx)
        idx += 1
    return folder / ("%s.md" % candidate), candidate


def build_page(item: Dict[str, Any], lang: str, env: Environment,
               pools: Dict[str, List[str]], snippets: Dict[str, List[str]],
               faqs: List[Dict[str, str]], repl: List[Tuple[re.Pattern, str]],
               link_pool: seo.LinkPool) -> Optional[Dict[str, Any]]:
    """生成单个页面。返回页面元数据；被合规拦截则返回 None。"""
    locale = config.locale_by_code(lang)
    section = config.topic_section()
    # 确定性随机：同一素材重跑结构稳定，不同素材结构各异
    rng = utils.seeded_random("%s|%s|%s" % (item["id"], lang, config.current_topic()))

    kw_count = rng.randint(*config.get("generate.keywords_per_item", [5, 10]))
    page_keywords = seo.pick_keywords(rng, pools, kw_count)
    if not page_keywords:
        page_keywords = [utils.slugify(item["name"]).replace("-", " ")]
    main_kw = page_keywords[0]

    region = locale.get("region", "")
    payments = ", ".join(locale.get("payment_channels", []) or [])
    var_ctx = {
        "name": item["name"], "region": region, "kw": main_kw,
        "currency": locale.get("currency_symbol", ""),
        "currency_code": locale.get("currency_code", ""),
        "payments": payments, "brand": config.get("site.brand", ""),
        "year": str(utils.utc_now().year), "month": seo.month_name(lang),
    }

    # 双保险：采集层已清洗，这里再剥一次，确保任何历史/外部译文里的
    # Reddit/HN 机器字段（"submitted by /u/... [link] [comments]" 等）不会进正文。
    summary = utils.strip_source_cruft(item["summary"])
    faq_count = rng.randint(*config.get("generate.faq_pick_range", [5, 8]))
    chosen_faqs = [
        {"q": interpolate(f.get("question", ""), var_ctx),
         "a": interpolate(f.get("answer", ""), var_ctx)}
        for f in pick_many(rng, faqs, faq_count)
    ]
    chosen_faqs = [f for f in chosen_faqs if f["q"] and f["a"]]

    layout = pick(rng, config.get("generate.image_layouts", ["hero", "side", "textonly"]), "textonly")
    # 头图改为本地生成（见 build_page 中 ensure_banner），此处不再依赖外部图源
    alt_text = utils.clean_text(" ".join(page_keywords[:2]) + " " + item["name"], 110)

    link_count = rng.randint(*config.get("generate.internal_links_range", [3, 5]))
    related = link_pool.sample(rng, link_count, "", page_keywords[1:])

    pros = [interpolate(s, var_ctx) for s in pick_many(rng, snippets.get("pro_point", []), rng.randint(4, 6))]
    cons = [interpolate(s, var_ctx) for s in pick_many(rng, snippets.get("con_point", []), rng.randint(3, 4))]
    # 表格：表头与行都来自短句库的 "左|右" 成对条目，避免随机配对导致语义错配
    head_raw = pick(rng, snippets.get("table_head", []), "Aspect|Details")
    head_parts = [p.strip() for p in interpolate(head_raw, var_ctx).split("|")]
    table_head = (head_parts + ["Details"])[:2]
    pairs = pick_many(rng, snippets.get("table_pair", []), rng.randint(4, 6))
    table_rows: List[Dict[str, str]] = []
    for raw in pairs or ["Item|-"]:
        parts = [p.strip() for p in interpolate(raw, var_ctx).split("|")]
        table_rows.append({"label": parts[0], "value": parts[1] if len(parts) > 1 else "-"})

    target_words = rng.randint(*config.get("generate.target_words_range", [300, 900]))
    order = build_component_order(rng)

    ctx: Dict[str, Any] = {
        "item": item, "lang": lang, "region": region, "summary": summary,
        "keywords": page_keywords, "main_kw": main_kw,
        "h2": {slot: interpolate(pick(rng, snippets.get(slot, []), ""), var_ctx)
               for slot in ("h2_intro", "h2_pros", "h2_cons", "h2_table", "h2_faq", "h2_related")},
        "transitions": [interpolate(s, var_ctx) for s in pick_many(rng, snippets.get("transition", []), 4)],
        "closing": interpolate(pick(rng, snippets.get("closing", []), ""), var_ctx),
        "disclaimer": interpolate(pick(rng, snippets.get("disclaimer", []), ""), var_ctx),
        "pros": pros, "cons": cons, "table_rows": table_rows, "table_head": table_head,
        "faqs": chosen_faqs, "layout": layout, "alt": alt_text, "related": related,
        "payments": payments, "currency": locale.get("currency_symbol", ""),
        "outbound": item.get("affiliate_url", ""), "vars": var_ctx,
    }

    # ---- 渲染 + 合规校验（软阈值触发时自动截取片段后复渲染一次）----
    body = render_body(env, order, ctx, target_words)
    body = postprocess(body, lang, repl)
    verdict = compliance.validate_page(body, item.get("summary_src") or summary, lang)
    if not verdict["ok"] and verdict["action"] in ("soft", "reject"):
        shortened = compliance.trim_snippet(summary)
        if shortened != summary:
            ctx["summary"] = shortened
            body = postprocess(render_body(env, order, ctx, target_words), lang, repl)
            verdict = compliance.validate_page(body, item.get("summary_src") or summary, lang)
    if verdict["action"] == "soft" and verdict["ok"]:
        LOG.info("[%s] %s 相似度进入软阈值(%.2f%%)，已自动截取片段",
                 lang, item["name"][:32], verdict["similarity"] * 100)
    if not verdict["ok"]:
        LOG.warning("[%s] 拦截页面 %s -> %s", lang, item["name"][:32], verdict["reason"])
        return None

    # ---- 标题 / 描述 / slug / Schema ----
    title = postprocess(seo.build_title(rng, lang, item["name"], main_kw, region), lang, repl)
    description = postprocess(
        seo.build_description(rng, lang, item["name"], ctx["summary"], main_kw, region), lang, repl)
    slug = seo.build_slug(rng, item["name"], main_kw, region)
    schema_type = seo.decide_schema(rng, bool(chosen_faqs), "table" in order)

    folder = config.CONTENT_DIR / lang / section
    folder.mkdir(parents=True, exist_ok=True)
    path, final_slug = _unique_path(folder, slug)

    # 本地 SVG 头图（摆脱外链热依赖）；生成失败则降级为无图布局
    image_src = ""
    if layout != "textonly":
        try:
            image_src = banners.ensure_banner(item["name"], lang, final_slug, kwmod.classify(main_kw))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("[%s] 头图生成失败，降级无图: %s", lang, exc)
            layout = "textonly"
            image_src = ""
    # 分销/变现外链：enabled+affiliate 时用映射链接，否则占位跳源站（带 utm + nofollow sponsored）
    mon = config.get("monetization", {}) or {}
    if mon.get("enabled") and str(mon.get("mode")) == "affiliate" and item.get("affiliate_url"):
        outbound = item["affiliate_url"]
    elif str(mon.get("mode")) == "search":
        outbound = "https://www.google.com/search?q=" + _url_quote(item["name"] + " official")
    else:
        outbound = item.get("affiliate_url", "")

    page_template = env.get_template("page.md.j2")
    content = page_template.render(
        title=title, description=description, slug=final_slug,
        date=utils.iso_now(), lang=lang, hugo_lang=locale["hugo_lang"],
        keywords=page_keywords, schema_type=schema_type, faqs=chosen_faqs,
        layout=layout, image=image_src, alt=alt_text,
        related=related, outbound=outbound,
        source_name=item.get("source_id", ""), region=region,
        word_count=verdict["words"], similarity=verdict["similarity"],
        group=kwmod.classify(main_kw), body=body,
    )
    path.write_text(content, encoding="utf-8")
    link_pool.add(title, final_slug)
    LOG.info("[%s] 生成 %s（%d 词，组件=%s，布局=%s，Schema=%s）",
             lang, path.name, verdict["words"], "+".join(order), layout, schema_type)

    return {"title": title, "url": "/%s/%s/%s/" % (lang, section, final_slug),
            "slug": final_slug, "description": description, "group": kwmod.classify(main_kw),
            "keyword": main_kw, "words": verdict["words"], "path": str(path)}


def postprocess(text: str, lang: str, repl: List[Tuple[re.Pattern, str]]) -> str:
    """全局正则后置处理：地区词汇 / 货币 / 支付渠道本地化 + 违规极限词替换。"""
    for pattern, replacement in repl:
        text = pattern.sub(replacement, text)
    return compliance.sanitize(text, lang)


# ---------------------------------------------------------------- 批量入口
GENERATED_STATE = "generated_pages"


def _load_generated() -> Dict[str, str]:
    """已生成页面登记表（"lang|item_id" -> 日期），防止二次重跑产生重复页面。"""
    data: Dict[str, str] = utils.load_state(GENERATED_STATE, {})
    return {k: v for k, v in data.items() if utils.days_between(utils.today_str(), v) <= 120}


def run(day: Optional[str] = None) -> Dict[str, int]:
    """读取当日译文 → 生成多语言页面 → 生成聚合页。返回各语种产出数。"""
    config.ensure_dirs()
    env = jinja_env()
    day = day or utils.today_str()
    section = config.topic_section()
    max_pages = int(config.get("generate.max_pages_per_run", 40))
    generated = _load_generated()
    stats: Dict[str, int] = {}

    for lang in config.locale_codes():
        src = config.TRANSLATED_DIR / ("items_%s_%s.json" % (lang, day))
        payload = utils.read_json(src, {})
        items = payload.get("items", [])
        if not items:
            LOG.warning("[%s] 无译文数据（%s），跳过", lang, src.name)
            stats[lang] = 0
            continue

        pools = kwmod.load_keywords(lang)
        snippets = load_snippets(lang)
        faqs = load_faqs(lang)
        repl = load_replacements(lang)
        link_pool = seo.LinkPool(lang, section)
        if not snippets or not faqs:
            LOG.error("[%s] 短句库/FAQ 库为空，无法生成（巡检脚本会自动补兜底模板）", lang)
            stats[lang] = 0
            continue

        produced: List[Dict[str, Any]] = []
        skipped = 0
        for item in items:
            if len(produced) >= max_pages:
                break
            key = "%s|%s" % (lang, item["id"])
            if key in generated:
                skipped += 1
                continue
            page = build_page(item, lang, env, pools, snippets, faqs, repl, link_pool)
            if page:
                produced.append(page)
                generated[key] = utils.today_str()

        rng = utils.seeded_random("hub|%s|%s" % (lang, day))
        seo.build_hub_pages(lang, section, produced, env, rng)
        stats[lang] = len(produced)
        LOG.info("[%s] 本轮共产出 %d 个页面（跳过已生成 %d 条）", lang, len(produced), skipped)

    utils.save_state(GENERATED_STATE, generated)
    return stats


if __name__ == "__main__":
    run()
