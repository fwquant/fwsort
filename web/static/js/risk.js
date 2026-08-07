/* global FWUI */
// ========== 风控管理页面交互（阶段二 1）==========
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  // 缓存
  const state = {
    role: 0,
    userId: null,
    accounts: [],
    tasks: [],
    profiles: [],
    currentAccountId: null,
    currentTaskId: null,
    currentTab: "accounts",
    eventLimit: 50,
    eventTotal: 0,
  };

  // ========= 注入风控页面专属样式（避免改全局 CSS 文件）=========
  function injectStyles() {
    if (document.getElementById("risk-inline-style")) return;
    const style = document.createElement("style");
    style.id = "risk-inline-style";
    style.textContent = `
      .risk-summary{margin-bottom:20px;}
      .risk-summary-card{background:linear-gradient(135deg,var(--fwui-card-bg),var(--fwui-bg-subtle));border:1px solid var(--fwui-border);padding:18px;border-radius:14px;}
      .risk-summary-card__label{font-size:12px;color:var(--fwui-text-muted);letter-spacing:0.05em;text-transform:uppercase;}
      .risk-summary-card__value{font-size:26px;font-weight:800;margin-top:6px;color:var(--fwui-text-primary);}
      .risk-section-header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:18px;padding-bottom:12px;border-bottom:1px solid var(--fwui-border);}
      .risk-section-header h2{font-size:17px;margin:0;}
      .risk-form{margin-top:6px;}
      .risk-form__grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;}
      .risk-form__grid label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--fwui-text-secondary);}
      .risk-form__grid input,.risk-form__grid select{margin-top:2px;}
      .risk-form__row{margin-top:18px;}
      .risk-form__row--actions{display:flex;gap:10px;align-items:center;}
      .risk-form__row--status{margin-bottom:18px;padding:14px 16px;background:var(--fwui-bg-subtle);border-radius:12px;border:1px solid var(--fwui-border);}
      .risk-status-chips{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;}
      .risk-chip{padding:6px 12px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid var(--fwui-border);background:var(--fwui-card-bg);}
      .risk-chip--ok{color:var(--fwui-success);border-color:color-mix(in srgb,var(--fwui-success) 40%,transparent);background:color-mix(in srgb,var(--fwui-success) 10%,transparent);}
      .risk-chip--danger{color:var(--fwui-danger);border-color:color-mix(in srgb,var(--fwui-danger) 40%,transparent);background:color-mix(in srgb,var(--fwui-danger) 10%,transparent);}
      .risk-chip--neutral{color:var(--fwui-text-muted);}
      .risk-frozen-actions{display:flex;gap:8px;}
      .risk-item-badge{margin-left:auto;background:var(--fwui-danger);color:#fff;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;}
      .risk-effective-params{background:var(--fwui-bg-subtle);border:1px dashed var(--fwui-border);border-radius:12px;padding:14px;}
      .risk-effective-params__grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px 18px;}
      .risk-effective-params__item{display:flex;justify-content:space-between;gap:10px;font-size:13px;border-bottom:1px dashed var(--fwui-border);padding:6px 0;}
      .risk-effective-params__k{color:var(--fwui-text-muted);}
      .risk-effective-params__v{font-weight:700;color:var(--fwui-text-primary);}
      .risk-effective__skeleton{color:var(--fwui-text-muted);text-align:center;padding:12px;font-size:13px;}
      .risk-profiles-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;}
      .risk-profile-card{border:1px solid var(--fwui-border);border-radius:14px;padding:16px;background:var(--fwui-card-bg);display:flex;flex-direction:column;gap:10px;}
      .risk-profile-card.is-default{border-color:var(--fwui-primary);box-shadow:0 0 0 3px color-mix(in srgb,var(--fwui-primary) 20%,transparent);}
      .risk-profile-card.is-system{background:linear-gradient(135deg,var(--fwui-card-bg),color-mix(in srgb,var(--fwui-primary) 6%,transparent));}
      .risk-profile-card__header{display:flex;justify-content:space-between;align-items:center;}
      .risk-profile-card__name{font-weight:800;font-size:15px;}
      .risk-profile-card__badge{font-size:11px;padding:2px 8px;border-radius:999px;font-weight:700;}
      .risk-profile-card__desc{font-size:12px;color:var(--fwui-text-muted);line-height:1.5;min-height:36px;}
      .risk-profile-card__params{display:grid;grid-template-columns:repeat(2,1fr);gap:4px 10px;font-size:11px;color:var(--fwui-text-secondary);}
      .risk-profile-card__params span b{color:var(--fwui-text-primary);font-weight:700;}
      .risk-profile-card__actions{display:flex;gap:8px;margin-top:auto;padding-top:8px;border-top:1px solid var(--fwui-border);}
      .risk-profile-card.is-system .risk-profile-card__actions button[data-action=delete]{display:none;}
      .risk-events-list{display:flex;flex-direction:column;gap:10px;}
      .risk-event{border:1px solid var(--fwui-border);border-radius:12px;padding:12px 14px;background:var(--fwui-card-bg);}
      .risk-event__row1{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;}
      .risk-event__title{font-weight:700;font-size:14px;display:flex;align-items:center;gap:8px;}
      .risk-event__sev{font-size:11px;padding:2px 8px;border-radius:999px;font-weight:800;}
      .risk-event__sev--1{background:var(--fwui-bg-subtle);color:var(--fwui-text-muted);}
      .risk-event__sev--2{background:color-mix(in srgb,var(--fwui-warning) 15%,transparent);color:var(--fwui-warning, #d97706);}
      .risk-event__sev--3{background:color-mix(in srgb,var(--fwui-danger) 15%,transparent);color:var(--fwui-danger);}
      .risk-event__type{font-size:11px;padding:2px 8px;border-radius:6px;font-weight:700;margin-left:4px;}
      .risk-event__type--1{background:color-mix(in srgb,var(--fwui-success) 15%,transparent);color:var(--fwui-success);}
      .risk-event__type--2{background:color-mix(in srgb,#f59e0b 15%,transparent);color:#d97706;}
      .risk-event__type--3{background:color-mix(in srgb,var(--fwui-danger) 20%,transparent);color:var(--fwui-danger);}
      .risk-event__type--4{background:color-mix(in srgb,var(--fwui-primary) 20%,transparent);color:var(--fwui-primary);}
      .risk-event__type--5{background:var(--fwui-bg-subtle);color:var(--fwui-text-secondary);}
      .risk-event__msg{font-size:13px;color:var(--fwui-text-secondary);margin:6px 0;line-height:1.55;}
      .risk-event__meta{display:flex;flex-wrap:wrap;gap:10px 16px;font-size:11px;color:var(--fwui-text-muted);}
      .risk-event__detail{margin-top:8px;padding:8px 10px;background:var(--fwui-bg-subtle);border-radius:8px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;color:var(--fwui-text-secondary);white-space:pre-wrap;display:none;}
      .risk-event__detail.is-open{display:block;}
      .risk-event__snapshot{display:flex;gap:16px;margin-top:6px;font-size:12px;color:var(--fwui-text-muted);}
      .risk-event__toggle{background:none;border:none;color:var(--fwui-primary);cursor:pointer;font-size:12px;padding:0;margin-top:4px;}
      .risk-mobile-tabs .profile-mobile-tab{cursor:pointer;}
      @media (max-width: 640px) {
        .risk-form__row--actions{flex-direction:column;align-items:flex-start;}
        .risk-status-chips{flex-direction:column;align-items:flex-start;}
        .risk-frozen-actions{width:100%;}
        .risk-frozen-actions button{flex:1;}
      }
    `;
    document.head.appendChild(style);
  }

  // ========= 通用：读账户列表 + 任务列表 =========
  async function loadAccounts() {
    try {
      const resp = await FWUI.api.request("GET", "/api/accounts?mine=1");
      state.accounts = (resp.data && resp.data.items) || (resp.data && resp.data.list) || resp.data || [];
    } catch (e) {
      // fallback 兼容旧接口
      try {
        const r = await FWUI.api.request("GET", "/api/ranking?type=my");
        state.accounts = (r && r.data && r.data.items) || [];
      } catch (_) { state.accounts = []; }
    }
    const sel = $("#risk-account-select");
    if (!sel) return;
    sel.innerHTML =
      '<option value="">-- 请选择账户 --</option>' +
      state.accounts.map((a) => `<option value="${a.id}">${escapeHtml(a.uid)} · ${escapeHtml(a.name || "")}  ${"balance" in a ? "($" + fmtNum(a.current_balance || a.balance) + ")" : ""}</option>`).join("");
    if (state.currentAccountId) sel.value = state.currentAccountId;
  }

  async function loadTasks() {
    try {
      const r = await FWUI.api.request("GET", "/api/tasks?mine=1");
      state.tasks = (r && r.data && r.data.items) || (r && r.data && r.data.list) || (Array.isArray(r && r.data) ? r.data : []) || [];
    } catch (_) { state.tasks = []; }
    const sel = $("#risk-task-select");
    if (!sel) return;
    sel.innerHTML =
      '<option value="">-- 请选择自动任务 --</option>' +
      state.tasks.map((t) => `<option value="${t.id}">${escapeHtml(t.task_name || t.name || ("Task #" + t.id))}${t.account_id ? ` · acct ${t.account_id}` : ""}</option>`).join("");
    if (state.currentTaskId) sel.value = state.currentTaskId;
  }

  async function loadProfiles(targetSel /* accounts/tasks/profiles list */) {
    try {
      const r = await FWUI.api.request("GET", "/api/risk/profiles");
      state.profiles = (r && r.data && r.data.items) || (Array.isArray(r && r.data) ? r.data : []) || [];
    } catch (e) { state.profiles = []; }
    // 更新两个下拉框（加空白选项）
    const baseOpt = '<option value="">-- 系统默认 / 个性化配置 --</option>' +
      state.profiles.map((p) => {
        const tag = p.owner_id == null ? "📦 [系统]" : (p.is_default ? "⭐ " : "");
        return `<option value="${p.id}">${tag} ${escapeHtml(p.name)}</option>`;
      }).join("");
    $$("#risk-account-form select[name=risk_profile_id], #risk-task-form select[name=risk_profile_id]").forEach((s) => {
      if (!s) return;
      const cur = s.value;
      s.innerHTML = baseOpt;
      s.value = cur;
    });
    // 如果在 profiles tab，渲染卡片
    if (targetSel === "profiles") renderProfiles();
  }

  // ========= 模板列表渲染 + 新建/修改/删除 =========
  function renderProfiles() {
    const wrap = $("#risk-profiles-list");
    if (!wrap) return;
    if (state.profiles.length === 0) {
      wrap.innerHTML = `<div class="risk-effective__skeleton">暂无模板，点击右上角「➕ 新建模板」创建。</div>`;
      return;
    }
    wrap.innerHTML = state.profiles.map((p) => {
      const cls = [
        "risk-profile-card",
        p.is_default ? "is-default" : "",
        p.owner_id == null ? "is-system" : "",
      ].join(" ");
      const badge = p.owner_id == null
        ? `<span class="risk-profile-card__badge" style="background:color-mix(in srgb,var(--fwui-primary) 18%,transparent);color:var(--fwui-primary);">系统内置</span>`
        : (p.is_default ? `<span class="risk-profile-card__badge" style="background:color-mix(in srgb,#f59e0b 20%,transparent);color:#d97706;">我的默认</span>` : "");
      const paramsHtml = [
        ["单笔比例", p.risk_single_ratio, "pct"],
        ["日亏比例", p.risk_daily_loss_ratio, "pct"],
        ["单日金额", p.max_daily_amount, "usd"],
        ["单日次数", p.max_daily_count, "int"],
        ["连续失败", p.max_consecutive_failures, "int"],
        ["最大回撤", p.max_drawdown_ratio, "pct"],
        ["最大持仓", p.max_open_positions, "int"],
        ["止损", p.stop_loss_ratio, "pct"],
        ["止盈", p.take_profit_ratio, "pct"],
      ].map(([k, v, t]) => {
        let display;
        if (v == null) display = "-";
        else if (t === "pct") display = (parseFloat(v) * 100).toFixed(1) + "%";
        else if (t === "usd") display = "$" + fmtNum(v);
        else display = fmtNum(v);
        return `<span>${k}：<b>${display}</b></span>`;
      }).join("");
      return `
        <div class="${cls}" data-profile-id="${p.id}">
          <div class="risk-profile-card__header">
            <div class="risk-profile-card__name">${escapeHtml(p.name)}</div>
            ${badge}
          </div>
          <div class="risk-profile-card__desc">${escapeHtml(p.description || (p.owner_id == null ? "系统默认保守风控参数，适用于绝大多数账户。" : "暂无描述"))}</div>
          <div class="risk-profile-card__params">${paramsHtml}</div>
          <div class="risk-profile-card__actions">
            <button type="button" class="fwui-btn fwui-btn--sm" data-action="edit">✏️ 修改</button>
            <button type="button" class="fwui-btn fwui-btn--sm" data-action="copy">📋 复制为我的模板</button>
            ${p.owner_id == null ? "" : '<button type="button" class="fwui-btn fwui-btn--sm fwui-btn--danger" data-action="delete">🗑️ 删除</button>'}
            ${p.is_default || p.owner_id == null ? "" : '<button type="button" class="fwui-btn fwui-btn--sm fwui-btn--primary" data-action="default">⭐ 设为默认</button>'}
          </div>
        </div>
      `;
    }).join("");
    // 绑定按钮事件
    wrap.querySelectorAll(".risk-profile-card").forEach((card) => {
      const pid = parseInt(card.dataset.profileId);
      card.querySelector("[data-action=edit]").onclick = () => openProfileEditor(pid);
      card.querySelector("[data-action=copy]").onclick = async () => {
        const name = prompt("请输入新模板名称：", state.profiles.find((p) => p.id === pid)?.name + " (副本)");
        if (!name) return;
        try {
          await FWUI.api.request("POST", "/api/risk/profiles", { name, risk_profile_id: pid });
          FWUI.toast.success("模板已复制");
          await loadProfiles("profiles");
        } catch (e) { FWUI.toast.error(e.message || "复制失败"); }
      };
      const delBtn = card.querySelector("[data-action=delete]");
      if (delBtn) {
        delBtn.onclick = async () => {
          if (!confirm("确定删除此模板吗？（已使用该模板的账户/任务将回退到系统默认）")) return;
          try {
            await FWUI.api.request("DELETE", `/api/risk/profiles/${pid}`);
            FWUI.toast.success("模板已删除");
            await loadProfiles("profiles");
          } catch (e) { FWUI.toast.error(e.message || "删除失败"); }
        };
      }
      const defBtn = card.querySelector("[data-action=default]");
      if (defBtn) {
        defBtn.onclick = async () => {
          try {
            await FWUI.api.request("PATCH", `/api/risk/profiles/${pid}`, { is_default: true });
            FWUI.toast.success("已设为默认模板");
            await loadProfiles("profiles");
          } catch (e) { FWUI.toast.error(e.message || "设置失败"); }
        };
      }
    });
  }

  function openProfileEditor(pid) {
    const p = state.profiles.find((x) => x.id === pid) || null;
    const isNew = !p;
    const form = document.createElement("div");
    form.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:10px;max-width:560px;">
        <label>模板名称
          <input class="fwui-input" name="name" value="${escapeAttr(p?.name || "")}" placeholder="例如：高风险激进" required>
        </label>
        <label>描述
          <input class="fwui-input" name="description" value="${escapeAttr(p?.description || "")}">
        </label>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;">
          <label>📛 单笔下单比例（账户余额 %）<input class="fwui-input" type="number" step="0.01" min="0" max="1" name="risk_single_ratio" value="${fmtNumOrEmpty(p?.risk_single_ratio)}" placeholder="0.05 = 5%"></label>
          <label>📉 日亏冻结比例（初始资金 %）<input class="fwui-input" type="number" step="0.01" min="0" max="1" name="risk_daily_loss_ratio" value="${fmtNumOrEmpty(p?.risk_daily_loss_ratio)}" placeholder="0.05 = 5%"></label>
          <label>💰 单日累计金额 (USD)<input class="fwui-input" type="number" step="0.01" min="0" name="max_daily_amount" value="${fmtNumOrEmpty(p?.max_daily_amount)}" placeholder="2000"></label>
          <label>🔢 单日累计次数<input class="fwui-input" type="number" step="1" min="0" name="max_daily_count" value="${fmtNumOrEmpty(p?.max_daily_count)}" placeholder="10"></label>
          <label>💥 连续失败阈值<input class="fwui-input" type="number" step="1" min="0" name="max_consecutive_failures" value="${fmtNumOrEmpty(p?.max_consecutive_failures)}" placeholder="8"></label>
          <label>📊 最大回撤比例<input class="fwui-input" type="number" step="0.01" min="0" max="1" name="max_drawdown_ratio" value="${fmtNumOrEmpty(p?.max_drawdown_ratio)}" placeholder="0.15 = 15%"></label>
          <label>🔁 最大持仓数<input class="fwui-input" type="number" step="1" min="0" name="max_open_positions" value="${fmtNumOrEmpty(p?.max_open_positions)}" placeholder="3"></label>
          <label>🛑 止损比例<input class="fwui-input" type="number" step="0.01" min="0" max="1" name="stop_loss_ratio" value="${fmtNumOrEmpty(p?.stop_loss_ratio)}" placeholder="0.05 = 5%"></label>
          <label>🎯 止盈比例<input class="fwui-input" type="number" step="0.01" min="0" max="1" name="take_profit_ratio" value="${fmtNumOrEmpty(p?.take_profit_ratio)}" placeholder="0.10 = 10%"></label>
        </div>
      </div>
    `;
    FWUI.modal.confirm({
      title: isNew ? "➕ 新建风控模板" : "✏️ 修改风控模板",
      content: form,
      okText: isNew ? "创建" : "保存",
      onOk: async () => {
        const fd = {};
        form.querySelectorAll("input,select").forEach((el) => {
          const v = (el.value || "").trim();
          if (el.type === "number") {
            fd[el.name] = v === "" ? null : (el.step === "1" ? parseInt(v) : parseFloat(v));
          } else {
            fd[el.name] = v || null;
          }
        });
        if (!fd.name) { FWUI.toast.error("请输入模板名称"); throw new Error("validation"); }
        try {
          if (isNew) await FWUI.api.request("POST", "/api/risk/profiles", fd);
          else await FWUI.api.request("PATCH", `/api/risk/profiles/${pid}`, fd);
          FWUI.toast.success(isNew ? "模板创建成功" : "模板已保存");
          await loadProfiles("profiles");
          await loadAccounts(); await loadTasks();  // 重新填充下拉
        } catch (e) { FWUI.toast.error(e.message || "保存失败"); throw e; }
      },
    });
  }

  // ========= 账户 Tab 渲染 =========
  async function selectAccount(accountId) {
    const id = parseInt(accountId);
    state.currentAccountId = id || null;
    const empty = $("#risk-account-empty"); const form = $("#risk-account-form");
    if (!state.currentAccountId) {
      if (empty) empty.style.display = "";
      if (form) form.style.display = "none";
      return;
    }
    if (empty) empty.style.display = "none";
    if (form) form.style.display = "";
    try {
      const r = await FWUI.api.request("GET", `/api/risk/account/${id}`);
      const d = r.data || r;
      const eff = d.effective_params || {};
      // 填表单
      const f = form;
      const paramLabels = {
        risk_single_ratio: "单笔下单比例",
        risk_daily_loss_ratio: "日亏比例阈值",
        max_daily_amount: "单日累计金额",
        max_daily_count: "单日累计次数",
        max_consecutive_failures: "连续失败阈值",
        max_drawdown_ratio: "最大回撤比例",
        max_open_positions: "最大持仓数",
        stop_loss_ratio: "止损比例",
        take_profit_ratio: "止盈比例",
      };
      const set = (name, val) => {
        const el = f.querySelector(`[name=${name}]`);
        if (!el) return;
        if (val != null && val !== "") {
          el.value = String(val);
          el.style.borderColor = "";
          el.title = "";
        } else {
          el.value = "";
          // 设置 placeholder 显示实际生效值
          const effVal = eff[name];
          if (effVal != null) {
            let displayVal;
            if (name.includes("ratio") || name === "max_drawdown_ratio") {
              displayVal = (parseFloat(effVal) * 100).toFixed(2) + "%";
            } else if (name.includes("amount")) {
              displayVal = "$" + fmtNum(effVal);
            } else {
              displayVal = fmtNum(effVal);
            }
            el.placeholder = `生效值: ${displayVal}（设置后覆盖）`;
            el.title = `当前生效值来自${d.risk_profile_id ? "风控模板" : "全局默认"}`;
          } else {
            el.placeholder = "";
            el.title = "";
          }
        }
      };
      set("risk_profile_id", d.risk_profile_id || "");
      set("risk_single_ratio", d.risk_single_ratio);
      set("risk_daily_loss_ratio", d.risk_daily_loss_ratio);
      set("max_daily_amount", d.max_daily_amount);
      set("max_daily_count", d.max_daily_count);
      set("max_consecutive_failures", d.max_consecutive_failures);
      set("max_drawdown_ratio", d.max_drawdown_ratio);
      set("max_open_positions", d.max_open_positions);
      set("stop_loss_ratio", d.stop_loss_ratio);
      set("take_profit_ratio", d.take_profit_ratio);
      // 冻结状态
      const chip = $("#risk-frozen-status");
      if (chip) {
        if (d.is_frozen) {
          chip.className = "risk-chip risk-chip--danger";
          chip.textContent = "🥶 已冻结";
        } else {
          chip.className = "risk-chip risk-chip--ok";
          chip.textContent = "✅ 未冻结";
        }
      }
      const lc = $("#risk-last-check");
      if (lc) lc.textContent = d.last_check_at ? ("上次检查：" + fmtDate(d.last_check_at)) : "上次检查：--";
      const frWrap = $("#risk-frozen-reason-wrap");
      const fr = $("#risk-frozen-reason");
      if (frWrap && fr) {
        if (d.is_frozen && d.frozen_reason) { frWrap.style.display = ""; fr.textContent = d.frozen_reason; }
        else frWrap.style.display = "none";
      }
      // 最终生效参数
      renderEffectiveParams("#risk-account-effective", d.effective_params);
    } catch (e) {
      FWUI.toast.error(e.message || "加载账户风控失败");
    }
  }

  function bindAccountForm() {
    $("#risk-account-select")?.addEventListener("change", (e) => selectAccount(e.target.value));
    $("#btn-new-profile")?.addEventListener("click", () => openProfileEditor(null));

    const form = $("#risk-account-form");
    if (!form) return;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!state.currentAccountId) return;
      const payload = {};
      form.querySelectorAll("input,select").forEach((el) => {
        if (el.disabled) return;
        const v = (el.value || "").trim();
        if (el.name === "risk_profile_id") {
          payload[el.name] = v === "" ? null : parseInt(v);
        } else if (el.type === "number") {
          if (el.step === "1") payload[el.name] = v === "" ? null : parseInt(v);
          else payload[el.name] = v === "" ? null : parseFloat(v);
        }
      });
      try {
        await FWUI.api.request("PATCH", `/api/risk/account/${state.currentAccountId}`, payload);
        const t = $("#risk-account-saved");
        if (t) { t.style.display = ""; setTimeout(() => (t.style.display = "none"), 2000); }
        FWUI.toast.success("账户风控已保存");
        await selectAccount(state.currentAccountId);  // 重新拉最新
      } catch (e) { FWUI.toast.error(e.message || "保存失败"); }
    });
    // 冻结 / 解冻
    $("#btn-freeze-account")?.addEventListener("click", () => {
      if (!state.currentAccountId) return;
      const reason = prompt("请输入冻结原因（记录在审计日志）：", "管理员手动冻结");
      if (reason == null) return;
      FWUI.modal.confirm({
        title: "⚠️ 冻结该账户？",
        content: `<div>冻结后该账户所有交易路径将被拦截，直到手动解冻。<br>原因：<b>${escapeHtml(reason)}</b></div>`,
        okText: "确认冻结", okClass: "fwui-btn--danger",
        onOk: async () => {
          try {
            await FWUI.api.request("POST", "/api/risk/account/freeze", { account_id: state.currentAccountId, reason });
            FWUI.toast.success("账户已冻结");
            await selectAccount(state.currentAccountId);
            refreshBadge();
          } catch (e) { FWUI.toast.error(e.message || "冻结失败"); throw e; }
        },
      });
    });
    $("#btn-unfreeze-account")?.addEventListener("click", () => {
      if (!state.currentAccountId) return;
      const reason = prompt("请输入解冻原因（记录在审计日志）：", "管理员手动解冻");
      if (reason == null) return;
      FWUI.modal.confirm({
        title: "🔥 确认解除冻结？",
        content: `<div>解除后账户将恢复正常交易。<br>原因：<b>${escapeHtml(reason)}</b></div>`,
        okText: "确认解冻", okClass: "fwui-btn--primary",
        onOk: async () => {
          try {
            await FWUI.api.request("POST", "/api/risk/account/unfreeze", { account_id: state.currentAccountId, reason });
            FWUI.toast.success("账户已解冻");
            await selectAccount(state.currentAccountId);
            refreshBadge();
          } catch (e) { FWUI.toast.error(e.message || "解冻失败"); throw e; }
        },
      });
    });
  }

  // ========= 任务 Tab 渲染 =========
  async function selectTask(taskId) {
    const id = parseInt(taskId);
    state.currentTaskId = id || null;
    const empty = $("#risk-task-empty"); const form = $("#risk-task-form");
    if (!state.currentTaskId) {
      if (empty) empty.style.display = "";
      if (form) form.style.display = "none";
      return;
    }
    if (empty) empty.style.display = "none";
    if (form) form.style.display = "";
    try {
      const r = await FWUI.api.request("GET", `/api/risk/strategy/${id}`);
      const d = r.data || r;
      const eff = d.effective_params || {};
      const set = (name, val) => {
        const el = form.querySelector(`[name=${name}]`);
        if (!el) return;
        if (name.startsWith("__")) {
          // 只读字段直接设值
          el.value = val == null ? "0" : String(val);
          return;
        }
        if (val != null && val !== "") {
          el.value = String(val);
          el.style.borderColor = "";
          el.title = "";
        } else {
          el.value = "";
          // 设置 placeholder 显示实际生效值
          const effVal = eff[name];
          if (effVal != null) {
            let displayVal;
            if (name.includes("ratio") || name === "max_drawdown_ratio") {
              displayVal = (parseFloat(effVal) * 100).toFixed(2) + "%";
            } else if (name.includes("amount")) {
              displayVal = "$" + fmtNum(effVal);
            } else {
              displayVal = fmtNum(effVal);
            }
            el.placeholder = `生效值: ${displayVal}（设置后覆盖）`;
            el.title = `当前生效值来自${d.risk_profile_id ? "风控模板" : "全局默认"}`;
          } else {
            el.placeholder = "";
            el.title = "";
          }
        }
      };
      set("risk_profile_id", d.risk_profile_id || "");
      set("risk_single_ratio", d.risk_single_ratio);
      set("risk_daily_loss_ratio", d.risk_daily_loss_ratio);
      set("max_daily_amount", d.max_daily_amount);
      set("max_daily_count", d.max_daily_count);
      set("max_consecutive_failures", d.max_consecutive_failures);
      set("max_drawdown_ratio", d.max_drawdown_ratio);
      set("max_open_positions", d.max_open_positions);
      set("stop_loss_ratio", d.stop_loss_ratio);
      set("take_profit_ratio", d.take_profit_ratio);
      set("__read_consecutive_failures", d.consecutive_failures || 0);
      renderEffectiveParams("#risk-task-effective", d.effective_params);
    } catch (e) { FWUI.toast.error(e.message || "加载任务风控失败"); }
  }

  function bindTaskForm() {
    $("#risk-task-select")?.addEventListener("change", (e) => selectTask(e.target.value));
    const form = $("#risk-task-form");
    if (!form) return;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!state.currentTaskId) return;
      const payload = {};
      form.querySelectorAll("input,select").forEach((el) => {
        if (el.disabled || !el.name || el.name.startsWith("__")) return;
        const v = (el.value || "").trim();
        if (el.name === "risk_profile_id") payload[el.name] = v === "" ? null : parseInt(v);
        else if (el.type === "number") {
          payload[el.name] = v === "" ? null : (el.step === "1" ? parseInt(v) : parseFloat(v));
        }
      });
      try {
        await FWUI.api.request("PATCH", `/api/risk/strategy/${state.currentTaskId}`, payload);
        const t = $("#risk-task-saved");
        if (t) { t.style.display = ""; setTimeout(() => (t.style.display = "none"), 2000); }
        FWUI.toast.success("任务风控已保存");
        await selectTask(state.currentTaskId);
      } catch (e) { FWUI.toast.error(e.message || "保存失败"); }
    });
  }

  // ========= 事件日志 Tab =========
  let eventOffset = 0;
  async function loadEvents(reset) {
    if (reset) eventOffset = 0;
    const params = new URLSearchParams({
      limit: String(state.eventLimit),
      offset: String(eventOffset),
    });
    ["type", "severity", "stage"].forEach((k) => {
      const v = $("#event-filter-" + (k === "type" ? "type" : (k === "severity" ? "severity" : "stage")))?.value;
      if (v) params.set(k, v);
    });
    try {
      const r = await FWUI.api.request("GET", "/api/risk/events?" + params.toString());
      const d = r.data || {};
      const list = d.items || [];
      state.eventTotal = d.total || 0;
      const wrap = $("#risk-events-list");
      if (!wrap) return;
      const html = list.length === 0
        ? `<div class="risk-effective__skeleton">${eventOffset === 0 ? "暂无可查看的风控事件" : "没有更多数据了"}</div>`
        : list.map(renderEvent).join("");
      if (reset) wrap.innerHTML = html;
      else wrap.insertAdjacentHTML("beforeend", html);
      // 总数 / 加载更多
      const total = $("#risk-events-total");
      if (total) total.textContent = `共 ${state.eventTotal} 条，当前展示 ${Math.min(eventOffset + list.length, state.eventTotal)} 条`;
      const btn = $("#btn-event-loadmore");
      if (btn) btn.style.display = (eventOffset + list.length < state.eventTotal) ? "" : "none";
      // 绑定展开详情
      wrap.querySelectorAll(".risk-event").forEach((card) => {
        const btn = card.querySelector(".risk-event__toggle");
        const detail = card.querySelector(".risk-event__detail");
        if (btn && detail) btn.onclick = () => detail.classList.toggle("is-open");
      });
    } catch (e) { FWUI.toast.error(e.message || "加载事件失败"); }
  }

  function renderEvent(e) {
    const sevMap = { 1: "ℹ️ 信息", 2: "⚠️ 警告", 3: "🚨 严重" };
    const typeMap = { 1: "✅ 通过", 2: "🛑 拦截", 3: "🥶 冻结", 4: "🔥 解冻", 5: "✏️ 参数变更" };
    const detailJson = (e.detail_json && Object.keys(e.detail_json).length > 0) ? `<div class="risk-event__detail" id="ev-d-${e.id}">${JSON.stringify(e.detail_json, null, 2)}</div>` : "";
    const hasDetail = !!detailJson;
    const titleExtra = [
      e.account_id ? `📂Acct#${e.account_id}` : "",
      e.auto_strategy_id ? `🤖Task#${e.auto_strategy_id}` : "",
      e.rule_name ? `（${escapeHtml(e.rule_name)}）` : "",
    ].filter(Boolean).join(" · ");
    return `
      <div class="risk-event">
        <div class="risk-event__row1">
          <div class="risk-event__title">
            <span class="risk-event__sev risk-event__sev--${e.severity || 1}">${sevMap[e.severity] || "ℹ️ 信息"}</span>
            <span class="risk-event__type risk-event__type--${e.event_type || 1}">${typeMap[e.event_type] || "事件"}</span>
            <span>${escapeHtml(e.title || "(无标题)")}</span>
          </div>
          <div style="font-size:12px;color:var(--fwui-text-muted);">${fmtDate(e.created_at)}</div>
        </div>
        ${titleExtra ? `<div style="font-size:11px;color:var(--fwui-text-muted);margin-top:2px;">${titleExtra}${e.stage ? " · STAGE=" + escapeHtml(e.stage) : ""}</div>` : ""}
        ${e.message ? `<div class="risk-event__msg">${escapeHtml(e.message).replace(/\n/g, "<br>")}</div>` : ""}
        <div class="risk-event__snapshot">
          <span>余额: $${fmtNum(e.balance_snapshot)}</span>
          <span>日盈亏: ${e.daily_pnl_snapshot >= 0 ? "+" : ""}$${fmtNum(e.daily_pnl_snapshot)}</span>
          ${e.order_amount_snapshot ? `<span>下单金额: $${fmtNum(e.order_amount_snapshot)}</span>` : ""}
        </div>
        ${hasDetail ? `<button type="button" class="risk-event__toggle" data-toggle="${e.id}">⚙️ 查看详情JSON ▾</button>${detailJson}` : ""}
      </div>
    `;
  }

  function bindEventsTab() {
    ["#event-filter-type", "#event-filter-severity", "#event-filter-stage"].forEach((sel) => {
      document.querySelector(sel)?.addEventListener("change", () => loadEvents(true));
    });
    $("#btn-event-refresh")?.addEventListener("click", () => { loadEvents(true); refreshBadge(); });
    $("#btn-event-loadmore")?.addEventListener("click", () => { eventOffset += state.eventLimit; loadEvents(false); });
  }

  // ========= 摘要（管理员）=========
  async function loadSummary() {
    const sec = $("#risk-summary");
    if (!sec || state.role < 3) return;
    try {
      const r = await FWUI.api.request("GET", "/api/risk/summary");
      const d = r.data || {};
      sec.style.display = "";
      $("#summary-frozen").textContent = fmtNum(d.frozen_accounts || 0);
      $("#summary-blocked").textContent = fmtNum(d.last_24h?.blocked_events || 0);
      $("#summary-freeze").textContent = fmtNum(d.last_24h?.freeze_events || 0);
      $("#summary-unfreeze").textContent = fmtNum(d.last_24h?.unfreeze_events || 0);
    } catch (_) {
      // 非管理员：隐藏
      if (sec) sec.style.display = "none";
    }
  }

  // ========= 通用工具 =========
  function renderEffectiveParams(sel, obj) {
    const wrap = document.querySelector(sel);
    if (!wrap) return;
    if (!obj || Object.keys(obj).length === 0) {
      wrap.innerHTML = `<div class="risk-effective__skeleton">暂无生效参数</div>`;
      return;
    }
    const LABEL = {
      risk_single_ratio: ["📛 单笔下单比例", "pct"],
      risk_daily_loss_ratio: ["📉 日亏冻结比例", "pct"],
      max_daily_amount: ["💰 单日累计金额", "usd"],
      max_daily_count: ["🔢 单日累计次数", "int"],
      max_consecutive_failures: ["💥 连续失败阈值", "int"],
      max_drawdown_ratio: ["📊 最大回撤比例", "pct"],
      max_open_positions: ["🔁 最大持仓数", "int"],
      stop_loss_ratio: ["🛑 止损比例", "pct"],
      take_profit_ratio: ["🎯 止盈比例", "pct"],
    };
    const inner = Object.keys(LABEL).map((k) => {
      const [label, kind] = LABEL[k];
      const raw = obj[k];
      let v;
      if (raw == null || raw === "") v = `<span style="color:var(--fwui-text-muted);font-weight:400;">未设置（取全局默认）</span>`;
      else if (kind === "pct") v = (parseFloat(raw) * 100).toFixed(2) + "%";
      else if (kind === "usd") v = "$" + fmtNum(raw);
      else if (kind === "int") v = fmtNum(raw);
      else v = fmtNum(raw);
      return `<div class="risk-effective-params__item"><div class="risk-effective-params__k">${label}</div><div class="risk-effective-params__v">${v}</div></div>`;
    }).join("");
    wrap.innerHTML = `<div class="risk-effective-params__grid">${inner}</div>`;
  }

  async function refreshBadge() {
    // 刷新事件 tab 数字徽标：今天的事件数量（管理员看全部，用户看自己）
    try {
      const r = await FWUI.api.request("GET", "/api/risk/events?limit=1");
      const badge = $("#events-badge");
      const total = (r && r.data && r.data.total) || 0;
      if (badge) {
        if (total > 0) { badge.style.display = ""; badge.textContent = total > 99 ? "99+" : String(total); }
        else badge.style.display = "none";
      }
    } catch (_) {}
  }

  // ========= Tab 切换（左侧菜单 + 移动端 tabs 联动）=========
  function switchTab(tabName) {
    state.currentTab = tabName;
    $$("[data-tab]").forEach((el) => el.classList.toggle("is-active", el.dataset.tab === tabName));
    $$(".risk-panel").forEach((p) => p.classList.toggle("is-active", p.dataset.panel === tabName));
    // 触发 tab 专属加载
    if (tabName === "events") loadEvents(true);
    else if (tabName === "profiles") loadProfiles("profiles");
  }

  function bindTabs() {
    $$("[data-tab]").forEach((el) => {
      el.addEventListener("click", () => switchTab(el.dataset.tab));
    });
  }

  // ========= 工具函数 =========
  function escapeHtml(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function escapeAttr(s) { return escapeHtml(s).replace(/"/g, "&quot;"); }
  function fmtNum(v) {
    if (v == null || v === "" || isNaN(Number(v))) return "-";
    const n = Number(v);
    if (Number.isInteger(n)) return n.toLocaleString();
    return n.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  function fmtNumOrEmpty(v) { return (v == null || v === "" || isNaN(Number(v))) ? "" : String(v); }
  function fmtDate(s) {
    try { return FWUI.utils ? FWUI.utils.fmtDate(s) : new Date(s).toLocaleString(); }
    catch (_) { return String(s); }
  }

  // ========= 启动 =========
  async function bootstrap() {
    injectStyles();
    bindTabs();
    bindAccountForm();
    bindTaskForm();
    bindEventsTab();
    // 加载当前用户
    try {
      const u = await FWUI.api.me({ silent401: true });
      state.role = u.role || 0;
      state.userId = u.id || null;
      const roleTag = $("#risk-current-role");
      if (roleTag) roleTag.textContent = u.nickname || u.email || "";
    } catch (_) { /* 未登录 */ }
    // 摘要（管理员才可见）
    if (state.role >= 3) await loadSummary();
    await Promise.all([loadAccounts(), loadTasks(), loadProfiles(state.currentTab === "profiles" ? "profiles" : null)]);
    refreshBadge();
    // 如果初始选中了某个 tab 是 events，加载
    if (state.currentTab === "events") loadEvents(true);
    else if (state.currentTab === "profiles") loadProfiles("profiles");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
  } else {
    bootstrap();
  }
})();
