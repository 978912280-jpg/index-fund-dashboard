/* 指数与基金估值观察台前端逻辑 */
(function () {
  "use strict";

  var state = { data: null, filter: "all", current: null };
  var trendChart = null;
  var radarChart = null;

  var RATING_ORDER = ["强烈买入", "买入", "观望", "卖出", "强烈卖出"];

  function $(id) { return document.getElementById(id); }

  function fmt(x, digits) {
    if (x === null || x === undefined || isNaN(x)) return "—";
    return Number(x).toFixed(digits === undefined ? 2 : digits);
  }

  function fmtPct(x) {
    if (x === null || x === undefined || isNaN(x)) return "—";
    var v = Number(x);
    return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
  }

  function pctClass(x) {
    if (x === null || x === undefined || isNaN(x)) return "muted";
    return Number(x) >= 0 ? "pos" : "neg";
  }

  // ---------- 数据加载 ----------
  function load() {
    fetch("data/dashboard.json")
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        state.data = data;
        renderOverview();
        renderTable();
        var t = $("update-time");
        if (data.meta && data.meta.generated_at) {
          t.textContent = "数据更新时间：" + data.meta.generated_at.replace("T", " ");
        }
      })
      .catch(function (e) {
        $("table-body").innerHTML =
          '<tr><td colspan="7" class="muted" style="text-align:center;padding:24px">数据加载失败：' +
          escapeHtml(e.message) + "（请确认 data/dashboard.json 已生成）</td></tr>";
      });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ---------- 总览 ----------
  function renderOverview() {
    var items = state.data.items;
    var counts = {};
    RATING_ORDER.forEach(function (r) { counts[r] = 0; });
    items.forEach(function (it) {
      counts[it.rating] = (counts[it.rating] || 0) + 1;
    });

    var cards = RATING_ORDER.map(function (r) {
      return '<div class="ov-card">' +
        '<div class="ov-label">' + r + "</div>" +
        '<div class="ov-value">' + counts[r] + "</div>" +
        "</div>";
    }).join("");
    $("overview").innerHTML =
      '<div class="ov-card"><div class="ov-label">标的总数</div>' +
      '<div class="ov-value">' + items.length + "</div></div>" + cards;
  }

  // ---------- 表格 ----------
  function filtered() {
    var items = state.data.items;
    if (state.filter === "all") return items;
    return items.filter(function (it) { return it.category === state.filter; });
  }

  function renderTable() {
    var items = filtered();
    var body = $("table-body");
    if (!items.length) {
      $("empty-tip").hidden = false;
      body.innerHTML = "";
      return;
    }
    $("empty-tip").hidden = true;

    body.innerHTML = items.map(function (it, idx) {
      var s = it.signals || {};
      var sub = (it.region || "") + " · " + (it.kind === "etf" ? "ETF" : "指数");
      var score = it.score === null || it.score === undefined ? "—" : fmt(it.score, 1);
      return '<tr data-idx="' + indexOf(it) + '">' +
        '<td><div class="cell-name">' + escapeHtml(it.name) + "</div>" +
        '<div class="cell-sub">' + escapeHtml(sub) + "</div></td>" +
        '<td class="num">' + fmt(it.price) + "</td>" +
        '<td class="num ' + pctClass(it.change_pct) + '">' + fmtPct(it.change_pct) + "</td>" +
        '<td class="num muted">' + fmt(s.valuation, 1) + "</td>" +
        '<td class="num muted">' + fmt(s.technical, 1) + "</td>" +
        '<td class="num muted">' + fmt(s.crowding, 1) + "</td>" +
        '<td><span class="score-pill"><span class="score-num">' + score + "</span>" +
        '<span class="badge rating-' + escapeHtml(it.rating) + '">' + escapeHtml(it.rating) + "</span></span></td>" +
        "</tr>";
    }).join("");

    Array.prototype.forEach.call(body.querySelectorAll("tr"), function (tr) {
      tr.addEventListener("click", function () {
        var code = tr.getAttribute("data-idx");
        var it = state.data.items.filter(function (x) { return x.code === code; })[0];
        if (it) openDetail(it);
      });
    });
  }

  function indexOf(it) { return it.code; }

  // ---------- 详情 ----------
  function openDetail(it) {
    state.current = it;
    $("modal-title").textContent = it.name + "（" + it.code + "）";

    var s = it.signals || {};
    var scoreText = it.score === null || it.score === undefined ? "—" : fmt(it.score, 1);
    $("modal-score").innerHTML =
      '<span class="big-score">' + scoreText + "</span>" +
      '<span class="badge rating-' + escapeHtml(it.rating) + '">' + escapeHtml(it.rating) + "</span>" +
      (it.note ? '<span class="muted">' + escapeHtml(it.note) + "</span>" : "");

    renderTrend(it);
    renderRadar(s);
    renderMetrics(it);
    $("modal-mask").hidden = false;
  }

  function closeDetail() {
    $("modal-mask").hidden = true;
    if (trendChart) { trendChart.dispose(); trendChart = null; }
    if (radarChart) { radarChart.dispose(); radarChart = null; }
  }

  function renderTrend(it) {
    var el = $("chart-trend");
    if (trendChart) trendChart.dispose();
    trendChart = echarts.init(el);
    var hist = it.history || [];
    var dates = hist.map(function (h) { return h[0]; });
    var closes = hist.map(function (h) { return h[1]; });
    trendChart.setOption({
      grid: { left: 12, right: 16, top: 16, bottom: 28, containLabel: true },
      tooltip: { trigger: "axis", confine: true },
      xAxis: { type: "category", data: dates, boundaryGap: false,
        axisLine: { lineStyle: { color: "#d1d5db" } }, axisLabel: { color: "#6b7280" } },
      yAxis: { type: "value", scale: true,
        splitLine: { lineStyle: { color: "#eeeeee" } }, axisLabel: { color: "#6b7280" } },
      series: [{
        type: "line", data: closes, smooth: true, showSymbol: false,
        lineStyle: { color: "#2563a8", width: 2 },
        areaStyle: { color: "rgba(37,99,168,0.08)" }
      }]
    });
  }

  function renderRadar(s) {
    var el = $("chart-radar");
    if (radarChart) radarChart.dispose();
    radarChart = echarts.init(el);
    radarChart.setOption({
      tooltip: { confine: true },
      radar: {
        indicator: [
          { name: "估值", max: 100 },
          { name: "技术", max: 100 },
          { name: "低拥挤", max: 100 },
          { name: "风险", max: 100 }
        ],
        radius: "65%",
        axisName: { color: "#6b7280" },
        splitLine: { lineStyle: { color: "#eeeeee" } },
        splitArea: { areaStyle: { color: ["#fff", "#f8f9fb"] } }
      },
      series: [{
        type: "radar",
        data: [{
          value: [
            s.valuation === null ? 0 : s.valuation,
            s.technical === null ? 0 : s.technical,
            s.crowding === null ? 0 : 100 - s.crowding,
            s.risk === null ? 0 : s.risk
          ],
          lineStyle: { color: "#2563a8" },
          itemStyle: { color: "#2563a8" },
          areaStyle: { color: "rgba(37,99,168,0.15)" }
        }]
      }]
    });
  }

  function renderMetrics(it) {
    var m = it.metrics || {};
    var rows = [
      ["价格历史分位", m.price_percentile === null ? "—" : fmt(m.price_percentile, 1) + "%"],
      ["量比(5/60日)", m.volume_ratio_5_60 === null ? "—" : fmt(m.volume_ratio_5_60, 2)],
      ["RSI(14)", m.rsi14 === null ? "—" : fmt(m.rsi14, 1)],
      ["20日涨幅", m.ret20 === null ? "—" : fmtPct(m.ret20)],
      ["60日涨幅", m.ret60 === null ? "—" : fmtPct(m.ret60)],
      ["年化波动率", m.volatility_1y === null ? "—" : fmt(m.volatility_1y, 1) + "%"],
      ["近一年最大回撤", m.max_drawdown_1y === null ? "—" : fmt(m.max_drawdown_1y, 1) + "%"],
      ["历史样本(日)", m.history_len === null ? "—" : m.history_len]
    ];
    $("modal-metrics").innerHTML = rows.map(function (r) {
      return '<div class="metric"><div class="m-label">' + r[0] + "</div>" +
        '<div class="m-value">' + r[1] + "</div></div>";
    }).join("");
  }

  // ---------- 事件绑定 ----------
  function bind() {
    $("tabs").addEventListener("click", function (e) {
      var btn = e.target.closest(".tab");
      if (!btn) return;
      Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
        t.classList.remove("active");
      });
      btn.classList.add("active");
      state.filter = btn.getAttribute("data-cat");
      renderTable();
    });

    $("modal-close").addEventListener("click", closeDetail);
    $("modal-mask").addEventListener("click", function (e) {
      if (e.target === $("modal-mask")) closeDetail();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeDetail();
    });

    $("toggle-disclaimer").addEventListener("click", function () {
      var d = $("disclaimer");
      d.hidden = !d.hidden;
    });

    window.addEventListener("resize", function () {
      if (trendChart) trendChart.resize();
      if (radarChart) radarChart.resize();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bind();
    load();
  });
})();
