// ==========================================================================
// TOAST SYSTEM — Cockpit (inspire de groundmaster)
// Remplace alert, confirm, prompt par des toasts modernes
// ==========================================================================

var TOAST_ICONS = { success: "check_circle", warning: "warning", error: "error", info: "info" };

function normalizeToastType(t) {
  return ["success", "warning", "error", "info"].includes(t) ? t : "info";
}

// --------------------------------------------------------------------------
// showToast — notification simple auto-dismiss
// --------------------------------------------------------------------------
function showToast(type, message, duration) {
  type = normalizeToastType(type);
  if (duration === undefined) duration = 3500;

  var container = document.getElementById("toast-container");
  if (!container) return null;

  var toast = document.createElement("div");
  toast.className = "toast toast-" + type;
  toast.setAttribute("role", "status");

  var icon = document.createElement("span");
  icon.className = "material-symbols-outlined toast-icon";
  icon.textContent = TOAST_ICONS[type];

  var text = document.createElement("span");
  text.className = "toast-text";
  text.textContent = message;

  toast.appendChild(icon);
  toast.appendChild(text);
  container.appendChild(toast);

  // Animate in
  requestAnimationFrame(function () {
    requestAnimationFrame(function () { toast.classList.add("show"); });
  });

  // Click to dismiss
  toast.addEventListener("click", function () { dismissToast(toast); });

  // Auto dismiss
  if (duration > 0) {
    setTimeout(function () { dismissToast(toast); }, duration);
  }

  return toast;
}

function dismissToast(toast) {
  if (!toast || toast._dismissed) return;
  toast._dismissed = true;
  toast.classList.remove("show");
  toast.classList.add("hide");
  setTimeout(function () { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 350);
}

// --------------------------------------------------------------------------
// showConfirmToast — remplace window.confirm, retourne Promise<boolean>
// --------------------------------------------------------------------------
function showConfirmToast(message, options) {
  options = options || {};
  var type = normalizeToastType(options.type || "warning");
  var okLabel = options.okLabel || "Oui";
  var cancelLabel = options.cancelLabel || "Non";

  return new Promise(function (resolve) {
    var container = document.getElementById("toast-container");
    if (!container) { resolve(false); return; }

    var toast = document.createElement("div");
    toast.className = "toast toast-confirm toast-" + type;
    toast.setAttribute("role", "alertdialog");

    var icon = document.createElement("span");
    icon.className = "material-symbols-outlined toast-icon";
    icon.textContent = TOAST_ICONS[type];

    var body = document.createElement("div");
    body.className = "toast-body";

    var text = document.createElement("span");
    text.className = "toast-text";
    text.textContent = message;

    var actions = document.createElement("div");
    actions.className = "toast-actions";

    var btnCancel = document.createElement("button");
    btnCancel.className = "toast-btn toast-btn-secondary";
    btnCancel.textContent = cancelLabel;

    var btnOk = document.createElement("button");
    btnOk.className = "toast-btn toast-btn-primary";
    btnOk.textContent = okLabel;

    actions.appendChild(btnCancel);
    actions.appendChild(btnOk);
    body.appendChild(text);
    body.appendChild(actions);
    toast.appendChild(icon);
    toast.appendChild(body);
    container.appendChild(toast);

    requestAnimationFrame(function () {
      requestAnimationFrame(function () { toast.classList.add("show"); });
    });

    function cleanup(result) {
      dismissToast(toast);
      resolve(result);
    }

    btnOk.addEventListener("click", function () { cleanup(true); });
    btnCancel.addEventListener("click", function () { cleanup(false); });
  });
}

// --------------------------------------------------------------------------
// showPromptToast — remplace window.prompt, retourne Promise<string|null>
// --------------------------------------------------------------------------
function showPromptToast(message, options) {
  options = options || {};
  var type = normalizeToastType(options.type || "info");
  var okLabel = options.okLabel || "OK";
  var cancelLabel = options.cancelLabel || "Annuler";
  var defaultValue = options.defaultValue || "";
  var inputType = options.inputType || "text";

  return new Promise(function (resolve) {
    var container = document.getElementById("toast-container");
    if (!container) { resolve(null); return; }

    var toast = document.createElement("div");
    toast.className = "toast toast-input toast-" + type;
    toast.setAttribute("role", "alertdialog");

    var icon = document.createElement("span");
    icon.className = "material-symbols-outlined toast-icon";
    icon.textContent = TOAST_ICONS[type];

    var body = document.createElement("div");
    body.className = "toast-body";

    var text = document.createElement("span");
    text.className = "toast-text";
    text.textContent = message;

    var inputRow = document.createElement("div");
    inputRow.className = "toast-input-field";

    var input = document.createElement("input");
    input.className = "toast-input-control";
    input.type = inputType;
    input.value = defaultValue;

    inputRow.appendChild(input);

    var actions = document.createElement("div");
    actions.className = "toast-actions";

    var btnCancel = document.createElement("button");
    btnCancel.className = "toast-btn toast-btn-secondary";
    btnCancel.textContent = cancelLabel;

    var btnOk = document.createElement("button");
    btnOk.className = "toast-btn toast-btn-primary";
    btnOk.textContent = okLabel;

    actions.appendChild(btnCancel);
    actions.appendChild(btnOk);
    body.appendChild(text);
    body.appendChild(inputRow);
    body.appendChild(actions);
    toast.appendChild(icon);
    toast.appendChild(body);
    container.appendChild(toast);

    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        toast.classList.add("show");
        input.focus();
        input.select();
      });
    });

    function cleanup(result) {
      dismissToast(toast);
      resolve(result);
    }

    btnOk.addEventListener("click", function () { cleanup(input.value); });
    btnCancel.addEventListener("click", function () { cleanup(null); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); cleanup(input.value); }
      if (e.key === "Escape") { e.preventDefault(); cleanup(null); }
    });
  });
}

// --------------------------------------------------------------------------
// showTripleChoiceToast — choix 3 voies, retourne Promise<'save'|'discard'|'cancel'>
// --------------------------------------------------------------------------
function showTripleChoiceToast(message, options) {
  options = options || {};
  var type = normalizeToastType(options.type || "warning");
  var saveLabel = options.saveLabel || "Sauvegarder";
  var discardLabel = options.discardLabel || "Ne pas sauvegarder";
  var cancelLabel = options.cancelLabel || "Annuler";

  return new Promise(function (resolve) {
    var container = document.getElementById("toast-container");
    if (!container) { resolve("cancel"); return; }

    var toast = document.createElement("div");
    toast.className = "toast toast-confirm toast-" + type;
    toast.setAttribute("role", "alertdialog");

    var icon = document.createElement("span");
    icon.className = "material-symbols-outlined toast-icon";
    icon.textContent = TOAST_ICONS[type];

    var body = document.createElement("div");
    body.className = "toast-body";

    var text = document.createElement("span");
    text.className = "toast-text";
    text.textContent = message;

    var actions = document.createElement("div");
    actions.className = "toast-actions";

    var btnCancel = document.createElement("button");
    btnCancel.className = "toast-btn toast-btn-secondary";
    btnCancel.textContent = cancelLabel;

    var btnDiscard = document.createElement("button");
    btnDiscard.className = "toast-btn";
    btnDiscard.textContent = discardLabel;

    var btnSave = document.createElement("button");
    btnSave.className = "toast-btn toast-btn-primary";
    btnSave.textContent = saveLabel;

    actions.appendChild(btnCancel);
    actions.appendChild(btnDiscard);
    actions.appendChild(btnSave);
    body.appendChild(text);
    body.appendChild(actions);
    toast.appendChild(icon);
    toast.appendChild(body);
    container.appendChild(toast);

    requestAnimationFrame(function () {
      requestAnimationFrame(function () { toast.classList.add("show"); });
    });

    function cleanup(result) {
      dismissToast(toast);
      resolve(result);
    }

    btnSave.addEventListener("click", function () { cleanup("save"); });
    btnDiscard.addEventListener("click", function () { cleanup("discard"); });
    btnCancel.addEventListener("click", function () { cleanup("cancel"); });
  });
}
