import { marked } from "./vendor/marked/marked.mjs";
import DOMPurify from "./vendor/dompurify/purify.mjs";

// Protect math from Markdown's backslash and emphasis processing; code stays literal.
export function renderChatMarkdown(value) {
  const math = [];
  const marker = `SHIJUMATH${Math.random().toString(36).slice(2)}X`;
  const source = String(value || "").replace(/```[^\n]*\n[\s\S]*?(?:```|$)|~~~[^\n]*\n[\s\S]*?(?:~~~|$)|`+[^`\n]*`+|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$\$[\s\S]*?\$\$|\$(?!\s)[^$\n]*?\$/g, (match) => {
    if (match.startsWith("`") || match.startsWith("~~~")) return match;
    math.push(match);
    return `${marker}${math.length - 1}END`;
  });
  const html = DOMPurify.sanitize(marked.parse(source, { gfm: true, breaks: true }));
  const escape = (text) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  return html.replace(new RegExp(`${marker}(\\d+)END`, "g"), (_, index) => escape(math[Number(index)]));
}
