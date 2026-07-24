// 榜单列表：支持列表/卡片风格切换、Tab 周期切换、平台筛选、排序、分页
(function () {
  "use strict";

  const state = {
    rankType: "realtime",
    platform: "",
    sortBy: "composite",
    page: 1,
    pageSize: 20,
    view: localStorage.getItem("fwsort.rankingView") || "list", // 锁定区第4条：默认列表
  };

  // 初始化
  function init() {
    bindUI();
    load();
  }

  // 事件绑定
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
        load();
      });
    });

    // 列表/卡片切换（锁定区第4条 PC默认list/移动默认card）
    document.querySelectorAll("[data-view]").forEach((el) => {
      el.addEventListener("click", () => {
        state.view = el.dataset.view;
        localStorage.setItem("fwsort.rankingView", state.view);
        document.querySelectorAll("[data-view]").forEach((x) => x.classList.remove("fwui-btn--primary"));
        el.classList.add("fwui-btn--primary");
        render();
      });
      // 初始化高亮
      el.classList.toggle("fwui-btn--primary", el.dataset.view === state.view);
    });
  }

  // 加载榜单数据
  async function load() {
    const container = document.getElementById("ranking-body");
    if (!container) return;
    container.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:24px;">加载中...</td></tr>`;
    try {
      const data = await FWUI.api.rankingList({
        rank_type: state.rankType,
        page: state.page,
        page_size: state.pageSize,
        platform: state.platform,
        sort_by: state.sortBy,
      });
      state._data = data;
      render();
      renderPager(data);
    } catch (e) {
      container.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:24px;color:var(--fwui-danger);">${e.message}</td></tr>`;
    }
  }

  // 渲染
  function render() {
    const data = state._data;
    if (!data) return;
    const container = document.getElementById("ranking-body");
    if (!data.items || data.items.length === 0) {
      container.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:24px;">暂无数据</td></tr>`;
      return;
    }

    // 移动端强制卡片（锁定区第2条）
    const isMobile = window.innerWidth < 768;
    const useCard = isMobile || state.view === "card";

    if (useCard) {
      container.innerHTML = data.items.map(rowCard).join("");
    } else {
      container.innerHTML = data.items.map(rowTable).join("");
    }
  }

  // 列表行
  function rowTable(it) {
    const t = FWUI.utils.tier(it.composite_score || 0);
    return `
      <tr onclick="location.href='/detail?uid=${it.uid}'" style="cursor:pointer;">
        <td><strong>#${it.rank}</strong></td>
        <td>${escapeHtml(it.name || it.uid)}</td>
        <td>${escapeHtml(it.platform || "-")}</td>
        <td><strong>${FWUI.utils.fmtNumber(it.composite_score, 2)}</strong></td>
        <td class="${(it.annualized_return || 0) >= 0 ? 'fwui-up' : 'fwui-down'}">${FWUI.utils.fmtPercent(it.annualized_return, 2)}</td>
        <td class="fwui-down">${FWUI.utils.fmtPercent(it.max_drawdown, 2)}</td>
        <td>${FWUI.utils.fmtNumber(it.sharpe_ratio, 2)}</td>
        <td>${it.trade_count || 0}</td>
        <td>
          <span class="fwui-tag fwui-tag--primary">${t.name}</span>
          <div class="fwui-progress" style="margin-top:4px;width:80px;">
            <div class="fwui-progress__bar" style="width:${(it.execution_score || 0) * 100}%"></div>
          </div>
        </td>
      </tr>
    `;
  }

  // 卡片视图
  function rowCard(it) {
    const t = FWUI.utils.tier(it.composite_score || 0);
    return `
      <div class="fwui-card" style="margin-bottom:12px;cursor:pointer;" onclick="location.href='/detail?uid=${it.uid}'">
        <div style="display:flex;justify-content:space-between;align-items:start;gap:8px;">
          <div>
            <div style="font-size:12px;color:var(--fwui-text-muted);">#${it.rank} · ${escapeHtml(it.platform || "-")}</div>
            <div style="font-size:16px;font-weight:600;margin-top:4px;">${escapeHtml(it.name || it.uid)}</div>
          </div>
          <span class="fwui-tier fwui-tier--${t.cls}">${t.name}</span>
        </div>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px;font-size:13px;">
          <div><div style="color:var(--fwui-text-muted);font-size:11px;">年化</div><div class="${(it.annualized_return||0)>=0?'fwui-up':'fwui-down'}">${FWUI.utils.fmtPercent(it.annualized_return, 2)}</div></div>
          <div><div style="color:var(--fwui-text-muted);font-size:11px;">回撤</div><div class="fwui-down">${FWUI.utils.fmtPercent(it.max_drawdown, 2)}</div></div>
          <div><div style="color:var(--fwui-text-muted);font-size:11px;">夏普</div><div>${FWUI.utils.fmtNumber(it.sharpe_ratio, 2)}</div></div>
          <div><div style="color:var(--fwui-text-muted);font-size:11px;">胜率</div><div>${FWUI.utils.fmtPercent(it.win_rate, 1)}</div></div>
          <div><div style="color:var(--fwui-text-muted);font-size:11px;">笔数</div><div>${it.trade_count || 0}</div></div>
          <div><div style="color:var(--fwui-text-muted);font-size:11px;">综合分</div><div><strong>${FWUI.utils.fmtNumber(it.composite_score, 2)}</strong></div></div>
        </div>
        <div style="margin-top:8px;">
          <div style="font-size:11px;color:var(--fwui-text-muted);">执行质量分</div>
          <div class="fwui-progress"><div class="fwui-progress__bar" style="width:${(it.execution_score||0)*100}%"></div></div>
        </div>
      </div>
    `;
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
      b.onclick = () => { state.page = parseInt(b.dataset.page); load(); };
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
