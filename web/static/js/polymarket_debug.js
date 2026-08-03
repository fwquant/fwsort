(function () {
  "use strict";

  var consoleEl = document.getElementById("f3-console");
  var tabBadge = document.getElementById("tab-badge-status");
  var marketInfoEl = document.getElementById("f3-market-info");
  var marketResultEl = document.getElementById("f3-market-result");
  var positionsResultEl = document.getElementById("f3-positions-result");

  var _isInitialized = false;
  var _initStatusVisible = false;
  var _initStatusLoaded = false;
  var _protectedTabs = ["market", "order", "close", "positions"];
  var _logBuffer = [];
  var _logFilter = "all";

  function log(msg, type, panel) {
    type = type || "info";
    panel = panel || "status";
    var clsMap = { info: "log-info", success: "log-success", error: "log-error", warn: "log-warn", dim: "log-dim" };
    var cls = clsMap[type] || "log-info";
    var ts = new Date().toLocaleTimeString();
    var entry = { ts: ts, msg: msg, type: type, panel: panel };
    _logBuffer.push(entry);

    if (_logFilter === "all" || _logFilter === panel) {
      var line = document.createElement("div");
      line.className = cls;
      line.textContent = "[" + ts + "] " + msg;
      if (consoleEl.children.length === 1 && consoleEl.children[0].classList.contains("log-dim")) {
        consoleEl.innerHTML = "";
      }
      consoleEl.appendChild(line);
      consoleEl.scrollTop = consoleEl.scrollHeight;
    }
  }

  function renderLogBuffer() {
    consoleEl.innerHTML = "";
    if (_logBuffer.length === 0) {
      consoleEl.innerHTML = '<span class="log-dim">等待操作...</span>';
      return;
    }
    var clsMap = { info: "log-info", success: "log-success", error: "log-error", warn: "log-warn", dim: "log-dim" };
    _logBuffer.forEach(function (entry) {
      if (_logFilter !== "all" && entry.panel !== _logFilter) return;
      var line = document.createElement("div");
      line.className = clsMap[entry.type] || "log-info";
      line.textContent = "[" + entry.ts + "] " + entry.msg;
      consoleEl.appendChild(line);
    });
    consoleEl.scrollTop = consoleEl.scrollHeight;
  }

  function clearLog() {
    _logBuffer = [];
    consoleEl.innerHTML = '<span class="log-dim">等待操作...</span>';
  }

  // ========== URL Tab 参数支持 ==========
  function getTabFromUrl() {
    var params = new URLSearchParams(location.search);
    return params.get("tab") || "status";
  }
  function setTabInUrl(tab) {
    var url = new URL(location.href);
    url.searchParams.set("tab", tab);
    history.replaceState(null, "", url.toString());
  }

  // ========== Tab 切换 ==========
  function switchTab(target) {
    if (_protectedTabs.indexOf(target) !== -1 && !_isInitialized) {
      FWUI.toast.warn("请先在「连接状态」中初始化连接");
      return;
    }
    document.querySelectorAll(".pm-tab").forEach(function (t) { t.classList.remove("pm-tab--active"); });
    var activeTab = document.querySelector('.pm-tab[data-tab="' + target + '"]');
    if (activeTab) activeTab.classList.add("pm-tab--active");
    document.querySelectorAll(".pm-panel").forEach(function (p) { p.classList.remove("pm-panel--active"); });
    var panel = document.getElementById("panel-" + target);
    if (panel) panel.classList.add("pm-panel--active");
    setTabInUrl(target);
  }

  function updateTabStates() {
    document.querySelectorAll(".pm-tab").forEach(function (tab) {
      var tabName = tab.dataset.tab;
      if (_protectedTabs.indexOf(tabName) !== -1 && !_isInitialized) {
        tab.style.opacity = "0.4";
        tab.style.pointerEvents = "none";
        tab.title = "请先在「连接状态」中初始化连接";
      } else {
        tab.style.opacity = "";
        tab.style.pointerEvents = "";
        tab.title = "";
      }
    });
  }

  document.querySelectorAll(".pm-tab").forEach(function (tab) {
    tab.onclick = function () {
      switchTab(tab.dataset.tab);
    };
  });

  // 🎯 下一步引导卡片点击 → 切换到对应 Tab
  document.querySelectorAll("[data-goto]").forEach(function (card) {
    card.onclick = function (e) {
      e.preventDefault();
      switchTab(card.dataset.goto);
    };
  });

  // 初始化时从 URL 读取 tab
  var initialTab = getTabFromUrl();
  switchTab(initialTab);
  updateTabStates();  // 初始时灰显未初始化 Tab

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

  // ========== 初始化详情渲染 ==========
  var stepIcons = { ok: "✅", error: "❌", warn: "⚠️", idle: "⏳" };

  function renderInitSteps(steps, okCount, totalCount) {
    var container = document.getElementById("f3-init-steps");
    var listEl = document.getElementById("f3-steps-list");
    if (!steps || steps.length === 0) {
      container.style.display = "none";
      return;
    }
    var html = '<div style="font-size:12px;color:var(--fwui-text-muted);margin-bottom:6px;">进度: ' + okCount + '/' + totalCount + ' 步完成</div>';
    steps.forEach(function (s) {
      var icon = stepIcons[s.status] || "⏳";
      html += '<div class="pm-step pm-step--' + s.status + '">';
      html += '<span class="pm-step-icon">' + icon + '</span>';
      html += '<span class="pm-step-name">[步骤' + s.step + '] ' + s.name + '</span>';
      html += '<span class="pm-step-detail">' + s.detail + '</span>';
      html += '</div>';
    });
    listEl.innerHTML = html;
  }

  function renderConfigItems(items) {
    var section = document.getElementById("f3-config-section");
    var grid = document.getElementById("f3-config-grid");
    if (!items || items.length === 0) {
      section.style.display = "none";
      return;
    }
    grid.innerHTML = items.map(function (item) {
      var statusIcon = item.configured ? "✅" : "❌";
      return '<div class="pm-info-item">' +
        '<div class="pm-info-label">' + statusIcon + ' ' + item.label + ' · [' + item.key + ']</div>' +
        '<div class="pm-info-value" style="font-family:monospace;font-size:11px;">' + item.value + '</div>' +
        '</div>';
    }).join("");
  }

  function renderRuntimeConfig(config) {
    var section = document.getElementById("f3-runtime-section");
    var grid = document.getElementById("f3-runtime-grid");
    if (!config) {
      section.style.display = "none";
      return;
    }
    var items = [];
    for (var key in config) {
      if (key === "env_reloaded") continue;
      var val = config[key];
      if (typeof val === "boolean") val = val ? "✅ 是" : "❌ 否";
      items.push({ label: key, value: val });
    }
    if (items.length === 0) {
      section.style.display = "none";
      return;
    }
    grid.innerHTML = items.map(function (item) {
      return '<div class="pm-info-item"><div class="pm-info-label">' + item.label + '</div><div class="pm-info-value">' + item.value + '</div></div>';
    }).join("");
  }

  // ========== 可用方式渲染 ==========
  function renderAvailableModes(modes) {
    var section = document.getElementById("f3-modes-section");
    var listEl = document.getElementById("f3-modes-list");
    if (!modes || modes.length === 0) {
      section.style.display = "none";
      return;
    }
    var modeDescs = {
      "F3": "Relayer Gasless(免Gas)"
    };
    listEl.innerHTML = modes.map(function (m) {
      var enabled = m !== "(无可用方式)";
      var cls = enabled ? "pm-mode-tag pm-mode-tag--ok" : "pm-mode-tag pm-mode-tag--off";
      var desc = modeDescs[m] || "";
      return '<span class="' + cls + '">' + (enabled ? "✅" : "⛔") + ' ' + m + (desc ? ' — ' + desc : '') + '</span>';
    }).join("");
  }

  // ========== 配置指南 HTML 生成（可独立调用） ==========
  function buildF3SetupGuideHTML(configItems) {
    var missing = configItems ? configItems.filter(function (item) { return !item.configured; }) : [];
    var configuredCount = configItems ? configItems.length - missing.length : 0;
    var totalCount = configItems ? configItems.length : 0;

    var html = '<div style="margin-bottom:10px;font-weight:600;">请在 <code>.env</code> 文件中配置以下 Relayer 密钥：</div>';

    html += '<div style="margin:8px 0;padding:12px;background:var(--fwui-bg);border-radius:8px;font-family:monospace;font-size:12px;line-height:1.8;border:1px solid var(--fwui-border);">';
    html += '<div style="color:var(--fwui-text-muted);"># ===== F3 Relayer 配置 =====</div>';
    html += '<div style="color:var(--fwui-text-muted);"># 0x开头42个字符，钱包地址</div>';
    html += '<div style="color:var(--fwui-primary);">POLYMARKET_RELAYER_API_KEY_ADDRESS=your_value_here</div>';
    html += '<div style="color:var(--fwui-text-muted);"># 36个字符(4段短横线分隔)，Relayer API密钥</div>';
    html += '<div style="color:var(--fwui-primary);">POLYMARKET_RELAYER_API_KEY=your_value_here</div>';
    html += '<div style="color:var(--fwui-text-muted);"># 0x开头66个字符，钱包私钥(POLYGON)【重要！代表钱包权限】</div>';
    html += '<div style="color:var(--fwui-primary);">POLYMARKET_RELAYER_PRIVATE_KEY=your_value_here</div>';
    html += '</div>';

    html += '<div style="margin-top:10px;padding:10px;background:rgba(139,92,246,.06);border-radius:8px;font-size:12px;line-height:1.7;">';
    html += '<div style="font-weight:600;margin-bottom:6px;color:var(--fwui-primary);">📌 获取步骤：</div>';

    html += '<div style="margin-bottom:4px;"><strong>[POLYMARKET_RELAYER_API_KEY_ADDRESS] - (签名者地址)</strong>：' +
        '<a href="https://polymarket.com" target="_blank" style="color:var(--fwui-primary);">polymarket.com</a> → 登录 → 右上头像 → 齿轮 → Relayer API密钥 → 新建</div>';

    html += '<div style="margin-bottom:4px;"><strong>[POLYMARKET_RELAYER_API_KEY] - (Relayer API密钥)</strong>：' +
        '<a href="https://polymarket.com" target="_blank" style="color:var(--fwui-primary);">polymarket.com</a> → 登录 → 右上头像 → 齿轮 → Relayer API密钥 → 新建</div>';
    html += '<div style="margin-bottom:4px;"><strong>[POLYMARKET_RELAYER_PRIVATE_KEY]</strong> -（钱包私钥）：</div>';
    html += '<div style="padding-left:12px;color:var(--fwui-text-muted);">';
    html += '<div>• OKX：头像 → 查看私钥 → 输密码 → POLYGON → 完整复制</div>';
    html += '<div>• MetaMask：账户 → 三点 → 账户详情 → 私钥 → 输密码 → POLYGON → 复制</div>';
    html += '</div>';
    html += '</div>';

    html += '<div style="margin-top:10px;padding:8px 12px;background:rgba(239,68,68,.06);border-radius:6px;font-size:11px;color:var(--fwui-danger);">⚠️ [POLYMARKET_RELAYER_PRIVATE_KEY]私钥代表钱包全部权限，切勿泄露！</div>';

    html += '<div style="margin-top:10px;font-size:12px;color:var(--fwui-text-muted);">📝 已配置 ' + configuredCount + '/' + totalCount + ' 项。修改 .env 后刷新页面，再点击「初始化连接」。</div>';

    return html;
  }

  // ========== 配置指南渲染 ==========
  function renderSetupGuide(configItems, forceShow) {
    var guideEl = document.getElementById("f3-setup-guide");
    var bodyEl = document.getElementById("f3-setup-guide-body");
    if (!configItems || configItems.length === 0) {
      if (!forceShow) {
        guideEl.style.display = "none";
        return;
      }
    }

    var missing = configItems ? configItems.filter(function (item) { return !item.configured; }) : [];
    // forceShow 时始终显示，否则僅在有缺失配置時顯示
    if (!forceShow && missing.length === 0) {
      guideEl.style.display = "none";
      return;
    }

    bodyEl.innerHTML = buildF3SetupGuideHTML(configItems);
    guideEl.style.display = "";
  }

  // 供外部调用的快捷函数：在需要时弹出配置指南
  window.showF3SetupGuide = function(configItems) {
    renderSetupGuide(configItems || [], true);
  };

  async function loadInitStatus() {
    try {
      var r = await FWUI.api.get("/api/polymarket/f3/init-status");
      if (!r) return;
      renderInitSteps(r.steps, r.ok_steps, r.total_steps);
      renderConfigItems(r.config_items);
      renderRuntimeConfig(r.runtime_config);
      renderAvailableModes(r.available_modes);
      renderSetupGuide(r.config_items);
      if (r.pm_initialized) {
        setTabBadge("ok", "已连接");
        _isInitialized = true;
      } else {
         if (_isInitialized) {
          setTabBadge("ok", "已连接");
        } else {
          _isInitialized = false;
          var errCount = (r.steps || []).filter(function (s) { return s.status === "error"; }).length;
          if (errCount > 0) {
            setTabBadge("err", "配置错误");
          } else {
            setTabBadge("idle", "未初始化");
          }
        }
      }
      updateTabStates();
      _initStatusLoaded = true;
      log("📋 初始化详情已加载: " + r.ok_steps + "/" + r.total_steps + " 步完成, 可用方式: " + (r.available_modes || []).join(","), "info", "status");
    } catch (e) {
      log("❌ 加载初始化详情失败: " + e.message, "error", "status");
    }
  }

  function setInitStatusVisible(visible) {
    _initStatusVisible = visible;
    var sectionContentMap = {
      "f3-init-steps": "f3-steps-list",
      "f3-config-section": "f3-config-grid",
      "f3-runtime-section": "f3-runtime-grid",
      "f3-modes-section": "f3-modes-list",
      "f3-next-steps": null
    };
    for (var sectionId in sectionContentMap) {
      var el = document.getElementById(sectionId);
      if (!el) continue;
      if (visible) {
        if (sectionId === "f3-next-steps") {
          el.style.display = _isInitialized ? "" : "none";
        } else {
          var contentEl = document.getElementById(sectionContentMap[sectionId]);
          var hasContent = contentEl && contentEl.children.length > 0;
          el.style.display = hasContent ? "" : "none";
        }
      } else {
        el.style.display = "none";
      }
    }
  }

  function getSlug(selectId) {
    var el = document.getElementById(selectId);
    return el ? el.value : "";
  }

  // ========== 初始化 ==========
  document.getElementById("btn-f3-init").onclick = async function () {
    var btn = this;
    var loadingTip = document.getElementById("init-loading-tip");
    var loadingText = document.getElementById("init-loading-text");
    btn.disabled = true;
    btn.textContent = "⏳ 初始化中...";
    btn.style.opacity = "0.6";
    if (loadingTip) loadingTip.style.display = "";
    log("▶ 开始初始化 F3 客户端（首次可能需要20秒~3分钟）...", "info", "status");
    setTabBadge("idle", "初始化中...");
    try {
      var r = await FWUI.api.post("/api/polymarket/f3/init");
      log("✅ F3 客户端初始化成功", "success", "status");
      setTabBadge("ok", "已连接");
      _isInitialized = true;
      updateTabStates();
      if (r && r.market) {
        renderMarketInfo(marketInfoEl, r.market);
        log("📊 市场: " + (r.market.question || r.market.slug || "未知"), "info", "status");
      }
      await loadInitStatus();
      setInitStatusVisible(true);
    } catch (e) {
      log("❌ 初始化失败: " + e.message, "error", "status");
      setTabBadge("err", "失败");
      _isInitialized = false;
      updateTabStates();
      await loadInitStatus();
      setInitStatusVisible(true);
    } finally {
      btn.disabled = false;
      btn.textContent = "🚀 初始化连接";
      btn.style.opacity = "";
      if (loadingTip) loadingTip.style.display = "none";
    }
  };

  // ========== 初始化详情开关 ==========
  var toggleInitDetails = document.getElementById("toggle-init-details");
  if (toggleInitDetails) {
    toggleInitDetails.addEventListener("change", async function () {
      if (toggleInitDetails.checked) {
        if (!_initStatusLoaded) {
          log("▶ 加载初始化详情...", "info", "status");
          await loadInitStatus();
        }
        setInitStatusVisible(true);
        log("📋 显示初始化详情", "info", "status");
      } else {
        setInitStatusVisible(false);
      }
    });
  }

  // ========== 重新检测配置 ==========
  var retryBtn = document.getElementById("btn-f3-setup-guide-retry");
  if (retryBtn) {
    retryBtn.onclick = async function () {
      _initStatusLoaded = false;
      _initStatusVisible = false;
      await loadInitStatus();
      setInitStatusVisible(true);
      log("🔄 已重新检测配置状态", "info", "status");
    };
  }

  // ========== 刷新状态 ==========
  document.getElementById("btn-f3-status").onclick = async function () {
    var btn = this;
    var originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "🔄 刷新中...";
    btn.style.opacity = "0.6";
    log("▶ 查询 F3 状态...", "info", "status");
    try {
      var r = await FWUI.api.get("/api/polymarket/f3/status");
      var stateText = r.initialized ? "已连接" : "未初始化";
      var stateType = r.initialized ? "ok" : "idle";
      setTabBadge(stateType, stateText);
      _isInitialized = !!r.initialized;
      updateTabStates();
      log("✅ 状态: " + stateText +
        " | Relayer Key: " + (r.relayer_key_configured ? "✅" : "❌") +
        " | Relayer Addr: " + (r.relayer_addr_configured ? "✅" : "❌") +
        " | Private Key: " + (r.private_key_configured ? "✅" : "❌"), "success", "status");
      if (r.market && r.market.question) {
        renderMarketInfo(marketInfoEl, r.market);
      }
      FWUI.toast.success("状态已刷新: " + stateText);
      await loadInitStatus();
      setInitStatusVisible(true);
    } catch (e) {
      log("❌ 查询失败: " + e.message, "error", "status");
      FWUI.toast.error("刷新失败: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
      btn.style.opacity = "";
    }
  };

  // ========== 配置指南 ==========
  document.getElementById("btn-f3-setup-guide").onclick = async function () {
    log("📖 显示 F3 配置指南", "info", "status");
    try {
      var r = await FWUI.api.get("/api/polymarket/f3/init-status");
      if (r && r.config_items) {
        window.showF3SetupGuide(r.config_items);
      } else {
        window.showF3SetupGuide([]);
      }
    } catch (e) {
      log("❌ 加载配置项失败: " + e.message, "error", "status");
      window.showF3SetupGuide([]);
    }
  };

  // ========== 市场查询 ==========
  document.getElementById("btn-f3-market").onclick = async function () {
    if (!_isInitialized) {
      FWUI.toast.warn("请先在「连接状态」中初始化连接");
      log("⚠️ 市场查询需要先初始化 F3 连接", "warn", "market");
      return;
    }
    var slug = getSlug("market-slug-select");
    log("▶ 获取市场 slug=" + slug, "info", "market");
    try {
      var r = await FWUI.api.get("/api/polymarket/f3/market", { slug: slug });
      log("✅ 市场查询成功: " + (r.question || r.slug), "success", "market");
      renderMarketInfo(marketResultEl, r);
    } catch (e) {
      log("❌ 查询失败: " + e.message, "error", "market");
    }
  };

  // ========== 下单 ==========
  async function placeOrder() {
    if (!_isInitialized) {
      FWUI.toast.warn("请先在「连接状态」中初始化连接");
      log("⚠️ 下单需要先初始化 F3 连接", "warn", "order");
      return;
    }
    var slug = getSlug("order-slug-select");
    var amount = parseFloat(document.getElementById("order-amount").value) || 1;
    var outcome = document.querySelector('input[name="order-direction"]:checked').value;
    log("▶ F3 下单: slug=" + slug + " outcome=" + outcome + " amount=" + amount, "info", "order");
    try {
      var r = await FWUI.api.post("/api/polymarket/f3/order", { slug: slug, outcome: outcome, amount: amount, side: "BUY" });
      log("✅ 下单成功!", "success", "order");
      if (r.response) log("   订单: " + JSON.stringify(r.response), "success", "order");
      if (r.raw) log("   原始: " + r.raw, "dim", "order");
      if (r.market_url) log("   🔗 " + r.market_url, "info", "order");
      FWUI.toast.success("F3 下单成功!");
    } catch (e) {
      log("❌ 下单失败: " + e.message, "error", "order");
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
    if (!_isInitialized) {
      FWUI.toast.warn("请先在「连接状态」中初始化连接");
      log("⚠️ 平仓需要先初始化 F3 连接", "warn", "close");
      return;
    }
    var slug = getSlug("close-slug-select");
    if (!slug) {
      log("⚠️ 请选择要平仓的市场", "warn", "close");
      return;
    }
    if (!(await FWUI.modal.confirm({ content: "确认平仓 " + slug + " 的全部持仓？\n\n系统会先检测流动性，市价单无对手盘时自动降级为限价单。", type: "danger", okText: "确认平仓" }))) return;
    log("▶ 平仓: slug=" + slug + "（自动检测流动性+限价单降级）", "info", "close");
    try {
      var r = await FWUI.api.post("/api/polymarket/f3/close", { slug: slug });
      log("✅ 平仓完成, 共 " + (r.count || 0) + " 笔", "success", "close");
      if (r.summary) {
        log(renderCloseSummary(r.summary), r.failed_count > 0 ? "warn" : "success", "close");
      }
      if (r.results) {
        r.results.forEach(function (res, i) {
          var line = formatCloseResult(res, i);
          var isFailed = typeof res === 'object' && res.type === 'FAILED';
          log(line, isFailed ? "error" : "info", "close");
        });
      }
      if (r.failed_count > 0) {
        FWUI.toast.warning("平仓完成，但有 " + r.failed_count + " 笔失败");
      } else {
        FWUI.toast.success("平仓完成");
      }
    } catch (e) {
      log("❌ 平仓失败: " + e.message, "error", "close");
      FWUI.toast.error(e.message || "平仓失败");
    }
  };

  document.getElementById("btn-f3-close-all").onclick = async function () {
    if (!_isInitialized) {
      FWUI.toast.warn("请先在「连接状态」中初始化连接");
      log("⚠️ 全平需要先初始化 F3 连接", "warn", "close");
      return;
    }
    if (!(await FWUI.modal.danger({ content: "确认一键全平所有持仓？此操作不可撤销！", okText: "一键全平" }))) return;
    log("▶ 一键全平...", "warn", "close");
    try {
      var r = await FWUI.api.post("/api/polymarket/f3/close", { slug: null });
      log("✅ 一键全平完成, 共 " + (r.count || 0) + " 笔", "success", "close");
      if (r.summary) {
        log(renderCloseSummary(r.summary), r.failed_count > 0 ? "warn" : "success", "close");
      }
      if (r.results) {
        r.results.forEach(function (res, i) {
          var line = formatCloseResult(res, i);
          var isFailed = typeof res === 'object' && res.type === 'FAILED';
          log(line, isFailed ? "error" : "info", "close");
        });
      }
      if (r.failed_count > 0) {
        FWUI.toast.warning("全平完成，但有 " + r.failed_count + " 笔失败");
      } else {
        FWUI.toast.success("一键全平完成");
      }
    } catch (e) {
      log("❌ 全平失败: " + e.message, "error", "close");
      FWUI.toast.error(e.message || "全平失败");
    }
  };

  // ========== 流动性检测 ==========
  document.getElementById("btn-f3-liquidity").onclick = async function () {
    if (!_isInitialized) {
      FWUI.toast.warn("请先在「连接状态」中初始化连接");
      log("⚠️ 流动性检测需要先初始化 F3 连接", "warn", "close");
      return;
    }
    var slug = getSlug("close-slug-select");
    if (!slug) {
      log("⚠️ 请先选择标的代码", "warn", "close");
      return;
    }
    log("▶ 查询 " + slug + " 的盘口流动性...", "info", "close");
    try {
      var market = await FWUI.api.get("/api/polymarket/f3/market", { slug: slug });
      var tokenIds = [];
      if (market.yes_token_id) tokenIds.push(market.yes_token_id);
      if (market.no_token_id) tokenIds.push(market.no_token_id);
      if (tokenIds.length === 0) {
        log("⚠️ 无法获取 token_id", "warn", "close");
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
        log('📊 ' + tokenLabel + ': best_bid=' + (l.best_bid_price || '-') + '(' + (l.best_bid_size || '-') + ') best_ask=' + (l.best_ask_price || '-') + '(' + (l.best_ask_size || '-') + ') spread=' + (l.spread || '-') + ' mid=' + (l.midpoint || '-') + (hasBid ? '' : ' ❌ 无买盘！'), hasBid ? 'info' : 'warn', 'close');
      });
      html += '</div>';
      var liqEl = document.getElementById("f3-liquidity-result");
      liqEl.style.display = "";
      liqEl.innerHTML = html;
    } catch (e) {
      log("❌ 查询流动性失败: " + e.message, "error", "close");
    }
  };

  // ========== 持仓查询 ==========
  document.getElementById("btn-f3-positions").onclick = async function () {
    if (!_isInitialized) {
      FWUI.toast.warn("请先在「连接状态」中初始化连接");
      log("⚠️ 持仓查询需要先初始化 F3 连接", "warn", "positions");
      return;
    }
    var slug = getSlug("positions-slug-select");
    var params = {};
    if (slug) params.slug = slug;
    log("▶ 查询持仓" + (slug ? " slug=" + slug : "（全部）"), "info", "positions");
    try {
      var r = await FWUI.api.get("/api/polymarket/f3/positions", params);
      var positions = r.positions || [];
      log("✅ 查询到 " + positions.length + " 个持仓", "success", "positions");
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
      log("❌ 查询失败: " + e.message, "error", "positions");
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

  // ========== 日志 Tab 筛选按钮 ==========
  document.querySelectorAll("[data-log-filter]").forEach(function (tab) {
    tab.onclick = function () {
      document.querySelectorAll("[data-log-filter]").forEach(function (t) { t.classList.remove("pm-sub-tab--active"); });
      tab.classList.add("pm-sub-tab--active");
      _logFilter = tab.dataset.logFilter;
      renderLogBuffer();
    };
  });

  // ========== 页面加载时自动查询状态 ==========
  setTimeout(function () {
    var token = FWUI.api.getToken();
    if (token) {
      // 已登录：自动加载初始化详情（数据预加载，但默认隐藏，用户点击按钮可展开）
      loadInitStatus();
    } else {
      setTabBadge("idle", "未登录");
      _isInitialized = false;
      updateTabStates();
      log("⚠️ 未登录，请先点击右上角「登录」按钮", "warn", "status");
    }
  }, 800);
})();