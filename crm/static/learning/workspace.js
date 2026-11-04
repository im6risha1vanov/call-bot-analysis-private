/* Bootstrap runs BEFORE app.js. Only the dedicated learning document has context.
   Missing/broken bootstrap must fail closed, never call the ordinary CRM API. */
(function () {
  'use strict';
  if (location.pathname !== '/learning/workspace') return;
  let classroom, failure;
  try {
    const packet = JSON.parse(document.getElementById('learning-workspace-context').textContent);
    classroom = new AveronClassroom.Classroom(packet, sessionStorage);
  } catch (e) { failure = 'Учебная учётка не открылась: ' + e.message; }
  const request = async (path, opts) => {
    if (failure) throw new Error(failure);
    return classroom.request(path, opts);
  };
  window.AveronLearningWorkspace = {classroom, request, failure};
  // Legacy modules using fetch (review.js) also stay entirely local. No fallback.
  window.fetch = async (resource, init = {}) => {
    const url = new URL(typeof resource === 'string' ? resource : resource.url, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) throw new Error('Сетевые запросы отключены в учебной учётке.');
    let body;
    if (init.body) body = JSON.parse(init.body);
    try { const data = await request(url.pathname + url.search, {method:init.method || 'GET',body}); return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}}); }
    catch (e) { return new Response(JSON.stringify({error:e.message}),{status:403,headers:{'Content-Type':'application/json'}}); }
  };
  document.addEventListener('click', event => {
    const link=event.target.closest('a[href]'); if(!link)return;
    const u=new URL(link.href,location.href);
    if(u.origin===location.origin && ((u.pathname===location.pathname && u.hash) || ['/learning/enter','/learning/exit'].includes(u.pathname)))return;
    event.preventDefault();event.stopImmediatePropagation();
    if(window.toast)toast('В учебной учётке внешние переходы и звонки отключены.',true);
  },true);
  document.addEventListener('submit', event => {event.preventDefault();event.stopImmediatePropagation();},true);
}());
