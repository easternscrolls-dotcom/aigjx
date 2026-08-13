/* ==========================================================================
   AgentHub 前端逻辑 —— 零依赖原生 JavaScript
   职责：
     1) 主题切换（浅色/深色，localStorage 记忆，跟随系统）
     2) 分类导航高亮当前页 + 自动填充各分类工具数量
     3) 根据当前页面 data-category 渲染工具卡片网格
     4) 全局搜索（顶栏与 Hero 两处输入框实时同步）
     5) 失效链接自动灰色置灰 + “隐藏失效”开关
     6) 首页分类快捷卡片 / 更新日志页面渲染
   数据安全：所有文本经 esc() 转义，防止 XSS。
   ========================================================================== */
(function () {
  'use strict';

  // 数据源路径（相对当前页面；所有页面都在根目录，故统一为 data/agent-list.json）
  var JSON_URL = 'data/agent-list.json';

  // 三类标签：开源免费 / 云端付费 / 本地离线
  var TYPE_LABEL = {
    'open-source': '开源免费',
    'cloud-paid': '云端付费',
    'local-offline': '本地离线'
  };
  var TYPE_CLASS = {
    'open-source': 'tag-open-source',
    'cloud-paid': 'tag-cloud-paid',
    'local-offline': 'tag-local-offline'
  };

  /* 部署难度 & 国内可访问性：根据既有字段(type/category/url/tags)纯前端推导，
     不新增 JSON 字段、不改动 data/agent-list.json 数据结构。 */
  var DIFF_MAP = {
    'cloud-paid':    { label: '免部署',   level: 'easy' },
    'open-source':   { label: '需部署',   level: 'medium' },
    'local-offline': { label: '本地部署', level: 'hard' }
  };
  // 国内可访问性启发式：域名白/黑名单 + 类型兜底（仅作参考，非实时探测）
  var ACCESS_OK   = ['github.com', 'gitee.com', 'gitcode.com'];
  var ACCESS_WARN = ['huggingface.co', 'hf.space', 'openai.com', 'anthropic.com',
                     'google.com', 'google.dev', 'vercel.app', 'replit.app', 'firecrawl.dev'];
  function hostOf(url) {
    try { return new URL(url).hostname.replace(/^www\./, ''); } catch (e) { return ''; }
  }
  function diffInfo(a) { return DIFF_MAP[a.type] || { label: '—', level: 'medium' }; }
  function accessInfo(a) {
    var h = hostOf(a.url);
    var ok = ACCESS_OK.some(function (d) { return h === d || h.endsWith('.' + d); });
    if (ok) return { label: '国内可访问', cls: 'acc-ok' };
    var warn = ACCESS_WARN.some(function (d) { return h === d || h.endsWith('.' + d); });
    if (warn) return { label: '可能受限', cls: 'acc-warn' };
    if (a.type === 'local-offline') return { label: '本地可用', cls: 'acc-ok' };
    if (a.type === 'open-source')   return { label: '自部署可控', cls: 'acc-unknown' };
    return { label: '视网络环境', cls: 'acc-unknown' };
  }

  // 全局状态：data 为 JSON，category 来自 <body data-category>，query 为搜索词
  var state = {
    data: null,
    category: document.body.getAttribute('data-category') || 'all',
    query: '',
    filters: new Set()   // 多条件筛选：open-source / local-offline / crossborder / no-code
  };

  /* ---------- 1. 主题切换 ---------- */
  function initTheme() {
    var saved = localStorage.getItem('ah-theme');
    var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    if (saved === 'dark' || (!saved && prefersDark)) {
      document.documentElement.setAttribute('data-theme', 'dark');
    }
    var btn = document.getElementById('themeToggle');
    if (!btn) return;
    btn.textContent = document.documentElement.getAttribute('data-theme') === 'dark' ? '☀️' : '🌙';
    btn.addEventListener('click', function () {
      var cur = document.documentElement.getAttribute('data-theme');
      var next = cur === 'dark' ? 'light' : 'dark';
      if (next === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
      else document.documentElement.removeAttribute('data-theme');
      localStorage.setItem('ah-theme', next);
      btn.textContent = next === 'dark' ? '☀️' : '🌙';
    });
  }

  /* ---------- 2. 分类导航：高亮 + 计数 ---------- */
  function initNav() {
    if (!state.data) return;
    var counts = {};
    state.data.agents.forEach(function (a) { counts[a.category] = (counts[a.category] || 0) + 1; });
    var links = document.querySelectorAll('.cat-link');
    Array.prototype.forEach.call(links, function (link) {
      var cat = link.getAttribute('data-cat');
      if (cat === state.category) link.classList.add('active');
      var badge = link.querySelector('.badge');
      if (badge && counts[cat] != null) badge.textContent = counts[cat];
    });
    // 侧边栏计数（.count[data-count]）
    var sideCounts = document.querySelectorAll('.count[data-count]');
    Array.prototype.forEach.call(sideCounts, function (el) {
      var cat = el.getAttribute('data-count');
      if (counts[cat] != null) el.textContent = counts[cat];
      // 侧边栏高亮当前分类
      var parent = el.closest('a');
      if (parent && cat === state.category) parent.classList.add('active');
    });
  }

  /* ---------- 3. 单张卡片 HTML ---------- */
  function cardHTML(a) {
    var dead = a.status === 'dead';
    var typeLabel = TYPE_LABEL[a.type] || a.type;
    var typeClass = TYPE_CLASS[a.type] || '';
    var tags = (a.tags || []).map(function (t) { return '<span class="chip">' + esc(t) + '</span>'; }).join('');
    var scen = (a.scenarios || []).map(function (s) { return '<span class="chip scenario">' + esc(s) + '</span>'; }).join('');
    var di = diffInfo(a);          // 部署难度（按 type 推导）
    var ai = accessInfo(a);        // 国内可访问性（按域名+type 推导）
    var btn = dead
      ? '<span class="btn-go">链接失效</span>'
      : '<a class="btn-go" href="' + esc(a.url) + '" target="_blank" rel="noopener noreferrer">直达官网 ↗</a>';
    return '' +
      '<article class="card' + (dead ? ' dead' : '') + '">' +
        '<div class="head">' +
          '<span class="name">' + esc(a.name) + '</span>' +
          '<span class="tag-type ' + typeClass + '">' + typeLabel + '</span>' +
        '</div>' +
        '<p class="summary">' + esc(a.summary || '') + '</p>' +
        '<div class="card-meta">' +
          '<span class="badge-meta diff-' + di.level + '">部署：' + di.label + '</span>' +
          '<span class="badge-meta ' + ai.cls + '">国内：' + ai.label + '</span>' +
        '</div>' +
        '<div class="tags">' + tags + scen + '</div>' +
        '<div class="foot">' +
          (dead ? '<span class="dead-flag">已失效</span>' : '') +
          btn +
        '</div>' +
      '</article>';
  }

  /* ---------- 4. 渲染主网格（按分类 + 搜索过滤） ---------- */
  function renderGrid() {
    var wrap = document.getElementById('agentGrid');
    if (!wrap) return;
    var list = state.data.agents;
    // 非首页/非全部：仅显示当前分类
    if (state.category !== 'all' && state.category !== 'home') {
      list = list.filter(function (a) { return a.category === state.category; });
    }
    // 搜索过滤：名称 + 简介 + 标签 + 场景
    var q = state.query.trim().toLowerCase();
    if (q) {
      list = list.filter(function (a) {
        var hay = (a.name + ' ' + (a.summary || '') + ' ' + (a.tags || []).join(' ') + ' ' + (a.scenarios || []).join(' ')).toLowerCase();
        return hay.indexOf(q) !== -1;
      });
    }
    // “隐藏失效”开关：勾选则剔除失效链接（前端过滤，不动数据）
    if (getHideDead()) {
      list = list.filter(function (a) { return a.status !== 'dead'; });
    }
    // 多条件筛选（免费开源/本地离线/跨境专用/零代码，OR 逻辑取并集）
    list = list.filter(passFilters);
    var countEl = document.getElementById('resultCount');
    if (countEl) countEl.textContent = list.length + ' 个工具';
    if (!list.length) {
      wrap.innerHTML = '<div class="empty">没有匹配的工具，换个关键词试试～</div>';
      return;
    }
    wrap.innerHTML = list.map(cardHTML).join('');
  }

  /* ---------- 5. 搜索（多输入框同步） ---------- */
  function initSearch() {
    var inputs = document.querySelectorAll('input[data-search]');
    Array.prototype.forEach.call(inputs, function (inp) {
      inp.addEventListener('input', function (e) {
        state.query = e.target.value;
        Array.prototype.forEach.call(inputs, function (o) {
          if (o !== e.target) o.value = e.target.value;
        });
        renderGrid();
      });
    });
  }

  /* ---------- 6. 失效链接隐藏开关 ---------- */
  function initDeadToggle() {
    var t = document.getElementById('hideDead');
    if (!t) return;
    t.addEventListener('change', function () {
      document.body.classList.toggle('hide-dead', t.checked);
    });
  }

  /* ---------- 6b. 移动端汉堡菜单 ---------- */
  function initNavToggle() {
    var btn = document.getElementById('navToggle');
    var menu = document.getElementById('catNavLinks');
    if (!btn || !menu) return;
    btn.addEventListener('click', function () {
      var open = menu.classList.toggle('open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }

  /* ---------- 6c. 多条件筛选（纯前端，不改动 JSON 结构） ---------- */
  function getHideDead() {
    var t = document.getElementById('hideDead');
    return !!(t && t.checked);
  }
  function isNoCode(a) {
    if (a.category === 'workflow') return true;
    var blob = ((a.tags || []).concat(a.scenarios || []).concat([a.summary || ''])).join(' ');
    return /(no-?code|low-?code|零代码|无代码|拖拽|可视化)/i.test(blob);
  }
  // 任一选中条件满足即保留（OR 并集），未选任何条件则全部通过
  function passFilters(a) {
    if (state.filters.size === 0) return true;
    if (state.filters.has('open-source') && a.type === 'open-source') return true;
    if (state.filters.has('local-offline') && a.type === 'local-offline') return true;
    if (state.filters.has('crossborder') && a.category === 'crossborder') return true;
    if (state.filters.has('no-code') && isNoCode(a)) return true;
    return false;
  }
  function initFilters() {
    var bar = document.getElementById('filters');
    if (!bar) return;
    var chips = bar.querySelectorAll('.chip.filter');
    Array.prototype.forEach.call(chips, function (ch) {
      ch.addEventListener('click', function () {
        var f = ch.getAttribute('data-filter');
        if (state.filters.has(f)) { state.filters.delete(f); ch.classList.remove('active'); }
        else { state.filters.add(f); ch.classList.add('active'); }
        renderGrid();
      });
    });
    var reset = document.getElementById('filterReset');
    if (reset) reset.addEventListener('click', function () {
      state.filters.clear();
      Array.prototype.forEach.call(chips, function (ch) { ch.classList.remove('active'); });
      renderGrid();
    });
  }

  /* ---------- 7. 首页分类快捷卡片 ---------- */
  function renderCatCards() {
    var wrap = document.getElementById('catCards');
    if (!wrap || !state.data) return;
    wrap.innerHTML = state.data.categories.map(function (c) {
      var n = state.data.agents.filter(function (a) { return a.category === c.id; }).length;
      return '' +
        '<a class="cat-card" href="' + c.id + '.html">' +
          '<div class="ico">' + c.icon + '</div>' +
          '<h3>' + esc(c.name) + '</h3>' +
          '<p>' + esc(c.desc) + '</p>' +
          '<p style="margin-top:8px;font-weight:700;color:var(--primary)">' + n + ' 个工具 →</p>' +
        '</a>';
    }).join('');
  }

  /* ---------- 8. 更新日志渲染 ---------- */
  function renderChangelog() {
    var wrap = document.getElementById('changelog');
    if (!wrap || !state.data) return;
    var log = (state.data.changelog || []).slice().sort(function (a, b) { return b.date.localeCompare(a.date); });
    if (!log.length) { wrap.innerHTML = '<div class="empty">暂无更新记录</div>'; return; }
    wrap.innerHTML = log.map(function (item) {
      var chips = (item.added || []).map(function (n) { return '<span class="chip scenario">' + esc(n) + '</span>'; }).join('');
      return '' +
        '<div class="changelog-item">' +
          '<div class="date">' + esc(item.date) + '</div>' +
          '<div class="note">' + esc(item.note || '') + '</div>' +
          '<div class="added">' + chips + '</div>' +
        '</div>';
    }).join('');
  }

  /* ---------- 工具：HTML 转义（防注入） ---------- */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* ---------- 启动 ---------- */
  function boot() {
    initTheme();                       // 主题（不依赖数据）
    fetch(JSON_URL, { cache: 'no-cache' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (json) {
        state.data = json;
        if (document.body.getAttribute('data-page') === 'update') {
          renderChangelog();
        } else {
          initNav();
          renderCatCards();
          renderGrid();
        }
        initSearch();
        initDeadToggle();
        initNavToggle();
        initFilters();
      })
      .catch(function (err) {
        var wrap = document.getElementById('agentGrid');
        if (wrap) wrap.innerHTML = '<div class="empty">数据加载失败，请确认 data/agent-list.json 存在。<br>错误信息：' + esc(err.message) + '</div>';
      });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
