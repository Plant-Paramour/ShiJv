"""Validate the shipped page and its local module/style dependency graph."""
import hashlib
from html.parser import HTMLParser
import json
import posixpath
import re
from urllib.parse import unquote, urlsplit


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('src'):
            self.references.append(attrs['src'])
        if tag == 'link' and attrs.get('rel') == 'stylesheet' and attrs.get('href'):
            self.references.append(attrs['href'])


def web_assets(entries):
    """Return reachable assets; reject missing, case-mismatched or mixed releases.

    JS extraction covers this application's literal static imports, re-exports,
    and literal dynamic imports. It is deliberately not a general JS parser.
    """
    pending = ['web/prosody-checker/index.html']
    visited = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        if name not in entries:
            raise ValueError('网页资源缺失或路径大小写不一致：' + name)
        visited.add(name)
        text = entries[name].decode('utf-8-sig')
        refs = []
        if name.endswith('.html'):
            page = _Page()
            page.feed(text)
            refs = page.references
        elif name.endswith(('.js', '.mjs')):
            # Ignore comments before looking for module specifiers. Preserve quoted
            # strings so comment-like text inside a string is not stripped.
            text = re.sub(
                r'''("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|/\*[\s\S]*?\*/|//[^\r\n]*''',
                lambda match: match.group(1) or ' ', text,
            )
            refs = re.findall(r'''(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s*)["']([^"']+)["']''', text)
        elif name.endswith('.css'):
            refs = re.findall(r'''@import\s+(?:url\(\s*)?["']([^"']+)["']''', text)
        for ref in refs:
            url = urlsplit(ref)
            if url.scheme or url.netloc:
                raise ValueError('启动资源不能依赖外部地址：' + name)
            path = unquote(url.path)
            if not path:
                continue
            target = posixpath.normpath(path.lstrip('/') if path.startswith('/') else posixpath.join(posixpath.dirname(name), path))
            if not target.startswith('web/'):
                raise ValueError('网页模块或样式路径超出 web：' + target)
            pending.append(target)
    return sorted(visited)


def check_web_assets(root):
    entries = {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / 'web').rglob('*') if p.is_file()}
    names = web_assets(entries)
    manifest_path = root / 'RELEASE-MANIFEST.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))['files']
        for name in names:
            if manifest.get(name) != hashlib.sha256(entries[name]).hexdigest():
                raise ValueError('网页资源与发布清单不一致：' + name)
    return names
