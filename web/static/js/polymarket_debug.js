(function () {
  "use strict";

  var consoleEl = document.getElementById("f3-console");
  var tabBadge = document.getElementById("tab-badge-status");
  var marketInfoEl = document.getElementById("f3-market-info");
  var marketResultEl = document.getElementById("f3-market-result");
  var positionsResultEl = document.getElementById("f3-positions-result");

  // ========== Tab 切换 ==========
  document.querySelectorAll(".pm-tab").forEach(function (tab) {
    tab.onclick = function () {
      var target = tab.dataset.tab;
      document.querySelectorAll(".pm-tab").forEach(function (t) { t.classList.remove("pm-tab--active"); });
      tab.classList.add("pm-tab--active");
      document.querySelectorAll(".pm-panel").forEach(function (p) { p.classList.remove("pm-panel--active"); });
      var panel = document.getElementById("panel-" + target);
      if (panel) panel.classList.add("pm-panel--active");
    };
  });

  // ========== 日志 ==========
  function log(msg, type) {
    type = type || "info";
    var cls = { info: "log-info", success: "log-success", error: "log-error", warn: "log-warn", dim: "log-dim" }[type] || "log-info";
    var ts = new Date().toLocaleTimeString();
    var line = document.createElement("div");
    line.className = cls;
    line.textContent = "[" + ts + "] " + msg;
    if (consoleEl.children.length === 1 && consoleEl.children[0].classList.contains("log-dim")) {
      consoleEl.innerHTML = "";
    }
    consoleEl.appendChild(line);
    consoleEl.scrollTop = consoleEl.scrollHeight;
  }

  function clearLog() {
    consoleEl.innerHTML = '<span class="log-dim">日志已清空</span>';
  }

  function setTabBadge(state, text) {
    if (!tabBadge) return;
    tabBadge.className = "pm-tab-badge pm-tab-badge--" + state;
    tabBadge.textContent = text;
  }

  // ========== 市场信息渲染 ==========
  function renderMarketInfo(container, data) {
    container.style.display = "";
    var items = [];
    if (data.question) items.push({ label: "问题", value: data.question });
    if (data.slug) items.push({ label: "Slug", value: data.slug });
    if (data.condition_id) items.push({ label: "Condition ID", value: data.condition_id });
    if (data.accepting_orders !== undefined) items.push({ label: "接受下单", value: data.accepting_orders ? "✅ 是" : "❌ 否" });
    if (data.closed !== undefined) items.push({ label: "已结算", value: data.closed ? "✅ 是" : "❌ 否" });
    if (data.yes_token_id) items.push({ label: "YES Token", value: data.yes_token_id });
    if (data.no_token_id) items.push({ label: "NO Token", value: data.no_token_id });
    if (data.url) {
      var url = data.url;
      items.push({ label: "市场链接", value: '<a href="' + url + '" target="_blank" style="color:var(--fwui-primary);word-break:break-all;">' + url + "</a>" });
    }
    container.innerHTML = items
      .map(function (item) {
        return '<div class="pm-info-item"><div class="pm-info-label">' + item.label + '</div><div class="pm-info-value">' + item.value + "</div></div>";
      })
      .join("");
  }

  function getSlug(selectId) {
    var el = document.getElementById(selectId);
    return el ? el.value : "";
  }

  // ========== 初始化 ==========
  document.getElementById("btn-f3-init").onclick = async function () {
    log("▶ 开始初始化 F3 客户端...", "info");
    setTabBadge("idle", "初始化中...");
    try {
      var r = await FWUI.api.post("/api/polymarket/f3/init");
      log("✅ F3 客户端初始化成功", "success");
      setTabBadge("ok", "已连接");
      if (r && r.market) {
        renderMarketInfo(marketInfoEl, r.market);
        log("📊 市场: " + (r.market.question || r.market.slug || "未知"), "info");
      }
    } catch (e) {
      log("❌ 初始化失败: " + e.message, "error");
      setTabBadge("err", "失败");
    }
  };

  // ========== 刷新状态 ==========
  document.getElementById("btn-f3-status").onclick = async function () {
    log("▶ 查询 F3 状态...", "info");
    try {
      var r = await FWUI.api.get("/api/polymarket/f3/status");
      var stateText = r.initialized ? "已连接" : "未初始化";
      var stateType = r.initialized ? "ok" : "idle";
      setTabBadge(stateType, stateText);
      log("✅ 状态: " + stateText +
        " | Relayer Key: " + (r.relayer_key_configured ? "✅" : "❌") +
        " | Relayer Addr: " + (r.relayer_addr_configured ? "✅" : "❌") +
        " | Private Key: " + (r.private_key_configured ? "✅" : "❌"), "success");
      if (r.market && r.market.question) {
        renderMarketInfo(marketInfoEl, r.market);
      }
    } catch (e) {
      log("❌ 查询失败: " + e.message, "error");
    }
  };

  // ========== 市场查询 ==========
  document.getElementById("btn-f3-market").onclick = async function () {
    var slug = getSlug("market-slug-select");
    log("▶ 查询市场 slug=" + slug, "info");
    try {
      var r = await FWUI.api.get("/api/polymarket/f3/market", { slug: slug });
      log("✅ 市场查询成功: " + (r.question || r.slug), "success");
      renderMarketInfo(marketResultEl, r);
    } catch (e) {
      log("❌ 查询失败: " + e.message, "error");
    }
  };

  // ========== 下单 ==========
  async function placeOrder() {
    var slug = getSlug("order-slug-select");
    var amount = parseFloat(document.getElementById("order-amount").value) || 1;
    var outcome = document.querySelector('input[name="order-direction"]:checked').value;
    log("▶ F3 下单: slug=" + slug + " outcome=" + outcome + " amount=" + amount, "info");
    try {
      var r = await FWUI.api.post("/api/polymarket/f3/order", { slug: slug, outcome: outcome, amount: amount, side: "BUY" });
      log("✅ 下单成功!", "success");
      if (r.response) log("   订单: " + JSON.stringify(r.response), "success");
      if (r.raw) log("   原始: " + r.raw, "dim");
      if (r.market_url) log("   🔗 " + r.market_url, "info");
      FWUI.toast.success("F3 下单成功!");
    } catch (e) {
      log("❌ 下单失败: " + e.message, "error");
      FWUI.toast.error(e.message || "下单失败");
    }
  }

  document.getElementById("btn-f3-order").onclick = placeOrder;

  // ========== 平仓 ==========
  function formatCloseResult(res, i) {
    if (typeof res === 'string') return "   [" + (i + 1) + "] " + res;
    var type = res.type || "UNKNOWN";
    var typeIcon = { MARKET: "📈 市价", LIMIT_ORDER: "📋 限价", REDEEM: "💰 赎回", FAILED: "❌ 失败", SKIPPED: "⏭️ 跳过", UNKNOWN: "❓" };
    var icon = typeIcon[type] || type;
    var detail = "";
    if (res.response) detail = res.response;
    else if (res.result) detail = res.result;
    else if (res.reason) detail = res.reason;
    if (res.price) detail += " @ " + res.price;
    if (res.size) detail += " (size=" + res.size + ")";
    return "   [" + (i + 1) + "] " + icon + " " + type + " — " + detail;
  }

  function renderCloseSummary(summary) {
    if (!summary) return "";
    var parts = [];
    if (summary.MARKET) parts.push('<span style="color:#238636">市价:' + summary.MARKET + '</span>');
    if (summary.LIMIT_ORDER) parts.push('<span style="color:#d29922">限价:' + summary.LIMIT_ORDER + '</span>');
    if (summary.REDEEM) parts.push('<span style="color:#3b82f6">赎回:' + summary.REDEEM + '</span>');
    if (summary.FAILED) parts.push('<span style="color:#da3633">失败:' + summary.FAILED + '</span>');
    if (summary.SKIPPED) parts.push('<span style="color:#8b949e">跳过:' + summary.SKIPPED + '</span>');
    return "📊 汇总: " + parts.join(" | ");
  }

  document.getElementById("btn-f3-close").onclick = async function () {
    var slug = getSlug("close-slug-select");
    if (!slug) {
      log("⚠️ 请选择要平仓的市场", "warn");
      return;
    }
    if (!confirm("确认平仓 " + slug + " 的全部持仓？\n\n系统会先检测流动性，市价单无对手盘时自动降级为限价单。")) return;
    log("▶ 平仓: slug=" + slug + "（自动检测流动性+限价单降级）", "info");
    try {
      var r = await FWUI.api.post("/api/polymarket/f3/close", { slug: slug });
      log("✅ 平仓完成, 共 " + (r.count || 0) + " 笔", "success");
      if (r.summary) {
        log(renderCloseSummary(r.summary), r.failed_count > 0 ? "warn" : "success");
      }
      if (r.results) {
        r.results.forEach(function (res, i) {
          var line = formatCloseResult(res, i);
          var isFailed = typeof res === 'object' && res.type === 'FAILED';
          log(line, isFailed ? "error" : "info");
        });
      }
      if (r.failed_count > 0) {
        FWUI.toast.warning("平仓完成，但有 " + r.failed_count + " 笔失败");
      } else {
        FWUI.toast.success("平仓完成");
      }
    } catch (e) {
      log("❌ 平仓失败: " + e.message, "error");
      FWUI.toast.error(e.message || "平仓失败");
    }
  };

  document.getElementById("btn-f3-close-all").onclick = async function () {
    if (!confirm("⚠️ 确认一键全平所有持仓？此操作不可撤销！")) return;
    log("▶ 一键全平...", "warn");
    try {
      var r = await FWUI.api.post("/api/polymarket/f3/close", { slug: null });
      log("✅ 一键全平完成, 共 " + (r.count || 0) + " 笔", "success");
      if (r.summary) {
        log(renderCloseSummary(r.summary), r.failed_count > 0 ? "warn" : "success");
      }
      if (r.results) {
        r.results.forEach(function (res, i) {
          var line = formatCloseResult(res, i);
          var isFailed = typeof res === 'object' && res.type === 'FAILED';
          log(line, isFailed ? "error" : "info");
        });
      }
      if (r.failed_count > 0) {
        FWUI.toast.warning("全平完成，但有 " + r.failed_count + " 笔失败");
      } else {
        FWUI.toast.success("一键全平完成");
      }
    } catch (e) {
      log("❌ 全平失败: " + e.message, "error");
      FWUI.toast.error(e.message || "全平失败");
    }
  };

  // ========== 流动性检测 ==========
  document.getElementById("btn-f3-liquidity").onclick = async function () {
    var slug = getSlug("close-slug-select");
    if (!slug) {
      log("⚠️ 请先选择标的代码", "warn");
      return;
    }
    log("▶ 查询 " + slug + " 的盘口流动性...", "info");
    try {
      var market = await FWUI.api.get("/api/polymarket/f3/market", { slug: slug });
      var tokenIds = [];
      if (market.yes_token_id) tokenIds.push(market.yes_token_id);
      if (market.no_token_id) tokenIds.push(market.no_token_id);
      if (tokenIds.length === 0) {
        log("⚠️ 无法获取 token_id", "warn");
        return;
      }
      var liqResult = await FWUI.api.post("/api/polymarket/f3/liquidity", { token_ids: tokenIds });
      var liqList = liqResult.liquidity || [];
      var html = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">';
      liqList.forEach(function (l) {
        var tokenLabel = l.token_id === (market.yes_token_id || '') ? '🟢 UP (YES)' : '🔴 DOWN (NO)';
        var hasBid = l.has_bid_liquidity;
        var rowColor = hasBid ? '#238636' : '#da3633';
        html += '<div style="border:1px solid #30363d;border-radius:8px;padding:10px;">';
        html += '<div style="font-weight:600;margin-bottom:6px;color:' + rowColor + ';">' + tokenLabel + (hasBid ? ' ✅' : ' ❌ 无流动性') + '</div>';
        html += '<div style="font-size:12px;line-height:1.6;">';
        html += '买一: <b>' + (l.best_bid_price || '-') + '</b>  (' + (l.best_bid_size || '-') + ')</br>';
        html += '卖一: <b>' + (l.best_ask_price || '-') + '</b>  (' + (l.best_ask_size || '-') + ')</br>';
        html += '中间价: ' + (l.midpoint || '-') + ' | 价差: ' + (l.spread || '-') + '</br>';
        html += '最新成交: ' + (l.last_trade_price || '-') + '</br>';
        html += '买盘档数: ' + (l.bids_count || 0) + ' | 卖盘档数: ' + (l.asks_count || 0) + '</br>';
        html += '最小下单: ' + (l.min_order_size || '-') + ' | tick: ' + (l.tick_size || '-');
        if (l.error) html += '<div style="color:#da3633;margin-top:4px;">⚠️ ' + l.error + '</div>';
        html += '</div></div>';
        log('📊 ' + tokenLabel + ': best_bid=' + (l.best_bid_price || '-') + '(' + (l.best_bid_size || '-') + ') best_ask=' + (l.best_ask_price || '-') + '(' + (l.best_ask_size || '-') + ') spread=' + (l.spread || '-') + ' mid=' + (l.midpoint || '-') + (hasBid ? '' : ' ❌ 无买盘！'), hasBid ? 'info' : 'warn');
      });
      html += '</div>';
      var liqEl = document.getElementById("f3-liquidity-result");
      liqEl.style.display = "";
      liqEl.innerHTML = html;
    } catch (e) {
      log("❌ 查询流动性失败: " + e.message, "error");
    }
  };

  // ========== 持仓查询 ==========
  document.getElementById("btn-f3-positions").onclick = async function () {
    var slug = getSlug("positions-slug-select");
    var params = {};
    if (slug) params.slug = slug;
    log("▶ 查询持仓" + (slug ? " slug=" + slug : "（全部）"), "info");
    try {
      var r = await FWUI.api.get("/api/polymarket/f3/positions", params);
      var positions = r.positions || [];
      log("✅ 查询到 " + positions.length + " 个持仓", "success");
      if (positions.length === 0) {
        positionsResultEl.style.display = "";
        positionsResultEl.innerHTML = '<div style="text-align:center;color:var(--fwui-text-muted);padding:24px;">暂无持仓</div>';
      } else {
        positionsResultEl.style.display = "";
        var html = '<table class="pm-positions"><thead><tr><th>市场</th><th>方向</th><th>数量</th><th>当前价</th><th>估值</th></tr></thead><tbody>';
        positions.forEach(function (p) {
          var outcomeTag = p.outcome && p.outcome.toUpperCase() === "YES"
            ? '<span class="pm-tag pm-tag--up">UP</span>'
            : '<span class="pm-tag pm-tag--down">DOWN</span>';
          html += "<tr><td>" + (p.title || "-") + "</td><td>" + outcomeTag + "</td><td>" + (p.size || "-") + "</td><td>" + (p.cur_price || "-") + "</td><td>" + (p.current_value || "-") + "</td></tr>";
        });
        html += "</tbody></table>";
        positionsResultEl.innerHTML = html;
      }
    } catch (e) {
      log("❌ 查询失败: " + e.message, "error");
    }
  };

  // ========== 代码示例 Tab ==========
  document.querySelectorAll("[data-code-tab]").forEach(function (tab) {
    tab.onclick = function () {
      document.querySelectorAll("[data-code-tab]").forEach(function (t) { t.classList.remove("pm-sub-tab--active"); });
      tab.classList.add("pm-sub-tab--active");
      var panels = ["init", "order", "close", "positions", "full"];
      panels.forEach(function (p) {
        var el = document.getElementById("code-panel-" + p);
        if (el) el.style.display = p === tab.dataset.codeTab ? "" : "none";
      });
    };
  });

  // ========== 清空日志 ==========
  document.getElementById("btn-clear-log").onclick = clearLog;

  // ========== 页面加载时自动查询状态 ==========
  setTimeout(function () {
    var token = FWUI.api.getToken();
    if (token) {
      document.getElementById("btn-f3-status").click();
    } else {
      setTabBadge("idle", "未登录");
      log("⚠️ 未登录，请先点击右上角「登录」按钮", "warn");
    }
  }, 800);
})();