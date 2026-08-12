# -*- coding: utf-8 -*-
"""模块5：全自动版权 & 合规过滤（上线前拦截风险页面）。

拦截逻辑（全部自动，无需人工审核）：
1. difflib 字符串查重：译文/正文与原始素材相似度 > 10% → 直接丢弃不生成页面；
2. 相似度处于软阈值（8%~10%）→ 自动截取片段（缩短引用），降低重合度后复检；
3. 多语种违规词黑名单正则替换（data/blacklist/banned_terms.csv）；
   欧盟语种（settings.compliance.eu_locales）自动删减现金收益类描述；
4. 自动过滤 download / full story / resource 等盗版资源敏感文案；
5. 低于 300 词的低质页面禁止生成独立 md 文件。
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Optional, Tuple

from . import config, utils

LOG = utils.get_logger("compliance")

# 现金收益类表述（欧盟等严格地区自动删减）
_CASH_PATTERNS = [
    r"\b(earn|make)\s+(up\s+to\s+)?\$?\d+[\w\s]*\b",
    r"\b(guaranteed|instant)\s+(income|profit|payout|earnings)\b",
    r"\bgana\s+dinero\b", r"\bganancias\s+garantizadas\b",
    r"\bhasilkan\s+uang\b", r"\bpenghasilan\s+terjamin\b",
]

_CASH_REPLACEMENT = {
    "en": "potential benefits",
    "es": "beneficios potenciales",
    "id": "manfaat potensial",
}


class BlacklistRules:
    """违规极限词 + 盗版敏感词规则集（CSV 驱动，改词库不改代码）。"""

    def __init__(self) -> None:
        self.rules: Dict[str, List[Tuple[re.Pattern, str]]] = {}
        for row in utils.read_csv_rows(config.BLACKLIST_CSV):
            lang = row.get("lang", "all") or "all"
            pattern = row.get("pattern", "")
            if not pattern:
                continue
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                LOG.warning("黑名单正则非法已跳过 [%s]: %s", pattern, exc)
                continue
            self.rules.setdefault(lang, []).append((compiled, row.get("replacement", "")))
        # 盗版资源敏感词：命中即替换为中性表述，避免侵权与被判资源站
        for term in config.get("compliance.piracy_terms", []):
            try:
                compiled = re.compile(r"\b%s\b" % re.escape(str(term)), re.IGNORECASE)
            except re.error:
                continue
            self.rules.setdefault("all", []).append((compiled, "official page"))
        LOG.info("黑名单规则载入：%s",
                 ", ".join("%s=%d" % (k, len(v)) for k, v in self.rules.items()) or "(空)")

    def apply(self, text: str, lang: str) -> str:
        for scope in ("all", lang):
            for compiled, replacement in self.rules.get(scope, []):
                text = compiled.sub(replacement, text)
        return text


_BLACKLIST: Optional[BlacklistRules] = None


def blacklist() -> BlacklistRules:
    global _BLACKLIST
    if _BLACKLIST is None:
        _BLACKLIST = BlacklistRules()
    return _BLACKLIST


# ---------------------------------------------------------------- 查重
def similarity(text_a: str, text_b: str) -> float:
    """difflib 快速相似度（0~1）。做小写与空白归一化，结果更稳定。"""
    norm = lambda s: " ".join((s or "").lower().split())
    a, b = norm(text_a), norm(text_b)
    if not a or not b:
        return 0.0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return round(matcher.quick_ratio(), 4)


def check_similarity(body: str, source_text: str) -> Tuple[bool, float, str]:
    """返回 (是否通过, 相似度, 动作说明)。

    - > similarity_max（10%）：不通过，丢弃；
    - 在 [similarity_soft, similarity_max]：通过但标记 "soft"，调用方需截取片段重组。
    """
    hard = float(config.get("compliance.similarity_max", 0.10))
    soft = float(config.get("compliance.similarity_soft", 0.08))
    score = similarity(body, source_text)
    if score > hard:
        return False, score, "reject"
    if score >= soft:
        return True, score, "soft"
    return True, score, "pass"


def trim_snippet(text: str) -> str:
    """软阈值触发时自动截取片段，降低与原素材的重合度。"""
    limit = int(config.get("compliance.soft_trim_chars", 90))
    return utils.clean_text(text, limit)


# ---------------------------------------------------------------- 文本合规
def sanitize(text: str, lang: str) -> str:
    """违规词替换 + 地区法规删减。返回处理后的安全文本。"""
    text = blacklist().apply(text, lang)
    if lang in (config.get("compliance.eu_locales", []) or []):
        replacement = _CASH_REPLACEMENT.get(lang, "potential benefits")
        for pattern in _CASH_PATTERNS:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    # 收敛多余空白与重复标点
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def has_piracy_signal(text: str) -> bool:
    """强盗版信号检测：命中高危词组合则整页丢弃（比替换更保险）。"""
    low = (text or "").lower()
    hard_terms = ("mod apk", "crack", "torrent", "keygen", "nulled")
    return any(term in low for term in hard_terms)


def validate_page(body: str, source_text: str, lang: str) -> Dict[str, Any]:
    """页面级总校验。返回 {"ok": bool, "reason": str, "similarity": float, "action": str}。"""
    min_words = int(config.get("compliance.min_words", 300))
    words = utils.word_count(body)
    if words < min_words:
        return {"ok": False, "reason": "低质页面（%d 词 < %d）" % (words, min_words),
                "similarity": 0.0, "action": "reject", "words": words}
    if has_piracy_signal(body):
        return {"ok": False, "reason": "命中高危盗版词",
                "similarity": 0.0, "action": "reject", "words": words}
    passed, score, action = check_similarity(body, source_text)
    if not passed:
        return {"ok": False, "reason": "与原素材相似度 %.2f%% 超限" % (score * 100),
                "similarity": score, "action": action, "words": words}
    return {"ok": True, "reason": "", "similarity": score, "action": action, "words": words}
