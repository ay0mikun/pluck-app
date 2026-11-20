import { createSoundService } from "./sound-service.mjs";

// Optional audio must never block the visual app or its loading states.
let createUISFX;
void import("/assets/uisfx.js")
  .then((runtime) => {
    createUISFX = runtime.createUISFX;
  })
  .catch(() => {});
let storage;
try {
  storage = window.localStorage;
} catch {}
export const sound = createSoundService({
  createPlayer: (options) => createUISFX?.(options),
  storage,
  createContext: () => {
    const Context = window.AudioContext || window.webkitAudioContext;
    return Context ? new Context({ latencyHint: "interactive" }) : null;
  },
  visible: () => !document.hidden,
});
const listeners = new AbortController();
const options = { capture: true, signal: listeners.signal };
// No blanket click sonification. These listeners only unlock audio.
document.addEventListener(
  "pointerdown",
  (event) => {
    if (event.isTrusted) sound.unlock();
  },
  options,
);
document.addEventListener(
  "keydown",
  (event) => {
    if (event.isTrusted && !event.repeat) sound.unlock();
  },
  options,
);
document.addEventListener(
  "click",
  (event) => {
    const button = event.target.closest(
      "button.primary:not(#confirm-delete), button.auth-submit, #new-board, #previous-design, #next-design",
    );
    if (
      event.isTrusted &&
      button &&
      !button.disabled &&
      button.getAttribute("aria-disabled") !== "true" &&
      !button.hasAttribute("data-muted")
    ) {
      sound.unlock();
      const cue = ["paste-link", "new-board"].includes(button.id)
        ? "pasteLink"
        : ["previous-design", "next-design"].includes(button.id) ? "board" : "primary";
      sound.event(cue, { gesture: true });
    }
  },
  options,
);
document.addEventListener("pointerover", (event) => {
  const board = event.target.closest("#filters [data-board]");
  if (!event.isTrusted || event.pointerType !== "mouse" || !board || board.contains(event.relatedTarget)) return;
  // Hover cannot unlock browser audio; it plays after a trusted activation.
  sound.event("boardHover", { hoverKey: board.dataset.board });
}, options);
document.addEventListener(
  "visibilitychange",
  () => {
    if (document.hidden) sound.stopAll();
  },
  { signal: listeners.signal },
);
window.addEventListener(
  "pagehide",
  (event) => {
    sound.stopAll();
    if (!event.persisted) {
      listeners.abort();
      void sound.destroy();
    }
  },
  { signal: listeners.signal },
);
window.addEventListener("hashchange", () => sound.stopAll(), {
  signal: listeners.signal,
});
