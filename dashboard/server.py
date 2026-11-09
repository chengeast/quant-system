"""交互面板 · 本地后端（零新依赖，仅 Python 标准库 http.server）

职责：把 backtest/run_strategy_with_rules.py 的能力暴露成 HTTP API，
供前端交互面板（dashboard/run_console.html）调用，实现「改参数 → 启动回测 → 看结果」全在界面完成。

接口：
  GET  /                          → 返回 run_console.html（若存在）
  GET  /api/config                → 返回当前默认 StrategyRules + 可用周期 + 币对 + 数据可用性
  POST /api/run                   → 接收参数 {entry, tp, sl, timeout_hours, ...}，
                                    跑 5 周期，返回 {results:[...], rules:{...}, passed:[...]}
  GET  /api/status                → 返回 4h/1d 真实数据是否已拉取、缓存文件状态

启动（双击或命令行）：
  python dashboard/server.py            # 默认 8890 端口
  python dashboard/server.py --port 9000

说明：这是研究/回测系统，不含任何实盘下单能力。
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import warnings
warnings.filterwarnings("ignore")

from backtest.run_strategy_with_rules import (
    LEG_A, LEG_B, BENCH, CACHE, GATE,
    run_one_timeframe, _rules_dict, _gate_summary,
)
from src.decision_layer.mean_reversion_strategy import StrategyRules


def _data_availability() -> Dict[str, Dict[str, Any]]:
    """返回每个周期数据可用性（实拉 / 1h 聚合回退 / 缺失）。"""
    def _f(name: str) -> bool:
        return (Path(CACHE) / name).exists()

    avail: Dict[str, Dict[str, Any]] = {}
    avail["15m"] = {"ok": _f(f"{LEG_A}_USDT_15m_95d.parquet"), "source": "实拉(95d)"}
    avail["30m"] = {"ok": _f(f"{LEG_A}_USDT_30m_195d.parquet"), "source": "实拉(195d)"}
    avail["1h"] = {"ok": _f(f"{LEG_A}_USDT_1h_410d.parquet"), "source": "实拉(410d)"}
    avail["4h"] = {"ok": _f(f"{LEG_A}_USDT_4h_400d.parquet"),
                   "source": "实拉(400d)" if _f(f"{LEG_A}_USDT_4h_400d.parquet") else "1h聚合回退"}
    avail["1d"] = {"ok": _f(f"{LEG_A}_USDT_1d_900d.parquet"),
                   "source": "实拉(900d)" if _f(f"{LEG_A}_USDT_1d_900d.parquet") else "1h聚合回退"}
    return avail


def _default_rules() -> StrategyRules:
    return StrategyRules(
        entry_sigma=3.0,
        take_profit_sigma_from_entry=2.5,
        stop_loss_sigma_from_entry=2.0,
        timeout_bars=0,
        timeout_hours=3.0,
        timeout_half_life_factor=1.2,
        position_cap=0.05,
    )


def _build_rules(payload: Dict[str, Any]) -> StrategyRules:
    return StrategyRules(
        entry_sigma=float(payload.get("entry", 3.0)),
        take_profit_sigma_from_entry=float(payload.get("tp", 2.5)),
        stop_loss_sigma_from_entry=float(payload.get("sl", 2.0)),
        timeout_bars=int(payload.get("timeout_bars", 0)),
        timeout_hours=float(payload.get("timeout_hours", 3.0)),
        timeout_half_life_factor=float(payload.get("timeout_hl_factor", 1.2)),
        position_cap=float(payload.get("leg_eq_pct", 0.05)),
    )


def _to_native(v: Any) -> Any:
    """把 numpy 标量 / 数组安全转成 JSON 原生类型（避免 default=str 把
    np.bool_ 序列化成字符串 'False'/'True'，使前端 gate 判定失效）。"""
    import numpy as np
    if isinstance(v, dict):
        return {k: _to_native(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_native(x) for x in v]
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    if isinstance(v, np.ndarray):
        return [_to_native(x) for x in v.tolist()]
    return v


def _run(payload: Dict[str, Any]) -> Dict[str, Any]:
    """跑 5 周期，返回前端可渲染的 JSON。"""
    rules = _build_rules(payload)
    # 1d 实拉天数：payload 指定则用，默认探测 900
    real_1d = int(payload.get("real_1d_days", 900))
    real_4h = int(payload.get("real_4h_days", 400))
    timeframes = payload.get("timeframes") or ["15m", "30m", "1h", "4h", "1d"]

    results = []
    passed = []
    for tf in timeframes:
        try:
            r = run_one_timeframe(tf, rules, CACHE, LEG_A, LEG_B, BENCH, GATE,
                                  real_4h_days=real_4h,
                                  real_1d_days=real_1d if tf == "1d" else None)
            m = _to_native(dict(r.metrics))
            m["gate_pass"] = _gate_summary(m, GATE)
            results.append({
                "timeframe": tf,
                "data_bars": r.data_bars,
                "n_trades": r.n_trades,
                "source": r.source.get(tf, "?"),
                "metrics": m,
                "errors": r.errors,
            })
            if m.get("gate_pass") and all(m["gate_pass"].values()):
                passed.append(tf)
        except Exception as e:
            results.append({
                "timeframe": tf, "data_bars": 0, "n_trades": 0,
                "source": "error", "metrics": {"error": str(e)},
                "errors": [str(e)],
            })
    return {
        "rules": _rules_dict(rules),
        "results": results,
        "passed": passed,
        "data": _data_availability(),
        "leg_a": LEG_A, "leg_b": LEG_B, "benchmark": BENCH,
        "gate": GATE,
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a) -> None:  # 静默
        pass

    def do_OPTIONS(self) -> None:
        self._send(204, b"")

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html", "/run_console.html"):
            p = ROOT / "dashboard" / "run_console.html"
            if p.exists():
                self._send(200, p.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(200,
                           b"run_console.html missing. Build it via frontend agent first.",
                           "text/plain")
        elif self.path == "/api/config":
            cfg = {
                "default_rules": _rules_dict(_default_rules()),
                "timeframes": ["15m", "30m", "1h", "4h", "1d"],
                "leg_a": LEG_A, "leg_b": LEG_B, "benchmark": BENCH,
                "gate": GATE,
                "data": _data_availability(),
            }
            self._send(200, json.dumps(cfg, ensure_ascii=False).encode())
        elif self.path == "/api/status":
            self._send(200, json.dumps({"data": _data_availability()},
                                       ensure_ascii=False).encode())
        else:
            self._send(404, json.dumps({"error": "not found"}).encode())

    def do_POST(self) -> None:
        if self.path != "/api/run":
            self._send(404, json.dumps({"error": "not found"}).encode())
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            out = _run(payload)
            self._send(200, json.dumps(out, ensure_ascii=False, default=str).encode())
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)},
                                       ensure_ascii=False).encode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8890)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("=" * 60)
    print(f"交互面板后端已启动：http://{args.host}:{args.port}")
    print("接口：/ (前端面板)  /api/config  /api/run  /api/status")
    print(f"数据可用性：{_data_availability()}")
    print("按 Ctrl+C 停止。")
    print("=" * 60)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
