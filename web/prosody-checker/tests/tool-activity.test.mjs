import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../site.js", import.meta.url), "utf8");
test("running tool appears inside the existing bubble and changes to past tense", () => {
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.classList = { add() {} }; }
    append(...nodes) { this.children.push(...nodes); }
    insertBefore(node) { this.children.unshift(node); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute() {}
    querySelector(selector) {
      for (const child of this.children) {
        if (`.${child.className}` === selector || child.tag === selector) return child;
        const found = child.querySelector?.(selector);
        if (found) return found;
      }
      return null;
    }
  }
  const article = new Element("article");
  article.dataset = { executionPhase: "running" };
  const content = new Element("div"); content.className = "message-content";
  const paragraph = new Element("p"); paragraph.textContent = "";
  content.append(paragraph); article.append(content);
  const query = article.querySelector.bind(article);
  article.querySelector = (selector) => selector === ".message-text" ? paragraph : query(selector);
  const context = vm.createContext({ document: { createElement: (tag) => new Element(tag) },
    isChatScrollPinnedToBottom: () => false, scrollChatToBottom() {} });
  vm.runInContext(source.slice(source.indexOf("function ensureToolActivity("), source.indexOf("function filterConversations(")), context);
  context.updateToolActivity(article, "call", "查询词牌格律", "running");
  let list = article.querySelector(".tool-activity__list");
  assert.equal(list.children[0].children[1].textContent, "正在查询词牌格律……");
  assert.equal(content.children[0].tag, "div");
  context.updateToolActivity(article, "call", "查询词牌格律", "completed");
  assert.equal(list.children.length, 1);
  assert.equal(list.children[0].children[1].textContent, "查询了词牌格律");
  context.updateToolActivity(article, "call", "查询词牌格律", "failed");
  assert.equal(list.children[0].children[1].textContent, "查询词牌格律失败");
  assert.ok(!list.children[0].children[1].textContent.includes("重试"));
  assert.equal(article.children.length, 1);
});
test("duplicate starts do not reopen settled calls; termination settles parallel calls", () => {
  const context = vm.createContext({ renderToolActivityItem() {} });
  vm.runInContext(source.slice(source.indexOf("function updateToolActivity("), source.indexOf("function filterConversations(")), context);
  const details = {};
  const article = { dataset: {}, querySelector: (selector) => selector === ".tool-activity" ? details : null };
  context.updateToolActivity(article, "one", "查询词牌", "running");
  context.updateToolActivity(article, "two", "查询词牌", "running");
  context.updateToolActivity(article, "one", "查询词牌", "completed");
  context.updateToolActivity(article, "one", "查询词牌", "running");
  assert.equal(article._toolActivityItems.size, 2);
  assert.equal(article._toolActivityItems.get("one").state, "completed");
  context.finishToolActivity(article);
  assert.equal(article._toolActivityItems.get("two").state, "interrupted");
  assert.equal("open" in details, false);
  assert.equal(article.dataset.executionPhase, "finished");
});
