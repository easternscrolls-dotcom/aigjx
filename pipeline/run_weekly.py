# -*- coding: utf-8 -*-
"""每周巡检 + 备份编排入口。

流程：完整巡检（翻译源连通性 / sitemap / 词库完整性 / 清理 / 备份）→ 提交推送。
巡检报告写入 data/state/health_report.json，对照 docs/RUNBOOK.md 可定位任何异常。
"""

from __future__ import annotations

import sys

from . import config, publish, selfheal, utils

LOG = utils.get_logger("run_weekly")


def main() -> int:
    config.ensure_dirs()
    report = selfheal.run(full=True)

    dead = [k for k, v in (report.get("translate_sources") or {}).items() if not v]
    if dead:
        LOG.warning("以下翻译源本周不可用（已自动冷却并轮询其余源）：%s", ", ".join(dead))

    result = publish.publish(message_extra="weekly-health")
    LOG.info("发布结果：%s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
