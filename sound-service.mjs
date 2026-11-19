export const SOUND_PACK = "soft";
export const SOUND_EVENTS = Object.freeze({
  primary: "select",
  deleted: "delete",
  copied: "copy",
  imported: "success",
});
const GLIDES = {
  previewOpen: {
    from: 220,
    to: 520,
    glide: 0.12,
    duration: 0.16,
    attack: 0.085,
    level: 0.04,
  },
  previewClose: {
    from: 700,
    to: 220,
    glide: 0.06,
    duration: 0.1,
    attack: 0.004,
    level: 0.05,
  },
  pasteLink: {
    from: 523.25,
    to: 783.99,
    glide: 0.07,
    duration: 0.28,
    attack: 0.003,
    level: 0.065,
  },
};
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
  const activeSounds = new Set();
  let clickBuffer;
  const persist = () => {
    try {
      storage?.setItem(PREFERENCE_KEY, JSON.stringify(preferences));
    } catch {}
  };
  function ensurePlayer() {
    if (!player && context)
      player = createPlayer({
        pack: SOUND_PACK,
        volume: preferences.volume,
        enabled: preferences.enabled,
        context,
      });
    return player;
  }
  function unlock() {
    if (disposed || !preferences.enabled) return;
    try {
      context ||= createContext();
      if (!context) return;
      // resume() must be invoked synchronously inside the user's gesture.
      const resumed = context.resume();
      ensurePlayer();
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
  // A dry 20ms noise tick, matching the reference's non-melodic click character.
  // It shares the existing unlocked context, volume and teardown lifecycle.
  function boardClick() {
    if (!clickBuffer) {
      clickBuffer = context.createBuffer(
        1,
        Math.ceil(context.sampleRate * 0.02),
        context.sampleRate,
      );
      const samples = clickBuffer.getChannelData(0);
      for (let i = 0; i < samples.length; i++) {
        const t = i / context.sampleRate;
        const envelope =
          Math.min(1, t / 0.001) *
          Math.exp(-t / 0.004) *
          (1 - i / samples.length);
        samples[i] = (Math.random() * 2 - 1) * envelope * 0.14;
      }
    }
    const source = context.createBufferSource();
    const filter = context.createBiquadFilter();
    const gain = context.createGain();
    source.buffer = clickBuffer;
    filter.type = "bandpass";
    filter.frequency.value = 5400;
    filter.Q.value = 1.8;
    gain.gain.value = preferences.volume * 0.5;
    source.connect(filter);
    filter.connect(gain);
    gain.connect(context.destination);
    const handle = trackVoice(source, [source, filter, gain]);
    source.start();
    return handle;
  }
  function trackVoice(source, nodes) {
    let done = false,
      resolve;
    const ended = new Promise((finish) => {
      resolve = finish;
    });
    const handle = {
      ended,
      stop() {
        try {
          source.stop();
        } catch {}
        cleanup();
      },
    };
    function cleanup() {
      if (done) return;
      done = true;
      activeSounds.delete(handle);
      nodes.forEach((node) => node.disconnect());
      resolve();
    }
    source.onended = cleanup;
    activeSounds.add(handle);
    return handle;
  }
  // Reference-style bubble motion: a gentle rise in, a quicker fall out.
  function glideSound(tone) {
    const source = context.createOscillator();
    const gain = context.createGain();
    const start = context.currentTime;
    const { duration } = tone;
    source.type = "sine";
    source.frequency.setValueAtTime(tone.from, start);
    source.frequency.exponentialRampToValueAtTime(tone.to, start + tone.glide);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.linearRampToValueAtTime(
      preferences.volume * tone.level,
      start + tone.attack,
    );
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    source.connect(gain);
    gain.connect(context.destination);
    const handle = trackVoice(source, [source, gain]);
    source.start(start);
    source.stop(start + duration);
    return handle;
  }
  function event(name, { gesture = false } = {}) {
    const cue = SOUND_EVENTS[name];
    const tone = Object.hasOwn(GLIDES, name) ? GLIDES[name] : null;
    if (
      (!cue && name !== "board" && !tone) ||
      disposed ||
      (!ready && !(gesture && context)) ||
      !preferences.enabled ||
      !preferences.volume ||
      !visible()
    )
      return null;
    const time = now();
    const cooldown = name === "primary" || name === "board" || tone ? 120 : 0;
    if (time - (lastPlayed.get(name) ?? -Infinity) < cooldown) return null;
    lastPlayed.set(name, time);
    try {
      if (name === "board") return boardClick();
      if (tone) return glideSound(tone);
      return ensurePlayer()?.play(cue) ?? null;
    } catch {
      return null;
    }
  }
  function stopAll() {
    for (const click of [...activeSounds]) click.stop();
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
      clickBuffer = null;
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
