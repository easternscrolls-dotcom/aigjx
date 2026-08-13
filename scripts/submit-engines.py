#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
submit-engines.py —— 主动推送站点地图给搜索引擎，加速收录
=========================================================================
支持三个通道：
  1) Google  ping：GET https://www.google.com/ping?sitemap=<sitemap_url>
                    （无需密钥，官方标准接口）
  2) Bing    ping：GET https://www.bing.com/ping?sitemap=<sitemap_url>
                    （无需密钥，官方标准接口，Bing 同时喂给 Yahoo/DuckDuckGo）
  3) 百度主动推送：POST http://data.zz.baidu.com/urls?site=<域名>&token=<TOKEN>
                    （需 BAIDU_PUSH_TOKEN；站点须先在百度搜索资源平台验证）
                    逐条推送 sitemap.xml 内的全部 URL（上限 2000 条/次，本站远小于此）

设计原则：
  · 零依赖（仅标准库 + requests）；网络失败只告警、不中断流水线（exit 0）。
  · 支持环境变量 BAIDU_PUSH_TOKEN（选填）。未配置则跳过百度推送并打印提示。
  · 支持命令行参数 --dry-run：只打印将要发送的内容，不发网络请求（本地调试用）。
  · 读取 data/agent-list.json 的 site.baseUrl 拼出 sitemap.xml 绝对地址。

用法：
  python scripts/submit-engines.py            # 真实推送
  python scripts/submit-engines.py --dry-run  # 仅打印，不发送
"""
import os
import sys
import json
import argparse
import urllib.parse
import urllib.request
import urllib.error

# 允许无 requests 时降级到标准库（CI 默认装了 requests，这里优先用标准库更稳）
try:
    import requests
    HAS_REQUESTS = True
except Exception:
    HAS_REQUESTS = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(ROOT, 'data', 'agent-list.json')
SITEMAP_FILE = os.path.join(ROOT, 'sitemap.xml')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
TIMEOUT = 20


def log(msg):
    print('[收录] ' + msg)


def load_base():
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    base = (data.get('site', {}).get('baseUrl') or 'https://aiagent.72tool.com').rstrip('/')
    return base


def extract_urls_from_sitemap():
    """从 sitemap.xml 解析出全部 <loc> URL（单一事实来源，避免与生成器重复逻辑）。"""
    urls = []
    try:
        with open(SITEMAP_FILE, 'r', encoding='utf-8') as f:
            txt = f.read()
        import re
        for m in re.findall(r'<loc>(.*?)</loc>', txt, re.S):
            u = m.strip()
            if u:
                urls.append(u)
    except Exception as e:
        log('读取 sitemap.xml 失败：%s' % e)
    return urls


def ping_search_engine(name, endpoint, params, dry_run):
    """对 Google/Bing 发 ping 请求。返回 True/False。"""
    q = urllib.parse.urlencode(params)
    url = endpoint + '?' + q
    if dry_run:
        log('[%s] DRY-RUN 将请求：%s' % (name, url))
        return True
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode('utf-8', 'ignore')
            ok = resp.status == 200
            log('[%s] HTTP %s %s' % (name, resp.status, (body[:80] if body else '')))
            return ok
    except urllib.error.HTTPError as e:
        log('[%s] HTTP 错误 %s：%s' % (name, e.code, e.read().decode('utf-8', 'ignore')[:120]))
        return False
    except Exception as e:
        log('[%s] 请求失败：%s' % (name, e))
        return False


def baidu_push(sitemap_url, all_urls, dry_run):
    """百度主动推送（普通收录接口）。需 BAIDU_PUSH_TOKEN 与已验证站点。"""
    token = os.environ.get('BAIDU_PUSH_TOKEN', '').strip()
    if not token:
        log('[百度] 未配置 BAIDU_PUSH_TOKEN，跳过百度主动推送（如需请到 GitHub Secrets 配置）')
        return False
    # site 取 baseUrl 主机名（须与百度搜索资源平台已验证站点一致）
    from urllib.parse import urlparse
    host = urlparse(sitemap_url).netloc
    if not host:
        log('[百度] 无法解析站点域名，跳过')
        return False
    endpoint = 'http://data.zz.baidu.com/urls?site=%s&token=%s' % (host, token)
    payload = '\n'.join(all_urls)
    if dry_run:
        log('[百度] DRY-RUN 将 POST %d 条 URL 到 %s' % (len(all_urls), endpoint))
        return True
    try:
        req = urllib.request.Request(
            endpoint, data=payload.encode('utf-8'),
            headers={'User-Agent': UA, 'Content-Type': 'text/plain'})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode('utf-8', 'ignore')
            log('[百度] HTTP %s -> %s' % (resp.status, body[:160]))
            return resp.status == 200
    except urllib.error.HTTPError as e:
        log('[百度] HTTP 错误 %s：%s' % (e.code, e.read().decode('utf-8', 'ignore')[:160]))
        return False
    except Exception as e:
        log('[百度] 请求失败：%s' % e)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='只打印将要发送的内容，不发送网络请求')
    args = ap.parse_args()
    dry = args.dry_run

    log('========== 搜索引擎收录推送开始 ==========')
    base = load_base()
    sitemap_url = base + '/sitemap.xml'
    log('站点根：%s' % base)
    log('Sitemap：%s' % sitemap_url)

    all_urls = extract_urls_from_sitemap()
    if not all_urls:
        log('未能从 sitemap.xml 解析到任何 URL，终止')
        return 0
    log('待推送 URL 总数：%d' % len(all_urls))

    # 1) Google ping
    g_ok = ping_search_engine('Google', 'https://www.google.com/ping',
                              {'sitemap': sitemap_url}, dry)
    # 2) Bing ping
    b_ok = ping_search_engine('Bing', 'https://www.bing.com/ping',
                              {'sitemap': sitemap_url}, dry)
    # 3) 百度主动推送（含全部 URL）
    baidu_ok = baidu_push(sitemap_url, all_urls, dry)

    log('结果：Google=%s, Bing=%s, 百度=%s' % (g_ok, b_ok, baidu_ok))
    log('========== 收录推送结束（无论成败均不阻断部署）==========')
    return 0  # 始终 0，避免网络波动中断 CI 部署


if __name__ == '__main__':
    sys.exit(main())
