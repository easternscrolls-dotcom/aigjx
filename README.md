# AgentHub · AI 智能体自动化工具大全

> 全网独家垂直 **AI 智能体 Agent 工具导航站** —— 只收录自动化 Agent，不混杂绘图与通用写作 AI。
> 纯静态、JSON 数据驱动、零后端、零付费资源，适配 Cloudflare Pages，全程免费。

---

## 一、目录结构

```text
autoguide-station/
├── index.html              # 首页：标题 + 搜索 + 深浅色切换 + 四大分类导航 + 工具网格
├── local.html              # 分类页：本地开源 Agent 框架
├── browser.html            # 分类页：浏览器网页 Agent
├── workflow.html           # 分类页：低代码工作流 Agent
├── crossborder.html        # 分类页：跨境商用 Agent
├── tools.html              # 分类页：Agent 配套辅助工具（侧边分类）
├── update.html             # 更新日志页（自动读取 JSON 的 changelog）
├── 404.html                # 友好 404 页
├── robots.txt              # 搜索引擎抓取规则 + sitemap 指向
├── sitemap.xml             # 站点地图（由脚本生成，可提交百度/谷歌）
├── sitemap.json            # 站点索引可读模板（由脚本生成）
├── _headers                # Cloudflare Pages 缓存/安全响应头
├── css/
│   └── style.css           # 全部样式（CSS 变量主题、响应式、置灰）
├── js/
│   └── app.js              # 全部前端逻辑（原生 JS，零依赖）
├── data/
│   └── agent-list.json     # ★ 唯一数据源：所有工具/分类/更新日志都在这里
└── scripts/
    └── generate-sitemap.js # 读取 JSON 生成 sitemap.xml / sitemap.json（零依赖 Node）
```

---

## 二、数据驱动：只改 JSON，不动前端

所有工具数据统一存放在 **`data/agent-list.json`**，前端页面不写死任何工具。
新增 / 修改 / 下架工具，只需编辑这个文件，重新生成 sitemap（可选），推送即可。

`agent-list.json` 字段说明：

| 字段 | 说明 |
| --- | --- |
| `site` | 站点标题、描述、关键词、baseUrl（用于 sitemap/SEO） |
| `categories[].id` | 分类标识，必须唯一（local/browser/workflow/crossborder/tools） |
| `agents[].id` | 工具唯一标识 |
| `agents[].category` | 对应 `categories[].id` |
| `agents[].type` | `open-source`(开源免费) / `cloud-paid`(云端付费) / `local-offline`(本地离线) |
| `agents[].status` | `active`(正常) / `dead`(失效链接，自动灰色置灰、可隐藏) |
| `agents[].tags` / `scenarios` | 标签 / 适用场景（用于搜索与展示） |
| `agents[].added` | 收录日期（YYYY-MM-DD），用于更新日志 |
| `changelog[]` | 更新日志条目（`date` / `added[]` / `note`） |

> 失效链接处理：`status:"dead"` 的工具不会被删除，仅在前端灰色置灰 + “已失效”标记；
> 用户可勾选“隐藏失效链接”将其隐藏。数据保留，符合“不删除仅隐藏”要求。

---

## 三、本地预览

纯静态，无需构建。任选一种方式起本地服务器（务必用 http 服务，不能直接双击打开，
否则 `fetch` 会因 file:// 跨域失败）：

```bash
# 方式 A：Python
cd autoguide-station
python -m http.server 8080
# 浏览器打开 http://localhost:8080/

# 方式 B：Node
npx serve .
```

生成/刷新站点地图：

```bash
node scripts/generate-sitemap.js
```

---

## 四、部署到 Cloudflare Pages（关键步骤）

本站是**纯静态、无 Hugo**，因此 Cloudflare Pages 的构建设置必须调整：

| 设置项 | 值 |
| --- | --- |
| 生产分支 | `main` |
| **构建命令** | `node scripts/generate-sitemap.js` （或留空） |
| **输出目录** | `/` （仓库根目录，不是 site/public） |
| 环境变量 | 无需 |

> ⚠️ 旧版这里是 `cd site && hugo --minify` → `site/public`，**与本纯静态架构不兼容**，
> 部署前务必在 Cloudflare 控制台把构建命令与输出目录改成上表。

推送后 Cloudflare 自动拉取并上线，域名（如 `freeai.72tool.com`）无需改动。

---

## 五、SEO 要点（已内置）

- 每个页面独立 `<title>` / `description` / `keywords`，并含 `canonical`、`og:` 社交标签。
- 关键词针对国内长尾词（如“本地开源智能体框架”“跨境电商 Agent”“外贸邮件自动回复”）。
- `robots.txt` 指向 `sitemap.xml`；`sitemap.xml` 可提交到百度搜索资源平台 / Google Search Console。
- 无弹窗广告、极简界面，加载速度快，利于收录。

---

## 六、二次扩展建议（保持零成本）

- **加工具**：编辑 `data/agent-list.json`（或写个小脚本批量追加），重跑 sitemap，推送。
- **加分类**：在 `categories` 增加一项，并新建一个 `<id>.html`（复制现有分类页改 `data-category` 与 meta 即可）。
- **每日自动记录**：用 GitHub Actions 定时任务，每天把新增 agents 写入 `changelog` 并提交，update.html 自动展示。

---

© 2026 AgentHub · 数据驱动纯静态导航 · 无广告 · 零后端
