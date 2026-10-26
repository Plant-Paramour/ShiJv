import test from "node:test";
import assert from "node:assert/strict";
import { createStreamingText } from "../stream-text.js";

test("large chunks render progressively and catch up within six frames", () => {
  const queue = new Map(), shown = []; let id = 0;
  const stream = createStreamingText((text) => shown.push(text), (fn) => { queue.set(++id, fn); return id; }, (key) => queue.delete(key));
  const text = "词牌格律实时输出😀".repeat(20);
  stream.set(text);
  let frames = 0;
  while (queue.size) {
    const [key, fn] = queue.entries().next().value; queue.delete(key); fn(); frames++;
    if (frames === 1) assert.ok(shown[0].length < text.length);
    assert.ok(frames <= 6);
  }
  assert.equal(shown.at(-1), text);
  assert.ok(shown.every((value) => !/[\uD800-\uDBFF]$/.test(value)));
  stream.set(text + "后续内容");
  stream.stop();
  assert.equal(queue.size, 0);
});
