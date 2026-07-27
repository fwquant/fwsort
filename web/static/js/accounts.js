// 我的账户页：列表 + 创建 + 触发投票
(function () {
  "use strict";

  async function init() {
    if (!FWUI.api.getToken()) {
      document.getElementById("accounts-grid").innerHTML = `
        <div class="fwui-empty-card">
          <div style="font-size:32px;margin-bottom:12px;">🔒</div>
          <div style="margin-bottom:16px;">登录后查看您的执行账户</div>
          <button class="fwui-btn fwui-btn--primary" onclick="FWUI.toast.info('请点击右上角登录')">去登录</button>
        </div>
      `;
      return;
    }
    bindUI();
    await load();
  }

  function bindUI() {
    document.getElementById("btn-create-account").onclick = openCreateModal;
  }

  async function load() {
    const grid = document.getElementById("accounts-grid");
    grid.innerHTML = `<div class="fwui-empty-card">⏳ 加载中...</div>`;
    try {
      const data = await FWUI.api.myAccounts();
      document.getElementById("account-count").textContent = `共 ${data.count} 个账户`;
      if (data.count === 0) {
        grid.innerHTML = `
          <div class="fwui-empty-card">
            <div style="font-size:32px;margin-bottom:12px;">💼</div>
            <div style="margin-bottom:16px;">还没有执行账户</div>
            <button class="fwui-btn fwui-btn--primary" onclick="document.getElementById('btn-create-account').click()">立即创建</button>
          </div>
        `;
        return;
      }
      grid.innerHTML = data.accounts.map(card).join("");
      // 绑定按钮
      grid.querySelectorAll("[data-action=vote]").forEach((b) => b.onclick = triggerVote);
      grid.querySelectorAll("[data-action=detail]").forEach((b) => b.onclick = goDetail);
      grid.querySelectorAll("[data-action=delete]").forEach((b) => b.onclick = delAccount);
    } catch (e) {
      grid.innerHTML = `<div class="fwui-empty-card fwui-empty-card--error">${escapeHtml(e.message)}</div>`;
    }
  }

  function card(a) {
    const platColor = a.platform === "polymarket" ? "primary" : "warning";
    const frozenTag = a.risk_frozen ? `<span class="fwui-tag fwui-tag--danger">风控冻结</span>` : `<span class="fwui-tag fwui-tag--success">正常</span>`;
    const pnlCls = a.daily_pnl >= 0 ? "fwui-up" : "fwui-down";
    return `
      <div class="fwui-card account-card">
        <div class="account-card__head">
          <div class="account-card__id">${escapeHtml(a.uid)}</div>
          <div class="account-card__name">${escapeHtml(a.name)}</div>
          <div class="account-card__tags">
            <span class="fwui-tag fwui-tag--${platColor}">${escapeHtml(a.platform)}</span>
            ${frozenTag}
            <span class="fwui-tag">${a.account_type === 0 ? "模拟盘" : "实盘"}</span>
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
        </div>
        <div class="account-card__actions">
          <button class="fwui-btn fwui-btn--primary fwui-btn--sm" data-action="vote" data-id="${a.id}" ${a.risk_frozen || a.status !== 0 ? 'disabled' : ''}>🧠 触发投票</button>
          <button class="fwui-btn fwui-btn--sm" data-action="detail" data-uid="${escapeHtml(a.uid)}" data-id="${a.id}">📋 详情/日志</button>
          <button class="fwui-btn fwui-btn--sm account-card__del" data-action="delete" data-id="${a.id}">🗑 删除</button>
        </div>
      </div>
    `;
  }

  // 创建账户弹框
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
        if (!name) { FWUI.toast.error("请输入账户名"); throw new Error("validation"); }
        if (!balance || balance <= 0) { FWUI.toast.error("请输入有效余额"); throw new Error("validation"); }
        try {
          // 通过专用 API 方法创建账户
          const resp = await fetch(
            "/api/agent/accounts?name=" + encodeURIComponent(name)
            + "&platform=" + platform
            + "&initial_balance=" + balance,
            {
              method: "POST",
              headers: { "Authorization": "Bearer " + FWUI.api.getToken(), "Content-Type": "application/json" },
            }
          );
          const json = await resp.json();
          if (!json.success) throw new Error(json.message);
          FWUI.toast.success("账户已创建");
          await load();
        } catch (e) { FWUI.toast.error(e.message); throw e; }
      },
    });
  }

  // 触发投票
  async function triggerVote(e) {
    const id = parseInt(e.currentTarget.dataset.id);
    const sym = prompt("交易对 (默认 BTCUSDT):", "BTCUSDT") || "BTCUSDT";
    const tf = prompt("时窗 (15m 或 1h):", "15m") || "15m";
    if (!confirm(`确认触发 3 智能体预测 + 投票 (${sym} ${tf})？`)) return;
    e.currentTarget.disabled = true;
    e.currentTarget.textContent = "投票中...";
    try {
      const data = await FWUI.api.predictAndVote(id, { symbol: sym, timeframe: tf });
      const msg = `方向:${data.final_direction === 1 ? "看涨" : data.final_direction === 2 ? "看跌" : "震荡"} 金额:$${data.order_amount_usd} 原因:${data.reason}`;
      FWUI.toast.success(msg);
      await load();
    } catch (e) {
      FWUI.toast.error("失败: " + e.message);
    } finally {
      e.currentTarget.disabled = false;
      e.currentTarget.textContent = "🧠 触发投票";
    }
  }

  // 去详情
  function goDetail(e) {
    const uid = e.currentTarget.dataset.uid;
    const id = e.currentTarget.dataset.id;
    location.href = `/detail?uid=${encodeURIComponent(uid)}&account_id=${id}`;
  }

  // 删除账户
  async function delAccount(e) {
    const id = parseInt(e.currentTarget.dataset.id);
    if (!confirm("确认删除该账户？（不会删除历史订单记录）")) return;
    try {
      const resp = await fetch("/api/agent/accounts/" + id, { method: "DELETE", headers: { "Authorization": "Bearer " + FWUI.api.getToken() } });
      const json = await resp.json();
      if (!json.success) throw new Error(json.message);
      FWUI.toast.success("已删除");
      await load();
    } catch (e) { FWUI.toast.error(e.message); }
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
