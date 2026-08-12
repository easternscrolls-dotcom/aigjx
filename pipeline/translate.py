# -*- coding: utf-8 -*-
"""模块2：免费多源翻译系统（全自动防限流封禁）。

三套 **无密钥免费** 翻译源轮询，代码中不存在任何付费密钥字段：
  1. google_free   —— translate.googleapis.com/translate_a/single（py-googletrans 同源端点）
                       若本机已安装 py-googletrans，优先走该库，失败自动降级到裸端点；
  2. edge_free     —— Edge 浏览器内置翻译：匿名 auth 接口换临时 token + 免费翻译端点；
  3. deeplx_public —— 社区 DeepLX 公共节点（settings.yaml 可随时增删，无需改代码）。

自动化风险规避：
  - 单接口报错/风控 → 该源自动冷却 30 分钟并切换下一个源；
  - 三源全部失效 → 素材写入延迟队列次日重试，绝不产出半成品页面；
  - 译文本地缓存（按 语种+源文 哈希），重复素材直接命中缓存；
  - 批量 50 条一组，组间随机休眠 2~5s，调用间再抖动，降低 IP 频率；
  - 分语种隔离：每个语种独立缓存文件与独立请求批次，杜绝语种错乱混排。

用法：
    python -m pipeline.translate            # 翻译当日采集结果
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from . import collect, config, utils

LOG = utils.get_logger("translate")

COOLDOWN_STATE = "translate_cooldown"

# 可选依赖：py-googletrans。缺失或版本冲突时自动降级，不影响流水线。
try:  # pragma: no cover
    from googletrans import Translator as _GoogletransTranslator  # type: ignore
    _HAS_GOOGLETRANS = True
except Exception:  # noqa: BLE001
    _GoogletransTranslator = None  # type: ignore
    _HAS_GOOGLETRANS = False


# ---------------------------------------------------------------- 冷却管理
def _load_cooldown() -> Dict[str, str]:
    return utils.load_state(COOLDOWN_STATE, {})


def _is_cooling(state: Dict[str, str], provider: str) -> bool:
    until = state.get(provider)
    if not until:
        return False
    return until > utils.iso_now()


def _set_cooldown(state: Dict[str, str], provider: str) -> None:
    minutes = int(config.get("translate.cooldown_minutes", 30))
    from datetime import timedelta
    until = (utils.utc_now() + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state[provider] = until
    utils.save_state(COOLDOWN_STATE, state)
    LOG.warning("翻译源 %s 触发风控/报错，自动冷却至 %s 并切换下一个源", provider, until)


# ---------------------------------------------------------------- 缓存
def _cache_file(lang: str) -> Path:
    return config.CACHE_DIR / ("tcache_%s.json" % lang)


class TranslationCache:
    """分语种隔离的本地译文缓存。进程内一次加载，结束统一落盘。"""

    def __init__(self, lang: str) -> None:
        self.lang = lang
        self.path = _cache_file(lang)
        self.data: Dict[str, str] = utils.read_json(self.path, {})
        self.dirty = False

    @staticmethod
    def key(text: str) -> str:
        return utils.md5_short(text, 24)

    def get(self, text: str) -> Optional[str]:
        return self.data.get(self.key(text))

    def put(self, text: str, translated: str) -> None:
        self.data[self.key(text)] = translated
        self.dirty = True

    def flush(self) -> None:
        if self.dirty:
            utils.write_json(self.path, self.data)
            LOG.info("[%s] 译文缓存已落盘，共 %d 条", self.lang, len(self.data))


# ---------------------------------------------------------------- 翻译源实现
def _post_json(url: str, payload: Any, headers: Dict[str, str]) -> Any:
    timeout = int(config.get("translate.request_timeout", 20))
    resp = requests.post(url, data=json.dumps(payload), headers=headers, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError("HTTP %s" % resp.status_code)
    return resp.json()


def _tr_google_free(text: str, locale: Dict[str, Any]) -> str:
    """免密钥 Google 端点。优先 py-googletrans，失败降级裸端点。"""
    target = locale["google"]
    if _HAS_GOOGLETRANS:
        try:  # pragma: no cover
            translator = _GoogletransTranslator()
            result = translator.translate(text, dest=target)
            if result and getattr(result, "text", ""):
                return str(result.text)
        except Exception as exc:  # noqa: BLE001
            LOG.info("py-googletrans 不可用（%s），降级到公共端点", exc)

    timeout = int(config.get("translate.request_timeout", 20))
    resp = requests.get(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": text},
        headers=utils.browser_headers(), timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError("HTTP %s" % resp.status_code)
    data = resp.json()
    if not data or not data[0]:
        raise RuntimeError("空响应")
    return "".join(seg[0] for seg in data[0] if seg and seg[0])


_EDGE_TOKEN: Dict[str, Any] = {"value": "", "at": 0.0}


def _edge_token() -> str:
    """Edge 翻译匿名 token，10 分钟内复用，减少 auth 请求。"""
    import time
    if _EDGE_TOKEN["value"] and (time.time() - float(_EDGE_TOKEN["at"])) < 600:
        return str(_EDGE_TOKEN["value"])
    timeout = int(config.get("translate.request_timeout", 20))
    resp = requests.get("https://edge.microsoft.com/translate/auth",
                        headers=utils.browser_headers(), timeout=timeout)
    if resp.status_code >= 400 or not resp.text.strip():
        raise RuntimeError("Edge auth 失败 HTTP %s" % resp.status_code)
    _EDGE_TOKEN["value"] = resp.text.strip()
    _EDGE_TOKEN["at"] = time.time()
    return str(_EDGE_TOKEN["value"])


def _tr_edge_free(text: str, locale: Dict[str, Any]) -> str:
    token = _edge_token()
    url = ("https://api-edge.cognitive.microsofttranslator.com/translate"
           "?api-version=3.0&to=%s" % locale["edge"])
    headers = utils.browser_headers({
        "Authorization": "Bearer %s" % token,
        "Content-Type": "application/json",
    })
    data = _post_json(url, [{"Text": text}], headers)
    try:
        return str(data[0]["translations"][0]["text"])
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Edge 响应结构异常: %s" % exc)


def _tr_deeplx_public(text: str, locale: Dict[str, Any]) -> str:
    endpoints = list(config.get("translate.deeplx_endpoints", []))
    random.shuffle(endpoints)
    last_error = "无可用节点"
    for url in endpoints:
        try:
            data = _post_json(url, {"text": text, "source_lang": "auto",
                                    "target_lang": locale["deeplx"]},
                              utils.browser_headers({"Content-Type": "application/json"}))
            out = data.get("data") or ""
            if out:
                return str(out)
            last_error = "空 data 字段"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            continue
    raise RuntimeError("DeepLX 全部节点失败: %s" % last_error)


PROVIDERS: Dict[str, Callable[[str, Dict[str, Any]], str]] = {
    "google_free": _tr_google_free,
    "edge_free": _tr_edge_free,
    "deeplx_public": _tr_deeplx_public,
}


# ---------------------------------------------------------------- 调度核心
def translate_text(text: str, locale: Dict[str, Any], cache: TranslationCache,
                   cooldown: Dict[str, str]) -> Optional[str]:
    """单条翻译：命中缓存直接返回；否则按顺序轮询未冷却的源。

    返回 None 表示三源全部失效（调用方应把素材转入延迟队列）。
    """
    text = (text or "").strip()
    if not text:
        return ""
    max_chars = int(config.get("translate.max_chars_per_call", 1800))
    if len(text) > max_chars:
        text = text[:max_chars]

    cached = cache.get(text)
    if cached is not None:
        return cached

    for provider in config.get("translate.providers", list(PROVIDERS)):
        func = PROVIDERS.get(provider)
        if func is None:
            continue
        if _is_cooling(cooldown, provider):
            LOG.info("跳过冷却中的翻译源 %s", provider)
            continue
        try:
            utils.rand_sleep(config.get("translate.sleep_between_calls", [0.4, 1.2]))
            out = func(text, locale)
            out = utils.clean_text(out, 0, drop_urls=False)
            if not out:
                raise RuntimeError("译文为空")
            cache.put(text, out)
            return out
        except Exception as exc:  # noqa: BLE001
            LOG.warning("翻译源 %s 失败: %s", provider, exc)
            _set_cooldown(cooldown, provider)
            continue

    LOG.error("三套免费翻译源全部不可用，素材转入延迟队列")
    return None


def translate_items(items: List[Dict[str, Any]], lang: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把素材批量翻译成指定语种。返回 (成功列表, 失败列表)。

    分语种隔离：每个语种独立 cache 与独立批次，绝不混用。
    """
    locale = config.locale_by_code(lang)
    cache = TranslationCache(lang)
    cooldown = _load_cooldown()
    batch_size = int(config.get("translate.batch_size", 50))
    ok_items: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    batches = utils.chunked(items, batch_size)
    LOG.info("[%s] 待翻译 %d 条，切分为 %d 批（每批 %d 条）",
             lang, len(items), len(batches), batch_size)

    for bi, batch in enumerate(batches, start=1):
        for item in batch:
            name_tr = translate_text(item["name"], locale, cache, cooldown)
            summary_tr = translate_text(item["summary"], locale, cache, cooldown)
            if name_tr is None or summary_tr is None:
                failed.append(item)
                continue
            record = dict(item)
            record.update({
                "lang": lang,
                "hugo_lang": locale["hugo_lang"],
                "name_src": item["name"],
                "summary_src": item["summary"],
                "name": name_tr or item["name"],
                "summary": summary_tr or item["summary"],
                "translated_at": utils.iso_now(),
            })
            ok_items.append(record)
        LOG.info("[%s] 第 %d/%d 批完成（成功 %d / 失败 %d）",
                 lang, bi, len(batches), len(ok_items), len(failed))
        if bi < len(batches):
            utils.rand_sleep(config.get("translate.sleep_between_batches", [2, 5]), "批间")

    cache.flush()
    utils.save_state(COOLDOWN_STATE, cooldown)
    return ok_items, failed


def run(items: Optional[List[Dict[str, Any]]] = None, merge: bool = True) -> Dict[str, int]:
    """对全部目标语种执行翻译并落盘 data/translated/items_<lang>_<date>.json。

    merge=True 时与当日已有译文按 id 合并（供“产出不足触发二次重跑”场景使用，
    避免第二轮覆盖第一轮成果）。
    """
    config.ensure_dirs()
    if items is None:
        items = collect.merge_shards()
    if not items:
        LOG.warning("无待翻译素材，跳过")
        return {}

    stats: Dict[str, int] = {}
    for lang in config.locale_codes():
        ok_items, failed = translate_items(items, lang)
        out = config.TRANSLATED_DIR / ("items_%s_%s.json" % (lang, utils.today_str()))
        if merge:
            old = utils.read_json(out, {}).get("items", [])
            bucket: Dict[str, Dict[str, Any]] = {i["id"]: i for i in old}
            bucket.update({i["id"]: i for i in ok_items})
            ok_items = list(bucket.values())
        utils.write_json(out, {"lang": lang, "date": utils.today_str(), "items": ok_items})
        stats[lang] = len(ok_items)
        for item in failed:
            collect.push_retry_queue("translate", {"lang": lang, "item": item})
        LOG.info("[%s] 翻译完成 %d 条，失败 %d 条已入延迟队列", lang, len(ok_items), len(failed))

    # 清理过期缓存文件，防止仓库体积膨胀
    utils.prune_old_files(config.CACHE_DIR, int(config.get("translate.cache_keep_days", 120)),
                          ["tcache_*.json"])
    return stats


def check_providers() -> Dict[str, bool]:
    """巡检用：逐个探活翻译源（周巡检脚本调用）。"""
    probe = "Hello world, this is a connectivity probe."
    locale = config.locale_by_code(config.locale_codes()[0])
    result: Dict[str, bool] = {}
    for provider, func in PROVIDERS.items():
        try:
            out = func(probe, locale)
            result[provider] = bool(out)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("巡检：翻译源 %s 不可用 -> %s", provider, exc)
            result[provider] = False
        utils.rand_sleep([1, 2])
    return result


if __name__ == "__main__":
    run()
