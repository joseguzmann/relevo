self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('push', e => {
  const d = e.data ? e.data.json() : {};
  e.waitUntil(self.registration.showNotification(d.title || 'Relevo', {body: d.body || '', data: {id: d.id}, icon: 'icon-192.png'}));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = '/#' + (e.notification.data?.id ?? '');
  e.waitUntil(self.clients.matchAll({type: 'window'}).then(list => {
    for (const c of list) { c.navigate(url); return c.focus(); }
    return self.clients.openWindow(url);
  }));
});
