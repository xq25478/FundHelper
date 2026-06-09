# 数据接入与可用性清单

> 当前真实状态(2026-06-01 核对)。本文档由人工维护,与 `_build_data_availability_section()` 程序检测互相校对。
> 若邮件正文出现"未接入"且本表标记为 ✅,几乎一定是 LLM 写错了,见 `B-邮件渲染修复`。

---

## 1. 数据域总览

| # | 数据域 | 状态 | SQLite 表 | 服务模块 | 数据源 | 刷新频率 |
|---|---|---|---|---|---|---|
| 1 | 持仓基金净值 | ✅ 已接入 | `nav_daily` | `nav_service.py` | 天天基金 / AkShare | 每个交易日盘后 |
| 2 | 基金当日公开估值 | ✅ 已接入 | `fund_realtime_snapshot` | `fund_realtime_service.py` | 天天基金 fundgz | 盘中 60s,盘后 12h |
| 3 | 大盘指数现货 | ✅ 已接入 | `index_snapshot` | `market_service.py` | 东方财富 | 盘中 60s |
| 4 | 指数分钟线 | ✅ 已接入 | `index_intraday` | `index_intraday_service.py` | 东方财富 | 盘中 60s |
| 5 | 板块行情 | ✅ 已接入 | `sector_snapshot` | `sector_service.py` | 新浪 / 同花顺 | 盘中 60s |
| 6 | 新闻与政策 | ✅ 已接入 | `news_item` | `news_service.py` | AkShare 聚合 | 30 分钟 |
| 7 | **同类基金样本/排名** | ✅ 已接入 | `fund_peer_rank_snapshot` | `peer_rank_service.py` | 东方财富 fund_open_fund_rank_em | 12h |
| 8 | **指数估值分位 (PE/PB)** | ✅ 已接入 | `index_valuation_snapshot` | `valuation_service.py` | 乐咕乐股 / 中证 | 24h |
| 9 | **北向资金摘要** | ✅ 已接入 | `market_flow_snapshot` (scope='northbound') | `market_flow_service.py` | 东方财富 hsgt | 盘中 60s |
| 10 | **融资融券汇总** | ✅ 已接入 | `market_margin_snapshot` | `margin_service.py` | AkShare macro_china_market_margin | 12h |
| 11 | 主力资金流(全市场/盘中) | ✅ 已接入 | `market_flow_snapshot` (scope='market' / 'market_intraday') | `market_flow_service.py` | 东方财富 | 盘中 60s |
| 12 | 持仓穿透(前十大重仓股) | ✅ 已接入 | `fund_top_holding` | `xray_service.py` | 天天基金 | 每个季度 |
| 13 | 重点公司消息匹配 | ✅ 已接入 | `company_news_match` | `company_watch_service.py` | 复用 news_item | 30 分钟 |
| 14 | 个股逐笔成交 / 盘口队列 | ❌ 未接入 | — | — | — | — |
| 15 | 个股逐笔资金流(逐单大单) | ❌ 未接入 | — | — | — | — |
| 16 | 期权 / 期货持仓 | ❌ 未接入 | — | — | — | — |
| 17 | 公募基金日度持仓(非披露) | ❌ 不可能接入 | — | — | — | — |

**结论**:你截图里标"❌缺失"的 4 项(同类样本、估值分位、北向资金、融资融券)**全部已接入**。
**真正未接入**的只有:个股逐笔成交、个股逐笔大单资金流、衍生品持仓。

---

## 2. 字段示例与查询方法

```bash
# 健康自检 — 一行命令看所有缓存最新时间
sqlite3 data/fund.db <<'SQL'
SELECT 'peer_rank' as topic, MAX(fetched_at), COUNT(*) FROM fund_peer_rank_snapshot
UNION ALL SELECT 'valuation', MAX(fetched_at), COUNT(*) FROM index_valuation_snapshot
UNION ALL SELECT 'margin',    MAX(fetched_at), COUNT(*) FROM market_margin_snapshot
UNION ALL SELECT 'flow',      MAX(fetched_at), COUNT(*) FROM market_flow_snapshot
UNION ALL SELECT 'realtime',  MAX(fetched_at), COUNT(*) FROM fund_realtime_snapshot;
SQL
```

| 数据域 | 关键字段 |
|---|---|
| `fund_peer_rank_snapshot` | `code, category, ret_1m, ret_3m, rank_3m, total, nav_date, fetched_at` |
| `index_valuation_snapshot` | `secid, name, pe, pb, pe_percentile, pb_percentile, trade_date, fetched_at` |
| `market_flow_snapshot` (北向) | `scope='northbound', item='沪股通'/'深股通', net_amount, trade_date` |
| `market_margin_snapshot` | `scope='上交所'/'深交所', financing_buy, financing_balance, securities_balance, margin_balance, trade_date` |

---

## 3. 时点对齐

| 数据域 | 数据时点性质 | 滞后程度 |
|---|---|---|
| 大盘现货 / 板块 / 指数分钟线 / 盘中资金流 / 北向 | **实时(盘中)** | 60s 内 |
| 基金估值 (fundgz) | **实时(盘中)** | 60s 内,T 日盘中估算 |
| 基金净值 (nav_daily) | T-1 收盘 | 跨过 15:30 后才出 |
| 主力资金流(盘后) | T-1 收盘 | 隔日 |
| 同类基金排行 | T-1 收盘 | 隔日 |
| 估值分位 (PE/PB) | T-1 | 隔日 |
| 融资融券 | T-1 | 交易所滞后一日 |
| 持仓穿透 | 上季报 | 1~3 个月 |

**邮件研判口径**:实时项与 T-1 项混用时,必须在结论里写明时点(盘中快照 / 隔日背景)。

---

## 4. 失败模式与降级

每个数据服务都有"抓取失败 → 用最近一次缓存兜底 → 写 error 字段"的降级路径。
邮件正文出现"未接入"时,优先检查:

1. **数据是否真的缺**:跑上一节那段 SQLite 自检。
2. **是不是 LLM 编的**:对比 `_build_data_availability_section()` 输出。命令:
   ```bash
   fund/bin/python -c "
   from fund_helper.config import load_config
   from fund_helper.services.ai_service import _build_data_availability_section
   print(_build_data_availability_section(load_config()))"
   ```
3. **是不是抓取超时**:看 `_fetch()` 的 errors,通常是网络/代理问题。

---

## 5. 未来扩展(P2 以下)

| 项 | 价值 | 难度 |
|---|---|---|
| 个股逐笔大单资金流(全持仓重仓股聚合) | 验证盘中"主力净流入"结论 | 中(需为每只重仓股拉 stock_individual_fund_flow) |
| 港股通流入(南向资金) | 跨市场情绪 | 低 |
| 行业 PE 分位(细分到中信/申万行业) | 行业级估值水位 | 中 |
| ETF 申赎数据 | 资金面的反向指标 | 中 |

