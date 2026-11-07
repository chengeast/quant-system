"""M2 重筛：410 天深数据下重新筛选币种对，输出评分表。"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_layer import build_kline_fetcher
from src.signal_layer.pair_screener import screen_pairs, top_pairs, DEFAULT_CANDIDATES

f = build_kline_fetcher()
print("候选:", DEFAULT_CANDIDATES, flush=True)
scores = screen_pairs(f, DEFAULT_CANDIDATES, interval="1h", lookback_days=410)
print(f"共评估 {len(scores)} 币对（410 天 1h 深数据）", flush=True)
hdr = f"{'#':<3}{'币对':<16}{'corr':>7}{'EG_p':>9}{'半衰期':>9}  全过?"
print(hdr)
print("-" * len(hdr))
for s in top_pairs(scores, 10):
    mark = "✅" if s.passes_all else "—"
    print(f"{s.rank:<3}{s.pair[0][:3]+'/'+s.pair[1][:3]:<16}"
          f"{s.corr:>7.3f}{s.eg_p:>9.4f}{s.half_life:>9.1f}  {mark}", flush=True)
passing = [s for s in scores if s.passes_all]
print("\n" + "=" * 60, flush=True)
if passing:
    c = passing[0]
    print(f"PM 选定: {c.pair[0]} × {c.pair[1]}  corr={c.corr:.3f} "
          f"EG_p={c.eg_p:.4f} 半衰期={c.half_life:.1f}bar", flush=True)
else:
    print("⚠ 无币对全过门槛，EG p 最小 Top3:", flush=True)
    for s in top_pairs(scores, 3):
        print(f"  {s.pair} corr={s.corr:.3f} EG_p={s.eg_p:.4f} HL={s.half_life:.1f}", flush=True)
