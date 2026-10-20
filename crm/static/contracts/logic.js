// Логика генератора договоров: числа прописью, падежи ФИО, разбор карточки, данные для шаблона.
(function (root) {
  'use strict';

  // ---------- Числа ----------
  function fmtNum(n) {
    if (n === '' || n === null || n === undefined || isNaN(Number(n))) return '';
    var x = Number(n);
    var parts = (Math.round(x * 100) / 100).toFixed(2).split('.');
    var int = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    return parts[1] === '00' ? int : int + ',' + parts[1];
  }

  var ONES_M = ['', 'один', 'два', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять'];
  var ONES_F = ['', 'одна', 'две', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять'];
  var TEENS = ['десять', 'одиннадцать', 'двенадцать', 'тринадцать', 'четырнадцать', 'пятнадцать', 'шестнадцать', 'семнадцать', 'восемнадцать', 'девятнадцать'];
  var TENS = ['', '', 'двадцать', 'тридцать', 'сорок', 'пятьдесят', 'шестьдесят', 'семьдесят', 'восемьдесят', 'девяносто'];
  var HUNDREDS = ['', 'сто', 'двести', 'триста', 'четыреста', 'пятьсот', 'шестьсот', 'семьсот', 'восемьсот', 'девятьсот'];
  var SCALES = [
    null,
    { forms: ['тысяча', 'тысячи', 'тысяч'], fem: true },
    { forms: ['миллион', 'миллиона', 'миллионов'], fem: false },
    { forms: ['миллиард', 'миллиарда', 'миллиардов'], fem: false }
  ];

  function plural(n, forms) {
    n = Math.abs(n) % 100;
    var n1 = n % 10;
    if (n > 10 && n < 20) return forms[2];
    if (n1 > 1 && n1 < 5) return forms[1];
    if (n1 === 1) return forms[0];
    return forms[2];
  }

  function tripletWords(n, fem) {
    var out = [];
    var h = Math.floor(n / 100), t = Math.floor((n % 100) / 10), o = n % 10;
    if (h) out.push(HUNDREDS[h]);
    if (t === 1) { out.push(TEENS[o]); return out; }
    if (t) out.push(TENS[t]);
    if (o) out.push((fem ? ONES_F : ONES_M)[o]);
    return out;
  }

  // Целое число прописью, именительный падеж: 37000 -> «тридцать семь тысяч»
  function numWords(num) {
    num = Math.floor(Math.abs(Number(num) || 0));
    if (num === 0) return 'ноль';
    var words = [], scale = 0;
    var groups = [];
    while (num > 0) { groups.push(num % 1000); num = Math.floor(num / 1000); }
    for (var i = groups.length - 1; i >= 0; i--) {
      var g = groups[i];
      if (!g) continue;
      var s = SCALES[i];
      words = words.concat(tripletWords(g, s ? s.fem : false));
      if (s) words.push(plural(g, s.forms));
    }
    return words.join(' ');
  }

  // Сумма прописью для скобок: целые рубли словами, копейки цифрами, если есть
  function sumWords(sum) {
    return numWords(Math.round(Number(sum) || 0));
  }

  // ---------- Даты ----------
  var MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  function dateParts(iso) { // 'YYYY-MM-DD'
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
    if (!m) return { day: '____', month: '____________', year: '20__' };
    return { day: m[3], month: MONTHS_GEN[Number(m[2]) - 1], year: m[1] };
  }
  function dateRu(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
    return m ? m[3] + '.' + m[2] + '.' + m[1] : '';
  }

  // ---------- ФИО ----------
  function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
  function splitFio(fio) { return String(fio || '').trim().split(/\s+/).filter(Boolean); }

  function isFemale(fio) {
    var p = splitFio(fio);
    if (p.length >= 3) return /(вна|чна|кызы)$/i.test(p[2]);
    if (p.length === 2) return /[ая]$/i.test(p[1]) && !/(ья|ла|та|ша|ня)$/i.test(p[1]) ? true : false;
    return false;
  }

  var HUSH = /[гкхжшщч]$/i;
  function genName(w, fem) {
    if (fem) {
      if (/ия$/i.test(w)) return w.slice(0, -1) + 'и';
      if (/а$/i.test(w)) return w.slice(0, -1) + (HUSH.test(w.slice(0, -1)) ? 'и' : 'ы');
      if (/я$/i.test(w)) return w.slice(0, -1) + 'и';
      if (/ь$/i.test(w)) return w.slice(0, -1) + 'и';
      return w;
    }
    if (/[йь]$/i.test(w)) return w.slice(0, -1) + 'я';
    if (/а$/i.test(w)) return w.slice(0, -1) + (HUSH.test(w.slice(0, -1)) ? 'и' : 'ы');
    if (/я$/i.test(w)) return w.slice(0, -1) + 'и';
    if (/[бвгджзклмнпрстфхцчшщ]$/i.test(w)) return w + 'а';
    return w;
  }
  function genSurname(w, fem) {
    var parts = w.split('-');
    return parts.map(function (s) {
      if (fem) {
        if (/(ова|ева|ёва|ина|ына)$/i.test(s)) return s.slice(0, -1) + 'ой';
        if (/(ская|цкая)$/i.test(s)) return s.slice(0, -2) + 'ой';
        if (/ая$/i.test(s)) return s.slice(0, -2) + 'ой';
        return s; // прочие женские фамилии не склоняются
      }
      if (/(ых|их|ко|о|е|и|у|ю|э)$/i.test(s)) return s;
      if (/(ский|цкий)$/i.test(s)) return s.slice(0, -2) + 'ого';
      if (/(ой|ый)$/i.test(s)) return s.slice(0, -2) + 'ого';
      if (/ий$/i.test(s)) return s.slice(0, -2) + 'его';
      if (/[йь]$/i.test(s)) return s.slice(0, -1) + 'я';
      if (/а$/i.test(s)) return s.slice(0, -1) + (HUSH.test(s.slice(0, -1)) ? 'и' : 'ы');
      if (/я$/i.test(s)) return s.slice(0, -1) + 'и';
      return s + 'а';
    }).join('-');
  }
  function genPatronymic(w, fem) {
    if (fem) return /на$/i.test(w) ? w.slice(0, -1) + 'ы' : w;
    return /ич$/i.test(w) ? w + 'а' : w;
  }
  // «Иванов Иван Иванович» -> «Иванова Ивана Ивановича»
  function fioGenitive(fio) {
    var p = splitFio(fio); if (!p.length) return '';
    var fem = isFemale(fio);
    var out = [genSurname(p[0], fem)];
    if (p[1]) out.push(genName(p[1], fem));
    if (p[2]) out.push(genPatronymic(p[2], fem));
    for (var i = 3; i < p.length; i++) out.push(p[i]);
    return out.join(' ');
  }
  // «Иванов Иван Иванович» -> «Иванов И.И.»
  function fioShort(fio) {
    var p = splitFio(fio); if (!p.length) return '';
    return p[0] + (p[1] ? ' ' + p[1].charAt(0).toUpperCase() + '.' : '') + (p[2] ? p[2].charAt(0).toUpperCase() + '.' : '');
  }

  // ---------- Разбор карточки реквизитов ----------
  function digits(s) { return String(s || '').replace(/\D/g, ''); }
  function parseCard(text) {
    var t = String(text || '').replace(/\r/g, '').replace(/[ \t]+/g, ' ');
    var lines = t.split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
    var r = {};
    function grab(re) { var m = re.exec(t); return m ? m[1].trim() : ''; }
    function lineAfter(re) {
      for (var i = 0; i < lines.length; i++) {
        var m = re.exec(lines[i]);
        if (m) {
          var v = lines[i].slice(m.index + m[0].length).replace(/^[\s:.\-–—]+/, '').trim();
          if (!v && lines[i + 1] && !/:/.test(lines[i + 1])) v = lines[i + 1];
          return v;
        }
      }
      return '';
    }

    var innKpp = /ИНН\s*\/\s*КПП[\s:.\-–—]*(\d{10,12})\s*\/\s*(\d{9})/i.exec(t);
    if (innKpp) { r.inn = innKpp[1]; r.kpp = innKpp[2]; }
    if (!r.inn) r.inn = grab(/ИНН(?!\s*\/)[\s:.\-–—№]*(\d{10,12})/i);
    // Без подписи «ИНН»: отдельное число из 10 или 12 цифр с верной контрольной суммой
    if (!r.inn) {
      var bare = t.match(/(^|[^\d])(\d{12}|\d{10})(?!\d)/g) || [];
      for (var bi = 0; bi < bare.length; bi++) { var cand = bare[bi].replace(/\D/g, ''); if (checkInn(cand)) { r.inn = cand; break; } }
    }
    if (!r.kpp) r.kpp = grab(/КПП[\s:.\-–—№]*(\d{9})/i);
    r.ogrn = grab(/ОГРН(?:ИП)?[\s:.\-–—№]*(\d{13,15})/i);
    r.bik = grab(/БИК[\s:.\-–—№]*(\d{9})/i);
    r.rs = digits(grab(/(?:р\/с|р\.с\.|расч[её]тный\s+сч[её]т|расч\.\s*сч[её]т|счет\s+получателя)[\s:.\-–—№]*([\d\s]{20,30})/i)).slice(0, 20);
    r.ks = digits(grab(/(?:к\/с|к\.с\.|корр?\.?\s*сч[её]т|корреспондентский\s+сч[её]т)[\s:.\-–—№]*([\d\s]{20,30})/i)).slice(0, 20);
    r.bank = lineAfter(/(?:^|\s)(?:банк|наименование\s+банка)(?=[\s:]|$)/i).replace(/^(?:получателя|:)\s*/i, '');
    if (!r.bank) {
      for (var b = 0; b < lines.length; b++) {
        if (/банк/i.test(lines[b]) && !/(р\/с|к\/с|бик|сч[её]т)/i.test(lines[b])) { r.bank = lines[b].replace(/"([^"]+)"/g, '«$1»'); break; }
      }
    }
    r.address = lineAfter(/(?:юридический\s+адрес|юр\.?\s*адрес|адрес\s+регистрации|адрес\s+места\s+нахождения|адрес)/i);
    r.phone = grab(/(?:тел(?:ефон)?\.?)[\s:.\-–—]*([+\d][\d\s()\-+,]{6,})/i).replace(/[,\s]+$/, '');
    r.email = grab(/([\w.+\-]+@[\w\-]+\.[\w.\-]+)/);

    var dir = /(генеральн\w+\s+директор\w*|директор\w*|руководител\w+)[\s:.\-–—]*([А-ЯЁ][а-яё\-]+\s+[А-ЯЁ][а-яё\-]+(?:\s+[А-ЯЁ][а-яё\-]+)?)/.exec(t);
    if (dir) r.signer = dir[2];

    // Наименование: строка с ООО/АО/ИП/«…»
    for (var i = 0; i < lines.length; i++) {
      var l = lines[i];
      if (/^(ООО|АО|ПАО|ЗАО|ОАО|ИП|Индивидуальный предприниматель|Общество с ограниченной)/i.test(l) || /«.+»|".+"/.test(l) && !/банк/i.test(l)) {
        r.name = l.replace(/^(полное\s+|сокращенное\s+)?наименование[\s:]*/i, '').replace(/\s*(ИНН|КПП).*$/i, '').trim();
        break;
      }
    }
    if (r.name) {
      r.name = r.name.replace(/"([^"]+)"/g, '«$1»');
      if (/^(ИП|Индивидуальный предприниматель)\b/i.test(r.name)) {
        r.kind = 'ip';
        var fio = r.name.replace(/^(ИП|Индивидуальный предприниматель)\s*/i, '').trim();
        if (fio && !r.signer) r.signer = fio;
      } else r.kind = 'org';
    } else if (r.inn && r.inn.length === 12) r.kind = 'ip';
    else if (r.inn) r.kind = 'org';
    Object.keys(r).forEach(function (k) { if (r[k] === '') delete r[k]; });
    return r;
  }

  // ---------- Проверки ----------
  function checkInn(inn) {
    var d = digits(inn).split('').map(Number);
    function cs(w) { var s = 0; for (var i = 0; i < w.length; i++) s += w[i] * d[i]; return (s % 11) % 10; }
    if (d.length === 10) return cs([2, 4, 10, 3, 5, 9, 4, 6, 8]) === d[9];
    if (d.length === 12) return cs([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) === d[10] && cs([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) === d[11];
    return false;
  }

  // ---------- Данные для шаблона ----------
  var BLANK = '______________________________';
  function v(x) { return (x === undefined || x === null || String(x).trim() === '') ? BLANK : String(x).trim(); }

  function clientName(f) {
    if (f.kind === 'ip') return 'Индивидуальный предприниматель ' + (f.signer || '').trim();
    return (f.name || '').trim();
  }

  function partyText(f) {
    if (f.kind === 'ip') {
      return 'Индивидуальный предприниматель ' + v(f.signer) + ', ОГРНИП ' + v(f.ogrn);
    }
    var fem = isFemale(f.signer);
    return v(f.name) + ', в лице ' + v(f.positionGen) + ' ' + v(f.signerGen) +
      ', ' + (fem ? 'действующей' : 'действующего') + ' на основании ' + v(f.basis);
  }

  function clientBlock(f) {
    var lines = ['', 'ИНН: ' + v(f.inn)];
    if (f.kind !== 'ip') lines.push('КПП: ' + v(f.kpp));
    lines.push((f.kind === 'ip' ? 'ОГРНИП: ' : 'ОГРН: ') + v(f.ogrn));
    lines.push('Адрес: ' + v(f.address));
    lines.push('Расчётный счёт: ' + v(f.rs));
    lines.push('Банк: ' + v(f.bank));
    lines.push('Корреспондентский счёт: ' + v(f.ks));
    lines.push('БИК: ' + v(f.bik));
    lines.push('Телефон: ' + v(f.phone));
    lines.push('E-mail: ' + v(f.email));
    return lines.join('\n');
  }

  function buildData(type, f, cfg) {
    cfg = cfg || {};
    var dp = dateParts(f.date);
    var d = {
      num: f.num || '______',
      day: dp.day, month: dp.month, year: dp.year,
      party: partyText(f),
      client_rest: clientBlock(f),
      client_name: clientName(f) || BLANK,
      signer_short: fioShort(f.signer) || '__________________________',
      our_email: cfg.ourEmail || BLANK
    };
    if (type === 'ops') {
      d.site = v(f.site); d.leads = fmtNum(f.leads) || '____'; d.tariff = v(f.tariff);
    } else if (type === 'gck') {
      d.leads = fmtNum(f.leads); d.leads_words = numWords(f.leads);
      d.sum = fmtNum(f.sum); d.sum_words = sumWords(f.sum);
      d.sites = (f.sites || '').trim() || '\n\n';
    } else if (type === 'kc') {
      d.contacts = fmtNum(f.contacts); d.sum = fmtNum(f.sum); d.sum_words = sumWords(f.sum);
      d.price_per = fmtNum(f.pricePer);
    } else if (type === 'ds') {
      d.base_num = v(f.baseNum); d.base_date = dateRu(f.baseDate) || BLANK;
      d.tariff = v(f.tariff); d.dialogs = fmtNum(f.dialogs);
      d.sum = fmtNum(f.sum); d.sum_words = sumWords(f.sum);
      var p = cfg.dsPrices || [];
      d.p1 = p[0] ? fmtNum(p[0]) : ''; d.p2 = p[1] ? fmtNum(p[1]) : '';
      d.p3 = p[2] ? fmtNum(p[2]) : ''; d.p4 = p[3] ? fmtNum(p[3]) : '';
    }
    return d;
  }

  // ---------- DaData: ответ findById/party и findById/bank → поля формы ----------
  // Должность из ЕГРЮЛ («ГЕНЕРАЛЬНЫЙ ДИРЕКТОР») → «в лице генерального директора»
  function positionGenitive(post) {
    var nouns = { 'директор': 'директора', 'президент': 'президента', 'председатель': 'председателя',
      'руководитель': 'руководителя', 'управляющий': 'управляющего', 'ректор': 'ректора', 'начальник': 'начальника' };
    // Каждую часть через запятую склоняем до первого существительного включительно
    return String(post || '').trim().toLowerCase().split(/\s*,\s*/).filter(String).map(function (part) {
      var done = false;
      return part.split(/\s+/).map(function (w) {
        if (done) return w;
        if (nouns[w]) { done = true; return nouns[w]; }
        if (/ый$/.test(w)) return w.replace(/ый$/, 'ого');
        if (/[жшчщ]ий$/.test(w)) return w.replace(/ий$/, 'его');
        if (/[гкх]ий$/.test(w)) return w.replace(/ий$/, 'ого');
        if (/ой$/.test(w)) return w.replace(/ой$/, 'ого');
        done = true;
        if (/[бвгджзклмнпрстфхцчшщ]$/.test(w)) return w + 'а';
        if (/ь$/.test(w)) return w.replace(/ь$/, 'я');
        return w;
      }).join(' ');
    }).join(', ');
  }
  function capWords(s) { return String(s || '').toLowerCase().replace(/(^|[\s-])([а-яёa-z])/g, function (m, a, b) { return a + b.toUpperCase(); }); }
  function fromDadataParty(s) {
    if (!s || !s.data) return null;
    var d = s.data, out = { inn: d.inn || '', ogrn: d.ogrn || '' };
    out.address = d.address ? (d.address.unrestricted_value || d.address.value || '') : '';
    if (d.type === 'INDIVIDUAL') {
      out.kind = 'ip';
      if (d.fio) out.signer = [d.fio.surname, d.fio.name, d.fio.patronymic].filter(Boolean).join(' ');
      else if (d.name && d.name.full) out.signer = capWords(d.name.full);
    } else {
      out.kind = 'org';
      out.kpp = d.kpp || '';
      out.name = String((d.name && (d.name.short_with_opf || d.name.full_with_opf)) || s.value || '').replace(/"([^"]*)"/g, "«$1»");
      if (d.management && d.management.name) out.signer = capWords(d.management.name);
      if (d.management && d.management.post) out.positionGen = positionGenitive(d.management.post);
    }
    if (d.state && d.state.status && d.state.status !== 'ACTIVE') out.warning = 'Внимание: по данным ЕГРЮЛ организация не действует (' + d.state.status + ').';
    return out;
  }
  function fromDadataBank(s) {
    if (!s || !s.data) return null;
    return { bank: (s.data.name && s.data.name.payment) || s.value || '', ks: s.data.correspondent_account || '' };
  }

  var api = {
    fmtNum: fmtNum, numWords: numWords, sumWords: sumWords, dateParts: dateParts, dateRu: dateRu,
    fioGenitive: fioGenitive, fioShort: fioShort, isFemale: isFemale, parseCard: parseCard,
    checkInn: checkInn, buildData: buildData, clientName: clientName, cap: cap,
    positionGenitive: positionGenitive, fromDadataParty: fromDadataParty, fromDadataBank: fromDadataBank
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ContractLogic = api;
})(this);
