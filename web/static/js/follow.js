// 跟单管理页：支持列表/卡片切换、搜索过滤、导出
(function () {
  "use strict";

  const state = {
    marketView: localStorage.getItem("fwsort.followMarketView") || "auto", // auto|list|card
    myView: localStorage.getItem("fwsort.followMyView") || "auto",
    marketData: null,
    myData: null,
    marketSearch: "",
    mySearch: "",
    marketSortBy: localStorage.getItem("fwsort.followMarketSortBy") || "",
    marketSortDir: localStorage.getItem("fwsort.followMarketSortDir") || "desc",
  };

  // 排序字段映射：data-sort-key -> 数据字段名
  const sortFieldMap = {
    rank: "rank",
    name: "name",
    composite: "composite_score",
    return: "annualized_return",
    drawdown: "max_drawdown",
    winrate: "win_rate",
  };

  // 各字段默认排序方向（首次点击）
  const sortDefaultDir = {
    rank: "asc",
    name: "asc",
    composite: "desc",
    return: "desc",
    drawdown: "asc",
    winrate: "desc",
  };

  function effectiveView(viewKey) {
    const v = state[viewKey];
    if (v !== "auto") return v;
    return window.innerWidth < 768 ? "card" : "list";
  }

  async function init() {
    bindTabs();
    bindToolbar();
    initTabFromUrl();
  }

  function initTabFromUrl() {
    var initialTab = window.__FW_INITIAL_TAB__;
    if (initialTab === "my") {
      switchTab("my");
    } else {
      loadMarket();
    }
  }

  function switchTab(tab) {
    document.querySelectorAll("[data-tab]").forEach(function (el) {
      var isActive = el.dataset.tab === tab;
      el.classList.toggle("fwui-tab--active", isActive);
    });
    document.getElementById("tab-market").style.display = tab === "market" ? "" : "none";
    document.getElementById("tab-my").style.display = tab === "my" ? "" : "none";
    if (tab === "market") {
      loadMarket();
    } else if (tab === "my") {
      loadMy();
    }
  }

  function bindTabs() {
    document.querySelectorAll("[data-tab]").forEach((el) => {
      el.onclick = () => { switchTab(el.dataset.tab); };
    });
  }

  // ========== 工具栏绑定 ==========
  function bindToolbar() {
    // 初始化下拉框显示（加载 localStorage 中的值）
    syncDropdownLabel("market", state.marketView);
    syncDropdownLabel("my", state.myView);

    // 下拉框触发器：展开 / 收起
    document.querySelectorAll(".fwui-select-dropdown").forEach((dd) => {
      const toggle = dd.querySelector("[data-dropdown-toggle]");
      if (toggle) {
        toggle.addEventListener("click", (e) => {
          e.stopPropagation();
          const willOpen = !dd.classList.contains("is-open");
          closeAllDropdowns();
          if (willOpen) dd.classList.add("is-open");
        });
      }
    });

    // 全局点击收起
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".fwui-select-dropdown")) closeAllDropdowns();
    });

    // 选项点击：市场视图
    document.querySelectorAll('.fwui-select-dropdown[data-section="market"] [data-dropdown-option]').forEach((opt) => {
      opt.addEventListener("click", () => {
        const val = opt.dataset.dropdownOption;
        state.marketView = val;
        localStorage.setItem("fwsort.followMarketView", val);
        syncDropdownLabel("market", val);
        closeAllDropdowns();
        renderMarket();
      });
    });

    // 选项点击：我的订阅视图
    document.querySelectorAll('.fwui-select-dropdown[data-section="my"] [data-dropdown-option]').forEach((opt) => {
      opt.addEventListener("click", () => {
        const val = opt.dataset.dropdownOption;
        state.myView = val;
        localStorage.setItem("fwsort.followMyView", val);
        syncDropdownLabel("my", val);
        closeAllDropdowns();
        renderMy();
      });
    });

    // 表头点击排序（跟单市场列表视图）
    document.querySelectorAll("#market-table th[data-sort-key]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sortKey;
        if (state.marketSortBy === key) {
          state.marketSortDir = state.marketSortDir === "desc" ? "asc" : "desc";
        } else {
          state.marketSortBy = key;
          state.marketSortDir = sortDefaultDir[key] || "desc";
        }
        localStorage.setItem("fwsort.followMarketSortBy", state.marketSortBy);
        localStorage.setItem("fwsort.followMarketSortDir", state.marketSortDir);
        updateSortIndicators();
        renderMarket();
      });
    });
    updateSortIndicators();

    // 搜索过滤
    const marketSearchEl = document.getElementById("market-search");
    if (marketSearchEl) {
      marketSearchEl.addEventListener("input", (e) => {
        state.marketSearch = e.target.value.trim().toLowerCase();
        renderMarket();
      });
    }
    const mySearchEl = document.getElementById("my-search");
    if (mySearchEl) {
      mySearchEl.addEventListener("input", (e) => {
        state.mySearch = e.target.value.trim().toLowerCase();
        renderMy();
      });
    }

    // 导出
    document.getElementById("market-download").addEventListener("click", () => downloadCsv("market"));
    document.getElementById("my-download").addEventListener("click", () => downloadCsv("my"));

    // 视口变化时 auto 模式重新渲染
    let resizeTimer;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (state.marketView === "auto") renderMarket();
        if (state.myView === "auto") renderMy();
      }, 150);
    });
  }

  // ========== 下拉框辅助 ==========
  function closeAllDropdowns() {
    document.querySelectorAll(".fwui-select-dropdown.is-open").forEach((d) => d.classList.remove("is-open"));
  }

  function syncDropdownLabel(section, value) {
    const dd = document.querySelector(`.fwui-select-dropdown[data-section="${section}"]`);
    if (!dd) return;
    dd.dataset.current = value;
    const currentEl = dd.querySelector("[data-dropdown-current]");
    const option = dd.querySelector(`[data-dropdown-option="${value}"]`);
    const nameMap = { auto: "自动", list: "列表", card: "卡片" };
    if (currentEl) {
      const nameEl = option && option.querySelector(".fwui-select-dropdown__option-name");
      currentEl.textContent = nameEl ? nameEl.textContent : (nameMap[value] || value);
    }
    // 高亮选中
    dd.querySelectorAll("[data-dropdown-option]").forEach((o) => {
      o.classList.toggle("is-selected", o.dataset.dropdownOption === value);
    });
  }

  // ========== 跟单市场 ==========
  async function loadMarket() {
    showLoading("market");
    try {
      const data = await FWUI.api.followMarket({ rank_type: "all_time", limit: 50 });
      state.marketData = data.items || [];
      renderMarket();
    } catch (e) {
      showError("market", e.message);
    }
  }

  function renderMarket() {
    let items = filterItems(state.marketData, state.marketSearch, ["name", "uid", "platform"]);
    items = sortItems(items, state.marketSortBy, state.marketSortDir);
    const view = effectiveView("marketView");
    const tableWrap = document.getElementById("market-table-wrap");
    const cardsWrap = document.getElementById("market-cards");
    const body = document.getElementById("market-body");

    if (!items || items.length === 0) {
      if (tableWrap) tableWrap.style.display = "none";
      if (cardsWrap) {
        cardsWrap.style.display = "";
        cardsWrap.innerHTML = `<div class="fwui-empty-card">📊 暂无数据</div>`;
      }
      return;
    }

    if (view === "card") {
      if (tableWrap) tableWrap.style.display = "none";
      if (cardsWrap) {
        cardsWrap.style.display = "";
        cardsWrap.innerHTML = items.map(marketCard).join("");
        cardsWrap.querySelectorAll("[data-action=subscribe]").forEach((b) => b.onclick = openSubscribeModal);
      }
    } else {
      if (tableWrap) tableWrap.style.display = "";
      if (cardsWrap) cardsWrap.style.display = "none";
      if (body) {
        body.innerHTML = items.map(marketRow).join("");
        body.querySelectorAll("[data-action=subscribe]").forEach((b) => b.onclick = openSubscribeModal);
      }
    }
  }

  function marketCard(it) {
    const t = FWUI.utils.tier(it.composite_score || 0);
    const retCls = (it.annualized_return || 0) >= 0 ? "fwui-up" : "fwui-down";
    return `
      <div class="fwui-card product-card">
        <div class="product-card__head">
          <div class="product-card__avatar product-card__avatar--success">📈</div>
          <div style="flex:1;min-width:0;">
            <div class="product-card__title">${escapeHtml(it.name || it.uid)}</div>
            <div class="product-card__subtitle">#${it.rank} · ${escapeHtml(it.platform || "-")} · <span class="fwui-tier fwui-tier--${t.cls}">${t.name}</span></div>
          </div>
        </div>
        <div class="product-card__stats">
          <div>
            <div class="product-card__stat-label">胜率</div>
            <div class="product-card__stat-value">${FWUI.utils.fmtPercent(it.win_rate, 1)}</div>
          </div>
          <div>
            <div class="product-card__stat-label">年化</div>
            <div class="product-card__stat-value ${retCls}">${FWUI.utils.fmtPercent(it.annualized_return, 1)}</div>
          </div>
          <div>
            <div class="product-card__stat-label">回撤</div>
            <div class="product-card__stat-value fwui-down">${FWUI.utils.fmtPercent(it.max_drawdown, 1)}</div>
          </div>
        </div>
        <div class="product-card__price-row">
          <div class="product-card__price-info">
            <div class="product-card__price-label">订阅 + 分成</div>
            <div class="product-card__price">$9.9<small>/月 + 20%</small></div>
          </div>
          <button class="fwui-btn fwui-btn--primary fwui-btn--sm" data-action="subscribe" data-uid="${escapeHtml(it.uid)}">订阅跟单</button>
        </div>
      </div>
    `;
  }

  function marketRow(it) {
    const t = FWUI.utils.tier(it.composite_score || 0);
    const retCls = (it.annualized_return || 0) >= 0 ? "fwui-up" : "fwui-down";
    return `
      <tr class="fwui-row-clickable">
        <td><span class="fwui-rank-badge fwui-rank-badge--${rankCls(it.rank)}">#${it.rank}</span></td>
        <td>
          <div class="fwui-cell-name">${escapeHtml(it.name || it.uid)}</div>
        </td>
        <td><span class="fwui-platform-tag">${escapeHtml(it.platform || "-")}</span></td>
        <td><strong class="fwui-score">${FWUI.utils.fmtNumber(it.composite_score, 2)}</strong></td>
        <td class="${retCls}">${FWUI.utils.fmtPercent(it.annualized_return, 1)}</td>
        <td class="fwui-down">${FWUI.utils.fmtPercent(it.max_drawdown, 1)}</td>
        <td>${FWUI.utils.fmtPercent(it.win_rate, 1)}</td>
        <td><button class="fwui-btn fwui-btn--primary fwui-btn--sm" data-action="subscribe" data-uid="${escapeHtml(it.uid)}">订阅</button></td>
      </tr>
    `;
  }

  // ========== 我的订阅 ==========
  async function loadMy() {
    const cardsWrap = document.getElementById("my-cards");
    if (!FWUI.api.getToken()) {
      const html = `<div class="fwui-empty-card" style="padding:48px;">🔒 登录后查看您的订阅</div>`;
      if (cardsWrap) cardsWrap.innerHTML = html;
      const body = document.getElementById("my-body");
      if (body) body.innerHTML = `<tr><td colspan="7" class="fwui-empty">🔒 登录后查看您的订阅</td></tr>`;
      return;
    }
    showLoading("my");
    try {
      const resp = await fetch("/api/follow/my", { headers: { "Authorization": "Bearer " + FWUI.api.getToken() } });
      const data = await resp.json();
      if (!data.success) throw new Error(data.message);
      state.myData = data.data.subscriptions || [];
      renderMy();
    } catch (e) {
      showError("my", e.message);
    }
  }

  function renderMy() {
    const items = filterItems(state.myData, state.mySearch, ["leader_name", "leader_uid"]);
    const view = effectiveView("myView");
    const tableWrap = document.getElementById("my-table-wrap");
    const cardsWrap = document.getElementById("my-cards");
    const body = document.getElementById("my-body");

    if (!items || items.length === 0) {
      if (tableWrap) tableWrap.style.display = "none";
      if (cardsWrap) {
        cardsWrap.style.display = "";
        cardsWrap.innerHTML = `<div class="fwui-empty-card">📋 还没有订阅</div>`;
      }
      return;
    }

    if (view === "card") {
      if (tableWrap) tableWrap.style.display = "none";
      if (cardsWrap) {
        cardsWrap.style.display = "";
        cardsWrap.innerHTML = items.map(myCard).join("");
        cardsWrap.querySelectorAll("[data-action=cancel]").forEach((b) => b.onclick = cancelSub);
      }
    } else {
      if (tableWrap) tableWrap.style.display = "";
      if (cardsWrap) cardsWrap.style.display = "none";
      if (body) {
        body.innerHTML = items.map(myRow).join("");
        body.querySelectorAll("[data-action=cancel]").forEach((b) => b.onclick = cancelSub);
      }
    }
  }

  function myCard(s) {
    return `
      <div class="fwui-card product-card">
        <div class="product-card__head">
          <div class="product-card__avatar product-card__avatar--warning">👥</div>
          <div style="flex:1;min-width:0;">
            <div class="product-card__title">${escapeHtml(s.leader_name || s.leader_uid)}</div>
            <div class="product-card__subtitle">${s.subscription_fee_usd} USD/月 · 分成 ${s.profit_share_ratio * 100}%</div>
          </div>
        </div>
        <div class="product-card__stats">
          <div>
            <div class="product-card__stat-label">已跟单</div>
            <div class="product-card__stat-value">${s.total_followed} 笔</div>
          </div>
          <div>
            <div class="product-card__stat-label">累计盈亏</div>
            <div class="product-card__stat-value ${s.total_pnl >= 0 ? 'fwui-up' : 'fwui-down'}">${FWUI.utils.fmtUsd(s.total_pnl, 2)}</div>
          </div>
          <div>
            <div class="product-card__stat-label">状态</div>
            <div class="product-card__stat-value">${s.status === 1 ? "✅ 订阅中" : "已取消"}</div>
          </div>
        </div>
        <div class="product-card__price-row">
          <div class="product-card__price-info">
            <div class="product-card__price-label">到期时间</div>
            <div class="product-card__price" style="font-size:13px;font-weight:500;">${s.expires_at || "永久"}</div>
          </div>
          ${s.status === 1 ? `<button class="fwui-btn fwui-btn--sm" data-action="cancel" data-id="${s.id}" style="color:var(--fwui-danger);">取消订阅</button>` : ""}
        </div>
      </div>
    `;
  }

  function myRow(s) {
    return `
      <tr class="fwui-row-clickable">
        <td><div class="fwui-cell-name">${escapeHtml(s.leader_name || s.leader_uid)}</div></td>
        <td>$${s.subscription_fee_usd || 0}/月</td>
        <td>${(s.profit_share_ratio || 0) * 100}%</td>
        <td>${s.total_followed || 0} 笔</td>
        <td class="${s.total_pnl >= 0 ? 'fwui-up' : 'fwui-down'}">${FWUI.utils.fmtUsd(s.total_pnl, 2)}</td>
        <td>${s.status === 1 ? "✅ 订阅中" : "已取消"}</td>
        <td>
          <div style="display:flex;align-items:center;gap:8px;">
            <span>${s.expires_at || "永久"}</span>
            ${s.status === 1 ? `<button class="fwui-btn fwui-btn--sm" data-action="cancel" data-id="${s.id}" style="color:var(--fwui-danger);">取消</button>` : ""}
          </div>
        </td>
      </tr>
    `;
  }

  // ========== 通用辅助 ==========
  function filterItems(items, keyword, fields) {
    if (!items) return [];
    if (!keyword) return items;
    return items.filter((it) => fields.some((f) => String(it[f] || "").toLowerCase().includes(keyword)));
  }

  function sortItems(items, sortBy, sortDir) {
    if (!sortBy || !items) return items;
    const field = sortFieldMap[sortBy];
    if (!field) return items;
    const dir = sortDir === "asc" ? 1 : -1;
    return [...items].sort((a, b) => {
      let av = a[field];
      let bv = b[field];
      if (typeof av === "string" && typeof bv === "string") {
        return av.localeCompare(bv, "zh-CN") * dir;
      }
      av = av == null ? 0 : Number(av);
      bv = bv == null ? 0 : Number(bv);
      return (av - bv) * dir;
    });
  }

  function updateSortIndicators() {
    document.querySelectorAll("#market-table th[data-sort-key]").forEach((th) => {
      const key = th.dataset.sortKey;
      if (key === state.marketSortBy) {
        th.dataset.sortDir = state.marketSortDir;
      } else {
        delete th.dataset.sortDir;
      }
    });
  }

  function showLoading(section) {
    const body = document.getElementById(section + "-body");
    const cards = document.getElementById(section + "-cards");
    if (body) body.innerHTML = `<tr><td colspan="8" class="fwui-empty"><div class="fwui-skeleton" style="width:80px;">加载中</div></td></tr>`;
    if (cards) cards.innerHTML = `<div class="fwui-empty-card">⏳ 加载中...</div>`;
  }

  function showError(section, msg) {
    const body = document.getElementById(section + "-body");
    const cards = document.getElementById(section + "-cards");
    if (body) body.innerHTML = `<tr><td colspan="8" class="fwui-empty fwui-empty--error">${escapeHtml(msg)}</td></tr>`;
    if (cards) cards.innerHTML = `<div class="fwui-empty-card fwui-empty-card--error">${escapeHtml(msg)}</div>`;
  }

  function rankCls(rank) {
    if (rank === 1) return "gold";
    if (rank === 2) return "silver";
    if (rank === 3) return "bronze";
    return "normal";
  }

  // ========== 导出 CSV ==========
  function downloadCsv(section) {
    const items = section === "market" ? filterItems(state.marketData, state.marketSearch, ["name", "uid", "platform"]) : filterItems(state.myData, state.mySearch, ["leader_name", "leader_uid"]);
    if (!items || items.length === 0) {
      FWUI.toast.warn("没有可导出的数据");
      return;
    }
    let csv = "";
    if (section === "market") {
      csv = "\uFEFF排名,名称,UID,平台,综合分,年化收益,最大回撤,胜率\n";
      items.forEach((it) => {
        csv += `${it.rank || ""},${escapeCsv(it.name || it.uid)},${it.uid},${it.platform || ""},${it.composite_score || 0},${it.annualized_return || 0},${it.max_drawdown || 0},${it.win_rate || 0}\n`;
      });
    } else {
      csv = "\uFEFF交易员,UID,订阅费/月,分成比例,已跟单笔数,累计盈亏,状态,到期时间\n";
      items.forEach((it) => {
        csv += `${escapeCsv(it.leader_name || it.leader_uid)},${it.leader_uid},${it.subscription_fee_usd || 0},${(it.profit_share_ratio || 0) * 100}%,${it.total_followed || 0},${it.total_pnl || 0},${it.status === 1 ? "订阅中" : "已取消"},${it.expires_at || "永久"}\n`;
      });
    }
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `follow_${section}_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    FWUI.toast.success("导出成功");
  }

  function escapeCsv(s) {
    if (s == null) return "";
    const str = String(s);
    if (str.includes(",") || str.includes('"') || str.includes("\n")) {
      return '"' + str.replace(/"/g, '""') + '"';
    }
    return str;
  }

  // 订阅弹框
  function openSubscribeModal(e) {
    const uid = e.currentTarget.dataset.uid;
    const form = document.createElement("div");
    form.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:12px;">
        <p>订阅交易员 <strong>${escapeHtml(uid)}</strong></p>
        <label>订阅模式
          <select class="fwui-select" name="mode">
            <option value="1">纯订阅费 ($9.9/月)</option>
            <option value="2">纯利润分成 (盈利抽 20%)</option>
            <option value="3" selected>订阅+分成 (推荐)</option>
          </select>
        </label>
        <label>跟单金额 (USDT) <input class="fwui-input" name="amount" type="number" value="50" min="5"></label>
        <label>订阅月数 <input class="fwui-input" name="months" type="number" value="1" min="1" max="12"></label>
        <div style="font-size:12px;color:var(--fwui-text-muted);">⚠️ 模拟盘：不真实扣费</div>
      </div>
    `;
    FWUI.modal.confirm({
      title: "订阅跟单",
      content: form,
      onOk: async () => {
        const mode = parseInt(form.querySelector("[name=mode]").value);
        const amount = parseFloat(form.querySelector("[name=amount]").value);
        const months = parseInt(form.querySelector("[name=months]").value);
        try {
          await FWUI.api.followSubscribe({ leader_uid: uid, mode, amount, months });
          FWUI.toast.success("订阅成功");
        } catch (e) { FWUI.toast.error(e.message); throw e; }
      },
    });
  }

  // 取消订阅
  async function cancelSub(e) {
    const id = parseInt(e.currentTarget.dataset.id);
    const confirmed = await FWUI.modal.confirm({
      title: "取消订阅",
      content: "取消后，该交易员的跟单将不再自动执行。确定要取消吗？",
      type: "warning",
      okText: "确认取消",
    });
    if (!confirmed) return;
    try {
      await FWUI.api.followCancel(id);
      FWUI.toast.success("已取消");
      await loadMy();
    } catch (e) { FWUI.toast.error(e.message); }
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();