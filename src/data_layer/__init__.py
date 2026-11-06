"""数据层：Gate.io K 线接入 + 清洗 + 对齐 + Parquet 落地。"""

from .gate_kline_fetcher import GateKLineFetcher, build_kline_fetcher

__all__ = ["GateKLineFetcher", "build_kline_fetcher"]
