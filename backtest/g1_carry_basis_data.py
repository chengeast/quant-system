# -*- coding: utf-8 -*-
"""G-3 · G1 carry 基差信号数据准备（落盘 parquet + 指纹，供回测脚本读取）。

窗口口径（G-3 任务单 + G-1 phase9 §二/§六）：
  现货 400d/900d ∩ funding 180d = 180d（4h=1080 根、1d=180 根）
  实际可用：funding_rate API 实得 100 根/币（G-3 2026-11-12 实拉，分页上限
  100 根/次，from=180d/170d 请求均被截断到最近 ~33d 窗口）→
  实际交集窗口 = 33d（4h 网格 ≈ 98~100 根、1d = 34 根）。
  任务单 180d 口径下 4h=1080 根/1d=180 根，其中超出实得 100 根 funding
  的部分无数据 → 本脚本只产出实得交集（如实申报，不脑补、不补 0）。

数据来源（全部落盘可回读）：
  现货 4h：data/cache/{SYM}_USDT_4h_400d.parquet（2401 根/400d，无缺口）
  现货 1d：data/cache/{SYM}_USDT_1d_900d.parquet（901 根/900d）
  永续 K线：agents/_g1_probes/perp_{ll}_usdt_4h_28d_g3.json（203 根）/
            perp_{ll}_usdt_1d_31d_g3.json（35 根）（G-3 实拉，funding 窗口对齐）
  funding ：agents/_g1_probes/fr_{ll}_usdt_169d_g3.json（100 根/币，8h 结算点
            00/08/16 UTC，实际覆盖 2026-10-10 ~ 2026-11-12）

网格对齐（防未来函数，复用 M8 t 出信号 / t+1 成交口径）：
  4h 网格：现货 4h parquet 的 ts（Gate 4h K线 00/04/.../20 整点，与 funding
           结算点 00/08/16 对齐，bar 收盘时刻即下一根开盘）
  1d 网格：funding ts 序列（00:00 UTC）
  4h 行：funding ∩ 永续 4h 内连接；现货 4h close 取该 bar 收盘（无未来函数）
  1d 行：funding ts 内连接；现货 1d close = 当日 close；永续 1d close 缺失
          时该 bar basis 记 NaN（如实，不脑补）

产物：
  data/cache/g1_carry_data/{SYM}_USDT_4h_400d_carry.parquet
  data/cache/g1_carry_data/{SYM}_USDT_1d_900d_carry.parquet
  列：ts, spot_open, spot_close, perp_open, perp_close,
      basis, basis_pct, funding_rate, basis_pos, fr_pos
  另写 data/cache/g1_carry_data/_manifest.json（SHA256 指纹 + 窗口 + 数据源）

本脚本不改 src/、不改 config/、不提交 Git。
运行：python backtest/g1_carry_basis_data.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
PROBES = ROOT / "agents" / "_g1_probes"
OUT_DIR = CACHE / "g1_carry_data"

COINS = ["BTC", "ETH", "DOGE", "LTC"]

# 任务单标称口径（180d ∩ 现货 400d/900d）：4h=1080 根、1d=180 根
NOMINAL_BARS = {"4h": 1080, "1d": 180}


def _sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def load_funding(coin: str) -> tuple[pd.DataFrame, Path]:
    p = PROBES / f"fr_{coin.lower()}_usdt_169d_g3.json"
    rows = json.loads(p.read_text())
    df = pd.DataFrame(rows)
    df["funding_rate"] = df["r"].astype(float)
    df["ts"] = df["t"].astype("int64")
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return df[["ts", "funding_rate"]], p


def load_perp(coin: str, iv: str) -> tuple[pd.DataFrame, Path]:
    fn = {"4h": f"perp_{coin.lower()}_usdt_4h_28d_g3.json",
          "1d": f"perp_{coin.lower()}_usdt_1d_31d_g3.json"}[iv]
    p = PROBES / fn
    rows = json.loads(p.read_text())
    # Gate 永续 K线：dict {t,v,c,h,l,o,sum}（见 phase9 §三 数据字典）
    df = pd.DataFrame(rows)
    df = df[["t", "o", "c"]].rename(
        columns={"t": "ts", "o": "perp_open", "c": "perp_close"})
    df["ts"] = df["ts"].astype("int64")
    for c in ("perp_open", "perp_close"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df = df.dropna().drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return df, p


def load_spot(coin: str, iv: str) -> tuple[pd.DataFrame, Path]:
    p = CACHE / f"{coin}_USDT_{iv}_{'400' if iv == '4h' else '900'}d.parquet"
    df = pd.read_parquet(p)
    df = df[["ts", "open", "close"]].rename(
        columns={"open": "spot_open", "close": "spot_close"}).copy()
    df["ts"] = df["ts"].astype("int64")
    return df, p


def build_candle(coin: str, iv: str) -> tuple[pd.DataFrame, dict]:
    """按 iv 网格（4h=现货 4h ts、1d=funding ts）内连接 funding/永续/现货。"""
    fr, fr_p = load_funding(coin)
    if iv == "4h":
        spot, spot_p = load_spot(coin, "4h")
        grid = spot[["ts"]]                      # 4h 现货网格（funding 结算点子集）
    else:
        spot, spot_p = load_spot(coin, "1d")
        grid = fr[["ts"]]                        # 1d 网格 = funding ts（00:00 UTC）

    m = grid.merge(fr, on="ts", how="inner")      # 4h：funding ∩ 4h 网格；1d：funding 自身
    perp, perp_p = load_perp(coin, iv)
    m = m.merge(perp, on="ts", how="left")        # 永续缺失 → basis NaN（如实）
    m = m.merge(spot[["ts", "spot_open", "spot_close"]], on="ts", how="left")
    m = m.dropna(subset=["spot_open", "spot_close", "funding_rate"]).reset_index(drop=True)

    m["basis"] = m["perp_close"] - m["spot_close"]
    m["basis_pct"] = m["basis"] / m["spot_close"]
    m["basis_pos"] = m["basis"] > 0               # perp_close NaN → False（如实，不开仓）
    m["fr_pos"] = m["funding_rate"] > 0
    keep = ["ts", "spot_open", "spot_close", "perp_open", "perp_close",
            "basis", "basis_pct", "funding_rate", "basis_pos", "fr_pos"]
    m = m[keep]

    out_p = OUT_DIR / f"{coin}_USDT_{iv}_{'400' if iv == '4h' else '900'}d_carry.parquet"
    m.to_parquet(out_p, index=False)
    span_days = round((m["ts"].max() - m["ts"].min()) / 86400)
    manifest = {
        "coin": coin, "iv": iv,
        "bars": int(len(m)),
        "nominal_bars_180d": NOMINAL_BARS[iv],    # 任务单标称 180d 口径
        "span_days": span_days,
        "ts_min": int(m["ts"].min()), "ts_max": int(m["ts"].max()),
        "window_note": ("funding 实得 100 根/币（~33d）→ 实际交集 < 180d 标称；"
                        "超出实得 funding 区间的 bar 无数据，未脑补"),
        "out_file": str(out_p.relative_to(ROOT)), "out_sha16": _sha16(out_p),
        "sources": {
            "spot": {"file": f"{iv}_{'400' if iv=='4h' else '900'}d",
                     "sha16": _sha16(spot_p)},
            "perp": {"file": perp_p.name, "sha16": _sha16(perp_p)},
            "funding": {"file": fr_p.name, "sha16": _sha16(fr_p),
                        "bars": int(len(fr)),
                        "span_days": int((fr['ts'].max() - fr['ts'].min()) / 86400)},
        },
        "basis_pos_frac": round(float(m["basis_pos"].sum()), 4),
        "fr_pos_frac": round(float(m["fr_pos"].sum()), 4),
        "fr_mean": float(m["funding_rate"].mean()),
    }
    return m, manifest


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifests = []
    for coin in COINS:
        for iv in ("4h", "1d"):
            df, man = build_candle(coin, iv)
            manifests.append(man)
            n_bp = int(man["basis_pos_frac"])
            n_fr = int(man["fr_pos_frac"])
            print(f"{coin} {iv}: {man['bars']} bars / {man['span_days']}d "
                  f"(标称180d={man['nominal_bars_180d']}) "
                  f"basis+={n_bp} fr+={n_fr} → {man['out_file']}")
    (OUT_DIR / "_manifest.json").write_text(
        json.dumps(manifests, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nmanifest → {OUT_DIR / '_manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
