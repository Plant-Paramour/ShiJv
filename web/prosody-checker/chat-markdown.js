// Chat output is untrusted: escape HTML first and allow only safe link schemes.
export function renderChatMarkdown(value) {
  const escape = (text) => String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  const inline = (text) => {
    const tokens = [];
    let html = escape(text).replace(/`([^`]+)`/g, (_, code) => { tokens.push(`<code>${code}</code>`); return `\u0000${tokens.length - 1}\u0000`; });
    html = html.replace(/\[([^\]]+)\]\(([^\s)]+)\)/g, (_, label, url) => {
      if (!/^(https?:\/\/|mailto:|\/(?!\/)|#)/i.test(url)) return label;
      tokens.push(`<a href="${url}" rel="noopener noreferrer">${label}</a>`);
      return `\u0000${tokens.length - 1}\u0000`;
    }).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/__(.+?)__/g, "<strong>$1</strong>")
      .replace(/\*([^*\n]+)\*/g, "<em>$1</em>").replace(/~~(.+?)~~/g, "<del>$1</del>");
    return html.replace(/\u0000(\d+)\u0000/g, (_, index) => tokens[Number(index)]);
  };
  const lines = String(value || "").replace(/\r\n?/g, "\n").replace(/\u0000/g, "").split("\n");
  const output = [];
  let index = 0;
  const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index++; continue; }
    if (/^\s*```/.test(line)) {
      const code = []; index++;
      while (index < lines.length && !/^\s*```/.test(lines[index])) code.push(lines[index++]);
      if (index < lines.length) index++;
      output.push(`<pre><code>${escape(code.join("\n"))}</code></pre>`); continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) { output.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`); index++; continue; }
    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) { output.push("<hr>"); index++; continue; }
    if (line.includes("|") && index + 1 < lines.length && cells(lines[index + 1]).every((cell) => /^:?-{3,}:?$/.test(cell))) {
      const header = cells(line); index += 2;
      let rows = "";
      while (index < lines.length && lines[index].includes("|")) rows += `<tr>${cells(lines[index++]).map((cell) => `<td>${inline(cell)}</td>`).join("")}</tr>`;
      output.push(`<div class="chat-markdown-table"><table><thead><tr>${header.map((cell) => `<th>${inline(cell)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`); continue;
    }
    if (/^\s*>/.test(line)) {
      const quote = [];
      while (index < lines.length && /^\s*>/.test(lines[index])) quote.push(lines[index++].replace(/^\s*>\s?/, ""));
      output.push(`<blockquote>${quote.map(inline).join("<br>")}</blockquote>`); continue;
    }
    const list = line.match(/^\s*(?:([-+*])|\d+[.)])\s+(.+)$/);
    if (list) {
      const tag = list[1] ? "ul" : "ol";
      const matcher = list[1] ? /^\s*[-+*]\s+(.+)$/ : /^\s*\d+[.)]\s+(.+)$/;
      const items = [];
      while (index < lines.length) { const match = lines[index].match(matcher); if (!match) break; items.push(`<li>${inline(match[1])}</li>`); index++; }
      output.push(`<${tag}>${items.join("")}</${tag}>`); continue;
    }
    output.push(`<p>${inline(line)}</p>`); index++;
  }
  return output.join("");
}
