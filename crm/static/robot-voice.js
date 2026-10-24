// Вкладка «Голос» в разделе «Робот» (директор): тестовый звонок роботом, голос и манера речи,
// произношение, проверка фразы на слух. Сохранённое действует со следующего звонка.
'use strict';

// Ползунок: [ключ, название, min, max, шаг, подпись слева, подпись справа, вид значения]
const ROBOT_VOICE = [
  ['speed', 'Скорость речи', 0.7, 1.2, 0.01, 'медленнее', 'быстрее', 'x'],
  ['stability', 'Ровность голоса', 0, 1, 0.05, 'живее, интонация гуляет', 'ровнее', '%'],
];
const ROBOT_MANNER = [
  ['casual', 'Разговорность', 0, 1, 0.05, 'как диктор', '«щас», «пожалста», «ну»', '%'],
  ['bg_noise', 'Фон на линии', 0, 1, 0.05, 'тишина', 'шум офиса', '%'],
  ['pause_ms', 'Пауза между фразами', 0, 600, 10, 'слитно', 'с паузой', 's'],
  ['tail_ms', 'Ждать, пока клиент договорит', 600, 2000, 50, 'отвечает быстрее', 'реже перебивает', 's'],
];
const ROBOT_EXTRA = [
  ['similarity', 'Схожесть с исходным голосом', 0, 1, 0.05, '', '', '%'],
  ['style', 'Выразительность', 0, 1, 0.05, 'спокойно', 'эмоционально', '%'],
  ['tempo_vary', 'Разный темп: вопросы медленнее', 0, 0.3, 0.01, 'один темп', 'заметно', '%'],
  ['inner_pause_ms', 'Пауза на знаках внутри фразы', 40, 600, 10, '', '', 's'],
  ['filler_after_ms', '«Угу…», если модель думает дольше', 900, 3000, 50, '', '', 's'],
];
// Переключатель может вести несколько настроек сразу: они значат для слуха одно и то же
const ROBOT_TOGGLES = [
  [['fillers', 'backchannel'], 'Живые реакции', '«угу…» или «записываю…», если модель долго думает; «угу» по ходу рассказа клиента'],
  [['calm', 'one_breath', 'fewer_commas'], 'Спокойная ровная речь', 'без «!» и восторгов, без пауз на запятых внутри фразы'],
  [['goodbye_wait'], 'Прощаться по-человечески', 'ответить «всего доброго» на прощание клиента и только потом положить трубку'],
];
const ROBOT_EXTRA_TOGGLES = [
  [['whole_reply'], 'Реплика целиком', 'интонация ровнее через всю реплику, ответ на 0,3–0,6 с позже'],
  [['speaker_boost'], 'Усиление сходства с голосом', ''],
];
const rbRub = v => Number(v).toLocaleString('ru-RU', {maximumFractionDigits: 2}) + ' ₽';
const rbValue = (v, kind) => kind === 'x' ? '×' + v.toFixed(2) : kind === 's' ? (v / 1000).toFixed(v < 1000 && v % 100 ? 2 : 1).replace('.', ',') + ' с'
  : Math.round(v * 100) + ' %';
let robotAudio = null;

async function robotVoiceView(view) {
  const d = await api('/api/robot');
  const cfg = {...d.settings};
  const slider = ([id, label, min, max, step, left, right, kind]) => `<div class="rb-sl">
    <div class="rb-sl-top"><label for="rb-${id}">${label}</label><output id="rb-${id}-v"></output></div>
    <input type="range" id="rb-${id}" data-k="${id}" data-kind="${kind}" min="${min}" max="${max}" step="${step}" value="${cfg[id]}">
    ${left || right ? `<div class="rb-sl-ends"><span>${left}</span><span>${right}</span></div>` : ''}</div>`;
  const toggle = ([keys, label, hint]) => `<label class="rb-sw"><input type="checkbox" data-c="${keys.join(',')}"${keys.every(k => cfg[k]) ? ' checked' : ''}>
    <span class="rb-sw-ui" aria-hidden="true"></span><span><b>${label}</b>${hint ? `<span class="muted">${hint}</span>` : ''}</span></label>`;
  const voiceNames = Object.entries(d.voices || {});
  const knownVoice = voiceNames.some(([, id]) => id === cfg.voice_id);
  const ro = d.readonly ? ' disabled' : '';
  view.innerHTML = `<div class="grid2"><div class="rb-col">
    <section class="card blk"><div class="card-h"><h2>Позвонить роботом</h2>${d.up ? '<span class="chip good">робот включён</span>' : '<span class="chip bad">робот выключен</span>'}</div>
      <p class="lead">${d.up ? '' : 'Включается на сервере: <code>bash scripts/voice_test.sh sip</code>. '}Звонит с сохранёнными настройками. Только тем, кто согласился на звонок.</p>
      <div class="outform">
        <label class="field">Номер<input id="rb-phone" inputmode="tel" placeholder="+7 999 123-45-67"></label>
        <label class="field">Сценарий<select id="rb-op">${d.operators.map(o => `<option value="${esc(o.id)}">${esc(o.name)} · ${esc(o.company)}</option>`).join('')}</select></label>
        <div class="row wide"><button class="btn primary" type="button" id="rb-call"${ro}>Позвонить роботом</button>
        ${d.test_phones.length ? `<button class="btn" type="button" id="rb-me"${ro}>Позвонить мне</button>` : ''}</div></div>
      <p class="hint" id="rb-call-st"></p></section>
    <section class="card blk" data-css="--i:1"><div class="card-h"><h2>Набор</h2></div>
      <p class="lead">Отвечает везде YandexGPT, наборы отличаются голосом. Выбор действует со следующего звонка.</p>
      <div class="rb-presets" role="radiogroup">${(d.presets || []).map(p => `<label class="rb-preset">
        <input type="radio" name="rb-preset" value="${esc(p.key)}"${p.key === d.preset ? ' checked' : ''}${ro}>
        <span class="rb-preset-ui"><span class="rb-preset-top"><b>${esc(p.name)}</b>
          <span class="rb-preset-cost">${p.cost_min ? '≈ ' + money(p.cost_min) + ' / мин' : ''}</span></span>
          <span class="muted">${esc(p.llm)} · ${esc(p.tts)}</span>
          <span>${esc(p.quality)}</span>
          ${p.tts_price != null ? `<span class="muted">Синтез: ≈ ${rbRub(p.tts_price)} за 1000 символов</span>` : ''}</span></label>`).join('')}</div>
      <p class="hint">${(d.presets || []).some(p => p.cost_min) ? 'Минута — оценка по звонкам робота за 30 дней: связь, распознавание, синтез и модель.' : 'Оценка минуты появится после звонков робота.'}</p></section>
    <section class="card blk" data-css="--i:1"><div class="card-h"><h2>Голос</h2></div>
      <label class="field">Чей голос<select id="rb-voice">
        ${voiceNames.map(([name, id]) => `<option value="${esc(id)}"${id === cfg.voice_id ? ' selected' : ''}>${esc(name)}</option>`).join('')}
        <option value=""${knownVoice ? '' : ' selected'}>Другой, по voice_id…</option></select></label>
      <label class="field" id="rb-vid-row"${knownVoice ? ' hidden' : ''}>voice_id<input id="rb-voice_id" value="${esc(cfg.voice_id)}" autocomplete="off"></label>
      ${ROBOT_VOICE.map(slider).join('')}
      <details class="rb-more"><summary>Найти другой голос или сделать клон</summary>
        <h3>Библиотека ElevenLabs</h3>
        <p class="muted">«Слушать» играет образец, «Взять» добавляет голос в аккаунт и выбирает его.</p>
        <div class="outform"><label class="field">Поиск<input id="rb-vq" placeholder="например calm, office, Анна" autocomplete="off"></label>
          <label class="field">Пол<select id="rb-vg"><option value="female">женский</option><option value="male">мужской</option><option value="">любой</option></select></label>
          <label class="field">Тип<select id="rb-vu"><option value="conversational">разговорный</option><option value="">любой</option></select></label>
          <button class="btn" type="button" id="rb-vfind">Найти</button></div>
        <div id="rb-vres"></div>
        <h3>Клон голоса</h3>
        <p class="muted">1–3 минуты чистой речи одного человека, без клиента и музыки. Только с его согласия.</p>
        <div class="outform"><label class="field">Название<input id="rb-cname" value="Ольга" autocomplete="off"></label>
          <label class="field">Записи (mp3, wav, m4a)<input type="file" id="rb-cfiles" accept="audio/*" multiple></label>
          <button class="btn" type="button" id="rb-clone"${ro}>Сделать клон</button></div>
        <div class="muted" id="rb-cst"></div></details></section>
    <section class="card blk" data-css="--i:2"><div class="card-h"><h2>Как говорит</h2></div>
      ${ROBOT_MANNER.map(slider).join('')}
      <div class="rb-sws">${ROBOT_TOGGLES.map(toggle).join('')}</div></section>
    <section class="card blk" data-css="--i:3"><div class="card-h"><h2>Произношение</h2></div>
      <p class="lead" data-css="margin-bottom:10px">Слово и как его читать, по строке на слово. «+» после ударной гласной, «*» — все формы слова.</p>
      <textarea id="rb-pron" class="rb-area" rows="3" placeholder="столешниц* = столе+шниц">${esc(cfg.pronounce || '')}</textarea>
      <details class="rb-more"><summary>Уже в словаре</summary><pre class="muted">${esc(cfg.pronounce_default || '')}</pre></details></section>
    ${moreBlock('Дополнительно', 'редкие настройки', `<div class="rb-extra">
      ${ROBOT_EXTRA.map(slider).join('')}
      <div class="rb-sws">${ROBOT_EXTRA_TOGGLES.map(toggle).join('')}</div></div>`, 4)}
    </div>
    <div class="rb-col">
    <section class="card rb-try blk" data-css="--i:1"><div class="card-h"><h2>Проверка на слух</h2></div>
      <textarea id="rb-text" class="rb-area" rows="3">Замер и дизайн-проект у нас бесплатные. По фасадам что смотрите: плёнку ПВХ, эмаль, пластик? Специалист вам перезвонит завтра.</textarea>
      <div class="row"><button class="btn" type="button" id="rb-listen">▶ Слушать</button>
        <button class="btn primary" type="button" id="rb-save"${d.can_save && !d.readonly ? '' : ' disabled'}>Сохранить</button>
        <button class="btn ghost" type="button" id="rb-reset">Отменить</button></div>
      <p class="hint" id="rb-st">${d.can_save ? 'Меняйте настройки слева и слушайте здесь. Сохранённое действует со следующего звонка.' : 'Робот не установлен на этом сервере: сохранить некуда.'}</p></section></div></div>`;
  const show = el => {
    const v = Number(el.value);
    $(`#${el.id}-v`, view).textContent = rbValue(v, el.dataset.kind);
    el.style.setProperty('--p', ((v - el.min) / (el.max - el.min) * 100) + '%');
  };
  $$('[data-k]', view).forEach(el => { show(el); el.addEventListener('input', () => show(el)); });
  const voiceSel = $('#rb-voice', view), voiceId = $('#rb-voice_id', view);
  voiceSel.addEventListener('change', () => {
    if (voiceSel.value) voiceId.value = voiceSel.value;
    $('#rb-vid-row', view).hidden = !!voiceSel.value;
  });
  const collect = () => {
    const out = {voice_id: voiceId.value.trim(), model: cfg.model, preset: $('[name=rb-preset]:checked', view)?.value || ''};
    $$('[data-k]', view).forEach(el => { out[el.dataset.k] = Number(el.value); });
    $$('[data-c]', view).forEach(el => el.dataset.c.split(',').forEach(k => { out[k] = el.checked; }));
    out.pronounce = $('#rb-pron', view).value;
    return out;
  };
  const st = (text, err) => { const el = $('#rb-st', view); el.textContent = text; el.className = err ? 'hint bad-text' : 'hint'; };
  $('#rb-listen', view).addEventListener('click', async () => {
    const s = collect(), btn = $('#rb-listen', view);
    if (!s.voice_id) return st('Выберите голос или впишите voice_id.', true);
    robotAudio = robotAudio || new AudioContext(); await robotAudio.resume();
    busy(btn, true); st('Синтезирую…');
    try {
      const r = await fetch('/api/robot/preview', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CRM': '1'},
        body: JSON.stringify({text: $('#rb-text', view).value, settings: s})});
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Синтез не получился.');
      const rate = Number(r.headers.get('X-Rate') || 48000), pcm = new Int16Array(await r.arrayBuffer());
      const buf = robotAudio.createBuffer(1, pcm.length, rate), ch = buf.getChannelData(0);
      for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
      const src = robotAudio.createBufferSource(); src.buffer = buf; src.connect(robotAudio.destination); src.start();
      st(`Фраза ${(pcm.length / rate).toFixed(1)} с. Это ещё не сохранено.`);
    } catch (e) { st(e.message, true); }
    busy(btn, false);
  });
  $('#rb-save', view).addEventListener('click', async e => {
    const btn = e.currentTarget;
    busy(btn, true);
    try { await api('/api/robot/settings', {method: 'POST', body: collect()}); toast('Сохранено. Следующий звонок робота пойдёт с этими настройками.'); }
    catch (err) { st(err.message, true); }
    busy(btn, false);
  });
  $('#rb-reset', view).addEventListener('click', () => reload());
  $$('[name=rb-preset]', view).forEach(el => el.addEventListener('change', async () => {
    try {
      await api('/api/robot/settings', {method: 'POST', body: collect()});
      toast(`Набор «${d.presets.find(p => p.key === el.value).name}» включён со следующего звонка. «Слушать» теперь звучит его голосом.`);
    } catch (err) { st(err.message, true); }
  }));
  const useVoice = (id, name, voices) => {
    if (voices) voiceSel.innerHTML = Object.entries(voices).map(([n, v]) => `<option value="${esc(v)}">${esc(n)}</option>`).join('') + '<option value="">Другой, по voice_id…</option>';
    if (![...voiceSel.options].some(o => o.value === id)) voiceSel.insertAdjacentHTML('beforeend', `<option value="${esc(id)}">${esc(name)}</option>`);
    voiceSel.value = id; voiceId.value = id; $('#rb-vid-row', view).hidden = true;
    st(`Голос «${name}» выбран. Нажмите «Слушать», затем «Сохранить».`);
  };
  let vPage = 0, vSample = null;
  const findVoices = async more => {
    const box = $('#rb-vres', view);
    vPage = more ? vPage + 1 : 0;
    if (!more) box.innerHTML = '<div class="sk line"></div>';
    try {
      const r = await api('/api/robot/voices' + qs({q: $('#rb-vq', view).value, gender: $('#rb-vg', view).value, use: $('#rb-vu', view).value, page: vPage}));
      const rows = r.items.map(v => `<div class="rb-voice"><div><b>${esc(v.name)}</b> <span class="muted">${esc([v.gender === 'female' ? 'жен.' : v.gender === 'male' ? 'муж.' : '', v.age, v.accent, v.descriptive, v.use_case].filter(Boolean).join(' · '))}${v.users ? ` · взяли ${v.users}` : ''}</span>
          ${v.description ? `<div class="muted">${esc(v.description)}</div>` : ''}</div>
        <div class="row">${v.preview_url ? `<button class="btn small" type="button" data-vs="${esc(v.preview_url)}">Слушать</button>` : ''}
          <button class="btn small primary" type="button" data-va="${esc(v.owner)}|${esc(v.voice_id)}" data-vn="${esc(v.name)}"${ro}>Взять</button></div></div>`).join('');
      if (!more) box.innerHTML = '';
      $('#rb-vmore', view)?.remove();
      box.insertAdjacentHTML('beforeend', (rows || (more ? '' : '<div class="empty">Ничего не нашлось.</div>')) + (r.more ? '<button class="btn small" type="button" id="rb-vmore">Ещё</button>' : ''));
      $('#rb-vmore', view)?.addEventListener('click', () => findVoices(true));
    } catch (e) { box.innerHTML = `<div class="muted bad-text">${esc(e.message)}</div>`; }
  };
  $('#rb-vfind', view).addEventListener('click', () => findVoices(false));
  $('#rb-vq', view).addEventListener('keydown', e => { if (e.key === 'Enter') findVoices(false); });
  $('#rb-vres', view).addEventListener('click', async e => {
    const play = e.target.closest('[data-vs]'), take = e.target.closest('[data-va]');
    if (play) {
      if (vSample) { vSample.pause(); URL.revokeObjectURL(vSample.src); }
      play.disabled = true;
      try {
        const r = await fetch('/api/robot/voices/sample?' + new URLSearchParams({url: play.dataset.vs}), {credentials: 'same-origin'});
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Образец не загрузился.');
        vSample = new Audio(URL.createObjectURL(await r.blob())); await vSample.play();
      } catch (err) { st(err.message, true); }
      play.disabled = false;
    } else if (take) {
      const [owner, id] = take.dataset.va.split('|');
      busy(take, true);
      try {
        const r = await api('/api/robot/voices/add', {method: 'POST', body: {owner, voice_id: id, name: take.dataset.vn}});
        useVoice(r.voice_id, take.dataset.vn, r.voices);
        doneBtn(take, 'Взят');
      } catch (err) { st(err.message, true); busy(take, false); }
    }
  });
  $('#rb-clone', view).addEventListener('click', async e => {
    const btn = e.currentTarget, files = $('#rb-cfiles', view).files, out = $('#rb-cst', view), name = $('#rb-cname', view).value.trim() || 'Клон';
    if (!files.length) return (out.textContent = 'Выберите файлы с записями голоса.');
    const form = new FormData();
    form.append('name', name);
    [...files].forEach(f => form.append('files', f, f.name));
    busy(btn, true); out.textContent = 'Отправляю записи в ElevenLabs, это до минуты…';
    try {
      const r = await fetch('/api/robot/voices/clone', {method: 'POST', credentials: 'same-origin', headers: {'X-CRM': '1'}, body: form});
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || 'Клон не получился.');
      useVoice(data.voice_id, name, data.voices);
      out.textContent = `Клон «${name}» готов и выбран. Послушайте и сохраните.`;
    } catch (err) { out.textContent = err.message; }
    busy(btn, false);
  });
  const dialNow = async number => {
    const out = $('#rb-call-st', view);
    out.textContent = 'Заказываю звонок…';
    try {
      const r = await api('/api/robot/call', {method: 'POST', body: {phone: number, operator: $('#rb-op', view).value}});
      out.textContent = `Робот звонит на ${phone(r.number)}. Итог появится на вкладке «Звонки» после разговора.`;
    } catch (e) { out.textContent = e.message; }
  };
  $('#rb-call', view).addEventListener('click', async () => {
    const n = $('#rb-phone', view).value;
    if (!n.trim()) return ($('#rb-call-st', view).textContent = 'Введите номер.');
    if (await askConfirm(`Робот позвонит на ${n}. Звонить?`, 'Позвонить')) dialNow(n);
  });
  $('#rb-me', view)?.addEventListener('click', () => dialNow(d.test_phones[0]));
}
