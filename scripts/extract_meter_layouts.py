"""从本地原始词谱 HTML 提取显示行；只写独立布局文件，不修改词谱。"""
import argparse
import json
import re
from html.parser import HTMLParser
from pathlib import Path


class LayoutParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_gl = False
        self.depth = 0
        self.pu_depth = 0
        self.skip = 0
        self.rows = []
        self.text = ''

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'div':
            if attrs.get('id') == 'gl':
                self.in_gl = True
                self.depth = 1
            elif self.in_gl:
                self.depth += 1
                if 'pu' in attrs.get('class', '').split():
                    self.pu_depth = self.depth
                    self.text = ''
        if self.pu_depth and tag == 'sup':
            self.skip += 1

    def handle_data(self, text):
        if self.pu_depth and not self.skip:
            self.text += text

    def handle_endtag(self, tag):
        if tag == 'sup' and self.skip:
            self.skip -= 1
        if tag == 'div' and self.in_gl:
            if self.depth == self.pu_depth:
                self.rows.append(self.text)
                self.pu_depth = 0
            self.depth -= 1
            if self.depth == 0:
                self.in_gl = False


def extract(html, variant):
    parser = LayoutParser()
    parser.feed(html)
    # 标点作为句界，顿号仅为句内的“读”；HTML 中只有显示行有意义。
    rows = []
    for raw in parser.rows:
        raw = re.sub(r'\([^)]*\)|（[^）]*）', '', raw)
        parts = re.findall(r'([平仄中]+(?:[、/][平仄中]+)*)([^平仄中、/\s]*)', re.sub(r'\s+', '', raw))
        segments = [(len(re.sub('[^平仄中]', '', text)), punctuation[:1]) for text, punctuation in parts]
        if segments:
            rows.append(segments)
    flat = [(length, punctuation, ri) for ri, row in enumerate(rows) for length, punctuation in row]
    expected = [(key, i + 1, len(re.sub('[^平仄中]', '', line)))
                for key in sorted((k for k in variant if re.fullmatch(r'stanza\d+', k)), key=lambda k: int(k[6:]))
                for i, line in enumerate(variant[key]['lines'])]
    nonempty = [x for x in expected if x[2]]
    if [x[0] for x in flat] != [x[2] for x in nonempty]:
        raise ValueError(f'原网页句长与清洗词谱不一致: {[x[0] for x in flat]} != {[x[2] for x in expected]}')
    result = {}
    for (length, punctuation, ri), (key, number, _) in zip(flat, nonempty):
        stanza = result.setdefault(key, [])
        if not stanza or stanza[-1]['source_row'] != ri:
            stanza.append({'source_row': ri, 'sentences': []})
        stanza[-1]['sentences'].append({'line': number, 'punctuation': punctuation})
    for key, number, length in expected:
        if not length:
            previous = next((row for row in result.get(key, []) if any(s['line'] == number - 1 for s in row['sentences'])), None)
            if previous is None:
                raise ValueError('无字位标记无法归入原网页显示行')
            previous['sentences'].append({'line': number, 'punctuation': ''})
    for key in variant:
        if re.fullmatch(r'stanza\d+', key):
            result.setdefault(key, [])
    for stanza in result.values():
        for row in stanza:
            row['sentences'].sort(key=lambda sentence: sentence['line'])
    return result


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--raw', type=Path, default=Path(r'C:\code\爬虫\Songci_Raw\pages'))
    cli.add_argument('--meter', type=Path, default=Path(__file__).resolve().parents[1] / 'Songci_Meter')
    args = cli.parse_args()
    output = {'schema_version': 1, 'description': '每段的显示行；sentences.line 为从 1 开始的清洗词谱句号，punctuation 保留原网页句末标点。', 'items': {}}
    errors = []
    for entry in json.loads((args.meter / 'index.json').read_text(encoding='utf-8')):
        data = json.loads((args.meter / entry['file']).read_text(encoding='utf-8'))
        manifest_path = args.raw / data['name'] / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
        sources = {str(v['source_id']): v['path'] for v in manifest.get('variants', [])}
        item = {}
        for variant in data['variants']:
            sid = str(variant['source_id'])
            try:
                source = manifest_path.parent / sources.get(sid, f'variants/{sid}.html')
                layout = extract(source.read_text(encoding='utf-8'), variant)
                item[sid] = {'name': variant['name'], 'stanzas': layout}
            except (OSError, ValueError) as exc:
                errors.append({'name': data['name'], 'source_id': variant['source_id'], 'error': str(exc)})
        output['items'][data['name']] = item
    (args.meter / 'display-layouts.json').write_text(json.dumps(output, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    report = {'matched': sum(len(v) for v in output['items'].values()), 'unmatched': len(errors), 'errors': errors}
    (args.meter / 'display-layout-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'matched': report['matched'], 'unmatched': report['unmatched'], 'examples': errors[:3]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
