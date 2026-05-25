from .base import FundDataSource
from .composite import CompositeDataSource

__all__ = ["FundDataSource", "CompositeDataSource"]


def build_default(cfg) -> FundDataSource:
    """Factory built from AppConfig (lazy import to keep optional deps optional)."""
    from .eastmoney import EastmoneyDataSource
    from .tiantian import TiantianDataSource

    name_map: dict[str, type[FundDataSource]] = {
        "eastmoney": EastmoneyDataSource,
        "tiantian":  TiantianDataSource,
    }
    try:
        from .akshare_src import AkShareDataSource
        name_map["akshare"] = AkShareDataSource
    except Exception:
        pass

    sources: list[FundDataSource] = []
    primary = cfg.datasource.primary
    if primary in name_map:
        sources.append(name_map[primary]())
    for fb in cfg.datasource.fallback:
        if fb in name_map and not any(isinstance(s, name_map[fb]) for s in sources):
            sources.append(name_map[fb]())
    if not sources:
        raise RuntimeError(f"no usable data source for primary={primary}")
    if len(sources) == 1:
        return sources[0]
    return CompositeDataSource(sources)
