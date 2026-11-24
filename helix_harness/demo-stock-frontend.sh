#!/bin/sh
# Generate a standalone Stock Analysis Frontend demo repository for Per-Repo RSI evaluation.
set -eu
dest="${1:-${RSI_PUBLIC_REPOS:-$HOME/Library/Application Support/Helix Harness/WebConsole/public-repos}/stock-analyzer-demo}"
mkdir -p "$(dirname "$dest")"
if [ -e "$dest" ]; then
    echo "Demo repository already exists at $dest"
    exit 0
fi

mkdir -p "$dest/scripts"

cat > "$dest/package.json" <<'EOF'
{
  "name": "stock-analyzer-demo",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "verify": "node scripts/verify_ui.mjs"
  }
}
EOF

cat > "$dest/index.html" <<'EOF'
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <link rel="icon" href="data:," />
    <title>AlphaPulse · 股票行情分析看板</title>
    <link rel="stylesheet" href="./styles.css" />
  </head>
  <body>
    <header class="top-bar">
      <h1>AlphaPulse 行情看板</h1>
      <div class="controls">
        <select id="symbol-select" aria-label="选择股票">
          <option value="600519.SH">贵州茅台 (600519.SH)</option>
          <option value="300750.SZ">宁德时代 (300750.SZ)</option>
          <option value="000858.SZ">五粮液 (000858.SZ)</option>
        </select>
      </div>
    </header>

    <div class="legacy-grid">
      <main class="chart-box">
        <div id="quote-summary"></div>
        <svg id="kline-svg" viewBox="0 0 640 320" width="100%" height="320"></svg>
      </main>
      <aside class="right-sidebar">
        <h2>技术指标</h2>
        <table id="indicator-table">
          <thead>
            <tr><th>指标</th><th>数值</th><th>信号</th></tr>
          </thead>
          <tbody></tbody>
        </table>
      </aside>
    </div>

    <script type="module" src="./app.js"></script>
  </body>
</html>
EOF

cat > "$dest/styles.css" <<'EOF'
:root {
  --bg-page: #ffffff;
  --bg-card: #f8fafc;
  --text-main: #111827;
  --border: #e5e7eb;
  --color-up: #16a34a;
  --color-down: #dc2626;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg-page);
  color: var(--text-main);
}
.top-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  border-bottom: 1px solid var(--border);
}
.legacy-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  padding: 16px;
}
.chart-box, .right-sidebar {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th, td {
  text-align: left;
  padding: 8px;
  border-bottom: 1px solid var(--border);
}
EOF

cat > "$dest/app.js" <<'EOF'
export const STOCK_DATA = {
  "600519.SH": {
    name: "贵州茅台",
    price: 1688.5,
    changePct: 2.34,
    series: {
      "1D": [
        { t: "09:30", o: 1650, h: 1668, l: 1645, c: 1662, v: 420 },
        { t: "10:30", o: 1662, h: 1679, l: 1658, c: 1674, v: 510 },
        { t: "11:30", o: 1674, h: 1682, l: 1665, c: 1670, v: 390 },
        { t: "13:30", o: 1670, h: 1690, l: 1668, c: 1685, v: 610 },
        { t: "14:30", o: 1685, h: 1695, l: 1678, c: 1688.5, v: 730 },
      ],
      "1W": [
        { t: "Mon", o: 1620, h: 1648, l: 1612, c: 1642, v: 2100 },
        { t: "Tue", o: 1642, h: 1660, l: 1635, c: 1655, v: 2450 },
        { t: "Wed", o: 1655, h: 1662, l: 1638, c: 1644, v: 1980 },
        { t: "Thu", o: 1644, h: 1675, l: 1640, c: 1669, v: 2640 },
        { t: "Fri", o: 1669, h: 1695, l: 1662, c: 1688.5, v: 3120 },
      ],
      "1M": [
        { t: "W1", o: 1580, h: 1625, l: 1565, c: 1612, v: 9800 },
        { t: "W2", o: 1612, h: 1640, l: 1598, c: 1628, v: 10400 },
        { t: "W3", o: 1628, h: 1665, l: 1618, c: 1652, v: 11200 },
        { t: "W4", o: 1652, h: 1695, l: 1642, c: 1688.5, v: 12900 },
      ],
    },
    indicators: [
      { name: "MA5", value: "1671.40", signal: "多头排列" },
      { name: "MACD", value: "+12.84", signal: "金叉放量" },
      { name: "RSI(14)", value: "63.2", signal: "强势区间" },
      { name: "成交额", value: "68.4亿", signal: "温和放大" },
    ],
  },
  "300750.SZ": {
    name: "宁德时代",
    price: 246.8,
    changePct: -1.12,
    series: {
      "1D": [
        { t: "09:30", o: 250, h: 252, l: 248, c: 249.2, v: 820 },
        { t: "10:30", o: 249.2, h: 250.5, l: 246, c: 247.5, v: 910 },
        { t: "11:30", o: 247.5, h: 248.4, l: 245.5, c: 246.1, v: 760 },
        { t: "13:30", o: 246.1, h: 248, l: 245.2, c: 247.3, v: 690 },
        { t: "14:30", o: 247.3, h: 247.8, l: 245.8, c: 246.8, v: 840 },
      ],
      "1W": [
        { t: "Mon", o: 254, h: 257, l: 251, c: 252.5, v: 4100 },
        { t: "Tue", o: 252.5, h: 254, l: 249, c: 250.1, v: 3900 },
        { t: "Wed", o: 250.1, h: 251.5, l: 247, c: 248.6, v: 3600 },
        { t: "Thu", o: 248.6, h: 250, l: 246, c: 249.6, v: 3500 },
        { t: "Fri", o: 249.6, h: 251, l: 245.2, c: 246.8, v: 4200 },
      ],
      "1M": [
        { t: "W1", o: 238, h: 249, l: 235, c: 246, v: 18200 },
        { t: "W2", o: 246, h: 258, l: 244, c: 255, v: 21000 },
        { t: "W3", o: 255, h: 257, l: 248, c: 250, v: 17500 },
        { t: "W4", o: 250, h: 252, l: 245, c: 246.8, v: 16900 },
      ],
    },
    indicators: [
      { name: "MA5", value: "249.52", signal: "短线承压" },
      { name: "MACD", value: "-2.15", signal: "绿柱收敛" },
      { name: "RSI(14)", value: "44.8", signal: "中性偏弱" },
      { name: "成交额", value: "51.2亿", signal: "缩量整理" },
    ],
  },
  "000858.SZ": {
    name: "五粮液",
    price: 152.4,
    changePct: 0.85,
    series: {
      "1D": [
        { t: "09:30", o: 151.1, h: 152, l: 150.6, c: 151.6, v: 310 },
        { t: "10:30", o: 151.6, h: 152.8, l: 151.3, c: 152.2, v: 350 },
        { t: "11:30", o: 152.2, h: 152.9, l: 151.8, c: 152.0, v: 280 },
        { t: "13:30", o: 152.0, h: 153.1, l: 151.9, c: 152.6, v: 390 },
        { t: "14:30", o: 152.6, h: 153.0, l: 152.1, c: 152.4, v: 410 },
      ],
      "1W": [
        { t: "Mon", o: 149.2, h: 151, l: 148.8, c: 150.4, v: 1800 },
        { t: "Tue", o: 150.4, h: 151.8, l: 149.9, c: 151.1, v: 1920 },
        { t: "Wed", o: 151.1, h: 152.2, l: 150.6, c: 151.5, v: 1740 },
        { t: "Thu", o: 151.5, h: 152.4, l: 150.9, c: 151.1, v: 1690 },
        { t: "Fri", o: 151.1, h: 153.1, l: 150.8, c: 152.4, v: 2050 },
      ],
      "1M": [
        { t: "W1", o: 146, h: 150, l: 145, c: 149, v: 8100 },
        { t: "W2", o: 149, h: 151.5, l: 148, c: 150.5, v: 8600 },
        { t: "W3", o: 150.5, h: 152.5, l: 149.8, c: 151.1, v: 8400 },
        { t: "W4", o: 151.1, h: 153.1, l: 150.6, c: 152.4, v: 9100 },
      ],
    },
    indicators: [
      { name: "MA5", value: "151.30", signal: "稳步抬升" },
      { name: "MACD", value: "+1.92", signal: "零轴上方" },
      { name: "RSI(14)", value: "57.1", signal: "温和偏强" },
      { name: "成交额", value: "29.6亿", signal: "平稳运行" },
    ],
  },
};

export function renderDashboard(symbol = "600519.SH", timeframe = "1D") {
  const stock = STOCK_DATA[symbol] || STOCK_DATA["600519.SH"];
  const bars = stock.series[timeframe] || stock.series["1D"];
  const summary = document.getElementById("quote-summary");
  if (summary) {
    summary.textContent = `${stock.name} (${symbol}) · ¥${stock.price.toFixed(2)} (${stock.changePct >= 0 ? "+" : ""}${stock.changePct}%)`;
  }
  const svg = document.getElementById("kline-svg");
  if (svg) {
    const prices = bars.flatMap((b) => [b.h, b.l]);
    const hi = Math.max(...prices);
    const lo = Math.min(...prices);
    const span = Math.max(1, hi - lo);
    const step = 600 / bars.length;
    svg.innerHTML = bars
      .map((b, i) => {
        const x = 20 + i * step + step / 2;
        const yH = 20 + ((hi - b.h) / span) * 260;
        const yL = 20 + ((hi - b.l) / span) * 260;
        const yO = 20 + ((hi - b.o) / span) * 260;
        const yC = 20 + ((hi - b.c) / span) * 260;
        const up = b.c >= b.o;
        const color = up ? "var(--color-up)" : "var(--color-down)";
        return `<line x1="${x}" y1="${yH}" x2="${x}" y2="${yL}" stroke="${color}" stroke-width="2" />
          <rect x="${x - 14}" y="${Math.min(yO, yC)}" width="28" height="${Math.max(4, Math.abs(yC - yO))}" fill="${color}" />`;
      })
      .join("");
  }
  const tbody = document.querySelector("#indicator-table tbody");
  if (tbody) {
    tbody.innerHTML = stock.indicators
      .map((row) => `<tr><td>${row.name}</td><td>${row.value}</td><td>${row.signal}</td></tr>`)
      .join("");
  }
}

if (typeof document !== "undefined") {
  const select = document.getElementById("symbol-select");
  if (select) {
    select.addEventListener("change", () => renderDashboard(select.value, "1D"));
  }
  renderDashboard("600519.SH", "1D");
}
EOF

cat > "$dest/scripts/verify_ui.mjs" <<'EOF'
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const html = readFileSync("index.html", "utf8");
const js = readFileSync("app.js", "utf8");
assert.match(html, /kline-svg/, "index.html must include #kline-svg");
assert.match(html, /indicator-table/, "index.html must include #indicator-table");
assert.match(js, /export\s+function\s+renderDashboard/, "app.js must export renderDashboard");
console.log("STOCK_FRONTEND_SMOKE_OK");
EOF

git -C "$dest" init -q -b main
git -C "$dest" add .
git -C "$dest" -c core.hooksPath=/dev/null -c user.name='Helix Demo' -c user.email='demo@localhost' -c commit.gpgSign=false commit -qm 'Initial legacy stock analyzer frontend'
echo "Created stock analyzer demo repository at $dest"
