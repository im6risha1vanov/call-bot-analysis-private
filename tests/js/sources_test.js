// Проверка страницы «Источники» без браузера: логика подборщика и закладка «Собрать номера»
// на поддельной странице 2ГИС и Яндекс Карт. Запуск: node tests/js/sources_test.js (его зовёт tests/test_crm_sources_js.py).
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const STATIC = path.join(__dirname, '..', '..', 'crm', 'static');
globalThis.window = globalThis;
require(path.join(STATIC, 'sources-data.js'));
const L = require(path.join(STATIC, 'sources-logic.js'));

// ---- логика ----
const realty = L.byId('realty-new', SP_NICHES);
assert.strictEqual(L.nicheForQuery('агентство недвижимости новостройки Новороссийск', 'Новороссийск', SP_NICHES), 'realty-new');
assert.strictEqual(L.nicheForQuery('кухни на заказ недорого Краснодар', 'Краснодар', SP_NICHES), 'kitchen');
assert.strictEqual(L.catForQuery(realty, 'ипотечный брокер Новороссийск'), 2);
assert.strictEqual(L.catForQuery(realty, 'что-то своё'), 0);
const got = L.importItems({src: '2gis', q: 'ипотечный брокер Новороссийск', items: [
  {n: 'Брокер', p: '8 (918) 123-45-67'}, {n: 'Брокер дубль', p: '+79181234567'}, {n: 'Уже есть', p: '79990000000'},
  {n: 'Циан', p: '88005553535', s: 'https://www.cian.ru/x'}, {n: 'Пусто'}, null]}, [{phone: '79990000000'}], realty);
assert.deepStrictEqual(got.added.map(d => [d.name, d.phone, d.site, d.cat, d.from]),
  [['Брокер', '79181234567', '', 2, '2gis'], ['Циан', '78005553535', 'cian.ru', 2, '2gis']]);
assert.deepStrictEqual([got.dup, got.bad], [2, 2]);
assert.strictEqual(L.evaluate(got.added[0], realty, '').level, 'good');
assert.strictEqual(L.evaluate(got.added[1], realty, '').label, 'стоп-лист');

// ---- закладка на поддельной странице ----
class El {
  constructor(tag) { this.tag = tag; this.style = {}; this.children = []; this.parentElement = null; this._text = ''; }
  appendChild(c) { if (c.parentElement) c.parentElement.removeChild(c); c.parentElement = this; this.children.push(c); return c; }
  insertBefore(c, ref) { c.parentElement = this; this.children.splice(Math.max(0, this.children.indexOf(ref)), 0, c); return c; }
  removeChild(c) { this.children = this.children.filter(x => x !== c); c.parentElement = null; }
  remove() { if (this.parentElement) this.parentElement.removeChild(this); }
  get firstChild() { return this.children[0] || null; }
  get isConnected() { let e = this; while (e.parentElement) e = e.parentElement; return e.tag === 'body'; }
  contains(x) { while (x) { if (x === this) return true; x = x.parentElement; } return false; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  all() { return [this].concat(...this.children.map(c => c.all())); }
}

function page(host, pathname, search, anchors, pages) {
  const body = new El('body');
  const links = anchors.map(([href, text]) => Object.assign(new El('a'), {
    getAttribute: () => href, href: 'https://' + host + href, textContent: text, parentElement: body}));
  const opened = [];
  const doc = {
    body, scrollingElement: {scrollTop: 0, scrollHeight: 1000},
    createElement: t => new El(t), createTextNode: t => Object.assign(new El('#text'), {textContent: t}),
    querySelectorAll: sel => {
      if (sel === 'a[href]') return links;
      const m = sel.match(/^a\[href([\^*])="([^"]+)"\]$/);
      return m ? links.filter(a => m[1] === '^' ? a.getAttribute().startsWith(m[2]) : a.getAttribute().includes(m[2])) : [];
    },
    querySelector: () => null,
  };
  const box = {
    document: doc, location: {hostname: host, pathname, search, href: 'https://' + host + pathname + search},
    navigator: {userAgent: 'test', clipboard: {writeText: async () => {}}},
    getComputedStyle: () => ({overflowY: 'visible'}), URLSearchParams,
    setTimeout: f => setImmediate(f), Promise, JSON, Math, RegExp, Error, String,
    fetch: async url => { const html = pages[url]; if (html == null) throw new Error('нет страницы ' + url); return {url, text: async () => html}; },
    open: (url, name) => { opened.push([url, name]); return {}; },
  };
  box.window = box;
  return {box, body, opened};
}

function bookmarklet() {
  const stop = [['Циан'], ['Сбер']];
  // Так же, как sources.js собирает ссылку javascript:
  const code = fs.readFileSync(path.join(STATIC, 'sources-collect.js'), 'utf8').split('\n').map(l => l.trim())
    .filter(l => l && !l.startsWith('//')).join('\n').replace('__CFG__', () => JSON.stringify({crm: 'https://crm.test', stop}));
  const href = 'javascript:' + encodeURIComponent(code);
  return decodeURIComponent(href.slice('javascript:'.length));
}

async function collect(p) {
  vm.runInNewContext(bookmarklet(), p.box);
  const panel = p.body.children[0];
  for (let i = 0; i < 400 && !/Готово|Не нашёл|Ошибка/.test(panel.textContent); i++) await new Promise(r => setImmediate(r));
  const send = panel.all().find(e => e.tag === 'button' && e.textContent.startsWith('Отправить'));
  return {panel, send};
}

const firm = (phone, site, other) => '<html><h1 class="x">Неважно</h1><script>var initialState = JSON.parse("{\\"data\\":{\\"contact_groups\\":[{\\"contacts\\":[' +
  `{\\"type\\":\\"phone\\",\\"value\\":\\"${phone}\\",\\"text\\":\\"+7 (8617) 60‒12‒34\\"},` +
  (site ? `{\\"type\\":\\"website\\",\\"url\\":\\"http://link.2gis.ru/abc\\",\\"text\\":\\"${site}\\"}` : '{}') +
  ']}]},\\"other\\":\\"' + (other || '') + '\\"}")</script></html>';

(async () => {
  // 2ГИС: две компании, у одной ссылка на отзывы, третья — стоп-лист.
  const g = page('2gis.ru', '/novorossiysk/search/' + encodeURIComponent('кухни на заказ Новороссийск'), '', [
    ['/novorossiysk/firm/70000001011111111?m=1', 'Кухни Юг'], ['/novorossiysk/firm/70000001011111111/tab/reviews', '12 отзывов'],
    ['/novorossiysk/firm/70000001022222222', 'Мебельная фабрика «Марья»'], ['/novorossiysk/firm/70000001033333333', 'Циан'],
  ], {
    'https://2gis.ru/novorossiysk/firm/70000001011111111': firm('+78617601234', 'kuhni-yug.ru', '+79990001122'),
    'https://2gis.ru/novorossiysk/firm/70000001022222222': firm('+79181112233', 'www.marya.ru'),
    'https://2gis.ru/novorossiysk/firm/70000001033333333': firm('+78005553535', 'cian.ru'),
  });
  const r = await collect(g);
  assert.match(r.panel.textContent, /Готово: 3 компаний, у 3 есть телефон, 1 в стоп-листе/);
  assert.match(r.send.textContent, /\(2\)/);
  r.send.onclick();
  assert.strictEqual(g.opened.length, 1);
  const [url, name] = g.opened[0];
  assert.strictEqual(name, 'averon-crm');
  assert.ok(url.startsWith('https://crm.test/?work#sources/import:'));
  const sent = JSON.parse(decodeURIComponent(url.split('#')[1]).slice('sources/import:'.length));
  assert.deepStrictEqual(sent, {v: 1, src: '2gis', q: 'кухни на заказ Новороссийск', items: [
    {n: 'Кухни Юг', p: '78617601234', s: 'kuhni-yug.ru'}, {n: 'Мебельная фабрика «Марья»', p: '79181112233', s: 'marya.ru'}]});

  // Яндекс Карты: телефоны в "phones", сайт в "urls"; вторая карточка без телефона, но со ссылкой tel:.
  const y = page('yandex.ru', '/maps/', '?text=' + encodeURIComponent('ремонт квартир Анапа'), [
    ['/maps/org/remont_pro/1234567890/', 'Ремонт Про'], ['/maps/org/remont_pro/1234567890/reviews/', 'Отзывы'],
    ['/maps/org/dom/2234567890/', 'Дом под ключ'],
  ], {
    'https://yandex.ru/maps/org/remont_pro/1234567890/': '<script>{"phones":[{"number":"+7 (918) 765-43-21","type":"phone"}],"x":["+7 (999) 000-00-00"],"urls":["https://remont-pro.ru/"]}</script>',
    'https://yandex.ru/maps/org/dom/2234567890/': '<a href="tel:+78613300000">позвонить</a>',
  });
  const r2 = await collect(y);
  r2.send.onclick();
  const sent2 = JSON.parse(decodeURIComponent(y.opened[0][0].split('#')[1]).slice('sources/import:'.length));
  assert.deepStrictEqual(sent2.items, [{n: 'Ремонт Про', p: '79187654321', s: 'remont-pro.ru'}, {n: 'Дом под ключ', p: '78613300000', s: ''}]);
  assert.strictEqual(sent2.q, 'ремонт квартир Анапа');

  // Капча Яндекса: останавливаемся и говорим, что делать.
  const c = page('yandex.ru', '/maps/', '?text=x', [['/maps/org/a/1234567890/', 'А']],
    {'https://yandex.ru/maps/org/a/1234567890/': '<form action="/showcaptcha">'});
  const r3 = await collect(c);
  assert.match(r3.panel.textContent, /я не робот/);

  console.log('ok');
})().catch(e => { console.error(e); process.exit(1); });
