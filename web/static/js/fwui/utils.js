// 通用工具：格式化、列表/卡片切换、URL 参数
(function (global) {
  "use strict";

  const utils = {
    // 数字格式化（百分比/金额/比率）
    fmtPercent(v, digits = 2) {
      if (v === null || v === undefined || isNaN(v)) return "-";
      return (v * 100).toFixed(digits) + "%";
    },
    fmtUsd(v, digits = 2) {
      if (v === null || v === undefined || isNaN(v)) return "-";
      return "$" + Number(v).toFixed(digits);
    },
    fmtNumber(v, digits = 2) {
      if (v === null || v === undefined || isNaN(v)) return "-";
      return Number(v).toFixed(digits);
    },
    fmtDate(d) {
      if (!d) return "-";
      return new Date(d).toLocaleString("zh-CN", { hour12: false });
    },

    // 段位判定
    tier(score) {
      if (score >= 80) return { name: "钻石", cls: "diamond" };
      if (score >= 60) return { name: "铂金", cls: "platinum" };
      if (score >= 40) return { name: "黄金", cls: "gold" };
      if (score >= 20) return { name: "白银", cls: "silver" };
      return { name: "青铜", cls: "bronze" };
    },

    // 方向中文
    direction(v) {
      return { 0: "震荡", 1: "看涨", 2: "看跌" }[v] || "-";
    },
    directionClass(v) {
      return { 0: "fwui-flat", 1: "fwui-up", 2: "fwui-down" }[v] || "";
    },

    // URL 参数
    getQuery(name) {
      return new URLSearchParams(location.search).get(name);
    },

    // 防抖
    debounce(fn, wait = 300) {
      let t;
      return function (...args) {
        clearTimeout(t);
        t = setTimeout(() => fn.apply(this, args), wait);
      };
    },
  };

  global.FWUI = global.FWUI || {};
  global.FWUI.utils = utils;
})(window);
