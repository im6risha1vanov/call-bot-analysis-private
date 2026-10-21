// Подробный разбор звонка: окно поверх любой страницы CRM (менеджер и директор).
// Открывается кнопкой с data-review="<id звонка>". Таймкоды ошибок, удачных
// мест и реплик расшифровки перематывают запись на это место.
'use strict';
(() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const LEAD_IN = 2;  // начинаем чуть раньше реплики, чтобы услышать, к чему она

  const stamp = (at, tc) => at == null ? '' : `<button class="rvm-t" data-at="${at}" title="Перейти к этому месту записи">▶ ${esc(tc)}</button>`;
  const tcLinks = text => esc(text).replace(/\[(\d{1,2}):(\d{2})\]/g, (m, a, b) => stamp(+a * 60 + +b, `${a}:${b}`));
  const dur = s => s ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : '';

  function item(m, kind) {
    return `<div class="rvm-item ${kind}">
      <div class="rvm-ih">${stamp(m.at, m.timecode)}${m.label ? `<b>${esc(m.label)}</b>` : ''}</div>
      ${m.what ? `<div>${esc(m.what)}</div>` : ''}
      ${m.quote ? `<div class="rvm-q">${m.speaker ? esc(m.speaker) + ': ' : ''}«${esc(m.quote)}»</div>` : ''}
      ${m.say ? `<div class="rvm-say">Как можно было: «${esc(m.say)}»</div>` : ''}</div>`;
  }

  function body(r) {
    const mark = r.success ? ['good', 'Результат: ' + r.outcomes.join(', ')] : r.chance === 'hot' ? ['bad', 'Горячий, потерян']
      : r.chance === 'normal' ? ['warn', 'Потерян, шанс был'] : r.reviewed ? ['', 'Без шанса'] : ['', 'Не разбирался'];
    const misses = r.misses.length ? r.misses.map(m => item(m, 'miss')).join('') : '';
    const good = [...r.evidence, ...r.positives];
    return `<header class="rvm-head"><div>
        <span class="rvm-chip ${mark[0]}">${esc(mark[1])}</span>
        <h2>${esc(r.manager || 'Менеджер')} · ${esc(phone(r.phone))}</h2>
        <div class="rvm-mut">${esc(r.at || '')}${r.seconds ? ' · ' + dur(r.seconds) : ''} · звонок ${r.id}</div></div>
        <button class="rvm-x" aria-label="Закрыть">×</button>
        ${r.rec ? '<audio class="rvm-audio" controls preload="metadata" src="/api/recording/' + r.id + '"></audio>'
          : '<div class="rvm-mut rvm-audio">Записи этого звонка в Mango нет, таймкоды только для ориентира.</div>'}</header>
      <div class="rvm-body">
        ${r.summary ? `<section><h3>Итог</h3><div class="rvm-pre">${tcLinks(r.summary)}</div></section>` : ''}
        ${misses ? `<section><h3>Ошибки по времени</h3>${misses}${r.can_train ? '<button class="rvm-train">Потренировать этот момент в тренажёре</button><div class="rvm-mut rvm-train-out"></div>' : ''}</section>` : ''}
        ${good.length ? `<section><h3>Что получилось</h3>${good.map(m => item(m, 'good')).join('')}</section>` : ''}
        ${r.details ? `<section><h3>Подробный разбор</h3><div class="rvm-pre">${tcLinks(r.details)}</div></section>` : ''}
        ${r.facts.length || r.next_step ? `<section><h3>О клиенте</h3><dl class="rvm-kv">${r.facts.map(f => `<dt>${esc(f.name)}</dt><dd>${esc(f.value)}</dd>`).join('')}
          ${r.next_step ? `<dt>Договорённость</dt><dd>${esc(r.next_step)}</dd>` : ''}</dl></section>` : ''}
        ${r.transcript.length ? `<details class="rvm-tr"><summary>Расшифровка разговора · ${r.transcript.length} реплик</summary>
          ${r.transcript.map(l => `<div class="rvm-line" data-line="${l.at}">${stamp(l.at, l.timecode)}<b>${esc(l.who)}</b> ${esc(l.text)}</div>`).join('')}</details>` : ''}
      </div>`;
  }

  async function train(btn, id) {
    const out = btn.nextElementSibling;
    btn.disabled = true;
    try {
      const r = await fetch('/api/review/' + encodeURIComponent(id) + '/train', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CRM': '1'}, body: '{}'});
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.link) throw new Error(d.error || 'Не получилось создать тренировку.');
      out.innerHTML = `Тренажёр повторит этот момент: клиент тот же, попробуй довести до шага. <a href="${esc(d.link)}" target="_blank" rel="noopener">Открыть тренажёр в Telegram</a>`;
      window.open(d.link, '_blank', 'noopener');
    } catch (e) { out.textContent = e.message; btn.disabled = false; }
  }

  async function open(id) {
    const veil = document.createElement('div'); veil.className = 'rvm-veil';
    const box = document.createElement('div'); box.className = 'rvm'; box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true');
    box.innerHTML = '<header class="rvm-head"><div class="rvm-mut">Загружаю разбор…</div><button class="rvm-x" aria-label="Закрыть">×</button></header>';
    document.body.append(veil, box);
    const close = () => { const a = box.querySelector('audio'); if (a) a.pause(); veil.remove(); box.remove(); document.removeEventListener('keydown', onKey, true); };
    const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
    document.addEventListener('keydown', onKey, true);
    veil.addEventListener('click', close);
    box.addEventListener('click', e => {
      if (e.target.closest('.rvm-x')) return close();
      const tr = e.target.closest('.rvm-train');
      if (tr) { train(tr, id); return; }
      const t = e.target.closest('.rvm-t');
      if (!t) return;
      const a = box.querySelector('audio');
      if (!a) return;
      a.currentTime = Math.max(0, +t.dataset.at - LEAD_IN);
      a.play().catch(() => {});
    });
    let r;
    try {
      const res = await fetch('/api/review/' + encodeURIComponent(id), {credentials: 'same-origin'});
      r = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(r.error || 'Разбор не открылся.');
    } catch (e) {
      box.innerHTML = `<header class="rvm-head"><div>${esc(e.message)}</div><button class="rvm-x" aria-label="Закрыть">×</button></header>`;
      return;
    }
    box.innerHTML = body(r);
    // CRM добавляет в шапку свои кнопки (позвонить клиенту): static/calls.js.
    document.dispatchEvent(new CustomEvent('crm-review', {detail: {box, review: r, close}}));
    const a = box.querySelector('audio');
    if (a) {
      a.addEventListener('error', () => {
        const m = document.createElement('div'); m.className = 'rvm-mut'; m.textContent = 'Mango не отдал запись. Попробуйте позже.'; a.replaceWith(m);
      });
      // Подсвечиваем реплику, которая звучит сейчас.
      const lines = [...box.querySelectorAll('.rvm-line')];
      a.addEventListener('timeupdate', () => {
        let cur = null;
        for (const l of lines) { if (+l.dataset.line <= a.currentTime) cur = l; else break; }
        lines.forEach(l => l.classList.toggle('now', l === cur));
      });
    }
    box.querySelector('.rvm-x').focus();
  }

  document.addEventListener('click', e => {
    const b = e.target.closest('[data-review]');
    if (!b) return;
    e.preventDefault(); e.stopPropagation();
    open(b.dataset.review);
  }, true);
  window.CRMReview = {open};
})();
