# -*- coding: utf-8 -*-
"""通用工具层：日志、状态持久化、CSV 读写、清洗、随机、跨平台兜底。

本文件不涉及任何外部收费服务，也不包含任何模型调用。
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import logging
import os
import random
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from . import config

# ---------------------------------------------------------------- 日志
_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-12s | %(message)s"


def get_logger(name: str) -> logging.Logger:
    """统一日志器。Windows 控制台默认 GBK，这里强制 UTF-8 避免中文乱码崩溃。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(handler)
    try:  # Python 3.7+ ：把标准输出切成 UTF-8，兼容 Windows 终端
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    logger.propagate = False
    return logger


LOG = get_logger("utils")

# ---------------------------------------------------------------- 时间
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def today_str() -> str:
    return utc_now().strftime("%Y-%m-%d")


def iso_now() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def week_tag() -> str:
    """ISO 周标签，用于每周备份目录名，例如 2026-W33。"""
    iso = utc_now().isocalendar()
    return "%d-W%02d" % (iso[0], iso[1])


def days_between(day_a: str, day_b: str) -> int:
    """两个 YYYY-MM-DD 字符串之间的天数差（a - b）。解析失败返回 0。"""
    try:
        da = datetime.strptime(day_a, "%Y-%m-%d").date()
        db = datetime.strptime(day_b, "%Y-%m-%d").date()
        return (da - db).days
    except Exception:
        return 0


def shift_day(days: int) -> str:
    return (utc_now().date() + timedelta(days=days)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 随机 / 反爬
def rand_sleep(rng: Sequence[float], label: str = "") -> None:
    """区间随机休眠，用于降低请求频率、打散指纹。"""
    if not rng:
        return
    low, high = float(rng[0]), float(rng[-1])
    if high <= 0:
        return
    seconds = random.uniform(low, high)
    if label:
        LOG.info("随机休眠 %.2fs (%s)", seconds, label)
    time.sleep(seconds)


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]


def browser_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "*/*",
        "Cache-Control": "no-cache",
    }
    if extra:
        headers.update(extra)
    return headers


def daily_slot_hit(candidates: Sequence[int]) -> bool:
    """按“日期哈希”判定当前 UTC 小时是否为今日选定执行时段。

    效果：cron 每天触发多次，但只有一次真正跑完整流程，
    执行时间随日期在候选时段之间浮动（±2 小时），无需人工调 cron。
    FORCE_RUN=1 时无条件执行（手动 workflow_dispatch 调试用）。
    """
    if os.environ.get("FORCE_RUN") == "1":
        LOG.info("FORCE_RUN=1，跳过时段判定，立即执行")
        return True
    if not candidates:
        return True
    seed = int(hashlib.md5(today_str().encode("utf-8")).hexdigest(), 16)
    chosen = list(candidates)[seed % len(candidates)]
    now_hour = utc_now().hour
    LOG.info("今日选定执行时段 UTC %02d:00，当前 UTC %02d:00", chosen, now_hour)
    return now_hour == chosen


def retry_call(func: Callable[[], Any], times: int, backoff: Sequence[float],
               label: str = "") -> Any:
    """通用重试：失败按 backoff 递增等待，全部失败返回 None（不抛异常中断流水线）。"""
    for attempt in range(1, max(1, times) + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 —— 流水线必须不中断
            wait = backoff[min(attempt - 1, len(backoff) - 1)] if backoff else 3
            LOG.warning("[%s] 第 %d/%d 次失败: %s；%.1fs 后重试",
                        label, attempt, times, exc, wait)
            if attempt >= times:
                LOG.error("[%s] 重试耗尽，转入延迟队列", label)
                return None
            time.sleep(wait)
    return None


# ---------------------------------------------------------------- JSON 状态
def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default if default is not None else {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # 文件损坏不许拖垮流水线，直接兜底
        LOG.warning("读取 JSON 失败(%s): %s，使用默认值", path.name, exc)
        return default if default is not None else {}


def write_json(path: Path, payload: Any) -> None:
    """原子写：先写 .tmp 再替换，防止 Actions 中途被杀导致文件损坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(path))


def state_path(name: str) -> Path:
    return config.STATE_DIR / ("%s.json" % name)


def load_state(name: str, default: Any = None) -> Any:
    return read_json(state_path(name), default if default is not None else {})


def save_state(name: str, payload: Any) -> None:
    write_json(state_path(name), payload)


# ---------------------------------------------------------------- CSV
def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    """读取 CSV 为字典列表。文件缺失返回空列表（由巡检脚本兜底生成）。"""
    if not path.exists():
        LOG.warning("CSV 不存在: %s", path)
        return []
    rows: List[Dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append({(k or "").strip(): (v or "").strip() for k, v in row.items()})
    return rows


def write_csv_rows(path: Path, fieldnames: Sequence[str],
                   rows: Iterable[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    os.replace(str(tmp), str(path))


def append_csv_rows(path: Path, fieldnames: Sequence[str],
                    rows: Sequence[Dict[str, str]]) -> None:
    if not rows:
        return
    exists = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames))
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


# ---------------------------------------------------------------- 文本清洗
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[\s\u00a0\u3000]+")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_URL_RE = re.compile(r"https?://\S+")


def strip_html(text: str) -> str:
    """剔除 HTML 标签与实体，压缩空白。"""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _CTRL_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def clean_text(text: str, max_chars: int = 0, drop_urls: bool = True) -> str:
    """完整清洗：去标签 → 去乱码 → 去 URL → Unicode 规范化 → 截断。"""
    text = strip_html(text or "")
    if drop_urls:
        text = _URL_RE.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    # 丢弃替换字符与私有区乱码
    text = "".join(ch for ch in text if ch != "\ufffd" and not (0xE000 <= ord(ch) <= 0xF8FF))
    text = _WS_RE.sub(" ", text).strip(" -–—|·,;:")
    if max_chars and len(text) > max_chars:
        cut = text[:max_chars]
        # 优先在句末或空格处截断，避免把单词切断
        for sep in (". ", "! ", "? ", "; ", ", ", " "):
            idx = cut.rfind(sep)
            if idx > max_chars * 0.55:
                cut = cut[:idx]
                break
        text = cut.rstrip(" ,;:-") + "..."
    return text


def is_garbage(text: str, min_len: int = 20) -> bool:
    """垃圾文本判定：过短、几乎无字母、疑似纯符号/纯乱码。"""
    if not text or len(text) < min_len:
        return True
    letters = sum(1 for ch in text if ch.isalpha())
    if letters / max(1, len(text)) < 0.45:
        return True
    return False


def word_count(text: str) -> int:
    """英文/西语/印尼语均以空格分词，统计词数（Markdown 语法符号不计入）。"""
    plain = re.sub(r"[#*_>`\[\]\(\)\|\-]+", " ", text or "")
    return len([w for w in _WS_RE.split(plain) if w.strip()])


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 70) -> str:
    """生成 URL 友好 slug（不引入第三方依赖，跨平台一致）。"""
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = text.encode("ascii", "ignore").decode("ascii")
    text = _SLUG_RE.sub("-", text).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rsplit("-", 1)[0]
    return text or ("item-%s" % md5_short(str(random.random())))


def md5_short(text: str, size: int = 10) -> str:
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()[:size]


def yaml_escape(text: str) -> str:
    """Front matter 双引号字符串转义，避免 Hugo 解析失败。"""
    return (text or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def unique_keep_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        key = (item or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out


def title_case(text: str) -> str:
    small = {"a", "an", "the", "of", "for", "and", "or", "to", "in", "on", "vs", "de", "y", "para", "dan", "di"}
    words = (text or "").split()
    out = []
    for idx, word in enumerate(words):
        low = word.lower()
        out.append(word if word.isupper() else (low if idx and low in small else low.capitalize()))
    return " ".join(out)


def chunked(items: Sequence[Any], size: int) -> List[Sequence[Any]]:
    size = max(1, int(size))
    return [items[i:i + size] for i in range(0, len(items), size)]


def prune_old_files(directory: Path, keep_days: int, patterns: Sequence[str] = ("*",)) -> int:
    """按修改时间清理过期文件，压缩仓库体积、避免构建超时。"""
    if not directory.exists() or keep_days <= 0:
        return 0
    deadline = time.time() - keep_days * 86400
    removed = 0
    for pattern in patterns:
        for path in directory.glob(pattern):
            if path.is_file() and path.stat().st_mtime < deadline:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed


def seeded_random(seed_text: str) -> random.Random:
    """按素材键生成确定性随机器：同一素材重跑结构稳定，不同素材千页千面。"""
    return random.Random(int(hashlib.md5(seed_text.encode("utf-8")).hexdigest(), 16))
