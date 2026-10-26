import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../site.js", import.meta.url), "utf8");
const start = source.indexOf("function partialSentenceLengths(");
const end = source.indexOf("function appendProposalEditor(", start);
const context = vm.createContext({});
vm.runInContext(source.slice(start, end), context);
const lengths = (...args) => Array.from(context.partialSentenceLengths(...args));

test("direct partial editor can construct ordinary sentence inputs without an API response", () => {
  assert.deepEqual(lengths("唐诗", "七言绝句"), [7, 7, 7, 7]);
  assert.deepEqual(lengths("唐诗", "五言律诗"), Array(8).fill(5));
  assert.deepEqual(lengths("排律", "五言排律", 16), Array(16).fill(5));
  assert.deepEqual(lengths("汉俳", "汉俳", null, { line_pattern: "3-5-3" }), [3, 5, 3]);
  assert.equal(context.partialSentenceLengths("宋词", "忆秦娥"), null);
});

test("sentence layout rejects invalid counts and Hanpai patterns", () => {
  assert.throws(() => lengths("排律", "七言排律", 5), /偶数/);
  assert.throws(() => lengths("汉俳", "汉俳", null, { line_pattern: "4-4-4" }), /汉俳格式/);
});

test("local rewrite target selection must be a proper contiguous span", () => {
  assert.throws(() => context.validateRewriteTargetNumbers([2, 4], 5), /连续/);
  assert.throws(() => context.validateRewriteTargetNumbers([], 5), /至少取消勾选/);
  assert.throws(() => context.validateRewriteTargetNumbers([1,2,3,4,5], 5), /至少保留一句/);
  assert.deepEqual(Array.from(context.validateRewriteTargetNumbers([2, 3], 5)), [2, 3]);
});

test("sentence labels match the selected local edit mode", () => {
  const refresh = source.match(/const refresh = \(\) => \{\s*row\.classList\.toggle\("is-selected", check\.checked\);[\s\S]*?\n        \};/)[0];
  for (const [isLocalRewrite, checked, expected] of [[true, true, "重写"], [true, false, "保留"], [false, true, "固定保留"], [false, false, "空缺待补"]]) {
    const state = { textContent: "" };
    const sandbox = vm.createContext({ row: { classList: { toggle() {} } }, check: { checked }, isLocalRewrite, state });
    vm.runInContext(`${refresh}\nrefresh();`, sandbox);
    assert.equal(state.textContent, expected);
  }
});
