# -*- coding: utf-8 -*-
"""模块7：托管平台故障自动自愈（GitHub + Cloudflare）。

每周巡检 + 日常兜底，全部自动，无需人工日常维护：
1. 校验三套免费翻译源连通性，全部失效时降级告警并延长冷却（不影响已有页面）；
2. 校验 sitemap / robots / 站点模板齐备，缺失文件自动生成空白兜底模板；
3. 校验词库、FAQ 库、短句库、黑名单完整性，缺失则自动生成带表头的空白 CSV；
4. 清理过期缓存与超龄旧文章，压缩仓库与构建体积，避免构建超时；
5. 每周自动备份全套词库/FAQ/短句/黑名单到 backup/<年-周>/ 目录，防丢失；
6. 汇总巡检报告写入 data/state/health_report.json，供故障自查手册对照。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List

from . import config, publish, seo, translate, utils

LOG = utils.get_logger("selfheal")

HEALTH_STATE = "health_report"

# 必备 CSV 及其表头（缺失时自动生成空白模板，保证流水线不因缺文件中断）
CSV_SCHEMA = {
    config.KEYWORDS_CSV: ["lang", "keyword", "category", "source", "added_at"],
    config.FAQ_CSV: ["lang", "question", "answer", "tag"],
    config.SNIPPETS_CSV: ["lang", "slot", "text"],
    config.LOCALIZATION_CSV: ["lang", "pattern", "replacement", "kind"],
    config.BLACKLIST_CSV: ["lang", "pattern", "replacement", "note"],
}


# ---------------------------------------------------------------- 文件兜底
def ensure_csv_files() -> Dict[str, str]:
    """缺失 CSV 自动生成空白模板；已存在但为空（仅表头）时给出告警。"""
    report: Dict[str, str] = {}
    for path, fields in CSV_SCHEMA.items():
        if not path.exists():
            utils.write_csv_rows(path, fields, [])
            report[path.name] = "created-empty-template"
            LOG.warning("缺失 %s，已自动生成空白模板（请补充内容）", path.name)
            continue
        rows = utils.read_csv_rows(path)
        report[path.name] = "ok(%d rows)" % len(rows) if rows else "empty"
        if not rows:
            LOG.warning("%s 内容为空，生成流程会跳过对应语种", path.name)
    return report


def ensure_site_files() -> Dict[str, str]:
    """站点关键模板缺失时生成最小可用兜底版本，保证 Hugo 一定能构建成功。"""
    report: Dict[str, str] = {}
    layouts = config.SITE_DIR / "layouts"
    layouts.mkdir(parents=True, exist_ok=True)

    fallbacks = {
        layouts / "sitemap.xml": (
            '{{ printf "<?xml version=\\"1.0\\" encoding=\\"utf-8\\" standalone=\\"yes\\"?>" | safeHTML }}\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            '  {{ range .Data.Pages }}<url><loc>{{ .Permalink }}</loc></url>\n  {{ end }}\n'
            '</urlset>\n'
        ),
        layouts / "robots.txt": (
            "User-agent: *\nAllow: /\nSitemap: {{ .Site.BaseURL }}sitemap.xml\n"
        ),
    }
    for path, content in fallbacks.items():
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            report[path.name] = "created-fallback"
            LOG.warning("站点模板缺失，已生成兜底版本：%s", path.name)
        else:
            report[path.name] = "ok"

    for lang in config.locale_codes():
        folder = config.CONTENT_DIR / lang
        folder.mkdir(parents=True, exist_ok=True)
        index = folder / "_index.md"
        if not index.exists():
            locale = config.locale_by_code(lang)
            index.write_text(
                '---\ntitle: "%s"\ndescription: "%s"\ntype: "home"\n---\n' % (
                    config.get("site.brand", "AutoGuide"), locale.get("name", lang)),
                encoding="utf-8")
            report["content/%s/_index.md" % lang] = "created-fallback"
    report.update({k: ("ok" if v else "missing") for k, v in seo.verify_sitemap_layout().items()})
    return report


# ---------------------------------------------------------------- 连通性
def check_translation_sources() -> Dict[str, bool]:
    result = translate.check_providers()
    alive = [k for k, v in result.items() if v]
    if not alive:
        LOG.error("巡检：三套免费翻译源全部不可用！采集与生成将自动跳过翻译阶段，"
                  "素材进入延迟队列等待下次自动重试（无需人工干预）")
    else:
        LOG.info("巡检：可用翻译源 %s", ", ".join(alive))
    return result


# ---------------------------------------------------------------- 清理 / 备份
def clean_old_articles() -> int:
    """删除超龄旧文章，压缩构建体积（保留 hub 聚合页与 _index）。"""
    keep_days = int(config.get("selfheal.article_keep_days", 240))
    removed = 0
    for lang in config.locale_codes():
        folder = config.CONTENT_DIR / lang / config.topic_section()
        if not folder.exists():
            continue
        removed += utils.prune_old_files(folder, keep_days, ["*.md"])
    if removed:
        LOG.info("清理超龄旧文章 %d 篇（阈值 %d 天）", removed, keep_days)
    return removed


def clean_caches() -> Dict[str, int]:
    keep = int(config.get("selfheal.cache_file_keep_days", 60))
    return {
        "cache": utils.prune_old_files(config.CACHE_DIR, keep, ["*.json"]),
        "raw": utils.prune_old_files(config.RAW_DIR, 3, ["*.json"]),
        "translated": utils.prune_old_files(config.TRANSLATED_DIR, 5, ["*.json"]),
        "queue": utils.prune_old_files(config.QUEUE_DIR, 14, ["*.json"]),
    }


def backup_data() -> str:
    """每周备份词库/FAQ/短句/黑名单到 backup/<年-周>/。"""
    target = config.BACKUP_DIR / utils.week_tag()
    target.mkdir(parents=True, exist_ok=True)
    for path in CSV_SCHEMA:
        if path.exists():
            shutil.copy2(str(path), str(target / path.name))
    # 保留最近 N 周备份，其余自动删除
    keep_weeks = int(config.get("selfheal.backup_keep_weeks", 8))
    folders = sorted([p for p in config.BACKUP_DIR.iterdir() if p.is_dir()])
    for old in folders[:-keep_weeks] if len(folders) > keep_weeks else []:
        shutil.rmtree(old, ignore_errors=True)
        LOG.info("清理过期备份目录 %s", old.name)
    LOG.info("词库备份完成 -> %s", target)
    return str(target)


# ---------------------------------------------------------------- 汇总
def run(full: bool = True) -> Dict[str, Any]:
    config.ensure_dirs()
    report: Dict[str, Any] = {"at": utils.iso_now(), "topic": config.current_topic()}
    report["csv"] = ensure_csv_files()
    report["site"] = ensure_site_files()
    if full:
        report["translate_sources"] = check_translation_sources()
        report["cleaned_articles"] = clean_old_articles()
        report["cleaned_caches"] = clean_caches()
        report["backup"] = backup_data()
    report["content_counts"] = {
        lang: len(list((config.CONTENT_DIR / lang / config.topic_section()).glob("*.md")))
        if (config.CONTENT_DIR / lang / config.topic_section()).exists() else 0
        for lang in config.locale_codes()
    }
    utils.save_state(HEALTH_STATE, report)
    LOG.info("巡检报告：%s", report["content_counts"])
    return report


if __name__ == "__main__":
    run(full=True)
    publish.publish(message_extra="weekly-health")
