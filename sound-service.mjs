export const SOUND_PACK = "soft";
export const SOUND_EVENTS = Object.freeze({
  primary: "select",
  deleted: "delete",
  copied: "copy",
  imported: "success",
});
const PREFERENCE_KEY = "pluck:sound";

// Framework-neutral service: no browser or audio work until a trusted gesture.
export function createSoundService({
  createPlayer,
  createContext,
  storage,
  visible = () => true,
  now = () => performance.now(),
}) {
  let preferences = { enabled: true, volume: 0.75, typing: false };
  try {
    const saved = JSON.parse(storage?.getItem(PREFERENCE_KEY) || "{}");
    if (typeof saved.enabled === "boolean") preferences.enabled = saved.enabled;
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
  function event(name, { gesture = false } = {}) {
    const cue = SOUND_EVENTS[name];
    if (
      !cue ||
      disposed ||
      (!ready && !(gesture && context)) ||
      !preferences.enabled ||
      !preferences.volume ||
      !visible()
    )
      return null;
    const time = now();
    const cooldown = name === "primary" ? 120 : 0;
    if (time - (lastPlayed.get(name) ?? -Infinity) < cooldown) return null;
    lastPlayed.set(name, time);
    try {
      return player.play(cue);
    } catch {
      return null;
    }
  }
  function stopAll() {
    player?.stopAll();
  }
  async function run(name, action) {
    const result = await action();
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
    destroy,
    preferences: () => ({ ...preferences }),
  };
}
