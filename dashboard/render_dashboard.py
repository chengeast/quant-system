"""M9-b · 前端看板渲染器（非关键路径）。

读 M8 产出（backtest/output/series.json + golden_value.json），产出
dashboard/index.html（单文件、无外部依赖、双击可开），展示：
  净值曲线 / 价差 / z 曲线 / 持仓态 / 告警时间戳 + 指标四件套卡片 + 门槛判定。

数据源缺失时容错为空数组（非关键路径，不阻断）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "backtest" / "output"


def out_path() -> Path:
    return ROOT / "dashboard" / "index.html"


def _load(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def load_data() -> Dict[str, Any]:
    """读 M8 输出；缺失容错为空。"""
    series = _load(OUT_DIR / "series.json", {})
    golden = _load(OUT_DIR / "golden_value.json", {})
    return {"series": series, "golden": golden}


def render_dashboard() -> str:
    """产出单文件 HTML（无外部 JS/CSS，浏览器直接可开）。"""
    data = load_data()
    series = data["series"]
    golden = data["golden"]

    ts = series.get("ts", [])
    equity = series.get("equity", [])
    spread = series.get("spread", [])
    z = series.get("z", [])
    position = series.get("position", [])
    alert_ts = series.get("alert_ts", [])

    metrics = golden.get("metrics", {})
    gates = golden.get("gates", {})
    fp = golden.get("data_fingerprint", {})
    gen_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # JS 数组（NaN → null）
    def js_arr(v):
        return json.dumps(
            [None if (isinstance(x, float) and x != x) else x for x in v],
            ensure_ascii=False)

    # 指标卡片（M8 四件套 + 极端段 + 成本）
    def pct(v):
        try:
            return f"{float(v) * 100:+.2f}%"
        except Exception:
            return "—"

    def num(v, nd=2):
        try:
            return f"{float(v):.{nd}f}"
        except Exception:
            return "—"

    def g_ok(k):
        return "✅" if gates.get(k) else "❌"

    cards = [
        ("净收益(峰值)", pct(metrics.get("total_return")), "净"),
        ("稳态(0.5×)", pct(metrics.get("steady_total_return")), "稳"),
        ("最大回撤", num(metrics.get("max_drawdown")) + "%" if metrics.get("max_drawdown") is not None else "—", "回撤"),
        ("夏普(1h年化)", num(metrics.get("sharpe"), 3), "夏普"),
        ("Calmar", num(metrics.get("calmar"), 3), "Calmar"),
        ("极端段PnL", pct(metrics.get("extreme_pnl")) + (" ✅" if metrics.get("extreme_survived") else " ❌"), "极端段"),
        ("跑赢BTC", pct(metrics.get("benchmark_total_return")) + (" ✅" if metrics.get("beat_benchmark") else " ❌"), "基准"),
        ("累计成本", "$" + num(metrics.get("cost_paid")), "成本"),
        ("成交笔数", str(metrics.get("n_trades", "—")), "笔数"),
        ("最长连亏", str(metrics.get("max_consecutive_losses", "—")), "连亏"),
    ]
    card_html = ""
    for title, val, tag in cards:
        card_html += (
            f'<div class="card"><div class="card-tag">{tag}</div>'
            f'<div class="card-title">{title}</div>'
            f'<div class="card-val">{val}</div></div>\n')

    gate_html = ""
    for k, v in gates.items():
        gate_html += (
            f'<li class="{ "ok" if v else "bad" }">'
            f'{"✅" if v else "❌"} {k}</li>')

    fp_html = "".join(f'<code>{k}={v}</code>' for k, v in fp.items())

    n_alerts = len(alert_ts)
    alert_line = (f"本回测监控事件 <b>{n_alerts}</b> 条（非关键路径占位；"
                  "实盘接入 M7 Webhook 后实时填充）。"
                  if n_alerts == 0
                  else f"本回测监控事件 <b>{n_alerts}</b> 条。")

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>M8 回测看板 · ETH_USDT × SOL_USDT</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    --bg: #f5f6f8; --panel: #ffffff; --line: #e3e6ea; --text: #1f2329;
    --muted: #6b7280; --accent: #2563eb; --eq: #2563eb; --z: #d97706;
    --sp: #059669; --pos: #dc2626;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font: 14px/1.5 system-ui, "Segoe UI", "Microsoft YaHei", sans-serif;
         background: var(--bg); color: var(--text); }}
  header {{ background: var(--panel); border-bottom: 1px solid var(--line);
            padding: 14px 20px; display: flex; justify-content: space-between; align-items: center; }}
  header h1 {{ font-size: 18px; margin: 0; }}
  header .meta {{ color: var(--muted); font-size: 12px; text-align: right; }}
  main {{ padding: 16px 20px; max-width: 1200px; margin: 0 auto; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px,1fr));
            gap: 10px; margin-bottom: 16px; }}
  .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
           padding: 10px 12px; }}
  .card-tag {{ font-size: 11px; color: var(--muted); }}
  .card-title {{ font-size: 12px; margin-top: 2px; }}
  .card-val {{ font-size: 17px; font-weight: 700; margin-top: 4px; }}
  .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
            padding: 12px 14px; margin-bottom: 14px; }}
  .panel h2 {{ font-size: 14px; margin: 0 0 8px; }}
  svg {{ width: 100%; height: 220px; background: #fafbfc; border: 1px solid var(--line);
         border-radius: 6px; display: block; }}
  .two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  @media (max-width: 720px) {{ .two {{ grid-template-columns: 1fr; }} }}
  ul.gates {{ list-style: none; padding: 0; margin: 0; columns: 2; gap: 16px; }}
  ul.gates li {{ font-size: 13px; padding: 2px 0; }}
  ul.gates li.ok {{ color: #059669; }}
  ul.gates li.bad {{ color: #dc2626; }}
  .legend {{ font-size: 12px; color: var(--muted); margin-top: 6px; }}
  code {{ background: #eef1f5; padding: 1px 5px; border-radius: 4px; font-size: 11px; }}
  .note {{ font-size: 12px; color: var(--muted); }}
</style>
</head>
<body>
<header>
  <h1>M8 回测看板 · ETH_USDT × SOL_USDT · 410 天 1h 统计套利 MVP</h1>
  <div class="meta">生成 {gen_ts}<br>seed={golden.get("seed","?")} · {n_alerts} 告警</div>
</header>
<main>
  <div class="cards">
{card_html}
  </div>

  <div class="panel">
    <h2>净值曲线 · 价差 · z 曲线</h2>
    <svg id="chart" viewBox="0 0 1000 360" preserveAspectRatio="none" style="height:360px"></svg>
    <div class="legend">
      <span style="color:var(--eq)">■ 净值（子图1）</span>&nbsp;
      <span style="color:var(--sp)">■ 价差（子图2）</span>&nbsp;
      <span style="color:var(--z)">■ z 曲线（子图3）</span>&nbsp;
      <span style="color:var(--pos)">■ 持仓区间（下方独立子图）</span>
    </div>
  </div>

  <div class="two">
    <div class="panel">
      <h2>门槛判定（backtest_gate 冻结）</h2>
      <ul class="gates">{gate_html}</ul>
    </div>
    <div class="panel">
      <h2>告警 & 数据指纹</h2>
      <p class="note">{alert_line}</p>
      <div style="margin-top:8px">{fp_html}</div>
    </div>
  </div>

  <div class="panel">
    <h2>持仓态（M4 决策状态机）</h2>
    <svg id="poschart" viewBox="0 0 1000 90" preserveAspectRatio="none" style="height:90px"></svg>
    <div class="legend">
      <span style="color:var(--pos)">■ 持仓（LONG_SPREAD / SHORT_SPREAD）</span>
      &nbsp;<span>FLAT = 未持仓（白色）</span>
    </div>
  </div>
</main>

<script>
// ---- 数据（M8 输出注入）----
const TS     = {js_arr(ts)};
const EQUITY = {js_arr(equity)};
const SPREAD = {js_arr(spread)};
const Z      = {js_arr(z)};
const POS    = {js_arr(position)};

function buildPath(vals, min, max, W, H, pad) {{
  if (!vals.length) return "";
  const n = vals.length;
  const scale = v => H - pad - ((v - min) / (max - min || 1)) * (H - 2 * pad);
  const step = W / (n - 1 || 1);
  let d = "";
  for (let i = 0; i < n; i++) {{
    const v = vals[i];
    if (v === null || v === undefined || isNaN(v)) continue;
    d += (d === "" ? "M" : "L") + (i * step).toFixed(1) + "," + scale(v).toFixed(1) + " ";
  }}
  return d;
}}

function norm(arr, w, h, pad) {{
  let mn = Infinity, mx = -Infinity;
  for (const v of arr) if (v !== null && v !== undefined && !isNaN(v)) {{
    mn = Math.min(mn, v); mx = Math.max(mx, v);
  }}
  if (mn === Infinity) {{ mn = 0; mx = 1; }}
  if (mn === mx) {{ mn -= 1; mx += 1; }}
  return [mn, mx];
}}

function drawChart(id, series) {{
  const svg = document.getElementById(id);
  const W = 1000, H = parseInt(svg.getAttribute("viewBox").split(" ")[2]), pad = 8;
  // 多序列独立子图（净值/价差/z 量纲不同，各自上下堆叠 + 标签，不强行共轴）
  const per = H / series.length;
  let out = "";
  for (let s = 0; s < series.length; s++) {{
    const subTop = s * per;
    const [mn, mx] = norm(series[s].vals, W, per, pad);
    const hsub = per;
    const path = buildPath(series[s].vals, mn, mx, W, hsub, pad);
    // 子图背景框 + 标签 + 曲线（曲线纵坐标基于 0..hsub）
    out += '<g transform="translate(0,' + subTop + ')">'
         +  '<rect x="0" y="0" width="' + W + '" height="' + hsub + '" fill="#fafbfc"/>'
         +  '<text x="6" y="14" font-size="11" fill="' + series[s].color + '">'
         +  series[s].label + '</text>'
         +  '<path d="' + path + '" fill="none" stroke="' + series[s].color + '" stroke-width="1.6"/>'
         +  '</g>';
  }}
  svg.innerHTML = out;
}}

function drawPositions(id, pos, ts) {{
  const svg = document.getElementById(id);
  const W = 1000, H = 90, n = pos.length;
  if (!n) {{ svg.innerHTML = '<text x="8" y="20" fill="#999">无持仓数据</text>'; return; }}
  const step = W / n;
  let bars = "";
  for (let i = 0; i < n; i++) {{
    if (pos[i] && pos[i] !== "FLAT") {{
      bars += '<rect x="' + (i * step).toFixed(2) + '" y="10" width="' + step.toFixed(2)
            + '" height="' + (H - 20) + '" fill="#dc2626" opacity="0.55"/>';
    }}
  }}
  svg.innerHTML = '<rect x="0" y="10" width="' + W + '" height="' + (H-20) + '" fill="#f0f2f5"/>' + bars;
}}

// 三条核心曲线各占独立子图（H=360，每图 120）
drawChart("chart", [
  {{ vals: EQUITY, color: "#2563eb", label: "净值 equity" }},
  {{ vals: SPREAD, color: "#059669", label: "价差 spread" }},
  {{ vals: Z,      color: "#d97706", label: "z 曲线" }},
]);
drawPositions("poschart", POS, TS);
</script>
</body>
</html>
"""
    return html


if __name__ == "__main__":
    html = render_dashboard()
    p = out_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    print(f"M9-b 看板 → {p} ({len(html)} bytes)")
