(() => {
  function abrirWhatsApp(texto, url) {
    const mensaje = [texto, url].filter(Boolean).join("\n");
    const destino = `https://wa.me/?text=${encodeURIComponent(mensaje)}`;
    const ventana = window.open(destino, "_blank");
    if (ventana) ventana.opener = null;
    else window.location.assign(destino);
  }

  document.querySelectorAll("[data-compartir]").forEach((boton) => {
    boton.addEventListener("click", async () => {
      const title = boton.dataset.shareTitle || document.title;
      const text = boton.dataset.shareText || "";
      const url = boton.dataset.shareUrl || window.location.href;

      if (navigator.share) {
        try {
          await navigator.share({ title, text, url });
          return;
        } catch (error) {
          if (error && error.name === "AbortError") return;
        }
      }

      abrirWhatsApp(text, url);
    });
  });
})();
