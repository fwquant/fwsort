// 我的账户页：列表 + 创建 + 触发投票 + 排序 + 搜索 + 类型过滤
// PC 默认列表 / 移动端默认卡片，支持下拉切换
(function () {
  "use strict";

  let _accountsCache = null;
  let _filteredData = null;
  let _viewMode = "list";
  let _sortState = { key: null, dir: "asc" };
  let _searchQuery = "";
  let _typeFilter = -1;

  async function init() {
    bindUI();
    _viewMode = detectDefaultView();
    const switchEl = document.getElementById("view-switch");
    if (switchEl) switchEl.value = _viewMode;

    if (!FWUI.api.getToken()) {
      document.getElementById("accounts-view").innerHTML = `
        <div class="fwui-empty-card">
          <div style="font-size:32px;margin-bottom:12px;">🔒</div>
          <div style="margin-bottom:16px;">登录后查看您的执行账户</div>
          <button class="fwui-btn fwui-btn--primary" onclick="FWUI.auth.openLoginModal()">去登录</button>
        </div>
      `;
      return;
    }
    await load();
  }

  function bindUI() {
    document.getElementById("btn-create-account").onclick = openCreateModal;

    // 演示模式：把"任务状态"按钮链接加 /demo 前缀
    const tasksBtn = document.getElementById("btn-go-tasks");
    if (tasksBtn && window.__FW_DEMO_PREFIX__) {
      tasksBtn.setAttribute("href", window.__FW_DEMO_PREFIX__ + "/myrank/tasks");
    }

    const switchEl = document.getElementById("view-switch");
    if (switchEl) {
      switchEl.onchange = (e) => {
        _viewMode = e.target.value;
        render();
      };
    }

    const searchEl = document.getElementById("account-search");
    if (searchEl) {
      searchEl.oninput = (e) => {
        _searchQuery = e.target.value.trim().toLowerCase();
        applyFiltersAndSort();
        render();
      };
    }

    const typeEl = document.getElementById("account-type-filter");
    if (typeEl) {
      typeEl.onchange = (e) => {
        _typeFilter = parseInt(e.target.value);
        applyFiltersAndSort();
        render();
      };
    }
  }

  function detectDefaultView() {
    return window.innerWidth >= 768 ? "list" : "card";
  }

  async function load() {
    const container = document.getElementById("accounts-view");
    container.innerHTML = `<div class="fwui-empty-card">⏳ 加载中...</div>`;
    try {
      // 演示模式：未登录时使用 MOCK 数据填充
      if (window.__FW_DEMO_MODE__ && !FWUI.api.getToken()) {
        _accountsCache = mockAccounts();
      } else {
        const data = await FWUI.api.myAccounts();
        _accountsCache = data;
      }
      applyFiltersAndSort();
      render();
    } catch (e) {
      container.innerHTML = `<div class="fwui-empty-card fwui-empty-card--error">${escapeHtml(e.message)}</div>`;
    }
  }

  function mockAccounts() {
    const sigs = ["UP", "DOWN", "NEUTRAL"];
    const names = ["量化王", "趋势猎手", "波段大师", "套利先锋", "价值捕手", "日内高手", "加密游侠", "AI猎人"];
    const platforms = ["polymarket", "okx"];
    return {
      count: 5,
      accounts: names.slice(0, 5).map((n, i) => ({
        id: 9000 + i,
        uid: `ACC-DEMO${1000 + i}`,
        name: `${n}（演示）`,
        platform: platforms[i % 2],
        account_type: 0,
        current_balance: 1000 + i * 250,
        daily_pnl: (i % 2 === 0 ? 1 : -1) * (10 + i * 5),
        risk_frozen: false,
        status: 0,
        target_url: i % 2 === 0 ? `https://www.okx.com/trade-spot/btc-usdt` : `https://polymarket.com/event/demo-${i}`,
        target_symbol: i % 2 === 0 ? "BTC-USDT" : `POLY-DEMO-${i}`,
        order_amount_usd: 50,
        signal: sigs[i % 3],
        signal_source: "random",
        signal_updated_at: new Date().toISOString(),
        last_order_at: new Date(Date.now() - 3600 * 1000).toISOString(),
        public_enabled: true,
        created_at: new Date(Date.now() - 86400 * 1000 * 7).toISOString(),
      })),
    };
  }

  // 对缓存数据应用过滤 + 排序，生成 _filteredData
  function applyFiltersAndSort() {
    if (!_accountsCache) return;
    let list = [..._accountsCache.accounts];

    // 1. 类型过滤（模拟盘/实盘）
    if (_typeFilter !== -1) {
      list = list.filter((a) => a.account_type === _typeFilter);
    }

    // 2. 模糊搜索（账户名 / UID / 平台）
    if (_searchQuery) {
      list = list.filter((a) => {
        const hay = `${a.name || ""} ${a.uid || ""} ${a.platform || ""}`.toLowerCase();
        return hay.includes(_searchQuery);
      });
    }

    // 3. 排序（仅列表视图下有意义，但统一在这里处理）
    if (_sortState.key) {
      const k = _sortState.key;
      const d = _sortState.dir === "asc" ? 1 : -1;
      list.sort((a, b) => {
        let av = a[k];
        let bv = b[k];
        // 对可能为 undefined 的字段做兜底
        if (av === undefined) av = "";
        if (bv === undefined) bv = "";
        // 数字型直接比较
        if (typeof av === "number" && typeof bv === "number") {
          return (av - bv) * d;
        }
        // 布尔型转数字
        if (typeof av === "boolean") av = av ? 1 : 0;
        if (typeof bv === "boolean") bv = bv ? 1 : 0;
        // 字符串比较
        return String(av).localeCompare(String(bv), "zh-CN") * d;
      });
    }

    _filteredData = { count: list.length, accounts: list };
  }

  function render() {
    const container = document.getElementById("accounts-view");
    if (!_filteredData) return;

    document.getElementById("account-count").textContent =
      `共 ${_filteredData.count} 个账户${_searchQuery || _typeFilter !== -1 ? "（已过滤）" : ""}`;

    if (_filteredData.count === 0) {
      container.innerHTML = `
        <div class="fwui-empty-card">
          <div style="font-size:32px;margin-bottom:12px;">💼</div>
          <div style="margin-bottom:16px;">
            ${_accountsCache && _accountsCache.count === 0 ? "还没有执行账户" : "没有匹配的账户"}
          </div>
          ${_accountsCache && _accountsCache.count === 0
            ? `<button class="fwui-btn fwui-btn--primary" onclick="document.getElementById('btn-create-account').click()">立即创建</button>`
            : ""}
        </div>
      `;
      return;
    }

    if (_viewMode === "list") {
      container.className = "accounts-view accounts-view--list";
      container.innerHTML = renderList(_filteredData);
      bindListActions();
      bindSortHeaders();
    } else {
      container.className = "accounts-view accounts-view--card fwui-card-grid fwui-card-grid--wide";
      container.innerHTML = renderCards(_filteredData);
      bindCardActions();
      loadSparklines();
    }
  }

  // ========== 资金曲线 Sparkline ==========
  async function loadSparklines() {
    const sparkEls = document.querySelectorAll("[data-account-id]");
    for (const el of sparkEls) {
      const accId = el.getAttribute("data-account-id");
      try {
        const res = await FWUI.api.equityCurve(accId, 50);
        if (res && res.data && res.data.curve && res.data.curve.length > 1) {
          renderSparkline(el, res.data.curve);
        } else {
          el.querySelector("text").textContent = "暂无交易数据";
        }
      } catch (e) {
        el.querySelector("text").textContent = "曲线加载失败";
      }
    }
  }

  function renderSparkline(el, curve) {
    const values = curve.map((p) => p.cumulative);
    const w = 200, h = 40;
    const min = Math.min(...values, 0);
    const max = Math.max(...values, 0);
    const range = max - min || 1;
    const points = values.map((v, i) => {
      const x = (i / (values.length - 1)) * w;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    const color = values[values.length - 1] >= 0 ? "var(--fwui-up)" : "var(--fwui-down)";
    el.innerHTML = `
      <svg width="100%" height="40" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <polyline fill="none" stroke="${color}" stroke-width="1.5" points="${points}"/>
        <line x1="0" y1="${h - 2 - ((0 - min) / range) * (h - 4)}" x2="${w}" y2="${h - 2 - ((0 - min) / range) * (h - 4)}" stroke="var(--fwui-border)" stroke-width="0.5" stroke-dasharray="2 2"/>
      </svg>
    `;
  }

  // ========== 卡片视图 ==========
  function renderCards(data) {
    return data.accounts.map(card).join("");
  }

  function card(a) {
    const platColor = a.platform === "polymarket" ? "primary" : "warning";
    const frozenTag = a.risk_frozen
      ? `<span class="fwui-tag fwui-tag--danger">风控冻结</span>`
      : `<span class="fwui-tag fwui-tag--success">正常</span>`;
    const pnlCls = a.daily_pnl >= 0 ? "fwui-up" : "fwui-down";
    const sigColor = a.signal === "UP" ? "success" : a.signal === "DOWN" ? "danger" : "";
    const publicTag = a.public_enabled
      ? `<span class="fwui-tag fwui-tag--primary" title="参与总榜单">🌍 公开</span>`
      : `<span class="fwui-tag" title="不参与总榜单">🔒 私有</span>`;
    // 绩效指标
    const totalPnl = a.total_pnl || 0;
    const totalPnlCls = totalPnl >= 0 ? "fwui-up" : "fwui-down";
    const winRate = (a.win_rate || 0) * 100;
    const winRateCls = winRate >= 50 ? "fwui-up" : "fwui-down";
    const tradeCount = a.trade_count || 0;
    const score = a.composite_score || 0;
    const scoreCls = score >= 60 ? "fwui-up" : (score >= 30 ? "" : "fwui-down");
    const sharpe = a.sharpe_ratio || 0;
    const sharpeCls = sharpe >= 1 ? "fwui-up" : (sharpe < 0 ? "fwui-down" : "");
    const dd = (a.max_drawdown || 0) * 100;
    const plRatio = a.profit_loss_ratio || 0;
    return `
      <div class="fwui-card account-card">
        <div class="account-card__head">
          <div class="account-card__id">${escapeHtml(a.uid)}</div>
          <div class="account-card__name">${escapeHtml(a.name)}</div>
          <div class="account-card__tags">
            <span class="fwui-tag fwui-tag--${platColor}">${escapeHtml(a.platform)}</span>
            ${frozenTag}
            <span class="fwui-tag">${a.account_type === 0 ? "模拟盘" : "实盘"}</span>
            ${publicTag}
          </div>
        </div>
        <div class="account-card__stats">
          <div class="account-card__stat">
            <div class="account-card__stat-label">余额</div>
            <div class="account-card__stat-value">${FWUI.utils.fmtUsd(a.current_balance, 2)}</div>
          </div>
          <div class="account-card__stat">
            <div class="account-card__stat-label">日盈亏</div>
            <div class="account-card__stat-value ${pnlCls}">${(a.daily_pnl >= 0 ? "+" : "")}${FWUI.utils.fmtUsd(a.daily_pnl, 2)}</div>
          </div>
          <div class="account-card__stat">
            <div class="account-card__stat-label">总盈亏</div>
            <div class="account-card__stat-value ${totalPnlCls}">${(totalPnl >= 0 ? "+" : "")}${FWUI.utils.fmtUsd(totalPnl, 2)}</div>
          </div>
          <div class="account-card__stat">
            <div class="account-card__stat-label">交易笔数</div>
            <div class="account-card__stat-value">${tradeCount}</div>
          </div>
        </div>
        <div class="account-card__stats">
          <div class="account-card__stat">
            <div class="account-card__stat-label">胜率</div>
            <div class="account-card__stat-value ${winRateCls}">${winRate.toFixed(1)}%</div>
          </div>
          <div class="account-card__stat">
            <div class="account-card__stat-label">盈亏比</div>
            <div class="account-card__stat-value">${plRatio.toFixed(2)}</div>
          </div>
          <div class="account-card__stat">
            <div class="account-card__stat-label">夏普率</div>
            <div class="account-card__stat-value ${sharpeCls}">${sharpe.toFixed(2)}</div>
          </div>
          <div class="account-card__stat">
            <div class="account-card__stat-label">综合分</div>
            <div class="account-card__stat-value ${scoreCls}">${score.toFixed(1)}</div>
          </div>
        </div>
        <div class="account-card__sparkline" id="spark-${a.id}" data-account-id="${a.id}">
          <svg width="100%" height="40" viewBox="0 0 200 40" preserveAspectRatio="none">
            <polyline class="spark-line" fill="none" stroke="var(--fwui-text-muted)" stroke-width="1.5" points="0,20 200,20"/>
            <text x="100" y="24" text-anchor="middle" fill="var(--fwui-text-muted)" font-size="10">资金曲线加载中...</text>
          </svg>
        </div>
        <div class="account-card__extra" style="font-size:12px;color:var(--fwui-text-muted);padding:0 16px 8px;">
          标的：<strong>${escapeHtml(a.target_symbol || a.target_url || "未设置")}</strong>
          · 最近下单：<strong>${a.last_order_at ? escapeHtml(a.last_order_at.replace("T", " ").slice(0, 16)) : "—"}</strong>
          · 最大回撤：<strong class="${dd > 20 ? 'fwui-down' : ''}">${dd.toFixed(1)}%</strong>
        </div>
        <div class="account-card__actions">
          <button class="fwui-btn fwui-btn--primary fwui-btn--sm" data-action="vote" data-id="${a.id}" ${a.risk_frozen || a.status !== 0 ? "disabled" : ""}>🧠 触发投票</button>
          <button class="fwui-btn fwui-btn--sm" data-action="signal" data-id="${a.id}" title="刷新信号">📡 信号</button>
          <button class="fwui-btn fwui-btn--sm" data-action="edit" data-id="${a.id}">✏️ 编辑</button>
          <button class="fwui-btn fwui-btn--sm" data-action="detail" data-uid="${escapeHtml(a.uid)}" data-id="${a.id}">📋 详情</button>
          <button class="fwui-btn fwui-btn--sm account-card__del" data-action="delete" data-id="${a.id}">🗑 删除</button>
        </div>
      </div>
    `;
  }

  function bindCardActions() {
    const view = document.getElementById("accounts-view");
    view.querySelectorAll("[data-action=vote]").forEach((b) => (b.onclick = triggerVote));
    view.querySelectorAll("[data-action=signal]").forEach((b) => (b.onclick = refreshSignal));
    view.querySelectorAll("[data-action=edit]").forEach((b) => (b.onclick = openEditModal));
    view.querySelectorAll("[data-action=detail]").forEach((b) => (b.onclick = goDetail));
    view.querySelectorAll("[data-action=delete]").forEach((b) => (b.onclick = delAccount));
  }

  // ========== 列表视图 ==========
  function renderList(data) {
    const rows = data.accounts.map((a) => listRow(a)).join("");
    return `
      <div class="fwui-table-wrap">
        <table class="fwui-table">
          <thead>
            <tr>
              <th data-sort-key="uid">UID</th>
              <th data-sort-key="name">账户名</th>
              <th data-sort-key="platform">平台</th>
              <th data-sort-key="account_type">类型</th>
              <th data-sort-key="signal">信号</th>
              <th data-sort-key="target_symbol">交易标</th>
              <th data-sort-key="order_amount_usd" style="text-align:right;">下单金额</th>
              <th data-sort-key="current_balance" style="text-align:right;">余额</th>
              <th data-sort-key="daily_pnl" style="text-align:right;">日盈亏</th>
              <th data-sort-key="public_enabled">公开</th>
              <th data-sort-key="status">状态</th>
              <th style="text-align:center;">操作</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
        </table>
      </div>
    `;
  }

  function listRow(a) {
    const platColor = a.platform === "polymarket" ? "primary" : "warning";
    const frozenTag = a.risk_frozen
      ? `<span class="fwui-tag fwui-tag--danger" style="font-size:11px;padding:2px 6px;">冻结</span>`
      : `<span class="fwui-tag fwui-tag--success" style="font-size:11px;padding:2px 6px;">正常</span>`;
    const pnlCls = a.daily_pnl >= 0 ? "fwui-up" : "fwui-down";
    const sigColor = a.signal === "UP" ? "success" : a.signal === "DOWN" ? "danger" : "";
    const sigTag = a.signal
      ? `<span class="fwui-tag fwui-tag--${sigColor}" style="font-size:11px;padding:2px 6px;">${escapeHtml(a.signal)}</span>`
      : `<span class="fwui-tag" style="font-size:11px;padding:2px 6px;">—</span>`;
    const publicToggle = `<label class="fwui-switch" title="${a.public_enabled ? "参与总榜单" : "私有，不参与总榜单"}">
        <input type="checkbox" data-action="toggle-public" data-id="${a.id}" ${a.public_enabled ? "checked" : ""}>
        <span class="fwui-switch__slider"></span>
      </label>`;
    const statusTag = a.status === 0
      ? `<span class="fwui-tag fwui-tag--success" style="font-size:11px;padding:2px 6px;">运行</span>`
      : a.status === 1
      ? `<span class="fwui-tag fwui-tag--warning" style="font-size:11px;padding:2px 6px;">暂停</span>`
      : `<span class="fwui-tag fwui-tag--danger" style="font-size:11px;padding:2px 6px;">停用</span>`;
    return `
      <tr>
        <td><code style="font-size:11px;color:var(--fwui-text-muted);background:var(--fwui-bg);padding:2px 6px;border-radius:4px;">${escapeHtml(a.uid)}</code></td>
        <td><strong>${escapeHtml(a.name)}</strong></td>
        <td><span class="fwui-tag fwui-tag--${platColor}" style="font-size:11px;padding:2px 6px;">${escapeHtml(a.platform)}</span></td>
        <td><span class="fwui-tag" style="font-size:11px;padding:2px 6px;">${a.account_type === 0 ? "模拟盘" : "实盘"}</span></td>
        <td>${sigTag}</td>
        <td><code style="font-size:11px;color:var(--fwui-text-secondary);">${escapeHtml(a.target_symbol || "—")}</code></td>
        <td style="text-align:right;font-weight:600;font-variant-numeric:tabular-nums;">$${FWUI.utils.fmtNumber(a.order_amount_usd || 0, 0)}</td>
        <td style="text-align:right;font-weight:600;font-variant-numeric:tabular-nums;">${FWUI.utils.fmtUsd(a.current_balance, 2)}</td>
        <td style="text-align:right;font-weight:600;font-variant-numeric:tabular-nums;" class="${pnlCls}">${(a.daily_pnl >= 0 ? "+" : "")}${FWUI.utils.fmtUsd(a.daily_pnl, 2)}</td>
        <td>${publicToggle}</td>
        <td>${statusTag}${a.risk_frozen ? " " + frozenTag : ""}</td>
        <td style="text-align:center;white-space:nowrap;">
          <button class="fwui-btn fwui-btn--primary fwui-btn--xs" data-action="vote" data-id="${a.id}" ${a.risk_frozen || a.status !== 0 ? "disabled" : ""} title="触发投票">🧠</button>
          <button class="fwui-btn fwui-btn--sm" data-action="signal" data-id="${a.id}" title="刷新信号" style="padding:4px 8px;font-size:12px;">📡</button>
          <button class="fwui-btn fwui-btn--sm" data-action="edit" data-id="${a.id}" title="编辑" style="padding:4px 8px;font-size:12px;">✏️</button>
          <button class="fwui-btn fwui-btn--sm" data-action="detail" data-uid="${escapeHtml(a.uid)}" data-id="${a.id}" title="详情/日志" style="padding:4px 8px;font-size:12px;">📋</button>
          <button class="fwui-btn fwui-btn--sm" data-action="delete" data-id="${a.id}" title="删除" style="padding:4px 8px;font-size:12px;color:var(--fwui-danger);">🗑</button>
        </td>
      </tr>
    `;
  }

  function bindListActions() {
    const view = document.getElementById("accounts-view");
    view.querySelectorAll("[data-action=vote]").forEach((b) => (b.onclick = triggerVote));
    view.querySelectorAll("[data-action=signal]").forEach((b) => (b.onclick = refreshSignal));
    view.querySelectorAll("[data-action=edit]").forEach((b) => (b.onclick = openEditModal));
    view.querySelectorAll("[data-action=detail]").forEach((b) => (b.onclick = goDetail));
    view.querySelectorAll("[data-action=delete]").forEach((b) => (b.onclick = delAccount));
    view.querySelectorAll("[data-action=toggle-public]").forEach((b) => (b.onchange = togglePublic));
  }

  // ========== 排序 ==========
  function bindSortHeaders() {
    const view = document.getElementById("accounts-view");
    view.querySelectorAll("th[data-sort-key]").forEach((th) => {
      const key = th.dataset.sortKey;
      // 恢复当前排序状态到表头
      if (_sortState.key === key) {
        th.dataset.sortDir = _sortState.dir;
      } else {
        delete th.dataset.sortDir;
      }
      th.onclick = () => {
        if (_sortState.key === key) {
          _sortState.dir = _sortState.dir === "asc" ? "desc" : "asc";
        } else {
          _sortState.key = key;
          _sortState.dir = "asc";
        }
        applyFiltersAndSort();
        render();
      };
    });
  }

  // ========== 创建账户弹框 ==========
  function openCreateModal() {
    const form = document.createElement("div");
    form.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:12px;">
        <label>账户名 <input class="fwui-input" name="name" placeholder="例如：我的BTC猎手" required></label>
        <label>交易平台
          <select class="fwui-select" name="platform">
            <option value="polymarket">Polymarket (预测市场)</option>
            <option value="okx">OKX (现货/合约)</option>
          </select>
        </label>
        <label>初始余额 (USDT) <input class="fwui-input" name="balance" type="number" value="1000" min="100"></label>
        <label>交易标 URL <input class="fwui-input" name="target_url" placeholder="https://www.okx.com/trade-spot/btc-usdt 或 https://polymarket.com/event/xxx"></label>
        <label>每笔下单金额 (USDT) <input class="fwui-input" name="order_amount_usd" type="number" value="50" min="1" max="10000"></label>
        <label class="fwui-toggle-row">
          <span>🌍 参与总榜单</span>
          <label class="fwui-switch"><input type="checkbox" name="public_enabled" checked><span class="fwui-switch__slider"></span></label>
        </label>
        <div style="font-size:12px;color:var(--fwui-text-muted);">⚠️ 本轮仅模拟盘，不会动用真实资金</div>
      </div>
    `;
    FWUI.modal.confirm({
      title: "创建执行账户",
      content: form,
      okText: "创建",
      onOk: async () => {
        const name = form.querySelector("[name=name]").value.trim();
        const platform = form.querySelector("[name=platform]").value;
        const balance = parseFloat(form.querySelector("[name=balance]").value);
        const target_url = form.querySelector("[name=target_url]").value.trim();
        const order_amount_usd = parseFloat(form.querySelector("[name=order_amount_usd]").value);
        const public_enabled = form.querySelector("[name=public_enabled]").checked;
        if (!name) { FWUI.toast.error("请输入账户名"); throw new Error("validation"); }
        if (!balance || balance <= 0) { FWUI.toast.error("请输入有效余额"); throw new Error("validation"); }
        if (target_url && !target_url.startsWith("https://")) { FWUI.toast.error("URL 必须以 https:// 开头"); throw new Error("validation"); }
        try {
          const qs = new URLSearchParams({
            name, platform, initial_balance: String(balance),
            order_amount_usd: String(order_amount_usd),
            public_enabled: public_enabled ? "true" : "false",
          });
          if (target_url) qs.set("target_url", target_url);
          const resp = await fetch("/api/agent/accounts?" + qs.toString(), {
            method: "POST",
            headers: { "Authorization": "Bearer " + FWUI.api.getToken(), "Content-Type": "application/json" },
          });
          const json = await resp.json();
          if (!json.success) throw new Error(json.message);
          FWUI.toast.success("账户已创建");
          // 重置过滤
          _searchQuery = "";
          _typeFilter = -1;
          document.getElementById("account-search").value = "";
          document.getElementById("account-type-filter").value = "-1";
          await load();
        } catch (e) { FWUI.toast.error(e.message); throw e; }
      },
    });
  }

  // ========== 编辑账户弹框 ==========
  function openEditModal(e) {
    const id = parseInt(e.currentTarget.dataset.id);
    const acc = _accountsCache.accounts.find((a) => a.id === id);
    if (!acc) return;
    const form = document.createElement("div");
    form.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:12px;">
        <label>账户名 <input class="fwui-input" name="name" value="${escapeHtml(acc.name)}"></label>
        <label>交易标 URL <input class="fwui-input" name="target_url" value="${escapeHtml(acc.target_url || "")}" placeholder="https://..."></label>
        <label>每笔下单金额 (USDT) <input class="fwui-input" name="order_amount_usd" type="number" value="${acc.order_amount_usd || 50}" min="1" max="10000"></label>
        <label>账户状态
          <select class="fwui-select" name="status">
            <option value="0" ${acc.status === 0 ? "selected" : ""}>运行</option>
            <option value="1" ${acc.status === 1 ? "selected" : ""}>暂停</option>
            <option value="2" ${acc.status === 2 ? "selected" : ""}>停用</option>
          </select>
        </label>
        <label class="fwui-toggle-row">
          <span>🌍 参与总榜单</span>
          <label class="fwui-switch"><input type="checkbox" name="public_enabled" ${acc.public_enabled ? "checked" : ""}><span class="fwui-switch__slider"></span></label>
        </label>
        <div style="font-size:12px;color:var(--fwui-text-muted);">
          当前解析：<code>${escapeHtml(acc.target_symbol || "—")}</code>
        </div>
      </div>
    `;
    FWUI.modal.confirm({
      title: "编辑账户",
      content: form,
      okText: "保存",
      onOk: async () => {
        const name = form.querySelector("[name=name]").value.trim();
        const target_url = form.querySelector("[name=target_url]").value.trim();
        const order_amount_usd = parseFloat(form.querySelector("[name=order_amount_usd]").value);
        const status = parseInt(form.querySelector("[name=status]").value);
        const public_enabled = form.querySelector("[name=public_enabled]").checked;
        if (!name) { FWUI.toast.error("账户名不能为空"); throw new Error("validation"); }
        if (target_url && !target_url.startsWith("https://")) { FWUI.toast.error("URL 必须以 https://"); throw new Error("validation"); }
        try {
          const resp = await fetch("/api/agent/accounts/" + id, {
            method: "PUT",
            headers: { "Authorization": "Bearer " + FWUI.api.getToken(), "Content-Type": "application/json" },
            body: JSON.stringify({ name, target_url: target_url || null, order_amount_usd, status, public_enabled }),
          });
          const json = await resp.json();
          if (!json.success) throw new Error(json.message);
          FWUI.toast.success("已保存");
          await load();
        } catch (e) { FWUI.toast.error(e.message); throw e; }
      },
    });
  }

  // ========== 刷新信号 ==========
  async function refreshSignal(e) {
    const id = parseInt(e.currentTarget.dataset.id);
    const btn = e.currentTarget;
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = "⏳";
    try {
      const resp = await fetch("/api/agent/accounts/" + id + "/signal/refresh?source=random", {
        method: "POST",
        headers: { "Authorization": "Bearer " + FWUI.api.getToken() },
      });
      const json = await resp.json();
      if (!json.success) throw new Error(json.message);
      FWUI.toast.success("新信号: " + json.data.signal);
      await load();
    } catch (e) {
      FWUI.toast.error(e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = orig;
    }
  }

  // ========== 行内公开 toggle ==========
  async function togglePublic(e) {
    const id = parseInt(e.currentTarget.dataset.id);
    const v = e.currentTarget.checked;
    try {
      const resp = await fetch("/api/agent/accounts/" + id, {
        method: "PUT",
        headers: { "Authorization": "Bearer " + FWUI.api.getToken(), "Content-Type": "application/json" },
        body: JSON.stringify({ public_enabled: v }),
      });
      const json = await resp.json();
      if (!json.success) throw new Error(json.message);
      FWUI.toast.success(v ? "已加入总榜单" : "已退出总榜单");
    } catch (e) {
      FWUI.toast.error(e.message);
      e.currentTarget.checked = !v;
    }
  }

  // 触发投票
  async function triggerVote(e) {
    const id = parseInt(e.currentTarget.dataset.id);
    const input = await FWUI.modal.prompt({
      title: "触发 3 智能体预测",
      content: "请设置交易参数，系统将启动 3 个 AI 智能体进行预测与投票。",
      inputFields: [
        { name: "symbol", label: "交易对", value: "BTCUSDT", placeholder: "BTCUSDT" },
        { name: "timeframe", label: "时窗 (15m 或 1h)", value: "15m", placeholder: "15m" },
      ],
    });
    if (!input) return;
    const sym = (input.symbol || "BTCUSDT").toUpperCase();
    const tf = input.timeframe || "15m";

    const confirmed = await FWUI.modal.confirm({
      title: "确认触发投票",
      content: `即将触发 3 智能体预测 + 投票 (${sym} ${tf})，是否继续？`,
      type: "warning",
      okText: "开始投票",
    });
    if (!confirmed) return;

    const btn = e.currentTarget;
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "投票中...";
    try {
      const data = await FWUI.api.predictAndVote(id, { symbol: sym, timeframe: tf });
      const msg = `方向:${data.final_direction === 1 ? "看涨" : data.final_direction === 2 ? "看跌" : "震荡"} 金额:$${data.order_amount_usd} 原因:${data.reason}`;
      FWUI.toast.success(msg);
      await load();
    } catch (e) {
      FWUI.toast.error("失败: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }

  // 去详情
  function goDetail(e) {
    const uid = e.currentTarget.dataset.uid;
    const id = e.currentTarget.dataset.id;
    const prefix = window.__FW_DEMO_MODE__ ? "/demo" : "";
    location.href = `${prefix}/detail?uid=${encodeURIComponent(uid)}&account_id=${id}`;
  }

  // 删除账户（软删除：保留历史数据，仅从列表隐藏）
  async function delAccount(e) {
    const id = parseInt(e.currentTarget.dataset.id);
    const confirmed = await FWUI.modal.danger({
      title: "停用账户",
      content: "该账户将被停用并从列表移除，历史订单/投票/绩效数据会保留用于审计。确定要停用此账户吗？",
      okText: "确认停用",
    });
    if (!confirmed) return;
    try {
      const resp = await fetch("/api/agent/accounts/" + id, { method: "DELETE", headers: { "Authorization": "Bearer " + FWUI.api.getToken() } });
      const json = await resp.json();
      if (!json.success) throw new Error(json.message);
      FWUI.toast.success("已停用（历史数据保留）");
      await load();
    } catch (e) { FWUI.toast.error(e.message); }
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();