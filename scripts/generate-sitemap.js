#!/usr/bin/env node
/* ==========================================================================
 * generate-sitemap.js —— 站点地图生成器（零依赖，仅用 Node 内置模块）
 *
 * 作用：读取 data/agent-list.json，生成两份产物：
 *   1) sitemap.xml  —— 标准 XML 站点地图，供百度/谷歌等搜索引擎抓取
 *   2) sitemap.json —— 人类可读的站点索引“模板”，方便人工核对与二次处理
 *
 * 用法：
 *   node scripts/generate-sitemap.js
 * 也可以把它设为 Cloudflare Pages 的“构建命令”：
 *   构建命令： node scripts/generate-sitemap.js
 *   输出目录： /
 * 这样每次推送都会根据最新 JSON 重新生成 sitemap。
 * ======================================================================== */

const fs = require('fs');
const path = require('path');

// 仓库根目录（scripts/ 的上一级）
const ROOT = path.resolve(__dirname, '..');
const DATA_FILE = path.join(ROOT, 'data', 'agent-list.json');
const OUT_XML = path.join(ROOT, 'sitemap.xml');
const OUT_JSON = path.join(ROOT, 'sitemap.json');

// 站点内所有静态页面（工具本身以卡片形式存在于分类页，不单独生成 URL）
const STATIC_PAGES = [
  { file: 'index.html', priority: '1.0', changefreq: 'daily' },
  { file: 'local.html', priority: '0.9', changefreq: 'daily' },
  { file: 'browser.html', priority: '0.9', changefreq: 'daily' },
  { file: 'workflow.html', priority: '0.9', changefreq: 'daily' },
  { file: 'crossborder.html', priority: '0.9', changefreq: 'daily' },
  { file: 'tools.html', priority: '0.8', changefreq: 'weekly' },
  { file: 'update.html', priority: '0.6', changefreq: 'weekly' }
];

function loadData() {
  const raw = fs.readFileSync(DATA_FILE, 'utf8');
  return JSON.parse(raw);
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

// XML 转义
function xmlEscape(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function build() {
  const data = loadData();
  const base = (data.site && data.site.baseUrl || 'https://aiagent.72tool.com').replace(/\/$/, '');
  const lastmodDefault = today();
  // 取最新一条 changelog 日期作为内容更新时间（没有则用今天）
  const logDates = (data.changelog || []).map(x => x.date).filter(Boolean).sort().reverse();
  const contentLastmod = logDates[0] || lastmodDefault;

  // ---- 1) sitemap.xml ----
  const urls = STATIC_PAGES.map(p => {
    const isHome = p.file === 'index.html';
    const loc = isHome ? base + '/' : base + '/' + p.file;
    const lm = isHome ? contentLastmod : contentLastmod; // 全站随内容更新
    return `  <url>
    <loc>${xmlEscape(loc)}</loc>
    <lastmod>${xmlEscape(lm)}</lastmod>
    <changefreq>${p.changefreq}</changefreq>
    <priority>${p.priority}</priority>
  </url>`;
  }).join('\n');

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls}
</urlset>
`;
  fs.writeFileSync(OUT_XML, xml, 'utf8');

  // ---- 2) sitemap.json（可读模板，含分类与工具计数） ----
  const cats = (data.categories || []).map(c => ({
    id: c.id,
    name: c.name,
    url: base + '/' + c.id + '.html'
  }));
  const json = {
    generatedAt: new Date().toISOString(),
    baseUrl: base,
    pages: STATIC_PAGES.map(p => ({
      url: p.file === 'index.html' ? base + '/' : base + '/' + p.file,
      priority: p.priority,
      changefreq: p.changefreq
    })),
    categories: cats,
    stats: {
      totalAgents: (data.agents || []).length,
      byCategory: (data.categories || []).map(c => ({
        id: c.id,
        name: c.name,
        count: (data.agents || []).filter(a => a.category === c.id).length
      })),
      deadLinks: (data.agents || []).filter(a => a.status === 'dead').length
    }
  };
  fs.writeFileSync(OUT_JSON, JSON.stringify(json, null, 2), 'utf8');

  console.log('✅ sitemap.xml 生成成功：' + STATIC_PAGES.length + ' 个页面');
  console.log('✅ sitemap.json 生成成功：共 ' + json.stats.totalAgents + ' 个工具，失效 ' + json.stats.deadLinks + ' 个');
}

try {
  build();
} catch (e) {
  console.error('❌ 生成失败：', e.message);
  process.exit(1);
}
