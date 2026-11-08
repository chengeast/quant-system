"""精确定位 pnl=0：跟踪腿账本在开/平仓 bar 的 qty 变化。"""
import sys, warnings
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings('ignore')

from src.backtest.engine import Backtester, BacktestConfig
from src.core.market_ifaces import RegimeStatus


def main():
    cfg = BacktestConfig()
    bt = Backtester(cfg)
    sig = bt.load_signals()

    # 手动跑主循环的简化版，跟踪 leg_a.qty / leg_b.qty 在每笔 fill 后
    from src.backtest.engine import LegAccount
    leg_a, leg_b = LegAccount(), LegAccount()
    base = cfg.base_equity

    print('=== 前 30 笔 fill 的腿 qty 轨迹 ===')
    fills = []
    import itertools
    strat = bt.strat
    pos = None
    from src.decision_layer.mean_reversion_strategy import PositionSnapshot
    pos = PositionSnapshot(state="FLAT")
    pending = None
    for i in range(len(sig)):
        ts_ms = int(sig['ts_ms'].iloc[i])
        ca, cb = float(sig['close_a'].iloc[i]), float(sig['close_b'].iloc[i])
        z = sig['zscore'].iloc[i]
        state = str(sig['state'].iloc[i])
        if pending is not None:
            verdict = bt.risk_policy.gate(
                pending,
                bt._to_position(pos, base, bt._exposure(leg_a, leg_b)),
                bt._equity_now(base, leg_a, leg_b, ca, cb, 0.0),
                RegimeStatus.OK if state == 'OK' else RegimeStatus.INVALID,
            )
            if verdict.value == 'PASS':
                a_qt, b_qt = bt._effective_leg_qtys(pending, leg_a, leg_b)
                if a_qt > 0: leg_a.apply('buy', a_qt, ca)
                elif a_qt < 0: leg_a.apply('sell', abs(a_qt), ca)
                if b_qt > 0: leg_b.apply('buy', b_qt, cb)
                elif b_qt < 0: leg_b.apply('sell', abs(b_qt), cb)
                fills.append((i, ts_ms, pending.side, pending.reason,
                              round(leg_a.qty, 3), round(leg_b.qty, 3)))
                if len(fills) > 30:
                    break
        intent = strat.decide(sig.iloc[:i+1].tail(60), pos, ts_ms=ts_ms)
        if intent is not None:
            pending = intent
            if intent.side in ('buy','sell') and intent.qty > 0:
                pos = PositionSnapshot(state='LONG_SPREAD' if intent.symbol=='long_spread' else 'SHORT_SPREAD',
                                       entry_ts_ms=ts_ms, entry_zscore=0.0,
                                       position_qty=intent.qty,
                                       leg_a_qty=intent.leg_a_qty, leg_b_qty=intent.leg_b_qty)
            else:
                pos = PositionSnapshot(state='FLAT', entry_ts_ms=ts_ms, entry_zscore=0.0)

    for f in fills[:15]:
        print(f'  i={f[0]:4d} ts={f[1]} side={f[2]:4s} reason={f[3]:12s} legA={f[4]:8} legB={f[5]:8}')
    print(f'\n总 fill 次数: {len(fills)}')
    # 看 legA 是否真的非 0
    nonzero_a = sum(1 for f in fills if f[4] != 0)
    nonzero_b = sum(1 for f in fills if f[5] != 0)
    print(f'legA 非0 的 fill 次数: {nonzero_a}/{len(fills)}')
    print(f'legB 非0 的 fill 次数: {nonzero_b}/{len(fills)}')


if __name__ == '__main__':
    main()
