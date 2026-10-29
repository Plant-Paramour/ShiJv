"""保存指定典藏词谱页面并输出可供核对的正文。"""
import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen


class ContentParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'div':
            if self.depth:
                self.depth += 1
            elif attrs.get('id') == 'content' and 'display:none' not in attrs.get('style', '').replace(' ', ''):
                self.depth = 1
        if self.depth and tag in ('p', 'br', 'h2', 'h3'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.depth and tag in ('p', 'h2', 'h3'):
            self.parts.append('\n')
        if tag == 'div' and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


def main():
    url = 'https://www.diancang.xyz/shicixiqu/qindingcipu/29262.html'
    folder = Path(__file__).resolve().parents[1] / 'output' / 'diancang-qindingcipu-29262'
    folder.mkdir(parents=True, exist_ok=True)
    with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=40) as response:
        raw = response.read()
    (folder / '29262.html').write_bytes(raw)
    parser = ContentParser()
    parser.feed(raw.decode('utf-8'))
    lines = [line.strip() for line in ''.join(parser.parts).splitlines() if line.strip()]
    (folder / '29262.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (folder / 'source.json').write_text(json.dumps({'url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'bytes': len(raw)}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\n'.join(lines[:35]))
    print('正文行数:', len(lines))
    root = Path(__file__).resolve().parents[1]
    records = []
    for path in (root / 'Songci_Meter').glob('*.json'):
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if isinstance(data, dict) and isinstance(data.get('variants'), list):
            records.append(data)
    # 本页目录九调；名称对照取自正文标题，不把近似名称当作同调。
    entries = [
        ('清平调', ['清平调', '清平调辞'], '3首', '清平乐不是本调'),
        ('水调歌', ['水调歌', '水调'], '11首', '水调歌头不等于水调大曲全套'),
        ('凉州歌', ['凉州歌', '凉州'], '5首', ''),
        ('伊州歌', ['伊州歌', '伊州'], '10首', '伊州令、伊州三台不是本页大曲全套'),
        ('陆州歌', ['陆州歌', '陆州'], '7首', ''),
        ('调笑令', ['调笑令'], '3体：10首、8首、12首', '本地仅王建古调笑体；本页为毛滂、乐府雅词无名氏、郑僅的联章体，仍缺失'),
        ('九张机', ['九张机'], '2体：11首、9首', ''),
        ('梅花曲', ['梅花曲'], '3首', '梅花引、江城梅花引不是本调'),
        ('薄媚', ['薄媚'], '10首', '本地薄媚摘遍为另列的摘遍，不能视为本页董颖大曲全套'),
    ]
    audit = []
    for name, spellings, count, note in entries:
        matches = [d['name'] for d in records if set(spellings) & {d['name'], *d.get('aliases', [])}]
        audit.append({'page_name': name, 'page_spellings': spellings, 'page_extent': count, 'local_matches': matches, 'status': '同名但本页体式缺失' if matches else '正名及别名均未收录', 'note': note})
    (folder / 'comparison.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report = ['# 《钦定词谱》卷四十附编核对', '', f'来源：{url}', '', f'核对本地 {len(records)} 个词牌文件的 name、aliases，并人工核对同名体式。', '', '本页九调十二体。八调未匹配到本地正名或别名；调笑令虽有同名文件，但仅收古调笑，本页三种联章体仍缺。这里只核对指定单页，未爬取整部词谱。', '', '| 本页名称 | 本页篇目 | 本地情况 | 说明 |', '| --- | --- | --- | --- |']
    for item in audit:
        report.append(f"| {item['page_name']} | {item['page_extent']} | {item['status']} | {item['note']} |")
    report += ['', '本页调笑令另见“调笑集句”“调笑转踏”名称，分别对应八首体和十二首体，不能直接作为本地王建古调笑体的别名。', '', '原始网页：29262.html；可见正文：29262.txt；结构化核对：comparison.json；抓取元信息：source.json。', '', '本次未修改 Songci_Meter。']
    (folder / 'comparison.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
