// FWUI API 客户端：统一封装 fetch，自动注入 JWT，统一错误处理
// WP-06：演示模式自动给所有 /api/* 路径加 /api/demo 前缀（数据层物理隔离）
(function (global) {
  "use strict";

  const TOKEN_KEY = "fwsort.token";

  function getToken() { return localStorage.getItem(TOKEN_KEY) || ""; }
  function setToken(t) { localStorage.setItem(TOKEN_KEY, t); }
  function clearToken() { localStorage.removeItem(TOKEN_KEY); }

  // WP-06：演示模式路径前缀处理
  function _applyDemoPrefix(path) {
    if (!global.__FW_DEMO_MODE__) return path;
    if (typeof path !== "string" || !path.startsWith("/api/")) return path;
    if (path.startsWith("/api/demo/")) return path;  // 已加过
    return "/api/demo" + path.slice(4);
  }

  // 内部：统一请求
  async function request(method, url, body, opts) {
    const headers = { "Content-Type": "application/json" };
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const finalUrl = _applyDemoPrefix(url);
    const resp = await fetch(finalUrl, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });

    let data = null;
    try { data = await resp.json(); } catch (e) { /* 忽略 */ }

    if (!resp.ok || (data && data.success === false)) {
      // 解析错误信息：优先 message，其次 data.errors[0].message，最后 HTTP 状态码
      let message = (data && data.message) || "";
      if (!message && data && Array.isArray(data.detail) && data.detail.length) {
        // FastAPI 默认 422 格式兜底
        const first = data.detail[0];
        const field = (first.loc || []).filter((x) => x !== "body" && x !== "query" && x !== "path").join(".");
        message = field ? `${field}: ${first.msg || "invalid value"}` : (first.msg || `HTTP ${resp.status}`);
      }
      if (!message && data && data.data && Array.isArray(data.data.errors) && data.data.errors.length) {
        // 统一响应格式兜底
        const e0 = data.data.errors[0];
        message = e0 && e0.field !== "<root>" ? `${e0.field}: ${e0.message}` : (e0 && e0.message) || `HTTP ${resp.status}`;
      }
      if (!message) message = `HTTP ${resp.status}`;
      // 401 自动清 token（silent 模式下不弹 toast，用于页面初始化探活）
      if (resp.status === 401) {
        clearToken();
        if (!opts || !opts.silent401) {
          if (global.FWUI && global.FWUI.toast) global.FWUI.toast.error("登录已过期，请重新登录");
        }
      }
      const err = new Error(message);
      err.status = resp.status;
      err.data = data;
      throw err;
    }
    return data && data.data !== undefined ? data.data : data;
  }

  const api = {
    getToken, setToken, clearToken,

    // 便捷方法
    async get(path, params = {}, opts) {
      const qs = new URLSearchParams(params).toString();
      const url = qs ? `${path}?${qs}` : path;
      return request("GET", url, null, opts);
    },
    async post(path, body = {}, opts) {
      return request("POST", path, body, opts);
    },

    // 认证
    hasAdmin: (opts) => request("GET", "/api/auth/has-admin", null, opts),
    register: (payload) => request("POST", "/api/auth/register", payload),
    login:    (payload) => {
      // 演示模式：忽略 payload，直接走 demo-login（不需要密码）
      if (global.__FW_DEMO_MODE__) {
        const demoPrefix = global.__FW_DEMO_PREFIX__ || "/api/demo";
        return request("POST", `${demoPrefix}/auth/demo-login`, {});
      }
      return request("POST", "/api/auth/login", payload);
    },
    me:       (opts) => request("GET", "/api/auth/me", null, opts),

    // 榜单
    rankingList:    (params = {}) => {
      const qs = new URLSearchParams(params).toString();
      return request("GET", `/api/ranking/list?${qs}`);
    },
    rankingDetail:  (uid) => request("GET", `/api/ranking/detail/${uid}`),
    rankingHistory: (params) => {
      const qs = new URLSearchParams(params || {}).toString();
      return request("GET", `/api/ranking/history?${qs}`);
    },
    rankingChange:  (uid, days) => request("GET", `/api/ranking/change/${uid}?days=${days || 7}`),
    rankingExport:  (rankType) => request("GET", `/api/ranking/export?rank_type=${rankType}`),

    // 智能体
    myAccounts:        () => request("GET", "/api/agent/accounts"),
    predictAndVote:    (accountId, payload) => request("POST", `/api/agent/predict-and-vote?account_id=${accountId}`, payload),
    executionLogs:     (uid, limit = 50) => request("GET", `/api/agent/execution/${uid}?limit=${limit}`),
    agentTasks:        () => request("GET", "/api/agent/tasks"),

    // 跟单
    followMarket:      (params) => {
      const qs = new URLSearchParams(params || { rank_type: "all_time", limit: 20 }).toString();
      return request("GET", `/api/follow/market?${qs}`);
    },
    followSubscribe:   (params) => {
      const qs = new URLSearchParams(params).toString();
      return request("POST", `/api/follow/subscribe?${qs}`);
    },
    followMy:          () => request("GET", "/api/follow/my"),
    followCancel:      (id) => request("DELETE", `/api/follow/${id}`),
    followOrders:      (id, limit = 50) => request("GET", `/api/follow/orders/${id}?limit=${limit}`),

    // 智能体租用
    rentalAgents:      () => request("GET", "/api/rental/agents"),
    rentalCall:        (params) => {
      const qs = new URLSearchParams(params).toString();
      return request("POST", `/api/rental/call?${qs}`);
    },
    rentPackage:       (params) => {
      const qs = new URLSearchParams(params).toString();
      return request("POST", `/api/rental/rent?${qs}`);
    },
    rentalMy:          () => request("GET", "/api/rental/my"),
    rentalCancel:      (id) => request("POST", `/api/rental/${id}/cancel`),

    // 通知
    notifyList:        (onlyUnread = false, limit = 30) => request("GET", `/api/notify/list?only_unread=${onlyUnread}&limit=${limit}`),
    notifyMarkRead:    (id) => request("POST", `/api/notify/${id}/read`),
    notifyReadAll:     () => request("POST", "/api/notify/read-all"),

    // 权重
    getWeights:   (rankType = 1) => request("GET", `/api/ranking/config/weights?rank_type=${rankType}`),
    updateWeights:(rankType, payload) => request("PUT", `/api/ranking/config/weights?rank_type=${rankType}`, payload),
  };

  global.FWUI = global.FWUI || {};
  global.FWUI.api = api;
})(window);
