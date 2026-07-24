// 跟单管理页
(function () {
  "use strict";

  async function init() {
    bindTabs();
    await loadMarket();
  }

  function bindTabs() {
    document.querySelectorAll("[data-tab]").forEach((el) => {
      el.onclick = async () => {
        document.querySelectorAll("[data-tab]").forEach((x) => x.classList.remove("fwui-tab--active"));
        el.classList.add("fwui-tab--active");
        const tab = el.dataset.tab;
        document.getElementById("tab-market").style.display = tab === "market" ? "" : "none";
        document.getElementById("tab-my").style.display = tab === "my" ? "" : "none";
        if (tab === "my") await loadMy();
      };
    });
  }

  // 跟单市场（从榜单取 top 10 高分交易员）
  async function loadMarket() {
    const grid = document.getElementById("market-grid");
    grid.innerHTML = `<div class="fwui-card" style="grid-column:1/-1;text-align:center;">加载中...</div>`;
    try {
      const data = await FWUI.api.followMarket({ rank_type: "all_time", limit: 20 });
      grid.innerHTML = data.items.map(marketCard).join("");
      grid.querySelectorAll("[data-action=subscribe]").forEach((b) => b.onclick = openSubscribeModal);
    } catch (e) {
      grid.innerHTML = `<div class="fwui-card" style="grid-column:1/-1;color:var(--fwui-danger);">${escapeHtml(e.message)}</div>`;
    }
  }

  function marketCard(it) {
    const t = FWUI.utils.tier(it.composite_score || 0);
    return `
      <div class="fwui-card">
        <div style="display:flex;justify-content:space-between;align-items:start;gap:8px;">
          <div>
            <div style="font-size:11px;color:var(--fwui-text-muted);">#${it.rank} · ${escapeHtml(it.platform || "-")}</div>
            <div style="font-size:16px;font-weight:600;margin-top:4px;">${escapeHtml(it.name || it.uid)}</div>
          </div>
          <span class="fwui-tier fwui-tier--${t.cls}">${t.name}</span>
        </div>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0;font-size:12px;">
          <div><div style="color:var(--fwui-text-muted);font-size:11px;">胜率</div><div>${FWUI.utils.fmtPercent(it.win_rate, 1)}</div></div>
          <div><div style="color:var(--fwui-text-muted);font-size:11px;">年化</div><div class="${(it.annualized_return||0)>=0?'fwui-up':'fwui-down'}">${FWUI.utils.fmtPercent(it.annualized_return, 1)}</div></div>
          <div><div style="color:var(--fwui-text-muted);font-size:11px;">回撤</div><div class="fwui-down">${FWUI.utils.fmtPercent(it.max_drawdown, 1)}</div></div>
        </div>
        <div style="font-size:11px;color:var(--fwui-text-muted);margin-bottom:8px;">订阅：$9.9/月 + 盈利20%分成</div>
        <button class="fwui-btn fwui-btn--primary" data-action="subscribe" data-uid="${escapeHtml(it.uid)}">订阅跟单</button>
      </div>
    `;
  }

  // 我的订阅
  async function loadMy() {
    const grid = document.getElementById("my-grid");
    if (!FWUI.api.getToken()) {
      grid.innerHTML = `<div class="fwui-card" style="grid-column:1/-1;text-align:center;padding:48px;color:var(--fwui-text-muted);">请先登录</div>`;
      return;
    }
    grid.innerHTML = `<div class="fwui-card" style="grid-column:1/-1;text-align:center;">加载中...</div>`;
    try {
      const resp = await fetch("/api/follow/my", { headers: { "Authorization": "Bearer " + FWUI.api.getToken() } });
      const data = await resp.json();
      if (!data.success) throw new Error(data.message);
      if (data.data.subscriptions.length === 0) {
        grid.innerHTML = `<div class="fwui-card" style="grid-column:1/-1;text-align:center;padding:48px;color:var(--fwui-text-muted);">还没有订阅</div>`;
        return;
      }
      grid.innerHTML = data.data.subscriptions.map(myCard).join("");
      grid.querySelectorAll("[data-action=cancel]").forEach((b) => b.onclick = cancelSub);
    } catch (e) {
      grid.innerHTML = `<div class="fwui-card" style="grid-column:1/-1;color:var(--fwui-danger);">${escapeHtml(e.message)}</div>`;
    }
  }

  function myCard(s) {
    return `
      <div class="fwui-card">
        <div style="font-size:16px;font-weight:600;">${escapeHtml(s.leader_name || s.leader_uid)}</div>
        <div style="margin-top:8px;font-size:12px;color:var(--fwui-text-muted);">订阅 ${s.subscription_fee_usd} USD/月 · 分成 ${s.profit_share_ratio * 100}%</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0;font-size:12px;">
          <div><div style="color:var(--fwui-text-muted);">已跟单</div><div>${s.total_followed} 笔</div></div>
          <div><div style="color:var(--fwui-text-muted);">累计盈亏</div><div class="${s.total_pnl >= 0 ? 'fwui-up' : 'fwui-down'}">${FWUI.utils.fmtUsd(s.total_pnl, 2)}</div></div>
        </div>
        <div style="font-size:11px;color:var(--fwui-text-muted);">状态：${s.status === 1 ? "✅ 订阅中" : "已取消"} · 到期 ${s.expires_at || "永久"}</div>
        ${s.status === 1 ? `<button class="fwui-btn fwui-btn--sm" data-action="cancel" data-id="${s.id}" style="margin-top:8px;color:var(--fwui-danger);">取消订阅</button>` : ""}
      </div>
    `;
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
    if (!confirm("确认取消订阅？")) return;
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
