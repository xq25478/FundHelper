# fund-helper

> 面向专业股市投资者的十年经验 A 股市场 / 板块 / 基金资深分析专家 · Python 3.12 · Rich TUI · SQLite · DeepSeek/兼容 OpenAI 协议模型

`fund-helper` 现在走终端优先路线：保留本地数据、命令行、Markdown 报告和邮件推送，移除网页 UI。TUI 启动后会拉起后台刷新服务，每 1 分钟自动刷新持仓净值、基金公开估值、大盘、板块和新闻缓存，并自动重绘本地缓存视图。分析链路从“模型直接写报告”升级为“程序事实包 + 专家规则校验 + AI 总结”。

## 核心能力

| 能力 | 内容 |
| ---- | ---- |
| **TUI 工作台** | 自动重绘 · 持仓概览 · 大盘快照 · 数据状态 · 一键完整分析 · 最新报告预览 |
| **后台刷新** | TUI 内置 1 分钟刷新循环，页面只读本地缓存；分析前默认复用同一刷新锁刷新一轮 |
| **专家事实包** | 持仓指标 · 当日公开估值涨跌 · 真实组合收益序列风险 · 主题代理基准 · 重仓股重合度 · 数据缺口 |
| **主力意图框架** | 公开资金流 · 大单/小单结构 · 指数确认 · 板块宽度 · 持仓主题强弱，输出倾向而非确定事实 |
| **AI 分析报告** | 大盘研判 / 主力意图 / 板块轮动 / 持仓诊断 / 操作建议 / 下个交易日、7 天、本月趋势 |
| **规则护栏** | 单只基金权重上限 · 缺失数据禁用 · 不得编造同类/估值/资金流结论 |
| **建议复盘** | 自动归档每次报告 · 提取操作建议 · 粗略跟踪报告日至今组合收益 |
| **本地持久化** | SQLite 缓存净值、指数、板块、新闻、穿透持仓 |
| **交易日历** | AkShare/Sina A 股交易日历缓存，用于下一个交易日与趋势窗口计算 |
| **邮件推送** | SMTP 推送 Markdown 分析报告 |

## 快速开始

```bash
python3.12 -m venv fund
source fund/bin/activate
pip install -e ".[dev]"

cp config.example.yml config.yml
vim config.yml

fh tui
```

## 常用命令

```bash
# 终端工作台
fh tui

# TUI 中无需手动刷新；以下 refresh 命令主要用于脚本或临时维护

# 数据刷新
fh refresh universe
fh refresh nav --code 017811
fh refresh holdings
fh refresh realtime

# AI 分析报告
fh analyze --target all --refresh-nav
fh analyze --target holdings
fh analyze --target market
fh analyze --target sectors

# 规则和数据状态
fh data-status
fh advice-log
fh advice-log --review --horizon-days 7

# 报告推送
fh push-report reports/advice/latest.md --title "基金分析日报"

# 其他工具
fh report 017811
fh screen --type equity --min-aum 5e8
fh backtest configs/portfolio.example.yaml
```

## 定时推送

GitHub Actions 日报会在 A 股交易日的北京时间 `10:00`、`11:30`、`13:00`、`14:50` 触发分析并邮件推送。workflow 内部会先用交易所节假日日历判断当天是否开市，非交易日自动跳过；若日历接口不可用而只能退化为工作日猜测，定时推送也会跳过，避免节假日误发。

## 专家化改造

### 事实包优先

AI prompt 会注入由程序计算的专家事实包，包含：

- 持仓基金近 1/3 月收益、年化收益、波动、Sharpe、最大回撤
- 当日公开估值涨跌幅，来自天天基金/Fundgz 公开接口，不做本项目自行估算
- 基于当前权重合成的组合日收益、波动、Sharpe、最大回撤
- 每只基金的主题代理基准、近 3 月超额收益、相关性、Beta、信息比率
- 前十大重仓股重合度，用于识别“伪分散”
- A 股交易日历缓存，用于下一个交易日、未来 7 天、本月剩余窗口的趋势分析
- 主力意图事实框架：只基于公开资金流、指数量价、板块宽度和持仓主题强弱生成倾向判断
- 数据缺口，明确哪些结论不能下

### 规则护栏

`services/guardrails.py` 会在报告生成后自动复核：

- 建议权重不能超过投资画像中的单只基金上限，默认 25%
- 未接入同类样本时，不允许写“同类均值/同类排名”
- 未接入估值、北向资金、融资融券、逐笔资金流时，不允许据此下结论
- 不允许输出满仓、清仓、立即买卖等确定性交易指令

若模型报告越界，最终 Markdown 会追加“自动校验”章节；若与正文冲突，以自动校验为准。

### 建议日志与复盘

每次通过 `fh analyze` 或 `fh tui` 生成报告，都会追加一条 `reports/advice/advice_log.jsonl` 记录，包含报告路径、目标、自动校验数量和提取到的操作建议行。

```bash
fh advice-log
fh advice-log --review --horizon-days 7
```

报告日志会保存生成时的持仓快照。复盘目前按“当前持仓权重”粗略计算报告日至今组合收益，用来检查报告后的实际方向，不等同于严格按建议调仓后的收益。

### 板块别名

新浪板块名与同花顺历史 K 线名称不完全一致。自动匹配失败时可在 `configs/sector_aliases.yaml` 维护别名，例如：

```yaml
concept:
  HIT电池: HJT电池
```

### 可选基金档案

公开接口暂未稳定接入基金经理、规模、费率、换手率等信息。可以先在 `config.yml` 手工维护：

```yaml
fund_profiles:
  "017811":
    category: 主动权益/AI主题
    manager: ""
    aum: ""
    fee: C类
    benchmark: "000688.SH"
    note: "空字段会被视为缺口，报告不能编造。"
```

### 主题代理基准

`benchmarks.by_fund` 可指定每只基金的代理基准。当前内置支持：

| 代码 | 名称 |
| ---- | ---- |
| `000001.SH` / `1.000001` | 上证指数 |
| `000300.SH` / `1.000300` | 沪深300 |
| `399006.SZ` / `0.399006` | 创业板指 |
| `000688.SH` / `1.000688` | 科创50 |

## 配置

所有个人配置集中在 `config.yml`，已加入 `.gitignore`：

- 持仓基金：代码、名称、组合权重
- 投资者画像：风险偏好、行业偏好、权重上限
- 主题代理基准：每只基金对应的比较基准
- 可选基金档案：经理、规模、费率、备注
- AI 大模型：协议、Base URL、模型、API Key
- SMTP 邮件：服务器、账号、收件人

模板见 `config.example.yml`。

显式传入 `--config path/to/file.yml` 时，该文件优先于根目录 `config.yml`。未传 `--config` 时，优先读取本地个人配置 `config.yml`。

## 数据来源

| 数据域 | 来源 | 实现 |
| ------ | ---- | ---- |
| 基金净值 | 天天基金 F10 | `datasource/tiantian.py` · `services/nav_service.py` |
| 基金当日公开估值 | 天天基金 Fundgz | `datasource/tiantian.py` · `services/fund_realtime_service.py` |
| 持仓穿透 | 天天基金 F10 / akshare | `datasource/stock_akshare.py` · `services/xray_service.py` |
| 大盘指数 | 新浪/东方财富/efinance | `datasource/eastmoney_index.py` · `services/market_service.py` |
| 指数日线 | akshare 新浪指数 | `datasource/index_daily.py` · `services/index_daily_service.py` |
| 板块行情 | 新浪/同花顺映射 | `services/sector_service.py` · `services/sector_daily_service.py` |
| 新闻 | 财联社、东方财富、同花顺、富途、华尔街见闻等 | `services/news_service.py` |

## 目录结构

```text
src/fund_helper/
├── cli.py
├── tui.py
├── config.py
├── datasource/
├── storage/
├── services/
│   ├── ai_service.py
│   ├── fact_pack.py
│   ├── guardrails.py
│   ├── nav_service.py
│   ├── xray_service.py
│   ├── market_service.py
│   ├── sector_service.py
│   ├── news_service.py
│   └── notify_service.py
├── analytics/
├── screener/
├── portfolio/
└── report/
```

## 开发

```bash
./fund/bin/pytest
./fund/bin/ruff check src tests
```

## 设计取舍

- **专家优先**：先计算事实和约束，再让 AI 写解释。
- **终端优先**：去掉网页 UI，集中维护 CLI/TUI，TUI 自动重绘但不在渲染阶段发起网络请求。
- **本地优先**：数据落 SQLite，断网时仍可查看缓存。
- **证据优先**：缺数据就是缺数据，报告不能补故事。

数据源接口归原网站所有，本项目仅做个人学习与持仓分析用途。报告不构成投资建议，市场有风险，投资需谨慎。
