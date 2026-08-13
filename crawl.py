#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crawl.py —— AgentHub 全自动数据采集 + AI 清洗 + 数据更新脚本
=====================================================================
运行环境：Python 3.8+，仅依赖 requests / beautifulsoup4（见 requirements.txt，全部免费开源）
设计目标：零付费、零后端、零服务器，配合 GitHub Actions 每日定时运行（电脑关机也照常执行）。

整体流程（与 .github/workflows/daily-crawl.yml 一一对应）：
  步骤1  多源采集（无反爬风险、优先官方/免费源，全部失败优雅跳过）
          · GitHub Trending 每日热门（读公开 HTML，GitHub 无官方 Trending API，故抓取页面）
          · Hugging Face Spaces API（免费 JSON 接口，筛选 agent 相关空间）
          · ProductHunt RSS（官方 Atom Feed，无需密钥，抓取 AI 工具上新）
          · Firecrawl 免费抓取接口（可选，每月 1000 次免费额度，需 FIRECRAWL_API_KEY）
  步骤2  AI 自动处理（智谱 GLM-4-Flash 免费大模型，需 ZHIPU_API_KEY）
          · 英文简介翻译 + 按「分类定位」差异化生成中文一句话简介
          · 自动分类到 4 大板块 + 配套辅助
          · 自动打标签（开源免费 / 云端付费 / 本地离线）
          · 智谱免费 AI 违规检测：过滤翻墙 / VPN / 代理 / 破解 / 色情 / 赌博 / 黑产等灰色内容
          · 自动去重（对比现有 data/agent-list.json，只新增未收录）
          · 自动校验链接有效性（失效标记 status=dead，前端自动灰色置灰）
  步骤2.6 每日链接复检：旋转批次复检既有 active 工具，404 自动降级为 dead（仅改 status，不重写）
  步骤3  增量追加写入 data/agent-list.json（仅 append 新工具，完整保留既有记录），并追加更新日志 changelog
  步骤4  （部署由 GitHub Actions 完成：重新生成 sitemap + 提交 + Cloudflare 自动上线）
  步骤5  输出 crawl-YYYYMMDD.log 执行日志（Workflow 上传为 artifact 留存 30 天，供每月人工核查）

───────────────────────── 免费密钥填写位置 ─────────────────────────
以下三项通过环境变量注入（GitHub：仓库 Settings → Secrets and variables → Actions）：
  ZHIPU_API_KEY      智谱开放平台 https://open.bigmodel.cn 注册，开通 GLM-4-Flash（永久免费），复制 API Key
  FIRECRAWL_API_KEY  Firecrawl https://www.firecrawl.dev 注册，免费版每月 1000 次；不填则跳过该数据源
  CRAWL_TARGET_URL   可选，Firecrawl 要抓取的“海外 AI 工具社区上新页”URL（如 https://theresanaiforthat.com）
本地调试：export 同样的环境变量后直接 `python crawl.py` 即可。
"""

import os
import sys
import json
import time
import datetime
import re
import logging

import requests
from bs4 import BeautifulSoup

# ------------------------- 基础配置 -------------------------
# 数据文件绝对路径（与脚本同目录下的 data/agent-list.json）
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'agent-list.json')

# 浏览器标识：采集公开页面时使用，避免被简单 UA 拦截
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
REQUEST_TIMEOUT = 12

# 每日最多新增数量（控制免费模型额度消耗，也避免列表无限膨胀）
MAX_NEW_PER_RUN = 20
# 两次 GLM 调用之间的间隔（秒），避免触发免费模型限流（智谱免费版有 RPM 限制）
AI_CALL_INTERVAL = 1.5

# 每日链接复检：对「既有 active 工具」按 (index % CYCLE) == (年内第几天 % CYCLE) 旋转抽样，
# 单日最多复检 LINK_RECHECK_BATCH 个，约等于每 CYCLE 天把全量轮一遍，控制请求量。
LINK_RECHECK_CYCLE_DAYS = 7
LINK_RECHECK_BATCH = 12

# 分类与类型白名单（必须与此站 categories[].id、前端 TYPE_LABEL 完全一致）
VALID_CATEGORIES = ['local', 'browser', 'workflow', 'crossborder', 'tools']
VALID_TYPES = ['open-source', 'cloud-paid', 'local-offline']

# Agent 相关关键词（筛选采集源里的“真·Agent 工具”，过滤无关仓库/项目）
AGENT_KEYWORDS = [
    'agent', 'autonomous', 'automation', 'automate', 'llm-agent', 'multi-agent',
    'browser-use', 'web agent', 'rpa', 'workflow', 'orchestration', '智能体',
    '自动化', '自主', 'agentic', 'copilot', 'task automation'
]

# 违规/灰色内容关键词（命中即预筛拦截，无需消耗 AI 额度）
VIOLATION_KEYWORDS = [
    'vpn', '翻墙', '科学上网', '翻墙代理', '机场节点', '破解', 'crack', '破解版',
    '注册机', 'keygen', '激活码', '盗版', '色情', '成人', '赌博', '博彩', '私彩',
    'bc.game', '彩票', '刷单', '刷量', '刷评', '灰产', '黑产', '黑客攻击', 'ddos',
    '网赚', '资金盘', '杀猪盘', 'hack tool', 'exploit kit', '钓鱼', '引流欺诈'
]

# ------------------------- 日志 -------------------------
def beijing_date():
    """返回北京时间（UTC+8）日期，保证 added / changelog 用北京时间而非 runner 的 UTC。"""
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).date()

def setup_logger():
    """日志同时输出到控制台（GitHub Actions 可见）与 crawl-YYYYMMDD.log（留存 artifact）。"""
    today = beijing_date()
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'crawl-%s.log' % today.isoformat())
    logger = logging.getLogger('agenthub')
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', '%Y-%m-%d %H:%M:%S')
    fh = logging.FileHandler(log_path, encoding='utf-8')
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger

LOG = setup_logger()

# ------------------------- 步骤1：数据采集 -------------------------
def fetch_github_trending():
    """抓取 GitHub Trending 每日榜单，筛选 Agent/自动化 相关仓库。
    说明：GitHub 官方不提供 Trending API，这里只读公开 HTML 页面（合规、无登录、无反爬风险）。
    """
    url = 'https://github.com/trending?since=daily'
    out = []
    try:
        r = requests.get(url, headers={'User-Agent': UA}, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        for art in soup.select('article.Box-row'):
            a = art.select_one('h2 a')
            if not a:
                continue
            full = a.get('href', '').strip('/')
            repo_url = 'https://github.com/' + full
            desc = art.select_one('p')
            desc_text = desc.get_text(strip=True) if desc else ''
            name = full.split('/')[-1]
            if _match_agent(name + ' ' + desc_text):
                out.append({'name': name, 'url': repo_url, 'raw_desc': desc_text, 'source': 'github-trending'})
        LOG.info('[采集] GitHub Trending 命中 %d 个候选', len(out))
    except Exception as e:
        LOG.warning('[采集] GitHub Trending 失败：%s', e)
    return out

def fetch_huggingface():
    """读取 Hugging Face Spaces 公开 API，筛选近期更新、含 agent 关键词的空间。
    免费、无需密钥；返回按最近修改排序的 spaces 列表（JSON）。
    增强：先取 lastModified 排序的 60 个，再按关键词过滤，保证新鲜度与相关性。
    """
    url = 'https://huggingface.co/api/spaces?sort=lastModified&direction=-1&limit=60'
    out = []
    try:
        r = requests.get(url, headers={'User-Agent': UA}, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        items = r.json()
        for it in items:
            sid = it.get('id', '')
            desc = (it.get('description') or '')
            if _match_agent(sid + ' ' + desc):
                out.append({
                    'name': sid.split('/')[-1],
                    'url': 'https://huggingface.co/spaces/' + sid,
                    'raw_desc': desc,
                    'source': 'huggingface'
                })
        LOG.info('[采集] Hugging Face 命中 %d 个候选', len(out))
    except Exception as e:
        LOG.warning('[采集] Hugging Face 失败：%s', e)
    return out

def fetch_producthunt():
    """抓取 ProductHunt 官方 Atom RSS（无需密钥），发现最新 AI Agent 工具。
    说明：RSS 的 <link> 指向 ProductHunt 帖子页；本函数会优先从简介 HTML 中
    抽取工具的真实官网链接，抽不到则回退到 PH 帖子页（仍为可访问链接）。
    """
    url = 'https://www.producthunt.com/feed'
    out = []
    try:
        r = requests.get(url, headers={'User-Agent': UA}, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        for e in soup.select('entry'):
            title = e.select_one('title')
            name = title.get_text(strip=True) if title else ''
            link = e.select_one('link')
            ph_url = link.get('href') if link else ''
            summary = e.select_one('summary') or e.select_one('content')
            raw = summary.get_text(strip=True) if summary else ''
            # 从简介 HTML 里抽取第一个非 producthunt 的外链作为真实官网
            m = re.search(r'href="(https?://(?!www\.producthunt\.com)[^"]+)"', raw)
            real = m.group(1) if m else ''
            final_url = real or ph_url
            if name and _match_agent(name + ' ' + raw):
                out.append({'name': name, 'url': final_url, 'raw_desc': raw, 'source': 'producthunt'})
        LOG.info('[采集] ProductHunt RSS 命中 %d 个候选', len(out))
    except Exception as e:
        LOG.warning('[采集] ProductHunt RSS 失败：%s', e)
    return out

def fetch_firecrawl(target_url):
    """使用 Firecrawl 免费抓取接口采集“海外 AI 工具社区上新页”。
    需 FIRECRAWL_API_KEY（免费 1000 次/月）。无 key 或失败则跳过。
    抓回的 markdown 交由 ai_extract_from_text() 用 AI 提取结构化工具列表。
    """
    key = os.environ.get('FIRECRAWL_API_KEY', '').strip()
    if not key:
        LOG.info('[采集] 未配置 FIRECRAWL_API_KEY，跳过 Firecrawl 数据源')
        return []
    if not target_url:
        LOG.info('[采集] 未配置 CRAWL_TARGET_URL，跳过 Firecrawl 数据源')
        return []
    try:
        r = requests.post(
            'https://api.firecrawl.dev/v1/scrape',
            headers={'Authorization': 'Bearer %s' % key, 'Content-Type': 'application/json'},
            json={'url': target_url, 'formats': ['markdown'], 'onlyMainContent': True},
            timeout=30
        )
        r.raise_for_status()
        md = r.json().get('data', {}).get('markdown', '')
        LOG.info('[采集] Firecrawl 抓取 %s，获得 %d 字符', target_url, len(md))
        return ai_extract_from_text(md) if md else []
    except Exception as e:
        LOG.warning('[采集] Firecrawl 失败：%s', e)
        return []

def _match_agent(text):
    """判断文本是否涉及 Agent / 自动化（大小写不敏感）。"""
    t = text.lower()
    return any(k in t for k in AGENT_KEYWORDS)

# ------------------------- 步骤2：AI 处理（智谱 GLM-4-Flash） -------------------------
def call_glm(system_prompt, user_prompt):
    """调用智谱 GLM-4-Flash（OpenAI 兼容接口）。无 key 时返回 None（由调用方回退到启发式）。"""
    key = os.environ.get('ZHIPU_API_KEY', '').strip()
    if not key:
        return None
    try:
        r = requests.post(
            'https://open.bigmodel.cn/api/paas/v4/chat/completions',
            headers={'Authorization': 'Bearer %s' % key, 'Content-Type': 'application/json'},
            json={
                'model': 'glm-4-flash',      # 智谱永久免费模型
                'temperature': 0.3,
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt}
                ]
            },
            timeout=40
        )
        r.raise_for_status()
        content = r.json()['choices'][0]['message']['content']
        return _extract_json(content)
    except Exception as e:
        LOG.warning('[AI] GLM 调用失败：%s', e)
        return None

def _extract_json(text):
    """从模型返回中稳妥提取第一个 JSON 对象或数组（兼容模型夹带的说明文字）。"""
    if not text:
        return None
    s, e = text.find('{'), text.rfind('}')
    if s != -1 and e != -1 and e > s:
        try:
            return json.loads(text[s:e + 1])
        except Exception:
            pass
    s, e = text.find('['), text.rfind(']')
    if s != -1 and e != -1 and e > s:
        try:
            return json.loads(text[s:e + 1])
        except Exception:
            pass
    return None

# 各分类的「差异化文案风格」提示：让 GLM 生成贴合分类定位的一句话简介
CATEGORY_HINTS = {
    'local':       '本地开源框架：强调可本地/私有化部署、数据不出域、自托管、适合开发者二次开发',
    'browser':     '浏览器网页Agent：强调自动操作网页、自动填表、数据采集、RPA、无需写代码',
    'workflow':    '低代码工作流Agent：强调可视化拖拽编排、连接 SaaS、自动化重复业务流程',
    'crossborder': '跨境商用Agent：强调面向跨境电商/外贸、Listing 生成、邮件自动回复、多语言客服',
    'tools':       '配套辅助：强调为 Agent 提供模型/算力/数据/托管等基础设施支撑',
}
_HINT_TEXT = '；'.join('%s（%s）' % (k, v) for k, v in CATEGORY_HINTS.items())

# 单工具清洗的系统提示词（限定 JSON schema，避免模型自由发挥；内置差异化文案 + 违规检测）
ENRICH_SYSTEM = (
    '你是 AgentHub 导航站的 AI 编辑。根据工具名称与简介，严格只输出一个 JSON 对象，字段：'
    'category(必填, 取值 local/browser/workflow/crossborder/tools)、'
    'type(必填, 取值 open-source/cloud-paid/local-offline)、'
    'summary_cn(必填, 一句中文简介, 不超过 40 字, 必须体现该分类的定位风格)、'
    'tags(中文标签数组, 2-4 个)、scenarios(适用场景数组, 1-3 个)、'
    'is_violation(布尔, 若工具涉及翻墙/VPN/代理/破解/注册机/色情/赌博/黑产等灰色违规内容则为 true)。'
    '不要任何解释文字。'
    '【各分类简介文案风格】' + _HINT_TEXT
)

def enrich_tool(cand):
    """对单个候选做 AI 翻译/分类/打标签/违规检测；无 key 时回退到关键词启发式（仅调试用）。"""
    name = cand['name']
    raw = cand.get('raw_desc', '')
    ai = call_glm(ENRICH_SYSTEM, '工具名称：%s\n原始简介：%s\n来源：%s' % (name, raw, cand.get('source', '')))
    if ai:
        cat = ai.get('category')
        if cat not in VALID_CATEGORIES:
            cat = 'tools'
        typ = ai.get('type')
        if typ not in VALID_TYPES:
            typ = 'cloud-paid'
        return {
            'name': name,
            'category': cat,
            'type': typ,
            'summary': (ai.get('summary_cn') or raw)[:60],
            'tags': (ai.get('tags') or [])[:4],
            'scenarios': (ai.get('scenarios') or [])[:3],
            'url': cand['url'],
            'status': 'active',
            'is_violation': bool(ai.get('is_violation'))   # 智谱免费 AI 违规检测结论
        }
    # —— 无 AI key 的本地回退（不会翻译，仅用于没有密钥时跑通流程）——
    h = heuristic_enrich(cand)
    h['is_violation'] = False
    return h

def heuristic_enrich(cand):
    """无 AI key 时的关键词启发式分类（兜底，不翻译）。"""
    name = cand['name']
    raw = (cand.get('raw_desc', '') + ' ' + cand['url']).lower()
    if any(k in raw for k in ['local', 'offline', 'self-host', '本地', '离线', '开源框架', 'framework']):
        cat = 'local'
    elif any(k in raw for k in ['browser', '网页', '填表', '采集', 'scrape', '插件', 'rpa']):
        cat = 'browser'
    elif any(k in raw for k in ['workflow', '流程', '集成', '调度', 'zapier', 'n8n', 'automation']):
        cat = 'workflow'
    elif any(k in raw for k in ['跨境', '电商', 'tiktok', '亚马逊', '外贸', '出海', 'cross-border']):
        cat = 'crossborder'
    else:
        cat = 'tools'
    typ = 'open-source' if ('github.com' in cand['url'] or 'open-source' in raw or '开源' in raw) \
        else ('local-offline' if ('offline' in raw or '本地' in raw) else 'cloud-paid')
    return {
        'name': name, 'category': cat, 'type': typ,
        'summary': cand.get('raw_desc', '')[:60], 'tags': [], 'scenarios': [],
        'url': cand['url'], 'status': 'active'
    }

# Firecrawl 抓回网页文本 → AI 提取结构化候选
EXTRACT_SYSTEM = (
    '你是 AgentHub 导航站的采集编辑。从下面的网页内容中提取“新出现的 AI Agent / 自动化智能体工具”，'
    '每个工具输出 JSON 对象 {name, url, raw_desc}。最多提取 15 个，返回 JSON 数组。只输出 JSON 数组。'
)
def ai_extract_from_text(text):
    """把 Firecrawl 抓回的网页文本交给 AI 提取结构化候选工具。"""
    if not text.strip():
        return []
    ai = call_glm(EXTRACT_SYSTEM, text[:6000])
    if isinstance(ai, list):
        out = []
        for it in ai[:15]:
            if isinstance(it, dict) and it.get('url') and it.get('name'):
                out.append({'name': it['name'], 'url': it['url'],
                            'raw_desc': it.get('raw_desc', ''), 'source': 'firecrawl'})
        LOG.info('[AI] Firecrawl 文本提取到 %d 个候选', len(out))
        return out
    return []

# ------------------------- 步骤2.3：违规/灰色内容过滤 -------------------------
def keyword_violation(cand):
    """违规关键词预筛（无需 AI 调用）。命中翻墙/VPN/破解/色情/赌博/黑产等即拦截。返回 (是否违规, 理由)。"""
    text = ' '.join([str(cand.get('name', '')), str(cand.get('raw_desc', '')), str(cand.get('url', ''))]).lower()
    for k in VIOLATION_KEYWORDS:
        if k in text:
            return True, '命中违规关键词「%s」' % k
    return False, ''

# ------------------------- 步骤2.4/2.5：去重 + 链接校验 -------------------------
def normalize_url(u):
    return (u or '').strip().lower().rstrip('/')

def normalize_name(n):
    return re.sub(r'\s+', '', (n or '').lower())

def is_duplicate(cand, existing):
    """按 URL（归一化）或 名称（去空格小写）判断是否已收录。"""
    nu, nn = normalize_url(cand.get('url', '')), normalize_name(cand.get('name', ''))
    for a in existing:
        if normalize_url(a.get('url', '')) == nu:
            return True
        if normalize_name(a.get('name', '')) == nn:
            return True
    return False

def check_link(url):
    """校验链接有效性；异常或 HTTP>=400 视为失效。返回布尔。"""
    try:
        r = requests.get(url, headers={'User-Agent': UA}, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        return r.status_code < 400
    except Exception:
        return False

# ------------------------- 步骤2.6：每日旋转复检既有链接 -------------------------
def revalidate_existing(data):
    """按日旋转批次复检「既有 active 工具」的链接有效性，404 自动降级为 dead。
    仅修改 status 字段，不重写任何既有记录；返回本次新标记的失效数量。
    """
    active = [a for a in data['agents'] if a.get('status', 'active') == 'active']
    if not active:
        return 0
    doy = beijing_date().timetuple().tm_yday
    rotation = doy % LINK_RECHECK_CYCLE_DAYS
    batch = [a for i, a in enumerate(active) if (i % LINK_RECHECK_CYCLE_DAYS) == rotation]
    batch = batch[:LINK_RECHECK_BATCH]
    changed = 0
    for a in batch:
        if not check_link(a.get('url', '')):
            a['status'] = 'dead'
            changed += 1
            LOG.info('[失效] 链接不可达，标记 dead：%s (%s)', a.get('name'), a.get('url'))
    if batch:
        LOG.info('[复检] 当日抽样复检 %d 个活跃工具，新标记失效 %d 个', len(batch), changed)
    return changed

# ------------------------- 步骤3：增量合并写入 -------------------------
def load_data():
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_data(data):
    """写入数据文件。调用方保证只追加新工具 / 仅改 status，不重写既有条目。"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def slugify(name):
    """由工具名生成稳定 id（用于前端锚点与去重）。"""
    s = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return s or ('tool-' + beijing_date().isoformat())

# ------------------------- 主流程 -------------------------
def main():
    LOG.info('========== AgentHub 每日自动爬取开始 ==========')
    data = load_data()
    existing = data['agents']
    LOG.info('[数据] 现有工具 %d 个（沿用既有数据，增量追加，不重写）', len(existing))

    # —— 步骤1：多源采集（GitHub Trending + Hugging Face + ProductHunt RSS + Firecrawl）——
    candidates = []
    candidates += fetch_github_trending()
    candidates += fetch_huggingface()
    candidates += fetch_producthunt()
    candidates += fetch_firecrawl(os.environ.get('CRAWL_TARGET_URL', '').strip())
    LOG.info('[采集] 合计候选 %d 个', len(candidates))

    # —— 步骤2.4：去重（剔除已收录）——
    candidates = [c for c in candidates if not is_duplicate(c, existing)]
    LOG.info('[去重] 过滤后待处理候选 %d 个', len(candidates))

    # —— 步骤2：逐个违规预筛 + AI 清洗 + 违规检测 + 链接校验 ——
    added = []
    for c in candidates[:MAX_NEW_PER_RUN]:
        # 违规关键词预筛（无 API 调用，先挡掉明显灰色内容）
        viol, reason = keyword_violation(c)
        if viol:
            LOG.info('[违规过滤] %s：%s', c.get('name'), reason)
            continue
        try:
            tool = enrich_tool(c)
            time.sleep(AI_CALL_INTERVAL)          # 礼貌间隔，避免触发限流
            # 智谱 AI 违规检测结论（翻墙 / 灰产等）
            if tool.get('is_violation'):
                LOG.info('[违规过滤] %s：AI 判定为灰色/翻墙/违规内容，已跳过', tool['name'])
                continue
            alive = check_link(tool['url'])
            tool['status'] = 'active' if alive else 'dead'   # 步骤2.5：失效标记
            tool['id'] = slugify(tool['name'])
            tool['added'] = beijing_date().isoformat()
            # 二次去重（AI 可能把名称规范化后撞车）
            if is_duplicate(tool, existing + added):
                LOG.info('[跳过] 重复：%s', tool['name'])
                continue
            added.append(tool)
            LOG.info('[新增] %s | %s/%s | status=%s', tool['name'], tool['category'], tool['type'], tool['status'])
        except Exception as e:
            LOG.warning('[错误] 处理候选 %s 失败：%s', c.get('name'), e)

    # —— 步骤2.6：每日旋转复检既有链接，404 标记 dead（仅改 status）——
    reval_changed = revalidate_existing(data)

    if not added and reval_changed == 0:
        LOG.info('========== 本次无新增、无失效变更，结束 ==========')
        return

    # —— 步骤3：增量追加写入 JSON（仅 append 新工具，不动既有条目）+ 追加更新日志 ——
    if added:
        data['agents'].extend(added)
    note = ('每日自动爬取：GitHub Trending + Hugging Face + ProductHunt RSS + Firecrawl 多源采集；'
            '智谱 GLM-4-Flash 分类 + 4 大分类差异化文案生成 + 灰色/翻墙内容 AI 违规检测；'
            '新增工具链接校验 + 每日旋转复检失效标记。')
    data.setdefault('changelog', []).append({
        'date': beijing_date().isoformat(),
        'added': [a['name'] for a in added],
        'invalidated': reval_changed,
        'note': note
    })
    save_data(data)
    LOG.info('========== 增量写入：新增 %d 个工具（失效标记 %d 处），结束 ==========',
             len(added), reval_changed)

if __name__ == '__main__':
    main()
