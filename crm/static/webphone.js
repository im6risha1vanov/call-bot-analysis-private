// Телефон в браузере (crm/webphone.py): звонок прямо из вкладки CRM, без Talker.
// SIP идёт по WebSocket через наш сайт в Asterisk, оттуда в Mango от добавочного менеджера.
// Библиотека JsSIP грузится только при первом звонке. Как звонить, менеджер выбирает в карточке номера.
'use strict';

const WP = {ua: null, cfg: null, lib: null, session: null, number: '', started: 0, timer: null, muted: false, onState: null};
const WP_MODE_KEY = 'crm.dialMode';

const dialMode = () => S.me.webphone && store.get(WP_MODE_KEY, 'talker') === 'browser' ? 'browser' : 'talker';
const dialModeSeg = () => S.me.webphone && S.me.can_dial
  ? `<div class="dialmode"><span class="hint">Звонить</span>${seg('dialmode', [['talker', 'через Talker'], ['browser', 'из браузера']], dialMode(), 'Как звонить')}</div>` : '';

function wpLoadLib() {
  if (window.JsSIP) return Promise.resolve();
  WP.lib = WP.lib || new Promise((ok, fail) => {
    const s = document.createElement('script');
    s.src = '/static/vendor/jssip.min.js?v=3.10.1';
    s.onload = ok;
    s.onerror = () => { WP.lib = null; s.remove(); fail(new Error('Телефон не загрузился. Обновите страницу или позвоните через Talker.')); };
    document.head.appendChild(s);
  });
  return WP.lib;
}

async function wpConnect() {
  if (WP.ua && WP.ua.isConnected()) return WP.ua;
  await wpLoadLib();
  WP.cfg = WP.cfg || await api('/api/webphone');
  if (WP.ua) { try { WP.ua.stop(); } catch (e) { /* старый сокет уже закрыт */ } }
  const url = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + WP.cfg.ws;
  const ua = new JsSIP.UA({
    sockets: [new JsSIP.WebSocketInterface(url)], uri: `sip:${WP.cfg.user}@crm.invalid`,
    authorization_user: WP.cfg.user, password: WP.cfg.password, register: false, session_timers: false,
    user_agent: 'AveronCRM', connection_recovery_min_interval: 2, connection_recovery_max_interval: 30,
  });
  WP.ua = ua;
  await new Promise((ok, fail) => {
    const t = setTimeout(() => { ua.stop(); WP.ua = null; fail(new Error('Нет связи с телефонной станцией. Позвоните через Talker.')); }, 8000);
    ua.once('connected', () => { clearTimeout(t); ok(); });
    ua.start();
  });
  return ua;
}

const WP_CAUSE = {
  'Busy': 'Занято', 'Rejected': 'Клиент сбросил', 'No Answer': 'Не взял трубку', 'Unavailable': 'Номер недоступен',
  'Not Found': 'Номер не найден', 'Address Incomplete': 'Неполный номер', 'Canceled': 'Звонок отменён',
  'User Denied Media Access': 'Браузер не дал микрофон. Разрешите доступ к микрофону (значок слева от адреса) и позвоните снова.',
  'Authentication Error': 'Станция не приняла пароль телефона. Позвоните через Talker и скажите руководителю.',
  'Connection Error': 'Пропала связь со станцией.', 'Request Timeout': 'Станция не ответила вовремя.',
  'RTP Timeout': 'Пропал звук на линии.', 'Bad Media Description': 'Браузер и станция не договорились о звуке.',
};

// Звонок из браузера. onState(текст, вид) показывает ход звонка в карточке номера; вид: ring | talk | end | err.
async function wpCall(number, onState) {
  if (WP.session) throw new Error('Уже идёт звонок. Сначала положите трубку.');
  const ua = await wpConnect();
  WP.number = number; WP.onState = onState || null; WP.muted = false; WP.started = 0;
  const s = ua.call(`sip:${number}@crm.invalid`, {
    mediaConstraints: {audio: true, video: false}, pcConfig: {iceServers: []},
    rtcOfferConstraints: {offerToReceiveAudio: true, offerToReceiveVideo: false},
  });
  WP.session = s;
  // Не ждём полного сбора адресов (бывает до 40 секунд): станции с публичным адресом хватает первых.
  let iceWait = null;
  s.on('icecandidate', e => { if (!iceWait) iceWait = setTimeout(e.ready, 800); });
  s.on('sending', () => clearTimeout(iceWait));
  s.connection.addEventListener('track', e => { wpAudio().srcObject = e.streams[0]; });
  wpState('Соединяю…', 'ring');
  s.on('progress', () => wpState('Идут гудки…', 'ring'));
  s.on('confirmed', () => {
    WP.started = Date.now();
    wpState('Разговор', 'talk');
    clearInterval(WP.timer);
    WP.timer = setInterval(() => wpBar(), 1000);
  });
  s.on('ended', () => wpEnd(WP.started ? `Звонок завершён, ${dur(Math.round((Date.now() - WP.started) / 1000))}` : 'Звонок завершён', 'end'));
  s.on('failed', e => wpEnd(e.originator === 'local' && e.cause === 'Canceled' ? 'Звонок отменён' : WP_CAUSE[e.cause] || `Не получилось: ${e.cause}`,
    e.cause === 'Canceled' ? 'end' : 'err'));
}

function wpHangup() { if (WP.session) { try { WP.session.terminate(); } catch (e) { wpEnd('Звонок завершён', 'end'); } } }

function wpAudio() {
  let a = $('#wp-audio');
  if (!a) { a = document.createElement('audio'); a.id = 'wp-audio'; a.autoplay = true; a.hidden = true; document.body.appendChild(a); }
  return a;
}

function wpState(text, kind) {
  WP.state = {text, kind};
  wpBar();
  WP.onState && WP.onState(text, kind);
}

function wpEnd(text, kind) {
  clearInterval(WP.timer);
  WP.session = null;
  const a = $('#wp-audio');
  if (a) a.srcObject = null;
  wpState(text, kind);
  const shown = WP.state;
  setTimeout(() => { if (WP.state === shown && !WP.session) $('#wpbar')?.remove(); }, kind === 'err' ? 8000 : 4000);
}

// Плашка звонка внизу экрана: видна на любой странице, пока идёт разговор.
function wpBar() {
  const st = WP.state || {text: '', kind: 'ring'};
  let bar = $('#wpbar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'wpbar';
    bar.setAttribute('role', 'status');
    bar.innerHTML = `<div class="wp-who"><b class="wp-num"></b><span class="wp-st"></span></div>
      <div class="wp-keys hidden">${'123456789*0#'.split('').map(k => `<button type="button" data-key="${k}">${k}</button>`).join('')}</div>
      <div class="wp-acts"><button class="btn small" type="button" data-wp="keys">Кнопки</button>
        <button class="btn small" type="button" data-wp="mute" aria-pressed="false">Выключить микрофон</button>
        <button class="btn small wp-hang" type="button" data-wp="hang">${svg('phone', '')}Положить трубку</button></div>`;
    bar.addEventListener('click', e => {
      const k = e.target.closest('[data-key]');
      if (k && WP.session) { try { WP.session.sendDTMF(k.dataset.key); } catch (err) { /* звонок ещё не соединён */ } return; }
      const b = e.target.closest('[data-wp]');
      if (!b) return;
      if (b.dataset.wp === 'hang') wpHangup();
      else if (b.dataset.wp === 'keys') $('.wp-keys', bar).classList.toggle('hidden');
      else if (b.dataset.wp === 'mute' && WP.session) {
        WP.muted = !WP.muted;
        if (WP.muted) WP.session.mute({audio: true}); else WP.session.unmute({audio: true});
        b.setAttribute('aria-pressed', String(WP.muted));
        b.textContent = WP.muted ? 'Включить микрофон' : 'Выключить микрофон';
      }
    });
    document.body.appendChild(bar);
  }
  bar.className = 'wp-' + st.kind;
  $('.wp-num', bar).textContent = phone(WP.number);
  $('.wp-st', bar).textContent = st.kind === 'talk' && WP.started ? `${st.text} · ${dur(Math.round((Date.now() - WP.started) / 1000))}` : st.text;
  const live = !!WP.session;
  $$('[data-wp="mute"], [data-wp="keys"]', bar).forEach(b => { b.hidden = !live || st.kind !== 'talk'; });
  $('[data-wp="hang"]', bar).hidden = !live;
  if (!live) $('.wp-keys', bar).classList.add('hidden');
}

// Случайно закрытая вкладка оборвёт разговор: браузер переспросит.
addEventListener('beforeunload', e => { if (WP.session) { e.preventDefault(); e.returnValue = ''; } });
