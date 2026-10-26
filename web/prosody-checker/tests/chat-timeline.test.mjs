import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../site.js", import.meta.url), "utf8");
const start = source.indexOf("function insertChatTimelineNode(");
const end = source.indexOf("function confirmAction(", start);

function timeline() {
  const thread = {
    children: [],
    append(node) { node.parentElement = this; this.children.push(node); },
    insertBefore(node, later) {
      node.parentElement = this;
      this.children.splice(this.children.indexOf(later), 0, node);
    },
  };
  const node = (id, time) => ({
    id, dataset: time ? { createdAt: String(time) } : {},
    after(next) {
      next.parentElement = thread;
      thread.children.splice(thread.children.indexOf(this) + 1, 0, next);
    },
  });
  const context = vm.createContext({ chatThread: thread, Date });
  vm.runInContext(source.slice(start, end), context);
  return { thread, node, insert: context.insertChatTimelineNode };
}

test("restored direct creation precedes later user and Agent messages", () => {
  const { thread, node, insert } = timeline();
  thread.append(node("user", 20));
  thread.append(node("assistant", 21));
  const proposal = node("proposal");
  insert(proposal, 10);
  proposal.after(node("progress", 10));
  assert.deepEqual(thread.children.map((n) => n.id), ["proposal", "progress", "user", "assistant"]);
});

test("multiple historical jobs retain chronological order between messages", () => {
  const { thread, node, insert } = timeline();
  thread.append(node("first-message", 5));
  thread.append(node("later-message", 30));
  insert(node("newer-job"), 20);
  insert(node("older-job"), 10);
  assert.deepEqual(thread.children.map((n) => n.id), ["first-message", "older-job", "newer-job", "later-message"]);
});

test("Agent proposal stays attached to its message when timestamps match", () => {
  const { thread, node, insert } = timeline();
  const assistant = node("assistant", 10);
  thread.append(assistant);
  thread.append(node("later-user", 20));
  insert(node("proposal"), undefined, assistant);
  assert.deepEqual(thread.children.map((n) => n.id), ["assistant", "proposal", "later-user"]);
});

test("later jobs sharing an earlier message anchor do not reverse their order", () => {
  const { thread, node, insert } = timeline();
  const assistant = node("assistant", 5);
  thread.append(assistant);
  insert(node("first-job"), 10, assistant);
  insert(node("second-job"), 20, assistant);
  assert.deepEqual(thread.children.map((n) => n.id), ["assistant", "first-job", "second-job"]);
});

test("task timestamps cannot move an attached panel past the next tree message", () => {
  const { thread, node, insert } = timeline();
  const assistant = node("assistant", 5);
  const next = node("next-message", 6);
  next.dataset.messageId = "next-message";
  thread.append(assistant);
  thread.append(next);
  insert(node("late-submission"), 100, assistant);
  assert.deepEqual(thread.children.map((n) => n.id), ["assistant", "late-submission", "next-message"]);
});
