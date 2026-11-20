import test from "node:test";
import assert from "node:assert/strict";
import { CUES, getPlaybackMode } from "uisfx";
import {
  createSoundService,
  SOUND_EVENTS,
  SOUND_PACK,
} from "../sound-service.mjs";

function harness({
  saved = {},
  nullPlayback = false,
  noAudio = false,
  delayedPlayer = false,
} = {}) {
  const calls = [],
    data = new Map([["pluck:sound", JSON.stringify(saved)]]);
  let time = 0,
    visible = true;
  const context = {
    state: "suspended",
    sampleRate: 48000,
    currentTime: 0,
    destination: {},
    createBuffer(channels, length, sampleRate) {
      calls.push(["buffer", length, sampleRate]);
      return { getChannelData: () => new Float32Array(length) };
    },
    createBufferSource() {
      const source = {
        connect() {},
        disconnect() {},
        start() {
          calls.push(["tick"]);
        },
        stop() {
          calls.push(["tick.stop"]);
          source.onended?.();
        },
      };
      return source;
    },
    createBiquadFilter() {
      return { frequency: {}, Q: {}, connect() {}, disconnect() {} };
    },
    createGain() {
      return {
        gain: {
          setValueAtTime() {},
          linearRampToValueAtTime() {},
          exponentialRampToValueAtTime() {},
        },
        connect() {},
        disconnect() {},
      };
    },
    createOscillator() {
      const source = {
        frequency: {
          setValueAtTime(value) {
            calls.push(["frequency.start", value]);
          },
          exponentialRampToValueAtTime(value) {
            calls.push(["frequency.end", value]);
          },
        },
        connect() {},
        disconnect() {},
        start() {
          calls.push(["tone"]);
        },
        stop(time) {
          if (time === undefined) {
            calls.push(["tone.stop"]);
            source.onended?.();
          }
        },
      };
      return source;
    },
    resume() {
      calls.push("resume");
      this.state = "running";
      return Promise.resolve();
    },
    async close() {
      calls.push("context.close");
      this.state = "closed";
    },
  };
  const player = {
    play(cue) {
      calls.push(["play", cue]);
      return nullPlayback ? null : { stop() {} };
    },
    stopAll() {
      calls.push("stopAll");
    },
    setEnabled(enabled) {
      calls.push(["enabled", enabled]);
    },
    setVolume(volume) {
      calls.push(["volume", volume]);
    },
    async destroy() {
      calls.push("destroy");
    },
  };
  const service = createSoundService({
    createPlayer(options) {
      calls.push(["create", options]);
      return delayedPlayer ? null : player;
    },
    createContext() {
      calls.push("context");
      return noAudio ? null : context;
    },
    storage: {
      getItem: (key) => data.get(key),
      setItem: (key, value) => data.set(key, value),
    },
    visible: () => visible,
    now: () => time,
  });
  return {
    service,
    calls,
    data,
    context,
    cues: () =>
      calls
        .filter((call) => Array.isArray(call) && call[0] === "play")
        .map((call) => call[1]),
    loadPlayer() {
      delayedPlayer = false;
    },
    advance(ms) {
      time += ms;
    },
    hide() {
      visible = false;
      service.stopAll();
    },
  };
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function unlocked(options) {
  const h = harness(options);
  h.service.unlock();
  return h;
}

test("only the requested semantic cues are allowed and all are real one-shots", () => {
  assert.deepEqual(SOUND_EVENTS, {
    primary: "select",
    deleted: "delete",
    copied: "copy",
    imported: "success",
  });
  const cues = new Set(CUES.map((cue) => cue.name));
  for (const cue of Object.values(SOUND_EVENTS)) {
    assert.ok(cues.has(cue));
    assert.equal(getPlaybackMode(cue), "one-shot");
  }
  assert.equal(SOUND_PACK, "soft");
});
test("default sounds are on at 75%, typing off, with no autoplay or SSR audio construction", () => {
  const h = harness({ saved: { typing: true } });
  h.service.event("imported");
  assert.deepEqual(h.calls, []);
  assert.deepEqual(h.service.preferences(), {
    enabled: true,
    volume: 0.75,
    typing: false,
  });
  h.service.unlock();
  assert.equal(
    h.calls.find((call) => Array.isArray(call) && call[0] === "create")[1]
      .volume,
    0.75,
  );
  assert.deepEqual(h.cues(), []);
});
test("primary activation emits select once; pointer/keyboard unlocks reuse one player", () => {
  const h = unlocked();
  h.service.unlock();
  h.service.unlock();
  h.service.event("primary", { gesture: true });
  assert.equal(
    h.calls.filter((call) => Array.isArray(call) && call[0] === "create")
      .length,
    1,
  );
  assert.deepEqual(h.cues(), ["select"]);
  h.service.event("primary", { gesture: true });
  assert.deepEqual(h.cues(), ["select"]);
  h.advance(121);
  h.service.event("primary", { gesture: true });
  assert.deepEqual(h.cues(), ["select", "select"]);
});
test("first keyboard activation can play select while the gesture resumes audio; async cues wait", async () => {
  const h = harness(),
    resume = deferred();
  h.context.resume = () => resume.promise;
  h.service.unlock();
  h.service.event("primary", { gesture: true });
  h.service.event("imported");
  assert.deepEqual(h.cues(), ["select"]);
  h.context.state = "running";
  resume.resolve();
  await resume.promise;
  h.service.event("imported");
  assert.deepEqual(h.cues(), ["select", "success"]);
});
test("delete, copy and X import cues happen only after their real operations succeed", async () => {
  for (const event of ["deleted", "copied", "imported"]) {
    const h = unlocked(),
      task = deferred();
    const result = h.service.run(event, () => task.promise);
    assert.deepEqual(h.cues(), []);
    task.resolve("done");
    assert.equal(await result, "done");
    assert.deepEqual(h.cues(), [SOUND_EVENTS[event]]);
  }
});
test("failed, cancelled and timed out operations never play outcome or error sounds", async () => {
  for (const name of ["Error", "AbortError", "TimeoutError"]) {
    const h = unlocked(),
      task = deferred();
    const result = h.service.run("imported", () => task.promise);
    const error = new Error("failed");
    error.name = name;
    task.reject(error);
    await assert.rejects(result, error);
    assert.deepEqual(h.cues(), []);
  }
});
test("all other sounds remain off, including processing, typing, dialogs and image navigation", () => {
  const h = unlocked();
  for (const name of [
    "opened",
    "closed",
    "selected",
    "saved",
    "next",
    "previous",
    "failed",
    "typing",
    "processing",
    "sent",
    "enabled",
    "added",
    "removed",
    "volume",
  ])
    h.service.event(name);
  assert.deepEqual(h.cues(), []);
});
test("pre-unlock/background outcomes are suppressed, never replayed later", async () => {
  const h = harness();
  await h.service.run("imported", async () => 1);
  h.service.unlock();
  assert.deepEqual(h.cues(), []);
  h.hide();
  h.service.event("copied");
  assert.deepEqual(h.cues(), []);
});
test("mute immediately stops audio, persists volume and stays silent on a new instance", () => {
  const h = unlocked();
  h.service.setVolume(0.35);
  h.service.setEnabled(false);
  assert.ok(
    h.calls.lastIndexOf("stopAll") <
      h.calls.findIndex(
        (c) => Array.isArray(c) && c[0] === "enabled" && c[1] === false,
      ),
  );
  h.service.event("primary", { gesture: true });
  assert.deepEqual(h.cues(), []);
  const next = harness({ saved: JSON.parse(h.data.get("pluck:sound")) });
  assert.deepEqual(next.service.preferences(), {
    enabled: false,
    volume: 0.35,
    typing: false,
  });
  next.service.unlock();
  assert.deepEqual(next.calls, []);
});
test("zero volume, null playback and unsupported audio do not break product actions", async () => {
  const h = unlocked({ nullPlayback: true });
  assert.equal(await h.service.run("copied", async () => 7), 7);
  h.service.setVolume(0);
  h.service.event("deleted");
  assert.deepEqual(h.cues(), ["copy"]);
  const unsupported = unlocked({ noAudio: true });
  assert.equal(await unsupported.service.run("imported", async () => 8), 8);
});
test("final teardown is idempotent and destroys player before caller-owned context", async () => {
  const h = unlocked(),
    task = deferred();
  const result = h.service.run("imported", () => task.promise);
  await Promise.all([h.service.destroy(), h.service.destroy()]);
  task.resolve();
  await result;
  h.service.unlock();
  assert.deepEqual(h.cues(), []);
  assert.equal(h.calls.filter((c) => c === "destroy").length, 1);
  assert.ok(h.calls.indexOf("destroy") < h.calls.indexOf("context.close"));
  assert.equal(h.context.state, "closed");
});

test("board clicks use a short cached tick, not the old tonal press cue", async () => {
  const h = unlocked();
  const click = h.service.event("board", { gesture: true });
  h.service.event("board", { gesture: true });
  assert.equal(
    h.calls.filter((c) => Array.isArray(c) && c[0] === "tick").length,
    1,
  );
  assert.deepEqual(h.cues(), []);
  assert.deepEqual(
    h.calls.find((c) => Array.isArray(c) && c[0] === "buffer"),
    ["buffer", 960, 48000],
  );
  h.advance(121);
  h.service.event("board", { gesture: true });
  assert.equal(
    h.calls.filter((c) => Array.isArray(c) && c[0] === "tick").length,
    2,
  );
  assert.equal(
    h.calls.filter((c) => Array.isArray(c) && c[0] === "buffer").length,
    1,
  );
  h.service.setEnabled(false);
  await click.ended;
  assert.equal(
    h.calls.filter((c) => Array.isArray(c) && c[0] === "tick.stop").length,
    2,
  );
  h.service.event("hover");
  assert.deepEqual(h.cues(), []);
});

test("preview open rises and preview close falls, with shared mute and cleanup", async () => {
  const h = unlocked();
  const opening = h.service.event("previewOpen", { gesture: true });
  const closing = h.service.event("previewClose", { gesture: true });
  assert.deepEqual(
    h.calls
      .filter((c) => Array.isArray(c) && c[0] === "frequency.start")
      .map((c) => c[1]),
    [220, 700],
  );
  assert.deepEqual(
    h.calls
      .filter((c) => Array.isArray(c) && c[0] === "frequency.end")
      .map((c) => c[1]),
    [520, 220],
  );
  assert.deepEqual(h.cues(), []);
  h.service.setEnabled(false);
  await Promise.all([opening.ended, closing.ended]);
  assert.equal(
    h.calls.filter((c) => Array.isArray(c) && c[0] === "tone.stop").length,
    2,
  );
});

test("Paste link uses the reference Save chime instead of the primary Select cue", async () => {
  const h = unlocked();
  const sound = h.service.event("pasteLink", { gesture: true });
  assert.deepEqual(h.cues(), []);
  assert.deepEqual(
    h.calls.find((c) => Array.isArray(c) && c[0] === "frequency.start"),
    ["frequency.start", 523.25],
  );
  assert.deepEqual(
    h.calls.find((c) => Array.isArray(c) && c[0] === "frequency.end"),
    ["frequency.end", 783.99],
  );
  h.service.stopAll();
  await sound.ended;
});

test("slow optional audio can become available after the first gesture", async () => {
  const h = harness({ delayedPlayer: true });
  h.service.unlock();
  assert.doesNotThrow(() => h.service.event("primary", { gesture: true }));
  assert.deepEqual(h.cues(), []);
  h.loadPlayer();
  h.advance(500);
  h.service.event("imported");
  assert.deepEqual(h.cues(), ["success"]);
  assert.equal(h.calls.filter((call) => call === "context").length, 1);
  await h.service.destroy();
});

test("board hover uses the cached tick after unlock without swallowing the click", async () => {
  const h = harness();
  assert.equal(h.service.event("boardHover"), null);
  h.service.unlock();
  h.service.event("boardHover");
  h.service.event("boardHover");
  h.service.event("board", { gesture: true });
  assert.equal(h.calls.filter(c => Array.isArray(c) && c[0] === "tick").length, 2);
  assert.equal(h.calls.filter(c => Array.isArray(c) && c[0] === "buffer").length, 1);
  h.service.setEnabled(false);
  h.advance(500);
  assert.equal(h.service.event("boardHover"), null);
  await h.service.destroy();
});

test("hover ticks deduplicate re-entry without suppressing preview navigation", async () => {
  const h = harness();
  h.service.unlock();
  h.service.event("boardHover", { hoverKey: "web" });
  h.advance(180);
  assert.equal(h.service.event("boardHover", { hoverKey: "web" }), null);
  assert.ok(h.service.event("board", { gesture: true, hoverKey: "mobile" }));
  h.advance(150);
  assert.ok(h.service.event("board", { gesture: true })); // preview navigation
  h.advance(300);
  assert.ok(h.service.event("board", { gesture: true, hoverKey: "web" }));
  await h.service.destroy();
});
