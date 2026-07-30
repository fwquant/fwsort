// 账户管理页：左侧栏 / 移动端 Tab 切换 + 个人信息 / 修改密码 / 账户统计
(function () {
  "use strict";

  let _userInfo = null;
  let _currentTab = "info";

  async function init() {
    bindUI();

    if (!FWUI.api.getToken()) {
      showLoginPanel();
      return;
    }

    await loadUserInfo();
    await loadStats();
    await loadPrivacy();
    await loadTokenTTL();
  }

  function bindUI() {
    // 左侧栏菜单点击
    document.querySelectorAll(".profile-sidebar__item[data-tab]").forEach((el) => {
      el.addEventListener("click", () => switchTab(el.dataset.tab));
    });

    // 移动端 Tab 点击
    document.querySelectorAll(".profile-mobile-tab[data-tab]").forEach((el) => {
      el.addEventListener("click", () => switchTab(el.dataset.tab));
    });

    // 功能按钮
    document.getElementById("btn-update-nickname").onclick = updateNickname;
    document.getElementById("btn-change-password").onclick = changePassword;
  }

  // 切换 Tab
  function switchTab(tab) {
    _currentTab = tab;

    // 更新左侧栏选中态
    document.querySelectorAll(".profile-sidebar__item").forEach((el) => {
      el.classList.toggle("is-active", el.dataset.tab === tab);
    });

    // 更新移动端 Tab 选中态
    document.querySelectorAll(".profile-mobile-tab").forEach((el) => {
      el.classList.toggle("is-active", el.dataset.tab === tab);
    });

    // 切换内容面板
    document.querySelectorAll(".profile-panel").forEach((el) => {
      el.classList.toggle("is-active", el.id === "panel-" + tab);
    });

    // 滚动到顶部
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // 显示未登录面板
  function showLoginPanel() {
    document.querySelectorAll(".profile-panel").forEach((el) => el.classList.remove("is-active"));
    document.getElementById("panel-login").style.display = "";
    document.getElementById("panel-login").classList.add("is-active");

    document.querySelectorAll(".profile-sidebar__item, .profile-mobile-tab").forEach((el) => {
      el.style.pointerEvents = "none";
      el.style.opacity = "0.4";
    });
  }

  // 加载用户信息
  async function loadUserInfo() {
    const container = document.getElementById("profile-info");
    try {
      const data = await FWUI.api.get("/api/auth/me");
      _userInfo = data;
      container.innerHTML = renderInfo(data);

      // 填充昵称输入框
      document.getElementById("new-nickname").value = data.nickname || "";

      // 更新侧边栏用户信息
      document.getElementById("sidebar-name").textContent = data.nickname || data.email || "用户";
      document.getElementById("sidebar-email").textContent = data.email || "--";
      const avatarEl = document.getElementById("sidebar-avatar");
      if (avatarEl) {
        avatarEl.textContent = data.nickname ? data.nickname.charAt(0).toUpperCase() : "👤";
      }
    } catch (e) {
      container.innerHTML = `<div class="fwui-empty-card fwui-empty-card--error">${escapeHtml(e.message)}</div>`;
    }
  }

  // 渲染基本信息
  function renderInfo(data) {
    const roleColor = data.role >= 3 ? "primary" : data.role >= 1 ? "warning" : "default";
    const statusColor = data.status === 0 ? "success" : "danger";
    const createdAt = data.created_at ? new Date(data.created_at).toLocaleString("zh-CN") : "-";
    const updatedAt = data.updated_at ? new Date(data.updated_at).toLocaleString("zh-CN") : "-";
    return `
      <div class="profile-info__grid">
        <div class="profile-info__item">
          <span class="profile-info__label">用户ID</span>
          <span class="profile-info__value">#${data.id}</span>
        </div>
        <div class="profile-info__item">
          <span class="profile-info__label">邮箱</span>
          <span class="profile-info__value">${escapeHtml(data.email)}</span>
        </div>
        <div class="profile-info__item">
          <span class="profile-info__label">昵称</span>
          <span class="profile-info__value">${escapeHtml(data.nickname)}</span>
        </div>
        <div class="profile-info__item">
          <span class="profile-info__label">角色</span>
          <span class="profile-info__value"><span class="fwui-tag fwui-tag--${roleColor}">${escapeHtml(data.role_name)}</span></span>
        </div>
        <div class="profile-info__item">
          <span class="profile-info__label">状态</span>
          <span class="profile-info__value"><span class="fwui-tag fwui-tag--${statusColor}">${escapeHtml(data.status_name)}</span></span>
        </div>
        <div class="profile-info__item">
          <span class="profile-info__label">注册时间</span>
          <span class="profile-info__value">${createdAt}</span>
        </div>
        <div class="profile-info__item">
          <span class="profile-info__label">最后更新</span>
          <span class="profile-info__value">${updatedAt}</span>
        </div>
      </div>
    `;
  }

  // 加载账户统计
  async function loadStats() {
    const container = document.getElementById("profile-stats");
    try {
      const [accounts, subscriptions] = await Promise.all([
        FWUI.api.myAccounts().catch(() => ({ count: 0 })),
        FWUI.api.get("/api/follow/my-subscriptions").catch(() => ({ count: 0 })),
      ]);

      const stats = [
        { icon: "💼", number: accounts.count || 0, label: "执行账户" },
        { icon: "👥", number: subscriptions.count || 0, label: "跟单订阅" },
        { icon: "📈", number: _userInfo?.role >= 3 ? "管理" : "普通", label: "权限等级" },
        { icon: "🕐", number: _userInfo?.created_at ? new Date(_userInfo.created_at).getFullYear() : "-", label: "加入年份" },
      ];

      container.innerHTML = `
        <div class="profile-stats__grid">
          ${stats.map(s => `
            <div class="profile-stats__item">
              <div class="profile-stats__icon">${s.icon}</div>
              <div class="profile-stats__number">${s.number}</div>
              <div class="profile-stats__label">${s.label}</div>
            </div>
          `).join("")}
        </div>
      `;
    } catch (e) {
      container.innerHTML = `<div class="fwui-empty-card">暂无统计数据</div>`;
    }
  }

  // 修改昵称
  async function updateNickname() {
    const nickname = document.getElementById("new-nickname").value.trim();
    if (!nickname) {
      FWUI.toast.error("请输入昵称");
      return;
    }
    if (nickname.length > 32) {
      FWUI.toast.error("昵称最长32个字符");
      return;
    }
    const btn = document.getElementById("btn-update-nickname");
    btn.disabled = true;
    try {
      await FWUI.api.post("/api/auth/update-nickname", { nickname });
      FWUI.toast.success("昵称已更新");
      await loadUserInfo();
    } catch (e) {
      FWUI.toast.error(e.message);
    } finally {
      btn.disabled = false;
    }
  }

  // 修改密码
  async function changePassword() {
    const oldPassword = document.getElementById("old-password").value;
    const newPassword = document.getElementById("new-password").value;
    const confirmPassword = document.getElementById("confirm-password").value;

    if (!oldPassword) {
      FWUI.toast.error("请输入当前密码");
      return;
    }
    if (!newPassword || newPassword.length < 1) {
      FWUI.toast.error("新密码至少1位");
      return;
    }
    if (newPassword !== confirmPassword) {
      FWUI.toast.error("两次输入的密码不一致");
      return;
    }

    const btn = document.getElementById("btn-change-password");
    btn.disabled = true;
    try {
      await FWUI.api.post("/api/auth/change-password", {
        old_password: oldPassword,
        new_password: newPassword,
      });
      FWUI.toast.success("密码修改成功，请重新登录");
      document.getElementById("old-password").value = "";
      document.getElementById("new-password").value = "";
      document.getElementById("confirm-password").value = "";
      setTimeout(() => {
        if (confirm("密码已修改，需要重新登录。是否立即退出？")) {
          FWUI.api.logout();
          location.href = "/";
        }
      }, 500);
    } catch (e) {
      FWUI.toast.error(e.message);
    } finally {
      btn.disabled = false;
    }
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // 加载可见性设置
  async function loadPrivacy() {
    const container = document.getElementById("profile-privacy");
    try {
      const data = await FWUI.api.get("/api/auth/privacy");
      container.innerHTML = `
        <div class="profile-privacy__list">
          <div class="profile-privacy__item">
            <div class="profile-privacy__info">
              <div class="profile-privacy__title">🌍 参与总榜单</div>
              <div class="profile-privacy__desc">开启后，您的主账号将出现在首页「总榜单」中（按用户聚合）</div>
            </div>
            <label class="fwui-switch">
              <input type="checkbox" id="toggle-share" ${data.share_to_global ? "checked" : ""}>
              <span class="fwui-switch__slider"></span>
            </label>
          </div>
          <div class="profile-privacy__item">
            <div class="profile-privacy__info">
              <div class="profile-privacy__title">👥 允许被订阅跟单</div>
              <div class="profile-privacy__desc">开启后，其他用户可以订阅您的执行账户并自动跟单；关闭后已订阅用户将无法续订</div>
            </div>
            <label class="fwui-switch">
              <input type="checkbox" id="toggle-follow" ${data.allow_follow ? "checked" : ""}>
              <span class="fwui-switch__slider"></span>
            </label>
          </div>
        </div>
        <div class="profile-privacy__hint">
          💡 提示：账户级「参与总榜单」开关请到 <a href="${(window.__FW_DEMO_PREFIX__ || "") + "/accounts"}">我的账户</a> 单独配置
        </div>
      `;
      document.getElementById("toggle-share").onchange = (e) => updatePrivacy("share_to_global", e.target.checked);
      document.getElementById("toggle-follow").onchange = (e) => updatePrivacy("allow_follow", e.target.checked);
    } catch (e) {
      container.innerHTML = `<div class="fwui-empty-card fwui-empty-card--error">${escapeHtml(e.message)}</div>`;
    }
  }

  async function updatePrivacy(field, value) {
    try {
      await FWUI.api.post("/api/auth/privacy", { [field]: value });
      FWUI.toast.success("可见性已更新");
    } catch (e) {
      FWUI.toast.error(e.message);
      // 回滚 toggle
      const el = document.getElementById(field === "share_to_global" ? "toggle-share" : "toggle-follow");
      if (el) el.checked = !value;
    }
  }

  // 加载登录有效期设置
  async function loadTokenTTL() {
    const container = document.getElementById("profile-ttl");
    try {
      const data = await FWUI.api.get("/api/auth/privacy");
      const ttl = data.token_ttl_minutes || 10080;
      const options = [
        { value: 30, label: "30 分钟" },
        { value: 60, label: "1 小时" },
        { value: 180, label: "3 小时" },
        { value: 1440, label: "1 天" },
        { value: 10080, label: "7 天" },
      ];
      container.innerHTML = `
        <div class="fwui-form-group">
          <label class="fwui-label">登录有效期</label>
          <select class="fwui-input" id="select-token-ttl">
            ${options.map(o => `<option value="${o.value}" ${ttl === o.value ? "selected" : ""}>${o.label}</option>`).join("")}
          </select>
          <div style="font-size:12px;color:var(--fwui-text-muted);margin-top:4px;">设置后下次登录生效，当前登录不受影响</div>
        </div>
        <button class="fwui-btn fwui-btn--primary" id="btn-update-ttl">保存设置</button>
      `;
      document.getElementById("btn-update-ttl").onclick = updateTokenTTL;
    } catch (e) {
      container.innerHTML = `<div class="fwui-empty-card fwui-empty-card--error">${escapeHtml(e.message)}</div>`;
    }
  }

  async function updateTokenTTL() {
    const select = document.getElementById("select-token-ttl");
    const ttl = parseInt(select.value, 10);
    const btn = document.getElementById("btn-update-ttl");
    btn.disabled = true;
    try {
      await FWUI.api.post("/api/auth/privacy", { token_ttl_minutes: ttl });
      FWUI.toast.success("有效期已更新，下次登录生效");
    } catch (e) {
      FWUI.toast.error(e.message);
    } finally {
      btn.disabled = false;
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();