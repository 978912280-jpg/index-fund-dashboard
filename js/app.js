/* 指数与基金估值观察台前端逻辑 */
(function () {
  "use strict";

  var state = { data: null, filter: "all", current: null };
  var trendChart = null;
  var bigTrendChart = null;

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
        renderReport(data.report);
        renderTable();
        var t = $("update-time");
        if (data.meta && data.meta.generated_at) {
          t.textContent = "数据更新时间：" + data.meta.generated_at.replace("T", " ");
        }
        var v = $("meta-version");
        if (v && data.meta && data.meta.version) {
          v.textContent = "v" + data.meta.version;
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
    var items = state.data.items.slice().sort(function (a, b) {
      var sa = (a.score === null || a.score === undefined) ? -1 : a.score;
      var sb = (b.score === null || b.score === undefined) ? -1 : b.score;
      return sb - sa;
    });
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

  // ---------- 每日报告 ----------
  function renderReport(report) {
    var card = $("report-card");
    if (!report) { card.hidden = true; return; }
    card.hidden = false;

    var s = report.summary || {};
    var avg = s.avg_score === null || s.avg_score === undefined ? "—" : fmt(s.avg_score, 1);
    $("report-date").textContent = "生成时间：" + (report.generated_at || "").replace("T", " ");
    $("report-mood").textContent = s.mood || "—";
    $("report-summary").textContent =
      "共 " + s.total + " 只标的，" + s.valid + " 只完成评分；上涨 " + s.up +
      "、下跌 " + s.down + "、平盘 " + s.flat + "；平均综合分 " + avg + "。";

    var bullets = report.bullets || [];
    $("report-bullets").innerHTML = bullets.map(function (b) {
      return "<li>" + escapeHtml(b) + "</li>";
    }).join("");

    var groups = report.groups || {};
    renderRpList("rp-positive", groups.positive);
    renderRpList("rp-neutral", groups.neutral);
    renderRpList("rp-cautious", groups.cautious);

    var cats = report.category_summary || [];
    $("report-cats").innerHTML = cats.map(function (c) {
      return '<div class="rp-cat"><span class="rp-cat-label">' + escapeHtml(c.label) +
        "</span><span class=\"rp-cat-meta\">均分 " + fmt(c.avg_score, 1) +
        " · 最强 " + escapeHtml(c.best_name) + "（" + fmt(c.best_score, 1) + "）</span></div>";
    }).join("");
  }

  function renderRpList(id, list) {
    var el = $(id);
    if (!list || !list.length) {
      el.innerHTML = '<li class="rp-empty">暂无标的</li>';
      return;
    }
    el.innerHTML = list.map(function (x) {
      var score = x.score === null || x.score === undefined ? "—" : fmt(x.score, 1);
      var chg = x.change_pct === null || x.change_pct === undefined ? "" :
        '<span class="rp-chg ' + pctClass(x.change_pct) + '">' + fmtPct(x.change_pct) + "</span>";
      return '<li class="rp-item">' +
        '<div class="rp-top"><span class="rp-name">' + escapeHtml(x.name) + "</span>" +
        '<span class="rp-right">' + chg +
        '<span class="rp-score">' + score + "</span>" +
        '<span class="badge rating-' + escapeHtml(x.rating) + '">' + escapeHtml(x.rating) +
        "</span></span></div>" +
        '<div class="rp-reason">' + escapeHtml(x.reason) + "</div></li>";
    }).join("");
  }

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

    renderMetrics(it);
    renderModalScores(s);
    $("modal-mask").hidden = false;
    loadAndRenderTrend(it);
  }

  function closeDetail() {
    $("modal-mask").hidden = true;
    if (trendChart) { trendChart.dispose(); trendChart = null; }
  }

  function loadAndRenderTrend(it) {
    fetch("data/history/" + encodeURIComponent(it.code) + ".json")
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (d) {
        it._hist = d.history || [];
        renderKline(it._hist);
      })
      .catch(function () { renderLineFallback(it.history || []); });
  }

  function _sma(values, n) {
    var out = [];
    var sum = 0;
    for (var i = 0; i < values.length; i++) {
      sum += values[i];
      if (i >= n) sum -= values[i - n];
      out.push(i >= n - 1 ? sum / n : null);
    }
    return out;
  }

  function _klineOption(hist) {
    var WINDOW = 120;
    var data = hist.slice(-WINDOW);
    var dates = data.map(function (h) { return h.date; });
    var ohlc = data.map(function (h) { return [h.open, h.close, h.low, h.high]; });
    var closes = hist.map(function (h) { return h.close; });
    var ma100 = _sma(closes, 100).slice(-WINDOW);
    var ma250 = _sma(closes, 250).slice(-WINDOW);
    return {
      grid: { left: 12, right: 16, top: 28, bottom: 28, containLabel: true },
      tooltip: {
        trigger: "axis",
        confine: true,
        axisPointer: { type: "cross" },
        formatter: function (params) {
          if (!params || !params.length) return "";
          var p = params[0];
          var start = hist.length - WINDOW;
          var rec = hist[start + p.dataIndex];
          if (!rec) return "";
          var o = rec.open, c = rec.close, l = rec.low, h = rec.high;
          var prevClose = (start + p.dataIndex - 1 >= 0) ? closes[start + p.dataIndex - 1] : null;
          var chg = (prevClose && c != null && prevClose) ? (c - prevClose) / prevClose * 100 : null;
          var lines = ["<b>" + p.axisValue + "</b>"];
          lines.push("开盘：" + fmt(o));
          lines.push("收盘：" + fmt(c));
          lines.push("最低：" + fmt(l));
          lines.push("最高：" + fmt(h));
          if (chg !== null) {
            var sign = chg >= 0 ? "+" : "";
            var color = chg >= 0 ? "#2e7d32" : "#b71c1c";
            lines.push("涨跌幅：<span style='color:" + color + "'>" + sign + chg.toFixed(2) + "%</span>");
          }
          return lines.join("<br/>");
        }
      },
      legend: {
        top: 0, right: 0, textStyle: { color: "#6b7280", fontSize: 12 },
        data: ["MA100", "MA250"]
      },
      xAxis: {
        type: "category", data: dates, boundaryGap: true,
        axisLine: { lineStyle: { color: "#d1d5db" } }, axisLabel: { color: "#6b7280" }
      },
      yAxis: {
        type: "value", scale: true,
        splitLine: { lineStyle: { color: "#eeeeee" } }, axisLabel: { color: "#6b7280" }
      },
      series: [
        {
          type: "candlestick", data: ohlc,
          itemStyle: {
            color: "#2e7d32", color0: "#b71c1c",
            borderColor: "#2e7d32", borderColor0: "#b71c1c"
          }
        },
        {
          name: "MA100", type: "line", data: ma100, smooth: false, showSymbol: false,
          lineStyle: { width: 1.5, color: "#2563a8" }
        },
        {
          name: "MA250", type: "line", data: ma250, smooth: false, showSymbol: false,
          lineStyle: { width: 1.5, color: "#e65100" }
        }
      ]
    };
  }

  function renderKline(hist) {
    var el = $("chart-trend");
    if (trendChart) trendChart.dispose();
    trendChart = echarts.init(el);
    trendChart.setOption(_klineOption(hist));
    requestAnimationFrame(function () { if (trendChart) trendChart.resize(); });
    setTimeout(function () { if (trendChart) trendChart.resize(); }, 150);
  }

  function renderLineFallback(hist) {
    var el = $("chart-trend");
    if (trendChart) trendChart.dispose();
    trendChart = echarts.init(el);
    var dates = hist.map(function (h) { return h[0]; });
    var closes = hist.map(function (h) { return h[1]; });
    trendChart.setOption({
      grid: { left: 12, right: 16, top: 16, bottom: 28, containLabel: true },
      tooltip: { trigger: "axis", confine: true },
      xAxis: {
        type: "category", data: dates, boundaryGap: false,
        axisLine: { lineStyle: { color: "#d1d5db" } }, axisLabel: { color: "#6b7280" }
      },
      yAxis: {
        type: "value", scale: true,
        splitLine: { lineStyle: { color: "#eeeeee" } }, axisLabel: { color: "#6b7280" }
      },
      series: [{
        type: "line", data: closes, smooth: true, showSymbol: false,
        lineStyle: { color: "#2563a8", width: 2 },
        areaStyle: { color: "rgba(37,99,168,0.08)" }
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

  // ---------- 大屏放大 ----------
  function openBigScreen() {
    var it = state.current;
    if (!it) return;
    $("big-title").textContent = it.name + "（" + it.code + "）";
    $("big-mask").hidden = false;
    requestAnimationFrame(function () {
      if (it._hist) {
        renderBigKline(it._hist);
      } else {
        fetch("data/history/" + encodeURIComponent(it.code) + ".json")
          .then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
          })
          .then(function (d) {
            it._hist = d.history || [];
            renderBigKline(it._hist);
          })
          .catch(function () { /* 大屏 K 线留空 */ });
      }
    });
  }

  function closeBigScreen() {
    $("big-mask").hidden = true;
    if (bigTrendChart) { bigTrendChart.dispose(); bigTrendChart = null; }
  }

  function renderBigKline(hist) {
    var el = $("big-trend");
    if (bigTrendChart) bigTrendChart.dispose();
    bigTrendChart = echarts.init(el);
    bigTrendChart.setOption(_klineOption(hist));
    requestAnimationFrame(function () { if (bigTrendChart) bigTrendChart.resize(); });
    setTimeout(function () { if (bigTrendChart) bigTrendChart.resize(); }, 150);
  }

  function _scoreTone(v) {
    if (v === null || v === undefined || isNaN(v)) return "neutral";
    if (v >= 60) return "good";
    if (v >= 40) return "mid";
    return "bad";
  }

  function renderModalScores(s) {
    var dims = [
      ["估值", s.valuation],
      ["技术", s.technical],
      ["低拥挤", s.crowding === null ? null : 100 - s.crowding],
      ["风险", s.risk]
    ];
    $("modal-scores").innerHTML = dims.map(function (d) {
      var v = d[1];
      var valid = v !== null && v !== undefined && !isNaN(v);
      var tone = valid ? _scoreTone(v) : "neutral";
      var txt = valid ? fmt(v, 1) : "—";
      return '<div class="bs-card bs-' + tone + '">' +
        '<div class="bs-name">' + d[0] + "</div>" +
        '<div class="bs-val">' + txt + "</div>" +
        "</div>";
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

    $("big-open").addEventListener("click", openBigScreen);
    $("big-close").addEventListener("click", closeBigScreen);
    $("big-mask").addEventListener("click", function (e) {
      if (e.target === $("big-mask")) closeBigScreen();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        if (!$("big-mask").hidden) closeBigScreen();
        else closeDetail();
      }
    });

    $("toggle-disclaimer").addEventListener("click", function () {
      var d = $("disclaimer");
      d.hidden = !d.hidden;
    });

    window.addEventListener("resize", function () {
      if (trendChart) trendChart.resize();
      if (bigTrendChart) bigTrendChart.resize();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bind();
    load();
  });
})();
