// FWUI API 客户端：统一封装 fetch，自动注入 JWT，统一错误处理
(function (global) {
  "use strict";

  const TOKEN_KEY = "fwsort.token";

  function getToken() { return localStorage.getItem(TOKEN_KEY) || ""; }
  function setToken(t) { localStorage.setItem(TOKEN_KEY, t); }
  function clearToken() { localStorage.removeItem(TOKEN_KEY); }

  // 内部：统一请求
  async function request(method, url, body) {
    const headers = { "Content-Type": "application/json" };
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const resp = await fetch(url, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });

    let data = null;
    try { data = await resp.json(); } catch (e) { /* 忽略 */ }

    if (!resp.ok || (data && data.success === false)) {
      const message = (data && data.message) || `HTTP ${resp.status}`;
      // 401 自动清 token
      if (resp.status === 401) {
        clearToken();
        if (global.FWUI && global.FWUI.toast) global.FWUI.toast.error("登录已过期，请重新登录");
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

    // 认证
    register: (payload) => request("POST", "/api/auth/register", payload),
    login:    (payload) => request("POST", "/api/auth/login", payload),
    me:       () => request("GET", "/api/auth/me"),

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
