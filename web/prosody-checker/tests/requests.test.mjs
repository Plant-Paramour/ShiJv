import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const site = readFileSync(new URL("../site.js", import.meta.url), "utf8");
function section(start, end) { return site.slice(site.indexOf(start), site.indexOf(end, site.indexOf(start))); }

test("GET requests share only in-flight work and retry after failure", async () => {
  let calls = 0;
  let fail = false;
  const context = vm.createContext({
    currentUser: { id: "first" }, requestHeaders: (headers) => headers,
    responseError: () => "failed", invalidateAuth: () => {},
    fetch: async () => { calls++; await new Promise((resolve) => setTimeout(resolve, 5)); return { ok: !fail, status: 500, json: async () => ({ calls }) }; },
  });
  vm.runInContext(section("const pendingApiReads", "function requireLocalProfile"), context);
  await Promise.all([context.apiFetch("/test"), context.apiFetch("/test")]);
  assert.equal(calls, 1);
  await context.apiFetch("/test");
  assert.equal(calls, 2);
  fail = true;
  await assert.rejects(context.apiFetch("/test"));
  fail = false;
  await context.apiFetch("/test");
  assert.equal(calls, 4);
  const firstUser = context.apiFetch("/test");
  context.currentUser = { id: "second" };
  await Promise.all([firstUser, context.apiFetch("/test")]);
  assert.equal(calls, 6);
  await Promise.all([context.apiFetch("/test", { method: "POST" }), context.apiFetch("/test", { method: "POST" })]);
  assert.equal(calls, 8);
});

for (const [tab, expected] of [["account", []], ["poems", ["poems:owned"]], ["archive", ["/v1/conversations?include_deleted=true"]]]) {
  test(`profile ${tab} loads only its panel dependencies`, async () => {
    const calls = [];
    const context = vm.createContext({
      currentUser: { id: "user" }, profileGeneration: 1, profileLoadVersion: 0,
      document: { body: { dataset: { currentView: "profile" } }, querySelector: () => ({}) },
      profileTabFromHash: () => tab,
      apiFetch: async (url) => { calls.push(url); return { items: [] }; },
      loadPoems: async ({ source }) => calls.push(`poems:${source}`),
      renderArchive: () => {}, renderUserRows: () => {}, announce: (message) => { throw new Error(message); },
    });
    vm.runInContext(section("async function loadProfileData", "function renderArchive"), context);
    await context.loadProfileData();
    assert.deepEqual(calls, expected);
    context.document.body.dataset.currentView = "chat";
    await context.loadProfileData();
    assert.deepEqual(calls, expected);
  });
}

test("hidden poem lists cause no request; picker loads only selected source", async () => {
  const calls = [];
  const context = vm.createContext({
    currentUser: {}, profileGeneration: 1, ownedPoems: [], favoritePoems: [], profilePoems: [],
    document: { body: { dataset: { currentView: "forum" } }, querySelector: () => null },
    apiFetch: async (url) => { calls.push(url); return { items: [] }; },
    latestOwnedPoems: (items) => items, renderPoemList: () => {}, URLSearchParams, ownedPoemsRequestVersion: 0, ownedPoemsHasMore: false, ownedPoemsLoadingMore: false,
  });
  vm.runInContext(section("async function loadPoems", "async function loadProfileData"), context);
  await context.loadPoems();
  assert.deepEqual(calls, []);
  await context.loadPoems({ source: "owned" });
  assert.deepEqual(calls, ["/v1/profile/poems?latest=true&limit=24&offset=0"]);
  await context.loadPoems({ source: "favorites" });
  assert.deepEqual(calls, ["/v1/profile/poems?latest=true&limit=24&offset=0", "/v1/profile/poems?favorite=true"]);
});

test("favorites request only collections and fetch closed collection on expansion", async () => {
  const calls = [];
  const nodes = new Map();
  const groups = [];
  function node(tag) {
    const listeners = {};
    const children = [];
    const value = {
      dataset: {}, open: false, innerHTML: "", children,
      append: (...items) => children.push(...items),
      replaceChildren: () => { children.length = 0; },
      querySelector: () => ({ textContent: "" }),
      addEventListener: (name, listener) => { listeners[name] = listener; },
      expand: () => { value.open = true; listeners.toggle(); },
    };
    if (tag === "details") groups.push(value);
    return value;
  }
  const context = vm.createContext({
    currentUser: { id: "user" }, profileGeneration: 1, profileLoadVersion: 0,
    profileCollections: [], favoriteThreadCollections: [], favoriteThreads: [],
    document: {
      body: { dataset: { currentView: "profile" } }, createElement: node,
      querySelector: (selector) => { if (!nodes.has(selector)) nodes.set(selector, node()); return nodes.get(selector); },
    },
    profileTabFromHash: () => "favorites", mergeNamedCollections: (items) => items,
    migrateLegacyThreadCollections: async () => {},
    apiFetch: async (url) => {
      calls.push(url);
      return { items: url === "/v1/profile/poem-collections" ? [{ id: "default", name: "默认合集" }, { id: "closed", name: "其他合集" }] : [] };
    },
    announce: (message) => { throw new Error(message); },
  });
  vm.runInContext(section("async function loadProfileData", "function renderArchive"), context);
  await context.loadProfileData();
  assert.deepEqual(calls, ["/v1/profile/poem-collections", "/v1/profile/poem-collections/default/items"]);
  groups[1].expand();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(calls.at(-1), "/v1/profile/poem-collections/closed/items");
  groups[1].expand();
  assert.equal(calls.length, 3);
});

test("prosody and sentence matching read rhyme books and meters through persistent dataset cache", () => {
  const source = readFileSync(new URL("../prosody-evaluation.js", import.meta.url), "utf8");
  const app = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  const search = readFileSync(new URL("../sentence-search.js", import.meta.url), "utf8");
  const cache = readFileSync(new URL("../dataset-cache.js", import.meta.url), "utf8");
  assert.match(source, /getDatasetJson\("rhyme", book\.file\)/);
  assert.match(source, /getDatasetJson\("meter", `\$\{formName\}\.json`\)/);
  assert.match(app, /getDatasetJson\("meter", "index\.json"\)/);
  assert.match(search, /getDatasetJson\("rhyme", books\[book\]\)/);
  assert.match(search, /getMeterBundle\(items\.map\(\(item\) => item\.file\)\)/);
  assert.match(cache, /storedAt: new Date\(\)\.toISOString\(\)/);
  assert.match(cache, /rhymeVersion/);
  assert.match(cache, /meter-templates/);
});

test("drag priority order is applied lexicographically to all match criteria", () => {
  const source = readFileSync(new URL("../sentence-search.js", import.meta.url), "utf8");
  const start = source.indexOf("function compareMatchPriority");
  const end = source.indexOf("let draggedPriority", start);
  const context = vm.createContext({});
  vm.runInContext(source.slice(start, end), context);
  const shapeWins = [
    { id: "tone", intra: false, shape: true, score: 0.7, breaks: 0 },
    { id: "intra", intra: true, shape: false, score: 0.99, breaks: 1 },
  ];
  assert.equal(context.sortMatchResults(shapeWins, ["shape", "intra", "score", "breaks"])[0].id, "tone");
  assert.equal(context.sortMatchResults(shapeWins, ["intra", "shape", "score", "breaks"])[0].id, "intra");

  const toneWins = [
    { id: "high-tone", intra: false, shape: false, score: 0.9, breaks: 2 },
    { id: "low-breaks", intra: false, shape: false, score: 0.8, breaks: 0 },
  ];
  assert.equal(context.sortMatchResults(toneWins, ["score", "breaks", "shape", "intra"])[0].id, "high-tone");
  assert.equal(context.sortMatchResults(toneWins, ["breaks", "score", "shape", "intra"])[0].id, "low-breaks");
});

test("default matching priority is score, shape, intra-line, then fewer readings", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const list = html.match(/<ol id="match-priority-list"[\s\S]*?<\/ol>/)?.[0] || "";
  assert.deepEqual([...list.matchAll(/data-priority="([^"]+)"/g)].map((match) => match[1]), ["score", "shape", "intra", "breaks"]);
});

test("matching filters accept integer limits, apply score thresholds, and reject decimals", () => {
  const source = readFileSync(new URL("../sentence-search.js", import.meta.url), "utf8");
  const start = source.indexOf("function getMatchFilters");
  const end = source.indexOf("function claimMatchQuery", start);
  const controls = new Map([
    ["#match-limit-enabled", { checked: true }], ["#match-score-enabled", { checked: false }],
    ["#match-limit", { value: "2" }], ["#match-score-min", { value: "80" }],
    ["#match-status", { textContent: "" }],
  ]);
  const context = vm.createContext({
    $: (selector) => controls.get(selector),
    getMatchPriorityOrder: () => [],
    sortMatchResults: (results) => results,
  });
  vm.runInContext(source.slice(start, end), context);
  const event = () => ({ stopped: false, preventDefault() {}, stopImmediatePropagation() { this.stopped = true; } });
  const filters = context.getMatchFilters(event());
  assert.equal(filters.maxRows, 2);
  assert.equal(filters.minScore, null);
  const results = [0.79, 0.8, 0.95].map((score) => ({ score }));
  const filtered = context.applyMatchFilters(results, { maxRows: 1, minScore: 80 });
  assert.deepEqual(Array.from(filtered.filtered, (item) => item.score), [0.8, 0.95]);
  assert.deepEqual(Array.from(filtered.shown, (item) => item.score), [0.8]);
  controls.get("#match-limit").value = "2.5";
  const invalid = event();
  assert.equal(context.getMatchFilters(invalid), null);
  assert.equal(invalid.stopped, true);
});

test("matching claims suppress unchanged and in-flight queries but allow changed input or priority", () => {
  const source = readFileSync(new URL("../sentence-search.js", import.meta.url), "utf8");
  const start = source.indexOf("function claimMatchQuery");
  const end = source.indexOf("async function loadMatchingMeters", start);
  const context = vm.createContext({
    priorities: ["score", "shape", "intra", "breaks"],
    getMatchPriorityOrder() { return context.priorities; },
    $: (selector) => ({ value: "" }),
  });
  vm.runInContext(`let lastSuccessfulMatchKey = null; const runningMatchKeys = new Set(); let latestMatchRun = 0;\n${source.slice(start, end)}`, context);
  const event = () => ({ prevented: false, stopped: false, preventDefault() { this.prevented = true; }, stopImmediatePropagation() { this.stopped = true; } });
  const first = context.claimMatchQuery("春山", event(), { maxRows: 30, minScore: null });
  assert.ok(first);
  const duplicateEvent = event();
  assert.equal(context.claimMatchQuery("春山", duplicateEvent, { maxRows: 30, minScore: null }), null);
  assert.equal(duplicateEvent.prevented, true);
  const changedText = context.claimMatchQuery("秋水", event(), { maxRows: 30, minScore: null });
  assert.ok(changedText);
  changedText.complete();
  const repeated = event();
  assert.equal(context.claimMatchQuery("秋水", repeated, { maxRows: 30, minScore: null }), null);
  context.priorities = ["shape", "intra", "score", "breaks"];
  assert.ok(context.claimMatchQuery("秋水", event()));
  assert.ok(context.claimMatchQuery("秋水", event(), { maxRows: 10, minScore: 80 }));
  first.fail();
});

test("priority drag completion synchronizes the visible leading criterion", () => {
  const source = readFileSync(new URL("../sentence-search.js", import.meta.url), "utf8");
  const start = source.indexOf("function compareMatchPriority");
  const end = source.indexOf("type.addEventListener", start);
  const sourceSlice = source.slice(start, end);
  assert.match(sourceSlice, /item\.addEventListener\("dragend",[^\n]*syncPrimaryPriority\(\)/);
  assert.match(sourceSlice, /function getMatchPriorityOrder\(\)[\s\S]*\.map\(\(item\) => item\.dataset\.priority\)/);
});

test("proposal editor fetches Songci catalog only for Songci selections", () => {
  const editor = section("function appendProposalEditor", "let chatSessionId");
  assert.match(editor, /if \(meterType\.value === "宋词"\) populateSongciForms\(\)/);
  assert.match(editor, /if \(meterType\.value === "宋词"\) populateSongciForms\(\);\s*updateMeta\(\);\s*\}\);/);
  assert.doesNotMatch(editor, /loadMeterCatalog\(\)\.then/);
});
