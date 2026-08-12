# -*- coding: utf-8 -*-
"""配置加载层。

所有脚本统一从这里取路径与参数，避免各处硬编码。
跨平台：全部使用 pathlib，Windows / macOS / Linux 通用。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import yaml

# ---------------------------------------------------------------- 路径常量
# 本文件位于 <ROOT>/pipeline/config.py，因此上溯两级即项目根目录
ROOT = Path(__file__).resolve().parent.parent

CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
TEMPLATE_DIR = ROOT / "templates"
SITE_DIR = ROOT / "site"
CONTENT_DIR = SITE_DIR / "content"
DOCS_DIR = ROOT / "docs"
BACKUP_DIR = ROOT / "backup"

RAW_DIR = DATA_DIR / "raw"
TRANSLATED_DIR = DATA_DIR / "translated"
QUEUE_DIR = DATA_DIR / "queue"
CACHE_DIR = DATA_DIR / "cache"
STATE_DIR = DATA_DIR / "state"
KEYWORDS_DIR = DATA_DIR / "keywords"
FAQ_DIR = DATA_DIR / "faq"
SNIPPET_DIR = DATA_DIR / "snippets"
LOCALIZATION_DIR = DATA_DIR / "localization"
BLACKLIST_DIR = DATA_DIR / "blacklist"

# 词库 / 短句库 / 黑名单 CSV 的固定文件名（巡检脚本靠它做兜底生成）
KEYWORDS_CSV = KEYWORDS_DIR / "longtail.csv"
FAQ_CSV = FAQ_DIR / "faq_bank.csv"
SNIPPETS_CSV = SNIPPET_DIR / "snippets.csv"
LOCALIZATION_CSV = LOCALIZATION_DIR / "replacements.csv"
BLACKLIST_CSV = BLACKLIST_DIR / "banned_terms.csv"

ALL_DIRS = [
    RAW_DIR, TRANSLATED_DIR, QUEUE_DIR, CACHE_DIR, STATE_DIR,
    KEYWORDS_DIR, FAQ_DIR, SNIPPET_DIR, LOCALIZATION_DIR, BLACKLIST_DIR,
    CONTENT_DIR, BACKUP_DIR,
]


def ensure_dirs() -> None:
    """确保全部工作目录存在（首次运行 / 巡检兜底都会调用）。"""
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- YAML 加载
def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError("缺少配置文件: %s" % path)
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


_SETTINGS_CACHE: Dict[str, Any] = {}
_LOCALES_CACHE: Dict[str, Any] = {}
_SOURCES_CACHE: Dict[str, Any] = {}


def settings() -> Dict[str, Any]:
    """全局参数（config/settings.yaml），带进程内缓存。"""
    global _SETTINGS_CACHE
    if not _SETTINGS_CACHE:
        _SETTINGS_CACHE = _load_yaml(CONFIG_DIR / "settings.yaml")
    return _SETTINGS_CACHE


def locales() -> List[Dict[str, Any]]:
    """目标语种列表（config/locales.yaml）。"""
    global _LOCALES_CACHE
    if not _LOCALES_CACHE:
        _LOCALES_CACHE = _load_yaml(CONFIG_DIR / "locales.yaml")
    return list(_LOCALES_CACHE.get("locales", []))


def locale_codes() -> List[str]:
    return [item["code"] for item in locales()]


def locale_by_code(code: str) -> Dict[str, Any]:
    for item in locales():
        if item["code"] == code:
            return item
    raise KeyError("未配置的语种: %s" % code)


def sources_config() -> Dict[str, Any]:
    global _SOURCES_CACHE
    if not _SOURCES_CACHE:
        _SOURCES_CACHE = _load_yaml(CONFIG_DIR / "sources.yaml")
    return _SOURCES_CACHE


def current_topic() -> str:
    """站点题材。允许用环境变量 TOPIC 临时覆盖，便于一套代码跑多站。"""
    return os.environ.get("TOPIC") or settings()["site"]["topic"]


def topic_config() -> Dict[str, Any]:
    topic = current_topic()
    topics = sources_config().get("topics", {})
    if topic not in topics:
        raise KeyError("config/sources.yaml 中不存在题材: %s" % topic)
    return topics[topic]


def topic_section() -> str:
    """页面所在 section，保证 URL 结构为 /<lang>/<section>/<slug>/ 即 2 层。"""
    return topic_config().get("section", "guides")


def get(path: str, default: Any = None) -> Any:
    """按点号路径读取 settings，例如 get("collect.retry_times", 3)。"""
    node: Any = settings()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node
