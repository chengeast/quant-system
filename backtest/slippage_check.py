# -*- coding: utf-8 -*-
"""滑点实测：Gate.io 公开订单簿深度接口（GET /spot/order_book?currency_pair=X_USDT&limit=1000）
模拟 1000 USDT 单边买入（吃 ask），计算成交均价 vs 中间价的滑点 bp。

4 币：BCH / LTC / AVAX / ADA
归档 backtest/output/pair_slippage_check.md

局限：滑点是实时点测量（当前盘口），非历史 4h 收盘 bar 订单簿；
4h/1d 中低频每笔 1000u 通常不触发深度墙，当前盘口近似可接受，但需注明。
"""
import json
import time
import urllib.request
import datetime
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COINS = ["BCH", "LTC", "AVAX", "ADA"]
ORDER_USDT = 1000.0
GATE_BASE = "https://api.gateio.ws/api/v4"
UA = {"User-Agent": "Mozilla/5.0 (quant-system)"}


def fetch_order_book(pair: str, limit: int = 1000):
    """拉取当前订单簿（公开接口，无需签名）。返回 (asks, bids, timestamp)。"""
    url = f"{GATE_BASE}/spot/order_book?currency_pair={pair}&limit={limit}"
    last_exc = None
    for attempt in range(3):
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read().decode())
            return d["asks"], d["bids"], d.get("timestamp", int(time.time()))
        except Exception as e:
            last_exc = e
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"订单簿拉取失败 {pair}: {last_exc}")


def simulate_fill(asks, usdt_amount):
    """模拟在 ask 侧花 usdt_amount 买入（吃多档），返回 (成交均价, 吃到档数)。
    asks 格式: [[price, size], ...]  价格为 USDT，size 为基础币数量。"""
    remaining = usdt_amount
    total_base = 0.0
    levels_hit = 0
    for price_s, size_s in asks:
        p = float(price_s)
        s = float(size_s)
        # 该档能买多少基础币
        if remaining <= 0:
            break
        # 该档全部 USDT 容量 = p * s
        level_usdt = p * s
        take_usdt = min(remaining, level_usdt)
        base_this_level = take_usdt / p
        total_base += base_this_level
        remaining -= take_usdt
        levels_hit += 1
        if remaining <= 1e-9:
            break
    if total_base <= 0:
        return None, 0
    avg_price = (usdt_amount - remaining) / total_base  # 实际成交均价
    return avg_price, levels_hit


def main():
    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    rows = []
    for sym in COINS:
        pair = f"{sym}_USDT"
        try:
            asks, bids, ts = fetch_order_book(pair)
            # 中间价 = (best_ask + best_bid) / 2
            best_ask = float(asks[0][0])
            best_bid = float(bids[0][0])
            mid = (best_ask + best_bid) / 2.0
            avg_px, levels = simulate_fill(asks, ORDER_USDT)
            if avg_px is None:
                rows.append({"sym": sym, "mid": mid, "bp": None, "levels": 0, "err": "深度不足"})
                continue
            slippage_bp = (avg_px - mid) / mid * 10_000  # bp
            rows.append({
                "sym": sym, "mid": round(mid, 4), "bp": round(slippage_bp, 3),
                "levels": levels, "ts": ts,
                "pass_8bp": slippage_bp <= 8.0,
                "pass_15bp": slippage_bp <= 15.0,
            })
            print(f"{pair}: mid={mid:.4f}  avg_buy={avg_px:.4f}  levels={levels}  "
                  f"slip={slippage_bp:.3f}bp  8bp={'PASS' if slippage_bp<=8 else 'FAIL'}  "
                  f"15bp={'PASS' if slippage_bp<=15 else 'FAIL'}  ts={ts}")
        except Exception as e:
            print(f"{pair}: 失败 {e}")
            rows.append({"sym": sym, "mid": None, "bp": None, "levels": 0, "err": str(e)})
        time.sleep(1)

    # ---------- 写报告 ----------
    report = []
    report.append("# 滑点实测（订单簿深度）\n")
    report.append(f"拉取时间：{now_str}\n")
    report.append("方法：Gate.io 公开订单簿接口 GET /spot/order_book（limit=1000），"
                 "模拟 1000 USDT 单边买入（吃 ask 多档），成交均价 vs 中间价（best_ask+best_bid)/2，"
                 "滑点 = (avg - mid) / mid × 10000 bp。\n")
    report.append("门槛：C 专家 P0 币（BCH/LTC）要求 ≤8bp/边；P1/P2 币（AVAX/ADA）要求 ≤15bp/边。\n")
    report.append("## 结果\n")
    header = f"{'币':<6} | {'中间价':>10} | {'滑点(bp)':>10} | {'吃到档数':>8} | {'≤8bp':>6} | {'≤15bp':>6} | {'盘口ts':>14}"
    report.append(header)
    report.append("-" * len(header))
    for r in rows:
        if r["bp"] is None:
            report.append(f"{r['sym']:<6} | {r.get('err','?'):>10} | - | - | - | - | -")
            continue
        report.append(
            f"{r['sym']:<6} | {r['mid']:>10.4f} | {r['bp']:>10.3f} | {r['levels']:>8} | "
            f"{'✓' if r['pass_8bp'] else '✗':>6} | {'✓' if r['pass_15bp'] else '✗':>6} | "
            f"{r.get('ts','')}"
        )
    report.append("")
    report.append("## 局限\n")
    report.append("- 滑点为**实时点测量**（拉取时刻当前盘口），非历史 4h 收盘 bar 时刻的订单簿；"
                  "Gate.io 历史订单簿接口不可得。")
    report.append("- 1000 USDT 为 MVP 单笔量级；4h/1d 中低频下该量级通常不触发深度墙，"
                  "当前盘口近似可接受，但市场剧烈行情下深度会漂移。")
    report.append("- 未计成交时延（C 专家假设 1~3s）与做市商抢跑折扣（0.5x 毛利），仅做盘口滑点。")
    report.append("")
    report.append("## 结论（仅数据）\n")
    for r in rows:
        if r["bp"] is None:
            report.append(f"- {r['sym']}: {r.get('err','拉取失败')}")
            continue
        p8 = "过" if r["pass_8bp"] else "不过"
        p15 = "过" if r["pass_15bp"] else "不过"
        pri = "P0(BCH/LTC) 门槛8bp" if r["sym"] in ("BCH","LTC") else "P1/P2(AVAX/ADA) 门槛15bp"
        report.append(f"- {r['sym']}: 滑点 {r['bp']}bp —— {pri} {p8}；15bp {p15}")

    os.makedirs(os.path.join(ROOT, "backtest", "output"), exist_ok=True)
    out_md = os.path.join(ROOT, "backtest", "output", "pair_slippage_check.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"\nwritten: {out_md}")

    # 落 json 供程序读
    out_json = os.path.join(ROOT, "backtest", "output", "pair_slippage_check.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": now_str, "order_usdt": ORDER_USDT, "rows": rows},
                  f, ensure_ascii=False, indent=2)
    print(f"written: {out_json}")


if __name__ == "__main__":
    main()
