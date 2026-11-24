// Service worker del panel: recibe los avisos y los muestra, aunque el panel este cerrado.
// El aviso solo trae un titulo y un texto corto (sin remitentes ni asuntos).

self.addEventListener("push", (evento) => {
  let datos = {};
  try { datos = evento.data ? evento.data.json() : {}; } catch (e) { datos = {}; }
  const titulo = typeof datos.titulo === "string" ? datos.titulo : "Mail CRM";
  const texto = typeof datos.texto === "string" ? datos.texto : "Tenés mails nuevos para revisar.";
  evento.waitUntil(self.registration.showNotification(titulo, {
    body: texto, icon: "/icon-192.png", badge: "/icon-192.png", tag: "mail-crm",
  }));
});

// Tocar el aviso abre el panel (o lo trae al frente si ya estaba abierto).
self.addEventListener("notificationclick", (evento) => {
  evento.notification.close();
  evento.waitUntil((async () => {
    const abiertas = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const v of abiertas) {
      if (new URL(v.url).origin === self.location.origin) { await v.focus(); return; }
    }
    await self.clients.openWindow("/");
  })());
});
