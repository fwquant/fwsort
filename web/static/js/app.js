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

  // 登录弹框：打开时探测 has-admin，未播种时高亮引导 + 登录失败时提供跳转
  function openLoginModal() {
    const form = document.createElement("div");
    form.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:12px;">
        <div id="login-hint" style="display:none;padding:10px 12px;border-radius:8px;font-size:12px;line-height:1.6;"></div>
        <label>邮箱 <input class="fwui-input" name="email" type="email" placeholder="email@example.com" required value="admin@fwquant.com"></label>
        <label>密码 <input class="fwui-input" name="password" type="password" placeholder="≥ 1 位" required autocomplete="current-password"></label>
        <div style="font-size:11px;color:var(--fwui-text-muted);line-height:1.5;">
          · 默认管理员：<code>admin@fwquant.com</code> / <code>admin123456</code>（需先播种）<br>
          · 想先体验？<a href="/demo" style="color:var(--fwui-primary);font-weight:600;">进入演示模式 →</a>
        </div>
      </div>
    `;
    const hint = form.querySelector("#login-hint");
    // 打开时先检查是否已播种 admin（无副作用公开接口）
    let hasAdmin = true;
    FWUI.api.hasAdmin({ silent401: true }).then((d) => {
      hasAdmin = !!(d && d.has_admin);
      if (!hasAdmin) {
        hint.style.display = "";
        hint.style.background = "rgba(239,68,68,0.1)";
        hint.style.border = "1px solid rgba(239,68,68,0.3)";
        hint.style.color = "var(--fwui-danger)";
        hint.innerHTML =
          '⚠️ <strong>尚未创建管理员账户</strong>，首次使用请先：' +
          '<a href="/admin" style="color:var(--fwui-danger);font-weight:700;margin:0 4px;">前往 /admin →</a>' +
          "点击『一键初始化 + 播种』或『创建管理员』。也可直接进入<a href=\"/demo\" style=\"color:var(--fwui-primary);font-weight:700;margin:0 4px;\">演示模式</a>无需注册。";
      }
    }).catch(() => { /* 忽略探测失败 */ });

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
        } catch (e) {
          const msg = (e && e.message) || "登录失败";
          const status = (e && e.status) || 0;
          // 区分两种场景：
          // 1) 密码错误 / 用户名不存在 → 直接提示"用户名或密码错误"，不误导
          // 2) 系统未播种管理员（401 + 无管理员）→ 给出引导跳转
          const isCredentialError = /用户名或密码错误|invalid|credential/i.test(msg);
          const isNeedBootstrap = status === 401 && !hasAdmin;
          if (isNeedBootstrap) {
            // 场景2：系统确实没有管理员，引导用户去播种
            FWUI.modal.confirm({
              title: "登录失败：未创建管理员",
              content: (function () {
                const wrap = document.createElement("div");
                wrap.style.cssText = "font-size:13px;line-height:1.7;";
                wrap.innerHTML =
                  "<p>检测到系统可能还未初始化管理员账号（默认 <code>admin@fwquant.com</code> / <code>admin123456</code> 需先播种）。</p>" +
                  '<ul style="margin:8px 0 8px 20px;">' +
                  '<li><a href="/admin" style="color:var(--fwui-primary);font-weight:700;">前往控制台 /admin →</a> 点击『一键初始化 + 播种』</li>' +
                  '<li><a href="/demo" style="color:var(--fwui-primary);font-weight:700;">进入演示模式 /demo →</a>（内置演示数据，无需注册）</li>' +
                  "</ul>";
                return wrap;
              })(),
              okText: "前往控制台",
              cancelText: "再试一次",
              showCancel: true,
              onOk: () => { location.href = "/admin"; },
            });
            throw e;
          }
          // 场景1：密码错误等常规错误，直接提示真实原因
          FWUI.toast.error(msg);
          throw e;
        }
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

  // 暴露登录/注册接口，供 admin.html 等页面在未登录时主动唤起登录框
  window.FWUI = window.FWUI || {};
  window.FWUI.auth = {
    openLoginModal: openLoginModal,
    openRegisterModal: openRegisterModal,
    isLoggedIn: () => !!FWUI.api.getToken(),
  };

  // DOM Ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderNav);
  } else {
    renderNav();
  }
})();