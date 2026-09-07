self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (error) {
    payload = { body: event.data ? event.data.text() : "" };
  }

  const title = payload.title || "ClickLocal";
  const options = {
    body: payload.body || "Tenés una nueva notificación.",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    data: { url: "/admin" },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const adminUrl = new URL("/admin", self.location.origin).href;

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true })
      .then((clientes) => {
        const clienteAdmin = clientes.find((cliente) => {
          return new URL(cliente.url).origin === self.location.origin;
        });
        if (clienteAdmin) {
          return clienteAdmin.focus().then(() => clienteAdmin.navigate(adminUrl));
        }
        return self.clients.openWindow(adminUrl);
      })
  );
});
