// Генератор договоров в CRM: страница «Договоры» и кнопка «Составить договор» в карточке сделки.
// Перенесён из averon-contract-generator (app.html): договор собирается в браузере из .docx-шаблона
// (docxtemplater 3.71.1 + PizZip 3.3.0, MIT), сервер выдаёт номер, пишет журнал и ищет реквизиты по ИНН и БИК.
// Логика без интерфейса (числа прописью, падежи, разбор карточки) — logic.js, без изменений.
(function () {
  'use strict';
  var TYPES = {
    ops: { title: 'ОПС', desc: 'Сублицензия, определение посетителей сайта', file: 'Договор_ОПС' },
    gck: { title: 'ГЦК', desc: 'Сублицензия CRM, генерация целевых клиентов', file: 'Договор_ГЦК' },
    kc: { title: 'КЦ отдельно', desc: 'Договор оказания услуг, обзвон базы клиента', file: 'Договор_КЦ' },
    ds: { title: 'Допсоглашение ГЦК + КЦ', desc: 'Обзвон к уже заключённому договору ГЦК', file: 'Допсоглашение_КЦ' }
  };
  var OPS_TARIFFS = [['Старт', 300, 37000], ['Стандарт', 800, 76000], ['Профи', 2500, 163000], ['Премиум', 12000, 620000]];
  var GCK_PACKAGES = [[500, 35000], [1000, 70000], [3000, 165000], [6000, 330000], [10000, 520000]];
  var KC_TARIFFS = [['Старт', 300, 25000], ['Стандарт', 800, 55000], ['Профи', 2500, 165000]];
  var FIELDS = ['name', 'signer', 'signerGen', 'positionGen', 'basis', 'inn', 'kpp', 'ogrn', 'address', 'rs', 'bik', 'bank', 'ks', 'phone', 'email'];
  var LABELS = { inn: 'ИНН', ogrn: 'ОГРН', address: 'адрес', rs: 'расчётный счёт', bik: 'БИК', bank: 'банк', ks: 'корр. счёт', signer: 'ФИО подписанта', name: 'наименование', kpp: 'КПП', signerGen: 'ФИО в родительном падеже', site: 'сайт', leads: 'количество лидов', sum: 'сумма', contacts: 'количество контактов', baseNum: 'номер договора ГЦК', baseDate: 'дата договора ГЦК', dialogs: 'количество диалогов' };

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  function markup(deal) {
    return '<div id="dg-app">' +
      (deal ? '<div class="dg-card dg-deal"><h2>Договор по сделке: ' + esc(deal.title) + '</h2>' +
        '<p>Договор появится в истории сделки. «Договор отправлен» отметьте в сделке, когда отправите его клиенту.</p>' +
        (deal.missing && deal.missing.length ? '<p>По презентации для договора не хватало: ' + esc(deal.missing.join(', ')) + '.</p>' : '') + '</div>' : '') +
      '<p class="dg-sub">Выберите договор, вставьте реквизиты клиента и скачайте готовый .docx.</p>' +
      '<div class="dg-card"><h2><span class="dg-step">1</span>Какой договор</h2><div class="dg-types" id="dg-types"></div></div>' +
      '<div class="dg-card"><h2><span class="dg-step">2</span>Реквизиты клиента</h2>' +
      '<label for="dg-card">Вставьте карточку компании целиком (из письма или Telegram). Поля ниже заполнятся сами.</label>' +
      '<textarea id="dg-card" rows="5" placeholder="ООО «Ромашка»&#10;ИНН 7700000000 / КПП 770001001&#10;ОГРН 1027700000000&#10;Юридический адрес: ...&#10;Р/с ... Банк ... БИК ...&#10;Генеральный директор Иванов Иван Иванович"></textarea>' +
      '<div class="dg-row dg-mt8"><button class="dg-btn" id="dg-parseBtn" type="button">Разобрать карточку</button>' +
      '<button class="dg-btn dg-hidden" id="dg-innBtn" type="button">Найти по ИНН</button><span class="dg-hint" id="dg-parseHint"></span></div>' +
      '<div class="dg-row dg-kindrow"><span class="dg-seg" id="dg-kindSeg"><button type="button" data-kind="org" class="dg-active">Организация (ООО, АО)</button><button type="button" data-kind="ip">ИП</button></span></div>' +
      '<div class="dg-grid">' +
      '<div class="dg-full dg-org-only"><label for="dg-name">Наименование</label><input id="dg-name" placeholder="ООО «Ромашка»"></div>' +
      '<div><label for="dg-signer" id="dg-signerLabel">ФИО руководителя (подписанта)</label><input id="dg-signer" placeholder="Иванов Иван Иванович"></div>' +
      '<div class="dg-org-only"><label for="dg-signerGen">В лице кого (родительный падеж)</label><input id="dg-signerGen" placeholder="Иванова Ивана Ивановича"><div class="dg-hint">Заполняется само. Проверьте падеж.</div></div>' +
      '<div class="dg-org-only"><label for="dg-positionGen">Должность (в лице кого)</label><input id="dg-positionGen" value="генерального директора"></div>' +
      '<div class="dg-org-only"><label for="dg-basis">Действует на основании</label><input id="dg-basis" value="Устава"></div>' +
      '<div><label for="dg-inn">ИНН</label><input id="dg-inn" inputmode="numeric"><div class="dg-hint" id="dg-innHint"></div></div>' +
      '<div class="dg-org-only"><label for="dg-kpp">КПП</label><input id="dg-kpp" inputmode="numeric"></div>' +
      '<div><label for="dg-ogrn" id="dg-ogrnLabel">ОГРН</label><input id="dg-ogrn" inputmode="numeric"></div>' +
      '<div class="dg-full"><label for="dg-address">Адрес</label><input id="dg-address"></div>' +
      '<div><label for="dg-rs">Расчётный счёт</label><input id="dg-rs" inputmode="numeric"></div>' +
      '<div><label for="dg-bik">БИК</label><input id="dg-bik" inputmode="numeric"></div>' +
      '<div><label for="dg-bank">Банк</label><input id="dg-bank"></div>' +
      '<div><label for="dg-ks">Корреспондентский счёт</label><input id="dg-ks" inputmode="numeric"></div>' +
      '<div><label for="dg-phone">Телефон</label><input id="dg-phone"></div>' +
      '<div><label for="dg-email">E-mail</label><input id="dg-email"></div>' +
      '</div></div>' +
      '<div class="dg-card"><h2><span class="dg-step">3</span>Условия</h2><div class="dg-grid">' +
      '<div><label for="dg-num">Номер договора</label><input id="dg-num" readonly><div class="dg-hint" id="dg-numHint"></div></div>' +
      '<div><label for="dg-date">Дата</label><input id="dg-date" type="date"></div></div>' +
      '<div id="dg-f-ops" class="dg-grid dg-hidden dg-mt10">' +
      '<div><label for="dg-ops-tariff">Тариф</label><select id="dg-ops-tariff"></select></div>' +
      '<div><label for="dg-ops-leads">Количество лидов</label><input id="dg-ops-leads" inputmode="numeric"></div>' +
      '<div class="dg-full"><label for="dg-ops-site">Сайт клиента</label><input id="dg-ops-site" placeholder="example.ru"></div>' +
      '<div class="dg-full dg-hint" id="dg-ops-price"></div></div>' +
      '<div id="dg-f-gck" class="dg-grid dg-hidden dg-mt10">' +
      '<div class="dg-full"><label>Пакет</label><div class="dg-chips" id="dg-gck-chips"></div></div>' +
      '<div><label for="dg-gck-leads">Количество лидов</label><input id="dg-gck-leads" inputmode="numeric"></div>' +
      '<div><label for="dg-gck-sum">Сумма, руб.</label><input id="dg-gck-sum" inputmode="numeric"></div>' +
      '<div class="dg-full"><label for="dg-gck-sites">Сайты для приложения № 1 (каждый с новой строки)</label><textarea id="dg-gck-sites" rows="3"></textarea></div></div>' +
      '<div id="dg-f-kc" class="dg-grid dg-hidden dg-mt10">' +
      '<div class="dg-full"><label>Тариф из прайса</label><div class="dg-chips" id="dg-kc-chips"></div></div>' +
      '<div><label for="dg-kc-contacts">Количество контактов в базе</label><input id="dg-kc-contacts" inputmode="numeric"></div>' +
      '<div><label for="dg-kc-sum">Сумма, руб.</label><input id="dg-kc-sum" inputmode="numeric"></div>' +
      '<div class="dg-full dg-hint" id="dg-kc-per"></div></div>' +
      '<div id="dg-f-ds" class="dg-grid dg-hidden dg-mt10">' +
      '<div><label for="dg-ds-baseNum">Номер договора ГЦК</label><input id="dg-ds-baseNum"></div>' +
      '<div><label for="dg-ds-baseDate">Дата договора ГЦК</label><input id="dg-ds-baseDate" type="date"></div>' +
      '<div><label for="dg-ds-tariff">Тариф</label><select id="dg-ds-tariff"></select></div>' +
      '<div><label for="dg-ds-dialogs">Количество диалогов</label><input id="dg-ds-dialogs" inputmode="numeric"></div>' +
      '<div><label for="dg-ds-sum">Сумма, руб.</label><input id="dg-ds-sum" inputmode="numeric"></div></div>' +
      '</div>' +
      '<div class="dg-card"><h2><span class="dg-step">4</span>Проверка и скачивание</h2>' +
      '<div class="dg-preview" id="dg-preview"></div><div id="dg-warnings"></div>' +
      '<div class="dg-footer dg-mt14"><button class="dg-btn dg-primary" id="dg-genBtn" type="button">Скачать договор (.docx)</button>' +
      '<span class="dg-mode" id="dg-modeHint">Номер присваивается и договор записывается в журнал при скачивании.</span></div>' +
      '<div id="dg-result"></div></div></div>';
  }

  // opts: api(path, {method, body}) — запрос к CRM; deal — {phone, title, number, missing} или null;
  // readonly — вход для просмотра; onDone() — договор записан в журнал.
  function mount(el, opts) {
    var L = window.ContractLogic;
    var api = opts.api, deal = opts.deal || null;
    el.innerHTML = markup(deal);
    var cfg = { ourEmail: '', dsTariffs: [], innLookup: false }, templates = {};
    var state = { type: 'ops', kind: 'org' };
    var $ = function (id) { return document.getElementById('dg-' + id); };
    var signerGenTouched = false;

    function num(v) { var x = String(v || '').replace(/\s| /g, '').replace(',', '.'); return x === '' ? '' : Number(x); }
    function today() { var d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }

    // ---------- построение интерфейса ----------
    function buildTypes() {
      $('types').innerHTML = Object.keys(TYPES).map(function (k) {
        var t = TYPES[k];
        return '<button type="button" class="dg-type' + (k === state.type ? ' dg-active' : '') + '" data-type="' + k + '"><b>' + esc(t.title) + '</b><span>' + esc(t.desc) + '</span></button>';
      }).join('');
      Array.prototype.forEach.call(el.querySelectorAll('.dg-type'), function (b) {
        b.onclick = function () { state.type = b.getAttribute('data-type'); buildTypes(); showType(); update(); };
      });
    }
    function fillSelect(id, list) {
      $(id).innerHTML = list.map(function (t, i) { return '<option value="' + i + '">' + esc(t[0]) + ' — ' + L.fmtNum(t[1]) + '</option>'; }).join('');
    }
    function buildChips(id, list, onPick, label) {
      $(id).innerHTML = list.map(function (p, i) { return '<button type="button" class="dg-chip" data-i="' + i + '">' + label(p) + '</button>'; }).join('');
      Array.prototype.forEach.call($(id).querySelectorAll('.dg-chip'), function (c) { c.onclick = function () { onPick(list[Number(c.getAttribute('data-i'))]); update(); }; });
    }
    function showType() {
      ['ops', 'gck', 'kc', 'ds'].forEach(function (t) { $('f-' + t).classList.toggle('dg-hidden', t !== state.type); });
      $('num').parentNode.classList.toggle('dg-hidden', state.type === 'ds');
    }
    function setKind(k) {
      state.kind = k;
      Array.prototype.forEach.call(el.querySelectorAll('#dg-kindSeg button'), function (b) { b.classList.toggle('dg-active', b.getAttribute('data-kind') === k); });
      Array.prototype.forEach.call(el.querySelectorAll('.dg-org-only'), function (x) { x.classList.toggle('dg-hidden', k === 'ip'); });
      $('signerLabel').textContent = k === 'ip' ? 'ФИО предпринимателя' : 'ФИО руководителя (подписанта)';
      $('ogrnLabel').textContent = k === 'ip' ? 'ОГРНИП' : 'ОГРН';
    }

    // ---------- сбор формы ----------
    function form() {
      var f = { type: state.type, kind: state.kind, num: $('num').value.trim(), date: $('date').value };
      FIELDS.forEach(function (k) { f[k] = $(k).value.trim(); });
      if (state.type === 'ops') {
        var t = OPS_TARIFFS[Number($('ops-tariff').value)] || [];
        f.tariff = t[0]; f.leads = num($('ops-leads').value); f.site = $('ops-site').value.trim(); f.sum = t[2];
      } else if (state.type === 'gck') {
        f.leads = num($('gck-leads').value); f.sum = num($('gck-sum').value); f.sites = $('gck-sites').value;
      } else if (state.type === 'kc') {
        f.contacts = num($('kc-contacts').value); f.sum = num($('kc-sum').value);
        f.pricePer = (f.contacts && f.sum) ? Math.round(f.sum / f.contacts * 100) / 100 : '';
      } else if (state.type === 'ds') {
        var d = cfg.dsTariffs[Number($('ds-tariff').value)] || [];
        f.tariff = d[0]; f.dialogs = num($('ds-dialogs').value); f.sum = num($('ds-sum').value);
        f.baseNum = $('ds-baseNum').value.trim(); f.baseDate = $('ds-baseDate').value;
      }
      return f;
    }

    function required(f) {
      var req = ['inn', 'ogrn', 'address', 'rs', 'bik', 'bank', 'ks', 'signer'];
      if (f.kind === 'org') req = req.concat(['name', 'kpp', 'signerGen']);
      var spec = { ops: ['site', 'leads'], gck: ['leads', 'sum'], kc: ['contacts', 'sum'], ds: ['baseNum', 'baseDate', 'dialogs', 'sum'] }[f.type];
      return { client: req, spec: spec };
    }

    function update() {
      var f = form();
      var r = required(f), missing = [];
      r.client.forEach(function (k) { var x = $(k); if (x) x.classList.toggle('dg-miss', !f[k]); if (!f[k]) missing.push(LABELS[k]); });
      r.spec.forEach(function (k) { if (f[k] === '' || f[k] === undefined) missing.push(LABELS[k]); });
      var warns = [];
      if (f.inn) {
        var ok = L.checkInn(f.inn);
        $('inn').classList.toggle('dg-bad', !ok);
        $('innHint').textContent = ok ? '' : 'ИНН не проходит проверку контрольной суммы. Проверьте цифры.';
        $('innHint').className = 'dg-hint' + (ok ? '' : ' dg-err');
        if (!ok) warns.push('ИНН не проходит проверку контрольной суммы.');
        if (f.kind === 'org' && f.inn.length === 12) warns.push('ИНН из 12 цифр бывает у ИП, а выбрана организация.');
        if (f.kind === 'ip' && f.inn.length === 10) warns.push('ИНН из 10 цифр бывает у организаций, а выбран ИП.');
      } else { $('inn').classList.remove('dg-bad'); $('innHint').textContent = ''; }
      if (f.rs && f.rs.replace(/\D/g, '').length !== 20) warns.push('Расчётный счёт должен быть из 20 цифр.');
      if (f.ks && f.ks.replace(/\D/g, '').length !== 20) warns.push('Корр. счёт должен быть из 20 цифр.');
      if (f.bik && f.bik.replace(/\D/g, '').length !== 9) warns.push('БИК должен быть из 9 цифр.');

      // предпросмотр ключевых фраз
      var d = L.buildData(f.type, f, cfgForDoc());
      var p = [];
      var role = f.type === 'kc' ? 'Заказчик' : 'Сублицензиат';
      p.push('…и <b>' + esc(d.party) + '</b>, далее — «' + role + '»…');
      if (f.type === 'ops') p.push('Тариф «' + esc(d.tariff) + '», ' + esc(d.leads) + ' лидов, сайт ' + esc(d.site) + (f.sum ? ' (стоимость по прайсу ' + L.fmtNum(f.sum) + ' руб.)' : ''));
      if (f.type === 'gck') p.push('На получение ' + esc(d.leads) + ' (' + esc(d.leads_words) + ') лидов. Вознаграждение ' + esc(d.sum) + ' (' + esc(d.sum_words) + ') рублей.');
      if (f.type === 'kc') p.push('Стоимость ' + esc(d.sum) + ' (' + esc(d.sum_words) + ') рублей за ' + esc(d.contacts) + ' контактов, ' + esc(d.price_per) + ' руб. за контакт.');
      if (f.type === 'ds') p.push('К договору № ' + esc(d.base_num) + ' от ' + esc(d.base_date) + '. Тариф «' + esc(d.tariff) + '», ' + esc(d.dialogs) + ' диалогов, ' + esc(d.sum) + ' (' + esc(d.sum_words) + ') рублей.');
      p.push('Подпись: ________ / ' + esc(d.signer_short) + ' /');
      $('preview').innerHTML = p.map(function (x) { return '<div>' + x + '</div>'; }).join('');

      if (f.type === 'kc') $('kc-per').textContent = f.pricePer ? 'Цена за контакт: ' + L.fmtNum(f.pricePer) + ' руб.' : '';
      if (f.type === 'ops') { var t = OPS_TARIFFS[Number($('ops-tariff').value)]; $('ops-price').textContent = t ? 'Стоимость тарифа по договору: ' + L.fmtNum(t[2]) + ' руб. (в тексте договора цена указана в таблице тарифов)' : ''; }

      var html = '';
      if (missing.length) html += '<div class="dg-note dg-warn">Не заполнено: ' + esc(missing.join(', ')) + '. Договор можно скачать и так, пустые места останутся с прочерками.</div>';
      if (warns.length) html += '<div class="dg-note dg-err">' + warns.map(esc).join('<br>') + '</div>';
      $('warnings').innerHTML = html;
    }

    function cfgForDoc() { return { ourEmail: cfg.ourEmail, dsPrices: cfg.dsTariffs.map(function (t) { return t[2]; }) }; }

    // ---------- карточка ----------
    function applyParsed(r) {
      var got = [];
      if (r.kind) setKind(r.kind);
      FIELDS.forEach(function (k) { if (r[k]) { $(k).value = r[k]; got.push(k); } });
      if (r.signer && !signerGenTouched) $('signerGen').value = L.fioGenitive(r.signer);
      return got;
    }
    $('parseBtn').onclick = function () {
      var r = L.parseCard($('card').value);
      var got = applyParsed(r);
      $('parseHint').textContent = got.length ? 'Найдено полей: ' + got.length + '. Проверьте и дополните подсвеченные.' : 'Ничего не нашлось. Заполните поля вручную.';
      update();
      if (cfg.innLookup && r.inn && (!r.name || !r.ogrn || !r.address || !r.kpp)) lookupInn(r.inn, true);
    };

    // ---------- поиск по ИНН и БИК: через сервер CRM, ключ DaData только там ----------
    var lastLookup = '';
    function lookupInn(inn, quiet) {
      inn = String(inn || '').replace(/\D/g, '');
      if (!cfg.innLookup) return;
      if (inn.length !== 10 && inn.length !== 12) { if (!quiet) $('parseHint').textContent = 'В ИНН должно быть 10 цифр (организация) или 12 (ИП).'; return; }
      lastLookup = inn;
      $('parseHint').classList.remove('dg-err');
      $('parseHint').textContent = 'Ищу по ИНН…';
      api('/api/contracts/lookup/inn/' + inn).then(function (res) {
        if (res && res.ok && res.raw) {
          var r = L.fromDadataParty(res.raw), filled = [];
          if (r.kind) setKind(r.kind);
          FIELDS.forEach(function (k) {
            if (!r[k]) return;
            if (k === 'positionGen' ? $(k).value !== r[k] : !$(k).value) { $(k).value = r[k]; filled.push(k); }
          });
          if (r.signer && !signerGenTouched) $('signerGen').value = L.fioGenitive($('signer').value);
          $('parseHint').textContent = (filled.length ? 'По ИНН заполнено полей: ' + filled.length + '. Банк и р/с добавьте из карточки клиента.' : 'По ИНН всё уже заполнено.') + (r.warning ? ' ' + r.warning : '');
          $('parseHint').classList.toggle('dg-err', !!r.warning);
        } else $('parseHint').textContent = quiet ? '' : (res && res.error) || 'Не нашлось по ИНН.';
        update();
      }).catch(function (e) { $('parseHint').textContent = 'Поиск по ИНН не сработал: ' + e.message; });
    }
    // ИНН берём из поля «ИНН», а если там пусто, то из вставленной карточки
    $('innBtn').onclick = function () {
      var inn = $('inn').value.replace(/\D/g, '');
      if (!inn) { inn = L.parseCard($('card').value).inn || ''; if (inn) { $('inn').value = inn; update(); } }
      if (!inn) { $('parseHint').classList.add('dg-err'); $('parseHint').textContent = 'Введите ИНН в поле «ИНН» или в карточку выше.'; return; }
      lookupInn(inn, false);
    };
    // Ввели ИНН руками, а название пустое: ищем сами
    $('inn').addEventListener('change', function () {
      var v = this.value.replace(/\D/g, '');
      if (cfg.innLookup && v !== lastLookup && (v.length === 10 || v.length === 12) && L.checkInn(v) && !$('name').value && !$('ogrn').value) lookupInn(v, true);
    });
    $('signer').addEventListener('input', function () { if (!signerGenTouched) $('signerGen').value = L.fioGenitive(this.value); });
    $('signerGen').addEventListener('input', function () { signerGenTouched = this.value.trim() !== ''; });
    Array.prototype.forEach.call(el.querySelectorAll('#dg-kindSeg button'), function (b) { b.onclick = function () { setKind(b.getAttribute('data-kind')); update(); }; });

    // ---------- генерация ----------
    function b64ToBytes(b64) { var bin = atob(b64), a = new Uint8Array(bin.length); for (var i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i); return a; }
    function safeName(s) { return String(s || '').replace(/[«»"']/g, '').replace(/[\\/:*?<>|]+/g, ' ').replace(/\s+/g, '_').replace(/\.+$/, '').slice(0, 60); }
    function render(f) {
      var tpl = templates[f.type];
      if (!tpl) throw new Error('Нет шаблона для этого договора.');
      var zip = new window.PizZip(b64ToBytes(tpl));
      var doc = new window.docxtemplater(zip, { paragraphLoop: true, linebreaks: true, nullGetter: function () { return ''; } });
      doc.render(L.buildData(f.type, f, cfgForDoc()));
      return doc.getZip().generate({ type: 'blob', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
    }
    function download(blob, name) {
      var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name;
      document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
    }
    function fileName(f) {
      var who = f.kind === 'ip' ? 'ИП_' + L.fioShort(f.signer) : f.name;
      var n = f.type === 'ds' ? 'к_№' + f.baseNum : '№' + f.num;
      return TYPES[f.type].file + '_' + n + '_' + safeName(who) + '.docx';
    }
    $('genBtn').onclick = function () {
      var f = form();
      // Сначала проверяем, что шаблон собирается, и только потом занимаем номер в журнале.
      try { render(f); } catch (e) { $('result').innerHTML = '<div class="dg-note dg-err">Не удалось собрать договор: ' + esc(e.message) + '</div>'; return; }
      $('genBtn').disabled = true; $('result').innerHTML = '';
      api('/api/contracts/register', { method: 'POST', body: {
        type: f.type, date: f.date, client: L.clientName(f), inn: f.inn, sum: f.sum === '' || f.sum === undefined ? '' : f.sum,
        tariff: f.tariff || '', baseNum: f.baseNum || '', deal: deal ? deal.phone : ''
      } }).then(function (res) {
        if (f.type !== 'ds') { f.num = String(res.num); $('num').value = f.num; }
        try {
          download(render(f), fileName(f));
          $('result').innerHTML = '<div class="dg-note dg-ok">Готово: ' + esc(fileName(f)) + '<br>' + (f.type !== 'ds' ? 'Номер договора ' + esc(f.num) + ' записан в журнал.' : 'Записано в журнал.') + '</div>';
        } catch (e) {
          $('result').innerHTML = '<div class="dg-note dg-err">Не удалось собрать договор: ' + esc(e.message) + '</div>';
        }
        $('genBtn').disabled = false;
        loadNextNum();
        if (opts.onDone) opts.onDone();
      }).catch(function (e) {
        $('result').innerHTML = '<div class="dg-note dg-err">Не получилось записать в журнал: ' + esc(e.message) + '</div>'; $('genBtn').disabled = false;
      });
    };

    function loadNextNum() {
      api('/api/contracts/next-number').then(function (d) { $('num').value = String(d.num); $('numHint').textContent = 'Номер присваивается автоматически при скачивании.'; }).catch(function () {});
    }

    // ---------- старт ----------
    function init() {
      fillSelect('ops-tariff', OPS_TARIFFS);
      fillSelect('ds-tariff', cfg.dsTariffs.filter(function (t) { return t[2]; }));
      $('ops-tariff').onchange = function () { $('ops-leads').value = OPS_TARIFFS[Number(this.value)][1]; update(); };
      $('ds-tariff').onchange = function () { var t = cfg.dsTariffs[Number(this.value)]; $('ds-dialogs').value = t[1]; $('ds-sum').value = t[2] || ''; update(); };
      $('ops-leads').value = OPS_TARIFFS[0][1];
      if (cfg.dsTariffs.length) { $('ds-dialogs').value = cfg.dsTariffs[0][1]; $('ds-sum').value = cfg.dsTariffs[0][2] || ''; }
      buildChips('gck-chips', GCK_PACKAGES, function (p) { $('gck-leads').value = p[0]; $('gck-sum').value = p[1]; }, function (p) { return L.fmtNum(p[0]) + ' лидов — ' + L.fmtNum(p[1]) + ' ₽'; });
      buildChips('kc-chips', KC_TARIFFS, function (p) { $('kc-contacts').value = p[1]; $('kc-sum').value = p[2]; }, function (p) { return p[0] + ': ' + L.fmtNum(p[1]) + ' — ' + L.fmtNum(p[2]) + ' ₽'; });
      $('date').value = today();
      if (deal && deal.number) $('phone').value = deal.number;
      $('bik').addEventListener('change', function () {
        var b = this.value.replace(/\D/g, '');
        if (cfg.innLookup && b.length === 9 && (!$('bank').value || !$('ks').value)) {
          api('/api/contracts/lookup/bik/' + b).then(function (res) {
            if (res && res.ok && res.raw) { var d = L.fromDadataBank(res.raw); if (!$('bank').value) $('bank').value = d.bank; if (!$('ks').value) $('ks').value = d.ks; update(); }
          }).catch(function () {});
        }
      });
      buildTypes(); showType(); setKind('org');
      $('innBtn').classList.toggle('dg-hidden', !cfg.innLookup);
      Array.prototype.forEach.call(el.querySelectorAll('#dg-app input,#dg-app select,#dg-app textarea'), function (x) { if (x.id !== 'dg-card') x.addEventListener('input', update); x.addEventListener('change', update); });
      if (opts.readonly) {
        $('genBtn').disabled = true;
        $('modeHint').textContent = 'Это вход только для просмотра: скачать договор можно по личной ссылке.';
      }
      loadNextNum();
      update();
    }

    return api('/api/contracts/config').then(function (c) {
      cfg.ourEmail = c.ourEmail || '';
      cfg.dsTariffs = c.dsTariffs || [];
      cfg.innLookup = !!c.innLookup;
      templates = c.templates || {};
      init();
    });
  }

  window.ContractGenerator = { mount: mount };
})();
