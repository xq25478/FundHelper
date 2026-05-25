"""News relevance scoring against the user's holdings + watchlist (buyer.md).

The theme -> keyword map mirrors `.skills/fund-advisor.skill.md` §二/§七:
each holding is mapped to a theme bucket, and each bucket carries a list of
matching keywords (industry / stock / policy / ETF-name aliases).

Output:
- relevance(text) -> (score:int, hits:list[str])
- score = number of distinct theme buckets the text hits, weighted by holding weight
- hits  = ordered list of "<theme>:<keyword>" for display
"""
from __future__ import annotations

from dataclasses import dataclass

# --- theme -> keyword 索引 (skill §二/§七) -----------------------------
# 每个 bucket 是 (theme_label, [关键词...])。关键词覆盖：板块名 / 龙头股 / 行业代名词 / 政策代名词
THEME_KEYWORDS: dict[str, list[str]] = {
    "人工智能": [
        "人工智能", "AI", "大模型", "大语言模型", "ChatGPT", "GPT", "Sora",
        "通用人工智能", "AGI", "Agent", "AI应用", "AI 应用", "AI 算力",
        "智能驾驶", "智能体", "DeepSeek", "豆包", "Kimi", "OpenAI", "Anthropic",
        "百度", "文心", "通义", "讯飞", "智谱",
    ],
    "算力": [
        "算力", "数据中心", "IDC", "服务器", "AI 服务器", "AI服务器",
        "推理芯片", "训练芯片", "英伟达", "NVIDIA", "H100", "H200", "B200",
        "GPU", "ASIC", "TPU", "AI 基础设施", "光模块",
    ],
    "半导体": [
        "半导体", "芯片", "集成电路", "晶圆", "晶圆代工", "封测", "光刻",
        "EDA", "IP", "国产替代", "国产芯片", "自主可控", "中芯国际", "华虹",
        "长江存储", "长鑫", "兆易创新", "韦尔股份", "中微", "北方华创",
        "拓荆", "盛美", "屹唐", "上海微电子", "ASML", "刻蚀", "薄膜沉积",
        "光刻胶", "存储芯片", "DRAM", "NAND", "HBM", "先进封装",
    ],
    "电力": [
        "电力", "电网", "特高压", "电气设备", "变压器", "智能电网",
        "新型电力系统", "电网投资", "国家电网", "南方电网", "国电南瑞",
        "许继电气", "平高电气", "思源电气", "新型储能", "电力体制改革",
        "调度", "调峰", "调频", "虚拟电厂",
    ],
    "通信": [
        "5G", "5G通信", "5G 通信", "通信", "光通信", "光模块", "光纤",
        "运营商", "中国移动", "中国电信", "中国联通", "基站", "射频",
        "CPO", "硅光", "中际旭创", "新易盛", "天孚通信", "光迅",
        "信维通信", "华为", "ZTE", "中兴通讯", "卫星互联网",
    ],
    "AI 上游 / 国产替代": [  # buyer.md 核心仰角
        "国产替代", "自主可控", "国产化", "国产", "卡脖子", "外资限制",
        "出口管制", "BIS", "实体清单", "国产 EDA", "国产光刻", "国产存储",
    ],
}

# 板块归因里 skill 提到的"涨停 / 资金流入"等仍属于通用宏观；这里只筛行业相关。

# --- holding -> theme buckets （skill §七 + buyer.md） -------------------
HOLDING_THEMES: dict[str, list[str]] = {
    "017811": ["人工智能", "算力", "AI 上游 / 国产替代"],
    "025209": ["半导体", "AI 上游 / 国产替代"],
    "025857": ["电力"],
    "014143": ["人工智能", "算力", "半导体"],
    "010524": ["通信", "算力"],
}

# 用户买家画像额外关注（不一定持仓，但希望关注）
WATCHLIST_THEMES: list[str] = ["AI 上游 / 国产替代", "半导体", "算力", "电力", "通信"]


@dataclass
class RelevanceResult:
    score: float           # 加权 relevance（覆盖的题材的持仓权重之和；watchlist 主题按 0.05 给个底）
    themes: list[str]      # 命中的主题标签
    keywords: list[str]    # 命中的具体关键词（去重，按出现顺序）

    @property
    def is_relevant(self) -> bool:
        return len(self.themes) > 0


def _build_theme_weights(holding_weights: dict[str, float] | None) -> dict[str, float]:
    """Aggregate per-theme weight from holdings (each theme inherits the fund weight);
    watchlist-only themes get a small base weight so they still surface."""
    w: dict[str, float] = {}
    if holding_weights:
        for code, fw in holding_weights.items():
            for theme in HOLDING_THEMES.get(code, []):
                w[theme] = w.get(theme, 0.0) + float(fw)
    for theme in WATCHLIST_THEMES:
        w[theme] = max(w.get(theme, 0.0), 0.05)
    return w


class RelevanceScorer:
    def __init__(self, holding_weights: dict[str, float] | None = None) -> None:
        self.theme_weights = _build_theme_weights(holding_weights)

    def score(self, text: str) -> RelevanceResult:
        t = text or ""
        themes_hit: list[str] = []
        kws_hit: list[str] = []
        seen_kw: set[str] = set()
        for theme, kws in THEME_KEYWORDS.items():
            hit_kw: list[str] = []
            for kw in kws:
                if kw in t and kw not in seen_kw:
                    hit_kw.append(kw)
                    seen_kw.add(kw)
            if hit_kw:
                themes_hit.append(theme)
                kws_hit.extend(hit_kw)
        score = sum(self.theme_weights.get(th, 0.0) for th in themes_hit)
        return RelevanceResult(score=score, themes=themes_hit, keywords=kws_hit)


def all_keywords() -> list[str]:
    out: list[str] = []
    for kws in THEME_KEYWORDS.values():
        out.extend(kws)
    return out
