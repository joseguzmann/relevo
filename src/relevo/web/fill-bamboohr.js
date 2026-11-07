// Fills a BambooHR application in the person's own Safari with the answers they approved in Relevo.
// It never touches the captcha or the submit button: the person does both.
(async () => {
  const D = __DATA__;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const done = text => (typeof completion === 'function' ? completion(text) : alert(text));
  const setValue = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    for (const t of ['input', 'change', 'blur']) el.dispatchEvent(new Event(t, {bubbles: true}));
  };
  const labelOf = input => (input.closest('label')?.innerText || '').trim();

  if (!document.querySelector('[name=firstName]')) {
    const open = [...document.querySelectorAll('button, a')].find(b => /apply for this job/i.test(b.innerText));
    if (open) open.click();
    for (let i = 0; i < 30 && !document.querySelector('[name=firstName]'); i++) await sleep(300);
  }
  if (!document.querySelector('[name=firstName]')) return done('Relevo: no encontré el formulario en esta página.');

  async function fill(f) {
    if (f.kind === 'file') {
      const input = document.querySelector('input[type=file]');
      const bytes = Uint8Array.from(atob(D.cv.b64), c => c.charCodeAt(0));
      const dt = new DataTransfer();
      dt.items.add(new File([bytes], D.cv.name, {type: 'application/pdf'}));
      input.files = dt.files;
      input.dispatchEvent(new Event('change', {bubbles: true}));
      for (let i = 0; i < 30; i++) {
        await sleep(300);
        if (document.querySelector('form')?.innerText.includes(D.cv.name)) return true;
      }
      return false;
    }
    if (f.id === 'countryId.value') {
      const toggle = () => document.querySelector('button[aria-label^="Country"]');
      toggle().click();
      await sleep(400);
      const search = document.querySelector('.fab-MenuSearch__input');
      if (search) { setValue(search, f.value); await sleep(500); }
      const item = [...document.querySelectorAll('[role=menuitem]')].find(m => m.innerText.trim() === f.value);
      if (!item) return false;
      item.click();
      await sleep(1200);  // the province and postal code fields change with the country
      return toggle().getAttribute('aria-label').endsWith(f.value);
    }
    const group = [...document.querySelectorAll(`input[name="${f.id}"]`)];
    if (f.kind === 'yes_no') {
      const want = f.value === 'Yes';
      if (group[0].checked !== want) group[0].click();
      return group[0].checked === want;
    }
    if (f.kind === 'single_choice') {
      const pick = group.find(i => labelOf(i) === f.value);
      if (!pick) return false;
      pick.click();
      return pick.checked;
    }
    let box = document.querySelector(`[name="${f.id}"]`);
    if (f.id === 'dateAvailable') {
      const label = [...document.querySelectorAll('label')].find(l => l.innerText.trim().startsWith('Date Available'));
      box = label && document.getElementById(label.htmlFor);
    }
    if (!box) return false;
    setValue(box, String(f.value));
    return box.value === String(f.value);
  }

  const bad = [];
  for (const f of D.fields) {
    let ok = false;
    try { ok = await fill(f); } catch (e) { ok = false; }
    if (!ok) bad.push(f.label);
  }
  document.querySelector('iframe[src*="recaptcha"]')?.scrollIntoView({block: 'center'});
  done(bad.length
    ? `Relevo llenó ${D.fields.length - bad.length} de ${D.fields.length}. Revisa: ${bad.join(' · ')}`
    : `Relevo llenó los ${D.fields.length} campos. Resuelve el captcha y toca Submit Application.`);
})();
