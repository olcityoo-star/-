self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open("fridge-v1").then((cache) => cache.addAll(["/", "/styles.css", "/app.js", "/icon.svg"]))
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.url.includes("/api/")) return;
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
