#!/usr/bin/env python
"""
统计套利量化系统 · 本地统一运行入口（离线回测 / 验收 / 看板）

一条命令出结果：
    python run.py                 # 环境自检 → M8 回测报告 → 重渲染看板

子命令：
    python run.py check           # 只自检：依赖 / 数据 / 参数指纹 / 当前结论
    python run.py backtest        # 只跑 M8 回测（golden value / series / 报告）
    python run.py dashboard       # 只重渲染看板（直接用已有 series.json）
    python run.py acceptance      # 跑全部验收 M1–M9 + 周期参数化专项（约 6 分钟）
    python run.py fetch           # 重新拉取 5 币 410 天 1h 数据（需 .env 只读 KEY + 联网）

边界（务必知悉）：
  * 这是「研究 / 回测系统」，不是可下单的实盘系统。live_trading.enabled=false，
    第三轮 3:0 裁决已关闭 1h 统计套利的上线评估分支，本入口不会下任何真实订单。
  * 数据与产物不进 Git（.gitignore：data/cache/、backtest/output/、.env）。
    本机已经齐备；换一台机器需先 `python run.py fetch` 或拷贝 data/cache/。
  * 看板 dashboard/index.html 是离线单文件（原生 SVG，无 CDN、无构建步骤），
    直接双击即可在浏览器打开。
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CACHE = ROOT / "data" / "cache"
OUT = ROOT / "backtest" / "output"
DASHBOARD = ROOT / "dashboard" / "index.html"
SYMS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
DATA_FILES = [CACHE / f"{s}_USDT_1h_410d.parquet" for s in SYMS]
REQUIRED_MODULES = ("pandas", "numpy", "pyarrow", "statsmodels", "scipy")
ACCEPTANCE = tuple(f"backtest/m{n}_acceptance.py" for n in range(1, 10)) + (
    "backtest/timeframe_acceptance.py",
)


def _invoke(script: str, *args, tail: int = 0):
    """在项目根目录下调用一个已有脚本；tail>0 时只打印末尾若干行。"""
    cmd = [sys.executable, str(ROOT / script), *map(str, args)]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    shown = lines[-tail:] if tail else lines
    if shown:
        print("\n".join(shown))
    if r.returncode != 0:
        print(f"  ⚠️ {script} 退出码 {r.returncode}")
        if r.stderr:
            print((r.stderr or "")[-1200:])
    return r, lines


def check() -> bool:
    """环境自检 + 数据自检 + 参数指纹 + 当前结论摘要。"""
    print("=" * 72)
    print("环境自检")
    print("=" * 72)
    print(f"Python: {sys.version.split()[0]}   工作目录: {ROOT}")

    missing_mods = []
    for mod in REQUIRED_MODULES:
        try:
            m = importlib.import_module(mod)
            print(f"  ✅ {mod:<12s} {getattr(m, '__version__', '')}")
        except ImportError:
            missing_mods.append(mod)
            print(f"  ❌ {mod:<12s} 未安装")
    if missing_mods:
        print("  → 修复：python -m pip install -r requirements.txt")

    print("\n" + "-" * 72)
    print("数据自检（410 天 1h K 线，5 币含 BTC 基准）")
    print("-" * 72)
    missing_files = []
    for p in DATA_FILES:
        if p.exists():
            print(f"  ✅ {p.name:<32s} {p.stat().st_size / 1024:8.1f} KB")
        else:
            missing_files.append(p)
            print(f"  ❌ {p.name:<32s} 缺失")
    if missing_files:
        print("\n  ⚠️ 这些文件不进 Git（体量大），换机器后需先准备数据，二选一：")
        print("     ① 把本机 data/cache/ 整个目录拷到新机器同路径（最快、离线）")
        print("     ② 配好 .env 的 Gate.io 只读 KEY 后执行：python run.py fetch")

    print("\n" + "-" * 72)
    print("参数指纹（M9-a 门禁：改参数未留痕即阻断回测）")
    print("-" * 72)
    _invoke("config/verify_params.py", tail=2)

    print("\n" + "-" * 72)
    print("当前回测结论（若尚未跑过回测则显示为空）")
    print("-" * 72)
    gv = OUT / "golden_value.json"
    if gv.exists():
        d = json.loads(gv.read_text(encoding="utf-8"))
        m = d.get("metrics", {})
        print(f"  周期 {d.get('interval', '?')} ｜ {int(m.get('bars', 0))} bar ｜ "
              f"{int(m.get('n_trades', 0))} 笔成交 ｜ {d.get('annualize_label', '')}")
        print(f"  净收益(峰值) {m.get('total_return', 0) * 100:+.2f}% ｜ "
              f"稳态 {m.get('steady_total_return', 0) * 100:+.2f}% ｜ "
              f"最大回撤 {m.get('max_drawdown', 0) * 100:.2f}%")
        print(f"  夏普 {m.get('sharpe', 0):.3f} ｜ Calmar {m.get('calmar', 0):.3f} ｜ "
              f"成本可执行 {'是' if m.get('cost_executable') else '否'}")
        # 门槛判定直接从产物读取，避免展示文案与回测结果脱节
        g = d.get("gates", {})
        def _flag(v) -> str:
            return "✅" if (v.strip().lower() == "true"
                           if isinstance(v, str) else bool(v)) else "❌"
        labels = (("净收益为正", "net_positive"), ("回撤≤10%", "max_dd"),
                  ("夏普≥2.0", "sharpe"), ("Calmar≥1.5", "calmar"),
                  ("跑赢BTC单边", "beat_benchmark"),
                  ("极端段不亏", "extreme_survived"))
        print("  冻结门槛：" + " / ".join(
            f"{name} {_flag(g.get(key, False))}" for name, key in labels))
        print("  → 第三轮 3:0 裁决：关闭 1h 统计套利上线评估分支（不启用实盘）")
    else:
        print("  尚无产物；执行 python run.py backtest 生成。")

    ok = not missing_mods and not missing_files
    print("\n" + "=" * 72)
    print("自检结论：" + ("✅ 可离线运行" if ok else "❌ 先补齐上面缺失项"))
    print("=" * 72)
    return ok


def backtest() -> None:
    print("\n" + "=" * 72)
    print("M8 端到端回测（t+1 成交 / 真实成本 / 冻结门槛）")
    print("=" * 72)
    _invoke("backtest/m8_report.py")
    print(f"\n产物：{OUT / 'golden_value.json'}")
    print(f"      {OUT / 'series.json'}（逐 bar 序列）")
    print(f"      {OUT / 'm8_backtest_report.md'}")


def dashboard() -> None:
    print("\n" + "=" * 72)
    print("渲染前端看板")
    print("=" * 72)
    for f in ("series.json", "golden_value.json"):
        if not (OUT / f).exists():
            print(f"  ❌ 缺少 {OUT / f}，请先 python run.py backtest")
            return
    _invoke("dashboard/render_dashboard.py")
    print(f"\n👉 直接双击打开：{DASHBOARD}")
    print("   （离线单文件，无需服务器 / 无需联网）")


def acceptance() -> None:
    print("\n" + "=" * 72)
    print("全量验收（约 6 分钟）")
    print("=" * 72)
    known = {"backtest/m3_acceptance.py": "既有失败：30 天样本未达冻结阈值，非回归引入"}
    for script in ACCEPTANCE:
        r, lines = _invoke(script, tail=4)
        if script in known:
            print(f"     ℹ️ {known[script]}")
    print("\n说明：M3 的 ❌ 是项目已知的既有失败；深数据重筛已改用 ETH/SOL 纠正。")
    print("     其余验收与周期参数化专项（47 条）应全绿。")


def fetch() -> None:
    """重新拉取 5 币 410 天 1h K 线（需要联网 + .env 的 Gate.io 只读 KEY）。"""
    print("\n" + "=" * 72)
    print("拉取 Gate.io 现货 K 线（只读，不写入任何订单）")
    print("=" * 72)
    if not (ROOT / ".env").exists():
        print("  ⚠️ 未发现 .env；公开通道历史深度有限，410 天通常需要只读 APIKEY。")
        print("     参考 .env.example 配置后重试。")
    CACHE.mkdir(parents=True, exist_ok=True)
    from src.data_layer.gate_kline_fetcher import GateKLineFetcher
    f = GateKLineFetcher(cache_dir=str(CACHE), use_cache=False)
    for s in SYMS:
        pair = f"{s}_USDT"
        df = f.fetch_kline(pair, "1h", lookback_days=410)
        dest = CACHE / f"{pair}_1h_410d.parquet"
        f.to_parquet(df, str(dest))
        print(f"  ✅ {pair:<10s} {len(df):5d} bar -> {dest.name}")
    print("数据准备完成，可执行 python run.py")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    commands = {
        "check": lambda: 0 if check() else 1,
        "backtest": lambda: (backtest(), 0)[1],
        "dashboard": lambda: (dashboard(), 0)[1],
        "acceptance": lambda: (acceptance(), 0)[1],
        "fetch": lambda: (fetch(), 0)[1],
        "all": lambda: (check(), backtest(), dashboard(), 0)[3],
    }
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if cmd not in commands:
        print(f"未知子命令：{cmd}\n")
        print(__doc__)
        return 2
    print("统计套利量化系统 · 本地运行（研究/回测，非实盘；不会下任何真实订单）")
    return commands[cmd]()


if __name__ == "__main__":
    sys.exit(main())
