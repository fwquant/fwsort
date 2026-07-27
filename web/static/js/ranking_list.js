// 榜单列表：支持列表/卡片风格切换、Tab 周期切换、平台筛选、排序、分页
// 锁定区第4条 PC默认列表 / 移动默认卡片（自动按视口判断）
(function () {
  "use strict";

  const state = {
    rankType: "realtime",
    platform: "",
    sortBy: "composite",
    sortDir: "desc",
    page: 1,
    pageSize: 20,
    view: localStorage.getItem("fwsort.rankingView") || "auto", // auto|list|card
  };

  // 排序字段映射
  const sortKeyMap = {
    rank: "composite",
    composite: "composite",
    return: "return",
    drawdown: "drawdown",
    sharpe: "sharpe",
    trades: "trades",
  };

  // 实际渲染视图：auto 模式按视口宽度切换
  function effectiveView() {
    if (state.view !== "auto") return state.view;
    return window.innerWidth < 768 ? "card" : "list";
  }

  function init() {
    bindUI();
    load();
  }

  function bindUI() {
    // 周期 Tab
    document.querySelectorAll("[data-rank-type]").forEach((el) => {
      el.addEventListener("click", () => {
        state.rankType = el.dataset.rankType;
        state.page = 1;
        document.querySelectorAll("[data-rank-type]").forEach((x) => x.classList.remove("fwui-tab--active"));
        el.classList.add("fwui-tab--active");
        load();
      });
    });

    // 平台筛选
    document.querySelectorAll("[data-platform]").forEach((el) => {
      el.addEventListener("click", () => {
        state.platform = el.dataset.platform;
        state.page = 1;
        document.querySelectorAll("[data-platform]").forEach((x) => x.classList.remove("fwui-tag--primary"));
        el.classList.add("fwui-tag--primary");
        load();
      });
    });

    // 排序
    document.querySelectorAll("[data-sort]").forEach((el) => {
      el.addEventListener("click", () => {
        state.sortBy = el.dataset.sort;
        state.sortDir = "desc";
        updateSortIndicators();
        document.querySelectorAll("[data-sort]").forEach((x) => x.classList.remove("fwui-btn--primary"));
        el.classList.add("fwui-btn--primary");
        load();
      });
    });

    // 表头点击排序
    document.querySelectorAll("th[data-sort-key]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sortKey;
        const mapped = sortKeyMap[key] || "composite";
        if (state.sortBy === mapped) {
          state.sortDir = state.sortDir === "desc" ? "asc" : "desc";
        } else {
          state.sortBy = mapped;
          state.sortDir = "desc";
        }
        // 同步顶部排序按钮高亮
        document.querySelectorAll("[data-sort]").forEach((x) => {
          x.classList.toggle("fwui-btn--primary", x.dataset.sort === state.sortBy);
        });
        updateSortIndicators();
        load();
      });
    });

    // 列表/卡片/自动 切换
    document.querySelectorAll("[data-view]").forEach((el) => {
      el.addEventListener("click", () => {
        state.view = el.dataset.view;
        localStorage.setItem("fwsort.rankingView", state.view);
        document.querySelectorAll("[data-view]").forEach((x) => x.classList.remove("fwui-btn--primary"));
        el.classList.add("fwui-btn--primary");
        render();
      });
    });

    // 视口变化时 auto 模式重新渲染
    let resizeTimer;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (state.view === "auto") render();
      }, 150);
    });
  }

  // 更新表头排序指示器
  function updateSortIndicators() {
    document.querySelectorAll("th[data-sort-key]").forEach((th) => {
      const mapped = sortKeyMap[th.dataset.sortKey];
      if (mapped === state.sortBy) {
        th.dataset.sortDir = state.sortDir;
      } else {
        delete th.dataset.sortDir;
      }
    });
  }

  // 加载榜单数据
  async function load() {
    showLoading();
    try {
      const data = await FWUI.api.rankingList({
        rank_type: state.rankType,
        page: state.page,
        page_size: state.pageSize,
        platform: state.platform,
        sort_by: state.sortBy,
        sort_dir: state.sortDir,
      });
      state._data = data;
      render();
      renderPager(data);
    } catch (e) {
      showError(e.message);
    }
  }

  function showLoading() {
    const tb = document.getElementById("ranking-body");
    const cg = document.getElementById("ranking-cards");
    if (tb) tb.innerHTML = `<tr><td colspan="9" class="fwui-empty"><div class="fwui-skeleton" style="width:80px;">加载中</div></td></tr>`;
    if (cg) cg.innerHTML = `<div class="fwui-empty-card">⏳ 加载中...</div>`;
  }

  function showError(msg) {
    const tb = document.getElementById("ranking-body");
    const cg = document.getElementById("ranking-cards");
    if (tb) tb.innerHTML = `<tr><td colspan="9" class="fwui-empty fwui-empty--error">${escapeHtml(msg)}</td></tr>`;
    if (cg) cg.innerHTML = `<div class="fwui-empty-card fwui-empty-card--error">${escapeHtml(msg)}</div>`;
  }

  function showEmpty() {
    const tb = document.getElementById("ranking-body");
    const cg = document.getElementById("ranking-cards");
    if (tb) tb.innerHTML = `<tr><td colspan="9" class="fwui-empty">📊 暂无数据</td></tr>`;
    if (cg) cg.innerHTML = `<div class="fwui-empty-card">📊 暂无数据</div>`;
  }

  // 渲染入口
  function render() {
    const data = state._data;
    if (!data) return;
    if (!data.items || data.items.length === 0) {
      showEmpty();
      return;
    }

    const view = effectiveView();
    const tableWrap = document.getElementById("ranking-table-wrap");
    const cardsWrap = document.getElementById("ranking-cards");
    const body = document.getElementById("ranking-body");

    if (view === "card") {
      tableWrap.style.display = "none";
      cardsWrap.style.display = "";
      cardsWrap.innerHTML = data.items.map(rowCard).join("");
    } else {
      tableWrap.style.display = "";
      cardsWrap.style.display = "none";
      body.innerHTML = data.items.map(rowTable).join("");
    }
  }

  // ========== 列表行（PC）==========
  function rowTable(it) {
    const t = FWUI.utils.tier(it.composite_score || 0);
    const retCls = (it.annualized_return || 0) >= 0 ? "fwui-up" : "fwui-down";
    return `
      <tr onclick="location.href='/detail?uid=${it.uid}'" class="fwui-row-clickable">
        <td><span class="fwui-rank-badge fwui-rank-badge--${rankCls(it.rank)}">#${it.rank}</span></td>
        <td>
          <div class="fwui-cell-name">${escapeHtml(it.name || it.uid)}</div>
        </td>
        <td><span class="fwui-platform-tag">${escapeHtml(it.platform || "-")}</span></td>
        <td><strong class="fwui-score">${FWUI.utils.fmtNumber(it.composite_score, 2)}</strong></td>
        <td class="${retCls}">${FWUI.utils.fmtPercent(it.annualized_return, 2)}</td>
        <td class="fwui-down">${FWUI.utils.fmtPercent(it.max_drawdown, 2)}</td>
        <td>${FWUI.utils.fmtNumber(it.sharpe_ratio, 2)}</td>
        <td>${it.trade_count || 0}</td>
        <td>
          <div class="fwui-cell-tier">
            <span class="fwui-tier fwui-tier--${t.cls}">${t.name}</span>
            <div class="fwui-progress" title="执行质量 ${(it.execution_score || 0) * 100 | 0}%">
              <div class="fwui-progress__bar" style="width:${(it.execution_score || 0) * 100}%"></div>
            </div>
          </div>
        </td>
      </tr>
    `;
  }

  // ========== 卡片（手机为主，PC 也可）==========
  function rowCard(it) {
    const t = FWUI.utils.tier(it.composite_score || 0);
    const retCls = (it.annualized_return || 0) >= 0 ? "fwui-up" : "fwui-down";
    const retSign = (it.annualized_return || 0) >= 0 ? "+" : "";
    const execPct = ((it.execution_score || 0) * 100).toFixed(0);
    const ddPct = ((it.max_drawdown || 0) * 100).toFixed(2);
    const winPct = ((it.win_rate || 0) * 100).toFixed(1);
    const annPct = ((it.annualized_return || 0) * 100).toFixed(2);
    const rankBg = rankCls(it.rank);

    return `
      <div class="ranking-card" onclick="location.href='/detail?uid=${it.uid}'">
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
            <div class="ranking-card__stat-label">夏普比率</div>
            <div class="ranking-card__stat-value">${FWUI.utils.fmtNumber(it.sharpe_ratio, 2)}</div>
          </div>
          <div class="ranking-card__stat">
            <div class="ranking-card__stat-label">胜率</div>
            <div class="ranking-card__stat-value">${winPct}%</div>
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

  function rankCls(rank) {
    if (rank === 1) return "gold";
    if (rank === 2) return "silver";
    if (rank === 3) return "bronze";
    if (rank <= 10) return "top";
    return "normal";
  }

  // 分页
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
      b.onclick = () => { state.page = parseInt(b.dataset.page); load(); window.scrollTo({ top: 0, behavior: "smooth" }); };
    });
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // 暴露给全局供 detail 页用
  window.RankingList = { init, load };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
