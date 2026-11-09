"""M8 回测引擎：M3→M4→M5→M6→M7 端到端主循环 + 四件套 + 极端段 + 峰值/稳态双栏。"""

from src.backtest.engine import (
    BacktestConfig, Backtester, BacktestResult, LegAccount,
    compute_performance, check_gates,
)
from src.decision_layer.mean_reversion_strategy import StrategyRules
from src.backtest.param_gate import (
    ParamCheckResult, check_params, ensure_params_for_backtest,
)

__all__ = [
    "BacktestConfig", "Backtester", "BacktestResult", "LegAccount",
    "StrategyRules",
    "compute_performance", "check_gates",
    "ParamCheckResult", "check_params", "ensure_params_for_backtest",
]
