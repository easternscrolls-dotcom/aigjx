# STRUCTURE.md — 项目目录结构与模块映射

> 适用对象：需要整体理解本仓库、定位文件、或在交接/二次开发时快速上手的维护者。
> 本文件描述 `autoguide-station/` 仓库的真实结构。如与代码不符，以代码为准并向文档提 PR。

---

## 0. 一句话定位

本仓库是一个 **零现金投入、内容创作全程不使用任何生成式大模型** 的海外多语言导读站自动化流水线。
它负责：定时采集公开 RSS → 免费接口翻译 → 无 AI 模板拼装成文 → 合规/查重过滤 → SEO 增强 → 自动提交并由 Cloudflare Pages 构建发布。

**红线（不可触碰）：**
1. 内容生成环节不使用 GPT/Gemini/Claude/Llama/Qwen 等任何生成式模型做改写、解读、扩写、原创。
2. 全栈零付费：不调用任何付费 API、不买服务器、不买翻译服务。
3. AI 仅产出工程代码/配置/模板/文档，不产出网站正文解读文案。
4. 一切故障、SEO、版权、限流风险均内置自动规避逻辑，无需人工日常维护。

---

## 1. 完整目录树

```text
autoguide-station/
├── .github/
│   └── workflows/                 # 三个定时调度工作流（模块 8 的运行载体）
│       ├── daily-pipeline.yml     #   每日：采集→翻译→生成→构建预检→推送
│       ├── monthly-keywords.yml   #   每月 1 日：长尾词挖掘并入库
│       └── weekly-health.yml      #   每周一：巡检+清理+备份
├── .gitignore                     # 忽略 Python 产物、Hugo public/、运行期中间文件
├── requirements.txt               # 纯 PyPI 依赖（requests/PyYAML/Jinja2/feedparser/bs4）
│
├── config/                        # 全部可调参数都在这三层 YAML，改配置不改代码
│   ├── settings.yaml              #   全局参数（调度/采集/翻译/生成/合规/自愈/发布）
│   ├── locales.yaml               #   三语种映射（Hugo 码、翻译源语言码、货币、支付渠道）
│   └── sources.yaml               #   RSS 源与备用源（按优先级 + 多镜像设计）
│
├── data/                          # 运行期数据（CSV 模板 + 中间产物；部分进 Git 部分不进）
│   ├── raw/                       #   采集原始 JSON（.gitkeep 占位）
│   ├── translated/                #   翻译后 JSON（按语种+日期命名，如 items_en_2026-08-12.json）
│   ├── queue/                     #   延迟重试队列 JSON（失败源/失败素材，次日重试）
│   ├── cache/                     #   翻译缓存（按语种隔离）
│   ├── state/                     #   熔断/去重/生成状态，generated_pages.json 防重复
│   ├── backup/                    #   每周词库与 CSV 备份（含 .gitkeep）
│   ├── snippets/snippets.csv      #   短句库（三语种各 13+ 槽位，含占位符）
│   ├── faq/faq_bank.csv           #   FAQ 库（三语种各 16 条，含占位符）
│   ├── localization/replacements.csv # 地区货币/支付渠道本地化替换表
│   ├── blacklist/banned_terms.csv #   极限词/盗版诱导/收益承诺黑名单（all/en/es/id）
│   └── keywords/longtail.csv      #   长尾词种子库（三语种各 20 条，5 分类）
│
├── pipeline/                      # 全部 Python 工程代码（模块 1~8 实现）
│   ├── __init__.py                #   包声明，重申「无 LLM」红线
│   ├── config.py                  #   路径常量、YAML 加载、点号路径读取、TOPIC 环境变量覆盖
│   ├── utils.py                   #   日志/随机休眠/原子写/清洗/字数/相似度/确定性随机等工具
│   ├── collect.py                 #   【模块1】RSS 采集：分片、镜像轮换、源健康、延迟队列
│   ├── translate.py               #   【模块2】免费多源翻译轮询 + 缓存 + 连通性巡检
│   ├── keywords.py                #   【模块3】长尾词挖掘：Google Suggest + DuckDuckGo
│   ├── generate.py                #   【模块4】无 AI 模板生成：组件重组 + 软阈值复渲染
│   ├── compliance.py              #   【模块5】合规查重：相似度/黑名单/盗版词/字数下限
│   ├── seo.py                     #   【模块6】SEO 增强：标题句式池/结构化数据/内链/聚合页
│   ├── selfheal.py                #   【模块7】自愈：兜底生成/翻译源巡检/清理/备份
│   ├── publish.py                 #   【模块8】发布：构建预检/失败回滚/git 提交推送/清理
│   ├── run_daily.py               #   每日编排入口（时段浮动→巡检→两轮→发布）
│   ├── run_monthly.py             #   每月挖词编排入口
│   └── run_weekly.py              #   每周巡检编排入口
│
├── templates/                     # Jinja2 模板（模块4 的"内容骨架"，不写正文）
│   ├── page.md.j2                 #   单页正文骨架（调用各 component 组件）
│   ├── hub.md.j2                  #   聚合页（主题集群）骨架
│   └── components/                #   可插拔内容组件，顺序由脚本随机重组
│       ├── intro.md.j2           #     引入段
│       ├── pros.md.j2            #     优点列表
│       ├── cons.md.j2            #     缺点列表
│       ├── table.md.j2           #     对比表（语义成对，左|右配对）
│       ├── faq.md.j2             #     FAQ 段（从 faq_bank 抽 5~8 条）
│       └── outro.md.j2           #     结尾+免责声明
│
├── site/                          # Hugo 静态站（被流水线写入 content/ 后由 Cloudflare 构建）
│   ├── hugo.toml                  #   多语言/扁平目录/分语种 sitemap 配置
│   ├── i18n/{en,es,id}.toml       #   三语种 UI 字符串
│   ├── content/                   #   流水线生成的 Markdown 落在此（en/es/id 子目录）
│   │   ├── en/{_index.md, tools/*.md, hub/*.md}
│   │   ├── es/{_index.md, tools/*.md, hub/*.md}
│   │   └── id/{_index.md, tools/*.md, hub/*.md}
│   ├── layouts/                   #   HTML 模板（baseof/single/list/partials）
│   │   ├── _default/{baseof,single,list}.html
│   │   ├── index.html / 404.html
│   │   ├── partials/{head,nav,schema,related}.html
│   │   ├── sitemap.xml / sitemapindex.xml / robots.txt
│   └── static/css/main.css        #   轻量无 JS 样式（hero/side/textonly 三布局）
│
└── docs/                          # 本文档所在目录
    ├── STRUCTURE.md               #   本文件：目录树与模块映射
    ├── DEPLOY.md                  #   分步部署文档
    └── RUNBOOK.md                 #   故障自查手册
```

---

## 2. 八大模块 ↔ 文件映射

| # | 模块 | 主实现文件 | 配套配置/数据 | 调度入口 |
|---|------|-----------|--------------|---------|
| 1 | RSS 素材定时采集 | `pipeline/collect.py` | `config/sources.yaml`, `data/raw/`, `data/queue/`, `data/state/` | `run_daily.py`（分片采集） |
| 2 | 免费多源翻译 | `pipeline/translate.py` | `config/settings.yaml[translate]`, `data/cache/`, `data/translated/` | `run_daily.py` |
| 3 | 全自动长尾词挖掘 | `pipeline/keywords.py` | `data/keywords/longtail.csv`, `config/settings.yaml[keywords]` | `run_monthly.py` |
| 4 | 无 AI 模块化内容生成 | `pipeline/generate.py` | `templates/*.j2`, `data/snippets/faq/localization/*.csv` | `run_daily.py` |
| 5 | 全自动版权&合规过滤 | `pipeline/compliance.py` | `data/blacklist/banned_terms.csv`, `config/settings.yaml[compliance]` | `generate.py` 内联调用 |
| 6 | 全自动 SEO 增强 | `pipeline/seo.py` | `site/layouts/partials/schema.html`, `sitemap*.xml`, `robots.txt` | `generate.py` 内联调用 |
| 7 | 托管平台故障自动自愈 | `pipeline/selfheal.py` | `data/backup/`, `data/state/health_report.json` | `run_weekly.py` + `run_daily.py` 轻量巡检 |
| 8 | 全自动部署发布链路 | `pipeline/publish.py` | `.github/workflows/*.yml`, `config/settings.yaml[publish]` | 三个 workflow 收尾 |

---

## 3. 数据流（一次每日运行的串联顺序）

```text
GitHub Actions(每日 3 次 cron)
   │
   ├─[gate] 时段判定：按"日期哈希"只命中 1 个候选时段，其余时段直接退出（省额度）
   │
   └─[pipeline]（命中时段才跑）
        │
        ├─ 1) 采集分片 1/3 → 2/3 → 3/3   (pipeline.collect --shard i/3)
        │        └─ 镜像轮换 + 源健康熔断 + 失败写延迟队列
        │
        ├─ 2) python -m pipeline.run_daily --skip-collect
        │        ├─ 消费延迟队列（回收上次失败素材）
        │        ├─ 合并分片、跨源去重
        │        ├─ 2.1 translate.run  → data/translated/items_<lang>_YYYY-MM-DD.json
        │        ├─ 2.2 generate.run   → 写入 site/content/<lang>/tools|hub/*.md
        │        │        ├─ compliance 过滤（相似度/黑名单/盗版/字数）
        │        │        └─ seo 增强（标题句式/结构化数据/内链/聚合页）
        │        └─ 产出 < min_pages_per_day(3) 时自动二次全流程重跑
        │
        ├─ 3) publish.cleanup_workspace  → 清理临时缓存
        ├─ 4) publish.publish            → 构建预检(git 回滚保护) + git 提交推送
        │
        └─ 5) Cloudflare Pages(Git 集成) 监听到 push → 自动 hugo 构建 → 上线
```

**月度 / 周度旁路：**
- `monthly-keywords.yml` → `run_monthly.py` → keywords 挖掘 → 合并入库 `longtail.csv` → 备份 → push
- `weekly-health.yml` → `run_weekly.py` → selfheal 全量巡检 → 清理超龄 → 备份 → push

---

## 4. 关键设计点速查

- **无 AI 如何千页千面**：`generate.py` 用基于素材 ID 的 *确定性随机*（`seeded_random`），
  对同一素材重跑结构稳定、对不同素材结果各异；组件顺序随机重组 + 短句库 + FAQ 库 + 长尾词库拼接。
- **防限流/反爬**：采集分片、源间随机休眠、`Actions` 执行时段按日期哈希浮动 ±2 小时 + 随机静默、
  User-Agent 池、翻译批间随机休眠 + 单源报错自动冷却轮询。
- **源健康管理**：单源连续 N 天 404/空数据 → 自动休眠并切备用源；休眠到期自动复活。
- **翻译源零密钥**：google_free（translate.googleapis.com 免密钥端点）、edge_free（匿名 token 换取）、
  deeplx_public（社区公共节点，失效时往 `settings.yaml[translate.deeplx_endpoints]` 补节点即可）。
- **合规安全阀**：`difflib` 相似度 > 10% 丢弃、8%~10% 软阈值截取片段、违规极限词正则替换、
  盗版敏感词过滤、< 300 词拦截。
- **自愈兜底**：缺失 CSV/站点文件自动生成模板；翻译源连通性巡检；超龄文章/缓存清理；
  每周备份；构建预检失败 `git checkout/clean` 回滚，线上永不空白。
- **Hugo 多语言**：`defaultContentLanguageInSubdir`、扁平 permalink（层级 ≤ 2）、
  分语种 sitemap/robots、hreflang 站点级配对、BreadcrumbList、Review 不伪造评分。
- **跨平台**：全程 `pathlib`、`subprocess`、UTF-8 兼容（`sys.stdout.reconfigure`），
  Windows/macOS/Linux 复制即用；不依赖 Docker、不写死绝对路径。

---

## 5. 配置文件改什么=变什么

| 想调整的行为 | 改哪个文件 |
|-------------|-----------|
| 换题材（手游/AI工具/网文） | `settings.yaml[site.topic]` + `sources.yaml[topics.*]` |
| 改每日执行时段 | `settings.yaml[schedule.candidate_slots_utc]` + `daily-pipeline.yml` 的 `CANDIDATE_SLOTS` + cron |
| 加 RSS 源 / 换镜像 | `sources.yaml` |
| 加/换翻译公共节点 | `settings.yaml[translate.deeplx_endpoints]` |
| 调页面字数、FAQ 数、内链数 | `settings.yaml[generate.*]` |
| 改合规红线阈值 | `settings.yaml[compliance.*]` |
| 改币种/支付渠道/地区话术 | `locales.yaml` + `data/localization/replacements.csv` |
| 补长尾词种子 | `data/keywords/longtail.csv`（每月自动扩，手工也可） |
| 换 Git 提交身份/分支 | `settings.yaml[publish.*]` |
| 改正式域名 | `settings.yaml[site.base_url]` + `site/hugo.toml[baseURL]` |

> 结论：**95% 的运维动作都在改 `config/*.yaml` 和 `data/*.csv`，无需触碰 `pipeline/*.py`。**
