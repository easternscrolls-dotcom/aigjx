# -*- coding: utf-8 -*-
"""构建期全量内链校验（快赢优化 #5.3）。

扫描 Hugo 构建产物 site/public 下所有 HTML，提取内部 href 链接并校验其
是否真实可解析到产物文件；发现失效内链即视为构建不合格，由 publish 预检
阻断推送，提前规避线上 404。

解析规则：
  * 跳过外部/协议相对/锚点/特殊协议（http(s)://、//、mailto:、tel:、#、空）；
  * 绝对路径 /x/y/ 与相对路径 ./x、../x 均按 Hugo 输出结构解析：
      - 精确文件（/robots.txt、/sitemap.xml、/css/main.css …）
      - 加 .html 后缀（/en/best/x -> /en/best/x.html）
      - 加 /index.html（目录式干净 URL /en/best/x/ -> /en/best/x/index.html）
      - 别名 301 存根 /en/tools/x/ 同样以 index.html 存在，自然命中；
  * 仅 stdlib，零外部依赖，CI 与本机均可运行。

零付费、无 LLM。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple

LOG = None  # 延迟导入，避免无 config 时崩溃

_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
_SKIP_PREFIXES = ("http://", "https://", "//", "mailto:", "tel:", "javascript:", "data:")


def _normalize(href: str) -> str:
    """去 fragment / query，返回用于解析的路径部分。"""
    href = href.strip()
    for sep in ("#", "?"):
        idx = href.find(sep)
        if idx >= 0:
            href = href[:idx]
    return href


def _resolve(public: Path, page: Path, href: str):
    """将 href 解析为 public 下的绝对路径；越界或非法返回 None。"""
    if href.startswith("/"):
        rel = href.lstrip("/")
        return public / rel
    # 相对路径：相对当前 html 文件目录解析
    try:
        abs_path = (page.parent / href).resolve()
        return abs_path
    except (OSError, ValueError):
        return None


def _exists(base: Path) -> bool:
    if base is None:
        return False
    if base.exists():
        return True
    # /en/best/x -> /en/best/x.html
    if base.with_suffix(".html").exists():
        return True
    # /en/best/x/ -> /en/best/x/index.html（含别名存根）
    if (base / "index.html").exists():
        return True
    return False


def check(public_dir) -> List[Tuple[str, str]]:
    """校验 public_dir 下全部 HTML 的内部链接。返回失效列表 [(page_rel, href), ...]。"""
    public = Path(public_dir)
    if not public.exists():
        return []
    broken: List[Tuple[str, str]] = []
    pages = sorted(public.rglob("*.html"))
    for page in pages:
        try:
            html = page.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in _HREF_RE.findall(html):
            href = _normalize(raw)
            if not href or href.startswith(_SKIP_PREFIXES) or href.startswith("#"):
                continue
            base = _resolve(public, page, href)
            if not _exists(base):
                broken.append((str(page.relative_to(public)), raw))
    return broken


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    public = Path(argv[0]) if argv else Path("site/public")
    broken = check(public)
    if broken:
        print("发现 %d 个失效内链：" % len(broken), file=sys.stderr)
        for page, href in broken[:50]:
            print("  %s  ->  %s" % (page, href), file=sys.stderr)
        return 1
    print("内链校验通过：无失效链接。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
