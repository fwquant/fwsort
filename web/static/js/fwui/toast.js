// FWUI Toast 提示框：成功/错误/警告/信息，自动消失
(function (global) {
  "use strict";

  // 容器（懒加载）
  function ensureContainer() {
    let c = document.querySelector(".fwui-toast-container");
    if (!c) {
      c = document.createElement("div");
      c.className = "fwui-toast-container";
      document.body.appendChild(c);
    }
    return c;
  }

  // 内部：显示一条 toast
  function show(message, type = "info", duration = 3000) {
    const container = ensureContainer();
    const el = document.createElement("div");
    el.className = `fwui-toast fwui-toast--${type}`;
    el.textContent = message;
    container.appendChild(el);

    setTimeout(() => {
      el.classList.add("fwui-toast--leaving");
      setTimeout(() => el.remove(), 250);
    }, duration);
  }

  const Toast = {
    success(msg, dur) { show(msg, "success", dur); },
    error(msg, dur)   { show(msg, "error",   dur || 4000); },
    warning(msg, dur) { show(msg, "warning", dur); },
    info(msg, dur)    { show(msg, "info",    dur); },
  };

  global.FWUI = global.FWUI || {};
  global.FWUI.toast = Toast;
})(window);
