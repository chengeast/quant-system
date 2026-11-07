"""M8 回测引擎：M3→M4→M5→M6→M7 端到端主循环 + 四件套 + 极端段 + 峰值/稳态双栏。"""

from src.backtest.engine import (
    BacktestConfig, Backtester, BacktestResult, LegAccount,
    compute_performance, check_gates,
)

__all__ = [
    "BacktestConfig", "Backtester", "BacktestResult", "LegAccount",
    "compute_performance", "check_gates",
]
