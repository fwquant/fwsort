// 任务状态页：拉取 /api/agent/tasks，5 秒自动刷新，支持列表/卡片切换
(function () {
  "use strict";

  var REFRESH_MS = 5000;
  var TASK_LABELS = {
    refresh_realtime_rank: { title: "实时榜刷新", desc: "每分钟把综合分写入 Redis ZSet", icon: "📊" },
    daily_snapshot: { title: "日榜快照", desc: "每日 00:05 固化榜单快照到 PG", icon: "📸" },
    daily_cleanup: { title: "每日清理", desc: "清理 fwsort:tmp:* 临时键", icon: "🧹" },
    archive_hot_to_cold: { title: "订单归档", desc: "90 天前订单从 PG 迁到 ES", icon: "🗃️" },
    follow_auto_copy: { title: "跟单同步", desc: "把 leader 最近一笔订单复制给粉丝", icon: "👥" },
    notify_scan: { title: "通知扫描", desc: "扫描风控/订阅异常推通知", icon: "🔔" },
    refresh_account_signals: { title: "账户信号刷新", desc: "每 5 分钟给所有账户生成 UP/DOWN/NEUTRAL", icon: "📡" },
    auto_predict_vote_trade: { title: "全账户预测-下单", desc: "每 1 分钟跑 V1.0 流水线（信号→MoA→投票→下单）", icon: "🧠" },
  };

  var _timer = null;
  var _tasksData = [];
  var view = localStorage.getItem("fwsort.tasksView") || "auto";

  function effectiveView() {
    if (view !== "auto") return view;
    return window.innerWidth < 768 ? "card" : "list";
  }

  async function init() {
    if (!window.__FW_DEMO_MODE__ && !FWUI.api.getToken()) {
      document.getElementById("tasks-cards-wrap").innerHTML = '<div class="fwui-empty-card"><div style="font-size:32px;margin-bottom:12px;">🔒</div><div style="margin-bottom:16px;">登录后查看任务状态</div><button class="fwui-btn fwui-btn--primary" onclick="FWUI.toast.info(\'请点击右上角登录\')">去登录</button></div>';
      return;
    }
    document.getElementById("btn-refresh-tasks").onclick = load;
    initViewDropdown();
    await load();
    _timer = setInterval(load, REFRESH_MS);
  }

  function initViewDropdown() {
    var dropdown = document.querySelector('[data-view-dropdown="tasks"]');
    if (!dropdown) return;
    var trigger = dropdown.querySelector(".fwui-select-dropdown__trigger");
    var menu = dropdown.querySelector(".fwui-select-dropdown__menu");
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
        localStorage.setItem("fwsort.tasksView", view);
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
      opt.classList.toggle("is-selected", v === val);
      if (v === val) {
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
      _tasksData = mockTasks();
      render();
      return;
    }
    try {
      var data = await FWUI.api.agentTasks();
      _tasksData = data.tasks || [];
      render();
    } catch (e) {
      document.getElementById("tasks-cards-wrap").innerHTML = '<div class="fwui-empty-card fwui-empty-card--error">' + escapeHtml(e.message) + '</div>';
    }
  }

  function mockTasks() {
    var now = new Date();
    function ago(mins) { return new Date(now.getTime() - mins * 60000).toISOString(); }
    return [
      { task: "refresh_realtime_rank", status: "ok", last_run_at: ago(1), last_result: JSON.stringify({ updated: 20 }) },
      { task: "daily_snapshot", status: "ok", last_run_at: ago(180), last_result: JSON.stringify({ updated: 20 }) },
      { task: "daily_cleanup", status: "ok", last_run_at: ago(360), last_result: JSON.stringify({ removed: 12 }) },
      { task: "archive_hot_to_cold", status: "ok", last_run_at: ago(420), last_result: JSON.stringify({ archived: 0, failed: 0 }) },
      { task: "follow_auto_copy", status: "ok", last_run_at: ago(3), last_result: JSON.stringify({ copied: 2 }) },
      { task: "notify_scan", status: "ok", last_run_at: ago(7), last_result: JSON.stringify({ pushed: 0 }) },
      { task: "refresh_account_signals", status: "ok", last_run_at: ago(2), last_result: JSON.stringify({ updated: 3, failed: 0 }) },
      { task: "auto_predict_vote_trade", status: "ok", last_run_at: ago(1), last_result: JSON.stringify({ success: 2, skipped: 1, failed: 0 }) },
    ];
  }

  function render() {
    var tasks = _tasksData;
    document.getElementById("tasks-count").textContent = "共 " + tasks.length + " 个任务";

    var v = effectiveView();
    var tableWrap = document.getElementById("tasks-table-wrap");
    var cardsWrap = document.getElementById("tasks-cards-wrap");
    var tableBody = document.getElementById("tasks-table-body");

    if (v === "list") {
      tableWrap.style.display = "";
      cardsWrap.style.display = "none";
      tableBody.innerHTML = tasks.map(tableRow).join("");
    } else {
      tableWrap.style.display = "none";
      cardsWrap.style.display = "";
      cardsWrap.innerHTML = '<div class="fwui-card-grid fwui-card-grid--wide">' + tasks.map(card).join("") + '</div>';
    }
    bindActions();
  }

  function tableRow(t) {
    var meta = TASK_LABELS[t.task] || { title: t.task, desc: "", icon: "⚙️" };
    var last = t.last_run_at || "—";
    var status = t.status || "unknown";
    var statusCls = status === "ok" ? "success" : status === "failed" ? "danger" : "warning";
    var statusLabel = status === "ok" ? "正常" : status === "failed" ? "失败" : status;
    var triggerable = ["refresh_account_signals", "auto_predict_vote_trade", "follow_auto_copy"].includes(t.task);
    var resultSummary = parseResult(t.last_result);

    return '<tr>' +
      '<td><span class="fwui-tag fwui-tag--' + statusCls + '">' + escapeHtml(statusLabel) + '</span></td>' +
      '<td><div class="fwui-cell-name">' + meta.icon + ' ' + escapeHtml(meta.title) + '</div></td>' +
      '<td><code style="font-size:11px;">' + escapeHtml(t.task) + '</code></td>' +
      '<td style="font-size:13px;color:var(--fwui-text-secondary);">' + escapeHtml(meta.desc) + '</td>' +
      '<td style="font-size:12px;">' + escapeHtml(last.replace("T", " ").slice(0, 19)) + '</td>' +
      '<td><code style="font-size:11px;">' + escapeHtml(resultSummary) + '</code></td>' +
      '<td>' + (triggerable ? '<button class="fwui-btn fwui-btn--sm fwui-btn--primary" data-trigger="' + escapeHtml(t.task) + '">▶ 触发</button>' : '—') + '</td>' +
      '</tr>';
  }

  function card(t) {
    var meta = TASK_LABELS[t.task] || { title: t.task, desc: "", icon: "⚙️" };
    var last = t.last_run_at || "—";
    var status = t.status || "unknown";
    var statusCls = status === "ok" ? "success" : status === "failed" ? "danger" : "";
    var triggerable = ["refresh_account_signals", "auto_predict_vote_trade", "follow_auto_copy"].includes(t.task);
    var resultSummary = parseResult(t.last_result);

    return '<div class="fwui-card task-card" data-task="' + escapeHtml(t.task) + '">' +
      '<div class="task-card__head">' +
        '<div class="task-card__icon">' + meta.icon + '</div>' +
        '<div class="task-card__title">' +
          '<div style="font-weight:700;font-size:15px;">' + escapeHtml(meta.title) + '</div>' +
          '<div style="font-size:12px;color:var(--fwui-text-muted);">' + escapeHtml(t.task) + '</div>' +
        '</div>' +
        '<span class="fwui-tag fwui-tag--' + statusCls + '">' + escapeHtml(status) + '</span>' +
      '</div>' +
      '<div class="task-card__desc">' + escapeHtml(meta.desc) + '</div>' +
      '<div class="task-card__meta">' +
        '<div><span style="color:var(--fwui-text-muted);">最近执行：</span>' + escapeHtml(last.replace("T", " ").slice(0, 19)) + '</div>' +
        '<div><span style="color:var(--fwui-text-muted);">结果：</span><code style="font-size:12px;">' + escapeHtml(resultSummary) + '</code></div>' +
      '</div>' +
      '<div class="task-card__actions">' +
        (triggerable ? '<button class="fwui-btn fwui-btn--sm fwui-btn--primary" data-trigger="' + escapeHtml(t.task) + '">▶ 立即触发</button>' : "") +
      '</div>' +
    '</div>';
  }

  function parseResult(lastResult) {
    if (!lastResult) return "—";
    try {
      var r = typeof lastResult === "string" ? JSON.parse(lastResult) : lastResult;
      var parts = [];
      if (r.updated !== undefined) parts.push("更新 " + r.updated);
      if (r.success !== undefined) parts.push("成功 " + r.success);
      if (r.skipped !== undefined) parts.push("跳过 " + r.skipped);
      if (r.failed !== undefined) parts.push("失败 " + r.failed);
      if (r.archived !== undefined) parts.push("归档 " + r.archived);
      if (r.copied !== undefined) parts.push("复制 " + r.copied);
      if (r.removed !== undefined) parts.push("清理 " + r.removed);
      if (r.pushed !== undefined) parts.push("推送 " + r.pushed);
      if (parts.length) return parts.join(" / ");
      return String(lastResult).slice(0, 80);
    } catch (_) {
      return String(lastResult).slice(0, 80);
    }
  }

  function bindActions() {
    document.querySelectorAll("[data-trigger]").forEach(function (b) {
      b.onclick = async function () {
        var task = b.dataset.trigger;
        b.disabled = true;
        b.textContent = "⏳ 触发中...";
        try {
          var resp = await fetch("/api/agent/tasks/" + task + "/trigger", {
            method: "POST",
            headers: { "Authorization": "Bearer " + FWUI.api.getToken() },
          });
          var json = await resp.json();
          if (!json.success) throw new Error(json.message);
          FWUI.toast.success("已触发：" + task);
          setTimeout(load, 1500);
        } catch (e) {
          FWUI.toast.error(e.message);
        } finally {
          b.disabled = false;
          b.textContent = "▶ 立即触发";
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