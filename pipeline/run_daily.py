# -*- coding: utf-8 -*-
"""每日全流程编排入口（采集 → 翻译 → 生成 → 校验 → 发布）。

内置自动化风险规避：
1. 执行时段按日期哈希在候选时段间浮动 ±2 小时，再叠加随机静默，防平台反爬；
2. 自动消费延迟队列（上次失败的源与素材）；
3. 当日产出低于阈值（默认 3 篇）自动触发二次全流程重跑（换源 + 重试队列）；
4. 推送前构建预检，失败自动回滚，线上永不空白；
5. 每轮结束自动清理临时缓存，压缩打包体积，避免 Actions 超时。

用法：
    python -m pipeline.run_daily              # 遵守时段判定
    FORCE_RUN=1 python -m pipeline.run_daily  # 强制立即执行（调试）
    python -m pipeline.run_daily --skip-collect   # 仅跑翻译与生成（Actions 汇总 job 用）
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from typing import Any, Dict, List

from . import collect, config, generate, publish, selfheal, translate, utils

LOG = utils.get_logger("run_daily")


def consume_queue() -> List[Dict[str, Any]]:
    """消费延迟队列：取回上次翻译失败的素材，重新进入本轮流水线。"""
    items: List[Dict[str, Any]] = []
    for entry in collect.pop_today_queue():
        payload = entry.get("payload") or {}
        if entry.get("kind") == "translate" and payload.get("item"):
            items.append(payload["item"])
    if items:
        LOG.info("延迟队列回收素材 %d 条", len(items))
    return items


def total_pages(stats: Dict[str, int]) -> int:
    return sum(int(v) for v in stats.values())


def one_pass(skip_collect: bool, shard: str) -> Dict[str, int]:
    """单轮流程：采集（可跳过）→ 翻译 → 生成。返回各语种页面产出数。"""
    items: List[Dict[str, Any]] = []
    if not skip_collect:
        collect.collect(shard)
    items.extend(collect.merge_shards())
    items.extend(consume_queue())

    # 跨来源去重（合并分片与队列后可能重复）
    bucket: Dict[str, Dict[str, Any]] = {}
    for item in items:
        bucket[item["id"]] = item
    items = list(bucket.values())
    if not items:
        LOG.warning("本轮无素材可处理")
        return {}

    random.shuffle(items)     # 打散顺序，避免同源内容连续成篇
    translate.run(items)
    return generate.run()


def main() -> int:
    parser = argparse.ArgumentParser(description="每日全流程编排")
    parser.add_argument("--skip-collect", action="store_true", help="跳过采集（Actions 汇总阶段用）")
    parser.add_argument("--shard", default="1/1", help="采集分片，形如 1/3")
    parser.add_argument("--no-publish", action="store_true", help="只跑生成不推送（本地调试）")
    args = parser.parse_args()

    config.ensure_dirs()

    # ---- 1. 时段浮动判定（防反爬 + 节省 Actions 额度）----
    if not utils.daily_slot_hit(config.get("schedule.candidate_slots_utc", [1, 3, 5])):
        LOG.info("非今日选定执行时段，正常退出（这是预期行为，不是故障）")
        return 0
    jitter = int(config.get("schedule.jitter_seconds", 900))
    if jitter > 0 and os.environ.get("FORCE_RUN") != "1":
        wait = random.randint(0, jitter)
        LOG.info("随机静默 %ds 后开始，进一步打散请求特征", wait)
        time.sleep(wait)

    # ---- 2. 轻量兜底巡检（确保 CSV / 站点模板齐备）----
    selfheal.run(full=False)

    # ---- 3. 第一轮 ----
    stats = one_pass(args.skip_collect, args.shard)
    LOG.info("第一轮产出：%s（合计 %d）", stats, total_pages(stats))

    # ---- 4. 产出不足自动二次重跑（自动换备用源）----
    threshold = int(config.get("generate.min_pages_per_day", 3))
    if total_pages(stats) < threshold:
        LOG.warning("当日产出 %d < %d，自动触发二次全流程重跑", total_pages(stats), threshold)
        utils.rand_sleep([20, 60], "二次重跑前冷却")
        stats2 = one_pass(skip_collect=False, shard="1/1")
        LOG.info("第二轮产出：%s（合计 %d）", stats2, total_pages(stats2))
        for key, value in stats2.items():
            stats[key] = stats.get(key, 0) + value

    # ---- 5. 清理 + 发布（含构建预检与自动回滚）----
    publish.cleanup_workspace()
    if args.no_publish:
        LOG.info("--no-publish 已启用，跳过 Git 推送")
        return 0
    result = publish.publish(message_extra="pages=%d" % total_pages(stats))
    LOG.info("发布结果：%s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
