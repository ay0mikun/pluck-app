export const SOUND_PACK = "soft";
export const SOUND_EVENTS = Object.freeze({
  opened: "open",
  closed: "close",
  saved: "success",
  deleted: "delete",
  copied: "copy",
  failed: "error",
  selected: "select",
  added: "check",
  removed: "uncheck",
  next: "forward",
  previous: "back",
  enabled: "toggle-on",
  sent: "send",
  typing: "typing",
  volume: "volume-change",
});
const PREFERENCE_KEY = "pluck:sound";

// Framework-neutral service: no browser or audio work until a trusted gesture.
export function createSoundService({
  createPlayer,
  createContext,
  storage,
  visible = () => true,
  schedule = setTimeout,
  cancel = clearTimeout,
  now = () => performance.now(),
}) {
  let preferences = { enabled: true, volume: 0.75, typing: false };
  try {
    const saved = JSON.parse(storage?.getItem(PREFERENCE_KEY) || "{}");
    if (typeof saved.enabled === "boolean") preferences.enabled = saved.enabled;
    if (typeof saved.typing === "boolean") preferences.typing = saved.typing;
    if (Number.isFinite(saved.volume))
      preferences.volume = Math.max(0, Math.min(1, saved.volume));
  } catch {
    /* Private browsing can disable storage. */
  }
  let player,
    context,
    ready = false,
    disposed = false,
    disposing;
  const loops = new Map();
  const lastPlayed = new Map();
  const persist = () => {
    try {
      storage?.setItem(PREFERENCE_KEY, JSON.stringify(preferences));
    } catch {}
  };
  function unlock() {
    if (disposed || !preferences.enabled) return;
    try {
      context ||= createContext();
      if (!context) return;
      // resume() must be invoked synchronously inside the user's gesture.
      const resumed = context.resume();
      player ||= createPlayer({
        pack: SOUND_PACK,
        volume: preferences.volume,
        enabled: preferences.enabled,
        context,
      });
      ready = context.state === "running";
      Promise.resolve(resumed)
        .then(() => {
          if (!disposed) ready = context.state === "running";
        })
        .catch(() => {
          ready = false;
        });
    } catch {
      ready = false;
    }
  }
  function event(name) {
    const cue = SOUND_EVENTS[name];
    if (
      !cue ||
      disposed ||
      !ready ||
      !preferences.enabled ||
      !preferences.volume ||
      !visible()
    )
      return null;
    if (name === "typing" && !preferences.typing) return null;
    const time = now();
    const cooldown = [
      "selected",
      "next",
      "previous",
      "volume",
      "opened",
      "closed",
    ].includes(name)
      ? 120
      : name === "failed"
        ? 250
        : 0;
    if (time - (lastPlayed.get(name) ?? -Infinity) < cooldown) return null;
    lastPlayed.set(name, time);
    try {
      return player.play(
        cue,
        name === "typing" ? { volume: 0.1, cooldownMs: 0 } : undefined,
      );
    } catch {
      return null;
    }
  }
  function beginProcessing() {
    if (loops.has("processing")) return loops.get("processing").stop;
    const entry = { handle: null, timer: null, timeout: null, stop };
    function stop() {
      cancel(entry.timer);
      cancel(entry.timeout);
      entry.handle?.stop();
      entry.handle = null;
      if (loops.get("processing") === entry) loops.delete("processing");
    }
    entry.timer = schedule(() => {
      if (
        !disposed &&
        ready &&
        preferences.enabled &&
        preferences.volume &&
        visible()
      ) {
        try {
          entry.handle = player.play("processing", { volume: 0.025 });
        } catch {}
      }
    }, 800);
    entry.timeout = schedule(stop, 30000);
    loops.set("processing", entry);
    return stop;
  }
  function stopAll() {
    for (const loop of [...loops.values()]) loop.stop();
    player?.stopAll();
  }
  async function run(name, action, { processing = false } = {}) {
    const stop = processing ? beginProcessing() : () => {};
    let result;
    try {
      result = await action();
    } catch (error) {
      stop();
      if (error?.name !== "AbortError") event("failed");
      throw error;
    } finally {
      stop();
    }
    if (name) event(name);
    return result;
  }
  function setEnabled(enabled) {
    if (!enabled) stopAll();
    preferences.enabled = Boolean(enabled);
    player?.setEnabled(preferences.enabled);
    persist();
  }
  function setVolume(volume) {
    if (!Number.isFinite(volume)) return;
    preferences.volume = Math.max(0, Math.min(1, volume));
    if (!preferences.volume) stopAll();
    player?.setVolume(preferences.volume);
    persist();
  }
  function setTyping(enabled) {
    preferences.typing = Boolean(enabled);
    persist();
  }
  function destroy() {
    if (disposing) return disposing;
    disposed = true;
    ready = false;
    stopAll();
    disposing = (async () => {
      try {
        await player?.destroy();
      } finally {
        if (context && context.state !== "closed") await context.close();
      }
      player = context = null;
    })();
    return disposing;
  }
  return {
    unlock,
    event,
    run,
    stopAll,
    setEnabled,
    setVolume,
    setTyping,
    destroy,
    preferences: () => ({ ...preferences }),
  };
}
