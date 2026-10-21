// Закладка «Собрать номера» (страница «Источники», static/sources.js).
// sources.js берёт этот файл, подставляет CFG (адрес CRM и стоп-лист) и делает из него ссылку javascript:,
// которую менеджер перетаскивает на панель закладок. Код выполняется на странице 2ГИС, Яндекс Карт или сайта
// компании: листает список, открывает карточки компаний тем же браузером и забирает название, телефон и сайт.
// На чужой странице нельзя innerHTML и атрибуты style (их запрещают CSP и Trusted Types): только createElement и .style.
(function () {
  'use strict';
  var CFG = __CFG__;
  if (window.__averonCollect) { window.__averonCollect.show(); return; }
  var LIMIT = 150;
  var host = location.hostname;
  var SITE = /(^|\.)2gis\.[a-z]+$/.test(host) ? '2gis'
    : (/(^|\.)yandex\.[a-z]+$/.test(host) && /^\/maps/.test(location.pathname)) ? 'ymaps' : 'site';
  var FIRM = SITE === '2gis' ? /\/firm\/(\d{6,})/ : SITE === 'ymaps' ? /\/org\/(?:[^\/?#]+\/)?(\d{5,})/ : null;
  var NAME_NOISE = /^\d|отзыв|оцен|фото|маршрут|показать|подробнее|реклама|ещё|еще|филиал/i;
  var state = {items: [], stop: false, done: false};

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function norm(s) { return String(s || '').toLowerCase().replace(/ё/g, 'е').replace(/[«»"'.,()]/g, ' ').replace(/\s+/g, ' ').trim(); }
  function normPhone(s) {
    var d = String(s || '').replace(/\D/g, '');
    if (d.length === 10) d = '7' + d;
    if (d.length === 11 && d[0] === '8') d = '7' + d.slice(1);
    return /^7\d{10}$/.test(d) ? d : '';
  }
  function normSite(s) {
    s = String(s || '').trim().toLowerCase().replace(/^https?:\/\//, '').replace(/^www\./, '');
    s = s.split(/[\/?#\s]/)[0];
    return /\./.test(s) && !/(^|\.)(2gis|yandex|ya)\.[a-z]+$/.test(s) ? s : '';
  }
  function fmt(p) { return p ? '+7 ' + p.slice(1, 4) + ' ' + p.slice(4, 7) + '-' + p.slice(7, 9) + '-' + p.slice(9) : ''; }
  function stopOf(it) {
    var hay = ' ' + norm(it.n) + ' ' + norm(it.s) + ' ';
    for (var i = 0; i < CFG.stop.length; i++) {
      var p = norm(CFG.stop[i][0]);
      if (!p) continue;
      var hit = p.length <= 4
        ? new RegExp('(^|[\\s.,])' + p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '([\\s.,]|$)').test(hay)
        : hay.indexOf(p) >= 0;
      if (hit) return CFG.stop[i][0];
    }
    return '';
  }
  function query() {
    try {
      if (SITE === '2gis') { var m = location.pathname.match(/\/search\/([^\/]+)/); return m ? decodeURIComponent(m[1]) : ''; }
      if (SITE === 'ymaps') return new URLSearchParams(location.search).get('text') || '';
    } catch (e) { /* кривой адрес */ }
    return '';
  }

  // ---------- панель ----------
  function el(tag, css, text) {
    var e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (text != null) e.textContent = text;
    return e;
  }
  var C = {bg: '#151e1b', line: '#2a3732', fg: '#e6eeea', muted: '#93a39d', accent: '#3cc2a0', ink: '#06261e', bad: '#ef7565'};
  var BTN = 'border:0;border-radius:8px;padding:8px 12px;font:600 13px system-ui,sans-serif;cursor:pointer;';
  var panel = el('div', 'position:fixed;top:12px;right:12px;width:340px;max-width:calc(100vw - 24px);z-index:2147483647;' +
    'background:' + C.bg + ';color:' + C.fg + ';border:1px solid ' + C.line + ';border-radius:12px;padding:12px 14px;' +
    'box-shadow:0 12px 30px rgba(0,0,0,.45);font:13px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;text-align:left');
  var head = el('div', 'font-weight:600;font-size:13.5px;margin-bottom:2px');
  head.appendChild(document.createTextNode('Averon '));
  head.appendChild(el('span', 'color:' + C.accent, '· сбор номеров'));
  var bar = el('div', 'height:6px;background:' + C.line + ';border-radius:4px;margin:8px 0 4px;overflow:hidden');
  var fill = el('i', 'display:block;height:100%;width:3%;background:' + C.accent + ';transition:width .3s');
  bar.appendChild(fill);
  var status = el('div', 'color:' + C.muted + ';font-size:12px', 'Ищу компании на странице…');
  var list = el('div', 'margin-top:8px;max-height:45vh;overflow:auto');
  var row = el('div', 'display:flex;gap:8px;margin-top:10px;flex-wrap:wrap');
  var send = el('button', BTN + 'background:' + C.accent + ';color:' + C.ink + ';flex:1', 'Отправить в подборщик');
  var stopBtn = el('button', BTN + 'background:none;color:' + C.fg + ';border:1px solid ' + C.line, 'Остановить');
  var close = el('button', BTN + 'background:none;color:' + C.muted + ';border:1px solid ' + C.line, 'Закрыть');
  send.disabled = true; send.style.opacity = '.5';
  row.appendChild(send); row.appendChild(stopBtn); row.appendChild(close);
  panel.appendChild(head); panel.appendChild(bar); panel.appendChild(status); panel.appendChild(list); panel.appendChild(row);
  document.body.appendChild(panel);
  close.onclick = function () { state.stop = true; panel.remove(); window.__averonCollect = null; };
  stopBtn.onclick = function () { state.stop = true; stopBtn.disabled = true; stopBtn.textContent = 'Останавливаю…'; };
  window.__averonCollect = {show: function () { if (!panel.isConnected) document.body.appendChild(panel); }};

  function progress(part, text) { fill.style.width = Math.max(3, Math.min(100, Math.round(part * 100))) + '%'; status.textContent = text; }

  function checked() { return state.items.filter(function (it) { return it.on && (it.p || it.s); }); }
  function refreshSend() {
    var n = checked().length;
    send.textContent = 'Отправить в подборщик (' + n + ')';
    send.disabled = !n; send.style.opacity = n ? '1' : '.5';
  }
  function drawRow(it) {
    var r = it.row || (it.row = el('label', 'display:flex;gap:8px;align-items:center;padding:6px 0;border-top:1px dashed ' + C.line + ';cursor:pointer'));
    while (r.firstChild) r.removeChild(r.firstChild);
    var box = el('input'); box.type = 'checkbox'; box.checked = it.on; box.disabled = !(it.p || it.s);
    box.onchange = function () { it.on = box.checked; refreshSend(); };
    var name = el('span', 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' +
      (it.stop ? ';color:' + C.bad + ';text-decoration:line-through' : ''), it.n || it.s || 'без названия');
    name.title = it.n || '';
    var tail = it.stop ? 'стоп' : it.p ? '…' + it.p.slice(-4) : it.s ? it.s : it.wait ? '…' : 'нет телефона';
    r.appendChild(box); r.appendChild(name);
    r.appendChild(el('span', 'font:12px ui-monospace,Menlo,Consolas,monospace;color:' + C.muted + ';white-space:nowrap', tail));
    if (!r.isConnected) list.appendChild(r);
  }

  // ---------- сбор ----------
  function goodName(t) { return t.length > 1 && t.length < 120 && !NAME_NOISE.test(t); }
  function firmsOnPage() {
    var seen = {}, out = [];
    [].forEach.call(document.querySelectorAll('a[href]'), function (a) {
      if (panel.contains(a)) return;
      var m = (a.getAttribute('href') || '').match(FIRM);
      if (!m) return;
      var f = seen[m[1]];
      if (!f) { f = seen[m[1]] = {id: m[1], url: a.href.split('?')[0].split('#')[0], n: '', a: a}; out.push(f); }
      var t = (a.textContent || '').replace(/\s+/g, ' ').trim();
      if (!f.n && goodName(t)) f.n = t;
    });
    return out;
  }
  function scroller(a) {
    for (var e = a && a.parentElement; e && e !== document.body; e = e.parentElement) {
      var o = getComputedStyle(e).overflowY;
      if ((o === 'auto' || o === 'scroll') && e.scrollHeight > e.clientHeight + 20) return e;
    }
    return document.scrollingElement;
  }
  async function scrollList() {
    var last = -1, same = 0, firms = firmsOnPage();
    for (var i = 0; i < 40 && same < 3 && !state.stop; i++) {
      if (firms.length >= LIMIT) break;
      if (firms.length === last) same++; else { same = 0; last = firms.length; }
      progress(0.05 + i / 400, 'Листаю список: найдено ' + firms.length + '…');
      var sc = firms.length ? scroller(firms[firms.length - 1].a) : null;
      if (!sc) break;
      sc.scrollTop = sc.scrollHeight;
      await sleep(900);
      firms = firmsOnPage();
    }
    return firms.slice(0, LIMIT);
  }

  // Телефон и сайт из страницы компании. 2ГИС и Яндекс кладут данные карточки в JSON внутри страницы.
  function phonesIn(text) {
    var out = [], re = /(?:\+7|\b8)[\s(\-‒–]*\d{3,4}[\s)\-‒–]*\d{2,3}[\s\-‒–]*\d{2}[\s\-‒–]*\d{2}(?!\d)/g, m;
    while ((m = re.exec(text))) { var p = normPhone(m[0]); if (p && out.indexOf(p) < 0) out.push(p); }
    return out;
  }
  function block(t, key, len) { var i = t.indexOf(key); return i < 0 ? '' : t.slice(i, i + len); }
  function parsePage(html) {
    var t = html.replace(/\\"/g, '"').replace(/\\u002[Ff]/g, '/'), r = {p: '', s: '', n: ''}, b, m;
    if (SITE === '2gis' && (b = block(t, '"contact_groups"', 8000))) {
      r.p = phonesIn(b)[0] || '';
      m = b.match(/"type":"website"[^{}]*?"text":"([^"]+)"/) || b.match(/"text":"([^"]+)"[^{}]*?"type":"website"/);
      if (m) r.s = normSite(m[1]);
    }
    if (SITE === 'ymaps') {
      if ((b = block(t, '"phones":[', 3000))) r.p = phonesIn(b.slice(0, b.indexOf(']') + 1 || 3000))[0] || '';
      if ((b = block(t, '"urls":[', 600)) && (m = b.match(/"(https?:\/\/[^"]+)"/))) r.s = normSite(m[1]);
    }
    if (!r.p) { m = t.match(/href="tel:([^"]+)"/); if (m) r.p = normPhone(decodeURIComponent(m[1])); }
    m = t.match(/<h1[^>]*>([\s\S]*?)<\/h1>/);
    if (m) r.n = m[1].replace(/<[^>]+>/g, ' ').replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/&#39;/g, "'").replace(/\s+/g, ' ').trim();
    return r;
  }
  async function contacts(f) {
    var res = await fetch(f.url, {credentials: 'include'});
    var html = await res.text();
    if (/captcha/i.test(res.url) || (/showcaptcha|smartcaptcha/i.test(html) && html.length < 60000)) throw new Error('captcha');
    return parsePage(html);
  }
  // Сайт компании или открытая карточка без списка: берём телефоны со страницы как есть.
  function thisPage() {
    var p = '';
    [].some.call(document.querySelectorAll('a[href^="tel:"]'), function (a) { p = normPhone(decodeURIComponent(a.getAttribute('href').slice(4))); return !!p; });
    if (!p) p = phonesIn(document.body.innerText || '')[0] || '';
    var og = document.querySelector('meta[property="og:site_name"]');
    var h1 = document.querySelector('h1');
    var n = (og && og.content) || (h1 && h1.textContent) || document.title || host;
    return {n: n.replace(/\s+/g, ' ').trim().slice(0, 120), p: p, s: SITE === 'site' ? normSite(host) : ''};
  }

  function report(firms) {
    return ['Averon сбор номеров: отчёт', 'адрес: ' + location.href, 'сайт: ' + SITE, 'компаний в списке: ' + firms.length,
      'с телефоном: ' + state.items.filter(function (i) { return i.p; }).length,
      'ссылок tel: ' + document.querySelectorAll('a[href^="tel:"]').length,
      'ссылок /firm/: ' + document.querySelectorAll('a[href*="/firm/"]').length + ', /org/: ' + document.querySelectorAll('a[href*="/org/"]').length,
      'браузер: ' + navigator.userAgent].join('\n');
  }

  async function run() {
    var firms = FIRM ? await scrollList() : [];
    if (!firms.length) {
      var it = thisPage();
      it.on = !!(it.p || it.s); it.stop = stopOf(it); if (it.stop) it.on = false;
      if (it.p || it.s) { state.items.push(it); drawRow(it); }
    } else {
      firms.forEach(function (f) { state.items.push({n: f.n, p: '', s: '', on: false, wait: true, f: f}); });
      state.items.forEach(drawRow);
      for (var i = 0; i < state.items.length && !state.stop; i++) {
        var item = state.items[i];
        progress(0.1 + 0.9 * i / state.items.length, 'Открываю карточки: ' + (i + 1) + ' из ' + state.items.length + '…');
        try {
          var c = await contacts(item.f);
          item.p = c.p; item.s = c.s; if (!item.n) item.n = c.n;
        } catch (e) {
          if (e.message === 'captcha') { state.captcha = true; break; }
        }
        item.wait = false; item.stop = stopOf(item); item.on = !!(item.p || item.s) && !item.stop;
        drawRow(item); refreshSend();
        await sleep(SITE === 'ymaps' ? 700 : 350);
      }
      state.items.forEach(function (x) { if (x.wait) { x.wait = false; drawRow(x); } });
    }
    var withPhone = state.items.filter(function (x) { return x.p; }).length;
    var stops = state.items.filter(function (x) { return x.stop; }).length;
    fill.style.width = '100%';
    var captcha = state.captcha ? ' Сайт попросил проверку «я не робот»: пройдите её на этой вкладке и нажмите закладку ещё раз.' : '';
    if (state.captcha && !checked().length) {
      status.textContent = captcha.trim();
    } else if (!state.items.length || (!withPhone && !checked().length)) {
      status.textContent = 'Не нашёл компаний с телефоном. Откройте поиск в 2ГИС или Яндекс Картах из подборщика и нажмите ещё раз. ' +
        'Если поиск открыт, скопируйте отчёт и пришлите его Claude.';
      var copy = el('button', BTN + 'background:none;color:' + C.fg + ';border:1px solid ' + C.line, 'Скопировать отчёт');
      copy.onclick = function () { navigator.clipboard.writeText(report(firms)).then(function () { copy.textContent = 'Скопировано'; }); };
      row.insertBefore(copy, close);
    } else {
      status.textContent = 'Готово: ' + state.items.length + ' ' + (state.items.length === 1 ? 'компания' : 'компаний') + ', у ' + withPhone +
        ' есть телефон' + (stops ? ', ' + stops + ' в стоп-листе' : '') + '.' + captcha + (SITE === '2gis' ? ' Следующую страницу 2ГИС соберите так же: дубли подборщик уберёт.' : '');
    }
    stopBtn.remove();
    refreshSend();
  }

  send.onclick = function () {
    var items = checked().map(function (x) { return {n: x.n, p: x.p, s: x.s}; });
    var payload = {v: 1, src: SITE, q: query(), items: items};
    var w = window.open(CFG.crm + '/?work#sources/import:' + encodeURIComponent(JSON.stringify(payload)), 'averon-crm');
    status.textContent = w ? 'Отправлено ' + items.length + '. Номера уже в подборщике.' : 'Браузер не дал открыть CRM: разрешите всплывающие окна для этого сайта и нажмите ещё раз.';
  };

  run().catch(function (e) { status.textContent = 'Ошибка: ' + e.message; });
})();
