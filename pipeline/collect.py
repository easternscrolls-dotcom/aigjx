# -*- coding: utf-8 -*-
"""模块1：RSS 素材定时采集。

内置自动化风险规避：
1. 多备用源 + 单源连续 2 天 404/空数据自动休眠并切换备用；
2. 分片抓取（--shard i/n）+ 源间随机休眠，配合调度层时段浮动防反爬；
3. 自动清洗脏数据：剔除 HTML 标签、乱码、超长文本，简介强制截断 200 字符；
4. 字段完整性校验：缺名称/简介/链接直接丢弃；
5. 当日已抓取素材 ID 缓存去重，减少无效请求；
6. 失败自动重试 3 次，仍失败的源写入延迟队列次日重试。

用法：
    python -m pipeline.collect                # 全量
    python -m pipeline.collect --shard 1/3    # 第 1 片（共 3 片）
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import feedparser
import requests

from . import config, utils

LOG = utils.get_logger("collect")

SOURCE_STATE = "source_health"   # data/state/source_health.json
SEEN_STATE = "seen_items"        # data/state/seen_items.json


# ---------------------------------------------------------------- 源健康管理
def _load_health() -> Dict[str, Any]:
    return utils.load_state(SOURCE_STATE, {})


def _source_available(health: Dict[str, Any], source_id: str) -> bool:
    """休眠中的源直接跳过；休眠到期自动复活。"""
    node = health.get(source_id) or {}
    sleep_until = node.get("sleep_until")
    if not sleep_until:
        return True
    if utils.days_between(utils.today_str(), sleep_until) >= 0:
        LOG.info("源 %s 休眠到期，自动复活", source_id)
        node["sleep_until"] = ""
        node["fail_days"] = 0
        health[source_id] = node
        return True
    LOG.info("源 %s 休眠中（至 %s），跳过", source_id, sleep_until)
    return False


def _mark_result(health: Dict[str, Any], source_id: str, ok: bool) -> None:
    """成功清零失败计数；失败按“天”累计，连续 N 天则自动休眠。"""
    node = health.setdefault(source_id, {"fail_days": 0, "last_fail_day": "", "sleep_until": ""})
    today = utils.today_str()
    if ok:
        node["fail_days"] = 0
        node["last_ok_day"] = today
        node["sleep_until"] = ""
        return
    if node.get("last_fail_day") != today:
        node["fail_days"] = int(node.get("fail_days", 0)) + 1
        node["last_fail_day"] = today
    threshold = int(config.get("collect.source_fail_days_to_sleep", 2))
    if node["fail_days"] >= threshold:
        days = int(config.get("collect.source_sleep_days", 7))
        node["sleep_until"] = utils.shift_day(days)
        LOG.warning("源 %s 连续 %d 天异常，自动休眠 %d 天并切换备用源",
                    source_id, node["fail_days"], days)


# ---------------------------------------------------------------- 抓取
def _rewrite_mirror(url: str, mirror: str) -> str:
    """RSSHub 主实例限流时自动改写为镜像域名（全部为免费公共实例）。"""
    for base in ("https://rsshub.app", "http://rsshub.app"):
        if url.startswith(base):
            return mirror.rstrip("/") + url[len(base):]
    return url


def _fetch_feed(url: str) -> List[Dict[str, Any]]:
    """单次抓取：requests 拉原始字节 → feedparser 解析。空结果视为失败。"""
    timeout = int(config.get("collect.request_timeout", 20))
    resp = requests.get(url, headers=utils.browser_headers(), timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError("HTTP %s" % resp.status_code)
    parsed = feedparser.parse(resp.content)
    entries = list(parsed.entries or [])
    if not entries:
        raise RuntimeError("空数据（0 条 entry）")
    return entries


def _fetch_with_fallback(source: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """带重试 + 镜像轮换的抓取。全部失败返回 None。"""
    times = int(config.get("collect.retry_times", 3))
    backoff = config.get("collect.retry_backoff", [3, 8, 20])
    mirrors = list(config.sources_config().get("rsshub_mirrors", []))
    random.shuffle(mirrors)
    candidates = [source["url"]] + [_rewrite_mirror(source["url"], m) for m in mirrors]
    # 去重并保持顺序
    seen: set = set()
    urls = [u for u in candidates if not (u in seen or seen.add(u))]

    for url in urls:
        result = utils.retry_call(lambda u=url: _fetch_feed(u), times, backoff,
                                  label="%s|%s" % (source["id"], url[:48]))
        if result:
            return result
    return None


# ---------------------------------------------------------------- 素材整理
def _entry_link(entry: Dict[str, Any]) -> str:
    link = entry.get("link") or ""
    if not link:
        for item in entry.get("links", []) or []:
            if item.get("href"):
                link = item["href"]
                break
    return link.strip()


def _entry_summary(entry: Dict[str, Any]) -> str:
    for key in ("summary", "description", "subtitle"):
        value = entry.get(key)
        if value:
            return str(value)
    content = entry.get("content") or []
    if content and isinstance(content, list):
        return str(content[0].get("value", ""))
    return ""


def _entry_image(entry: Dict[str, Any]) -> str:
    for key in ("media_thumbnail", "media_content"):
        media = entry.get(key) or []
        if media and isinstance(media, list) and media[0].get("url"):
            return media[0]["url"]
    for item in entry.get("links", []) or []:
        if str(item.get("type", "")).startswith("image/") and item.get("href"):
            return item["href"]
    return ""


def _normalize(entry: Dict[str, Any], source: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """清洗 + 字段完整性校验。任一必填字段缺失或为垃圾文本则丢弃。"""
    title_max = int(config.get("collect.title_max_chars", 90))
    summary_max = int(config.get("collect.summary_max_chars", 200))

    # 先剥离采集源原始元数据（Reddit/HN 机器字段），再做常规清洗与截断，
    # 这样翻译与 SEO 拼接拿到的 summary 不再夹带 "submitted by /u/..." 等噪声。
    name = utils.clean_text(utils.strip_source_cruft(entry.get("title", "")), title_max)
    summary = utils.clean_text(utils.strip_source_cruft(_entry_summary(entry)), summary_max)
    link = _entry_link(entry)

    if not name or not summary or not link:
        LOG.info("丢弃：字段缺失（name=%s summary=%s link=%s）",
                 bool(name), bool(summary), bool(link))
        return None
    if utils.is_garbage(name, 6) or utils.is_garbage(summary, 30):
        LOG.info("丢弃：疑似乱码/过短 -> %s", name[:40])
        return None
    if not link.startswith("http"):
        LOG.info("丢弃：非法链接 -> %s", link[:60])
        return None

    item_id = utils.md5_short(link or name, 16)
    return {
        "id": item_id,
        "source_id": source["id"],
        "topic": config.current_topic(),
        "name": name,
        "summary": summary,
        # 分销/推广链接：原始外链 + 可配置 UTM 后缀
        "affiliate_url": link + (config.get("site.utm_suffix", "") or ""),
        "origin_url": link,
        "image": _entry_image(entry),
        "published": str(entry.get("published", "") or entry.get("updated", "")),
        "collected_at": utils.iso_now(),
    }


# ---------------------------------------------------------------- 去重缓存
def _load_seen() -> Dict[str, str]:
    """已抓取 ID -> 首次抓取日期。过期条目自动清理，防止文件无限膨胀。"""
    seen: Dict[str, str] = utils.load_state(SEEN_STATE, {})
    keep = int(config.get("collect.seen_id_keep_days", 45))
    return {k: v for k, v in seen.items() if utils.days_between(utils.today_str(), v) <= keep}


# ---------------------------------------------------------------- 延迟队列
def queue_path(day: Optional[str] = None) -> Path:
    return config.QUEUE_DIR / ("retry_%s.json" % (day or utils.today_str()))


def push_retry_queue(kind: str, payload: Any) -> None:
    """写入次日延迟队列（采集失败源 / 翻译失效素材共用）。"""
    path = queue_path(utils.shift_day(1))
    queue = utils.read_json(path, {"items": []})
    queue.setdefault("items", []).append({"kind": kind, "payload": payload, "at": utils.iso_now()})
    utils.write_json(path, queue)


def pop_today_queue() -> List[Dict[str, Any]]:
    """取出当日待重试项并清空文件。"""
    path = queue_path()
    queue = utils.read_json(path, {"items": []})
    items = list(queue.get("items", []))
    if items:
        utils.write_json(path, {"items": []})
        LOG.info("载入延迟队列 %d 条待重试项", len(items))
    return items


# ---------------------------------------------------------------- 主流程
def _parse_shard(text: str) -> Tuple[int, int]:
    try:
        idx, total = text.split("/")
        idx_i, total_i = int(idx), int(total)
        if idx_i < 1 or total_i < 1 or idx_i > total_i:
            raise ValueError
        return idx_i, total_i
    except Exception:
        LOG.warning("shard 参数非法（应形如 1/3），按全量执行")
        return 1, 1


def collect(shard: str = "1/1") -> Dict[str, Any]:
    config.ensure_dirs()
    shard_idx, shard_total = _parse_shard(shard)

    health = _load_health()
    seen = _load_seen()
    sources = sorted(config.topic_config().get("sources", []),
                     key=lambda s: int(s.get("priority", 99)))

    # 分片：按索引取模，保证多个 Actions 并行 job 不重复抓同一源
    my_sources = [s for i, s in enumerate(sources) if i % shard_total == (shard_idx - 1)]
    LOG.info("题材=%s 分片=%d/%d 本片待抓源: %s",
             config.current_topic(), shard_idx, shard_total,
             ", ".join(s["id"] for s in my_sources) or "(空)")

    limit = int(config.get("collect.per_source_limit", 25))
    max_items = int(config.get("collect.max_items_per_day", 60))
    items: List[Dict[str, Any]] = []
    dropped = {"dup": 0, "invalid": 0}

    for source in my_sources:
        if len(items) >= max_items:
            LOG.info("已达每日素材上限 %d，停止抓取", max_items)
            break
        if not _source_available(health, source["id"]):
            continue

        entries = _fetch_with_fallback(source)
        if entries is None:
            _mark_result(health, source["id"], ok=False)
            push_retry_queue("source", {"id": source["id"], "url": source["url"]})
            utils.rand_sleep(config.get("collect.sleep_between_sources", [2, 6]), "源间")
            continue

        got = 0
        for entry in entries[:limit]:
            item = _normalize(entry, source)
            if item is None:
                dropped["invalid"] += 1
                continue
            if item["id"] in seen:
                dropped["dup"] += 1
                continue
            seen[item["id"]] = utils.today_str()
            items.append(item)
            got += 1
            if len(items) >= max_items:
                break

        _mark_result(health, source["id"], ok=True)
        LOG.info("源 %s 采集成功：入库 %d 条", source["id"], got)
        utils.rand_sleep(config.get("collect.sleep_between_sources", [2, 6]), "源间")

    utils.save_state(SOURCE_STATE, health)
    utils.save_state(SEEN_STATE, seen)

    out_path = config.RAW_DIR / ("items_%s_shard%d.json" % (utils.today_str(), shard_idx))
    utils.write_json(out_path, {"date": utils.today_str(), "shard": shard,
                                "topic": config.current_topic(), "items": items})
    LOG.info("采集完成：%d 条有效素材（重复丢弃 %d，脏数据丢弃 %d）-> %s",
             len(items), dropped["dup"], dropped["invalid"], out_path.name)
    return {"count": len(items), "path": str(out_path), "dropped": dropped}


def merge_shards(day: Optional[str] = None) -> List[Dict[str, Any]]:
    """汇总当日各分片结果，跨分片再做一次 ID 去重。"""
    day = day or utils.today_str()
    merged: Dict[str, Dict[str, Any]] = {}
    for path in sorted(config.RAW_DIR.glob("items_%s_shard*.json" % day)):
        payload = utils.read_json(path, {})
        for item in payload.get("items", []):
            merged[item["id"]] = item
    LOG.info("合并 %s 分片素材共 %d 条", day, len(merged))
    return list(merged.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="RSS 素材采集（模块1）")
    parser.add_argument("--shard", default="1/1", help="分片，形如 1/3")
    args = parser.parse_args()
    collect(args.shard)


if __name__ == "__main__":
    main()
