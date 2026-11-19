import test from "node:test";
import assert from "node:assert/strict";
import { CUES, getPlaybackMode } from "uisfx";
import {
  createSoundService,
  SOUND_EVENTS,
  SOUND_PACK,
} from "../sound-service.mjs";

function harness({ saved = {}, nullPlayback = false, noAudio = false } = {}) {
  const calls = [],
    timers = new Map(),
    data = new Map([["pluck:sound", JSON.stringify(saved)]]);
  let time = 0,
    nextTimer = 0,
    visible = true;
  const context = {
    state: "suspended",
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
    play(cue, options) {
      calls.push(["play", cue, options]);
      return nullPlayback
        ? null
        : {
            stop() {
              calls.push(["stop", cue]);
            },
          };
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
      return player;
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
    schedule(fn, milliseconds) {
      const id = ++nextTimer;
      timers.set(id, { fn, at: time + milliseconds });
      return id;
    },
    cancel: (id) => timers.delete(id),
  });
  return {
    service,
    calls,
    timers,
    data,
    context,
    cues: () =>
      calls
        .filter((call) => Array.isArray(call) && call[0] === "play")
        .map((call) => call[1]),
    advance(ms) {
      time += ms;
      for (const [id, timer] of timers)
        if (timer.at <= time) {
          timers.delete(id);
          timer.fn();
        }
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
function enabled(options = {}) {
  const h = harness({ saved: { enabled: true, volume: 0.7 }, ...options });
  h.service.unlock();
  return h;
}

test("all event cues exist in the catalog; processing is the only loop used", () => {
  const cues = new Set(CUES.map((cue) => cue.name));
  for (const cue of Object.values(SOUND_EVENTS)) {
    assert.ok(cues.has(cue));
    assert.equal(getPlaybackMode(cue), "one-shot");
  }
  assert.equal(getPlaybackMode("processing"), "loop");
  assert.equal(SOUND_PACK, "soft");
  assert.equal(SOUND_EVENTS.deleted, "delete");
  assert.equal(SOUND_EVENTS.copied, "copy");
});
test("startup defaults to sounds on at 75%, typing off, with no autoplay", () => {
  const h = harness();
  h.service.event("opened");
  assert.deepEqual(h.calls, []);
  assert.deepEqual(h.service.preferences(), {
    enabled: true,
    volume: 0.75,
    typing: false,
  });
  h.service.unlock();
  const creation = h.calls.find(
    (call) => Array.isArray(call) && call[0] === "create",
  );
  assert.equal(creation[1].volume, 0.75);
  assert.equal(creation[1].enabled, true);
  assert.deepEqual(h.cues(), []);
});
test("suppress pre-unlock async feedback rather than replay it on unlock", async () => {
  const h = harness({ saved: { enabled: true } });
  await h.service.run("saved", async () => 42);
  assert.deepEqual(h.cues(), []);
  h.service.unlock();
  assert.deepEqual(h.cues(), []);
  h.service.event("opened");
  assert.deepEqual(h.cues(), ["open"]);
});
test("pointer and keyboard unlocks reuse one player and do not generate click cues", () => {
  const h = enabled();
  h.service.unlock();
  h.service.unlock();
  assert.equal(
    h.calls.filter((call) => Array.isArray(call) && call[0] === "create")
      .length,
    1,
  );
  assert.deepEqual(h.cues(), []);
  h.service.event("opened");
  assert.deepEqual(h.cues(), ["open"]);
});
test("save/delete outcomes happen only after the real task resolves", async () => {
  for (const event of ["saved", "deleted"]) {
    const h = enabled(),
      task = deferred();
    const result = h.service.run(event, () => task.promise, {
      processing: true,
    });
    assert.deepEqual(h.cues(), []);
    h.advance(800);
    assert.deepEqual(h.cues(), ["processing"]);
    task.resolve("done");
    assert.equal(await result, "done");
    const outcome = SOUND_EVENTS[event];
    assert.deepEqual(h.cues(), ["processing", outcome]);
    assert.ok(
      h.calls.findIndex((c) => Array.isArray(c) && c[0] === "stop") <
        h.calls.findIndex((c) => Array.isArray(c) && c[1] === outcome),
    );
    assert.equal(h.timers.size, 0);
  }
});
test("failure stops processing and emits error, cancellation remains silent", async () => {
  for (const name of ["Error", "AbortError", "TimeoutError"]) {
    const h = enabled(),
      task = deferred();
    const result = h.service.run("saved", () => task.promise, {
      processing: true,
    });
    h.advance(800);
    const error = new Error("failed");
    error.name = name;
    task.reject(error);
    await assert.rejects(result, error);
    assert.deepEqual(
      h.cues(),
      name === "AbortError" ? ["processing"] : ["processing", "error"],
    );
    assert.ok(h.calls.some((c) => Array.isArray(c) && c[0] === "stop"));
    assert.equal(h.timers.size, 0);
  }
});
test("fast operations cancel the delayed loop before it starts", async () => {
  const h = enabled();
  await h.service.run("saved", async () => {}, { processing: true });
  h.advance(1000);
  assert.deepEqual(h.cues(), ["success"]);
  assert.equal(h.timers.size, 0);
});
test("mute, zero volume, navigation/logout stopAll and visibility stop pending and active loops", async () => {
  for (const action of [
    (h) => h.service.setEnabled(false),
    (h) => h.service.setVolume(0),
    (h) => h.service.stopAll(),
    (h) => h.hide(),
  ]) {
    for (const active of [false, true]) {
      const h = enabled(),
        task = deferred();
      const result = h.service.run(null, () => task.promise, {
        processing: true,
      });
      if (active) h.advance(800);
      action(h);
      h.advance(1000);
      task.resolve();
      await result;
      assert.equal(h.timers.size, 0);
      assert.ok(h.calls.includes("stopAll"));
      if (active)
        assert.ok(h.calls.some((c) => Array.isArray(c) && c[0] === "stop"));
      else assert.deepEqual(h.cues(), []);
    }
  }
});
test("mute is immediate and preferences survive a new instance", () => {
  const h = enabled();
  h.service.setTyping(true);
  h.service.setVolume(0.35);
  h.service.setEnabled(false);
  const stop = h.calls.lastIndexOf("stopAll");
  const disabled = h.calls.findIndex(
    (c) => Array.isArray(c) && c[0] === "enabled" && c[1] === false,
  );
  assert.ok(stop < disabled);
  h.service.event("saved");
  assert.deepEqual(h.cues(), []);
  const next = harness({ saved: JSON.parse(h.data.get("pluck:sound")) });
  assert.deepEqual(next.service.preferences(), {
    enabled: false,
    volume: 0.35,
    typing: true,
  });
});
test("optional typing uses a short cue for every input without a cooldown", () => {
  const h = enabled();
  h.service.event("typing");
  assert.deepEqual(h.cues(), []);
  h.service.setTyping(true);
  for (let i = 0; i < 4; i++) h.service.event("typing");
  assert.deepEqual(h.cues(), ["typing", "typing", "typing", "typing"]);
  assert.deepEqual(h.calls.at(-1)[2], { volume: 0.1, cooldownMs: 0 });
});
test("rapid navigation is throttled and null playback/unsupported audio never breaks actions", async () => {
  const h = enabled({ nullPlayback: true });
  h.service.event("next");
  h.service.event("next");
  h.advance(121);
  h.service.event("next");
  assert.deepEqual(h.cues(), ["forward", "forward"]);
  assert.equal(
    await h.service.run("saved", async () => 7, { processing: true }),
    7,
  );
  const unsupported = enabled({ noAudio: true });
  assert.equal(await unsupported.service.run("saved", async () => 8), 8);
});
test("final teardown is idempotent, drains loops, destroys player before caller-owned context", async () => {
  const h = enabled(),
    task = deferred();
  const result = h.service.run("saved", () => task.promise, {
    processing: true,
  });
  h.advance(800);
  await Promise.all([h.service.destroy(), h.service.destroy()]);
  task.resolve();
  await result;
  h.service.unlock();
  assert.deepEqual(h.cues(), ["processing"]);
  assert.equal(h.calls.filter((c) => c === "destroy").length, 1);
  assert.ok(h.calls.indexOf("destroy") < h.calls.indexOf("context.close"));
  assert.equal(h.context.state, "closed");
  assert.equal(h.timers.size, 0);
});

test("a stalled import loop stops at its safety timeout without claiming success", async () => {
  const h = enabled(),
    task = deferred();
  const result = h.service.run("saved", () => task.promise, {
    processing: true,
  });
  h.advance(800);
  h.advance(29200);
  assert.ok(h.calls.some((c) => Array.isArray(c) && c[0] === "stop"));
  assert.deepEqual(h.cues(), ["processing"]);
  assert.equal(h.timers.size, 0);
  task.resolve();
  await result;
  assert.deepEqual(h.cues(), ["processing", "success"]);
});
