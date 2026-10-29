import json
from pathlib import Path

from scripts.extract_meter_layouts import extract


def test_layout_keeps_source_rows_punctuation_and_internal_read():
    html = '<div id="gl">例词<div class="pu">平平、仄仄，平仄。<br></div><div class="pu">平平！</div></div>'
    variant = {'stanza1': {'lines': ['平平、仄仄', '平仄', '平平']}}
    result = extract(html, variant)['stanza1']
    assert result == [
        {'source_row': 0, 'sentences': [{'line': 1, 'punctuation': '，'}, {'line': 2, 'punctuation': '。'}]},
        {'source_row': 1, 'sentences': [{'line': 3, 'punctuation': '！'}]},
    ]


def test_parenthetical_annotation_does_not_split_sentence():
    html = '<div id="gl"><div class="pu">平平(竹枝)仄平。</div></div>'
    assert extract(html, {'stanza1': {'lines': ['平平仄平']}})['stanza1'][0]['sentences'] == [{'line': 1, 'punctuation': '。'}]


def test_generated_layout_covers_every_variant_and_sentence():
    root = Path(__file__).resolve().parents[1] / 'Songci_Meter'
    layouts = json.loads((root / 'display-layouts.json').read_text(encoding='utf-8'))['items']
    for item in json.loads((root / 'index.json').read_text(encoding='utf-8')):
        data = json.loads((root / item['file']).read_text(encoding='utf-8'))
        for variant in data['variants']:
            stanzas = layouts[data['name']][str(variant['source_id'])]['stanzas']
            for key, stanza in variant.items():
                if key.startswith('stanza') and isinstance(stanza, dict) and 'lines' in stanza:
                    numbers = [sentence['line'] for row in stanzas[key] for sentence in row['sentences']]
                    assert numbers == list(range(1, len(stanza['lines']) + 1)), (data['name'], variant['source_id'], key)
