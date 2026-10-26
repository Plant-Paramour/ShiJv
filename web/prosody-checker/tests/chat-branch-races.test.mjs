import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../site.js", import.meta.url), "utf8");

function streamHarness() {
  let submit;
  let respond;
  const mutations = [];
  const thread = { querySelector: () => null };
  const context = vm.createContext({
    chatForm: { addEventListener: (_, callback) => { submit = callback; } },
    chatBusy: false, requireLocalProfile: () => true,
    chatInput: { value: "hello", style: {}, focus() {} },
    editingMessageId: null, resumeTurnId: null,
    hideChatWelcome() {}, chatThread: thread,
    appendMessage: () => ({ parentElement: thread, dataset: {} }),
    activeAssistantMessage: null, activeChatStopMode: null, activeTurnId: null,
    currentConversationId: "same", chatSessionId: "same", conversationLoadVersion: 1,
    activeChatRequest: null, AbortController, TextDecoder,
    setChatBusy() {}, agentStatus: { textContent: "", classList: { add() {}, remove() {} } },
    chatModel: { value: "fixture" }, loadModelConfig: async () => {}, requestHeaders: (value) => value,
    fetch: () => new Promise((resolve) => { respond = resolve; }),
    replaceMessage: () => mutations.push("message"),
    appendProposalEditor: () => mutations.push("proposal"),
    updateToolActivity: () => mutations.push("tool"),
    finishToolActivity() {},
    updateActivityPreview() {},
    renderToolActivityItem() {},
    scheduleConversationRefresh() {},
    startJobProgress: () => mutations.push("job"),
    loadConversations: async () => {},
    openConversation: async () => mutations.push("reload"),
    setConversationLocation() {}, currentUser: null,
  });
  const start = source.indexOf('chatForm.addEventListener("submit",');
  const end = source.indexOf("function pauseActiveChat()", start);
  vm.runInContext(source.slice(start, end), context);
  return {
    context, mutations,
    send: () => submit({ preventDefault() {} }),
    respond(events, stallAfterEvents = false) {
      let read = false;
      respond({ ok: true, body: { getReader: () => ({
        async read() {
          if (read && stallAfterEvents) throw new Error("reader must not wait after terminal event");
          if (read) return { done: true };
          read = true;
          return { value: new TextEncoder().encode(events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("")), done: false };
        },
        async cancel() {},
      }) } });
    },
  };
}

test("late stream events cannot add panels after switching branches in the same conversation", async () => {
  const harness = streamHarness();
  const pending = harness.send();
  await new Promise((resolve) => setImmediate(resolve));
  harness.context.conversationLoadVersion++;
  harness.respond([
    ["assistant.delta", { text: "old branch" }],
    ["tool.completed", { name: "prepare_generation", output: { ok: true, result: { proposal_id: "old" } } }],
    ["job.submitted", { job_id: "old-job" }],
  ]);
  await pending;
  assert.deepEqual(harness.mutations, []);
});

test("completed stream reloads the authoritative path and panels", async () => {
  const harness = streamHarness();
  const pending = harness.send();
  await new Promise((resolve) => setImmediate(resolve));
  harness.respond([["assistant.delta", { text: "answer" }], ["job.submitted", { job_id: "job" }], ["turn.completed", {}]], true);
  await pending;
  assert.deepEqual(harness.mutations, ["message", "message", "job", "reload"]);
});

test("image and text are sent in the same request and attachment is cleared", async () => {
  const harness = streamHarness();
  const dataUrl = "data:image/png;base64,iVBORw0KGgo=";
  const file = { name: "词谱.png", type: "image/png" };
  harness.context.chatAttachment = { files: [file], value: "selected" };
  harness.context.FileReader = class {
    readAsDataURL() { this.result = dataUrl; this.onload(); }
  };
  harness.context.renderChatAttachment = () => {};
  const fetch = harness.context.fetch;
  let submitted;
  harness.context.fetch = (url, options) => { submitted = JSON.parse(options.body); return fetch(url, options); };
  const pending = harness.send();
  await new Promise((resolve) => setImmediate(resolve));
  await Promise.resolve();
  assert.equal(submitted.message, "hello");
  assert.equal(submitted.images[0].data_url, dataUrl);
  assert.equal(submitted.images[0].name, "词谱.png");
  assert.equal(harness.context.chatAttachment.value, "");
  harness.respond([["turn.completed", { reply: "已理解图片。" }]], true);
  await pending;
});

test("text chunks stay expanded until the final turn event", async () => {
  const harness = streamHarness();
  const timeline = [];
  harness.context.updateActivityPreview = (_, text) => timeline.push(`preview:${text}`);
  harness.context.finishToolActivity = () => timeline.push("collapse");
  harness.context.replaceMessage = (_, text) => timeline.push(`final:${text}`);
  const pending = harness.send();
  await new Promise((resolve) => setImmediate(resolve));
  harness.respond([
    ["assistant.delta", { text: "第一句。" }],
    ["assistant.delta", { text: "第二句。" }],
    ["assistant.delta", { text: "第三句。" }],
    ["turn.completed", { reply: "最终返回文本。" }],
  ]);
  await pending;
  assert.deepEqual(timeline.slice(0, 5), [
    "preview:第一句。", "preview:第一句。第二句。", "preview:第一句。第二句。第三句。",
    "collapse", "final:最终返回文本。",
  ]);
});

test("retry event keeps consuming and the recovered final answer is displayed", async () => {
  const harness = streamHarness();
  const displayed = [];
  harness.context.replaceMessage = (_, text) => displayed.push(text);
  const pending = harness.send();
  await new Promise((resolve) => setImmediate(resolve));
  harness.respond([
    ["assistant.delta", { text: "unfinished" }],
    ["turn.retrying", { message: "正在恢复连接并继续处理。" }],
    ["assistant.delta", { text: "已查到格律。" }],
    ["turn.completed", { reply: "已查到格律。" }],
  ], true);
  await pending;
  assert.ok(displayed.length > 0);
  assert.ok(displayed.every((text) => text === "已查到格律。"));
  assert.equal(harness.context.activeChatRequest, null);
});

test("terminal error stays visible and loading state is released", async () => {
  const harness = streamHarness();
  const displayed = [], busy = [], refreshed = [];
  harness.context.replaceMessage = (_, text) => displayed.push(text);
  harness.context.setChatBusy = (value) => busy.push(value);
  harness.context.scheduleConversationRefresh = (id) => refreshed.push(id);
  const pending = harness.send();
  await new Promise((resolve) => setImmediate(resolve));
  harness.respond([["turn.failed", { message: "请求处理失败" }]], true);
  await pending;
  assert.match(displayed.at(-1), /请求处理失败/);
  assert.deepEqual(refreshed, []);
  assert.equal(busy.at(-1), false);
  assert.equal(harness.context.activeChatRequest, null);
});

test("conversation and job responses from different paths are rejected", async () => {
  const start = source.indexOf("async function loadConversationJobs(");
  const end = source.indexOf("function appendPersistedMessage(", start);
  const context = vm.createContext({ apiFetch: async () => ({ messages: [{ id: "other-branch" }], jobs: [{ kind: "generate" }] }) });
  vm.runInContext(source.slice(start, end), context);
  assert.equal(await context.loadConversationJobs({ id: "same", messages: [{ id: "current" }] }), null);
});

test("late job poll cannot render works into a different branch and keeps current expanded progress layout", async () => {
  let resolveJob;
  const status = { textContent: "" };
  const details = { dataset: {}, querySelector: () => status };
  const thread = { querySelector: () => null };
  let rendered = 0;
  const context = vm.createContext({
    currentConversationId: "same", conversationLoadVersion: 1, currentUser: { id: "fixture" },
    chatThread: thread, CSS: { escape: (value) => value }, activeJobPolls: new Map(),
    document: { createElement: () => details, visibilityState: "visible", addEventListener() {} },
    insertChatTimelineNode: (node) => { node.parentElement = thread; },
    chatThreadScroll: {}, Date,
    apiFetch: () => new Promise((resolve) => { resolveJob = resolve; }),
    renderAvailableScrolls: () => { rendered++; },
  });
  const start = source.indexOf("function startJobProgress(");
  const end = source.indexOf('chatForm.addEventListener("submit",', start);
  vm.runInContext(source.slice(start, end), context);
  context.startJobProgress("job");
  assert.equal(details.open, true);
  context.conversationLoadVersion++;
  resolveJob({ status: "succeeded" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(rendered, 0);
  assert.equal(status.textContent, "");
});

test("legacy task restoration uses message links, never UUIDs mentioned in chat text", async () => {
  const requests = [];
  const start = source.indexOf("async function loadConversationJobs(");
  const end = source.indexOf("function appendPersistedMessage(", start);
  const context = vm.createContext({ apiFetch: async (url) => {
    requests.push(url);
    if (url.endsWith("/state") && url.includes("/conversations/")) throw { status: 404 };
    return { kind: "generate", job_id: "owned" };
  } });
  vm.runInContext(source.slice(start, end), context);
  await context.loadConversationJobs({ id: "conversation", messages: [
    { job_id: "owned", content: "reference 00000000-0000-0000-0000-000000000000" },
  ] });
  assert.deepEqual(requests, ["/v1/conversations/conversation/state", "/v1/poetry/jobs/owned/state"]);
});
