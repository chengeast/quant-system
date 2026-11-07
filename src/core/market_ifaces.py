"""
core · 市场无关数据契约 + 跨市场 5 接口 ABC（M4 前置）

设计依据：
  - agents/phase2-architecture.md §3 数据结构（后端架构师）
  - agents/phase4-multi-market-arch.md §4 五个"市场相关"接口
  - config/strategy_params.yaml（冻结参数）

架构原则（换市场不换骨架）：
  - 6 层骨架代码（信号/决策/对账/数据清洗/紧急停止）市场无关，复用
  - 真正分市场的是 5 个 ABC：MarketAdapter / HedgeLegResolver / RegimeGate /
    RiskPolicy / BrokerAdapter。每市场一份 YAML 声明 5 接口实现类路径，
    MarketRegistry 注入，骨架代码零改动
  - 当前 MVP 只做加密市场 1 套实现，其它市场留占位

MVP 阶段：
  - 5 接口 ABC + 数据契约 + MarketRegistry 骨架在此实现
  - 加密市场的具体 adapter 实现放各层目录下（data_layer/decision_layer/...）
"""

from __future__ import annotations

import hashlib
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import pandas as pd


# ============================================================================
# 市场无关核心数据契约（phase2-architecture.md §3 数据结构）
# ============================================================================

@dataclass(frozen=True)
class KLine:
    """标准化 K 线（数据层输出，市场无关）。"""
    ts_ms: int
    symbol: str
    interval: str
    open: float
    high: float
    low: float
    close: float
    vol: float


@dataclass
class Signal:
    """信号层输出（市场无关）。strength 0~1 策略归一化强度，供决策层消费。"""
    ts_ms: int
    pair: tuple[str, str]
    spread: float
    zscore: float
    hedge_ratio: float = 0.0
    half_life: float = 0.0
    adf_p: float = 1.0
    strength: float = 0.0
    state: str = "OK"             # OK / INVALID / THIN / NaN_GUARD
    metric: dict = field(default_factory=dict)

    @staticmethod
    def from_row(row: pd.Series, pair: tuple[str, str]) -> "Signal":
        """从 M3 信号层 DataFrame 的一行构造 Signal。"""
        return Signal(
            ts_ms=int(row.get("ts_ms", row.get("ts", 0))),
            pair=pair,
            spread=float(row.get("spread", 0.0)),
            zscore=float(row.get("zscore", 0.0)),
            hedge_ratio=float(row.get("hedge_ratio", 0.0)),
            half_life=float(row.get("half_life", 0.0)),
            adf_p=float(row.get("adf_p", 1.0)),
            strength=0.0,           # 决策层填充
            state=str(row.get("state", "OK")),
            metric={"close_a": float(row.get("close_a", 0.0)),
                    "close_b": float(row.get("close_b", 0.0))},
        )


@dataclass
class OrderIntent:
    """决策层输出（市场无关），含仓位上限与 TTL。"""
    order_id: str
    ts_ms: int
    symbol: str
    side: str            # "buy" / "sell" / "close"
    qty: float
    limit_price: float
    strategy_tag: str
    confidence: float
    ttl_ms: int
    pair: Optional[tuple[str, str]] = None
    hedge_ratio: float = 0.0
    leg_a_qty: float = 0.0
    leg_b_qty: float = 0.0
    reason: str = ""

    @classmethod
    def make(cls, ts_ms: int, symbol: str, side: str, qty: float,
             limit_price: float, strategy_tag: str, confidence: float,
             ttl_ms: int = 600_000, **kwargs: Any) -> "OrderIntent":
        order_id = kwargs.pop("order_id", None) or \
            f"{strategy_tag}-{ts_ms}-{uuid.uuid4().hex[:8]}"
        return cls(order_id=order_id, ts_ms=ts_ms, symbol=symbol, side=side,
                   qty=qty, limit_price=limit_price,
                   strategy_tag=strategy_tag, confidence=confidence,
                   ttl_ms=ttl_ms, **kwargs)


@dataclass
class Position:
    """持仓快照（市场无关）。"""
    ts_ms: int
    symbol: str
    qty: float
    avg_cost: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0


@dataclass
class FillReport:
    """成交回报（市场无关）。"""
    order_id: str
    ts_ms: int
    fill_qty: float
    fill_price: float
    fee: float
    latency_ms: int
    slippage_bps: float
    reason: str = ""


@dataclass
class ReconRecord:
    """对账记录（市场无关）。"""
    ts_ms: int
    account_id: str
    local: Position
    remote: Position
    diff: Any = None
    status: str = "OK"   # OK / DRIFT / ERROR


class RegimeStatus(str, Enum):
    """regime 状态（市场相关：协整断裂 / 换月 / 涨跌停等）。"""
    OK = "OK"
    BROKEN = "BROKEN"        # 协整断裂
    ROLLING = "ROLLING"      # 期货换月
    INVALID = "INVALID"      # 数据异常
    LOCKED = "LOCKED"        # 涨跌停锁死（A股）


class RiskVerdict(str, Enum):
    """风控裁决。"""
    PASS = "PASS"
    REJECT = "REJECT"
    HALT = "HALT"


@dataclass
class DepthBook:
    """订单簿深度（市场相关：各市场盘口结构不同）。"""
    symbol: str
    ts_ms: int
    bids: list          # [(price, qty), ...] 买盘 top N
    asks: list          # [(price, qty), ...] 卖盘 top N


# ============================================================================
# 5 个市场相关接口 ABC（phase4-multi-market-arch.md §4）
# ============================================================================

class MarketAdapter(ABC):
    """行情源 + 订单簿深度 + 交易源（市场相关）。"""

    @abstractmethod
    def fetch_kline(self, symbol: str, interval: str,
                    from_ts: int, to_ts: int) -> pd.DataFrame:
        """拉取 K 线，返回 DataFrame(ts_ms, open, high, low, close, volume)。"""
        ...

    @abstractmethod
    def fetch_depth(self, symbol: str, top: int = 5) -> DepthBook:
        """拉取订单簿深度 top N。MVP 可占位返回空 DepthBook。"""
        ...


class HedgeLegResolver(ABC):
    """对冲腿解析（市场相关：加密=直接腿；A股=ETF替代腿；期货=展期腿）。"""

    @abstractmethod
    def resolve(self, primary: str, signal: Signal) -> dict:
        """解析对冲腿映射，返回 {leg_a_qty, leg_b_qty, conversion_ratio}。"""
        ...


class RegimeGate(ABC):
    """regime/协整断裂检测（市场相关）。"""

    @abstractmethod
    def check(self, pair: tuple[str, str],
              coint_df: Optional[pd.DataFrame]) -> RegimeStatus:
        """检测 regime，返回 OK / BROKEN / ROLLING / INVALID / LOCKED。"""
        ...


class RiskPolicy(ABC):
    """市场风控（市场相关：涨跌停锁死校验 / 展期跳空熔断 / 24h 发散止损）。"""

    @abstractmethod
    def gate(self, intent: OrderIntent, position: Position,
             equity: float, book: Any) -> RiskVerdict:
        """风控闸门，返回 PASS / REJECT / HALT。"""
        ...


class BrokerAdapter(ABC):
    """撮合 / 滑点 / 保证金（市场相关：加密滑点 3-5bp；A股 0.3-0.5%；期货跳空+保证金）。"""

    @abstractmethod
    def execute(self, intent: OrderIntent) -> FillReport:
        """执行订单，返回成交回报。"""
        ...

    @abstractmethod
    def calc_slippage(self, intent: OrderIntent, book: DepthBook) -> float:
        """估算滑点（bps）。"""
        ...


# ============================================================================
# MarketConfig / MarketRegistry（phase4-multi-market-arch.md §4 MarketConfig/Registry）
# ============================================================================

@dataclass
class MarketConfig:
    """市场配置：声明 5 接口实现类 + 交易参数（每市场一份 YAML）。"""
    market_id: str
    display_name: str
    market_params: dict = field(default_factory=dict)
    # 5 接口实现类（模块路径 + 类名），由 MarketRegistry 动态导入
    market_adapter_cls: str = ""
    hedge_leg_resolver_cls: str = ""
    regime_gate_cls: str = ""
    risk_policy_cls: str = ""
    broker_adapter_cls: str = ""

    @classmethod
    def from_yaml(cls, path: str) -> "MarketConfig":
        """从 YAML 加载 MarketConfig（不依赖 yaml 库，简化解析 key: value 嵌套）。"""
        # MVP 简化：只解析一层 key: value + 嵌套 dict（用 json 风格）
        # 实际项目用 pyyaml；此处保持标准库可用
        import json
        import re
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        # 简易解析：去掉注释行，把 "key: value" 转 json dict
        lines = [ln for ln in text.split("\n")
                 if ln.strip() and not ln.strip().startswith("#")]
        cfg_lines = "\n".join(lines)
        # 把 YAML 的 "key: value" 转成 JSON 风格（值包引号）
        json_text = re.sub(r'^(\s*)(\w+):\s*(.+)$',
                           lambda m: f'{m.group(1)}"{m.group(2)}": "{m.group(3)}"',
                           cfg_lines, flags=re.MULTILINE)
        json_text = re.sub(r",\s*([}\]])", r"\1", json_text)  # 去尾逗号
        try:
            data = json.loads("{" + json_text + "}")
        except Exception:
            data = {}
        return cls(
            market_id=data.get("market_id", "unknown"),
            display_name=data.get("display_name", data.get("market_id", "unknown")),
            market_params=data.get("market_params", {}),
            market_adapter_cls=data.get("market_adapter_cls", ""),
            hedge_leg_resolver_cls=data.get("hedge_leg_resolver_cls", ""),
            regime_gate_cls=data.get("regime_gate_cls", ""),
            risk_policy_cls=data.get("risk_policy_cls", ""),
            broker_adapter_cls=data.get("broker_adapter_cls", ""),
        )


class MarketRegistry:
    """市场注册表：register / load 5 接口实现 + 骨架代码通过此注入。"""

    def __init__(self) -> None:
        self._configs: dict[str, MarketConfig] = {}
        self._instances: dict[str, dict] = {}

    def register(self, market_id: str, cfg: MarketConfig) -> None:
        """注册市场配置（不实例化，仅登记）。"""
        self._configs[market_id] = cfg

    def load(self, market_id: str) -> dict:
        """实例化 5 接口，返回 dict（key=接口名，value=实例）。"""
        if market_id not in self._configs:
            raise KeyError(f"market '{market_id}' 未注册")
        cfg = self._configs[market_id]
        import importlib
        instances: dict[str, Any] = {"_config": cfg}
        for attr in ("market_adapter_cls", "hedge_leg_resolver_cls",
                     "regime_gate_cls", "risk_policy_cls", "broker_adapter_cls"):
            cls_path = getattr(cfg, attr)
            if cls_path and ":" in cls_path:
                mod_path, cls_name = cls_path.split(":")
                mod = importlib.import_module(mod_path)
                cls = getattr(mod, cls_name)
                key = attr.replace("_cls", "")
                instances[key] = cls()
        self._instances[market_id] = instances
        return instances

    def get_config(self, market_id: str) -> MarketConfig:
        if market_id not in self._configs:
            raise KeyError(f"market '{market_id}' 未注册")
        return self._configs[market_id]


__all__ = [
    "KLine", "Signal", "OrderIntent", "Position", "FillReport", "ReconRecord",
    "RegimeStatus", "RiskVerdict", "DepthBook",
    "MarketAdapter", "HedgeLegResolver", "RegimeGate", "RiskPolicy", "BrokerAdapter",
    "MarketConfig", "MarketRegistry",
]
