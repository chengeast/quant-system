"""HEAD 基线 M5 verdict 诊断（一次性，确认 8 笔成交的真瓶颈）。

不 patch 方法（避免 self 递归），直接读 RiskGate.reject_log + 复跑统计：
  - 拒单按 risk_category 分布（exposure / regime）
  - 拒单 reason 样例
  - M3 state!=OK bar 占比（h 越界 + NaN_GUARD）
  - 逐笔 pnl
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import warnings
warnings.filterwarnings('ignore')

from collections import Counter
import pandas as pd
from src.backtest.engine import Backtester, BacktestConfig


def main():
    cfg = BacktestConfig()
    bt = Backtester(cfg)
    sig = bt.load_signals()
    res = bt.run(sig)

    rg = bt.risk_policy.risk_gate
    log = rg.get_reject_log()

    print('HEAD M5 reject_log 总条数:', len(log))
    print('按 risk_category:', dict(Counter(x.risk_category for x in log)))
    print('拒单 reason 样例:')
    seen = set()
    for x in log:
        if x.risk_category not in seen:
            seen.add(x.risk_category)
            i = x.intent
            print(f'  [{x.risk_category}] {x.reason[:60]}  '
                  f'legA={i.leg_a_qty:.4f} legB={i.leg_b_qty:.4f} '
                  f'px={i.limit_price:.2f} qty={i.qty:.4f}')

    print('\n成交笔数:', len(res.trades))
    pnls = [t['pnl'] for t in res.trades]
    print('逐笔 pnl:', [round(p, 2) for p in pnls])
    print('合计 pnl = %.2f' % sum(pnls))

    inv = int((sig['state'] != 'OK').sum())
    print('\nM3 state!=OK bar:', inv, '/', len(sig),
          '= %.1f%%' % (inv / len(sig) * 100))
    # h 越界占比
    hoor = int(sig.get('h_out_range', pd.Series()).sum()) if 'h_out_range' in sig.columns else -1
    if hoor >= 0:
        print('M3 h越界(h_out_range) bar:', hoor, '/', len(sig),
              '= %.1f%%' % (hoor / len(sig) * 100))

    eq = res.equity_curve.dropna()
    base = cfg.base_equity
    print('\n净值 base=%.0f min=%.2f max=%.2f last=%.2f'
          % (base, eq.min(), eq.max(), eq.iloc[-1]))
    m = res.metrics
    for k in ('net_return_peak', 'net_return_steady', 'max_drawdown',
              'sharpe', 'calmar', 'n_trades', 'cost_ratio', 'win_rate'):
        if k in m:
            print('  %s: %s' % (k, m[k]))


if __name__ == '__main__':
    main()
