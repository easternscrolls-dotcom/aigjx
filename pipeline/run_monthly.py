# -*- coding: utf-8 -*-
"""每月长尾词挖掘编排入口。

流程：兜底巡检 → 全语种挖词 → 合并去重入库 → 备份 → 提交推送。
零成本：仅使用 Google Suggest / DuckDuckGo HTML 等免费无密钥接口。
"""

from __future__ import annotations

import sys

from . import config, keywords, publish, selfheal, utils

LOG = utils.get_logger("run_monthly")


def main() -> int:
    config.ensure_dirs()
    selfheal.ensure_csv_files()

    stats = keywords.run()
    LOG.info("挖词结果：%s", stats)

    # 挖词后立即备份词库，防止后续操作导致丢失
    selfheal.backup_data()

    result = publish.publish(message_extra="keywords+%d" % int(stats.get("_added", 0)))
    LOG.info("发布结果：%s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
