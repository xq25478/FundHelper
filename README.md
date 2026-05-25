# fund-helper

> 本地 Mac 桌面 A 股基金分析助手 · Python 3.12 · FastAPI + Jinja2 + ECharts · SQLite 持久化

四个导航页：**持仓基金 · 持仓穿透 · 大盘行情 · 实时新闻**。所有数据离线缓存，断网仍可看历史；联网时按 TTL 增量刷新。

不做实盘交易、不做投顾建议，仅做数据采集与可视化。

---

## 截图

| 持仓基金（6 个时间窗口）            | 持仓穿透（前十大重仓股迷你 K 线） |
| ----------------------------------- | --------------------------------- |
| 散点 + 折线，绿/红按区间收益自动着色 | 5 只基金 × 10 只重仓股网格            |

| 大盘行情（A 股 5 大指数）            | 实时新闻（情绪 / 消息 / 政策 / 美股） |
| ------------------------------------ | ---------------------------------- |
| 盘中 60s 缓存，非盘中 12h 缓存       | 利好 / 利空关键词高亮，行业相关性可视 |

---

## 快速开始

```bash
# 1. 准备虚拟环境（项目自带 fund/，不重新创建也行）
python3.12 -m venv fund
source fund/bin/activate
pip config set global.index-url https://mirrors.jd.com/pypi/web/simple    # 可选：京东源
pip install -e ".[dev]"

# 2. 配置你的持仓
cp configs/holdings.example.yaml configs/holdings.yaml   # 或直接编辑现有的
vim configs/holdings.yaml                                # 填基金代码 + 权重

# 3. 启动 Web
./fund/bin/fh serve --no-open --port 7788
# 浏览器打开 http://127.0.0.1:7788
```

可选 CLI：

```bash
fh refresh universe                     # 全市场基金列表
fh refresh nav   --code 017811          # 单只基金净值
fh refresh holdings                     # 按 holdings.yaml 批量拉净值
fh report    017811                     # Markdown 报告
fh screen    --type equity --min-aum 5e8
fh backtest  configs/portfolio.example.yaml
fh data-status                          # 查看本地数据新鲜度 / 覆盖率 / 缺口
fh analyze --target all --refresh-nav   # 自动生成大盘/板块分析与操作建议 Markdown
fh push-report reports/advice/latest.md # 邮件推送最新报告
```

---

## GitHub Actions 自动分析

项目已内置两个 workflow：

- `.github/workflows/ci.yml`：push / PR 时安装依赖、跑 `ruff check src tests` 和 `pytest`
- `.github/workflows/daily-analysis.yml`：交易日 15:40（Asia/Shanghai）自动执行 `fh analyze`，生成 `reports/advice/*.md` 并上传为 Actions artifact

在 GitHub 仓库 `Settings -> Secrets and variables -> Actions` 添加以下 Secrets：

| Secret | 说明 |
| ------ | ---- |
| `FH_AI_MODEL` | 必填，自动分析使用的模型名 |
| `FH_AI_API_KEY` 或 `OPENAI_API_KEY` | 使用默认 OpenAI endpoint 时必填 |
| `FH_AI_PROTOCOL` | 可选，默认 `openai_responses`；也支持 `openai_chat` / `anthropic` |
| `FH_AI_BASE_URL` | 可选，默认 `https://api.openai.com/v1` |
| `FH_AI_TIMEOUT` | 可选，默认 `120` |
| `FH_AI_MAX_TOKENS` | 可选，默认 `12000` |
| `FH_PUSH_MAILS` | 收件人邮箱，`;` 分隔多地址 |
| `FH_PUSH_SMTP_HOST` | SMTP 服务器地址 |
| `FH_PUSH_SMTP_PORT` | SMTP 端口（默认 465，SSL） |
| `FH_PUSH_SMTP_USER` | 发件人邮箱账号 |
| `FH_PUSH_SMTP_PASSWORD` | SMTP 密码/授权码 |
| `FH_PUSH_PROVIDER` | 可选，默认 `smtp` |
| `FH_PUSH_MAX_CHARS` | 可选，默认 `3600`，用于控制邮件内容长度 |

本地先试跑一遍：

```bash
FH_AI_ENABLED=true \
FH_AI_PROTOCOL=openai_responses \
FH_AI_BASE_URL=https://api.openai.com/v1 \
FH_AI_API_KEY=... \
FH_AI_MODEL=... \
fh analyze --target all --refresh-nav --out reports/advice
```

手动触发 `Daily Fund Analysis` 时可以选择只跑大盘或板块分析，也可以勾选 `commit_report`，把最新报告复制到 `analysis/latest.md` 并提交到仓库。默认定时任务只上传 artifact，不自动提交分析内容。

> 自动报告会包含“操作建议（仅供参考，非投顾意见）”和风险提示；它是公开数据驱动的复盘辅助，不是实盘交易指令。

Prompt 设计里会额外注入一段“数据质量与来源状态”，包括持仓净值覆盖、指数快照时间、板块快照时间、新闻条数与持仓相关度。新闻会按当前持仓主题打 `relevance_score/themes/kw_hits`，模型会优先引用高相关消息；如果数据缺失或过旧，输出会要求降低置信度并偏向观察/分批。

### 邮件推送

当前内置的是 SMTP 邮件推送。配置好邮箱参数后，定时任务生成 `reports/advice/latest.md` 后会自动发送邮件：

```bash
FH_PUSH_ENABLED=true \
FH_PUSH_PROVIDER=smtp \
FH_PUSH_MAILS="xq25478@qq.com;xq25478@gmail.com" \
FH_PUSH_SMTP_HOST=smtp.qq.com \
FH_PUSH_SMTP_PORT=465 \
FH_PUSH_SMTP_USER=xq25478@qq.com \
FH_PUSH_SMTP_PASSWORD=your_auth_code \
fh push-report reports/advice/latest.md --title "基金分析日报"
```

SMTP 密码/授权码等同于邮箱密钥，不要提交到 GitHub。后续可以在同一个 `push-report` 命令下继续扩展 provider。

---

## 导航与数据来源

每个页面用的接口、SQLite 表、缓存策略都列在这里 — 替换数据源时按图索骥。

### 01 持仓基金 `/holdings`

| 项 | 说明 |
| ----- | ----- |
| 内容 | `configs/holdings.yaml` 里每只基金的近 7d / 2w / 1m / 2m / 3m / 6m 归一化净值散点+折线 |
| 数据源 | 天天基金 F10 接口 `api.fund.eastmoney.com/f10/lsjz`（需 `Referer: http://fundf10.eastmoney.com/`，pageSize 硬上限 20） |
| 实现 | `src/fund_helper/datasource/tiantian.py` · `services/nav_service.py` |
| 表 | `fund(code,name,...)` · `nav_daily(code,trade_date,unit_nav,acc_nav,daily_return,...)` · `nav_fetch_log` |
| 缓存 | 仅缺失或最新日 < 今日−2 时增量补抓；TTL 在 `cache.nav_ttl_hours` |
| 备注 | TotalCount 字段不可信，分页按 "短页 = EOF" 终止 |

### 02 持仓穿透 `/holdings_xray`

| 项 | 说明 |
| ----- | ----- |
| 内容 | 每只持仓基金最近一期前十大重仓股 + 每只股票近 ~200 天日 K 线 + 时间窗口切换 |
| 数据源（前十大重仓） | 主：天天基金 F10（akshare `fund_portfolio_hold_em`）<br>备：ETF 联接基金无重仓披露时 → 跟踪指数前 10 大成份股（akshare `index_stock_cons_weight_csindex`），映射表见 `stock_akshare.INDEX_PROXY_MAP` |
| 数据源（日 K） | 主：新浪 `stock_zh_a_daily`（稳定，无频次限制）<br>备：东方财富 `stock_zh_a_hist`（push2his.eastmoney.com，有频控） |
| 实现 | `src/fund_helper/datasource/stock_akshare.py` · `services/xray_service.py` |
| 表 | `fund_top_holding(fund_code,season,rank,stock_code,...)` · `stock_meta` · `stock_daily(stock_code,trade_date,open,close,...)` · `stock_fetch_log` |
| 缓存 | 重仓股 24h；日 K 增量补抓（max_local + 1d → today），TTL 24h |
| 注意 | macOS 系统级代理会被 push2his 拒；`_no_proxy()` 上下文管理器会临时清空 `http_proxy / https_proxy / all_proxy` 走直连 |

### 03 大盘行情 `/market`

| 项 | 说明 |
| ----- | ----- |
| 内容 | 上证指数 / 深证成指 / 沪深 300 / 创业板指 / 科创 50 当日行情 |
| 数据源 | 主：akshare `stock_zh_index_spot_sina()`（单批 ~1s ~562 行无限流）<br>备：`efinance.stock.get_realtime_quotes(['沪深系列指数'])` |
| 实现 | `src/fund_helper/datasource/eastmoney_index.py` · `services/market_service.py` |
| 表 | `index_snapshot(secid,name,now,pct,pre_close,high,low,last_ts,fetched_at)` · `index_intraday`（保留位） |
| 缓存 | 盘中 (Mon-Fri 09:30-11:30/13:00-15:00) **60s**；非盘中 **12h** |
| 刷新 | 页面右上角 `↻ 刷新` = `?refresh=1`，跳过缓存重抓 |
| 注意 | `push2his.eastmoney.com/trends2/get` 每 secid 轮询触发风控，**勿用** |

### 04 实时新闻 `/news`

四个 Tab：**情绪面 / 消息面 / 政策面 / 美股动态**。每条新闻按 [`.skills/fund-advisor.skill.md`](.skills/fund-advisor.skill.md) §4 关键词表做情绪打分（利好+1 / 利空−1 / 中性 0），命中关键词以胶囊形式高亮。

| 数据源 | 接口 | 频次/条数 |
| ------ | ---- | -------- |
| 财联社电报 | akshare `stock_info_global_cls(symbol="全部"/"重点")` | ~20 |
| 东方财富全球财经快讯 | akshare `stock_info_global_em` | ~200 |
| 富途资讯 | akshare `stock_info_global_futu` | ~50 |
| 同花顺资讯 | akshare `stock_info_global_ths` | ~20 |
| 新浪财经全球快讯 | akshare `stock_info_global_sina` | ~20 |
| 财新主要新闻 | akshare `stock_news_main_cx` | ~100 |
| 新闻联播文字稿 | akshare `news_cctv(date=YYYYMMDD)` | 当日 ~10-20 |
| 华尔街见闻 7×24 | 直连 `api-prod.wallstreetcn.com/apiv1/content/lives` | ~50 |
| 美股三大指数 | akshare `index_us_stock_sina(symbol=".DJI/.IXIC/.INX")` | 3 |

实现：`src/fund_helper/datasource/news_akshare.py` · `services/news_service.py` · `services/news_relevance.py`

表：`news_item(id,category,source,title,content,url,published_at,sentiment,pos_hits,neg_hits,relevance_score,themes,kw_hits,fetched_at)`

分类规则：
- 通用源默认进**消息面**
- 命中 `央行 / 降准 / 国常会 / 政治局 / 新政 …` → **政策面**
- 命中 `美股 / 纳指 / 美联储 / 鲍威尔 / 华尔街 …` → **美股动态**
- 情绪非零的消息条目镜像到**情绪面** Tab

相关性：从 `configs/holdings.yaml` 推出每只基金题材桶（人工智能 / 算力 / 半导体 / 电力 / 通信 / 国产替代），叠加 `buyer.md` 的 watchlist，给每条新闻打 `relevance_score` 与 `themes/kw_hits`，命中可在卡片胶囊看到。

> 列表中未接入的「科技第一线 / 科技圈 / 风向旗参考快讯 / 联合早报 / 金十数据 / 财经慢报」等：要么是财联社的订阅栏目（公开 API 无栏目字段，内容会经财联社/东财同步），要么是 OSS referer 拒绝 / 反爬严格无法稳定抓取。详见 `news_akshare.py` 顶部注释。

---

## 目录结构

```
src/fund_helper/
├── cli.py                          # typer: fh serve / refresh / report / screen / backtest
├── config.py                       # AppConfig + load_config
├── domain/                         # Fund / NavSeries / Holding / Metrics dataclasses
├── datasource/
│   ├── tiantian.py                 # 天天基金 F10 净值
│   ├── eastmoney.py                # 东方财富（占位）
│   ├── eastmoney_index.py          # A 股指数（sina 主，efinance 备）
│   ├── akshare_src.py              # akshare 通用源
│   ├── stock_akshare.py            # 个股日 K + 基金前十大重仓
│   ├── news_akshare.py             # 新闻聚合 fetchers
│   └── composite.py                # 多源组合
├── storage/
│   ├── db.py                       # SQLite schema + connect()
│   ├── repo.py                     # NavRepo
│   └── cache.py
├── services/
│   ├── nav_service.py              # 持仓基金净值 (TTL + 增量)
│   ├── xray_service.py             # 持仓穿透 (重仓股 + 日 K)
│   ├── market_service.py           # 大盘 (盘中 60s / 非盘中 12h)
│   ├── news_service.py             # 新闻聚合 + 情绪打分 + 缓存
│   └── news_relevance.py           # 按持仓题材打 relevance
├── analytics/                      # 收益 / 回撤 / 比率 / 归因 / 风格
├── screener/                       # 多因子过滤 + 打分
├── portfolio/                      # holdings 加载 + 组合回测
├── report/                         # Jinja2 markdown 渲染
└── web/
    ├── app.py                      # FastAPI routes
    ├── _windows.py                 # 时间窗口 (7d/2w/1m/2m/3m/6m)
    ├── templates/*.html
    └── static/app.css

configs/
├── holdings.yaml                   # 你的持仓 (代码 + 权重)
└── portfolio.example.yaml          # 回测样例

data/fund.db                        # SQLite (自动生成)
```

---

## SQLite 表汇总

| 表 | 内容 | 主键 |
| - | - | - |
| `fund` | 基金元信息 | `code` |
| `nav_daily` | 每日单位净值 / 累计净值 / 涨跌幅 | `(code, trade_date)` |
| `nav_fetch_log` | 每次抓取的窗口和结果 | — |
| `fund_top_holding` | 基金最新一期前十大重仓股 | `(fund_code, season, rank)` |
| `stock_meta` | 个股代码 → 名称 | `stock_code` |
| `stock_daily` | 个股日 K（OHLC + 成交量 + 涨跌幅） | `(stock_code, trade_date)` |
| `stock_fetch_log` | 个股抓取日志 | — |
| `index_meta` / `index_intraday` / `index_snapshot` | 指数行情 | — |
| `news_item` | 新闻 + 情绪 + 相关性 | `id` (sha1) |
| `meta_kv` | 杂项 KV（schema_version 等） | `k` |

---

## 开发与测试

```bash
./fund/bin/pytest                            # 11 tests passing
./fund/bin/ruff check src tests
./fund/bin/black src tests
```

测试覆盖：analytics / screener / backtest / report / nav_service（hit / incremental / force_refresh）。

---

## 设计取舍

- **本地优先**：所有数据先落 sqlite，离线可看；联网按 TTL 增量补抓
- **多源退化**：每个数据维度都给出 1 主 1 备，主源限流时自动 fallback（见各 service 注释）
- **无前端构建**：单页 ECharts 静态 + Jinja2 模板，零 npm
- **无 auth/无加密**：本地单用户工具，不引入登录、cookie、HTTPS

---

## 引用与致谢

- 数据源：天天基金、东方财富、新浪财经、富途、同花顺、财新、央视新闻联播、华尔街见闻
- Python 库：[akshare](https://github.com/akfamily/akshare) · [efinance](https://github.com/Micro-sheep/efinance) · FastAPI · ECharts · Jinja2 · pandas
- 评分规则参考：`.skills/fund-advisor.skill.md` 内部分析框架

不依赖任何付费数据接口。所有引用的接口归原网站所有，仅做个人学习与持仓分析用途。
