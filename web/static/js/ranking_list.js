// 榜单列表：支持列表/卡片风格切换、Tab 周期切换、平台筛选、排序、分页
// 支持双一级Tab（总榜单/我的榜单）、跟单按钮、演示模式角标
// 锁定区第4条 PC默认列表 / 移动默认卡片（自动按视口判断）
(function () {
  "use strict";

  // 一级Tab状态：global=总榜单, my=我的榜单
  let mainTab = "global";

  // 演示模式路径前缀
  const _demoPrefix = window.__FW_DEMO_PREFIX__ || "";

  const state = {
    rankType: "realtime",
    platform: "",
    myPlatform: "",
    sortBy: "composite",
    sortDir: "desc",
    page: 1,
    pageSize: 20,
    view: localStorage.getItem("fwsort.rankingView") || "auto", // auto|list|card
    myView: localStorage.getItem("fwsort.myRankingView") || "auto", // auto|list|card
  };

  // 排序字段映射（总榜单用 globalSortMap，我的榜单用 mySortMap）
  const globalSortMap = {
    rank: "composite",
    composite: "composite",
    return: "return",
    capital: "capital",
    trades: "composite",
  };
  const mySortMap = {
    rank: "composite",
    composite: "composite",
    return: "return",
    drawdown: "drawdown",
    sharpe: "sharpe",
    trades: "trades",
    execution: "execution",
  };

  // 实际渲染视图：auto 模式按视口宽度切换
  function effectiveView() {
    if (state.view !== "auto") return state.view;
    return window.innerWidth < 768 ? "card" : "list";
  }

  // 我的榜单实际渲染视图：auto 模式 PC 默认列表，MOBILE 默认卡片
  function effectiveMyView() {
    if (state.myView !== "auto") return state.myView;
    return window.innerWidth < 768 ? "card" : "list";
  }

  // 获取当前token
  function token() {
    return FWUI.api.getToken();
  }

  async function init() {
    bindUI();
    initMainTabFromUrl();
    updateMyTabState();
    loadLastRefreshTime();
  }

  // ========== 刷新按钮 ==========
  let _refreshTimer = null;

  function bindRefreshButton() {
    const btn = document.getElementById("btn-refresh");
    if (!btn) return;
    btn.addEventListener("click", () => handleRefresh());
  }

  function loadLastRefreshTime() {
    const saved = localStorage.getItem("fwsort.lastRefreshTime");
    if (saved) {
      updateRefreshTimeDisplay(saved);
    }
  }

  function updateRefreshTimeDisplay(timeStr) {
    const timeEl = document.getElementById("refresh-time");
    if (timeEl) {
      const time = timeStr || new Date().toISOString();
      const date = new Date(time);
      const hh = String(date.getHours()).padStart(2, "0");
      const mm = String(date.getMinutes()).padStart(2, "0");
      const ss = String(date.getSeconds()).padStart(2, "0");
      timeEl.textContent = `上次: ${hh}:${mm}:${ss}`;
      localStorage.setItem("fwsort.lastRefreshTime", time);
    }
  }

  function setRefreshStatus(text, type) {
    const statusEl = document.getElementById("refresh-status");
    const btn = document.getElementById("btn-refresh");
    if (!statusEl || !btn) return;

    if (text) {
      statusEl.textContent = text;
      statusEl.style.display = "";
      statusEl.className = "fwui-toolbar__refresh-status";
      if (type) statusEl.classList.add("is-" + type);
    } else {
      statusEl.style.display = "none";
    }

    if (type === "loading") {
      btn.classList.add("is-loading");
      btn.disabled = true;
    } else {
      btn.classList.remove("is-loading");
      btn.disabled = false;
    }
  }

  async function handleRefresh() {
    const btn = document.getElementById("btn-refresh");
    if (!btn || btn.classList.contains("is-loading")) return;

    setRefreshStatus("结算回查中...", "loading");

    try {
      const apiPath = window.__FW_DEMO_MODE__
        ? "/api/demo/ranking/strategy/refresh"
        : "/api/ranking/strategy/refresh";
      const result = await FWUI.api.post(apiPath);
      const data = result.data || result;

      const settlementInfo = data.settlement
        ? `结算${data.settlement.updated || 0}笔`
        : "";
      const rankingInfo = data.ranking
        ? `榜单更新${data.ranking.updated || 0}`
        : "";

      if (data.last_update) {
        updateRefreshTimeDisplay(data.last_update);
      }

      setRefreshStatus(`${settlementInfo} ${rankingInfo}`.trim() || "已刷新", "success");

      if (mainTab === "global") {
        loadGlobal();
      } else {
        loadMy();
      }

      FWUI.toast && FWUI.toast.success && FWUI.toast.success(
        `刷新完成${settlementInfo ? "（" + settlementInfo + "）" : ""}`
      );

      if (_refreshTimer) clearTimeout(_refreshTimer);
      _refreshTimer = setTimeout(() => setRefreshStatus("", ""), 5000);

    } catch (e) {
      console.error("刷新失败:", e);
      setRefreshStatus("刷新失败", "error");
      FWUI.toast && FWUI.toast.error && FWUI.toast.error("刷新失败: " + (e.message || "未知错误"));

      if (_refreshTimer) clearTimeout(_refreshTimer);
      _refreshTimer = setTimeout(() => setRefreshStatus("", ""), 8000);
    }
  }

  function initMainTabFromUrl() {
    const path = window.location.pathname;
    const isMyPage = path.endsWith("/my") || path.endsWith("/my/");
    if (isMyPage && token()) {
      switchMainTab("my", true);
    } else {
      switchMainTab("global", true);
    }
  }

  function bindUI() {
    // 一级Tab切换：总榜单 / 我的榜单
    document.querySelectorAll("[data-main-tab]").forEach((el) => {
      el.addEventListener("click", () => {
        const tab = el.dataset.mainTab;
        // 未登录时点击「我的榜单」弹登录框
        if (tab === "my" && !token()) {
          const btnLogin = document.getElementById("btn-login");
          if (btnLogin) btnLogin.click();
          return;
        }
        switchMainTab(tab);
      });
    });

    // 周期 Tab
    document.querySelectorAll("[data-rank-type]").forEach((el) => {
      el.addEventListener("click", () => {
        state.rankType = el.dataset.rankType;
        state.page = 1;
        document.querySelectorAll("[data-rank-type]").forEach((x) => x.classList.remove("fwui-tab--active"));
        el.classList.add("fwui-tab--active");
        loadGlobal();
      });
    });

    // 平台筛选（总榜单）
    document.querySelectorAll("[data-platform]").forEach((el) => {
      el.addEventListener("click", () => {
        state.platform = el.dataset.platform;
        state.page = 1;
        document.querySelectorAll("[data-platform]").forEach((x) => x.classList.remove("fwui-tag--primary"));
        el.classList.add("fwui-tag--primary");
        loadGlobal();
      });
    });

    // 平台筛选（我的榜单）
    document.querySelectorAll("[data-my-platform]").forEach((el) => {
      el.addEventListener("click", () => {
        state.myPlatform = el.dataset.myPlatform;
        document.querySelectorAll("[data-my-platform]").forEach((x) => x.classList.remove("fwui-tag--primary"));
        el.classList.add("fwui-tag--primary");
        loadMy();
      });
    });

    // ========== 移动端排序下拉框 ==========
    function initSortDropdown(dropdownEl, options, onSelect) {
      const trigger = dropdownEl.querySelector(".fwui-select-dropdown__trigger");
      if (!trigger) return;

      trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        const isOpen = dropdownEl.classList.contains("is-open");
        document.querySelectorAll(".fwui-select-dropdown.is-open").forEach((d) => d.classList.remove("is-open"));
        if (!isOpen) dropdownEl.classList.add("is-open");
      });

      options.forEach((opt) => {
        opt.addEventListener("click", (e) => {
          e.stopPropagation();
          onSelect(opt);
          dropdownEl.classList.remove("is-open");
          options.forEach((o) => o.classList.remove("is-selected"));
          opt.classList.add("is-selected");
        });
      });
    }

    // 总榜单排序下拉框
    const globalSortDropdown = document.querySelector('[data-sort-dropdown="global"]');
    if (globalSortDropdown) {
      const sortOptions = globalSortDropdown.querySelectorAll("[data-sort-value]");
      initSortDropdown(globalSortDropdown, sortOptions, (opt) => {
        const val = opt.dataset.sortValue;
        const [field, dir] = val.split("_");
        state.sortBy = field;
        state.sortDir = dir;
        const labelEl = globalSortDropdown.querySelector("[data-sort-label]");
        if (labelEl) labelEl.textContent = opt.querySelector(".fwui-select-dropdown__option-name").textContent;
        updateSortIndicators();
        loadGlobal();
      });
    }

    // 我的榜单排序下拉框
    const mySortDropdown = document.querySelector('[data-sort-dropdown="my"]');
    if (mySortDropdown) {
      const mySortOptions = mySortDropdown.querySelectorAll("[data-my-sort-value]");
      initSortDropdown(mySortDropdown, mySortOptions, (opt) => {
        const val = opt.dataset.mySortValue;
        const [field, dir] = val.split("_");
        state.sortBy = field;
        state.sortDir = dir;
        const labelEl = mySortDropdown.querySelector("[data-my-sort-label]");
        if (labelEl) labelEl.textContent = opt.querySelector(".fwui-select-dropdown__option-name").textContent;
        updateSortIndicators();
        loadMy();
      });
    }

    // 表头点击排序（桌面端）
    document.querySelectorAll("th[data-sort-key]").forEach((th) => {
      th.style.cursor = "pointer";
      th.addEventListener("click", () => {
        const key = th.dataset.sortKey;
        const sortMap = mainTab === "global" ? globalSortMap : mySortMap;
        const mapped = sortMap[key] || "composite";
        if (state.sortBy === mapped) {
          state.sortDir = state.sortDir === "desc" ? "asc" : "desc";
        } else {
          state.sortBy = mapped;
          state.sortDir = "desc";
        }
        updateSortIndicators();
        syncSortDropdown();
        if (mainTab === "global") loadGlobal();
        else loadMy();
      });
    });

    // ========== 下拉框交互：总榜单视图 + 我的榜单视图 ==========
    function initDropdown(dropdownEl, options, onSelect) {
      const trigger = dropdownEl.querySelector(".fwui-select-dropdown__trigger");
      const menu = dropdownEl.querySelector(".fwui-select-dropdown__menu");
      if (!trigger || !menu) return;

      trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        const isOpen = dropdownEl.classList.contains("is-open");
        // 关闭所有其他下拉框
        document.querySelectorAll(".fwui-select-dropdown.is-open").forEach((d) => d.classList.remove("is-open"));
        if (!isOpen) dropdownEl.classList.add("is-open");
      });

      options.forEach((opt) => {
        opt.addEventListener("click", (e) => {
          e.stopPropagation();
          const value = opt.dataset.view || opt.dataset.myView;
          onSelect(value);
          dropdownEl.classList.remove("is-open");
          // 更新选中样式
          options.forEach((o) => o.classList.remove("is-selected"));
          opt.classList.add("is-selected");
          // 更新 trigger 文本
          const labelEl = dropdownEl.querySelector("[data-view-label], [data-my-view-label]");
          if (labelEl) labelEl.textContent = opt.querySelector(".fwui-select-dropdown__option-name")?.textContent || value;
        });
      });
    }

    // 总榜单视图下拉框
    const globalDropdown = document.querySelector('[data-view-dropdown="global"]');
    if (globalDropdown) {
      const globalOptions = globalDropdown.querySelectorAll("[data-view]");
      initDropdown(globalDropdown, globalOptions, (value) => {
        state.view = value;
        localStorage.setItem("fwsort.rankingView", state.view);
        renderGlobal();
      });
    }

    // 我的榜单视图下拉框
    const myDropdown = document.querySelector('[data-view-dropdown="my"]');
    if (myDropdown) {
      const myOptions = myDropdown.querySelectorAll("[data-my-view]");
      initDropdown(myDropdown, myOptions, (value) => {
        state.myView = value;
        localStorage.setItem("fwsort.myRankingView", state.myView);
        renderMy();
      });
    }

    // 点击页面其他地方关闭下拉框
    document.addEventListener("click", () => {
      document.querySelectorAll(".fwui-select-dropdown.is-open").forEach((d) => d.classList.remove("is-open"));
    });

    // 视口变化时 auto 模式重新渲染
    let resizeTimer;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (state.view === "auto" && mainTab === "global") renderGlobal();
        if (state.myView === "auto" && mainTab === "my") renderMy();
      }, 150);
    });

    // 全局跟单按钮委托（弹出选择框）
    document.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-follow-uid]");
      if (!btn) return;
      e.stopPropagation();
      const uid = btn.dataset.followUid;
      openFollowModal(uid);
    });

    // 初始化下拉框显示状态（与本地存储同步）
    function syncDropdownState(dropdownEl, currentValue, labelAttr) {
      if (!dropdownEl) return;
      const options = dropdownEl.querySelectorAll("[data-view], [data-my-view]");
      options.forEach((opt) => {
        const val = opt.dataset.view || opt.dataset.myView;
        opt.classList.toggle("is-selected", val === currentValue);
        if (val === currentValue) {
          const labelEl = dropdownEl.querySelector(labelAttr);
          if (labelEl) labelEl.textContent = opt.querySelector(".fwui-select-dropdown__option-name")?.textContent || val;
        }
      });
    }
    syncDropdownState(globalDropdown, state.view, "[data-view-label]");
    syncDropdownState(myDropdown, state.myView, "[data-my-view-label]");

    // 绑定刷新按钮
    bindRefreshButton();
  }

  // 切换一级Tab
  function switchMainTab(tab, skipUrlUpdate) {
    mainTab = tab;
    document.querySelectorAll("[data-main-tab]").forEach((x) => {
      x.classList.toggle("fwui-tab--active", x.dataset.mainTab === tab);
    });
    const panelGlobal = document.getElementById("panel-global");
    const panelMy = document.getElementById("panel-my");
    if (tab === "global") {
      if (panelGlobal) panelGlobal.style.display = "";
      if (panelMy) panelMy.style.display = "none";
      loadGlobal();
      if (!skipUrlUpdate) {
        history.pushState(null, "", _demoPrefix + "/global");
      }
    } else {
      if (panelGlobal) panelGlobal.style.display = "none";
      if (panelMy) panelMy.style.display = "";
      loadMy();
      if (!skipUrlUpdate) {
        history.pushState(null, "", _demoPrefix + "/my");
      }
    }
  }

  // 更新「我的榜单」Tab的可用状态
  function updateMyTabState() {
    const myTab = document.querySelector('[data-main-tab="my"]');
    if (!myTab) return;
    if (!token()) {
      myTab.classList.add("fwui-tab--disabled");
      myTab.title = "请先登录";
    } else {
      myTab.classList.remove("fwui-tab--disabled");
      myTab.title = "";
    }
  }

  // 更新表头排序指示器
  function updateSortIndicators() {
    document.querySelectorAll("th[data-sort-key]").forEach((th) => {
      const sortMap = mainTab === "global" ? globalSortMap : mySortMap;
      const mapped = sortMap[th.dataset.sortKey];
      if (mapped === state.sortBy) {
        th.dataset.sortDir = state.sortDir;
      } else {
        delete th.dataset.sortDir;
      }
    });
  }

  // ========== 总榜单 ==========

  // 加载总榜单数据
  async function loadGlobal() {
    showGlobalLoading();
    try {
      const data = await FWUI.api.get("/api/ranking/global", {
        rank_type: state.rankType,
        page: state.page,
        page_size: state.pageSize,
        platform: state.platform,
        sort_by: state.sortBy,
        sort_dir: state.sortDir,
      });
      state._globalData = data;
      renderGlobal();
      renderPager(data);
    } catch (e) {
      showGlobalError(e.message);
    }
  }

  function showGlobalLoading() {
    const tb = document.getElementById("ranking-body");
    const cg = document.getElementById("ranking-cards");
    if (tb) tb.innerHTML = `<tr><td colspan="10" class="fwui-empty"><div class="fwui-skeleton" style="width:80px;">加载中</div></td></tr>`;
    if (cg) cg.innerHTML = `<div class="fwui-empty-card">⏳ 加载中...</div>`;
  }

  function showGlobalError(msg) {
    const tb = document.getElementById("ranking-body");
    const cg = document.getElementById("ranking-cards");
    if (tb) tb.innerHTML = `<tr><td colspan="10" class="fwui-empty fwui-empty--error">${escapeHtml(msg)}</td></tr>`;
    if (cg) cg.innerHTML = `<div class="fwui-empty-card fwui-empty-card--error">${escapeHtml(msg)}</div>`;
  }

  function showGlobalEmpty() {
    const tb = document.getElementById("ranking-body");
    const cg = document.getElementById("ranking-cards");
    if (tb) tb.innerHTML = `<tr><td colspan="10" class="fwui-empty">📊 暂无数据</td></tr>`;
    if (cg) cg.innerHTML = `<div class="fwui-empty-card">📊 暂无数据</div>`;
  }

  // 渲染总榜单
  function renderGlobal() {
    const data = state._globalData;
    if (!data) return;
    if (!data.items || data.items.length === 0) {
      showGlobalEmpty();
      return;
    }

    const view = effectiveView();
    const tableWrap = document.getElementById("ranking-table-wrap");
    const cardsWrap = document.getElementById("ranking-cards");
    const body = document.getElementById("ranking-body");

    if (view === "card") {
      tableWrap.style.display = "none";
      cardsWrap.style.display = "";
      cardsWrap.innerHTML = data.items.map(rowGlobalCard).join("");
    } else {
      tableWrap.style.display = "";
      cardsWrap.style.display = "none";
      body.innerHTML = data.items.map(rowGlobalTable).join("");
    }
  }

  // 总榜单表格行（PC）— 用户级数据
  function rowGlobalTable(it) {
    const t = FWUI.utils.tier(it.avg_score || 0);
    const retCls = (it.avg_return || 0) >= 0 ? "fwui-up" : "fwui-down";
    const followBtn = token()
      ? `<button class="fwui-btn fwui-btn--sm fwui-btn--primary" data-follow-uid="${it.uid}" onclick="event.stopPropagation();">跟单</button>`
      : `<button class="fwui-btn fwui-btn--sm" onclick="event.stopPropagation();document.getElementById('btn-login')?.click();">跟单</button>`;
    const capitalStr = it.total_capital >= 10000
      ? `$${(it.total_capital / 1000).toFixed(1)}K`
      : `$${FWUI.utils.fmtNumber(it.total_capital, 0)}`;
    return `
      <tr class="fwui-row-clickable">
        <td><span class="fwui-rank-badge fwui-rank-badge--${rankCls(it.rank)}">#${it.rank}</span></td>
        <td><div class="fwui-cell-name">${escapeHtml(it.user_name || it.uid)}</div></td>
        <td><span class="fwui-platform-tag">${escapeHtml(it.platform || "-")}</span></td>
        <td><strong class="fwui-score">${FWUI.utils.fmtNumber(it.avg_score, 2)}</strong></td>
        <td class="${retCls}">${FWUI.utils.fmtPercent(it.avg_return, 2)}</td>
        <td>${capitalStr}</td>
        <td>${it.trade_count || 0}</td>
        <td><span class="fwui-tier fwui-tier--${t.cls}">${t.name}</span></td>
        <td>${followBtn}</td>
      </tr>
    `;
  }

  // 总榜单卡片（手机为主）— 用户级数据
  function rowGlobalCard(it) {
    const t = FWUI.utils.tier(it.avg_score || 0);
    const retCls = (it.avg_return || 0) >= 0 ? "fwui-up" : "fwui-down";
    const retSign = (it.avg_return || 0) >= 0 ? "+" : "";
    const annPct = ((it.avg_return || 0) * 100).toFixed(2);
    const capitalStr = it.total_capital >= 10000
      ? `$${(it.total_capital / 1000).toFixed(1)}K`
      : `$${FWUI.utils.fmtNumber(it.total_capital, 0)}`;
    const rankBg = rankCls(it.rank);
    const followBtn = token()
      ? `<button class="fwui-btn fwui-btn--sm fwui-btn--primary" data-follow-uid="${it.uid}" onclick="event.stopPropagation();">跟单</button>`
      : `<button class="fwui-btn fwui-btn--sm" onclick="event.stopPropagation();document.getElementById('btn-login')?.click();">跟单</button>`;

    return `
      <div class="ranking-card">
        <div class="ranking-card__head">
          <div class="ranking-card__rank">
            <span class="fwui-rank-badge fwui-rank-badge--${rankBg}">#${it.rank}</span>
            <span class="fwui-platform-tag">${escapeHtml(it.platform || "-")}</span>
          </div>
          <span class="fwui-tier fwui-tier--${t.cls}">${t.name}</span>
        </div>
        <div class="ranking-card__name">${escapeHtml(it.user_name || it.uid)}</div>

        <div class="ranking-card__score">
          <div class="ranking-card__score-label">综合分</div>
          <div class="ranking-card__score-value">${FWUI.utils.fmtNumber(it.avg_score, 2)}</div>
        </div>

        <div class="ranking-card__stats">
          <div class="ranking-card__stat">
            <div class="ranking-card__stat-label">平均收益率</div>
            <div class="ranking-card__stat-value ${retCls}">${retSign}${annPct}%</div>
          </div>
          <div class="ranking-card__stat">
            <div class="ranking-card__stat-label">资金量</div>
            <div class="ranking-card__stat-value">${capitalStr}</div>
          </div>
          <div class="ranking-card__stat">
            <div class="ranking-card__stat-label">账户数</div>
            <div class="ranking-card__stat-value">${it.account_count || 0}</div>
          </div>
          <div class="ranking-card__stat">
            <div class="ranking-card__stat-label">交易笔数</div>
            <div class="ranking-card__stat-value">${it.trade_count || 0}</div>
          </div>
        </div>

        <div class="ranking-card__footer">
          <div class="ranking-card__footer-cell">
            ${followBtn}
          </div>
        </div>
      </div>
    `;
  }

  // ========== 我的榜单 ==========

  // 加载我的榜单数据
  async function loadMy() {
    showMyLoading();
    try {
      const data = await FWUI.api.get("/api/ranking/my", {
        platform: state.myPlatform,
        sort_by: state.sortBy,
        sort_dir: state.sortDir,
      });
      state._myData = data;
      renderMy();
    } catch (e) {
      showMyError(e.message);
    }
  }

  function showMyLoading() {
    const tb = document.getElementById("my-ranking-body");
    const cg = document.getElementById("my-ranking-cards");
    if (tb) tb.innerHTML = `<tr><td colspan="9" class="fwui-empty"><div class="fwui-skeleton" style="width:80px;">加载中</div></td></tr>`;
    if (cg) cg.innerHTML = `<div class="fwui-empty-card">⏳ 加载中...</div>`;
  }

  function showMyError(msg) {
    const tb = document.getElementById("my-ranking-body");
    const cg = document.getElementById("my-ranking-cards");
    if (tb) tb.innerHTML = `<tr><td colspan="9" class="fwui-empty fwui-empty--error">${escapeHtml(msg)}</td></tr>`;
    if (cg) cg.innerHTML = `<div class="fwui-empty-card fwui-empty-card--error">${escapeHtml(msg)}</div>`;
  }

  function showMyEmpty() {
    const tb = document.getElementById("my-ranking-body");
    const cg = document.getElementById("my-ranking-cards");
    if (tb) tb.innerHTML = `<tr><td colspan="9" class="fwui-empty">📊 暂无数据</td></tr>`;
    if (cg) cg.innerHTML = `<div class="fwui-empty-card">📊 暂无数据</div>`;
  }

  // 渲染我的榜单
  function renderMy() {
    const data = state._myData;
    if (!data) return;
    if (!data.items || data.items.length === 0) {
      showMyEmpty();
      return;
    }

    const view = effectiveMyView();
    const tableWrap = document.getElementById("my-ranking-table-wrap");
    const cardsWrap = document.getElementById("my-ranking-cards");
    const body = document.getElementById("my-ranking-body");

    if (view === "card") {
      if (tableWrap) tableWrap.style.display = "none";
      if (cardsWrap) cardsWrap.style.display = "";
      cardsWrap.innerHTML = data.items.map(rowMyCard).join("");
    } else {
      if (tableWrap) tableWrap.style.display = "";
      if (cardsWrap) cardsWrap.style.display = "none";
      body.innerHTML = data.items.map(rowMyTable).join("");
    }
  }

  // 我的榜单表格行
  function rowMyTable(it) {
    const t = FWUI.utils.tier(it.composite_score || 0);
    const retCls = (it.annualized_return || 0) >= 0 ? "fwui-up" : "fwui-down";
    return `
      <tr onclick="location.href='${_demoPrefix}/detail?uid=${it.uid}'" class="fwui-row-clickable">
        <td><span class="fwui-rank-badge fwui-rank-badge--${rankCls(it.rank)}">#${it.rank}</span></td>
        <td><div class="fwui-cell-name">${escapeHtml(it.name || it.uid)}</div></td>
        <td><span class="fwui-platform-tag">${escapeHtml(it.platform || "-")}</span></td>
        <td><strong class="fwui-score">${FWUI.utils.fmtNumber(it.composite_score, 2)}</strong></td>
        <td class="${retCls}">${FWUI.utils.fmtPercent(it.annualized_return, 2)}</td>
        <td class="fwui-down">${FWUI.utils.fmtPercent(it.max_drawdown, 2)}</td>
        <td>${FWUI.utils.fmtNumber(it.sharpe_ratio, 2)}</td>
        <td>${it.trade_count || 0}</td>
        <td>
          <div class="fwui-cell-tier">
            <span class="fwui-tier fwui-tier--${t.cls}">${t.name}</span>
            <div class="fwui-progress" title="执行质量 ${Math.round((it.execution_score || 0) * 100)}%">
              <div class="fwui-progress__bar" style="width:${(it.execution_score || 0) * 100}%"></div>
            </div>
          </div>
        </td>
      </tr>
    `;
  }

  // 我的榜单卡片
  function rowMyCard(it) {
    const t = FWUI.utils.tier(it.composite_score || 0);
    const retCls = (it.annualized_return || 0) >= 0 ? "fwui-up" : "fwui-down";
    const retSign = (it.annualized_return || 0) >= 0 ? "+" : "";
    const execPct = ((it.execution_score || 0) * 100).toFixed(0);
    const ddPct = ((it.max_drawdown || 0) * 100).toFixed(2);
    const annPct = ((it.annualized_return || 0) * 100).toFixed(2);
    const rankBg = rankCls(it.rank);

    return `
      <div class="ranking-card" onclick="location.href='${_demoPrefix}/detail?uid=${it.uid}'">
        <div class="ranking-card__head">
          <div class="ranking-card__rank">
            <span class="fwui-rank-badge fwui-rank-badge--${rankBg}">#${it.rank}</span>
            <span class="fwui-platform-tag">${escapeHtml(it.platform || "-")}</span>
          </div>
          <span class="fwui-tier fwui-tier--${t.cls}">${t.name}</span>
        </div>
        <div class="ranking-card__name">${escapeHtml(it.name || it.uid)}</div>
        <div class="ranking-card__score">
          <div class="ranking-card__score-label">综合分</div>
          <div class="ranking-card__score-value">${FWUI.utils.fmtNumber(it.composite_score, 2)}</div>
        </div>
        <div class="ranking-card__stats">
          <div class="ranking-card__stat">
            <div class="ranking-card__stat-label">年化收益</div>
            <div class="ranking-card__stat-value ${retCls}">${retSign}${annPct}%</div>
          </div>
          <div class="ranking-card__stat">
            <div class="ranking-card__stat-label">最大回撤</div>
            <div class="ranking-card__stat-value fwui-down">${ddPct}%</div>
          </div>
          <div class="ranking-card__stat">
            <div class="ranking-card__stat-label">执行质量</div>
            <div class="ranking-card__stat-value">${execPct}%</div>
          </div>
        </div>
        <div class="ranking-card__footer">
          <div class="ranking-card__footer-cell">
            <span class="ranking-card__footer-label">笔数</span>
            <span class="ranking-card__footer-value">${it.trade_count || 0}</span>
          </div>
          <div class="ranking-card__exec">
            <div class="ranking-card__exec-head">
              <span class="ranking-card__footer-label">执行质量</span>
              <span class="ranking-card__exec-value">${execPct}%</span>
            </div>
            <div class="ranking-card__exec-bar">
              <div class="ranking-card__exec-fill" style="width:${execPct}%"></div>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  // ========== 跟单弹框 ==========

  function openFollowModal(uid) {
    const form = document.createElement("div");
    form.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:14px;">
        <div style="font-size:14px;color:var(--fwui-text-secondary);">跟单用户：<strong>${escapeHtml(uid)}</strong></div>
        <label style="display:flex;flex-direction:column;gap:4px;">
          <span>订阅模式</span>
          <select class="fwui-input" name="mode" style="padding:8px;">
            <option value="3">订阅+分成（推荐）</option>
            <option value="1">纯订阅</option>
            <option value="2">纯分成</option>
          </select>
        </label>
        <label style="display:flex;flex-direction:column;gap:4px;">
          <span>每笔跟单金额 (USDT)</span>
          <input class="fwui-input" name="amount" type="number" min="1" value="50" step="10">
        </label>
        <label style="display:flex;flex-direction:column;gap:4px;">
          <span>订阅月数</span>
          <select class="fwui-input" name="months" style="padding:8px;">
            <option value="1">1 个月</option>
            <option value="3">3 个月</option>
            <option value="6">6 个月</option>
            <option value="12">12 个月</option>
          </select>
        </label>
      </div>
    `;
    FWUI.modal.confirm({
      title: "跟单订阅",
      content: form,
      okText: "确认跟单",
      onOk: async () => {
        const mode = parseInt(form.querySelector("[name=mode]").value);
        const amount = parseFloat(form.querySelector("[name=amount]").value);
        const months = parseInt(form.querySelector("[name=months]").value);
        if (!amount || amount <= 0) { FWUI.toast.error("请输入有效金额"); throw new Error("validation"); }
        try {
          const isDemo = window.__FW_DEMO_MODE__;
          if (isDemo) {
            FWUI.toast.success("演示模式：跟单订阅模拟成功");
            return;
          }
          await FWUI.api.post("/api/follow/subscribe", { leader_uid: uid, mode, amount, months });
          FWUI.toast.success("跟单订阅成功");
        } catch (err) {
          FWUI.toast.error(err.message || "跟单失败");
          throw err;
        }
      },
    });
  }

  // ========== 通用工具函数 ==========

  function rankCls(rank) {
    if (rank === 1) return "gold";
    if (rank === 2) return "silver";
    if (rank === 3) return "bronze";
    if (rank <= 10) return "top";
    return "normal";
  }

  // 分页（仅总榜单使用）
  function renderPager(data) {
    const el = document.getElementById("ranking-pager");
    if (!el) return;
    const total = data.total || 0;
    const totalPage = Math.max(1, Math.ceil(total / state.pageSize));
    if (totalPage <= 1) { el.innerHTML = ""; return; }
    let html = "";
    for (let p = 1; p <= totalPage; p++) {
      html += `<button class="fwui-pagination__item ${p === state.page ? 'fwui-pagination__item--active' : ''}" data-page="${p}">${p}</button>`;
    }
    el.innerHTML = html;
    el.querySelectorAll("[data-page]").forEach((b) => {
      b.onclick = () => { state.page = parseInt(b.dataset.page); loadGlobal(); window.scrollTo({ top: 0, behavior: "smooth" }); };
    });
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function handlePopState() {
    initMainTabFromUrl();
  }

  window.addEventListener("popstate", handlePopState);

  // 暴露给全局供 detail 页用
  window.RankingList = { init, loadGlobal, loadMy, switchMainTab };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();