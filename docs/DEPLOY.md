# DEPLOY.md — 分步落地部署文档

> 目标：从零把本仓库跑成「GitHub + Actions + Hugo + Cloudflare Pages」的零现金自动站。
> 全程不买服务器、不买付费 API、不调用任何生成式大模型。
> 预计一次性配置 30 分钟，之后全自动无人值守。

---

## 阶段一：GitHub 仓库创建与推送

1. 在 GitHub 新建一个 **公开（Public）** 仓库，建议名 `autoguide-station`。
   - 公开仓库才能用 Cloudflare Pages 的免费 Git 集成（私有仓库也可，但需额外授权）。
   - 初始化时 **不要** 勾选自动生成 README/LICENSE（避免和本仓库文件冲突）。
2. 在本机仓库根执行（已含全部代码与 `.gitkeep` 占位）：

   ```bash
   # 若本地尚未 git 初始化
   git init
   git add .
   git commit -m "init: autoguide-station zero-cost pipeline"

   # 关联远端并推送到 main
   git remote add origin https://github.com/<你的用户名>/autoguide-station.git
   git branch -M main
   git push -u origin main
   ```

3. 确认推上去的文件包含：`pipeline/`、`config/`、`templates/`、`site/`、`data/`（含 `.gitkeep`）、
   `.github/workflows/*.yml`、`requirements.txt`、`docs/`。
   - `.gitignore` 已忽略 Python 产物（`__pycache__`）、`site/public/`、以及部分运行期中间产物，
     无需手动处理。

> 红线提醒：仓库里 **只放工程代码、配置、模板、词库 CSV、文档**，**不放任何由模型生成的网站正文**。
> 正文由流水线在 Actions 里生成后自动提交，符合「AI 仅产出工程产物」的纪律。

---

## 阶段二：本地环境准备（可选，仅用于调试）

> Actions 环境自带 Python 3.11 + 自动安装 Hugo，因此 **部署本身不需要本地环境**。
> 本阶段只在你想本地冒烟测试时才做。

### 依赖安装（跨平台一致）

```bash
# 1) 创建虚拟环境（推荐）
python -m venv .venv

# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# 2) 安装依赖
pip install -r requirements.txt
```

- 依赖均为纯 PyPI 包：`requests`、`PyYAML`、`Jinja2`、`feedparser`、`beautifulsoup4`。
- `googletrans` 在 `requirements.txt` 中已注释为可选——本系统优先用 `requests` 直连
  `translate.googleapis.com` 免密钥端点，不依赖 `googletrans`，离线也不报错。

### 本地 Hugo（仅调试用，非必装）

```bash
# 仅当要在本地预览页面时才装；建议 extended 版、0.128.0
# 见 https://github.com/gohugoio/hugo/releases
hugo version   # 期望输出 v0.128.0 或兼容版本
cd site && hugo server
```

---

## 阶段三：RSSHub 与素材源配置

1. 打开 `config/sources.yaml`：
   - 顶部 `rsshub_mirrors` 已内置 `rsshub.app` + 两个公共备用镜像。公共镜像若限流，
     把这里换成你 **fork 的免费 RSSHub 实例** 或自建免费实例即可，代码无需改动。
   - 下方 `topics:` 分 `ai_tools` / `mobile_game_quest` / `webnovel_list` 三组，
     每组 `sources` 是带 `priority` 的 RSS 列表（数字越小越先用，失败自动切下一个备用）。
2. 选择题材：打开 `config/settings.yaml`，把 `site.topic` 设为你要做的题材
   （默认 `ai_tools`），脚本会自动只加载 `sources.yaml` 里对应的那组源。
3. 想加自己的源：在对应 `topic.sources` 列表追加一项，例如：

   ```yaml
   - id: "my_blog"
     priority: 5
     type: "rss"
     url: "https://example.com/feed.xml"
     note: "我的博客 RSS"
   ```

> 版权安全提示：默认 `webnovel_list` 用的是古登堡计划（公共领域），天然合规；
> 其余题材使用的 Product Hunt / Hacker News / Reddit 官方免费 RSS 均为可引用来源，
> 且流水线在 `compliance.py` 内对译文做相似度与盗版词过滤，进一步降风险。

---

## 阶段四：Cloudflare Pages 绑定（核心）

1. 登录 [Cloudflare Dashboard](https://dash.cloudflare.com) → **Workers & Pages** → **Create** → **Pages** → **Connect to Git**。
2. 授权并选择刚创建的 `autoguide-station` 仓库。
3. 构建设置填写：

   | 字段 | 值 | 说明 |
   |------|-----|------|
   | Framework preset | `None` | 不用预设 |
   | **Build command** | `cd site && hugo --minify` | 在 `site/` 子目录构建（与 `publish.py` 预检一致） |
   | **Build output directory** | `site/public` | 产物输出到 `site/public`（Hugo 默认目录） |
   | Root directory | 留空（= 仓库根） | — |
   | Environment variables | 无需设置 | 全链路零密钥 |

   > 为什么是 `cd site && hugo --minify` / `site/public`？
   > 因为 `hugo.toml`、`content/`、`layouts/` 都在 `site/` 子目录，`publish.py` 的构建预检
   > 也是在这里跑的，本地预检与线上构建必须同构，避免「本地能过、线上崩」。
4. 点击 **Save and Deploy**。首次部署会拉取仓库并跑 `cd site && hugo --minify`，
   几分钟后获得形如 `https://autoguide-station.pages.dev/` 的默认域名。
5. **回填域名**（重要，否则结构化数据与 canonical 会指向占位地址）：
   - 把 `config/settings.yaml` 的 `site.base_url` 改为你的正式域名（结尾带 `/`）。
   - 把 `site/hugo.toml` 的 `baseURL` 改为同一域名。
   - 改完提交推送，触发下一次构建即生效。
6. （可选）绑定自定义域名：Pages 项目 → **Custom domains** → 添加你的域名并按提示加 DNS 记录，
   Cloudflare 免费提供 HTTPS 证书，无需付费。

> 发布链路闭环：Actions 每天把新生成的 `site/content/<lang>/*.md` 提交 push →
> Cloudflare 监听到 push → 自动 `hugo` 构建 → 上线。全程无付费 API、无人工操作。

---

## 阶段五：定时任务调试

### 自动触发（默认就生效）
- `daily-pipeline.yml`：cron 每天 UTC `17 1` / `43 3` / `29 5` 三次触发，
  但脚本按「日期哈希」只在 `settings.yaml[schedule.candidate_slots_utc]`（默认 `[1,3,5]`）
  **命中其中一个时段**才真正执行，另两个时段在 gate 阶段直接退出（几乎不耗 Actions 额度）。
- `monthly-keywords.yml`：每月 1 日 UTC `20 4` 挖词入库。
- `weekly-health.yml`：每周一 UTC `10 6` 巡检 + 清理 + 备份。

### 手动触发（调试/补跑）
- **GitHub 界面**：仓库 → **Actions** → 选对应 workflow → **Run workflow**。
  - `daily-pipeline` 的 `workflow_dispatch` 默认 `force=1`，即忽略时段判定立即跑。
  - `monthly-keywords` / `weekly-health` 直接 Run 即可。
- **本地强制跑**（忽略时段、立即执行，但仍会推送，调试慎用）：
  ```bash
  # macOS / Linux
  FORCE_RUN=1 python -m pipeline.run_daily
  # Windows (PowerShell)
  $env:FORCE_RUN="1"; python -m pipeline.run_daily
  ```
- **本地只生成不推送**（最安全的调试方式）：
  ```bash
  python -m pipeline.run_daily --no-publish
  ```
- **手动补一次挖词 / 巡检**：
  ```bash
  python -m pipeline.run_monthly --no-publish
  python -m pipeline.run_weekly  --no-publish
  ```

### 一致性注意
- `daily-pipeline.yml` 里 `env.CANDIDATE_SLOTS` **必须与** `settings.yaml[schedule.candidate_slots_utc]`
  保持一致，否则 gate 判定与脚本判定对不上，可能出现「gate 放行但脚本不跑」或反之。
  改其中一个时，另一个也要同步改。

---

## 阶段六：长期运维规则（日常零维护，偶发人工）

本系统设计目标是「部署完就不用管」。以下动作 **仅在需要时** 才做，且全部是改配置/CSV，不动 Python：

| 场景 | 怎么做 | 是否需改代码 |
|------|--------|-------------|
| 翻译公共节点失效（每周巡检会报警） | 往 `settings.yaml[translate.deeplx_endpoints]` 追加新的社区公共节点 | 否 |
| 想换题材 | 改 `settings.yaml[site.topic]` + `sources.yaml` 对应 `topic` 的源 | 否 |
| 某 RSS 源长期挂 | 在 `sources.yaml` 把它 `priority` 调大，或加新备用源 | 否 |
| 补充/修正长尾词 | 编辑 `data/keywords/longtail.csv`（每月也会自动扩） | 否 |
| 改地区话术/货币/支付渠道 | 改 `locales.yaml` + `data/localization/replacements.csv` | 否 |
| 调整页面字数/FAQ 数/内链数 | 改 `settings.yaml[generate.*]` | 否 |
| 收紧合规红线 | 改 `settings.yaml[compliance.*]` + `data/blacklist/banned_terms.csv` | 否 |
| 换 Git 提交身份/分支 | 改 `settings.yaml[publish.*]` | 否 |
| 改正式域名 | 改 `settings.yaml[site.base_url]` + `site/hugo.toml[baseURL]` 后提交 | 否 |

**唯一硬性纪律（不可突破的红线）：**
1. 不引入任何生成式大模型 API（OpenAI/Gemini/Claude/Qwen…）做内容改写/原创；
2. 不购买任何付费服务（服务器、翻译 API、SEO API 等）；
3. 不把模型生成的网站正文写进本仓库——正文只由流水线模板拼装生成。

---

## 阶段七：上线前红线自检清单

部署完成后，逐项确认：

- [ ] 仓库为公开（或已授权 Cloudflare 读取私有库）
- [ ] `config/settings.yaml[site.topic]` 已设为目标题材
- [ ] `config/settings.yaml[site.base_url]` 与 `site/hugo.toml[baseURL]` 已是真实域名
- [ ] Cloudflare Pages 构建命令 `cd site && hugo --minify`、输出 `site/public`
- [ ] `daily-pipeline.yml` 的 `CANDIDATE_SLOTS` 与 `settings.yaml` 候选时段一致
- [ ] 首次手动 Run `daily-pipeline`（force）成功，且 Cloudflare 构建出页面
- [ ] `pipeline/*.py` 全文检索确认 **无任何** `openai` / `anthropic` / `gemini` / `requests.post(...llm...)` 类调用
- [ ] `requirements.txt` 无付费 SDK（如 `google-cloud-translate`、`azure-cognitiveservices`）
- [ ] 首次巡检（weekly-health）报告无致命项

完成以上即视为交付达成：**零投入、内容创作全程无生成式 AI、全自动化无人值守。**
