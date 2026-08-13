#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate-tool-pages.py —— 为每个 Agent 工具生成独立静态详情页（零依赖，仅用 Python 标准库）
====================================================================================
读取 data/agent-list.json，为每一个 agent 生成 tools/<id>.html，包含：
  · 独立 TDK（title / description / keywords / og）+ canonical，利于百度/谷歌收录
  · 语义化 H2/H3 结构：工具简介 → 核心功能 → 适用场景 → 使用教程 → 同类推荐 → 相关分类
  · 详细教程：优先用 agent.tutorial（智谱 GLM 生成，mini-markdown）；缺则按字段模板生成
  · 同类 Agent 推荐：同分类内链（tools/<id>.html），形成站内网状结构（SEO 内链）
  · 外链 CTA：直达官网
与现有站点一致：复用 ../css/style.css、复用顶部导航与页脚布局，含轻量主题切换脚本。
用法：python scripts/generate-tool-pages.py
"""
import os
import re
import json
import html

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(ROOT, 'data', 'agent-list.json')
OUT_DIR = os.path.join(ROOT, 'tools')

TYPE_LABEL = {'open-source': '开源免费', 'cloud-paid': '云端付费', 'local-offline': '本地离线'}
DIFF_MAP = {'cloud-paid': '免部署', 'open-source': '需部署', 'local-offline': '本地部署'}
ACCESS_OK = ['github.com', 'gitee.com', 'gitcode.com']
ACCESS_WARN = ['huggingface.co', 'hf.space', 'openai.com', 'anthropic.com',
               'google.com', 'google.dev', 'vercel.app', 'replit.app', 'firecrawl.dev']


def host_of(url):
    try:
        from urllib.parse import urlparse
        return urlparse(url or '').hostname.replace('www.', '') if url else ''
    except Exception:
        return ''


def diff_label(t):
    return DIFF_MAP.get(t, '-')


def access_label(t, url):
    h = host_of(url)
    if any(h == d or h.endswith('.' + d) for d in ACCESS_OK):
        return '国内可访问'
    if any(h == d or h.endswith('.' + d) for d in ACCESS_WARN):
        return '可能受限'
    if t == 'local-offline':
        return '本地可用'
    if t == 'open-source':
        return '自部署可控'
    return '视网络环境'


def esc(s):
    return html.escape(str(s if s is not None else ''))


def md_to_html(text):
    """把 mini-markdown（## 小节、- 列表、数字列表、段落）转为安全 HTML（先转义再结构化）。"""
    text = esc(text).replace('\r\n', '\n')
    out = []
    list_type = [None]

    def close_list():
        if list_type[0]:
            out.append('</%s>' % list_type[0])
            list_type[0] = None

    for line in text.split('\n'):
        s = line.strip()
        if not s:
            close_list()
            continue
        if s.startswith('## '):
            close_list()
            out.append('<h3>' + s[3:].strip() + '</h3>')
        elif s.startswith('- '):
            if list_type[0] != 'ul':
                close_list()
                out.append('<ul>')
                list_type[0] = 'ul'
            out.append('<li>' + s[2:].strip() + '</li>')
        elif re.match(r'^\d+\.\s', s):
            if list_type[0] != 'ol':
                close_list()
                out.append('<ol>')
                list_type[0] = 'ol'
            out.append('<li>' + re.sub(r'^\d+\.\s', '', s) + '</li>')
        else:
            close_list()
            out.append('<p>' + s + '</p>')
    close_list()
    return '\n'.join(out)


def template_tutorial(a, cat_name):
    """无 AI 教程时的字段模板（保证每页都有可读内容）。"""
    scen = '、'.join(a.get('scenarios') or []) or '各类自动化场景'
    steps = [
        '打开官网 %s，注册账号或克隆仓库（开源类可本地部署）。' % esc(a.get('url', '')),
        '阅读官方文档，完成基础配置（API Key / 环境变量 / 工作流节点）。',
        '按你的业务场景接入数据源或外部 API，跑通第一个自动化任务。',
        '结合下方「同类 Agent 推荐」横向对比，挑选最契合你需求的方案。',
    ]
    return (
        '## 这个工具能做什么\n' + (a.get('summary') or '') + '\n'
        '## 适合谁用\n如果你正在寻找「' + cat_name + '」类的自动化方案，' + esc(a.get('name', '')) + ' 值得一试；常见场景包括：' + scen + '。\n'
        '## 快速上手步骤\n' + '\n'.join('%d. %s' % (i + 1, s) for i, s in enumerate(steps)) + '\n'
        '## 小提示\n本站对所有收录工具做统一标签（开源免费 / 云端付费 / 本地离线）与部署难度标注，对比选型更省心。'
    )


def build_html(a, data):
    base = (data.get('site', {}).get('baseUrl', 'https://aiagent.72tool.com')).rstrip('/')
    cats = {c['id']: c for c in data.get('categories', [])}
    cat_id = a.get('category', 'tools')
    cat = cats.get(cat_id, {'id': cat_id, 'name': cat_id, 'icon': '🛠️'})
    cat_name = cat.get('name', cat_id)
    aid = a.get('id') or re.sub(r'[^a-z0-9]+', '-', str(a.get('name', 'tool')).lower()).strip('-')
    url = '%s/tools/%s.html' % (base, aid)
    name = a.get('name', '工具')
    summary = a.get('summary', '')
    tags = a.get('tags', []) or []
    scen = a.get('scenarios', []) or []
    ttype = a.get('type', 'cloud-paid')
    tlabel = TYPE_LABEL.get(ttype, ttype)
    diff = diff_label(ttype)
    access = access_label(ttype, a.get('url', ''))

    title = '%s 是什么？功能介绍、使用教程与同类 Agent 推荐 | AgentHub' % name
    desc = '%s 收录于 AgentHub AI智能体导航，提供 %s 的详细功能介绍、上手教程与同类 Agent 工具推荐，覆盖%s方向。' % (name, name, cat_name)
    kw = ', '.join([name] + tags + [cat_name, 'AI智能体', 'Agent工具', '自动化'])

    peers = [p for p in data.get('agents', []) if p.get('category') == cat_id and (p.get('id') or '') != aid]
    peers = peers[:6]
    if peers:
        peer_html = ''.join(
            '<a class="peer-card" href="../tools/%s.html">'
            '<span class="name">%s</span>'
            '<span class="tag">%s</span>'
            '<span class="sum">%s</span>'
            '</a>' % (esc(p.get('id') or ''), esc(p.get('name', '')), TYPE_LABEL.get(p.get('type', ''), ''), esc((p.get('summary') or '')[:40]))
            for p in peers
        )
    else:
        peer_html = '<p class="empty">该分类下暂无其他收录工具。</p>'

    tutorial_raw = a.get('tutorial') or template_tutorial(a, cat_name)
    tutorial_html = md_to_html(tutorial_raw)

    tag_chips = ''.join('<span class="chip">%s</span>' % esc(t) for t in tags)
    scen_chips = ''.join('<span class="chip scenario">%s</span>' % esc(s) for s in scen)

    dead = a.get('status') == 'dead'
    cta = ('<span class="btn-go disabled">链接已失效</span>' if dead
           else '<a class="btn-go" href="%s" target="_blank" rel="noopener noreferrer">前往官网体验 ↗</a>' % esc(a.get('url', '#')))

    return '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>%(title)s</title>
  <meta name="description" content="%(desc)s" />
  <meta name="keywords" content="%(kw)s" />
  <meta name="robots" content="index, follow" />
  <link rel="canonical" href="%(url)s" />
  <meta name="theme-color" content="#4f46e5" />
  <meta property="og:title" content="%(title)s" />
  <meta property="og:description" content="%(desc)s" />
  <meta property="og:type" content="article" />
  <meta property="og:url" content="%(url)s" />
  <link rel="stylesheet" href="../css/style.css" />
</head>
<body data-page="tool">
  <header class="site-header">
    <div class="header-inner">
      <a class="brand" href="../index.html" aria-label="AgentHub 首页">
        <span class="logo">🤖</span>
        <span>AgentHub<small>AI智能体自动化工具大全</small></span>
      </a>
      <button class="theme-toggle" id="themeToggle" aria-label="切换深浅色">🌙</button>
    </div>
  </header>

  <nav class="cat-nav" aria-label="分类导航">
    <div class="cat-nav-inner">
      <a class="cat-link" href="../local.html">🖥️ 本地开源框架</a>
      <a class="cat-link" href="../browser.html">🌐 浏览器网页</a>
      <a class="cat-link" href="../workflow.html">🔗 低代码工作流</a>
      <a class="cat-link" href="../crossborder.html">🌏 跨境商用</a>
      <a class="cat-link" href="../tools.html">🛠️ 配套辅助</a>
    </div>
  </nav>

  <div class="layout single">
    <main class="tool-detail">
      <nav class="breadcrumb"><a href="../index.html">首页</a> › <a href="../%(catid)s.html">%(catname)s</a> › <span>%(name)s</span></nav>

      <article class="card%(deadcls)s">
        <div class="head">
          <span class="name">%(name)s</span>
          <span class="tag-type tag-%(type)s">%(tlabel)s</span>
        </div>
        <div class="card-meta">
          <span class="badge-meta">部署：%(diff)s</span>
          <span class="badge-meta">国内：%(access)s</span>
        </div>
        %(cta)s
      </article>

      <section class="block">
        <h2>工具简介</h2>
        <p>%(summary)s</p>
        <div class="tags">%(tagchips)s %(scenchips)s</div>
      </section>

      <section class="block">
        <h2>使用教程</h2>
        %(tutorial)s
      </section>

      <section class="block">
        <h2>同类 %(catname)s 推荐（站内导航）</h2>
        <div class="peer-grid">%(peers)s</div>
      </section>

      <section class="block">
        <h2>相关分类</h2>
        <p><a class="btn-go" href="../%(catid)s.html">浏览全部「%(catname)s」工具 →</a></p>
      </section>
    </main>
  </div>

  <footer class="site-footer">
    <div class="container">
      <p>AgentHub · 垂直 AI 智能体 Agent 工具导航 · 数据驱动纯静态站点，无广告、零后端</p>
      <p>© 2026 AgentHub · <a href="../update.html">更新日志</a> · <a href="%(sitemap)s">Sitemap</a></p>
    </div>
  </footer>

  <script>
    (function () {
      var saved = localStorage.getItem('ah-theme');
      var dark = saved === 'dark' || (!saved && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
      if (dark) document.documentElement.setAttribute('data-theme', 'dark');
      var btn = document.getElementById('themeToggle');
      if (btn) {
        btn.textContent = dark ? '☀️' : '🌙';
        btn.addEventListener('click', function () {
          var cur = document.documentElement.getAttribute('data-theme');
          var next = cur === 'dark' ? 'light' : 'dark';
          if (next === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
          else document.documentElement.removeAttribute('data-theme');
          localStorage.setItem('ah-theme', next);
          btn.textContent = next === 'dark' ? '☀️' : '🌙';
        });
      }
    })();
  </script>
</body>
</html>
''' % {
        'title': esc(title), 'desc': esc(desc), 'kw': esc(kw), 'url': esc(url),
        'name': esc(name), 'catid': esc(cat_id), 'catname': esc(cat_name),
        'type': esc(ttype), 'tlabel': esc(tlabel), 'diff': esc(diff), 'access': esc(access),
        'deadcls': ' dead' if dead else '', 'cta': cta, 'summary': esc(summary),
        'tagchips': tag_chips, 'scenchips': scen_chips, 'tutorial': tutorial_html,
        'peers': peer_html, 'sitemap': esc(base + '/sitemap.xml'),
    }


def main():
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    os.makedirs(OUT_DIR, exist_ok=True)
    n = 0
    for a in data.get('agents', []):
        aid = a.get('id') or re.sub(r'[^a-z0-9]+', '-', str(a.get('name', 'tool')).lower()).strip('-')
        html_text = build_html(a, data)
        with open(os.path.join(OUT_DIR, aid + '.html'), 'w', encoding='utf-8') as out:
            out.write(html_text)
        n += 1
    print('已生成 %d 个工具详情页 -> tools/' % n)


if __name__ == '__main__':
    main()
