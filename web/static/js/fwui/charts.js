// FWUI 图表模块：ECharts 封装（按需懒加载 ECharts CDN）
// 用法：FWUI.chart.line('el-id', { x, y, title })
(function (global) {
  "use strict";

  let echartsInstance = null;
  let loadingPromise = null;

  // 懒加载 ECharts（CDN）
  function loadEcharts() {
    if (echartsInstance) return Promise.resolve(echartsInstance);
    if (loadingPromise) return loadingPromise;

    loadingPromise = new Promise((resolve, reject) => {
      // 已存在则直接用
      if (global.echarts) {
        echartsInstance = global.echarts;
        return resolve(echartsInstance);
      }
      const script = document.createElement("script");
      script.src = "/static/js/lib/echarts.min.js";
      script.onload = () => {
        echartsInstance = global.echarts;
        if (!echartsInstance) reject(new Error("echarts not loaded"));
        else resolve(echartsInstance);
      };
      script.onerror = () => reject(new Error("echarts cdn load failed"));
      document.head.appendChild(script);
    });
    return loadingPromise;
  }

  // 工具：读取主题色
  function getThemeColors() {
    const isDark = document.documentElement.getAttribute("data-theme") !== "light";
    return {
      bg: isDark ? "#1a1325" : "#ffffff",
      text: isDark ? "#e8def8" : "#1a1325",
      textMuted: isDark ? "#7a6a91" : "#8a7da6",
      grid: isDark ? "#3d2e57" : "#e5dff0",
      up: isDark ? "#4ade80" : "#16a34a",
      down: isDark ? "#f87171" : "#dc2626",
      primary: isDark ? "#9b6dff" : "#6d3aff",
      success: isDark ? "#4ade80" : "#16a34a",
      danger: isDark ? "#f87171" : "#dc2626",
      warning: isDark ? "#fbbf24" : "#d97706",
    };
  }

  // 通用初始化
  async function init(elId) {
    const echarts = await loadEcharts();
    const el = document.getElementById(elId);
    if (!el) throw new Error("element not found: " + elId);
    if (el._chart) echarts.dispose(el._chart);
    el._chart = echarts.init(el);
    // 主题切换时重绘
    document.addEventListener("fwui:theme-change", () => el._chart && el._chart.resize());
    window.addEventListener("resize", () => el._chart && el._chart.resize());
    return el._chart;
  }

  // 折线图（净值/回撤等）
  async function line(elId, opt) {
    const chart = await init(elId);
    const c = getThemeColors();
    chart.setOption({
      backgroundColor: "transparent",
      title: opt.title ? { text: opt.title, textStyle: { color: c.text, fontSize: 14 } } : undefined,
      tooltip: { trigger: "axis", backgroundColor: c.bg, textStyle: { color: c.text } },
      grid: { left: 50, right: 20, top: opt.title ? 40 : 20, bottom: 30 },
      xAxis: { type: "category", data: opt.x, axisLine: { lineStyle: { color: c.grid } }, axisLabel: { color: c.textMuted } },
      yAxis: { type: "value", axisLine: { lineStyle: { color: c.grid } }, splitLine: { lineStyle: { color: c.grid } }, axisLabel: { color: c.textMuted } },
      series: (opt.series || [{ name: opt.name || "value", data: opt.y, type: "line", smooth: true, lineStyle: { color: c.primary }, areaStyle: { color: c.primary + "30" } }]).map((s) => ({
        type: "line", smooth: true, lineStyle: { color: s.color || c.primary }, areaStyle: { color: (s.color || c.primary) + "30" }, ...s,
      })),
    });
    return chart;
  }

  // 柱状图
  async function bar(elId, opt) {
    const chart = await init(elId);
    const c = getThemeColors();
    chart.setOption({
      backgroundColor: "transparent",
      title: opt.title ? { text: opt.title, textStyle: { color: c.text, fontSize: 14 } } : undefined,
      tooltip: { trigger: "axis", backgroundColor: c.bg, textStyle: { color: c.text } },
      grid: { left: 50, right: 20, top: opt.title ? 40 : 20, bottom: 30 },
      xAxis: { type: "category", data: opt.x, axisLine: { lineStyle: { color: c.grid } }, axisLabel: { color: c.textMuted } },
      yAxis: { type: "value", axisLine: { lineStyle: { color: c.grid } }, splitLine: { lineStyle: { color: c.grid } }, axisLabel: { color: c.textMuted } },
      series: [{ data: opt.y, type: "bar", itemStyle: { color: c.primary, borderRadius: [4, 4, 0, 0] } }],
    });
    return chart;
  }

  // 饼图（投票分布/段位分布）
  async function pie(elId, opt) {
    const chart = await init(elId);
    const c = getThemeColors();
    chart.setOption({
      backgroundColor: "transparent",
      title: opt.title ? { text: opt.title, textStyle: { color: c.text, fontSize: 14 }, left: "center" } : undefined,
      tooltip: { trigger: "item", backgroundColor: c.bg, textStyle: { color: c.text } },
      legend: { bottom: 0, textStyle: { color: c.textMuted } },
      series: [{
        type: "pie", radius: ["40%", "70%"], center: ["50%", opt.title ? "55%" : "50%"],
        data: opt.data,
        label: { color: c.text },
        itemStyle: { borderColor: c.bg, borderWidth: 2 },
      }],
    });
    return chart;
  }

  // 雷达图（多维评估）
  async function radar(elId, opt) {
    const chart = await init(elId);
    const c = getThemeColors();
    chart.setOption({
      backgroundColor: "transparent",
      title: opt.title ? { text: opt.title, textStyle: { color: c.text, fontSize: 14 } } : undefined,
      tooltip: { backgroundColor: c.bg, textStyle: { color: c.text } },
      radar: {
        indicator: opt.indicator,
        axisName: { color: c.textMuted },
        splitLine: { lineStyle: { color: c.grid } },
        splitArea: { areaStyle: { color: [c.bg, c.bg] } },
      },
      series: [{ type: "radar", data: opt.data, areaStyle: { color: c.primary + "40" }, lineStyle: { color: c.primary } }],
    });
    return chart;
  }

  global.FWUI = global.FWUI || {};
  global.FWUI.chart = { line, bar, pie, radar, loadEcharts };
})(window);