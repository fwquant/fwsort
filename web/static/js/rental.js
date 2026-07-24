// 智能体租用页
(function () {
  "use strict";

  async function init() {
    bindTabs();
    await loadAgents();
  }

  function bindTabs() {
    document.querySelectorAll("[data-tab]").forEach((el) => {
      el.onclick = async () => {
        document.querySelectorAll("[data-tab]").forEach((x) => x.classList.remove("fwui-tab--active"));
        el.classList.add("fwui-tab--active");
        const tab = el.dataset.tab;
        document.getElementById("tab-per-use").style.display = tab === "per_use" ? "" : "none";
        document.getElementById("tab-package").style.display = tab === "package" ? "" : "none";
        document.getElementById("tab-my").style.display = tab === "my" ? "" : "none";
        if (tab === "my") await loadMy();
      };
    });
  }

  // 加载智能体列表
  async function loadAgents() {
    try {
      const data = await FWUI.api.rentalAgents();
      const agents = data.agents;

      // 按次试算
      const perUse = document.getElementById("per-use-grid");
      perUse.innerHTML = agents.map((a) => `
        <div class="fwui-card">
          <div style="display:flex;align-items:center;gap:12px;">
            <div style="width:48px;height:48px;border-radius:50%;background:linear-gradient(135deg,#9b6dff,#6d3aff);display:flex;align-items:center;justify-content:center;font-size:24px;">🤖</div>
            <div>
              <div style="font-size:16px;font-weight:600;">${escapeHtml(a.name)}</div>
              <div style="font-size:11px;color:var(--fwui-text-muted);">${escapeHtml(a.model)}</div>
            </div>
          </div>
          <p style="font-size:13px;color:var(--fwui-text-secondary);margin:12px 0;">${escapeHtml(a.description || "")}</p>
          <div style="display:flex;justify-content:space-between;align-items:center;padding-top:12px;border-top:1px solid var(--fwui-border);">
            <div>
              <div style="font-size:11px;color:var(--fwui-text-muted);">按次试算</div>
              <div style="font-size:20px;font-weight:600;">$${a.price_per_call.toFixed(2)}<span style="font-size:12px;color:var(--fwui-text-muted);">/次</span></div>
            </div>
            <button class="fwui-btn fwui-btn--primary" data-action="call" data-id="${a.id}">立即调用</button>
          </div>
        </div>
      `).join("");
      perUse.querySelectorAll("[data-action=call]").forEach((b) => b.onclick = callAgent);

      // 包时段
      const pkg = document.getElementById("package-grid");
      pkg.innerHTML = agents.map((a) => `
        <div class="fwui-card">
          <div style="display:flex;align-items:center;gap:12px;">
            <div style="width:48px;height:48px;border-radius:50%;background:linear-gradient(135deg,#fbbf24,#d97706);display:flex;align-items:center;justify-content:center;font-size:24px;">📦</div>
            <div>
              <div style="font-size:16px;font-weight:600;">${escapeHtml(a.name)}</div>
              <div style="font-size:11px;color:var(--fwui-text-muted);">${escapeHtml(a.model)}</div>
            </div>
          </div>
          <div style="margin:12px 0;font-size:13px;">
            <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px dashed var(--fwui-border);">
              <span>1 小时独占</span><strong>$${a.price_per_hour.toFixed(2)}</strong>
            </div>
            <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px dashed var(--fwui-border);">
              <span>1 天独占</span><strong>$${(a.price_per_hour * 20).toFixed(2)}</strong>
            </div>
            <div style="display:flex;justify-content:space-between;padding:6px 0;">
              <span>7 天包</span><strong style="color:var(--fwui-success);">$${(a.price_per_hour * 20 * 6).toFixed(2)} <span style="font-size:11px;">省14%</span></strong>
            </div>
          </div>
          <button class="fwui-btn fwui-btn--warning" data-action="rent" data-id="${a.id}">租用一个时段</button>
        </div>
      `).join("");
      pkg.querySelectorAll("[data-action=rent]").forEach((b) => b.onclick = rentAgent);
    } catch (e) {
      document.getElementById("per-use-grid").innerHTML = `<div class="fwui-card" style="grid-column:1/-1;color:var(--fwui-danger);">${escapeHtml(e.message)}</div>`;
    }
  }

  // 按次调用
  async function callAgent(e) {
    if (!FWUI.api.getToken()) { FWUI.toast.error("请先登录"); return; }
    const id = parseInt(e.currentTarget.dataset.id);
    const form = document.createElement("div");
    form.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:12px;">
        <label>交易对 <input class="fwui-input" name="symbol" value="BTCUSDT"></label>
        <label>时窗
          <select class="fwui-select" name="timeframe">
            <option value="15m">15 分钟</option>
            <option value="1h">1 小时</option>
          </select>
        </label>
      </div>
    `;
    FWUI.modal.confirm({
      title: "按次调用（试算）",
      content: form,
      onOk: async () => {
        try {
          const d = await FWUI.api.rentalCall({
            agent_id: id,
            symbol: form.querySelector("[name=symbol]").value,
            timeframe: form.querySelector("[name=timeframe]").value,
          });
          FWUI.toast.success(`结果:${FWUI.utils.direction(d.direction)} 置信度 ${(d.confidence * 100).toFixed(1)}%`);
          FWUI.modal.info({
            title: "智能体返回",
            content: `<div style="padding:8px 0;"><p><strong>方向：</strong>${FWUI.utils.direction(d.direction)}</p><p><strong>置信度：</strong>${(d.confidence * 100).toFixed(1)}%</p><p style="color:var(--fwui-text-muted);font-size:13px;">${escapeHtml(d.reasoning || "")}</p></div>`,
          });
        } catch (e) { FWUI.toast.error(e.message); throw e; }
      },
    });
  }

  // 包时段
  async function rentAgent(e) {
    if (!FWUI.api.getToken()) { FWUI.toast.error("请先登录"); return; }
    const id = parseInt(e.currentTarget.dataset.id);
    const form = document.createElement("div");
    form.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:12px;">
        <label>时长
          <select class="fwui-select" name="hours">
            <option value="1">1 小时 ($0.5/h)</option>
            <option value="24" selected>1 天 ($10)</option>
            <option value="168">7 天 ($60, 省14%)</option>
          </select>
        </label>
        <div style="font-size:12px;color:var(--fwui-text-muted);">⚠️ 模拟盘：不真实扣费</div>
      </div>
    `;
    FWUI.modal.confirm({
      title: "租用智能体",
      content: form,
      onOk: async () => {
        const hours = parseInt(form.querySelector("[name=hours]").value);
        try {
          const d = await FWUI.api.rentPackage({ agent_id: id, hours });
          FWUI.toast.success(`租用成功,到 ${d.expires_at}`);
        } catch (e) { FWUI.toast.error(e.message); throw e; }
      },
    });
  }

  // 我的租用
  async function loadMy() {
    const grid = document.getElementById("my-rentals");
    if (!FWUI.api.getToken()) {
      grid.innerHTML = `<div class="fwui-card" style="grid-column:1/-1;text-align:center;color:var(--fwui-text-muted);padding:48px;">请先登录</div>`;
      return;
    }
    try {
      const data = await FWUI.api.rentalMy();
      if (data.rentals.length === 0) {
        grid.innerHTML = `<div class="fwui-card" style="grid-column:1/-1;text-align:center;color:var(--fwui-text-muted);padding:48px;">还没有租用</div>`;
        return;
      }
      grid.innerHTML = data.rentals.map((r) => `
        <div class="fwui-card">
          <div style="font-size:16px;font-weight:600;">${escapeHtml(r.agent_name)}</div>
          <div style="margin-top:8px;font-size:12px;color:var(--fwui-text-muted);">类型：${r.rental_type === "per_call" ? "按次" : "包时段"}</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0;font-size:12px;">
            <div><div style="color:var(--fwui-text-muted);">已用</div><div>${r.used_calls || 0} 次 / ${r.hours || 0} 小时</div></div>
            <div><div style="color:var(--fwui-text-muted);">已付</div><div>$${r.total_paid_usd.toFixed(2)}</div></div>
          </div>
          <div style="font-size:11px;color:var(--fwui-text-muted);">状态：${r.status === 1 ? "✅ 有效" : "已结束"} · 到期 ${r.expires_at || "-"}</div>
        </div>
      `).join("");
    } catch (e) {
      grid.innerHTML = `<div class="fwui-card" style="grid-column:1/-1;color:var(--fwui-danger);">${escapeHtml(e.message)}</div>`;
    }
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
