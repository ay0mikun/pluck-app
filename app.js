const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
let user = null,
  state = { boards: [], designs: [] },
  selectedBoard = null,
  onBoardCreated = null,
  pendingDelete;
let currentScreen = null,
  galleryFrame,
  toastTimer;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
const mediaURL = (path) => state.media_urls?.[path] || path;
const imageObserver = new IntersectionObserver((entries) => {
  for (const { target: image, isIntersecting } of entries) {
    if (!isIntersecting) continue;
    imageObserver.unobserve(image);
    image.src = mediaURL(image.dataset.src);
  }
}, { rootMargin: "240px" });
let wheelFrame = 0;
let wheelTarget = 0;

function stopWheelScroll() {
  cancelAnimationFrame(wheelFrame);
  wheelFrame = 0;
}

function animateWheelScroll() {
  const remaining = wheelTarget - window.scrollY;
  const done = Math.abs(remaining) < 2;
  window.scrollTo({
    top: done ? wheelTarget : window.scrollY + remaining * 0.28,
    behavior: "instant",
  });
  wheelFrame = done ? 0 : requestAnimationFrame(animateWheelScroll);
}

window.addEventListener("wheel", (event) => {
  if (
    currentScreen !== "workspace" || reducedMotion.matches ||
    !event.cancelable || event.ctrlKey || event.shiftKey ||
    Math.abs(event.deltaX) >= Math.abs(event.deltaY) ||
    event.target.closest("#filters, dialog, [popover]") ||
    document.querySelector("dialog[open]")
  ) {
    stopWheelScroll();
    return;
  }
  const root = document.scrollingElement;
  const maxScroll = root.scrollHeight - root.clientHeight;
  if (maxScroll <= 0) return;
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? root.clientHeight : 1;
  event.preventDefault();
  const start = wheelFrame ? wheelTarget : window.scrollY;
  wheelTarget = Math.min(maxScroll, Math.max(0, start + event.deltaY * unit));
  if (!wheelFrame) wheelFrame = requestAnimationFrame(animateWheelScroll);
}, { passive: false });
window.addEventListener("pointerdown", stopWheelScroll);
window.addEventListener("keydown", stopWheelScroll);
window.addEventListener("resize", stopWheelScroll);
reducedMotion.addEventListener("change", stopWheelScroll);
document.addEventListener(
  "keydown",
  () => (document.documentElement.dataset.keyboard = ""),
  true,
);
document.addEventListener(
  "pointerdown",
  () => delete document.documentElement.dataset.keyboard,
  true,
);
async function api(path, method = "GET", data) {
  const response = await fetch("/api/" + path, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }),
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(result.error || "Something went wrong. Try again.");
  return result;
}
function toast(message) {
  const node = $("#toast");
  node.replaceChildren();
  const icon = document.createElement("img");
  icon.src = "/assets/toast-success.svg";
  icon.width = icon.height = 18;
  icon.alt = "";
  node.append(icon, document.createTextNode(message));
  if (node.matches(":popover-open")) node.hidePopover();
  const dialogs = [...document.querySelectorAll("dialog:modal")];
  (dialogs.at(-1) || document.body).append(node);
  node.showPopover();
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.hidePopover(), 4000);
}
function showScreen(id) {
  document.documentElement.classList.remove("app-starting");
  const previous = currentScreen;
  if (previous !== id) stopWheelScroll();
  document.querySelectorAll(".screen").forEach((s) => (s.hidden = s.id !== id));
  currentScreen = id;
  if (
    previous !== id &&
    !reducedMotion.matches &&
    !("keyboard" in document.documentElement.dataset)
  ) {
    const target =
      id === "auth-screen" ? $(".auth-card") : $(".gallery-content");
    target?.animate(
      [
        { opacity: 0, transform: "translateY(6px)" },
        { opacity: 1, transform: "translateY(0)" },
      ],
      { duration: 240, easing: "cubic-bezier(.23,1,.32,1)" },
    );
  }
  if (id === "workspace") scheduleGalleryLayout();
}
async function route() {
  const hash = location.hash;
  if (new URLSearchParams(hash.slice(1)).has("email-token")) {
    location.reload();
    return;
  }
  if (user) {
    if (hash !== "#library") history.replaceState(null, "", "/#library");
    showScreen("workspace");
    try {
      await refresh();
    } catch (e) {
      toast(e.message);
    }
    return;
  }
  try { localStorage.removeItem("pluck-session-hint"); } catch {}
  history.replaceState(null, "", "/#auth");
  showScreen("auth-screen");
}
window.addEventListener("hashchange", route);
async function refresh() {
  state = await api("state");
  user = state.user;
  try { localStorage.setItem("pluck-session-hint", "1"); } catch {}
  renderAccount();
  if (!state.boards.some((board) => board.id === selectedBoard)) selectedBoard = null;
  render();
}
const touchInput = matchMedia("(pointer: coarse)");
function useTouchFocus() {
  return touchInput.matches && !("keyboard" in document.documentElement.dataset);
}

function openDialog(dialog) {
  stopWheelScroll();
  dialog.classList.remove("closing");
  const touch = useTouchFocus();
  if (touch) dialog.setAttribute("autofocus", "");
  try {
    dialog.showModal();
  } finally {
    if (touch) dialog.removeAttribute("autofocus");
  }
}
function closeDialog(dialog) {
  if (dialog.dataset.busy === "true") return;
  if (reducedMotion.matches || ("keyboard" in document.documentElement.dataset && !dialog.matches(".board-drawer"))) {
    dialog.close();
    return;
  }
  dialog.classList.add("closing");
  setTimeout(() => {
    dialog.close();
    dialog.classList.remove("closing");
  }, 140);
}
for (const dialog of document.querySelectorAll("dialog")) {
  dialog
    .querySelector(".close")
    ?.addEventListener("click", () => closeDialog(dialog));
  dialog.addEventListener("cancel", (e) => {
    e.preventDefault();
    closeDialog(dialog);
  });
  dialog.addEventListener("click", (e) => {
    if (e.target !== dialog) return;
    const r = dialog.getBoundingClientRect();
    if (
      e.clientX < r.left ||
      e.clientX > r.right ||
      e.clientY < r.top ||
      e.clientY > r.bottom
    )
      closeDialog(dialog);
  });
}
async function submitting(form, action) {
  const button = form.querySelector("[type=submit]"),
    status = form.querySelector(".form-status");
  const dialog = form.closest("dialog");
  const label = button.querySelector("span");
  const loader = button.querySelector("img");
  const input = form.elements.name;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  label.textContent = "Creating board";
  loader.hidden = false;
  input.readOnly = true;
  if (dialog) dialog.dataset.busy = "true";
  if (status) status.textContent = "";
  try {
    await action();
    if (status) status.textContent = "";
  } catch (e) {
    if (status) status.textContent = e.message;
    else toast(e.message);
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    label.textContent = "Create board";
    loader.hidden = true;
    input.readOnly = false;
    if (dialog) delete dialog.dataset.busy;
  }
}
const authForm = $("#auth-form");
const authEmail = $("#auth-email");
const authSubmit = authForm.querySelector("button");

function emailError(message) {
  const error = $("#auth-email-error");
  if (message) error.firstElementChild.textContent = message;
  error.toggleAttribute("data-visible", Boolean(message));
  error.setAttribute("aria-hidden", String(!message));
  authEmail.setAttribute("aria-invalid", String(Boolean(message)));
  authSubmit.toggleAttribute("data-muted", Boolean(message) || !authEmail.value.trim());
}

emailError("");
authEmail.oninput = () => {
  emailError("");
  $("#auth-status").textContent = "";
};
authEmail.onblur = () => {
  if (authEmail.value && !authEmail.validity.valid) emailError("Email address is not valid");
};

authForm.onsubmit = async (event) => {
  event.preventDefault();
  if (authSubmit.disabled) return;
  if (!authEmail.validity.valid) {
    emailError(authEmail.value.trim() ? "Email address is not valid" : "Required");
    authEmail.focus();
    return;
  }
  const status = $("#auth-status");
  const label = authSubmit.querySelector("span");
  const loader = authSubmit.querySelector("img");
  authSubmit.disabled = true;
  authEmail.readOnly = true;
  loader.hidden = false;
  label.textContent = "Please wait...";
  status.textContent = "";
  try {
    const result = await api("auth/continue", "POST", { email: authEmail.value });
    if (result.local_preview) {
      const link = document.createElement("a");
      link.href = result.local_preview;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "Open local sign-in link";
      status.replaceChildren(link);
    } else {
      status.textContent = "Check your email for a sign-in link.";
    }
  } catch (error) {
    status.textContent = error.message;
  } finally {
    authSubmit.disabled = false;
    authEmail.readOnly = false;
    loader.hidden = true;
    label.textContent = "Log in";
  }
};
$("#logout").onclick = async () => {
  try {
    await api("auth/logout", "POST", {});
    user = null;
    state = { boards: [], designs: [] };
    selectedBoard = null;
    render();
    if ($("#account-menu").matches(":popover-open")) $("#account-menu").hidePopover();
    if ($("#account-drawer").open) $("#account-drawer").close();
    location.hash = "auth";
  } catch (e) {
    toast(e.message);
  }
};
$("#account-menu").addEventListener("toggle", (e) =>
  $("#account-button").setAttribute("aria-expanded", e.newState === "open"),
);
const accountMenu = $("#account-menu");
const accountDrawer = $("#account-drawer");
const accountButton = $("#account-button");
function renderAccount() {
  const name = user?.display_name || user?.username ||
    (user?.email?.endsWith("@oauth.invalid") ? "Pluck account" : user?.email) || "Pluck account";
  accountButton.title = name;
  accountButton.replaceChildren();
  const fallback = document.createElement("span");
  fallback.className = "account-initial";
  fallback.setAttribute("aria-hidden", "true");
  fallback.textContent = Array.from(name)[0].toUpperCase();
  const avatar = user?.avatar_url;
  if (avatar && (avatar.startsWith("/avatars/") || avatar.startsWith("https://"))) {
    const image = document.createElement("img");
    image.src = mediaURL(avatar);
    image.width = image.height = 34;
    image.alt = "";
    image.onerror = () => image.replaceWith(fallback);
    accountButton.append(image);
  } else accountButton.append(fallback);
}
const mobileAccount = matchMedia("(max-width: 700px)");
let accountContents;

accountButton.addEventListener("click", (event) => {
  if (!mobileAccount.matches) return;
  event.preventDefault();
  if (accountDrawer.open) return;
  if (accountMenu.matches(":popover-open")) accountMenu.hidePopover();
  accountContents = [...accountMenu.children];
  accountDrawer.append(...accountContents);
  accountButton.setAttribute("aria-expanded", "true");
  openDialog(accountDrawer);
});
accountDrawer.addEventListener("close", () => {
  accountMenu.append(...accountContents);
  accountDrawer.querySelector(".board-drawer-close")?.remove();
  accountButton.setAttribute("aria-expanded", "false");
  if (useTouchFocus() && document.activeElement === accountButton) accountButton.blur();
});
mobileAccount.addEventListener("change", () => {
  if (accountDrawer.open) accountDrawer.close();
  if (accountMenu.matches(":popover-open")) accountMenu.hidePopover();
});
const filters = $("#filters");

function render() {
  const boardCounts = new Map();
  for (const design of state.designs) {
    for (const board of design.boards) {
      boardCounts.set(board, (boardCounts.get(board) || 0) + 1);
    }
  }
  const scrollLeft = filters.scrollLeft;
  filters.innerHTML = [{ id: "", name: "All" }, ...state.boards]
    .map(({ id, name }) => {
      const active = id === (selectedBoard || "");
      const count = id ? boardCounts.get(id) || 0 : state.designs.length;
      return `<button class="filter ${active ? "active" : ""}" data-board="${escapeHTML(id)}" aria-pressed="${active}">${escapeHTML(name)} <span class="filter-count">${count}</span></button>`;
    })
    .join("");
  filters.insertAdjacentHTML("beforeend", '<button id="new-board" class="filter add-board" type="button" aria-label="Create new board">+</button>');
  filters.scrollTo({ left: scrollLeft, behavior: "instant" });
  const designs = state.designs.filter(
    (design) => !selectedBoard || design.boards.includes(selectedBoard),
  );
  const isEmpty = designs.length === 0;
  const board = state.boards.find((item) => item.id === selectedBoard);
  $("#edit-board").hidden = !board;
  $("#item-count").innerHTML = !board && isEmpty ? "" : `${designs.length} design${designs.length === 1 ? "" : "s"} saved${board ? ` to <span>${escapeHTML(board.name)}</span>` : ""}`;
  $("#empty-state").hidden = !isEmpty;
  $("#empty-state h1").textContent = board ? "Save a design to your board" : "Add your first design from X";
  $("#empty-state p").textContent = "Paste a tweet link to save your first design.";
  $("#empty-add span").textContent = "Add my first design";
  const grid = $("#design-grid");
  const contentKey = JSON.stringify(designs.map((d) => [d.id, d.thumbnail_url, d.creator_avatar_url]));
  if (grid.dataset.contentKey === contentKey) {
    layoutGallery();
    return;
  }
  imageObserver.disconnect();
  grid.dataset.contentKey = contentKey;
  grid.innerHTML = designs
    .map((d, index) => {
      const ratio = d.width / d.height;
      const displayRatio = Math.max(ratio, 0.4);
      return `<button class="design-card loading" aria-busy="true" data-design="${d.id}" data-ratio="${displayRatio}" aria-label="Open ${escapeHTML(d.title)}"><span class="image-slot" style="--image-ratio:${displayRatio}"><img data-src="${escapeHTML(d.thumbnail_url)}" width="${d.width}" height="${d.height}" alt="${escapeHTML(d.title)}" loading="${index === 0 ? "eager" : "lazy"}" fetchpriority="${index === 0 ? "high" : "auto"}" decoding="async" /></span>${d.creator ? `<span class="creator-badge">${d.creator_avatar_url ? `<img class="creator-avatar" data-src="${escapeHTML(d.creator_avatar_url)}" alt="" width="28" height="28" loading="lazy" />` : `<span class="creator-initial" aria-hidden="true">${escapeHTML(d.creator.replace(/^@/, "")[0]?.toUpperCase() || "")}</span>`}<span class="creator-name">${escapeHTML(d.creator.replace(/^@/, ""))}</span></span>` : ""}${ratio < 0.4 ? '<span class="extreme-label">View full image ↗</span>' : ""}</button>`;
    })
    .join("");
  $("#design-grid")
    .querySelectorAll(".image-slot img")
    .forEach((img) => {
      const loaded = () => {
        img.closest(".design-card").removeAttribute("aria-busy");
        img.closest(".design-card").classList.remove("loading");
        img.closest(".design-card").classList.add("loaded");
      };
      const failed = () => {
        if (img.src !== new URL(img.dataset.src, location.href).href) {
          img.src = img.dataset.src;
          return;
        }
        img.closest(".design-card").removeAttribute("aria-busy");
        img.closest(".design-card").classList.remove("loading");
        const error = document.createElement("span");
        error.className = "image-error";
        error.textContent = "Preview unavailable · open full image";
        img.closest(".image-slot").append(error);
      };
      img.addEventListener("load", loaded, { once: true });
      img.addEventListener("error", failed);
      if (img.getAttribute("src") && img.complete) {
        if (img.naturalWidth) loaded();
        else failed();
      }
    });
  $("#design-grid")
    .querySelectorAll(".creator-avatar")
    .forEach((img) =>
      img.addEventListener(
        "error",
        () => {
          const fallback = document.createElement("span");
          fallback.className = "creator-initial";
          fallback.setAttribute("aria-hidden", "true");
          fallback.textContent = img.parentElement.textContent
            .trim()
            .charAt(0)
            .toUpperCase();
          img.replaceWith(fallback);
        },
        { once: true },
      ),
    );
  layoutGallery();
  grid.querySelectorAll("img[data-src]").forEach((image) => imageObserver.observe(image));
}
// Compute positions from stored ratios, not image load events. Each next card
// occupies the shortest column; image decoding can only change opacity.
function scheduleGalleryLayout() {
  cancelAnimationFrame(galleryFrame);
  galleryFrame = requestAnimationFrame(layoutGallery);
}

function layoutGallery() {
  const grid = $("#design-grid"),
    width = grid.clientWidth;
  if (!width) return;
  const style = getComputedStyle(grid),
    columns = Number(style.getPropertyValue("--columns")),
    gap = parseFloat(style.getPropertyValue("--gap"));
  const cardWidth = (width - gap * (columns - 1)) / columns,
    heights = Array(columns).fill(0);
  [...grid.children].forEach((card) => {
    const column = heights.indexOf(Math.min(...heights));
    const height = (cardWidth - 2) / Number(card.dataset.ratio) + 2;
    card.style.width = `${cardWidth}px`;
    card.style.transform = `translate(${column * (cardWidth + gap)}px,${heights[column]}px)`;
    heights[column] += height + gap;
  });
  grid.style.height = `${Math.max(0, ...heights) - (grid.children.length ? gap : 0)}px`;
}
let lastWidth = 0;
new ResizeObserver((entries) => {
  const width = entries[0].contentRect.width;
  if (width !== lastWidth) {
    lastWidth = width;
    scheduleGalleryLayout();
  }
}).observe($("#design-grid"));
$("#filters").onclick = (e) => {
  if (e.target.closest("#new-board")) {
    openBoard();
    return;
  }
  const button = e.target.closest("[data-board]");
  if (!button) return;
  const boardId = button.dataset.board;
  selectedBoard = boardId || null;
  render();
  if (!useTouchFocus()) {
    $("#filters [aria-pressed='true']")?.focus({ preventScroll: true });
  }
  if ($(".gallery-content").getBoundingClientRect().top < $("#filters").getBoundingClientRect().bottom) {
    $(".gallery-content").scrollIntoView({ block: "start", behavior: "instant" });
  }
};
const importDialog = $("#import-dialog");
const importForm = $("#import-form");
const importUrl = $("#tweet-url");
const importButton = importForm.querySelector("[type=submit]");
const importStatus = $("#import-status");
const importError = $("#import-error");
const importPicker = $("#import-board-picker");
let importBoard = null;
let importDrawer = null;
function validPostLink(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && ["x.com", "www.x.com", "twitter.com", "www.twitter.com"].includes(url.hostname) && /^\/[A-Za-z0-9_]+\/status\/\d+\/?$/.test(url.pathname);
  } catch { return false; }
}
function setImportState(error = "") {
  const busy = importDialog.dataset.busy === "true";
  const board = state.boards.find((item) => item.id === importBoard);
  const valid = validPostLink(importUrl.value.trim());
  importUrl.setAttribute("aria-invalid", String(Boolean(error)));
  importUrl.readOnly = busy;
  importUrl.nextElementSibling.hidden = !error;
  importError.hidden = !error;
  importError.textContent = error;
  importButton.disabled = busy || !valid || !board;
  importButton.toggleAttribute("data-muted", !valid || !board);
  importButton.setAttribute("aria-busy", String(busy));
  importButton.querySelector("img").hidden = !busy;
  importButton.querySelector(".button-label").textContent = error ? "Enter a valid link" : busy ? "Saving designs" : "Save designs";
  $("#import-board-name").textContent = board?.name || "Select board";
  importPicker.querySelector("summary").setAttribute("aria-disabled", String(busy));
  $("#import-board-error").hidden = true;
}
function renderImportBoards() {
  const panel = importDrawer?.querySelector(".board-picker-panel") || importPicker.querySelector(".board-picker-panel");
  panel.querySelector(".board-options").innerHTML = state.boards.map(({id, name}) => `<label><input type="radio" name="import-board" value="${escapeHTML(id)}"${id === importBoard ? " checked" : ""} /><span>${escapeHTML(name)}</span></label>`).join("");
  panel.querySelector(".board-options").hidden = !state.boards.length;
}
function closeImportPicker() {
  importPicker.open = false;
  if (importDrawer?.open) closeDialog(importDrawer);
  if (importPicker.contains(document.activeElement)) importDialog.focus({ preventScroll: true });
}
importPicker.addEventListener("change", ({ target }) => {
  if (!target.matches("input")) return;
  importBoard = target.value;
  closeImportPicker();
  setImportState();
});
importPicker.querySelector("summary").onclick = (event) => {
  if (importDialog.dataset.busy === "true") { event.preventDefault(); return; }
  if (!matchMedia("(max-width: 700px)").matches) return;
  event.preventDefault();
  if (importDrawer?.open) return;
  const panel = importPicker.querySelector(".board-picker-panel");
  const drawer = document.createElement("dialog");
  importDrawer = drawer;
  drawer.className = "board-drawer import-board-drawer";
  drawer.setAttribute("aria-label", "Save to a board");
  drawer.append(panel);
  importDialog.append(drawer);
  drawer.addEventListener("change", ({ target }) => {
    if (!target.matches("input")) return;
    importBoard = target.value;
    closeDialog(drawer);
    setImportState();
  });
  drawer.addEventListener("cancel", (event) => { event.preventDefault(); closeDialog(drawer); });
  drawer.addEventListener("click", (event) => {
    if (event.target === drawer) closeDialog(drawer);
  });
  drawer.addEventListener("close", () => {
    importPicker.append(panel);
    drawer.remove();
    if (importDrawer === drawer) importDrawer = null;
    requestAnimationFrame(() => { if (importDialog.open && !document.querySelector("#board-dialog[open]")) importDialog.focus({preventScroll:true}); });
  }, {once:true});
  openDialog(drawer);
};
importPicker.querySelector(".board-create").onclick = () => {
  closeImportPicker();
  openBoard(async ({id}) => {
    importBoard = id;
    renderImportBoards();
    setImportState();
    importDialog.focus({preventScroll:true});
  });
};
function openImportDialog() {
  importForm.reset();
  importBoard = selectedBoard;
  importStatus.textContent = "";
  renderImportBoards();
  setImportState();
  openDialog(importDialog);
}
$("#paste-link").onclick = $("#empty-add").onclick = openImportDialog;
importUrl.oninput = () => { importStatus.textContent = ""; setImportState(); };
importUrl.onblur = () => {
  if (importUrl.value.trim() && !validPostLink(importUrl.value.trim())) setImportState("Invalid link");
};
importForm.onsubmit = async (event) => {
  event.preventDefault();
  if (importDialog.dataset.busy === "true") return;
  const url = importUrl.value.trim();
  if (!validPostLink(url)) { setImportState(url ? "Invalid link" : "Link is missing!"); return; }
  if (!state.boards.some((board) => board.id === importBoard)) {
    $("#import-board-error").hidden = false;
    importButton.querySelector(".button-label").textContent = "Select a board";
    return;
  }
  closeImportPicker();
  importDialog.dataset.busy = "true";
  setImportState();
  try {
    const result = await api("import", "POST", {url, board_id: importBoard});
    selectedBoard = importBoard;
    if (result.designs) {
      const updated = new Map(result.designs.map((design) => [design.id, design]));
      const added = result.designs.filter((design) => !state.designs.some((item) => item.id === design.id));
      state.designs = [...added, ...state.designs.map((design) => updated.get(design.id) || design)];
      state.media_urls = {...state.media_urls, ...result.media_urls};
      render();
    } else await refresh();
    importDialog.close();
    importForm.reset();
    toast(result.existing ? "Designs saved to board." : `${result.ids.length} design${result.ids.length === 1 ? "" : "s"} saved.`);
  } catch (error) {
    importStatus.textContent = error.message;
  } finally { delete importDialog.dataset.busy; setImportState(); }
};
const boardName = $("#board-name");
const boardForm = $("#board-form");
const boardSubmit = boardForm.querySelector("[type=submit]");
// Product rule: keep 15 characters even when Figma examples show another limit.
const boardNameLimit = 15;

function updateBoardName(showRequired = false) {
  const length = Array.from(boardName.value).length;
  const message = length > boardNameLimit
    ? `Name should not be more than ${boardNameLimit} letters`
    : showRequired && !boardName.value.trim() ? "Required" : "";
  $("#board-name-count").textContent = `${length}/${boardNameLimit}`;
  const error = $("#board-name-error");
  error.textContent = message;
  error.hidden = !message;
  boardName.setAttribute("aria-invalid", String(Boolean(message)));
  boardSubmit.disabled = Boolean(message) || !boardName.value.trim();
  return !boardSubmit.disabled;
}

boardName.oninput = () => updateBoardName();

function openBoard(onCreated = null) {
  onBoardCreated = onCreated;
  boardName.value = "";
  updateBoardName();
  $("#board-dialog .form-status").textContent = "";
  openDialog($("#board-dialog"));
}
boardForm.onsubmit = (e) => {
  e.preventDefault();
  if ($("#board-dialog").dataset.busy === "true") return;
  if (!updateBoardName(true)) {
    boardName.focus();
    return;
  }
  const form = e.currentTarget;
  submitting(form, async () => {
    const result = await api("boards", "POST", { name: form.elements.name.value });
    const onCreated = onBoardCreated;
    if (!onCreated) selectedBoard = result.id;
    await refresh();
    $("#board-dialog").close();
    onCreated?.(result);
    if (!onCreated) toast("Board created.");
  }).finally(() => updateBoardName());
};
const editDialog = $("#edit-board-dialog");
const editForm = $("#edit-board-form");
const editName = $("#edit-board-name");
let editingBoard = null;
function updateEditBoard() {
  const length = Array.from(editName.value).length;
  const message = length > boardNameLimit ? `Name should not be more than ${boardNameLimit} letters` : "";
  $("#edit-board-count").textContent = `${length}/${boardNameLimit}`;
  $("#edit-board-error").textContent = message;
  $("#edit-board-error").hidden = !message;
  editName.setAttribute("aria-invalid", String(Boolean(message)));
  editForm.querySelector("[type=submit]").disabled = !editingBoard || Boolean(message) || !editName.value.trim() || editName.value.trim() === editingBoard.name || editDialog.dataset.busy === "true";
}
$("#edit-board").onclick = () => {
  editingBoard = state.boards.find((board) => board.id === selectedBoard);
  if (!editingBoard) return;
  editName.value = editingBoard.name;
  editForm.querySelector(".form-status").textContent = "";
  updateEditBoard();
  openDialog(editDialog);
};
editName.oninput = updateEditBoard;
$("#cancel-edit-board").onclick = () => closeDialog(editDialog);
editForm.onsubmit = async (event) => {
  event.preventDefault();
  updateEditBoard();
  const button = editForm.querySelector("[type=submit]");
  if (button.disabled) return;
  const board = editingBoard;
  editDialog.dataset.busy = "true";
  button.disabled = true;
  button.querySelector("img").hidden = false;
  button.querySelector("span").textContent = "Saving";
  editName.readOnly = true;
  try {
    await api(`boards/${board.id}`, "PATCH", {name: editName.value.trim()});
    await refresh();
    editDialog.close();
    toast("Board updated.");
  } catch (error) { editForm.querySelector(".form-status").textContent = error.message; }
  finally {
    delete editDialog.dataset.busy;
    button.querySelector("img").hidden = true;
    button.querySelector("span").textContent = "Save";
    editName.readOnly = false;
    updateEditBoard();
  }
};
$("#delete-board").onclick = () => {
  if (!editingBoard || editDialog.dataset.busy === "true") return;
  const boardId = editingBoard.id;
  confirmDelete("This board and its saved designs will be permanently deleted. This can’t be undone.", async () => {
    await api(`boards/${boardId}`, "DELETE", {});
    selectedBoard = null;
    editDialog.close();
  }, "Delete board?", "Keep board");
};
function confirmDelete(description, action, title = "Delete image?", cancel = "Cancel") {
  $("#confirm-title").textContent = title;
  $("#cancel-delete").textContent = cancel;
  pendingDelete = action;
  $("#confirm-description").textContent = description;
  $("#confirm-dialog .form-status").textContent = "";
  openDialog($("#confirm-dialog"));
}
$("#cancel-delete").onclick = () => closeDialog($("#confirm-dialog"));
$("#confirm-delete").onclick = async () => {
  const button = $("#confirm-delete");
  if (button.disabled) return;
  const confirmation = $("#confirm-dialog");
  button.disabled = true;
  confirmation.dataset.busy = "true";
  try {
    await pendingDelete();
    $("#confirm-dialog").close();
    await refresh();
    toast("Deleted.");
  } catch (e) {
    $("#confirm-dialog .form-status").textContent = e.message;
  } finally {
    button.disabled = false;
    delete confirmation.dataset.busy;
  }
};
$("#design-grid").onclick = (event) => {
  const card = event.target.closest("[data-design]");
  if (!card) return;
  const ids = [...$("#design-grid").querySelectorAll("[data-design]")].map(
    (item) => item.dataset.design,
  );
  openDetail(ids, ids.indexOf(card.dataset.design));
};

function openDetail(ids, index, focusTarget = "close-preview") {
  const id = ids[index];
  const design = state.designs.find((item) => item.id === id);
  const dialog = $("#detail-dialog");
  const creator = (design.creator || "").replace(/^@/, "");
  const icon = (name) =>
    `<img src="/assets/preview-${name}.svg" width="16" height="16" alt="" />`;

  $("#detail-content").innerHTML = `
    <div class="detail-layout">
      <div class="detail-toolbar">
        <button id="close-preview" class="preview-control preview-icon" type="button" aria-label="Close preview">${icon("close")}</button>
        ${design.source ? `<a class="preview-control source-post" href="${escapeHTML(design.source)}" target="_blank" rel="noopener noreferrer" aria-label="View post"><img src="/assets/workspace-x.svg" alt="" />View post</a>` : ""}
      </div>
      <div class="detail-stage">
        <div class="detail-image-slot loading" style="--image-ratio:${design.width / design.height}">
          <img class="detail-image" src="${escapeHTML(mediaURL(design.thumbnail_url))}" width="${design.width}" height="${design.height}" alt="${escapeHTML(design.title)}" decoding="async" fetchpriority="high" />
        </div>
        <nav class="detail-navigation" aria-label="Browse designs">
          <button id="previous-design" class="preview-control" type="button"${index === 0 ? " disabled" : ""}>${icon("previous")} Previous</button>
          <button id="next-design" class="preview-control" type="button"${index === ids.length - 1 ? " disabled" : ""}>Next ${icon("next")}</button>
        </nav>
      </div>
      <aside class="detail-info" aria-label="Design information">
        <div class="detail-metadata">
          <h2 id="detail-title" title="${escapeHTML(design.title)}">${escapeHTML(design.title)}</h2>
          ${creator ? `<div class="detail-author">${design.creator_avatar_url
            ? `<img src="${escapeHTML(mediaURL(design.creator_avatar_url))}" width="28" height="28" alt="" />`
            : `<span class="creator-initial" aria-hidden="true">${escapeHTML(creator[0].toUpperCase())}</span>`}
            <span>${escapeHTML(creator)}</span></div>` : ""}
          ${design.notes ? `<p class="detail-notes">${escapeHTML(design.notes)}</p>` : ""}
          <div class="detail-boards" aria-label="Saved to boards"></div>
        </div>
        <div class="detail-actions">
          <details class="board-picker">
            <summary class="preview-control"><span></span>${icon("chevron")}</summary>
            <div class="board-picker-panel">
              <h3>Select board</h3>
              <div class="board-options" role="group" aria-label="Select board"></div>
              <div class="board-picker-footer"><button type="button" class="board-create"><img src="/assets/board-add.svg" width="16" height="16" alt="" />Create new board</button></div>
            </div>
          </details>
          <button id="remove-design" class="preview-control preview-icon" type="button" aria-label="Delete design">${icon("delete")}</button>
          <button id="copy-design" class="preview-control preview-icon" type="button" aria-label="Copy image">${icon("copy")}</button>
        </div>
      </aside>
    </div>`;

  const previewImage = $(".detail-image");
  const previewSlot = $(".detail-image-slot");
  const revealPreview = () => previewSlot.classList.remove("loading");
  previewImage.addEventListener("load", revealPreview, { once: true });
  if (previewImage.complete && previewImage.naturalWidth) revealPreview();
  const sharpPreview = new Image();
  sharpPreview.decoding = "async";
  sharpPreview.fetchPriority = "high";
  sharpPreview.onload = () => {
    if (previewImage.isConnected) {
      previewImage.src = sharpPreview.src;
      revealPreview();
    }
  };
  sharpPreview.onerror = () => {
    const local = `/previews/${encodeURIComponent(design.id)}.webp`;
    if (sharpPreview.src !== new URL(local, location.href).href) {
      sharpPreview.src = local;
    } else if (previewImage.isConnected) previewImage.src = design.url;
  };
  sharpPreview.src = mediaURL(`/previews/${encodeURIComponent(design.id)}.webp`);

  const picker = dialog.querySelector(".board-picker");
  const options = picker.querySelector(".board-options");
  const summary = picker.querySelector("summary");
  const panel = picker.querySelector(".board-picker-panel");
  const pickerHeading = panel.querySelector("h3");
  let activeDrawer;
  let pickerMotion;
  let pickerExpanded = false;

  function setPickerOpen(expanded) {
    if (expanded === pickerExpanded) return;
    pickerExpanded = expanded;
    picker.toggleAttribute("data-expanded", expanded);
    panel.inert = !expanded;
    if (reducedMotion.matches) {
      pickerMotion?.cancel();
      pickerMotion = null;
      picker.open = expanded;
      return;
    }
    if (expanded) picker.open = true;
    if (pickerMotion) {
      pickerMotion.reverse();
      return;
    }
    pickerMotion = panel.animate([
      { opacity: 0, transform: "translateY(-3px)" },
      { opacity: 1, transform: "none" },
    ], { duration: 160, easing: "ease-out", fill: "both" });
    pickerMotion.onfinish = () => {
      if (!pickerExpanded) {
        picker.open = false;
        pickerMotion.cancel();
        pickerMotion = null;
      }
    };
  }

  summary.addEventListener("click", (event) => {
    event.preventDefault();
    if (!matchMedia("(max-width: 999px)").matches) {
      setPickerOpen(!pickerExpanded);
      return;
    }
    pickerMotion?.cancel();
    pickerMotion = null;
    pickerExpanded = false;
    picker.open = false;
    picker.removeAttribute("data-expanded");
    panel.inert = false;
    const drawer = document.createElement("dialog");
    activeDrawer = drawer;
    drawer.className = "board-drawer";
    drawer.setAttribute("aria-labelledby", "board-drawer-title");
    pickerHeading.id = "board-drawer-title";
    pickerHeading.textContent = "Select board";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "board-drawer-close";
    close.setAttribute("aria-label", "Close board picker");
    close.innerHTML = '<img src="/assets/import-close.svg" alt="" />';
    close.onclick = () => closeDialog(drawer);
    drawer.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeDialog(drawer);
    });
    drawer.append(panel, close);
    dialog.append(drawer);
    drawer.onclick = (event) => {
      if (event.target === drawer) {
        const bounds = drawer.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right ||
            event.clientY < bounds.top || event.clientY > bounds.bottom) {
          closeDialog(drawer);
        }
      }
    };
    drawer.addEventListener("close", () => {
      if (activeDrawer === drawer) activeDrawer = null;
      if (document.activeElement === summary) dialog.focus({ preventScroll: true });
      pickerHeading.removeAttribute("id");
      pickerHeading.textContent = "Select board";
      picker.append(panel);
      drawer.remove();
    }, { once: true });
    openDialog(drawer);
  });
  let saving = false;

  function renderBoards() {
    options.innerHTML = state.boards.map(({ id, name }) =>
      `<label><input type="checkbox" value="${escapeHTML(id)}"${design.boards.includes(id) ? " checked" : ""} /><span>${escapeHTML(name)}</span></label>`
    ).join("");
    options.hidden = !state.boards.length;
    $(".detail-boards").innerHTML = state.boards
      .filter((board) => design.boards.includes(board.id))
      .map(({ name }) => `<span>${escapeHTML(name)}</span>`).join("");
    picker.classList.toggle("unassigned", !design.boards.length);
    summary.firstElementChild.textContent = design.boards.length
      ? "Change board"
      : "Save to board";
  }

  async function saveBoards(boards, boardId) {
    saving = true;
    const controls = dialog.querySelectorAll(".board-options input, .board-create, .detail-navigation button:not(:disabled), #remove-design");
    controls.forEach((control) => { control.disabled = true; });
    try {
      await api(`designs/${id}`, "PATCH", { boards });
      design.boards = boards;
      const current = state.designs.find((item) => item.id === id);
      if (current) current.boards = boards;
      render();
      setPickerOpen(false);
      if (activeDrawer?.open) {
        activeDrawer.addEventListener("close", () => toast("Boards updated."), { once: true });
        closeDialog(activeDrawer);
      } else {
        toast("Boards updated.");
      }
      if (document.activeElement === summary || panel.contains(document.activeElement)) {
        dialog.focus({ preventScroll: true });
      }
    } catch (error) {
      toast(error.message);
    } finally {
      controls.forEach((control) => { control.disabled = false; });
      saving = false;
      if (dialog.open && picker.isConnected) {
        const restoreFocus = picker.contains(document.activeElement) || document.activeElement === dialog;
        renderBoards();
        if (pickerExpanded && restoreFocus && !useTouchFocus()) {
          [...options.querySelectorAll("input")].find((input) => input.value === boardId)?.focus();
        }
      }
    }
  }

  renderBoards();
  options.onchange = ({ target }) => {
    const boards = new Set(design.boards);
    if (target.checked) boards.add(target.value);
    else boards.delete(target.value);
    void saveBoards([...boards], target.value);
  };
  panel.querySelector(".board-create").onclick = () => openBoard(({ id: boardId }) => {
    renderBoards();
    void saveBoards([...design.boards, boardId], boardId);
  });

  function navigate(step, button) {
    if (!saving && ids[index + step]) openDetail(ids, index + step, button);
  }
  dialog.onkeydown = (event) => {
    if (event.key === "Escape" && pickerExpanded) {
      event.preventDefault();
      setPickerOpen(false);
      summary.focus();
    } else if (event.target.closest(".detail-stage, #close-preview") && !event.altKey && !event.metaKey && !event.ctrlKey) {
      if (event.key === "ArrowLeft") navigate(-1, "previous-design");
      if (event.key === "ArrowRight") navigate(1, "next-design");
    }
  };
  dialog.onclick = (event) => {
    if (!picker.contains(event.target)) setPickerOpen(false);
    if (event.target.classList.contains("detail-stage")) closeDialog(dialog);
  };
  $("#close-preview").onclick = () => closeDialog(dialog);
  $("#previous-design").onclick = () => navigate(-1, "previous-design");
  $("#next-design").onclick = () => navigate(1, "next-design");
  $("#copy-design").onclick = async () => {
    try {
      if (!navigator.clipboard?.write || typeof ClipboardItem === "undefined") {
        throw new Error("Image clipboard is unavailable.");
      }
      const png = fetch(design.url).then((response) => {
        if (!response.ok) throw new Error("Could not download the original image.");
        return response.blob();
      });
      await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
      toast("Image copied.");
    } catch {
      toast("Could not copy the image.");
    }
  };
  $("#remove-design").onclick = () =>
    confirmDelete(
      "This design will be removed from your library and every board.",
      async () => {
        await api(`designs/${id}`, "DELETE", {});
        dialog.close();
      },
    );

  if (!dialog.open) openDialog(dialog);
  if (useTouchFocus()) return;
  const focus = document.getElementById(focusTarget);
  (focus.disabled ? $("#close-preview") : focus).focus();
}
async function init() {
  let returning = false;
  try { returning = localStorage.getItem("pluck-session-hint") === "1"; } catch {}
  showScreen(returning ? "workspace" : "auth-screen");
  const emailToken = new URLSearchParams(location.hash.slice(1)).get("email-token");
  if (emailToken) {
    history.replaceState(null, "", "/#auth");
    showScreen("auth-screen");
    $("#auth-status").textContent = "Signing in…";
    try {
      user = (await api("auth/verify", "POST", { token: emailToken })).user;
      $("#auth-status").textContent = "";
      location.hash = "library";
    } catch (error) {
      $("#auth-status").textContent = error.message;
    }
    return;
  }
  try {
    user = (await api("auth/me")).user;
    await route();
    const error = new URLSearchParams(location.search).get("auth_error");
    if (error) {
      $("#auth-status").textContent = error;
      history.replaceState(null, "", "/#auth");
    }
  } catch {
    showScreen("auth-screen");
    $("#auth-status").textContent = "Could not connect to Pluck. Please try again shortly.";
  }
}
init();
