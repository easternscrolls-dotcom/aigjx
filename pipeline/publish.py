# -*- coding: utf-8 -*-
"""模块8：全自动部署发布链路 + 构建失败自动回滚。

能力：
1. 多语言 Markdown 已按语种分目录存储（由 generate 完成），此处只负责入库与推送；
2. 无变更自动跳过 Git 推送，避免空提交与冲突；
3. 推送前本地 `hugo --minify` 构建预检：
   - 预检通过 → 提交并推送 → Cloudflare Pages 自动构建上线；
   - 预检失败 → 自动回滚本轮新增/修改内容（git checkout/clean），
     线上继续保留上一个稳定版本，网站不会空白下线；
4. 记录最近一次成功构建的 commit，便于人工/自动追溯。

零成本说明：不使用任何付费 API。回滚通过 Git 与本地构建预检实现，
不依赖 Cloudflare 付费接口；如你另行配置了免费的 wrangler token，
也可在 workflow 里追加一步 `wrangler pages deploy`，本模块无需改动。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import config, sitemaps, utils, check_links

LOG = utils.get_logger("publish")

BUILD_STATE = "build_history"


def _run(cmd: Sequence[str], cwd: Optional[Path] = None, check: bool = False) -> Dict[str, Any]:
    """跨平台执行子进程（Windows / macOS / Linux 通用）。"""
    LOG.info("$ %s", " ".join(cmd))
    proc = subprocess.run(
        list(cmd), cwd=str(cwd or config.ROOT), capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    if proc.stdout.strip():
        LOG.info(proc.stdout.strip()[:4000])
    if proc.returncode != 0:
        LOG.warning("命令退出码 %s: %s", proc.returncode, (proc.stderr or "").strip()[:2000])
        if check:
            raise RuntimeError("命令失败: %s" % " ".join(cmd))
    return {"code": proc.returncode, "out": proc.stdout, "err": proc.stderr}


# ---------------------------------------------------------------- Git 基础
def git_available() -> bool:
    return shutil.which("git") is not None


def has_changes(paths: Sequence[str]) -> bool:
    """判断指定路径是否存在待提交变更（包含未跟踪文件）。"""
    result = _run(["git", "status", "--porcelain", "--"] + list(paths))
    return bool(result["out"].strip())


def configure_identity() -> None:
    name = config.get("publish.git_user_name", "autoguide-bot")
    email = config.get("publish.git_user_email", "autoguide-bot@users.noreply.github.com")
    _run(["git", "config", "user.name", name])
    _run(["git", "config", "user.email", email])


# ---------------------------------------------------------------- 构建预检
def hugo_available() -> bool:
    return shutil.which(str(config.get("selfheal.hugo_binary", "hugo"))) is not None


def build_precheck() -> bool:
    """本地 Hugo 构建预检。hugo 不存在时跳过（返回 True，不阻塞流水线）。"""
    if not config.get("selfheal.build_precheck", True):
        return True
    if not hugo_available():
        LOG.warning("未检测到 hugo 可执行文件，跳过构建预检（Actions 环境会安装 hugo）")
        return True
    binary = str(config.get("selfheal.hugo_binary", "hugo"))
    result = _run([binary, "--minify", "--gc",
                   "--destination", str(config.SITE_DIR / "public")],
                  cwd=config.SITE_DIR)
    ok = result["code"] == 0
    if ok:
        # 构建期全量内链校验：失效内链直接阻断发布，提前规避线上 404（快赢 #5.3）
        try:
            broken = check_links.check(config.SITE_DIR / "public")
            if broken:
                LOG.warning("内链校验发现 %d 个失效链接，阻断发布（前 %d 条）：%s",
                            len(broken), min(len(broken), 5), broken[:5])
                ok = False
            else:
                LOG.info("内链校验通过：无失效链接")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("内链校验异常（不阻塞发布）：%s", exc)
    LOG.info("构建预检%s", "通过" if ok else "失败")
    return ok


def rollback(paths: Sequence[str]) -> None:
    """回滚本轮内容变更：已跟踪文件 checkout，未跟踪文件 clean。

    目的：坏内容永不进入远端仓库 → Cloudflare 永远构建的是上一个稳定版本。
    """
    LOG.warning("触发自动回滚，放弃本轮内容变更以保住线上稳定版本")
    _run(["git", "checkout", "--"] + list(paths))
    _run(["git", "clean", "-fd", "--"] + list(paths))


# ---------------------------------------------------------------- 主流程
def publish(message_extra: str = "") -> Dict[str, Any]:
    """提交并推送内容变更。返回执行结果摘要。"""
    if not git_available():
        LOG.error("未安装 git，跳过发布（本地调试可忽略）")
        return {"pushed": False, "reason": "git-missing"}

    watch_paths = ["site/content", "site/static", "data/keywords", "data/faq", "data/snippets",
                   "data/localization", "data/blacklist", "data/state", "backup"]
    existing = [p for p in watch_paths if (config.ROOT / p).exists()]

    if not has_changes(existing):
        LOG.info("无变更，跳过 Git 推送（避免空提交与冲突）")
        return {"pushed": False, "reason": "no-changes"}

    if not build_precheck():
        rollback(existing)
        state = utils.load_state(BUILD_STATE, {"history": []})
        state.setdefault("history", []).append(
            {"at": utils.iso_now(), "status": "rolled_back", "reason": "hugo build failed"})
        utils.save_state(BUILD_STATE, state)
        return {"pushed": False, "reason": "build-failed-rolled-back"}

    # 内容变更已落盘 → 依据最新内容重新生成分层 sitemap + robots.txt
    try:
        counts = sitemaps.build()
        LOG.info("分层 sitemap 生成：%s", counts)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("sitemap 生成失败（不阻塞发布）：%s", exc)

    configure_identity()
    _run(["git", "add", "--"] + existing)
    prefix = str(config.get("publish.commit_prefix", "chore(auto): "))
    message = "%spipeline %s %s" % (prefix, utils.today_str(), message_extra)
    commit = _run(["git", "commit", "-m", message.strip()])
    if commit["code"] != 0:
        LOG.info("commit 无内容或失败，视为无变更")
        return {"pushed": False, "reason": "nothing-to-commit"}

    branch = str(config.get("publish.branch", "main"))
    # 先 rebase 拉取远端，避免并发 Actions 造成非快进拒绝
    _run(["git", "pull", "--rebase", "--autostash", "origin", branch])
    push = _run(["git", "push", "origin", "HEAD:%s" % branch])
    ok = push["code"] == 0

    head = _run(["git", "rev-parse", "HEAD"])["out"].strip()
    state = utils.load_state(BUILD_STATE, {"history": []})
    state["last_success_commit"] = head if ok else state.get("last_success_commit", "")
    state.setdefault("history", []).append(
        {"at": utils.iso_now(), "status": "pushed" if ok else "push_failed", "commit": head})
    state["history"] = state["history"][-50:]
    utils.save_state(BUILD_STATE, state)

    LOG.info("发布%s（commit=%s）", "成功" if ok else "失败", head[:8])
    return {"pushed": ok, "commit": head}


def cleanup_workspace() -> Dict[str, int]:
    """清理临时缓存与构建产物，压缩打包体积，避免构建超时。"""
    removed = {
        "raw": utils.prune_old_files(config.RAW_DIR, 3, ["items_*.json"]),
        "translated": utils.prune_old_files(config.TRANSLATED_DIR, 5, ["items_*.json"]),
        "queue": utils.prune_old_files(config.QUEUE_DIR, 14, ["retry_*.json"]),
    }
    public = config.SITE_DIR / "public"
    if public.exists():
        shutil.rmtree(public, ignore_errors=True)
        removed["public"] = 1
    LOG.info("工作区清理：%s", removed)
    return removed


if __name__ == "__main__":
    publish()
