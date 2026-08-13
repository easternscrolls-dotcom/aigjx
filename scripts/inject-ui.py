#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inject-ui.py —— 一次性 UI 注入脚本（幂等，可重复运行）
作用：
  1) 为所有 HTML 页面重构“四大核心分类导航栏”：
     - 顶部导航移除“配套辅助”链接（它本就是侧边栏分类，保持 4 大核心纯净）
     - 增加移动端汉堡按钮 (id=navToggle) 与可折叠菜单容器 (id=catNavLinks)
  2) 在每个含 .toolbar 的页面（首页 + 5 个分类页）的搜索工具条前，
     注入多条件筛选栏（免费开源 / 本地离线 / 跨境专用 / 零代码）。
  3) 全仓将示例域名 freeai.72tool.com 统一切换为 aiagent.72tool.com
     （与用户声明的线上域名一致；仅改字符串，不改 JSON 数据结构）。
所有改动均带存在性判断，重复运行不会重复注入或破坏文件。
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 需要注入导航/筛选的 HTML 页面
HTML_PAGES = [
    "index.html", "local.html", "browser.html",
    "workflow.html", "crossborder.html", "tools.html",
    "update.html", "404.html",
]

FILTER_BAR = '''      <div class="filters" id="filters" role="group" aria-label="多条件筛选">
        <span class="filters-label">筛选：</span>
        <button type="button" class="chip filter" data-filter="open-source">免费开源</button>
        <button type="button" class="chip filter" data-filter="local-offline">本地离线</button>
        <button type="button" class="chip filter" data-filter="crossborder">跨境专用</button>
        <button type="button" class="chip filter" data-filter="no-code">零代码</button>
        <button type="button" class="chip filter-reset" id="filterReset">重置</button>
      </div>
'''


def inject_html(path):
    with open(path, "r", encoding="utf-8") as f:
        s = f.read()
    orig = s

    # 1) cat-nav-inner 加 id（幂等）
    if 'id="catNavLinks"' not in s:
        s = s.replace('<div class="cat-nav-inner">',
                      '<div class="cat-nav-inner" id="catNavLinks">')

    # 2) 注入汉堡按钮（幂等：仅在 nav 开标签后、且尚无 navToggle 时加）
    if 'id="navToggle"' not in s:
        s = re.sub(r'(<nav class="cat-nav" aria-label="分类导航">)(\r?\n)',
                   r'\1\2    <button class="nav-toggle" id="navToggle" '
                   r'aria-label="展开/收起分类菜单" aria-expanded="false">☰ 分类</button>\2',
                   s, count=1)

    # 3) 移除顶部“配套辅助”链接（仅保留侧边栏中的），保持四大核心纯净（幂等）
    s = re.sub(r'\s*<a class="cat-link side"[^>]*>.*?</a>\r?\n', '\n', s)

    # 4) 在 .toolbar 前注入筛选栏（仅当存在 toolbar 且尚未注入，幂等）
    if 'id="filters"' not in s and '<div class="toolbar">' in s:
        s = s.replace('<div class="toolbar">',
                      FILTER_BAR + '      <div class="toolbar">', 1)

    if s != orig:
        with open(path, "w", encoding="utf-8") as f:
            f.write(s)
        print("  ✅ 已更新", os.path.basename(path))
    else:
        print("  – 无需改动", os.path.basename(path))


def replace_domain():
    """全仓将 freeai.72tool.com 切换为 aiagent.72tool.com（仅字符串，不改结构）。"""
    count = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        # 跳过版本控制与记忆目录
        dirnames[:] = [d for d in dirnames if d not in (".git", ".workbuddy")]
        for fn in filenames:
            if fn.endswith((".html", ".css", ".js", ".json", ".xml",
                            ".txt", ".md", ".toml", ".yml", ".yaml")):
                p = os.path.join(dirpath, fn)
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        s = f.read()
                except (UnicodeDecodeError, OSError):
                    continue
                if "freeai.72tool.com" in s:
                    s = s.replace("freeai.72tool.com", "aiagent.72tool.com")
                    with open(p, "w", encoding="utf-8") as f:
                        f.write(s)
                    count += 1
                    print("  ✅ 域名替换", os.path.relpath(p, ROOT))
    if count == 0:
        print("  – 未发现 freeai.72tool.com 引用")


def main():
    print("=== 1) 注入导航重构 + 筛选栏 ===")
    for pg in HTML_PAGES:
        p = os.path.join(ROOT, pg)
        if os.path.exists(p):
            inject_html(p)
        else:
            print("  (跳过，不存在)", pg)
    print("=== 2) 全仓域名切换 ===")
    replace_domain()
    print("完成。")


if __name__ == "__main__":
    main()
