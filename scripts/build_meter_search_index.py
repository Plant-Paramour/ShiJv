"""生成词谱说明搜索索引；修改词谱说明后可重新运行。"""
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1] / 'Songci_Meter'
    catalog = json.loads((root / 'index.json').read_text(encoding='utf-8'))
    items = {}
    for item in catalog:
        data = json.loads((root / item['file']).read_text(encoding='utf-8'))
        items[item['file']] = {
            'description': data.get('description', ''),
            'variants': [{'source_id': v['source_id'], 'description': v.get('variants_description', '')}
                         for v in data.get('variants', [])],
        }
    (root / 'search-index.json').write_text(json.dumps({'schema_version': 1, 'items': items}, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    print(f'Indexed {len(items)} meters')


if __name__ == '__main__':
    main()
