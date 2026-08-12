# -*- coding: utf-8 -*-
"""模块3：全自动长尾词挖掘（每月自动运行一次）。

数据来源全部为免费无密钥公共接口：
  1. Google Suggest 下拉词：suggestqueries.google.com/complete/search（client=firefox）
     —— 按 a~z、疑问前缀、对比句式、人群细分、本地支付词做批量扩展；
  2. DuckDuckGo HTML 版相关搜索：html.duckduckgo.com/html/ 的 related-searches 区块
     —— 作为“底部相关搜索”的免费替代（直接抓 Google 结果页会被反爬封 IP）；
  3. 问答型长尾（AnswerThePublic 思路）：ATP 无免费开放 API，此处用
     “疑问前缀 × 种子词”驱动 Suggest 接口，产出等价的问答长尾词，零成本且不封号。

自动化能力：
  - 结果自动分类：question / comparison / audience / payment / general；
  - 与既有 CSV 自动去重合并，永久扩充词库（只增不减，避免长尾枯竭）；
  - 分语种挖掘（hl/gl 参数按 locales.yaml 自动切换），保证本地化长尾。

用法：
    python -m pipeline.keywords            # 全语种挖掘并合并入库
"""

from __future__ import annotations

import string
from typing import Any, Dict, List, Sequence

import requests
from bs4 import BeautifulSoup

from . import config, utils

LOG = utils.get_logger("keywords")

CSV_FIELDS = ["lang", "keyword", "category", "source", "added_at"]

# 分类判定关键词（多语种通用，覆盖英/西/印尼常见疑问与对比词）
_QUESTION_WORDS = ("how", "what", "why", "is ", "can ", "does ", "should",
                   "como", "qué", "que ", "por qué", "cuál", "es seguro",
                   "bagaimana", "apa ", "apakah", "kenapa", "mengapa")
_COMPARISON_WORDS = (" vs", "versus", "alternative", "alternativa", "alternatif",
                     " or ", " o ", " atau ", "better than", "mejor que", "lebih baik")
_AUDIENCE_WORDS = ("for beginners", "for students", "for small business", "for kids",
                   "para principiantes", "para estudiantes", "para negocios",
                   "untuk pemula", "untuk pelajar", "untuk bisnis")
_PAYMENT_WORDS = ("free trial", "no credit card", "gratis", "sin tarjeta", "oxxo", "spei",
                  "dana", "gopay", "ovo", "transfer bank", "tanpa kartu", "murah", "cheap")


def classify(keyword: str) -> str:
    """自动分类：问答词 / 对比词 / 人群细分词 / 本地支付专属词 / 通用词。"""
    low = " " + keyword.lower() + " "
    if any(w in low for w in _PAYMENT_WORDS):
        return "payment"
    if any(low.startswith(" " + w) or (" " + w) in low for w in _QUESTION_WORDS):
        return "question"
    if any(w in low for w in _COMPARISON_WORDS):
        return "comparison"
    if any(w in low for w in _AUDIENCE_WORDS):
        return "audience"
    return "general"


# ---------------------------------------------------------------- 免费数据源
def fetch_suggest(query: str, locale: Dict[str, Any]) -> List[str]:
    """Google Suggest 免密钥端点。失败静默返回空列表，不中断挖词。"""
    timeout = int(config.get("keywords.request_timeout", 15))
    params = {
        "client": "firefox",
        "q": query,
        "hl": locale["google"],
        "gl": (locale.get("region") or "us")[:2].lower(),
    }
    try:
        resp = requests.get("https://suggestqueries.google.com/complete/search",
                            params=params, headers=utils.browser_headers(), timeout=timeout)
        if resp.status_code >= 400:
            raise RuntimeError("HTTP %s" % resp.status_code)
        data = resp.json()
        return [str(x) for x in (data[1] if len(data) > 1 else []) if x]
    except Exception as exc:  # noqa: BLE001
        LOG.info("Suggest 失败(%s): %s", query[:40], exc)
        return []


def fetch_related_ddg(query: str) -> List[str]:
    """DuckDuckGo HTML 版相关搜索（免费、无密钥、反爬宽松）。"""
    timeout = int(config.get("keywords.request_timeout", 15))
    try:
        resp = requests.post("https://html.duckduckgo.com/html/", data={"q": query},
                             headers=utils.browser_headers(), timeout=timeout)
        if resp.status_code >= 400:
            raise RuntimeError("HTTP %s" % resp.status_code)
        soup = BeautifulSoup(resp.text, "html.parser")
        out: List[str] = []
        for node in soup.select(".related-searches__item, .js-related-search-item, a.related-searches__link"):
            text = utils.clean_text(node.get_text(" "), 80)
            if text and 3 <= len(text) <= 80:
                out.append(text.lower())
        return out
    except Exception as exc:  # noqa: BLE001
        LOG.info("DDG 相关搜索失败(%s): %s", query[:40], exc)
        return []


# ---------------------------------------------------------------- 扩展策略
def build_queries(seeds: Sequence[str]) -> List[str]:
    """由种子词生成扩展查询：字母扩展 + 疑问 + 对比 + 人群 + 支付。"""
    cfg = config.settings().get("keywords", {})
    queries: List[str] = []
    for seed in seeds:
        queries.append(seed)
        # a~z 字母扩展：Suggest 的经典长尾拓词法
        queries.extend("%s %s" % (seed, ch) for ch in string.ascii_lowercase)
        # 问答长尾（等价 AnswerThePublic 的疑问维度）
        queries.extend("%s %s" % (p, seed) for p in cfg.get("question_prefixes", []))
        # 对比 / 人群 / 支付
        for group in ("comparison_patterns", "audience_patterns", "payment_patterns"):
            queries.extend(str(p).replace("{s}", seed) for p in cfg.get(group, []))
    return utils.unique_keep_order(queries)


def mine_lang(lang: str) -> List[Dict[str, str]]:
    """单语种挖词。返回待写入 CSV 的行。"""
    locale = config.locale_by_code(lang)
    seeds = list(config.get("keywords.seeds", []))
    max_new = int(config.get("keywords.max_new_per_run", 400))
    sleep_rng = config.get("keywords.sleep_between_calls", [1.5, 4.0])

    found: List[Dict[str, str]] = []
    seen: set = set()
    queries = build_queries(seeds)
    LOG.info("[%s] 扩展查询 %d 条，开始挖词（上限 %d 个新词）", lang, len(queries), max_new)

    for idx, query in enumerate(queries, start=1):
        if len(found) >= max_new:
            break
        words = fetch_suggest(query, locale)
        # 每 12 个查询补一次 DDG 相关搜索，兼顾覆盖面与请求量
        if idx % 12 == 0:
            words += fetch_related_ddg(query)
        for word in words:
            key = word.strip().lower()
            if not key or key in seen or len(key) < 8 or len(key) > 90:
                continue
            seen.add(key)
            found.append({
                "lang": lang,
                "keyword": key,
                "category": classify(key),
                "source": "suggest+ddg",
                "added_at": utils.today_str(),
            })
            if len(found) >= max_new:
                break
        utils.rand_sleep(sleep_rng)

    LOG.info("[%s] 本轮挖到 %d 个候选词", lang, len(found))
    return found


# ---------------------------------------------------------------- 入库
def merge_into_csv(rows: List[Dict[str, str]]) -> int:
    """与既有词库去重合并（只增不删），返回新增数量。"""
    path = config.KEYWORDS_CSV
    existing = utils.read_csv_rows(path)
    have = {(r.get("lang", ""), r.get("keyword", "").lower()) for r in existing}
    fresh = [r for r in rows if (r["lang"], r["keyword"].lower()) not in have]
    # 本批次内部再去重
    dedup: List[Dict[str, str]] = []
    batch_seen: set = set()
    for row in fresh:
        key = (row["lang"], row["keyword"].lower())
        if key in batch_seen:
            continue
        batch_seen.add(key)
        dedup.append(row)
    utils.append_csv_rows(path, CSV_FIELDS, dedup)
    LOG.info("词库合并：新增 %d 条，总量 %d 条", len(dedup), len(existing) + len(dedup))
    return len(dedup)


def load_keywords(lang: str) -> Dict[str, List[str]]:
    """供内容生成模块使用：按分类返回该语种的长尾词。"""
    grouped: Dict[str, List[str]] = {}
    for row in utils.read_csv_rows(config.KEYWORDS_CSV):
        if row.get("lang") != lang:
            continue
        grouped.setdefault(row.get("category", "general"), []).append(row.get("keyword", ""))
    for key in grouped:
        grouped[key] = utils.unique_keep_order(grouped[key])
    return grouped


def run() -> Dict[str, int]:
    config.ensure_dirs()
    stats: Dict[str, int] = {}
    all_rows: List[Dict[str, str]] = []
    for lang in config.locale_codes():
        rows = mine_lang(lang)
        all_rows.extend(rows)
        stats[lang] = len(rows)
    added = merge_into_csv(all_rows)
    stats["_added"] = added
    return stats


if __name__ == "__main__":
    run()
