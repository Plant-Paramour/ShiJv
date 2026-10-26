import test from "node:test";
import assert from "node:assert/strict";
import { renderChatMarkdown } from "../chat-markdown.js";

test("streaming Markdown updates incomplete text and fenced code", () => {
  assert.equal(renderChatMarkdown("**词牌"), "<p>**词牌</p>");
  assert.equal(renderChatMarkdown("**词牌**"), "<p><strong>词牌</strong></p>");
  assert.equal(renderChatMarkdown("```python\nprint(1)"), "<pre><code>print(1)</code></pre>");
  assert.match(renderChatMarkdown("# 格律\n- 平仄\n- 韵部\n> 引文"), /<h1>格律<\/h1><ul><li>平仄<\/li><li>韵部<\/li><\/ul><blockquote>引文<\/blockquote>/);
});
test("table and links render without interpreting HTML or unsafe URLs", () => {
  assert.match(renderChatMarkdown("|体|韵|\n|---|---|\n|正体|平声|"), /<th>体<\/th>.*<td>平声<\/td>/);
  const html = renderChatMarkdown('<img src=x onerror=alert(1)> [坏链接](javascript:alert) [资料](https://example.com)');
  assert.ok(!html.includes("<img"));
  assert.ok(!html.includes('href="javascript:'));
  assert.ok(html.includes('href="https://example.com"'));
});
