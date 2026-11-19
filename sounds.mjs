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
const toggle = document.querySelector("#sound-toggle");
const typing = document.querySelector("#typing-sound-toggle");
const volume = document.querySelector("#sound-volume");
function renderSettings() {
  const state = sound.preferences();
  toggle.setAttribute("aria-checked", String(state.enabled));
  toggle.querySelector(".sound-setting-value").textContent = state.enabled
    ? "On"
    : "Off";
  typing.setAttribute("aria-checked", String(state.typing));
  typing.querySelector(".sound-setting-value").textContent = state.typing
    ? "On"
    : "Off";
  typing.disabled = volume.disabled = !state.enabled;
  volume.value = Math.round(state.volume * 100);
  volume.setAttribute("aria-valuetext", `${volume.value}%`);
}
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
toggle.addEventListener(
  "click",
  () => {
    const enabled = !sound.preferences().enabled;
    sound.setEnabled(enabled);
    if (enabled) {
      sound.unlock();
      sound.event("enabled");
    }
    renderSettings();
  },
  { signal: listeners.signal },
);
typing.addEventListener(
  "click",
  () => {
    sound.setTyping(!sound.preferences().typing);
    renderSettings();
  },
  { signal: listeners.signal },
);
volume.addEventListener(
  "input",
  () => {
    sound.setVolume(Number(volume.value) / 100);
    renderSettings();
  },
  { signal: listeners.signal },
);
volume.addEventListener("change", () => sound.event("volume"), {
  signal: listeners.signal,
});
document.addEventListener(
  "input",
  (event) => {
    if (
      event.isTrusted &&
      event.target.matches(
        'input:not([type]), input[type="text"], input[type="email"], input[type="url"], textarea',
      ) &&
      !event.target.readOnly
    )
      sound.event("typing");
  },
  options,
);
document.addEventListener(
  "click",
  (event) => {
    const close = event.target.closest(
      ".close, .board-drawer-close, #close-preview, #cancel-edit-board, #cancel-delete",
    );
    if (
      event.isTrusted &&
      close &&
      !close.disabled &&
      close.closest("dialog")?.dataset.busy !== "true"
    )
      sound.event("closed");
  },
  options,
);
document.addEventListener(
  "cancel",
  (event) => {
    if (event.isTrusted && event.target.dataset.busy !== "true")
      sound.event("closed");
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
renderSettings();
