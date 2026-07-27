// 策略详情页：基础信息、投票下单、订单执行日志
(function () {
  "use strict";

  const uid = FWUI.utils.getQuery("uid");
  const accountId = FWUI.utils.getQuery("account_id");

  async function init() {
    if (!uid) { FWUI.toast.error("缺少 uid 参数"); return; }
    await loadDetail();
    await loadExecutionLogs();
    await loadCharts();
    bindVoting();
  }

  async function loadDetail() {
    try {
      const data = await FWUI.api.rankingDetail(uid);
      renderDetail(data);
    } catch (e) {
      FWUI.toast.error("加载详情失败: " + e.message);
    }
  }

  function renderDetail(d) {
    const t = FWUI.utils.tier(d.composite_score || 0);
    document.getElementById("detail-title").textContent = d.name || d.uid;
    document.getElementById("detail-meta").innerHTML = `
      <span class="fwui-tag">${escapeHtml(d.platform || "-")}</span>
      <span class="fwui-tier fwui-tier--${t.cls}">${t.name}</span>
    `;
    document.getElementById("detail-stats").innerHTML = `
      <div class="fwui-stat-card"><div class="fwui-stat-card__label">综合分</div><div class="fwui-stat-card__value fwui-stat-card__value--primary">${FWUI.utils.fmtNumber(d.composite_score, 2)}</div></div>
      <div class="fwui-stat-card"><div class="fwui-stat-card__label">年化收益</div><div class="fwui-stat-card__value ${(d.annualized_return||0)>=0?'fwui-up':'fwui-down'}">${FWUI.utils.fmtPercent(d.annualized_return, 2)}</div></div>
      <div class="fwui-stat-card"><div class="fwui-stat-card__label">最大回撤</div><div class="fwui-stat-card__value fwui-down">${FWUI.utils.fmtPercent(d.max_drawdown, 2)}</div></div>
      <div class="fwui-stat-card"><div class="fwui-stat-card__label">夏普</div><div class="fwui-stat-card__value">${FWUI.utils.fmtNumber(d.sharpe_ratio, 2)}</div></div>
      <div class="fwui-stat-card"><div class="fwui-stat-card__label">胜率</div><div class="fwui-stat-card__value">${FWUI.utils.fmtPercent(d.win_rate, 1)}</div></div>
      <div class="fwui-stat-card"><div class="fwui-stat-card__label">执行质量</div><div class="fwui-stat-card__value">${FWUI.utils.fmtNumber(d.execution_score, 2)}</div></div>
    `;
  }

  async function loadExecutionLogs() {
    const container = document.getElementById("execution-logs");
    if (!container) return;
    try {
      const data = await FWUI.api.executionLogs(uid, 20);
      if (!data.logs || data.logs.length === 0) {
        container.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:16px;color:var(--fwui-text-muted);">暂无执行记录</td></tr>`;
        return;
      }
      container.innerHTML = data.logs.map((l) => `
        <tr>
          <td>${escapeHtml(l.order_id || "-")}</td>
          <td>${escapeHtml(l.platform || "-")}</td>
          <td>${FWUI.utils.direction(l.side)}</td>
          <td>${FWUI.utils.fmtUsd(l.amount_usd, 2)}</td>
          <td><span class="fwui-tag fwui-tag--${l.status === 3 ? 'success' : l.status === 5 ? 'danger' : 'warning'}">${statusName(l.status)}</span></td>
          <td>${FWUI.utils.fmtDate(l.created_at)}</td>
        </tr>
      `).join("");
    } catch (e) {
      container.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--fwui-danger);">${e.message}</td></tr>`;
    }
  }

  function statusName(s) {
    return { 1: "已提交", 2: "部分成交", 3: "已成交", 4: "已撤销", 5: "失败" }[s] || "-";
  }

  // ========== ECharts 图表：净值 / 回撤 / 投票分布 / 执行质量 ==========
  async function loadCharts() {
    const emptyIds = ["chart-equity", "chart-drawdown", "chart-vote-pie", "chart-exec"];
    try {
      // 拉取执行日志作为数据源
      const data = await FWUI.api.executionLogs(uid, 100);
      const logs = (data.logs || []).slice().reverse();

      if (logs.length === 0) {
        emptyIds.forEach((id) => {
          const el = document.getElementById(id);
          if (el) el.innerHTML = `<div class="fwui-chart-empty">📊 暂无执行数据</div>`;
        });
        return;
      }

      // 1) 净值曲线：用每笔 pnl 累加
      const equity = computeEquitySeries(logs, 1000);
      const x = equity.map((_, i) => `#${i + 1}`);
      FWUI.chart.line("chart-equity", {
        title: "账户净值（$）",
        x,
        y: equity,
        name: "净值",
      });

      // 2) 回撤曲线：相对最高点的回落
      const dd = computeDrawdownSeries(equity);
      FWUI.chart.line("chart-drawdown", {
        title: "回撤（%）",
        x,
        y: dd.map((v) => +(v * 100).toFixed(2)),
        name: "回撤",
      });

      // 3) 投票方向分布饼图（看 logs 的 side）
      const sideCount = { 1: 0, 2: 0 };
      logs.forEach((l) => { if (l.side === 1) sideCount[1]++; else if (l.side === 2) sideCount[2]++; });
      FWUI.chart.pie("chart-vote-pie", {
        title: "方向占比",
        data: [
          { name: "看涨", value: sideCount[1] },
          { name: "看跌", value: sideCount[2] },
        ],
      });

      // 4) 执行质量分（按日聚合 slippage 反向作为质量）
      const dailyExec = computeDailyExecution(logs, 14);
      FWUI.chart.bar("chart-exec", {
        title: "执行质量（近 14 天）",
        x: dailyExec.map((d) => d.date),
        y: dailyExec.map((d) => +(d.score * 100).toFixed(1)),
      });
    } catch (e) {
      console.warn("charts load failed:", e);
      emptyIds.forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = `<div class="fwui-chart-empty">📊 暂无执行数据</div>`;
      });
    }
  }

  // 累加 pnl 生成净值序列
  function computeEquitySeries(logs, init) {
    if (!logs.length) return [init];
    const out = [init];
    logs.forEach((l) => { out.push(out[out.length - 1] + (Number(l.pnl) || 0)); });
    return out;
  }

  // 回撤序列（百分比）
  function computeDrawdownSeries(equity) {
    let peak = equity[0];
    return equity.map((v) => {
      peak = Math.max(peak, v);
      return peak > 0 ? (peak - v) / peak : 0;
    });
  }

  // 按日聚合执行质量（1 - 归一化 slippage）
  function computeDailyExecution(logs, days) {
    const map = {};
    logs.forEach((l) => {
      const d = (l.created_at || "").slice(0, 10);
      if (!d) return;
      if (!map[d]) map[d] = { slip: 0, n: 0, latency: 0 };
      map[d].slip += Math.abs(Number(l.slippage) || 0);
      map[d].n += 1;
      map[d].latency += Number(l.latency_ms) || 0;
    });
    const arr = Object.entries(map).map(([date, v]) => ({
      date: date.slice(5),
      score: Math.max(0, Math.min(1, 1 - v.slip / v.n * 50)),
    }));
    return arr.slice(-days);
  }

  function bindVoting() {
    const btn = document.getElementById("btn-trigger-vote");
    if (!btn) return;
    btn.onclick = async () => {
      const symbol = document.getElementById("symbol-input")?.value || "BTCUSDT";
      const tf = document.getElementById("tf-input")?.value || "15m";
      if (!accountId) {
        FWUI.toast.warning("缺少 account_id 参数，无法下单（仅查看）");
        return;
      }
      btn.disabled = true;
      btn.textContent = "投票中...";
      try {
        const data = await FWUI.api.predictAndVote(accountId, { symbol, timeframe: tf });
        renderVoteResult(data);
        FWUI.toast.success("投票完成");
        await loadExecutionLogs();
      } catch (e) {
        FWUI.toast.error("投票失败: " + e.message);
      } finally {
        btn.disabled = false;
        btn.textContent = "触发一次预测+投票";
      }
    };
  }

  function renderVoteResult(d) {
    const el = document.getElementById("vote-result");
    if (!el) return;
    const dirClass = FWUI.utils.directionClass(d.final_direction);
    el.innerHTML = `
      <div class="fwui-card" style="margin-top:12px;">
        <div style="display:flex;gap:24px;flex-wrap:wrap;align-items:center;">
          <div><div style="font-size:12px;color:var(--fwui-text-muted);">最终方向</div><div class="${dirClass}" style="font-size:24px;font-weight:700;">${FWUI.utils.direction(d.final_direction)}</div></div>
          <div><div style="font-size:12px;color:var(--fwui-text-muted);">下单金额</div><div style="font-size:24px;font-weight:700;">${FWUI.utils.fmtUsd(d.order_amount_usd, 2)}</div></div>
          <div><div style="font-size:12px;color:var(--fwui-text-muted);">原因</div><div><span class="fwui-tag">${escapeHtml(d.reason)}</span></div></div>
          <div><div style="font-size:12px;color:var(--fwui-text-muted);">订单ID</div><div style="font-family:monospace;">${escapeHtml(d.order_id || "-")}</div></div>
        </div>
        <div style="margin-top:16px;">
          <div style="font-size:12px;color:var(--fwui-text-muted);margin-bottom:6px;">3 智能体预测详情</div>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;">
            ${(d.predictions || []).map((p) => `
              <div class="fwui-card" style="padding:8px;">
                <div style="font-size:13px;font-weight:600;">${escapeHtml(p.agent_name)} <span style="color:var(--fwui-text-muted);font-weight:400;">${escapeHtml(p.agent_model)}</span></div>
                <div class="${FWUI.utils.directionClass(p.direction)}" style="font-size:18px;font-weight:700;margin:4px 0;">${FWUI.utils.direction(p.direction)} · ${FWUI.utils.fmtPercent(p.confidence, 1)}</div>
                <div style="font-size:12px;color:var(--fwui-text-muted);">${escapeHtml(p.reasoning || "")}</div>
                <div style="font-size:11px;color:var(--fwui-text-muted);margin-top:4px;">延迟 ${p.latency_ms}ms</div>
              </div>
            `).join("")}
          </div>
        </div>
      </div>
    `;
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
