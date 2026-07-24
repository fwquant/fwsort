// FWUI Modal 弹框：confirm/info，自定义内容
(function (global) {
  "use strict";

  // 内部：创建弹框
  function open(opts) {
    return new Promise((resolve) => {
      const { title = "提示", content = "", onOk, onCancel, okText = "确定", cancelText = "取消", showCancel = true } = opts || {};

      const mask = document.createElement("div");
      mask.className = "fwui-modal-mask";
      mask.innerHTML = `
        <div class="fwui-modal" role="dialog">
          <div class="fwui-modal__header">
            <span>${escapeHtml(title)}</span>
            <button class="fwui-modal__close" aria-label="close">×</button>
          </div>
          <div class="fwui-modal__body">${typeof content === "string" ? content : ""}</div>
          <div class="fwui-modal__footer">
            ${showCancel ? `<button class="fwui-btn" data-action="cancel">${escapeHtml(cancelText)}</button>` : ""}
            <button class="fwui-btn fwui-btn--primary" data-action="ok">${escapeHtml(okText)}</button>
          </div>
        </div>
      `;
      document.body.appendChild(mask);

      // 如果 content 是 DOM，直接挂载
      if (content instanceof HTMLElement) {
        mask.querySelector(".fwui-modal__body").innerHTML = "";
        mask.querySelector(".fwui-modal__body").appendChild(content);
      }

      function close(result) {
        mask.style.animation = "fwui-mask-in 0.2s ease reverse";
        setTimeout(() => { mask.remove(); resolve(result); }, 180);
      }

      mask.querySelector(".fwui-modal__close").onclick = () => { onCancel && onCancel(); close(false); };
      const cancelBtn = mask.querySelector('[data-action="cancel"]');
      if (cancelBtn) cancelBtn.onclick = () => { onCancel && onCancel(); close(false); };
      mask.querySelector('[data-action="ok"]').onclick = () => { onOk && onOk(); close(true); };
      mask.onclick = (e) => { if (e.target === mask) { onCancel && onCancel(); close(false); } };
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  const Modal = {
    confirm(opts) { return open({ ...opts, showCancel: true }); },
    info(opts)    { return open({ ...opts, showCancel: false, okText: opts.okText || "我知道了" }); },
  };

  global.FWUI = global.FWUI || {};
  global.FWUI.modal = Modal;
})(window);
