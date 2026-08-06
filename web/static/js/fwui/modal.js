// FWUI Modal 弹框：统一主题风格（玻璃质感 + 类型图标）
// 支持 alert / confirm / info / prompt 四种弹框，替代原生 alert/confirm/prompt
(function (global) {
  "use strict";

  // 类型 → 图标映射
  const TYPE_ICONS = {
    info: "ℹ️",
    success: "✅",
    warning: "⚠️",
    danger: "🔴",
  };

  const TYPE_TITLE = {
    info: "提示",
    success: "成功",
    warning: "警告",
    danger: "确认操作",
  };

  // 核心：创建弹框
  function open(opts) {
    return new Promise((resolve) => {
      const {
        title,
        content = "",
        type = "info",
        onOk,
        onCancel,
        okText,
        cancelText = "取消",
        showCancel = true,
        inputFields = null, // prompt 专用：[{name,label,value,placeholder}]
      } = opts || {};

      const resolvedTitle = title || TYPE_TITLE[type] || "提示";
      const resolvedOkText = okText || (type === "danger" ? "确认删除" : type === "warning" ? "确认" : "确定");
      const icon = TYPE_ICONS[type] || TYPE_ICONS.info;

      const modalClass = type ? `fwui-modal fwui-modal--${type}` : "fwui-modal";

      let bodyHtml = "";
      const contentHtml = typeof content === "string" && content
        ? `<div class="fwui-modal__content">${content}</div>`
        : "";
      if (inputFields && inputFields.length) {
        const fieldsHtml = inputFields.map((f) => `
          <div class="fwui-form-row">
            ${f.label ? `<label>${escapeHtml(f.label)}</label>` : ""}
            <input class="fwui-input" name="${escapeHtml(f.name)}" value="${escapeHtml(f.value || "")}" placeholder="${escapeHtml(f.placeholder || "")}" ${f.required ? "required" : ""} />
          </div>
        `).join("");
        bodyHtml = contentHtml + fieldsHtml;
      } else if (contentHtml) {
        bodyHtml = contentHtml;
      }

      const mask = document.createElement("div");
      mask.className = "fwui-modal-mask";
      mask.innerHTML = `
        <div class="${modalClass}" role="dialog" aria-modal="true">
          <div class="fwui-modal__header">
            <div class="fwui-modal__header-text">
              <span class="fwui-modal__icon fwui-modal__icon--${type}">${icon}</span>
              <span>${escapeHtml(resolvedTitle)}</span>
            </div>
            <button class="fwui-modal__close" aria-label="close">×</button>
          </div>
          <div class="fwui-modal__body">${bodyHtml}</div>
          <div class="fwui-modal__footer">
            ${showCancel ? `<button class="fwui-btn" data-action="cancel">${escapeHtml(cancelText)}</button>` : ""}
            <button class="fwui-btn fwui-btn--primary" data-action="ok">${escapeHtml(resolvedOkText)}</button>
          </div>
        </div>
      `;
      document.body.appendChild(mask);

      // 如果 content 是 DOM，直接挂载到 body
      if (content instanceof HTMLElement) {
        const bodyEl = mask.querySelector(".fwui-modal__body");
        bodyEl.innerHTML = "";
        bodyEl.appendChild(content);
      }

      // 聚焦第一个 input
      if (inputFields && inputFields.length) {
        const firstInput = mask.querySelector(".fwui-modal__body input");
        if (firstInput) setTimeout(() => firstInput.focus(), 50);
      }

      function close(result) {
        mask.style.animation = "none";
        mask.style.opacity = "0";
        mask.style.transition = "opacity 0.18s ease";
        document.removeEventListener("keydown", escHandler);
        document.removeEventListener("keydown", enterHandler);
        setTimeout(() => {
          if (mask.parentNode) mask.remove();
          resolve(result);
        }, 160);
      }

      function getInputValues() {
        if (!inputFields || !inputFields.length) return null;
        const form = mask.querySelector(".fwui-modal__body");
        const result = {};
        let valid = true;
        inputFields.forEach((f) => {
          const el = form.querySelector(`[name="${f.name}"]`);
          result[f.name] = el ? el.value : (f.value || "");
          if (f.required && el && !el.value) valid = false;
        });
        return valid ? result : null;
      }

      mask.querySelector(".fwui-modal__close").onclick = () => { onCancel && onCancel(); close(false); };
      const cancelBtn = mask.querySelector('[data-action="cancel"]');
      if (cancelBtn) cancelBtn.onclick = () => { onCancel && onCancel(); close(false); };

      mask.querySelector('[data-action="ok"]').onclick = () => {
        let inputVals = null;
        if (inputFields && inputFields.length) {
          inputVals = getInputValues();
          if (!inputVals) {
            const firstInvalid = mask.querySelector(".fwui-modal__body input:invalid");
            if (firstInvalid) firstInvalid.focus();
            return;
          }
        }
        const payload = inputVals || true;
        const ret = onOk ? onOk(payload) : undefined;
        if (ret && typeof ret.catch === "function") {
          ret.then((ok) => { if (ok !== false) close(payload); }).catch(() => { /* onOk rejected, keep modal open */ });
        } else {
          close(payload);
        }
      };

      mask.onclick = (e) => { if (e.target === mask) { onCancel && onCancel(); close(false); } };

      // ESC 关闭
      const escHandler = (e) => {
        if (e.key === "Escape") {
          onCancel && onCancel();
          close(false);
        }
      };
      document.addEventListener("keydown", escHandler);

      // Enter 提交（焦点在模态框内输入框时）
      const enterHandler = (e) => {
        if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
          const active = document.activeElement;
          if (active && active.tagName === "INPUT" && active.closest(".fwui-modal")) {
            // 检查输入框类型：textarea 允许换行，普通 input 回车提交
            if (active.type !== "textarea") {
              e.preventDefault();
              const okBtn = mask.querySelector('[data-action="ok"]');
              if (okBtn) okBtn.click();
            }
          }
        }
      };
      document.addEventListener("keydown", enterHandler);
    });
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  const Modal = {
    // 确认弹框（替代原生 confirm）
    confirm(opts) {
      if (typeof opts === "string") opts = { content: opts };
      return open({ type: "warning", showCancel: true, ...opts });
    },
    // 信息弹框（仅一个确定按钮）
    info(opts) {
      if (typeof opts === "string") opts = { content: opts };
      return open({ type: "info", showCancel: false, okText: "我知道了", ...opts });
    },
    // 警告弹框
    warning(opts) {
      if (typeof opts === "string") opts = { content: opts };
      return open({ type: "warning", showCancel: true, ...opts });
    },
    // 危险操作弹框（红色按钮）
    danger(opts) {
      if (typeof opts === "string") opts = { content: opts };
      return open({ type: "danger", showCancel: true, okText: opts.okText || "确认删除", ...opts });
    },
    // 成功弹框
    success(opts) {
      if (typeof opts === "string") opts = { content: opts };
      return open({ type: "success", showCancel: false, okText: "好的", ...opts });
    },
    // 输入弹框（替代原生 prompt）
    prompt(opts) {
      if (typeof opts === "string") opts = { content: opts };
      const fields = opts.inputFields || [{
        name: "value",
        label: opts.label || "请输入",
        value: opts.value || "",
        placeholder: opts.placeholder || "",
        required: opts.required || false,
      }];
      return open({
        type: "info",
        showCancel: true,
        okText: opts.okText || "确定",
        ...opts,
        inputFields: fields,
      });
    },
    // 底层 open（高级用法）
    open,
  };

  global.FWUI = global.FWUI || {};
  global.FWUI.modal = Modal;
})(window);