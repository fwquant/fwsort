// 执行任务页：每个账户的定时任务流水线状态，支持列表/卡片切换
(function () {
  "use strict";

  var REFRESH_MS = 5000;
  var _timer = null;
  var _data = [];
  var view = localStorage.getItem("fwsort.execView") || "auto";

  function effectiveView() {
    if (view !== "auto") return view;
    return window.innerWidth < 768 ? "card" : "list";
  }

  async function init() {
    if (!window.__FW_DEMO_MODE__ && !FWUI.api.getToken()) {
      document.getElementById("exec-cards-wrap").innerHTML = '<div class="fwui-empty-card"><div style="font-size:32px;margin-bottom:12px;">🔒</div><div style="margin-bottom:16px;">登录后查看执行任务</div><button class="fwui-btn fwui-btn--primary" onclick="FWUI.auth.openLoginModal()">去登录</button></div>';
      return;
    }
    document.getElementById("btn-refresh-exec").onclick = load;
    initViewDropdown();
    await load();
    _timer = setInterval(load, REFRESH_MS);
  }

  function initViewDropdown() {
    var dropdown = document.querySelector('[data-view-dropdown="exec"]');
    if (!dropdown) return;
    var trigger = dropdown.querySelector(".fwui-select-dropdown__trigger");
    var options = dropdown.querySelectorAll("[data-view]");

    trigger.addEventListener("click", function (e) {
      e.stopPropagation();
      var isOpen = dropdown.classList.contains("is-open");
      document.querySelectorAll(".fwui-select-dropdown.is-open").forEach(function (d) { d.classList.remove("is-open"); });
      if (!isOpen) dropdown.classList.add("is-open");
    });

    options.forEach(function (opt) {
      opt.addEventListener("click", function (e) {
        e.stopPropagation();
        view = opt.dataset.view;
        localStorage.setItem("fwsort.execView", view);
        dropdown.classList.remove("is-open");
        options.forEach(function (o) { o.classList.remove("is-selected"); });
        opt.classList.add("is-selected");
        var label = dropdown.querySelector("[data-view-label]");
        if (label) label.textContent = opt.querySelector(".fwui-select-dropdown__option-name").textContent;
        render();
      });
    });

    syncDropdownState(dropdown, view);
  }

  function syncDropdownState(dropdown, val) {
    var options = dropdown.querySelectorAll("[data-view]");
    options.forEach(function (opt) {
      var v = opt.dataset.view;
      var selected = v === val;
      opt.classList.toggle("is-selected", selected);
      if (selected) {
        var label = dropdown.querySelector("[data-view-label]");
        if (label) label.textContent = opt.querySelector(".fwui-select-dropdown__option-name").textContent;
      }
    });
  }

  document.addEventListener("click", function () {
    document.querySelectorAll(".fwui-select-dropdown.is-open").forEach(function (d) { d.classList.remove("is-open"); });
  });

  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      if (view === "auto") render();
    }, 150);
  });

  async function load() {
    if (window.__FW_DEMO_MODE__) {
      _data = mockData();
      render();
      return;
    }
    try {
      var resp = await fetch("/api/agent/accounts/execution", {
        headers: { "Authorization": "Bearer " + FWUI.api.getToken() },
      });
      var json = await resp.json();
      _data = json.accounts || [];
      render();
    } catch (e) {
      document.getElementById("exec-cards-wrap").innerHTML = '<div class="fwui-empty-card fwui-empty-card--error">' + escapeHtml(e.message) + '</div>';
    }
  }

  function mockData() {
    return [
      {
        account_id: "acc_001",
        account_name: "OKX 主账户",
        platform: "OKX",
        status: "running",
        signal: { status: "ok", last_run: "2025-01-15T10:30:05Z", result: "UP" },
        predict: { status: "ok", last_run: "2025-01-15T10:30:08Z", result: "BTC 看多 0.73" },
        trade: { status: "ok", last_run: "2025-01-15T10:30:12Z", result: "已下单 BTCUSDT" },
      },
      {
        account_id: "acc_002",
        account_name: "币安 合约",
        platform: "Binance",
        status: "running",
        signal: { status: "ok", last_run: "2025-01-15T10:30:00Z", result: "DOWN" },
        predict: { status: "ok", last_run: "2025-01-15T10:30:03Z", result: "ETH 看空 0.68" },
        trade: { status: "skipped", last_run: "2025-01-15T10:30:06Z", result: "无信号跳过" },
      },
      {
        account_id: "acc_003",
        account_name: "Gate 现货",
        platform: "Gate.io",
        status: "idle",
        signal: { status: "ok", last_run: "2025-01-15T10:29:55Z", result: "NEUTRAL" },
        predict: { status: "ok", last_run: "2025-01-15T10:29:58Z", result: "观望" },
        trade: { status: "skipped", last_run: "2025-01-15T10:30:00Z", result: "中性不交易" },
      },
      {
        account_id: "acc_004",
        account_name: "Bybit 跟单",
        platform: "Bybit",
        status: "error",
        signal: { status: "failed", last_run: "2025-01-15T10:28:00Z", result: "API 超时" },
        predict: { status: "pending", last_run: null, result: "—" },
        trade: { status: "pending", last_run: null, result: "—" },
      },
    ];
  }

  function render() {
    document.getElementById("exec-count").textContent = "共 " + _data.length + " 个账户";

    var v = effectiveView();
    var tableWrap = document.getElementById("exec-table-wrap");
    var cardsWrap = document.getElementById("exec-cards-wrap");
    var tableBody = document.getElementById("exec-table-body");

    if (v === "list") {
      tableWrap.style.display = "";
      cardsWrap.style.display = "none";
      tableBody.innerHTML = _data.map(tableRow).join("");
    } else {
      tableWrap.style.display = "none";
      cardsWrap.style.display = "";
      cardsWrap.innerHTML = '<div class="fwui-card-grid fwui-card-grid--wide">' + _data.map(card).join("") + '</div>';
    }
    bindActions();
  }

  function tableRow(a) {
    var statusLabel = a.status === "running" ? "运行中" : a.status === "idle" ? "空闲" : "异常";
    var statusCls = a.status === "running" ? "success" : a.status === "idle" ? "secondary" : "danger";

    return '<tr>' +
      '<td><div class="fwui-cell-name">' + escapeHtml(a.account_name) + '</div><div style="font-size:11px;color:var(--fwui-text-muted);">' + escapeHtml(a.platform) + '</div></td>' +
      '<td><span class="fwui-tag fwui-tag--' + statusCls + '">' + escapeHtml(statusLabel) + '</span></td>' +
      '<td>' + stepCell(a.signal) + '</td>' +
      '<td>' + stepCell(a.predict) + '</td>' +
      '<td>' + stepCell(a.trade) + '</td>' +
      '<td style="font-size:12px;">' + escapeHtml(lastRun(a)) + '</td>' +
      '<td><button class="fwui-btn fwui-btn--sm fwui-btn--primary" data-trigger="' + escapeHtml(a.account_id) + '">▶ 触发</button></td>' +
      '</tr>';
  }

  function card(a) {
    var statusLabel = a.status === "running" ? "运行中" : a.status === "idle" ? "空闲" : "异常";
    var statusCls = a.status === "running" ? "success" : a.status === "idle" ? "secondary" : "danger";

    return '<div class="fwui-card task-card" data-account="' + escapeHtml(a.account_id) + '">' +
      '<div class="task-card__head">' +
        '<div class="task-card__icon">🔗</div>' +
        '<div class="task-card__title">' +
          '<div style="font-weight:700;font-size:15px;">' + escapeHtml(a.account_name) + '</div>' +
          '<div style="font-size:12px;color:var(--fwui-text-muted);">' + escapeHtml(a.platform) + '</div>' +
        '</div>' +
        '<span class="fwui-tag fwui-tag--' + statusCls + '">' + escapeHtml(statusLabel) + '</span>' +
      '</div>' +
      '<div class="task-card__meta" style="margin-top:12px;">' +
        '<div style="display:flex;gap:8px;flex-wrap:wrap;">' +
          stepBadge("📡 信号", a.signal) +
          stepBadge("🧠 预测", a.predict) +
          stepBadge("💹 下单", a.trade) +
        '</div>' +
      '</div>' +
      '<div class="task-card__meta" style="margin-top:4px;">' +
        '<div><span style="color:var(--fwui-text-muted);">最近运行：</span>' + escapeHtml(lastRun(a)) + '</div>' +
      '</div>' +
      '<div class="task-card__actions">' +
        '<button class="fwui-btn fwui-btn--sm fwui-btn--primary" data-trigger="' + escapeHtml(a.account_id) + '">▶ 触发流水线</button>' +
      '</div>' +
    '</div>';
  }

  function stepCell(step) {
    if (!step) return '<span style="color:var(--fwui-text-muted);">—</span>';
    var cls = step.status === "ok" ? "success" : step.status === "failed" ? "danger" : step.status === "pending" ? "secondary" : "warning";
    return '<span class="fwui-tag fwui-tag--' + cls + '">' + escapeHtml(step.result || step.status) + '</span>';
  }

  function stepBadge(label, step) {
    if (!step) return '<span style="font-size:11px;color:var(--fwui-text-muted);">' + label + ' —</span>';
    var cls = step.status === "ok" ? "success" : step.status === "failed" ? "danger" : step.status === "pending" ? "secondary" : "warning";
    return '<span class="fwui-tag fwui-tag--' + cls + '" style="font-size:11px;">' + label + ': ' + escapeHtml(step.result || step.status) + '</span>';
  }

  function lastRun(a) {
    var steps = [a.signal, a.predict, a.trade];
    var last = null;
    steps.forEach(function (s) {
      if (s && s.last_run) {
        var d = new Date(s.last_run);
        if (!last || d > last) last = d;
      }
    });
    if (!last) return "—";
    return last.toISOString().replace("T", " ").slice(0, 19);
  }

  function bindActions() {
    document.querySelectorAll("[data-trigger]").forEach(function (b) {
      b.onclick = async function () {
        var accountId = b.dataset.trigger;
        b.disabled = true;
        b.textContent = "⏳ 触发中...";
        try {
          var resp = await fetch("/api/agent/accounts/" + accountId + "/trigger", {
            method: "POST",
            headers: { "Authorization": "Bearer " + FWUI.api.getToken() },
          });
          var json = await resp.json();
          if (!json.success) throw new Error(json.message);
          FWUI.toast.success("已触发：" + accountId);
          setTimeout(load, 2000);
        } catch (e) {
          FWUI.toast.error(e.message);
        } finally {
          b.disabled = false;
          b.textContent = "▶ 触发流水线";
        }
      };
    });
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  }

  window.addEventListener("beforeunload", function () { if (_timer) clearInterval(_timer); });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();