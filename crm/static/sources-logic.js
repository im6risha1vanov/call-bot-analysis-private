/* Логика подбора источников без интерфейса. Работает в браузере (window.SPLogic) и в Node (module.exports) для тестов. */
(function(root){
  'use strict';

  var TYPE_LABEL = {direct:'Конкуренты', aggr:'Агрегаторы', trigger:'Триггер', adjacent:'Смежные'};
  var TYPE_SHARE = {direct:'50–60%', aggr:'10–20%', trigger:'20–30%', adjacent:'до 10%, только тест'};

  var CITY_SLUG = {'москва':'moskva','санкт-петербург':'sankt-peterburg','новороссийск':'novorossiysk','краснодар':'krasnodar','сочи':'sochi','анапа':'anapa',
    'геленджик':'gelendzhik','ростов-на-дону':'rostov-na-donu','екатеринбург':'ekaterinburg','новосибирск':'novosibirsk','казань':'kazan',
    'нижний новгород':'nizhniy_novgorod','самара':'samara','уфа':'ufa','челябинск':'chelyabinsk','омск':'omsk','красноярск':'krasnoyarsk',
    'пермь':'perm','воронеж':'voronezh','волгоград':'volgograd','тюмень':'tyumen','ставрополь':'stavropol','саратов':'saratov','тула':'tula',
    'ярославль':'yaroslavl','иркутск':'irkutsk','хабаровск':'habarovsk','владивосток':'vladivostok','калининград':'kaliningrad','севастополь':'sevastopol'};
  var CITIES = ['Москва','Санкт-Петербург','Новороссийск','Краснодар','Сочи','Анапа','Геленджик','Ростов-на-Дону','Екатеринбург','Новосибирск','Казань',
    'Нижний Новгород','Самара','Уфа','Челябинск','Омск','Красноярск','Пермь','Воронеж','Волгоград','Тюмень','Ставрополь','Саратов','Тула',
    'Ярославль','Иркутск','Хабаровск','Владивосток','Калининград','Севастополь'];

  // Длина цикла решения: от неё зависит, брать ли ретро.
  var CYCLE = {
    long:['realty-new','realty-sec','house','auto','edu','franch','leasing','wedding','tender','metal','kitchen','mebel'],
    short:['autoservice','autolaw','clean','clean-b2b','spec','cargo','climate']
  };

  function norm(s){return String(s||'').toLowerCase().replace(/ё/g,'е').replace(/[«»"'.,()]/g,' ').replace(/\s+/g,' ').trim();}
  function stem(w){return w.length>5 ? w.slice(0,w.length>7?5:4) : w;}

  function findNiches(q, list){
    q = String(q||'').trim();
    if (!q) return [];
    if (/^\d{2}(\.\d{1,2}){0,2}$/.test(q)) {
      return list.filter(function(n){return n.okved.some(function(o){return o.indexOf(q)===0 || q.indexOf(o)===0;});});
    }
    var nq = norm(q), words = nq.split(' ').filter(function(w){return w.length>2;}).map(stem);
    var scored = list.map(function(n){
      var hay = norm(n.name+' '+n.syn.join(' '));
      var s = 0;
      if (norm(n.name).indexOf(nq)>=0) s += 5;
      n.syn.forEach(function(x){var nx=norm(x); if (nx===nq) s+=6; else if ((' '+nx+' ').indexOf(' '+nq)>=0 || (nx.length>3 && (' '+nq+' ').indexOf(' '+nx+' ')>=0)) s+=3;});
      words.forEach(function(w){ if (hay.indexOf(w)>=0) s += 1; });
      return {n:n, s:s};
    }).filter(function(x){return x.s>0;});
    scored.sort(function(a,b){return b.s-a.s;});
    return scored.map(function(x){return x.n;});
  }
  function byId(id, list){for (var i=0;i<list.length;i++) if (list[i].id===id) return list[i]; return null;}

  function genericNiche(text){
    return {id:'custom', name:text, group:'', okved:[], syn:[], custom:true,
      cats:[
        {t:'direct', name:'Прямые конкуренты клиента', why:'Компании, которые продают ту же услугу в этом городе.', q:[text, text+' цена', 'заказать '+text]},
        {t:'aggr', name:'Площадки исполнителей и агрегаторы', why:'Там выбирают исполнителя под задачу.', q:['найти исполнителя '+text], sites:['profi.ru','youdo.com','avito.ru']},
        {t:'trigger', name:'Что человек делает перед покупкой', why:'Подумайте, какое событие запускает спрос (переезд, ремонт, открытие бизнеса, покупка авто) и найдите компании этого шага.', q:[]},
        {t:'adjacent', name:'Смежные услуги той же аудитории', why:'Только на тест, небольшой долей.', q:[text+' под ключ']}
      ],
      stop:[], tip:'Этой ниши нет в справочнике. Используйте общие запросы ниже и правило: донор продаёт ту же услугу, и к нему обращаются в момент покупки.'};
  }

  function withSeg(q, seg){return seg==='eco' ? q+' недорого' : seg==='prem' ? q+' премиум' : q;}

  function links(q, city){
    var c = String(city||'').trim(), text = c ? q+' '+c : q, e = encodeURIComponent(text);
    var slug = CITY_SLUG[norm(c)] || '';
    return {
      ymaps:'https://yandex.ru/maps/?text='+e,
      gis:'https://2gis.ru/search/'+e,
      yandex:'https://yandex.ru/search/?text='+e,
      avito: slug ? 'https://www.avito.ru/'+slug+'?q='+encodeURIComponent(q) : 'https://www.avito.ru/rossiya?q='+e
    };
  }

  function cycleOf(niche){
    if (!niche) return 'mid';
    if (CYCLE.long.indexOf(niche.id)>=0) return 'long';
    if (CYCLE.short.indexOf(niche.id)>=0) return 'short';
    return 'mid';
  }
  function signalAdvice(niche){
    var c = cycleOf(niche);
    var text = c==='long'
      ? 'Решение принимают неделями и месяцами. Берите звонки и СМС за текущий период и ретро звонки и СМС за 30–60 дней: многие ещё не купили. Сайты берите для доноров без телефона или в дополнение.'
      : c==='short'
      ? 'Решение принимают за дни. Берите свежие звонки и СМС. Ретро старше 7 дней почти не работает: человек уже нашёл исполнителя.'
      : 'Берите звонки и СМС за текущий период и ретро за 14–30 дней. Сайты берите для доноров без телефона или в дополнение.';
    return {cycle:c, text:text};
  }
  function signalsFor(d, niche){
    var c = cycleOf(niche), out = [];
    if (d.phone){ out.push('Звонки','СМС'); if (c!=='short') out.push('Ретро звонки','Ретро СМС'); }
    if (d.site) out.push('Сайт');
    return out;
  }

  function stopList(niche){
    var all = ((niche && niche.stop) || []).concat(root.SP_GLOBAL_STOP || []), seen = {}, out = [];
    all.forEach(function(s){var k=norm(s[0]); if(!seen[k]){seen[k]=1; out.push(s);}});
    return out;
  }
  function matchStop(d, niche){
    var hay = ' '+norm(d.name)+' '+norm(d.site)+' ', list = stopList(niche);
    for (var i=0;i<list.length;i++){
      var p = norm(list[i][0]);
      if (!p) continue;
      var hit = p.length<=4 ? new RegExp('(^|[\\s.,])'+p.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'([\\s.,]|$)').test(hay) : hay.indexOf(p)>=0;
      if (hit) return list[i];
    }
    return null;
  }

  function normPhone(s){
    var d = String(s||'').replace(/\D/g,'');
    if (d.length===10) d = '7'+d;
    if (d.length===11 && d[0]==='8') d = '7'+d.slice(1);
    return d;
  }
  function fmtPhone(d){
    d = String(d||'');
    if (/^7\d{10}$/.test(d)) return '+7 '+d.slice(1,4)+' '+d.slice(4,7)+'-'+d.slice(7,9)+'-'+d.slice(9);
    return d;
  }
  function normSite(s){
    s = String(s||'').trim().toLowerCase().replace(/^https?:\/\//,'').replace(/^www\./,'');
    return s.split(/[\/?#\s]/)[0];
  }
  var SITE_RE = /(^|[^@\w.-])((?:https?:\/\/)?(?:www\.)?[a-zа-я0-9][a-zа-я0-9-]*(?:\.[a-zа-я0-9-]+)*\.(?:ru|рф|com|net|org|su|pro|online|site|store|shop|info|biz|moscow|spb))(?=$|[\s\/,;)])/i;
  function parseCard(text){
    var t = String(text||''), r = {name:'', phone:'', site:''};
    var pm = t.match(/(?:\+7|8)[\s\-()]*\d[\d\s\-()]{8,16}\d/);
    if (pm){ var p = normPhone(pm[0]); if (/^7\d{10}$/.test(p)) r.phone = p; }
    var sm = t.match(SITE_RE);
    if (sm) r.site = normSite(sm[2]);
    var lines = t.split(/\r?\n/).map(function(x){return x.trim();}).filter(Boolean);
    for (var i=0;i<lines.length;i++){
      var l = lines[i];
      if (l.length>80) continue;
      if (/(?:\+7|8)[\s\-()]*\d/.test(l) && l.replace(/[\d\s+\-()]/g,'').length<3) continue;
      if (SITE_RE.test(l) && l.split(' ').length===1) continue;
      if (/^(открыто|закрыто|ежедневно|пн|вт|ср|чт|пт|сб|вс|\d)/i.test(l)) continue;
      l = l.replace(/(?:\+7|8)[\s\-()]*\d[\d\s\-()]{8,16}\d/g,' ').replace(/\S+@\S+/g,' ').replace(new RegExp(SITE_RE.source,'gi'),' ').replace(/(^|\s)\/\S*/g,' ').replace(/\s*[,;|·]\s*(?=[,;|·]|$)/g,'').replace(/[\s,;|·]+$/,'').trim();
      if (!l) continue;
      r.name = l; break;
    }
    return r;
  }

  function evaluate(d, niche, client){
    var cl = norm(client);
    if (cl && cl.length>2 && (norm(d.name).indexOf(cl)>=0 || (d.site && norm(d.site).indexOf(normSite(client))>=0)))
      return {score:0, level:'bad', label:'это клиент', reason:'Это сам клиент: его номер не даст новых лидов.'};
    var st = matchStop(d, niche);
    if (st) return {score:0, level:'bad', label:'стоп-лист', reason:'Стоп-лист: '+st[0]+' ('+st[1]+').'};
    var score = (d.checks||[]).filter(Boolean).length;
    if (d.proven) return {score:4, level:'good', label:'проверен', reason:''};
    if (score>=3) return {score:score, level:'good', label:score+'/4 · берём', reason:''};
    if (score===2) return {score:score, level:'mid', label:'2/4 · запас', reason:''};
    return {score:score, level:'bad', label:score+'/4 · не брать', reason:''};
  }

  function selectionWarnings(donors, niche, client){
    var out = [], ev = donors.map(function(d){return {d:d, e:evaluate(d, niche, client)};});
    var keep = ev.filter(function(x){return x.e.level!=='bad';}), good = ev.filter(function(x){return x.e.level==='good';}).length;
    if (!donors.length) return out;
    if (good<8) out.push({kind:'warn', text:'Сильных доноров '+good+', нужно хотя бы 8. Иначе за первую неделю не будет данных, чтобы понять, какие работают.'});
    if (good>15) out.push({kind:'info', text:'Сильных доноров '+good+'. Для первой волны хватит 10–15, остальных оставьте на следующую базу.'});
    if (keep.length){
      var by = {direct:0, aggr:0, trigger:0, adjacent:0};
      keep.forEach(function(x){var c=niche.cats[x.d.cat]||niche.cats[0]; by[c.t]++;});
      var n = keep.length;
      if (by.direct/n < 0.4) out.push({kind:'warn', text:'Прямых конкурентов '+by.direct+' из '+n+'. Они дают основную конверсию, их должно быть не меньше половины.'});
      if (by.adjacent/n > 0.15) out.push({kind:'warn', text:'Смежных доноров '+by.adjacent+' из '+n+'. Это тестовая категория, держите её до 10%.'});
      if (!keep.some(function(x){return x.d.phone;})) out.push({kind:'warn', text:'Ни у одного донора нет телефона: звонки и СМС парсить будет не по чему.'});
    }
    var bad = ev.length-keep.length;
    if (bad) out.push({kind:'info', text:'Отклонено '+bad+': они не попадут в выгрузку.'});
    if (!out.some(function(w){return w.kind==='warn';}) && good>=8) out.push({kind:'ok', text:'Подборка готова: '+good+' сильных доноров, баланс категорий в норме.'});
    return out;
  }

  // ---- приём номеров из закладки «Собрать номера» (static/sources-collect.js) ----

  // Поисковый запрос из 2ГИС или Яндекс Карт без города и сегмента: по нему узнаём нишу и категорию.
  function bareQuery(q, city){
    var t = ' '+norm(q)+' ', c = norm(city);
    if (c) t = t.split(' '+c+' ').join(' ');
    return t.replace(/ (недорого|премиум) /g,' ').replace(/\s+/g,' ').trim();
  }
  // Категория ниши, из запроса которой открыт поиск: самый длинный запрос категории внутри поиска.
  function catForQuery(niche, q){
    var nq = ' '+norm(q)+' ', best = 0, len = 0;
    ((niche && niche.cats) || []).forEach(function(c, i){
      (c.q||[]).forEach(function(x){var nx = norm(x); if (nx.length>len && nq.indexOf(' '+nx+' ')>=0){best = i; len = nx.length;}});
    });
    return best;
  }
  // Ниша по запросу закладки, когда в подборщике ниша ещё не выбрана.
  function nicheForQuery(q, city, list){
    var bare = bareQuery(q, city);
    for (var i=0;i<list.length;i++){
      if (list[i].cats.some(function(c){return (c.q||[]).some(function(x){return norm(x)===bare;});})) return list[i].id;
    }
    var found = findNiches(bare, list);
    return found.length ? found[0].id : '';
  }
  // Номера из закладки в подборку: телефон или сайт обязательны, дубли по телефону и сайту убираем.
  // Новые доноры сразу «берём» (все 4 признака): менеджер снимает лишних глазами, стоп-лист отсеет оценка.
  function importItems(payload, donors, niche){
    var out = {added:[], dup:0, bad:0}, items = (payload && payload.items) || [];
    var cat = catForQuery(niche, payload && payload.q);
    var phones = {}, sites = {};
    (donors||[]).forEach(function(d){ if (d.phone) phones[d.phone]=1; if (d.site) sites[d.site]=1; });
    items.slice(0, 500).forEach(function(it){
      if (!it || typeof it!=='object'){out.bad++;return;}
      var phone = normPhone(it.p), site = normSite(it.s), name = String(it.n||'').replace(/\s+/g,' ').trim().slice(0,150);
      if (!/^7\d{10}$/.test(phone)) phone = '';
      if (site.length>100 || !/\./.test(site)) site = '';
      if (!phone && !site){out.bad++;return;}
      if ((phone && phones[phone]) || (site && sites[site])){out.dup++;return;}
      if (phone) phones[phone]=1;
      if (site) sites[site]=1;
      out.added.push({name:name, phone:phone, site:site, cat:cat, checks:[true,true,true,true], from:String(payload.src||'').slice(0,10)});
    });
    return out;
  }

  var api = {TYPE_LABEL:TYPE_LABEL, TYPE_SHARE:TYPE_SHARE, CITIES:CITIES, findNiches:findNiches, byId:byId, genericNiche:genericNiche,
    withSeg:withSeg, links:links, cycleOf:cycleOf, signalAdvice:signalAdvice, signalsFor:signalsFor, stopList:stopList, matchStop:matchStop,
    normPhone:normPhone, fmtPhone:fmtPhone, normSite:normSite, parseCard:parseCard, evaluate:evaluate, selectionWarnings:selectionWarnings,
    bareQuery:bareQuery, catForQuery:catForQuery, nicheForQuery:nicheForQuery, importItems:importItems};
  root.SPLogic = api;
  if (typeof module!=='undefined' && module.exports) module.exports = api;
})(typeof window!=='undefined' ? window : globalThis);
