"""M2 验收脚本：对候选币做币种对筛选，输出评分表，PM 仲裁定 1 对。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_layer import build_kline_fetcher
from src.signal_layer.pair_screener import screen_pairs, top_pairs, DEFAULT_CANDIDATES


def main():
    print("=" * 70)
    print("M2 币种对筛选  ·  Gate.io 1h 30天 · 候选:", DEFAULT_CANDIDATES)
    print("=" * 70)

    f = build_kline_fetcher(cache_dir=str(ROOT / "data" / "cache"))
    scores = screen_pairs(f, DEFAULT_CANDIDATES, interval="1h", lookback_days=30)
    if not scores:
        print("❌ 没有可评估的币对（候选币 K 线拉取失败或不足）")
        return 1

    print(f"\n共评估 {len(scores)} 个币对，按综合得分排序（Top {min(5,len(scores))}）：\n")
    hdr = f"{'排名':<4}{'币对':<18}{'corr':>6}{'EG_p':>8}{'半衰期(bar)':>12}  全过?"
    print(hdr)
    print("-" * len(hdr))
    for s in top_pairs(scores, 5):
        flag = "✅" if s.passes_all else "—"
        print(f"{s.rank:<4}{s.pair[0][:4]+'/'+s.pair[1][:4]:<18}"
              f"{s.corr:>6.3f}{s.eg_p:>8.4f}{s.half_life:>12.1f}  {flag}")

    # PM 仲裁：取第一个全过门槛的；若都未过，取 EG p 最小的并标注
    passing = [s for s in scores if s.passes_all]
    print("\n" + "=" * 70)
    if passing:
        chosen = passing[0]
        print(f"✅ PM 仲裁选定：{chosen.pair[0]} × {chosen.pair[1]}")
        print(f"   corr={chosen.corr:.3f}  EG_p={chosen.eg_p:.4f}  半衰期={chosen.half_life:.1f} bar")
    else:
        chosen = scores[0]
        print(f"⚠️  无币对通过全部门槛。取 EG p 最小者：{chosen.pair[0]} × {chosen.pair[1]}")
        print(f"   corr={chosen.corr:.3f}({'>=0.85' if chosen.corr>=0.85 else '<0.85'})"
              f"  EG_p={chosen.eg_p:.4f}({'<0.05' if chosen.eg_p<0.05 else '>=0.05'})"
              f"  半衰期={chosen.half_life:.1f}bar")
        print("   → 建议：扩大候选池 / 放宽阈值 / 等深历史数据后重筛")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
