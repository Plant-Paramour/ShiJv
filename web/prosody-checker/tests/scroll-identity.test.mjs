import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../site.js", import.meta.url), "utf8");
function extract(name, next) {
  return source.slice(source.indexOf(name), source.indexOf(next, source.indexOf(name)));
}

test("修改卷轴打开所点版本，不查询或选择森林最新版本", async () => {
  const calls = [];
  const context = vm.createContext({
    requireLocalProfile: () => true,
    apiFetch: async (url) => { calls.push(url); return { id: "original-version" }; },
    window: { location: { hash: "#chat/s/conversation" } },
    poemPageState: {}, announce: (message) => assert.fail(message),
  });
  vm.runInContext(extract("async function openPoemEditorFromScroll(", "function poemDraftKey("), context);
  await context.openPoemEditorFromScroll({ job_id: "job-A", candidate_ordinal: 11 });
  assert.deepEqual(calls, ["/v1/profile/poems/by-job/job-A/11"]);
  assert.equal(context.window.location.hash, "poem/edit/original-version");
  assert.equal(context.poemPageState.returnHash, "#chat/s/conversation");
  calls.length = 0;
  await context.openPoemEditorFromScroll({ id: "clicked-version" });
  assert.equal(context.window.location.hash, "poem/edit/clicked-version");
  assert.deepEqual(calls, []);
});

test("作品 ID 跟随当前卷轴，其他卷轴晚返回也不会覆盖", () => {
  const scrolls = [
    { dataset: { candidateOrdinal: "1", poemId: "poem-A" } },
    { dataset: { candidateOrdinal: "11" } },
  ];
  const identity = {};
  const buttons = [{}, {}];
  const count = {};
  const input = { setCustomValidity() {} };
  const navigation = {
    querySelectorAll: () => buttons,
    querySelector: (selector) => selector.endsWith("count") ? count : input,
  };
  const container = {
    dataset: {}, querySelectorAll: () => scrolls,
    querySelector: (selector) => selector.endsWith("poem-id") ? identity : navigation,
  };
  const context = vm.createContext({ document: {}, requestAnimationFrame: (fn) => fn(), updatePoemLayout() {} });
  vm.runInContext(extract("function updateScrollNavigation(", "function startJobProgress("), context);
  context.updateScrollNavigation(container);
  assert.equal(identity.textContent, "作品 ID：poem-A");
  assert.equal(buttons[0].disabled, true);
  container.dataset.selectedScroll = "11";
  context.updateScrollNavigation(container);
  assert.equal(identity.textContent, "作品 ID：正在读取……");
  scrolls[0].dataset.poemId = "late-A";
  context.updateScrollNavigation(container);
  assert.equal(identity.textContent, "作品 ID：正在读取……");
  scrolls[1].dataset.poemId = "poem-B";
  context.updateScrollNavigation(container);
  assert.equal(identity.textContent, "作品 ID：poem-B");
  assert.equal(scrolls[0].hidden, true);
  assert.equal(scrolls[1].hidden, false);
  assert.equal(buttons[1].disabled, true);
  scrolls.splice(0, 1);
  context.updateScrollNavigation(container);
  assert.equal(navigation.hidden, true);
  assert.equal(identity.textContent, "作品 ID：poem-B");
});
