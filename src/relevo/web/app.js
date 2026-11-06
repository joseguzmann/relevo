// All job text is third-party data: it only ever goes in through textContent, never innerHTML.
const root = document.getElementById('app');
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  kids.flat().forEach(k => k != null && n.append(k));
  return n;
};
const api = async (path, opts = {}) => {
  const r = await fetch('/api' + path, {credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, ...opts});
  if (r.status === 401 && path !== '/login') { login(); throw new Error('login'); }
  if (!r.ok) throw Object.assign(new Error(r.status), {body: await r.json().catch(() => ({}))});
  return r.json();
};
const STATE = {ready: 'listo', confirm: 'confirmar', missing: 'falta'};
const STATUS = {ready: 'Para enviar', sending: 'Enviando…', sent: 'Enviada', failed: 'No se envió', unclear: 'Revisar', manual: 'Manual', dropped: 'Descartada'};

function login() {
  const code = el('input', {type: 'password', placeholder: 'Clave', autocomplete: 'current-password'});
  const msg = el('p', {className: 'soft'});
  const go = el('button', {className: 'main', textContent: 'Entrar', onclick: async () => {
    try { await api('/login', {method: 'POST', body: JSON.stringify({passcode: code.value})}); route(); }
    catch { msg.textContent = 'Clave incorrecta'; }
  }});
  root.replaceChildren(el('h1', {textContent: 'Relevo'}), el('div', {className: 'card'}, code, el('p'), go, msg));
}

async function enablePush(btn) {
  const b64 = s => Uint8Array.from(atob((s + '='.repeat((4 - s.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
  if (await Notification.requestPermission() !== 'granted') { btn.textContent = 'Notificaciones bloqueadas'; return; }
  const {vapid} = await api('/me');
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: b64(vapid)});
  await api('/subscribe', {method: 'POST', body: JSON.stringify(sub.toJSON())});
  btn.remove();
}

async function list() {
  const items = await api('/items');
  const open = items.filter(i => ['ready', 'failed', 'unclear', 'sending'].includes(i.status));
  const rest = items.filter(i => !open.includes(i));
  const row = i => el('div', {className: 'card tap', onclick: () => { location.hash = i.id; }},
    i.warning ? el('div', {className: 'tag confirm', textContent: i.warning.discard ? '⚠️ Posible descarte' : '⚠️ Con duda'}) : null,
    el('div', {textContent: i.job.title || 'Vacante'}),
    el('div', {className: 'soft', textContent: `${i.job.company || ''} · ${i.job.match ?? '-'}/10 · ${STATUS[i.status] || i.status}`}),
    el('div', {className: 'soft', textContent: `${i.origin.channel} · ${i.origin.source} → ${i.origin.exit}`}));
  const views = [el('h1', {textContent: 'Relevo'})];
  if ('Notification' in window && Notification.permission !== 'granted') {
    const b = el('button', {className: 'main', textContent: 'Activar notificaciones'});
    b.onclick = () => enablePush(b);
    views.push(b, el('p'));
  }
  views.push(...(open.length ? open.map(row) : [el('p', {className: 'soft', textContent: 'Nada pendiente.'})]));
  if (rest.length) views.push(el('details', {className: 'history'},
    el('summary', {textContent: `Historial (${rest.length})`}), ...rest.map(row)));
  root.replaceChildren(...views);
}

function fieldInput(f, changes) {
  const set = v => { changes[f.id] = v; };
  if (f.kind === 'file') return el('div', {className: 'soft', textContent: 'CV adjunto'});
  if (f.kind === 'yes_no' || (f.kind === 'single_choice' && f.options.length && f.options.length <= 6)) {
    const opts = f.kind === 'yes_no' ? ['Yes', 'No'] : f.options;
    return opts.map(o => el('label', {className: 'opt'},
      el('input', {type: 'radio', name: f.id, checked: f.value === o, onchange: () => set(o)}), el('span', {textContent: o})));
  }
  if (f.kind === 'single_choice') {
    const s = el('select', {onchange: () => set(s.value)}, el('option', {value: '', textContent: '—'}),
      f.options.map(o => el('option', {value: o, textContent: o, selected: f.value === o})));
    return s;
  }
  if (f.kind === 'multi_choice') {
    const now = new Set(Array.isArray(f.value) ? f.value : f.value ? [f.value] : []);
    return f.options.map(o => el('label', {className: 'opt'},
      el('input', {type: 'checkbox', checked: now.has(o), onchange: e => { e.target.checked ? now.add(o) : now.delete(o); set([...now]); }}),
      el('span', {textContent: o})));
  }
  if (f.kind === 'long_text') return el('textarea', {value: f.value ?? '', oninput: e => set(e.target.value)});
  return el('input', {type: f.kind === 'number' ? 'number' : 'text', value: f.value ?? '', oninput: e => set(e.target.value)});
}

async function detail(id) {
  const item = await api('/items/' + id);
  const d = item.data, job = d.job || {};
  const changes = {}, mail = {}, accepted = new Set(d.accepted_unknown || []);
  const views = [el('button', {className: 'back', textContent: '‹ Volver', onclick: () => { location.hash = ''; }}),
    el('h1', {textContent: job.title || 'Vacante'}),
    d.warning ? el('div', {className: 'card warn'},
      el('div', {className: 'tag confirm', textContent: d.warning.discard ? '⚠️ Posible descarte' : '⚠️ Con duda'}),
      el('p', {textContent: d.warning.text})) : null,
    el('div', {className: 'card'},
      el('div', {textContent: `${job.company || ''} · ${job.match ?? '-'}/10`}),
      el('p', {className: 'soft', textContent: `Canal: ${item.origin.channel} · Fuente: ${item.origin.source}`}),
      el('p', {className: 'soft', textContent: `Sale por: ${item.origin.exit}`}),
      item.origin.location ? el('p', {className: 'soft', textContent: `Ubicación: ${item.origin.location}`}) : null,
      el('p', {className: 'soft', textContent: job.why || ''}),
      job.blockers && job.blockers !== '[]' ? el('p', {className: 'soft', textContent: 'Ojo: ' + job.blockers}) : null,
      d.reread ? el('p', {className: 'soft', textContent: 'Relectura: ' + d.reread.reason}) : null,
      el('a', {href: job.url, target: '_blank', rel: 'noopener', textContent: 'Abrir vacante'}),
      el('details', {}, el('summary', {textContent: 'Texto completo'}), el('pre', {textContent: job.description || ''})))];
  if ((d.history || []).length) views.push(el('details', {className: 'history'},
    el('summary', {textContent: `Historial con esta empresa (${d.history.length})`}),
    el('div', {className: 'card'}, d.history.map(h => el('div', {className: 'soft', textContent: `#${h.number} ${h.title} · ${h.status}`})))));
  if (item.reason && ['manual', 'failed', 'dropped'].includes(item.status)) views.push(el('div', {className: 'card soft', textContent: item.reason}));
  (d.unknown || []).length && views.push(el('h2', {textContent: 'Esto está en la página y no en los datos'}),
    ...d.unknown.map(u => el('label', {className: 'card opt'},
      el('input', {type: 'checkbox', checked: accepted.has(u), onchange: e => { e.target.checked ? accepted.add(u) : accepted.delete(u); }}),
      el('span', {textContent: u}))));
  if (d.email) views.push(el('h2', {textContent: 'Correo'}), el('div', {className: 'card'},
    el('label', {className: 'f', textContent: 'Para'}), el('input', {type: 'email', value: d.email.to, oninput: e => { mail.to = e.target.value; }}),
    el('p'), el('label', {className: 'f', textContent: 'Asunto'}), el('input', {type: 'text', value: d.email.subject, oninput: e => { mail.subject = e.target.value; }}),
    el('p'), el('label', {className: 'f', textContent: 'Mensaje'}), el('textarea', {value: d.email.body, rows: 12, oninput: e => { mail.body = e.target.value; }}),
    el('p', {className: 'soft', textContent: 'Lleva el CV en PDF.'})));
  if (d.fields) views.push(el('h2', {textContent: 'Campos'}), ...d.fields.map(f => el('div', {className: 'card'},
    el('label', {className: 'f', textContent: f.label + (f.required ? ' *' : '')}),
    el('div', {className: 'tag ' + f.state, textContent: STATE[f.state]}), fieldInput(f, changes),
    f.source ? el('div', {className: 'soft', textContent: f.source}) : null)));
  if (item.status === 'manual') views.push(el('p', {className: 'soft', textContent: 'Esta se postula a mano.'}));
  const save = () => api('/items/' + id, {method: 'PUT', body: JSON.stringify({fields: changes, email: mail, accepted_unknown: [...accepted]})});
  if (['ready', 'failed'].includes(item.status)) {
    const msg = el('div', {className: 'soft'});
    const send = el('button', {className: 'main', textContent: item.route === 'email' ? 'Enviar correo' : 'Postular', onclick: async () => {
      const label = send.textContent;
      working(send, item.route === 'email' ? 'Enviando…' : 'Postulando…');
      msg.className = 'soft'; msg.textContent = '';
      try {
        await save();
        await api(`/items/${id}/send`, {method: 'POST'});
        drop.remove();
        poll(id, msg);
      } catch (e) {
        send.disabled = false; send.textContent = label;
        const m = e.body?.detail?.missing;
        msg.className = 'soft missing';
        msg.textContent = m ? 'Falta: ' + m.join(' · ') : (typeof e.body?.detail === 'string' ? e.body.detail : 'No se pudo enviar');
      }
    }});
    const drop = el('button', {textContent: 'Descartar', onclick: async () => {
      await api(`/items/${id}/discard`, {method: 'POST'}); location.hash = '';
    }});
    views.push(el('div', {className: 'bar'}, el('div', {}, msg, el('div', {className: 'actions'}, drop, send))));
  } else if (item.status === 'sending') {
    const msg = el('div', {className: 'soft'}), busy = el('button', {className: 'main'});
    working(busy, 'Enviando…');
    views.push(el('div', {className: 'bar'}, el('div', {}, msg, busy)));
    poll(id, msg);
  } else if (item.status === 'unclear') {
    const answer = sent => async () => {
      await api(`/items/${id}/resolve`, {method: 'POST', body: JSON.stringify({sent})});
      detail(id);
    };
    const img = el('img', {className: 'shot', src: `/api/items/${id}/shot`, alt: 'Pantalla después de enviar', onerror: () => img.remove()});
    views.push(el('h2', {textContent: 'Sin confirmación'}), el('div', {className: 'card'},
      el('p', {textContent: 'Relevo tocó enviar, pero la página no confirmó. Mira si llegó el correo de acuse antes de decidir.'}),
      img, el('details', {}, el('summary', {textContent: 'Lo que respondió la página'}), el('pre', {textContent: item.reason || ''})),
      el('p'), el('button', {textContent: 'Sí se envió', onclick: answer(true)}), ' ',
      el('button', {textContent: 'No se envió', onclick: answer(false)})));
  } else views.push(el('div', {className: 'card', textContent: STATUS[item.status] || item.status}));
  if (['sent', 'unclear'].includes(item.status) && item.route !== 'email') {
    const box = el('div');
    views.push(el('details', {className: 'history', ontoggle: async e => {
      if (!e.target.open || box.childElementCount) return;
      const reqs = await api(`/items/${id}/sent`);
      box.append(...(reqs.length ? reqs.map(r => el('div', {className: 'card'},
        el('div', {className: 'soft', textContent: `${r.method} ${r.url}`}),
        el('div', {className: 'tag', textContent: 'Respuesta: ' + (r.answer || 'ninguna')}),
        el('pre', {className: 'soft', textContent: r.body || '(sin cuerpo)'}))) : [el('p', {className: 'soft', textContent: 'No hay registro de esta postulación.'})]));
    }}, el('summary', {textContent: 'Lo que se envió'}), box));
  }
  root.replaceChildren(...views);
}

function working(btn, text) {
  btn.disabled = true;
  btn.replaceChildren(el('span', {className: 'spin'}), text);
}

async function poll(id, msg) {
  msg.textContent = 'Tarda hasta 2 minutos. Sigue aunque cierres la app.';
  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 3000));
    if (location.hash.slice(1) !== String(id)) return;
    const it = await api('/items/' + id).catch(() => null);
    if (it && it.status !== 'sending') return detail(id);
  }
  msg.textContent = 'Sigue en proceso. Vuelve a abrirla en un momento.';
}

async function route() {
  try {
    const id = location.hash.slice(1);
    id ? await detail(id) : await list();
  } catch (e) { if (e.message !== 'login') root.replaceChildren(el('p', {textContent: 'Sin conexión con Relevo.'})); }
}
addEventListener('hashchange', route);
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js');
route();
