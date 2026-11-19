import { createUISFX } from "/assets/uisfx.js";
import { createSoundService } from "./sound-service.mjs";

let storage;
try {
  storage = window.localStorage;
} catch {}
export const sound = createSoundService({
  createPlayer: createUISFX,
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
      "button.primary:not(#confirm-delete), button.auth-submit",
    );
    if (
      event.isTrusted &&
      button &&
      !button.disabled &&
      button.getAttribute("aria-disabled") !== "true" &&
      !button.hasAttribute("data-muted")
    ) {
      sound.unlock();
      sound.event(button.id === "paste-link" ? "pasteLink" : "primary", {
        gesture: true,
      });
    }
  },
  options,
);
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
