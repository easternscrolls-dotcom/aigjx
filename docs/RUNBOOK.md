# RUNBOOK.md — 故障自查手册

> 设计哲学：**自动优先，人工兜底。**
> 本系统的每一类已知故障都内置了自动处理逻辑（源降级、延迟重试、冷却轮询、构建回滚、
> 缺失兜底、超龄清理、周备份）。绝大多数情况下你什么都不用做，第二天它自己就好了。
> 本手册用于：① 区分「系统在正常工作」与「真出事了」；② 只在确需人工时告诉你怎么动。

---

## 0. 先看这一节：什么情况其实不是故障

以下现象 **都是预期行为，不要误判为故障**：

| 你看到的现象 | 为什么是正常的 |
|-------------|---------------|
| Actions 每天触发 3 次，但有 2 次 **瞬间结束、几乎没有日志** | 时段判定（gate）没命中今日执行时段，主动退出省额度。每个 job 只命中 1 个时段 |
| 日志里有 `随机静默 Ns 后开始` | 反爬抖动，正常 |
| 某些素材被 `skip` / `dropped`，页面数少于素材数 | 合规过滤（相似度/字数/盗版词）在生效，是好事 |
| 偶尔某天只产 1~2 篇后自动 `二次全流程重跑` | 产出低于 `min_pages_per_day(3)` 触发自动补跑 |
| 某 RSS 源连续几天不出现 | 被源健康熔断自动休眠 `source_sleep_days(7)` 天，到期自动复活 |
| 周一时多出一次推送/构建 | `weekly-health` 巡检+备份提交，正常 |
| 每月 1 日多出一次推送/构建 | `monthly-keywords` 挖词入库，正常 |
| `health_report.json` 里个别翻译源 `false` | 该免费源临时不可用，已自动冷却并轮询其余源 |

**判断口诀：** 只要 `health_report.json.content_counts` 三语种都有数、且最近一次 `daily-pipeline`
不是 `build-failed-rolled-back`，就不用管。

---

## 1. 分模块故障对照表

### 模块1 · RSS 素材采集（collect.py）
| 故障 | 自动处理 | 需人工吗 | 人工动作（仅当长期不愈） |
|------|---------|---------|------------------------|
| 单源 404 / 空数据 / 超时 | 镜像轮换（`rsshub_mirrors`）+ 重试（retry_times=3，退避 3/8/20s） | 否 | — |
| 单源连续 `source_fail_days_to_sleep(2)` 天失败 | 自动休眠该源 `source_sleep_days(7)` 天并切下一个备用源；到期自动复活 | 否 | 长期挂就改 `sources.yaml` 调低其 `priority` 或换 URL |
| 分片某片失败 | `--shard` 各片独立，`continue-on-error` 不阻断其它片 | 否 | — |
| 抓取到的 ID 已存在（45 天内） | `_load_seen` 去重，跳过 | 否 | — |
| 某源完全拉不到且当天无备用 | 失败素材写入 `data/queue/retry_*.json`，次日由 `run_daily` 自动消费 | 否 | — |

### 模块2 · 免费多源翻译（translate.py）
| 故障 | 自动处理 | 需人工吗 | 人工动作 |
|------|---------|---------|---------|
| 某翻译源报错（限流/超时） | 该源进入 `cooldown_minutes(30)` 冷却，自动轮询下一源 | 否 | — |
| 全部源都失败 | 素材进 `translate` 类型延迟队列，下次运行重试；已生成页面不受影响 | 否 | — |
| 某源长期不可用（周巡检报警） | `check_providers` 标记 `false`，轮询其余源 | 仅 **DeepLX 公共节点** 失效时需 | 往 `settings.yaml[translate.deeplx_endpoints]` 加新社区节点 |
| 翻译结果语种错乱 | 翻译按 `locales.yaml` 的 `google/edge/deeplx` 码分语种隔离，且结果进语种缓存 | 否 | — |

### 模块3 · 长尾词挖掘（keywords.py）
| 故障 | 自动处理 | 需人工吗 |
|------|---------|---------|
| Google Suggest 被限流 | 退避后换 DuckDuckGo 相关搜索兜底 | 否 |
| 当月新词达 `max_new_per_run(400)` 上限 | 停止扩词，避免失控 | 否 |
| 合并去重 | `merge_into_csv` **只增不删**，不会误删已有词 | 否 |

### 模块4 · 无 AI 模板生成（generate.py）
| 故障 | 自动处理 | 需人工吗 |
|------|---------|---------|
| 同一素材重复运行 | `generated_pages.json`（`GENERATED_STATE`）防重复生成 | 否 |
| 软阈值触发（相似度 8%~10%） | 自动截取片段（`soft_trim_chars=90`）降低重合 | 否 |
| 某页产出不足 | `run_daily` 低于 `min_pages_per_day(3)` 触发二次重跑 | 否 |
| 模板变量缺失导致渲染异常 | Jinja2 `StrictUndefined` —— 若真缺变量会报错，由构建预检拦截（见模块8） | 否 |

### 模块5 · 合规查重（compliance.py）
| 故障 | 自动处理 | 需人工吗 |
|------|---------|---------|
| 译文与原素材相似度 > `similarity_max(10%)` | **直接丢弃**该页，不发布 | 否 |
| 相似度 8%~10% | 软阈值截取片段 | 否 |
| 命中极限词/收益承诺黑名单 | 正则替换（合规化） | 否 |
| 命中盗版敏感词（crack/torrent/mod apk…） | `has_piracy_signal` 拦截 | 否 |
| 页面 < `min_words(300)` | **拦截**不生成（质量兜底） | 否 |

### 模块6 · SEO 增强（seo.py）
| 故障 | 自动处理 | 需人工吗 |
|------|---------|---------|
| 标题/描述重复风险 | `TITLE_PATTERNS`/`DESC_PATTERNS` 多语种句式池随机拼接 | 否 |
| 内链失效 | `LinkPool` 扫描现有页面动态采样，只链已存在页 | 否 |
| 结构化数据异常 | `decide_schema` 自动选 FAQPage/ItemList，Review 不伪造评分 | 否 |
| sitemap/robots 布局异常 | `verify_sitemap_layout` 自检，缺失由 selfheal 兜底生成 | 否 |

### 模块7 · 自愈巡检（selfheal.py / run_weekly.py）
| 故障 | 自动处理 | 需人工吗 |
|------|---------|---------|
| 必备 CSV 缺失 | `ensure_csv_files` 自动生成带表头空白模板（生成流程跳过对应语种，不崩） | 仅当空白模板长期未补 |
| sitemap.xml / robots.txt 缺失 | `ensure_site_files` 生成最小兜底版，Hugo 仍能构建 | 否 |
| 三语种 `_index.md` 缺失 | 自动生成兜底首页 | 否 |
| 翻译源全部不可用 | 降级告警，延长冷却，已发布页不受影响 | 否 |
| 文章超龄（> `article_keep_days(240)` 天） | 自动清理压缩构建体积（保留 hub 与 _index） | 否 |
| 缓存/队列超龄 | `clean_caches` 按保留天数清理 | 否 |
| 词库/FAQ/短句/黑名单丢失风险 | 每周备份到 `backup/<年-周>/`，保留 `backup_keep_weeks(8)` 周 | 否 |

### 模块8 · 发布链路（publish.py）
| 故障 | 自动处理 | 需人工吗 |
|------|---------|---------|
| 无内容变更 | `has_changes` 跳过 Git 推送，避免空提交 | 否 |
| Hugo 构建预检失败 | `rollback`（git checkout/clean）放弃本轮内容，线上保留上一稳定版 | 否（必要时看日志） |
| 并发 push 被拒（非快进） | `git pull --rebase --autostash` 后再推 | 否 |
| 本地无 hugo | 预检跳过（Actions 环境有 hugo） | 否 |

---

## 2. `health_report.json` 字段解读

文件位置：`data/state/health_report.json`（每周一刷新，调试时可本地 `python -m pipeline.run_weekly` 立即生成）。

```jsonc
{
  "at": "2026-08-12T13:00:00Z",        // 巡检时间
  "topic": "ai_tools",                  // 当前题材
  "csv": {                             // 各 CSV 状态
    "longtail.csv": "ok(60 rows)",      //   ok(N rows) = 正常且有数据
    "faq_bank.csv": "empty",            //   empty = 仅表头无内容，对应语种会跳过生成
    "snippets.csv": "created-empty-template" // 缺失时自动建了空白模板，需补内容
  },
  "site": {                            // 站点关键文件状态
    "sitemap.xml": "ok",
    "robots.txt": "ok",
    "verify_sitemap_layout": {"flat": true, "subdir": true} // seo 自检
  },
  "translate_sources": {               // 仅 full 巡检有；false=该源本周不可用
    "google_free": true,
    "edge_free": true,
    "deeplx_public": false             //   DeepLX 公共节点挂了 → 往 settings 加新节点
  },
  "cleaned_articles": 0,               // 本次清理的超龄文章数
  "cleaned_caches": {"cache":0,"raw":2,"translated":1,"queue":0},
  "backup": "backup/2026-33/...",      // 本周备份目录
  "content_counts": {"en": 120, "es": 110, "id": 105} // 各语种现存页面数
}
```

**快速判读：**
- `csv` 出现 `empty` / `created-empty-template` → 对应语种会减产，需补 CSV 内容（人工）。
- `translate_sources` 全 `false` → 当周新内容会进延迟队列，等源恢复；已发布页不受影响。
- `content_counts` 三语种均 > 0 且稳定 → 系统健康。
- `cleaned_articles` / `cleaned_caches` 偶尔有数 → 正常清理，非故障。

---

## 3. 确实需要人工的场景（短清单）

绝大多数情况你 **不需要** 打开本手册。仅在以下信号出现时才动手：

1. **`faq_bank.csv` / `snippets.csv` / `longtail.csv` 长期 `empty` 或 `created-empty-template`**
   → 编辑对应 CSV 补内容（见 DEPLOY.md 阶段六表格）。
2. **`translate_sources.deeplx_public == false` 持续多周**
   → 往 `config/settings.yaml[translate.deeplx_endpoints]` 追加新的社区公共节点。
3. **某 RSS 源连续多周不产内容且非熔断期**
   → 在 `config/sources.yaml` 调整其 `priority` 或替换为可用 URL。
4. **Cloudflare 构建持续失败（Pages 面板红灯）**
   → 看 Actions 的 `daily-pipeline` 日志；若是模板/配置改动导致 Hugo 报错，本地
     `cd site && hugo --minify` 复现并修复，再推送。
5. **想把站做大/换题材/换域名**
   → 全部改配置，见 DEPLOY.md 阶段三、四、六。

---

## 4. 排查决策树（遇事按图走）

```text
网站今天「没更新 / 没流量 / 报错」？
│
├─ 先看 GitHub Actions 最近一次 daily-pipeline 状态
│   ├─ 未运行 / 秒退            → 看 gate 日志，多半是「非执行时段」，正常，等命中时段
│   ├─ 运行了但 0 产出          → 看 run_daily 日志：
│   │   ├─ "本轮无素材可处理"   → 源全挂/全熔断 → 检查 sources.yaml + health_report.translate_sources
│   │   ├─ 大量 "dropped"       → 合规拦截（相似度/字数），正常，非故障
│   │   └─ "二次重跑"后仍有产出 → 当天素材少，系统已自动补，无事
│   └─ build-failed-rolled-back → 模板/配置改动导致 Hugo 失败，本地 `cd site && hugo --minify` 修
│
├─ Cloudflare Pages 红灯
│   ├─ 是首次部署               → 检查构建命令 `cd site && hugo --minify` / 输出 `site/public`
│   └─ 之前正常突然红           → 多半是最近 push 的 content/配置有问题，本地 hugo 复现修复
│
└─ 一切看起来在跑但内容偏少
    ├─ health_report.csv 有 empty → 补 CSV（人工场景 1）
    └─ content_counts 三语种均>0  → 正常波动，无需干预
```

---

## 5. 红线与禁区（动这里会破坏「零投入/无 AI」承诺）

- **禁止** 在 `pipeline/*.py` 里加入任何 `openai` / `anthropic` / `google.generativeai` /
  `requests.post(<大模型 endpoint>)` 等生成式调用。
- **禁止** 在 `requirements.txt` 引入付费 SDK（如 `google-cloud-translate`、`azure-cognitiveservices-speech`）。
- **禁止** 把模型生成的网站正文文案写进本仓库（正文只能由 `templates/*.j2` + `data/*.csv`
  经 `generate.py` 拼装得出，没有任何模型参与解读/改写/原创）。
- **禁止** 为「提速/稳定」而购买付费 API、服务器、代理或翻译服务——本系统的全部韧性
  来自「多源免费 + 自动降级 + 延迟重试 + 构建回滚」，付费反而违背设计。

> 自检命令（部署后随手跑）：在 `pipeline/` 目录全文搜索确认无 LLM 调用——
> ```bash
> grep -rniE "openai|anthropic|gemini|qwen|claude|llm|gpt" pipeline/ requirements.txt
> # 期望：无匹配（或仅出现在注释/文档说明中）
> ```

---

## 6. 一行救命命令（本地调试）

```bash
# 1) 强制立即跑一轮（含采集，会推送，慎用）
FORCE_RUN=1 python -m pipeline.run_daily
# 2) 只跑生成不推送（最安全）
python -m pipeline.run_daily --no-publish
# 3) 立即出一份健康报告
python -m pipeline.run_weekly --no-publish
# 4) 立即补挖一轮长尾词（不推送）
python -m pipeline.run_monthly --no-publish
# 5) 本地预览站点
cd site && hugo server
# 6) 本地验证 Hugo 构建能通过（等价于线上构建预检）
cd site && hugo --minify
```

> 红线提醒：本手册所述「人工动作」全部是改 `config/*.yaml` 与 `data/*.csv`，
> 不要求你阅读或修改任何 Python 逻辑。如遇超出本手册的异常，保留 Actions 日志与
> `health_report.json` 再向上反馈。
