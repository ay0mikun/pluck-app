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
  pendingImport = false,
  editingBoard = false,
  pendingDelete;
let currentScreen = null,
  galleryFrame,
  toastTimer;
let landingAnimations = [];
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
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
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.hidden = true), 4000);
}
function showScreen(id, animate = true) {
  const previous = currentScreen;
  if (previous !== id) {
    landingAnimations.forEach((animation) => animation.cancel());
    landingAnimations = [];
  }
  document.querySelectorAll(".screen").forEach((s) => (s.hidden = s.id !== id));
  currentScreen = id;
  if (
    previous !== id &&
    animate &&
    !reducedMotion.matches &&
    !("keyboard" in document.documentElement.dataset)
  ) {
    if (id === "landing") {
      // A small stagger gives the heading, supporting copy, and CTA a clear order.
      // Backwards fill reserves the delayed state without keeping a transform
      // after completion, so the button's press feedback stays responsive.
      landingAnimations = [...$(".hero").children].map((element, index) =>
        element.animate(
          [
            { opacity: 0, transform: "translateY(8px)" },
            { opacity: 1, transform: "translateY(0)" },
          ],
          {
            duration: 280,
            delay: index * 60,
            easing: "cubic-bezier(.23,1,.32,1)",
            fill: "backwards",
          },
        ),
      );
    } else {
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
  }
  if (id === "workspace") scheduleGalleryLayout();
}
async function route() {
  const hash = location.hash;
  if (hash === "#auth") return showScreen("auth-screen");
  if (hash.startsWith("#library")) {
    if (!user) {
      location.hash = "auth";
      return;
    }
    try {
      await refresh();
      showScreen("workspace");
    } catch (e) {
      toast(e.message);
    }
    return;
  }
  showScreen("landing");
}
window.addEventListener("hashchange", route);
async function refresh() {
  state = await api("state");
  const order = ["mobile", "desktop", "web", "branding", "motion", "poster"];
  const rank = (name) => {
    const index = order.findIndex((prefix) =>
      name.toLowerCase().startsWith(prefix),
    );
    return index < 0 ? 99 : index;
  };
  state.boards.sort(
    (a, b) => rank(a.name) - rank(b.name) || a.name.localeCompare(b.name),
  );
  render();
}
function goLibrary() {
  if (location.hash === "#library") {
    showScreen("workspace");
    refresh().catch((e) => toast(e.message));
  } else location.hash = "library";
}
function start(importAfter = false) {
  pendingImport = importAfter;
  if (user) {
    goLibrary();
    if (importAfter) openDialog($("#import-dialog"));
  } else location.hash = "auth";
}
document
  .querySelectorAll("[data-start]")
  .forEach((b) => (b.onclick = () => start(b.dataset.start === "import")));
function openDialog(dialog) {
  dialog.classList.remove("closing");
  dialog.showModal();
}
function closeDialog(dialog) {
  if (dialog.dataset.busy === "true") return;
  if (reducedMotion.matches || "keyboard" in document.documentElement.dataset) {
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
document.querySelectorAll("[data-oauth]").forEach((button) => {
  button.onclick = () => {
    location.href = `/api/auth/${button.dataset.oauth.toLowerCase()}/start`;
  };
});
async function submitting(form, action, message = "Saving…") {
  const button = form.querySelector("[type=submit]"),
    status = form.querySelector(".form-status");
  const dialog = form.closest("dialog");
  button.disabled = true;
  if (dialog) dialog.dataset.busy = "true";
  if (status) status.textContent = message;
  const spinner = button.querySelector(".spinner");
  if (spinner) spinner.hidden = false;
  try {
    await action();
    if (status) status.textContent = "";
  } catch (e) {
    if (status) status.textContent = e.message;
    else toast(e.message);
  } finally {
    button.disabled = false;
    if (dialog) delete dialog.dataset.busy;
    if (spinner) spinner.hidden = true;
  }
}
$("#auth-form").onsubmit = (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  submitting(
    form,
    async () => {
      const result = await api(
        "auth/continue",
        "POST",
        Object.fromEntries(new FormData(form)),
      );
      user = result.user;
      form.reset();
      await refresh();
      location.hash = "library";
      showScreen("workspace");
      if (pendingImport) {
        pendingImport = false;
        openDialog($("#import-dialog"));
      }
    },
    "Getting started…",
  );
};
$("#logout").onclick = async () => {
  try {
    await api("auth/logout", "POST", {});
    user = null;
    state = { boards: [], designs: [] };
    selectedBoard = null;
    $("#search").value = "";
    $("#account-menu").hidePopover();
    location.hash = "auth";
  } catch (e) {
    toast(e.message);
  }
};
$("#account-menu").addEventListener("toggle", (e) =>
  $("#account-button").setAttribute("aria-expanded", e.newState === "open"),
);
function render() {
  const accountEmail = state.user?.email || user?.email || "";
  $("#account-email").textContent = accountEmail.endsWith("@oauth.invalid")
    ? "Social sign-in"
    : accountEmail;
  $("#filters").innerHTML =
    `<button class="filter ${!selectedBoard ? "active" : ""}" data-board="" aria-pressed="${!selectedBoard}">All</button>` +
    state.boards
      .map(
        (b) =>
          `<button class="filter ${selectedBoard === b.id ? "active" : ""}" data-board="${b.id}" aria-pressed="${selectedBoard === b.id}">${escapeHTML(b.name)}</button>`,
      )
      .join("");
  const term = $("#search").value.toLowerCase();
  const designs = state.designs.filter(
    (d) =>
      (!selectedBoard || d.boards.includes(selectedBoard)) &&
      `${d.title} ${d.creator} ${d.notes}`.toLowerCase().includes(term),
  );
  const isEmpty = designs.length === 0;
  $("#item-count").textContent = isEmpty
    ? ""
    : `${designs.length} item${designs.length === 1 ? "" : "s"} displayed`;
  $("#board-actions").hidden = !selectedBoard;
  $("#empty-state").hidden = !isEmpty;
  $("#empty-state h1").textContent = term
    ? "No matching designs"
    : selectedBoard
      ? "A fresh moodboard, ready for inspiration"
      : "Add your first design from X";
  $("#empty-state p").textContent = term
    ? "Try another title, creator, or note."
    : "Paste a tweet link to save your first design.";
  $("#empty-add").textContent = state.designs.length
    ? "Add a design"
    : "Add my first design";
  $("#design-grid").innerHTML = designs
    .map((d, index) => {
      const ratio = d.width / d.height;
      const displayRatio = Math.max(ratio, 0.4);
      return `<button class="design-card loading" data-design="${d.id}" data-ratio="${displayRatio}" aria-label="Open ${escapeHTML(d.title)}"><span class="image-slot" style="--image-ratio:${displayRatio}"><img src="${d.thumbnail_url}" width="${d.width}" height="${d.height}" alt="${escapeHTML(d.title)}" loading="${index < 5 ? "eager" : "lazy"}" decoding="async" /></span>${d.creator ? `<span class="creator-badge">${d.creator_avatar_url ? `<img class="creator-avatar" src="${escapeHTML(d.creator_avatar_url)}" alt="" width="28" height="28" loading="lazy" />` : `<span class="creator-initial" aria-hidden="true">${escapeHTML(d.creator.replace(/^@/, "")[0]?.toUpperCase() || "")}</span>`}${escapeHTML(d.creator.replace(/^@/, ""))}</span>` : ""}${ratio < 0.4 ? '<span class="extreme-label">View full image ↗</span>' : ""}</button>`;
    })
    .join("");
  $("#design-grid")
    .querySelectorAll(".image-slot img")
    .forEach((img) => {
      const loaded = () => {
        img.closest(".design-card").classList.remove("loading");
        img.closest(".design-card").classList.add("loaded");
      };
      const failed = () => {
        img.closest(".design-card").classList.remove("loading");
        const error = document.createElement("span");
        error.className = "image-error";
        error.textContent = "Preview unavailable · open full image";
        img.closest(".image-slot").append(error);
      };
      img.addEventListener("load", loaded, { once: true });
      img.addEventListener("error", failed, { once: true });
      if (img.complete) {
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
  scheduleGalleryLayout();
}
// Compute positions from stored ratios, not image load events. Each next card
// occupies the shortest column; image decoding can only change opacity.
function scheduleGalleryLayout() {
  cancelAnimationFrame(galleryFrame);
  galleryFrame = requestAnimationFrame(() => {
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
  });
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
  const button = e.target.closest("[data-board]");
  if (!button) return;
  selectedBoard = button.dataset.board || null;
  render();
};
$("#search").oninput = render;
$("#paste-link").onclick = $("#empty-add").onclick = () => {
  $("#import-status").textContent = "";
  openDialog($("#import-dialog"));
};
$("#paste-clipboard").onclick = async () => {
  try {
    $("#tweet-url").value = await navigator.clipboard.readText();
    $("#tweet-url").focus();
  } catch {
    $("#tweet-url").focus();
    $("#import-status").textContent =
      "Use ⌘V or Ctrl+V to paste your X post link.";
  }
};
$("#import-form").onsubmit = (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  submitting(
    form,
    async () => {
      const result = await api("import", "POST", {
        url: $("#tweet-url").value,
      });
      if (selectedBoard) {
        await Promise.all(
          result.ids.map((id) => {
            const d = state.designs.find((x) => x.id === id);
            return api(`designs/${id}`, "PATCH", {
              boards: [...new Set([...(d?.boards || []), selectedBoard])],
            });
          }),
        );
      }
      await refresh();
      $("#import-dialog").close();
      form.reset();
      toast(
        result.existing
          ? "This post is already in your library."
          : `${result.ids.length} design${result.ids.length === 1 ? "" : "s"} saved.`,
      );
    },
    "Fetching designs…",
  );
};
$("#upload-design").onclick = () => {
  $("#account-menu").hidePopover();
  openDialog($("#upload-dialog"));
};
$("#image-file").onchange = () => {
  const file = $("#image-file").files[0];
  if (file && !$("#upload-form").elements.title.value)
    $("#upload-form").elements.title.value = file.name
      .replace(/\.[^.]+$/, "")
      .replace(/[-_]/g, " ");
};
$("#upload-form").onsubmit = (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  submitting(form, async () => {
    const file = $("#image-file").files[0];
    if (!file || file.size > 10 * 1024 * 1024)
      throw new Error("Choose an image under 10 MB.");
    const image = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(",")[1]);
      reader.onerror = () => reject(new Error("Could not read the image."));
      reader.readAsDataURL(file);
    });
    const result = await api("designs", "POST", {
      ...Object.fromEntries(new FormData(form)),
      image,
    });
    if (selectedBoard)
      await api(`designs/${result.id}`, "PATCH", { boards: [selectedBoard] });
    await refresh();
    form.reset();
    $("#upload-dialog").close();
    toast("Design saved.");
  });
};
function openBoard(edit) {
  editingBoard = edit;
  $("#board-title").textContent = edit ? "Rename moodboard" : "New moodboard";
  $("#board-form").elements.name.value = edit
    ? state.boards.find((b) => b.id === selectedBoard).name
    : "";
  $("#board-dialog .form-status").textContent = "";
  openDialog($("#board-dialog"));
}
$("#new-board").onclick = () => {
  $("#account-menu").hidePopover();
  openBoard(false);
};
$("#rename-board").onclick = () => openBoard(true);
$("#board-form").onsubmit = (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  submitting(form, async () => {
    const result = await api(
      editingBoard ? `boards/${selectedBoard}` : "boards",
      editingBoard ? "PATCH" : "POST",
      { name: form.elements.name.value },
    );
    if (!editingBoard) selectedBoard = result.id;
    await refresh();
    $("#board-dialog").close();
    toast("Moodboard saved.");
  });
};
function confirmDelete(description, action) {
  pendingDelete = action;
  $("#confirm-description").textContent = description;
  $("#confirm-dialog .form-status").textContent = "";
  openDialog($("#confirm-dialog"));
}
$("#cancel-delete").onclick = () => closeDialog($("#confirm-dialog"));
$("#confirm-delete").onclick = async () => {
  const button = $("#confirm-delete");
  button.disabled = true;
  try {
    await pendingDelete();
    $("#confirm-dialog").close();
    await refresh();
    toast("Deleted.");
  } catch (e) {
    $("#confirm-dialog .form-status").textContent = e.message;
  } finally {
    button.disabled = false;
  }
};
$("#delete-board").onclick = () =>
  confirmDelete(
    "The moodboard will be removed. Its designs will stay in All.",
    async () => {
      await api(`boards/${selectedBoard}`, "DELETE", {});
      selectedBoard = null;
    },
  );
$("#design-grid").onclick = (e) => {
  const card = e.target.closest("[data-design]");
  if (card) openDetail(card.dataset.design);
};
function openDetail(id) {
  const d = state.designs.find((x) => x.id === id);
  $("#detail-content").innerHTML =
    `<div class="detail-layout"><div><img class="detail-image" src="${d.url}" width="${d.width}" height="${d.height}" alt="${escapeHTML(d.title)}" /></div><div class="detail-info"><h2 id="detail-title">${escapeHTML(d.title)}</h2><p>${escapeHTML(d.creator ? "@" + d.creator.replace(/^@/, "") : "")}</p>${d.notes ? `<p>${escapeHTML(d.notes)}</p>` : ""}${d.source ? `<a class="source-link" href="${escapeHTML(d.source)}" target="_blank" rel="noopener noreferrer">View original post ↗</a>` : ""}<h3>Save to moodboards</h3><div class="board-options">${state.boards.map((b) => `<label><input type="checkbox" value="${b.id}" ${d.boards.includes(b.id) ? "checked" : ""} />${escapeHTML(b.name)}</label>`).join("")}</div><div class="confirm-actions"><button class="button secondary" id="remove-design">Delete</button><button class="button primary" id="save-boards">Save changes</button></div><p id="detail-status" class="form-status" role="status"></p></div></div>`;
  $("#save-boards").onclick = async () => {
    const button = $("#save-boards");
    button.disabled = true;
    try {
      await api(`designs/${id}`, "PATCH", {
        boards: [
          ...document.querySelectorAll(".board-options input:checked"),
        ].map((i) => i.value),
      });
      await refresh();
      closeDialog($("#detail-dialog"));
      toast("Moodboards updated.");
    } catch (e) {
      $("#detail-status").textContent = e.message;
    } finally {
      button.disabled = false;
    }
  };
  $("#remove-design").onclick = () =>
    confirmDelete(
      "This design will be removed from your library and every moodboard.",
      async () => {
        await api(`designs/${id}`, "DELETE", {});
        $("#detail-dialog").close();
      },
    );
  openDialog($("#detail-dialog"));
}
async function init() {
  try {
    user = (await api("auth/me")).user;
    await route();
    const error = new URLSearchParams(location.search).get("auth_error");
    if (error) {
      $("#auth-status").textContent = error;
      history.replaceState(null, "", "/#auth");
    }
  } catch {
    toast("Backend unavailable. Start the app with npm start, then refresh.");
    showScreen("landing", false);
  }
}
init();
