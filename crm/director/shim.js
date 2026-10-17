// Страница директора раньше жила в claude.ai и брала данные через window.claude:
// Google Таблицы через подключение, общую базу, пользователя, скачивание файлов.
// Здесь те же вызовы идут на наш сервер, поэтому сама страница не меняется.
'use strict';
(() => {
  const call = async (path, opts = {}) => {
    const init = {method: opts.method || 'GET', credentials: 'same-origin', headers: {}};
    if (opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    if (init.method !== 'GET') init.headers['X-CRM'] = '1';
    const r = await fetch(path, init);
    const data = await r.json().catch(() => ({}));
    if (r.status === 401) { location.href = '/'; }
    if (!r.ok) { const e = new Error(data.error || 'Ошибка сервера'); e.code = r.status === 403 ? 'not_granted' : 'tool_error'; e.retryable = r.status >= 500; throw e; }
    return data;
  };

  // ---- Google Таблицы ----
  const mcp = {
    async callTool(server, tool, input) {
      if (tool !== 'get_values') throw Object.assign(new Error('Недоступно'), {code: 'not_in_manifest'});
      const res = await call('/api/gs', {method: 'POST', body: {spreadsheetId: input.spreadsheetId, range: input.range}});
      return {payload: {values: res.values || []}};
    },
  };

  // ---- общая база: документы и коллекции, обновление раз в 30 секунд ----
  const listeners = new Set();
  const segs = p => p.split('/').filter(Boolean);
  const snapDoc = (path, row) => ({id: segs(path).pop(), exists: !!row, data: () => row ? JSON.parse(JSON.stringify(row)) : undefined});
  async function fire(l) {
    try {
      if (l.kind === 'doc') {
        const r = await call('/api/docs?path=' + encodeURIComponent(l.path));
        l.cb(snapDoc(l.path, r.data));
      } else {
        const r = await call('/api/docs?collection=' + encodeURIComponent(l.path));
        let docs = r.docs.map(d => snapDoc(d.path, d.data));
        if (l.order) {
          const [f, dir] = l.order;
          docs.sort((a, b) => { const x = a.data()[f] ?? '', y = b.data()[f] ?? ''; return (x < y ? -1 : x > y ? 1 : 0) * (dir === 'desc' ? -1 : 1); });
        }
        if (l.lim) docs = docs.slice(0, l.lim);
        l.cb({docs, size: docs.length, empty: !docs.length});
      }
    } catch (e) { l.err && l.err(e); }
  }
  const refresh = () => listeners.forEach(fire);
  setInterval(() => { if (!document.hidden) refresh(); }, 30000);
  const write = async (path, op, data) => { await call('/api/docs', {method: 'POST', body: {path, op, data}}); refresh(); };
  const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  const query = (path, order = null, lim = 0) => ({
    orderBy: (f, dir = 'asc') => query(path, [f, dir], lim),
    limit: n => query(path, order, n),
    onSnapshot(cb, err) { const l = {kind: 'col', path, cb, err, order, lim}; listeners.add(l); fire(l); return () => listeners.delete(l); },
    add: async data => { const id = newId(); await write(path + '/' + id, 'set', data); return {id}; },
    doc: id => doc(path + '/' + id),
  });
  const doc = path => ({
    id: segs(path).pop(),
    onSnapshot(cb, err) { const l = {kind: 'doc', path, cb, err}; listeners.add(l); fire(l); return () => listeners.delete(l); },
    get: async () => snapDoc(path, (await call('/api/docs?path=' + encodeURIComponent(path))).data),
    set: data => write(path, 'set', data),
    update: data => write(path, 'update', data),
    delete: () => write(path, 'delete'),
  });
  const db = {doc, collection: path => query(path)};

  // ---- кто смотрит и скачивание ----
  let me = null;
  const meReady = call('/api/me').then(m => { me = m; return m; }).catch(() => null);
  const user = {
    id: async () => { await meReady; return me ? 'crm:' + me.role + ':' + (me.ext || '') : 'crm'; },
    can: async () => { await meReady; return !!me; },
    isOwner: () => false, canEdit: () => false,
    me: async () => { await meReady; return {id: 'crm', name: me ? me.name : ''}; },
    profiles: async ids => Object.fromEntries((ids || []).map(id => [id, {name: id.startsWith('crm:') ? 'Сотрудник' : ''}])),
  };
  const downloads = {
    async save({filename, data}) {
      const blob = data instanceof Blob ? data : new Blob([data], {type: 'text/plain;charset=utf-8'});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = filename || 'export.csv';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    },
  };
  const caps = {mcp, db, user, downloads};
  window.claude = {use: async name => caps[name] || null};

  // ---- полоска входов сверху ----
  meReady.then(m => {
    if (!m) return;
    const bar = document.createElement('div');
    bar.className = 'crm-switch';
    bar.innerHTML = `<b>Директор</b><a href="/?work">Очереди менеджеров и кабинеты клиентов</a>${m.test ? '<a href="/test-exit">Сменить вход</a><span>Тестовый вход</span>' : '<a href="/logout">Выйти</a>'}`;
    document.body.prepend(bar);
  });
})();
