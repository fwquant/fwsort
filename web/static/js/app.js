// 应用入口：导航栏 + 主题切换按钮 + 登录态
(function () {
  "use strict";

  // 渲染导航栏
  function renderNav() {
    const token = FWUI.api.getToken();
    const demoMode = !!window.__FW_DEMO_MODE__;
    const demoPrefix = window.__FW_DEMO_PREFIX__ || "";

    // 演示模式：未登录时自动调用 demo-login 拿 token（无密码）
    if (demoMode && !token) {
      FWUI.api.login({}).then((data) => {
        FWUI.api.setToken(data.access_token);
        localStorage.setItem("fwsort.refresh", data.refresh_token || "");
        // 刷新一次页面，让导航/数据都按登录态渲染
        location.reload();
      }).catch((e) => {
        console.warn("demo auto-login failed:", e);
        FWUI.toast.error("演示模式自动登录失败：" + (e.message || "未知错误"));
      });
    }

    const actions = token
      ? `<a href="${demoPrefix}/profile" class="fwui-tag fwui-tag--primary" id="user-nickname" style="text-decoration:none;cursor:pointer;" title="账户管理">--</a>
         <button class="fwui-btn fwui-btn--sm" id="btn-notify" title="通知中心" style="position:relative;">🔔<span id="notify-badge" style="display:none;position:absolute;top:-4px;right:-4px;background:var(--fwui-danger);color:#fff;font-size:10px;border-radius:8px;padding:0 4px;min-width:14px;text-align:center;">0</span></button>
         <button class="fwui-btn fwui-btn--sm" id="btn-logout">退出</button>`
      : `<button class="fwui-btn fwui-btn--sm" id="btn-login">登录</button>
         <button class="fwui-btn fwui-btn--primary fwui-btn--sm" id="btn-register">注册</button>`;

    document.querySelectorAll(".fwui-nav-actions-slot").forEach((slot) => {
      slot.innerHTML = actions;
    });

    // 主题切换按钮
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => FWUI.theme.toggle());
    });
    FWUI.theme.onChange((t) => {
      document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
        btn.textContent = t === "dark" ? "明亮" : "暗紫";
      });
    });
    // 初始化按钮文本
    const cur = FWUI.theme.current();
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      btn.textContent = cur === "dark" ? "明亮" : "暗紫";
    });

    // 登录/注册/退出
    document.getElementById("btn-login")?.addEventListener("click", openLoginModal);
    document.getElementById("btn-register")?.addEventListener("click", openRegisterModal);
    document.getElementById("btn-notify")?.addEventListener("click", openNotifyModal);
    document.getElementById("btn-logout")?.addEventListener("click", () => {
      FWUI.api.clearToken();
      FWUI.toast.success("已退出");
      setTimeout(() => location.reload(), 600);
    });

    // 拉取当前用户 + 通知未读数
    if (token) {
      FWUI.api.me({ silent401: true })
        .then((u) => {
          const el = document.getElementById("user-nickname");
          if (el) el.textContent = u.nickname;
        })
        .catch(() => FWUI.api.clearToken());
      // 通知小红点
      refreshNotifyBadge();
      setInterval(refreshNotifyBadge, 60_000);  // 每分钟刷一次
    }
  }

  // 刷新未读通知数
  async function refreshNotifyBadge() {
    try {
      const data = await FWUI.api.notifyList(true);
      const badge = document.getElementById("notify-badge");
      if (!badge) return;
      const n = data.count || 0;
      if (n > 0) { badge.style.display = ""; badge.textContent = n > 99 ? "99+" : n; }
      else { badge.style.display = "none"; }
    } catch (e) { /* 静默 */ }
  }

  // 通知中心弹框
  async function openNotifyModal() {
    let list = [];
    try {
      const data = await FWUI.api.notifyList(false, 30);
      list = data.items || [];
    } catch (e) { FWUI.toast.error(e.message); return; }
    const html = `
      <div style="max-height:480px;overflow:auto;">
        ${list.length === 0 ? '<p style="text-align:center;color:var(--fwui-text-muted);padding:24px;">暂无通知</p>' : list.map((n) => `
          <div style="padding:10px 8px;border-bottom:1px solid var(--fwui-border);${n.is_read ? 'opacity:0.6;' : ''}">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
              <strong>${escapeHtml(n.title)}</strong>
              <span style="font-size:11px;color:var(--fwui-text-muted);">${FWUI.utils.fmtDate(n.created_at)}</span>
            </div>
            <div style="font-size:13px;color:var(--fwui-text-secondary);margin-top:4px;">${escapeHtml(n.content || "")}</div>
            ${!n.is_read ? `<button class="fwui-btn fwui-btn--sm" data-read="${n.id}" style="margin-top:6px;font-size:11px;">标已读</button>` : ''}
          </div>
        `).join("")}
        ${list.some((n) => !n.is_read) ? '<button class="fwui-btn fwui-btn--sm" id="notify-read-all" style="margin-top:12px;">全部已读</button>' : ''}
      </div>
    `;
    const wrap = document.createElement("div");
    wrap.innerHTML = html;
    FWUI.modal.confirm({
      title: "🔔 通知中心",
      content: wrap,
      okText: "关闭",
      onOk: async () => {
        // 关闭前标已读
        try { await FWUI.api.notifyReadAll(); } catch (e) {}
        refreshNotifyBadge();
      },
    });
    // 单条标已读
    wrap.querySelectorAll("[data-read]").forEach((b) => {
      b.onclick = async (e) => {
        e.stopPropagation();
        try {
          await FWUI.api.notifyMarkRead(parseInt(b.dataset.read));
          b.closest("div[style*='border-bottom']").style.opacity = "0.6";
          b.remove();
          refreshNotifyBadge();
        } catch (e) { FWUI.toast.error(e.message); }
      };
    });
    wrap.querySelector("#notify-read-all")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      try { await FWUI.api.notifyReadAll(); refreshNotifyBadge(); FWUI.toast.success("全部已读"); } catch (e) { FWUI.toast.error(e.message); }
    });
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // 登录弹框
  function openLoginModal() {
    const form = document.createElement("div");
    form.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:12px;">
        <label>邮箱 <input class="fwui-input" name="email" type="email" placeholder="email@example.com" required value="admin@fwquant.com"></label>
        <label>密码 <input class="fwui-input" name="password" type="password" placeholder="≥ 1 位" required></label>
      </div>
    `;
    FWUI.modal.confirm({
      title: "登录",
      content: form,
      okText: "登录",
      onOk: async () => {
        const email = form.querySelector("[name=email]").value.trim();
        const password = form.querySelector("[name=password]").value;
        if (!email || !password) { FWUI.toast.error("请填写完整"); throw new Error("validation"); }
        try {
          const data = await FWUI.api.login({ email, password });
          FWUI.api.setToken(data.access_token);
          localStorage.setItem("fwsort.refresh", data.refresh_token);
          FWUI.toast.success("登录成功");
          setTimeout(() => location.reload(), 600);
        } catch (e) { FWUI.toast.error(e.message || "登录失败"); throw e; }
      },
    });
  }

  // 注册弹框
  function openRegisterModal() {
    const form = document.createElement("div");
    form.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:12px;">
        <label>邮箱 <input class="fwui-input" name="email" type="email" placeholder="email@example.com" required></label>
        <label>昵称 <input class="fwui-input" name="nickname" placeholder="2-64 个字符" required></label>
        <label>密码 <input class="fwui-input" name="password" type="password" placeholder="6-64 位" required></label>
        <div style="font-size:12px;color:var(--fwui-text-muted);line-height:1.6;">
          · 邮箱用于登录与找回<br>
          · 昵称长度 2-64 个字符<br>
          · 密码长度 6-64 位
        </div>
      </div>
    `;
    FWUI.modal.confirm({
      title: "注册",
      content: form,
      okText: "注册",
      onOk: async () => {
        const email = form.querySelector("[name=email]").value.trim();
        const nickname = form.querySelector("[name=nickname]").value.trim();
        const password = form.querySelector("[name=password]").value;
        // 邮箱格式
        const emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!email) { FWUI.toast.error("请输入邮箱"); throw new Error("validation"); }
        if (!emailRe.test(email)) { FWUI.toast.error("邮箱格式不正确"); throw new Error("validation"); }
        // 昵称
        if (nickname.length < 2) { FWUI.toast.error("昵称至少 2 个字符"); throw new Error("validation"); }
        if (nickname.length > 64) { FWUI.toast.error("昵称最多 64 个字符"); throw new Error("validation"); }
        // 密码
        if (password.length < 1) { FWUI.toast.error("密码至少 1 位"); throw new Error("validation"); }
        if (password.length > 64) { FWUI.toast.error("密码最多 64 位"); throw new Error("validation"); }
        try {
          await FWUI.api.register({ email, nickname, password });
          FWUI.toast.success("注册成功，请登录");
        } catch (e) { FWUI.toast.error(e.message || "注册失败"); throw e; }
      },
    });
  }

  // DOM Ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderNav);
  } else {
    renderNav();
  }
})();