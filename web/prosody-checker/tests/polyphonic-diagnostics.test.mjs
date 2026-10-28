import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { buildLexicon, evaluateTang } from '../core.js';

const book = buildLexicon(JSON.parse(readFileSync(new URL('../../../Rhyme/Pinshui.json', import.meta.url), 'utf8')));
const app = readFileSync(new URL('../app.js', import.meta.url), 'utf8');
const site = readFileSync(new URL('../site.js', import.meta.url), 'utf8');
const suffix = '，顽痴自乐拾微轻。扑腾滑跌声含涩，憨拙方知趣未平。';

function renderDiagnostics(result) {
  const createNode = (tag) => ({
    tag, children: [], textContent: '', classList: { toggle() {} },
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    querySelectorAll() { return []; },
  });
  const panel = createNode('section');
  const groups = new Map();
  const code = app.slice(app.indexOf('function renderDetectionSummary('), app.indexOf('function charCountLabel('));
  vm.runInNewContext(code + '\nrenderDetectionSummary(result);', {
    result,
    elements: { detectionSummary: panel, aoJiuLegend: {}, polyphonicInputs: [{ checked: true, value: 'automatic' }] },
    document: { createElement: createNode },
    appendSummaryGroup(_panel, title, messages) { groups.set(title, messages); },
  });
  const list = panel.children.at(-1).children.find((node) => node.tag === 'ul');
  return { decisions: groups.get('多音字判定'), errors: list?.children.map((node) => node.textContent) ?? [] };
}

function inspectorDiagnostics(result) {
  const code = site.slice(site.indexOf('function violationMessages('), site.indexOf('function errorPositions('));
  return Array.from(vm.runInNewContext(code + '\nscoreDeductionMessages(evaluation);', {
    evaluation: { result, tonal_score: result.tonalScore, rhyme_score: result.rhymeScore },
  }));
}

test('实际页面渲染和作品检查器复用同一条多音条件错误，不泄漏分支错误', () => {
  const result = evaluateTang('圆腹短翘戏石横' + suffix,
    { charCount: 7, form: 'jueju', polyphonicMode: 'automatic', allowAoJiu: true }, book);
  const rendered = renderDiagnostics(result);
  const expected = '第 1 句第 7 字“横”：若为仄声，第 5 字应平；若为平声，第 3 字应平。';
  assert.equal(rendered.decisions[0], expected);
  assert.deepEqual(rendered.errors, [expected]);
  assert.deepEqual(inspectorDiagnostics(result), [expected]);
  assert.ok(result.firstLineAlternatives.some(({ result: branch }) => branch.violations.some((item) => item.rule === '孤平')));
});

test('有可行读法时，页面和作品检查器都不列为确定错误', () => {
  for (const first of ['圆腹春翘戏石横', '圆腹短翘风石横']) {
    const result = evaluateTang(first + suffix,
      { charCount: 7, form: 'jueju', polyphonicMode: 'automatic', allowAoJiu: true }, book);
    const rendered = renderDiagnostics(result);
    assert.ok(rendered.decisions.length);
    assert.deepEqual(rendered.errors, []);
    assert.deepEqual(inspectorDiagnostics(result), []);
  }
});
