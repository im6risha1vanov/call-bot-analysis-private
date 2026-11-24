// Страница «Источники»: подбор доноров под нишу клиента (из podbor-istochnikov.html Григория) и закладка
// «Собрать номера» для 2ГИС и Яндекс Карт (static/sources-collect.js). Справочник ниш — sources-data.js,
// логика без интерфейса — sources-logic.js. Черновик подборки живёт в браузере, сохранённые подборки — в CRM.
// Работает поверх app.js (его помощники api, toast, go, S).
'use strict';

(() => {
  const L = window.SPLogic, NICHES = window.SP_NICHES;
  const DRAFT_KEY = 'sp-draft-v1';
  const HEAD = ['Донор', 'Категория', 'Телефон', 'Сайт', 'Что парсить', 'Оценка'];
  const blank = () => ({query: '', nicheId: '', city: '', client: '', seg: '', donors: [], savedId: null});
  let state = blank(), niche = null, saved = [], bookmarklet = '';
  const g = id => document.getElementById(id);
  const h = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const lsGet = k => { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (e) { return null; } };
  const saveDraft = () => { try { localStorage.setItem(DRAFT_KEY, JSON.stringify(state)); } catch (e) { /* без хранилища */ } };
  const loadDraft = () => {
    const dr = lsGet(DRAFT_KEY);
    state = Object.assign(blank(), dr && typeof dr === 'object' ? dr : {});
    if (!Array.isArray(state.donors)) state.donors = [];
  };
  const newId = () => Date.now() + Math.random();

  const PAGE = `<div id="sp-app">
  <p class="sp-sub">Укажите нишу клиента и город. Страница покажет, где искать доноров, даст готовые ссылки на поиск и поможет отобрать 8–15 сильных доноров для проекта.</p>
  <div class="sp-card">
    <h2><span class="sp-step">1</span>Клиент</h2>
    <div class="sp-grid">
      <div><label for="sp-niche">Ниша или код ОКВЭД</label><input id="sp-niche" placeholder="например: кухни на заказ, 69.20, ремонт квартир" autocomplete="off"></div>
      <div><label for="sp-city">Город или регион</label><input id="sp-city" list="sp-cities" placeholder="Новороссийск"><datalist id="sp-cities"></datalist></div>
      <div><label for="sp-client">Клиент (чтобы не взять его самого донором)</label><input id="sp-client" placeholder="название или сайт"></div>
    </div>
    <div class="sp-sugg" id="sp-sugg"></div>
    <div class="sp-row sp-mt12">
      <span class="sp-mode">Сегмент:</span>
      <span class="sp-seg" id="sp-seg"><button type="button" data-seg="" class="sp-active">Любой</button><button type="button" data-seg="eco">Эконом</button><button type="button" data-seg="prem">Премиум</button></span>
    </div>
  </div>
  <div class="sp-card sp-hidden" id="sp-mapCard">
    <h2><span class="sp-step">2</span>Где искать доноров</h2>
    <div class="sp-bm">
      <a class="sp-bm-btn" id="sp-bm" href="#" draggable="true" title="Перетащите на панель закладок">★ Собрать номера</a>
      <div><b>Номера соберёт закладка.</b> Один раз перетащите кнопку мышкой на панель закладок браузера (показать панель: Ctrl+Shift+B).
        Потом откройте ссылку «2ГИС» или «Я.Карты» у нужного запроса и нажмите закладку: она сама пройдёт по компаниям, заберёт название, телефон и сайт и пришлёт их сюда. Стоп-лист зачёркнут сразу.</div>
    </div>
    <div id="sp-tip"></div>
    <div id="sp-proven"></div>
    <div class="sp-cats" id="sp-cats"></div>
    <div class="sp-note sp-info" id="sp-sigNote"></div>
    <div class="sp-note sp-err" id="sp-stopNote"></div>
  </div>
  <div class="sp-card sp-hidden" id="sp-addCard">
    <h2><span class="sp-step">3</span>Добавить донора вручную</h2>
    <label for="sp-paste">Вставьте карточку компании из Яндекс Карт, 2ГИС или с сайта. Название, телефон и сайт разберутся сами.</label>
    <textarea id="sp-paste" rows="3" placeholder="Кухни Мария&#10;+7 (8617) 00-00-00&#10;marya.ru"></textarea>
    <div class="sp-grid4 sp-mt10">
      <div><label for="sp-dName">Название донора</label><input id="sp-dName"></div>
      <div><label for="sp-dPhone">Телефон (звонки, СМС)</label><input id="sp-dPhone" inputmode="tel"></div>
      <div><label for="sp-dSite">Сайт</label><input id="sp-dSite" placeholder="site.ru"></div>
      <div><label for="sp-dCat">Категория</label><select id="sp-dCat"></select></div>
    </div>
    <div class="sp-checks" id="sp-checks">
      <label><input type="checkbox" data-c="0"><span>Продаёт ту же услугу, что наш клиент (не софт, не банк, не справочник)</span></label>
      <label><input type="checkbox" data-c="1"><span>К нему обращаются в момент покупки: заявка, расчёт, замер, запись</span></label>
      <label><input type="checkbox" data-c="2"><span>Тот же город и похожий ценовой сегмент</span></label>
      <label><input type="checkbox" data-c="3"><span>Не федеральный гигант со своей лояльной базой</span></label>
    </div>
    <div class="sp-row">
      <button class="sp-btn sp-primary" id="sp-addBtn" type="button">Добавить в подборку</button>
      <span class="sp-hint" id="sp-addHint"></span>
    </div>
  </div>
  <div class="sp-card sp-hidden" id="sp-listCard">
    <h2><span class="sp-step">4</span>Подборка для проекта</h2>
    <div class="sp-kpis" id="sp-kpis"></div>
    <div id="sp-warns"></div>
    <div class="sp-scroll"><table class="sp-table" id="sp-table"></table></div>
    <div class="sp-row sp-mt14">
      <button class="sp-btn sp-primary" id="sp-copyBtn" type="button">Скопировать для проекта</button>
      <button class="sp-btn" id="sp-csvBtn" type="button">Скачать CSV</button>
      <button class="sp-btn" id="sp-saveBtn" type="button">Сохранить подборку</button>
      <button class="sp-btn" id="sp-resetBtn" type="button">Начать заново</button>
      <span class="sp-hint" id="sp-outHint"></span>
    </div>
    <div class="sp-mode sp-mt10">Черновик хранится в этом браузере. «Сохранить подборку» кладёт её в CRM: её увидят коллеги и её можно открыть снова.</div>
  </div>
  <div class="sp-card sp-hidden" id="sp-savedCard">
    <h2>Сохранённые подборки</h2>
    <div class="sp-scroll"><table class="sp-table" id="sp-saved"></table></div>
  </div>
</div>`;

  // ---------- шаг 1 ----------
  function renderSugg() {
    const list = L.findNiches(state.query, NICHES);
    let html = list.slice(0, 8).map(n =>
      `<button type="button" class="sp-chip${n.id === state.nicheId ? ' sp-active' : ''}" data-id="${n.id}">${h(n.name)}<small>${n.group}</small></button>`).join('');
    if (state.query.trim().length > 2) html += `<button type="button" class="sp-chip${state.nicheId === 'custom' ? ' sp-active' : ''}" data-id="custom">Своя ниша: «${h(state.query.trim())}»</button>`;
    if (!state.query.trim()) html = `<span class="sp-hint">Начните вводить нишу или код ОКВЭД, появятся варианты. В справочнике ${NICHES.length} ниш.</span>`;
    else if (!list.length) html = '<span class="sp-hint">В справочнике такой ниши нет.</span> ' + html;
    g('sp-sugg').innerHTML = html;
  }
  function pickNiche(id) {
    state.nicheId = id;
    if (id && id !== 'custom') {
      const n = L.byId(id, NICHES);
      if (n && !L.findNiches(state.query, [n]).length) { state.query = n.name; g('sp-niche').value = n.name; }
    }
    renderSugg(); render(); saveDraft();
  }

  // ---------- шаг 2 ----------
  function currentNiche() {
    if (!state.nicheId) return null;
    if (state.nicheId === 'custom') return L.genericNiche(state.query.trim());
    return L.byId(state.nicheId, NICHES);
  }
  function render() {
    niche = currentNiche();
    const on = !!niche;
    ['sp-mapCard', 'sp-addCard', 'sp-listCard'].forEach(id => g(id).classList.toggle('sp-hidden', !on));
    if (!on) return;
    g('sp-tip').innerHTML = (niche.tip ? `<div class="sp-note sp-ok sp-top">${h(niche.tip)}</div>` : '') +
      (state.city ? '' : '<div class="sp-note sp-warn sp-top">Укажите город: ссылки на поиск будут искать по нему.</div>');
    g('sp-cats').innerHTML = niche.cats.map(c => {
      const qs = c.q.map(q => {
        const full = L.withSeg(q, state.seg), lk = L.links(full, state.city);
        return `<div class="sp-q"><span>${h(full)}</span>` +
          `<a href="${h(lk.ymaps)}" target="_blank" rel="noopener">Я.Карты</a>` +
          `<a href="${h(lk.gis)}" target="_blank" rel="noopener">2ГИС</a>` +
          `<a href="${h(lk.yandex)}" target="_blank" rel="noopener">Яндекс</a>` +
          `<a href="${h(lk.avito)}" target="_blank" rel="noopener">Авито</a></div>`;
      }).join('');
      const sites = (c.sites || []).map(s => `<a href="https://${h(s)}" target="_blank" rel="noopener">${h(s)}</a>`).join(' ');
      return `<div class="sp-cat"><h3><span class="sp-badge sp-b-${c.t}">${L.TYPE_LABEL[c.t]}</span>${h(c.name)}</h3>` +
        `<p>${h(c.why)} Доля в подборке: ${L.TYPE_SHARE[c.t]}.</p>${qs}` +
        (sites ? `<div class="sp-q"><span>Площадки</span>${sites}</div>` : '') + '</div>';
    }).join('');
    g('sp-sigNote').innerHTML = '<b>Что парсить.</b> ' + h(L.signalAdvice(niche).text);
    g('sp-stopNote').innerHTML = '<b>Не брать донорами:</b><div class="sp-stoplist">' +
      L.stopList(niche).map(s => `<span title="${h(s[1])}">${h(s[0])}</span>`).join('') + '</div>';
    renderProven();
    g('sp-dCat').innerHTML = niche.cats.map((c, i) => `<option value="${i}">${h(L.TYPE_LABEL[c.t] + ': ' + c.name)}</option>`).join('');
    renderList();
  }
  function renderProven() {
    const items = niche.proven || [];
    if (!items.length) { g('sp-proven').innerHTML = ''; return; }
    g('sp-proven').innerHTML = '<div class="sp-note sp-ok sp-top"><b>Проверенные доноры этой ниши</b> (по прошлым прозвонам). Их стоит взять первыми, если город совпадает.' +
      '<table class="sp-table sp-mt6">' + items.map((p, i) =>
        `<tr><td>${h(p.name)}</td><td class="sp-mono">${h(L.fmtPhone(p.phone))}</td><td>${h(p.site || '')}</td><td>${h(p.note)}</td>` +
        `<td><button class="sp-btn sp-sm" type="button" data-proven="${i}">В подборку</button></td></tr>`).join('') + '</table></div>';
  }

  // ---------- шаг 3 ----------
  function addDonor(d) {
    const dup = state.donors.some(x => (d.phone && x.phone === d.phone) || (d.site && x.site === d.site));
    if (dup) { g('sp-addHint').textContent = 'Такой донор уже в подборке.'; return false; }
    d.id = newId();
    state.donors.push(d);
    g('sp-addHint').textContent = 'Добавлено: ' + (d.name || d.phone || d.site) + '.';
    renderList(); saveDraft();
    return true;
  }

  // ---------- шаг 4 ----------
  const evalDonor = d => L.evaluate(d, niche, state.client);
  const kpi = (n, t) => `<div class="sp-kpi"><b>${n}</b><span>${t}</span></div>`;
  const FROM = {'2gis': '2ГИС', ymaps: 'Я.Карты', site: 'сайт'};
  function renderList() {
    if (!niche) return;
    const rows = state.donors.map(d => ({d, e: evalDonor(d)}));
    const count = lvl => rows.filter(r => r.e.level === lvl).length;
    g('sp-kpis').innerHTML = kpi(rows.length, 'в подборке') + kpi(count('good'), 'сильных (берём)') + kpi(count('mid'), 'запасных') + kpi(count('bad'), 'отклонено');
    g('sp-warns').innerHTML = L.selectionWarnings(state.donors, niche, state.client).map(w => `<div class="sp-note sp-${w.kind}">${h(w.text)}</div>`).join('');
    if (!rows.length) {
      g('sp-table').innerHTML = '<tr><td class="sp-empty">Пока пусто. Откройте ссылки из шага 2 и нажмите закладку «Собрать номера» или добавьте доноров вручную.</td></tr>';
      return;
    }
    g('sp-table').innerHTML = '<tr><th>Донор</th><th>Категория</th><th>Телефон</th><th>Сайт</th><th>Что парсить</th><th>Оценка</th><th></th></tr>' +
      rows.map(r => {
        const c = niche.cats[r.d.cat] || niche.cats[0];
        return `<tr><td>${h(r.d.name || '—')}${r.d.from ? `<div class="sp-sig">из ${h(FROM[r.d.from] || r.d.from)}</div>` : ''}` +
          (r.e.reason ? `<div class="sp-sig sp-bad">${h(r.e.reason)}</div>` : '') + '</td>' +
          `<td><span class="sp-badge sp-b-${c.t}">${L.TYPE_LABEL[c.t]}</span></td>` +
          `<td class="sp-mono">${h(L.fmtPhone(r.d.phone))}</td><td>${h(r.d.site)}</td>` +
          `<td class="sp-sig">${h(L.signalsFor(r.d, niche).join(', '))}</td>` +
          `<td><span class="sp-score sp-s-${r.e.level}">${r.e.label}</span></td>` +
          `<td><button class="sp-x" type="button" title="Убрать" data-del="${r.d.id}">×</button></td></tr>`;
      }).join('');
  }
  function exportRows() {
    return state.donors.map(d => ({d, e: evalDonor(d)})).filter(r => r.e.level !== 'bad').map(r => {
      const c = niche.cats[r.d.cat] || niche.cats[0];
      return [r.d.name, L.TYPE_LABEL[c.t] + ': ' + c.name, r.d.phone ? '+' + r.d.phone : '', r.d.site, L.signalsFor(r.d, niche).join(', '), r.e.label];
    });
  }
  function fallbackCopy(t) {
    const a = document.createElement('textarea');
    a.value = t; document.body.appendChild(a); a.select();
    try { document.execCommand('copy'); } catch (e) { /* старый браузер */ }
    a.remove();
  }

  // ---------- сохранённые подборки ----------
  async function loadSaved() {
    try { saved = (await api('/api/sources/selections', {})).items; } catch (e) { saved = []; }
    renderSaved();
  }
  function renderSaved() {
    const card = g('sp-savedCard');
    if (!card) return;
    card.classList.toggle('sp-hidden', !saved.length);
    g('sp-saved').innerHTML = '<tr><th>Подборка</th><th>Город</th><th>Клиент</th><th>Доноров</th><th>Кто и когда</th><th></th></tr>' + saved.map(s =>
      `<tr${s.id === state.savedId ? ' class="sp-cur"' : ''}><td>${h(s.niche_name)}</td><td>${h(s.city || '—')}</td><td>${h(s.client || '—')}</td>` +
      `<td class="sp-mono">${s.good} из ${s.donors}</td><td class="sp-sig">${h(s.author || '')} · ${h(fmtWhen(s.updated_at))}</td>` +
      `<td class="sp-nowrap"><button class="sp-btn sp-sm" type="button" data-open="${s.id}">Открыть</button>` +
      (s.can_delete ? ` <button class="sp-x" type="button" title="Удалить подборку" data-drop="${s.id}">×</button>` : '') + '</td></tr>').join('');
  }
  async function openSaved(id) {
    if (state.donors.length && state.savedId !== id && !confirm('Открыть сохранённую подборку? Текущий черновик заменится.')) return;
    const s = await api('/api/sources/selections/' + id, {});
    state = Object.assign(blank(), {query: s.query || s.niche_name, nicheId: s.niche, city: s.city || '', client: s.client || '', seg: s.seg || '',
      savedId: s.id, donors: s.donors.map(d => Object.assign({}, d, {id: newId()}))});
    fill(); saveDraft(); renderSaved(); window.scrollTo(0, 0);
  }

  // ---------- закладка ----------
  async function makeBookmarklet() {
    if (bookmarklet) return bookmarklet;
    const r = await fetch('/static/sources-collect.js', {credentials: 'same-origin'});
    if (!r.ok) throw new Error('Не загрузился код закладки.');
    const seen = {}, stop = [];
    NICHES.reduce((all, n) => all.concat(n.stop || []), []).concat(window.SP_GLOBAL_STOP || []).forEach(s => {
      const k = s[0].toLowerCase();
      if (!seen[k]) { seen[k] = 1; stop.push([s[0]]); }
    });
    const code = (await r.text()).split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('//')).join('\n')
      .replace('__CFG__', () => JSON.stringify({crm: location.origin, stop}));
    bookmarklet = 'javascript:' + encodeURIComponent(code);
    return bookmarklet;
  }

  // ---------- номера из закладки ----------
  function importFrom(arg) {
    let payload;
    try { payload = JSON.parse(arg.slice('import:'.length)); } catch (e) { toast('Не разобрал номера из закладки.', true); return; }
    if (!payload || !Array.isArray(payload.items)) { toast('Не разобрал номера из закладки.', true); return; }
    if (!state.nicheId) {
      const id = L.nicheForQuery(payload.q || '', state.city, NICHES);
      state.nicheId = id || (payload.q ? 'custom' : '');
      state.query = id ? L.byId(id, NICHES).name : L.bareQuery(payload.q || '', state.city);
      if (!state.nicheId) { state.nicheId = 'custom'; state.query = 'Доноры'; }
    }
    const got = L.importItems(payload, state.donors, currentNiche());
    got.added.forEach(d => { d.id = newId(); state.donors.push(d); });
    saveDraft();
    toast(`Из закладки: добавлено ${got.added.length}` + (got.dup ? `, дублей ${got.dup}` : '') + (got.bad ? `, без телефона и сайта ${got.bad}` : '') + '.');
  }

  // ---------- страница ----------
  function fill() {
    g('sp-niche').value = state.query || '';
    g('sp-city').value = state.city || '';
    g('sp-client').value = state.client || '';
    [...g('sp-seg').children].forEach(x => x.classList.toggle('sp-active', x.getAttribute('data-seg') === (state.seg || '')));
    renderSugg(); render();
  }

  function bind(view) {
    g('sp-cities').innerHTML = L.CITIES.map(c => `<option value="${h(c)}">`).join('');
    g('sp-sugg').addEventListener('click', e => { const b = e.target.closest('[data-id]'); if (b) pickNiche(b.getAttribute('data-id')); });
    g('sp-niche').addEventListener('input', function () {
      state.query = this.value;
      const list = L.findNiches(state.query, NICHES);
      if (state.nicheId && state.nicheId !== 'custom' && !list.some(n => n.id === state.nicheId)) state.nicheId = '';
      if (state.nicheId === 'custom') state.nicheId = '';
      renderSugg(); render(); saveDraft();
    });
    g('sp-niche').addEventListener('keydown', e => {
      if (e.key !== 'Enter') return;
      const list = L.findNiches(state.query, NICHES);
      pickNiche(list.length ? list[0].id : (state.query.trim() ? 'custom' : ''));
    });
    g('sp-city').addEventListener('input', function () { state.city = this.value.trim(); render(); saveDraft(); });
    g('sp-client').addEventListener('input', function () { state.client = this.value.trim(); renderList(); saveDraft(); });
    g('sp-seg').addEventListener('click', function (e) {
      const b = e.target.closest('[data-seg]'); if (!b) return;
      state.seg = b.getAttribute('data-seg');
      [...this.children].forEach(x => x.classList.toggle('sp-active', x === b));
      render(); saveDraft();
    });
    g('sp-proven').addEventListener('click', e => {
      const b = e.target.closest('[data-proven]'); if (!b) return;
      const p = niche.proven[+b.getAttribute('data-proven')];
      addDonor({name: p.name, phone: p.phone, site: p.site || '', cat: 0, checks: [true, true, true, true], proven: true});
    });
    g('sp-paste').addEventListener('input', function () {
      const r = L.parseCard(this.value);
      if (r.name) g('sp-dName').value = r.name;
      if (r.phone) g('sp-dPhone').value = L.fmtPhone(r.phone);
      if (r.site) g('sp-dSite').value = r.site;
    });
    g('sp-addBtn').addEventListener('click', () => {
      const checks = [...g('sp-checks').querySelectorAll('input')].map(x => x.checked);
      const d = {name: g('sp-dName').value.trim(), phone: L.normPhone(g('sp-dPhone').value), site: L.normSite(g('sp-dSite').value),
        cat: +g('sp-dCat').value || 0, checks};
      if (!d.name && !d.phone && !d.site) { g('sp-addHint').textContent = 'Нужно название, телефон или сайт.'; return; }
      if (!d.phone && !d.site) { g('sp-addHint').textContent = 'Без телефона и сайта донора не задать в проекте.'; return; }
      if (addDonor(d)) {
        ['sp-paste', 'sp-dName', 'sp-dPhone', 'sp-dSite'].forEach(id => { g(id).value = ''; });
        g('sp-checks').querySelectorAll('input').forEach(x => { x.checked = false; });
        g('sp-paste').focus();
      }
    });
    g('sp-table').addEventListener('click', e => {
      const b = e.target.closest('[data-del]'); if (!b) return;
      const id = +b.getAttribute('data-del');
      state.donors = state.donors.filter(x => x.id !== id);
      renderList(); saveDraft();
    });
    g('sp-copyBtn').addEventListener('click', () => {
      const rows = exportRows();
      if (!rows.length) { g('sp-outHint').textContent = 'Нет доноров для выгрузки.'; return; }
      const tsv = [HEAD].concat(rows).map(r => r.join('\t')).join('\n');
      const done = () => { g('sp-outHint').textContent = `Скопировано ${rows.length} доноров. Вставьте в таблицу или в настройку проекта.`; };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(tsv).then(done, () => { fallbackCopy(tsv); done(); });
      else { fallbackCopy(tsv); done(); }
    });
    g('sp-csvBtn').addEventListener('click', () => {
      const rows = exportRows();
      if (!rows.length) { g('sp-outHint').textContent = 'Нет доноров для выгрузки.'; return; }
      const csv = '﻿' + [HEAD].concat(rows).map(r => r.map(x => '"' + String(x).replace(/"/g, '""') + '"').join(';')).join('\r\n');
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8'}));
      a.download = 'доноры_' + niche.id + '_' + (state.city || 'город').replace(/\s+/g, '_') + '.csv';
      document.body.appendChild(a); a.click(); a.remove();
    });
    g('sp-saveBtn').addEventListener('click', async () => {
      const body = {id: state.savedId, niche: niche.id, niche_name: niche.name, query: state.query, city: state.city, client: state.client, seg: state.seg,
        donors: state.donors.map(d => {
          const e = evalDonor(d), c = niche.cats[d.cat] || niche.cats[0];
          return {name: d.name, phone: d.phone, site: d.site, cat: d.cat || 0, checks: d.checks || [], proven: !!d.proven, from: d.from || '',
            category: c.name, type: c.t, level: e.level, label: e.label, signals: L.signalsFor(d, niche)};
        })};
      try {
        const r = await api('/api/sources/selections', {method: 'POST', body});
        state.savedId = r.id; saveDraft();
        g('sp-outHint').textContent = 'Подборка сохранена в CRM.';
        loadSaved();
      } catch (e) { g('sp-outHint').textContent = 'Не удалось сохранить: ' + e.message; }
    });
    g('sp-resetBtn').addEventListener('click', () => {
      state = Object.assign(blank(), {city: state.city});
      fill(); saveDraft(); renderSaved(); g('sp-niche').focus();
    });
    g('sp-saved').addEventListener('click', async e => {
      const o = e.target.closest('[data-open]'), d = e.target.closest('[data-drop]');
      try {
        if (o) await openSaved(+o.getAttribute('data-open'));
        if (d && confirm('Удалить сохранённую подборку?')) {
          await api(`/api/sources/selections/${+d.getAttribute('data-drop')}/delete`, {method: 'POST', body: {}});
          if (state.savedId === +d.getAttribute('data-drop')) { state.savedId = null; saveDraft(); }
          loadSaved();
        }
      } catch (err) { toast(err.message, true); }
    });
    const bm = g('sp-bm');
    bm.addEventListener('click', e => { e.preventDefault(); toast('Кнопку не нажимают здесь: перетащите её мышкой на панель закладок браузера.'); });
    makeBookmarklet().then(href => { bm.href = href; }).catch(e => { bm.classList.add('sp-off'); bm.title = e.message; });
  }

  // Другая вкладка CRM поменяла черновик (например, туда пришли номера из закладки): показываем свежий.
  window.addEventListener('storage', e => {
    if (e.key !== DRAFT_KEY || S.page !== 'sources' || !g('sp-app')) return;
    loadDraft(); fill(); renderSaved();
  });

  // Вкладка «Подбор доноров» раздела «Источники» (static/more.js). arg — номера из закладки (import:...).
  window.SourcesPicker = async (view, arg) => {
    view.innerHTML = PAGE;
    loadDraft();
    if (arg && arg.startsWith('import:')) importFrom(arg);
    bind(view);
    fill();
    loadSaved();
  };
})();
