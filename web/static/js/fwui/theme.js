// FWUI 主题管理：暗紫/明亮切换，状态本地存储
(function (global) {
  "use strict";

  const STORAGE_KEY = "fwsort.theme";
  const DEFAULT_THEME = "dark"; // 锁定区第14条：暗紫默认

  // 主题管理器
  const ThemeManager = {
    /** 获取当前主题 */
    current() {
      return localStorage.getItem(STORAGE_KEY) || DEFAULT_THEME;
    },

    /** 应用主题到 <html> */
    apply(theme) {
      theme = theme || this.current();
      document.documentElement.setAttribute("data-theme", theme);
      localStorage.setItem(STORAGE_KEY, theme);
      // 通知监听者（自定义事件）
      document.dispatchEvent(new CustomEvent("fwui:theme-change", { detail: { theme } }));
      return theme;
    },

    /** 切换主题 */
    toggle() {
      const next = this.current() === "dark" ? "light" : "dark";
      return this.apply(next);
    },

    /** 监听主题变化 */
    onChange(handler) {
      document.addEventListener("fwui:theme-change", (e) => handler(e.detail.theme));
    },
  };

  // 页面加载时立即应用（避免主题闪烁）
  ThemeManager.apply();

  // 导出
  global.FWUI = global.FWUI || {};
  global.FWUI.theme = ThemeManager;
})(window);
