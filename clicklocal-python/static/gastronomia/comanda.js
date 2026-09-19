(() => {
  const modal = document.querySelector("#comandaModal");
  if (!modal) return;

  const escapar = (valor) => String(valor ?? "").replace(
    /[&<>'"]/g,
    (caracter) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[caracter],
  );

  function abrirComanda(pedido) {
    modal.querySelector("#comandaTitulo").textContent = `Pedido #${pedido.numero_pedido}`;
    const fecha = pedido.created_at_mostrar || pedido.created_at || "";
    modal.querySelector("#comandaHora").textContent = fecha
      ? `Hora: ${String(fecha).slice(-5)}` : "Hora no disponible";
    modal.querySelector("#comandaModalidad").textContent =
      String(pedido.tipo_entrega || "Sin informar").replaceAll("_", " ");

    const items = Array.isArray(pedido.detalle_items)
      ? pedido.detalle_items : (Array.isArray(pedido.detalle) ? pedido.detalle : []);
    modal.querySelector("#comandaProductos").innerHTML = items.length
      ? items.map((item) => {
        const opciones = Array.isArray(item.opciones) && item.opciones.length
          ? `<ul>${item.opciones.map((opcion) => `<li>+ ${escapar(opcion.nombre || "Extra")}</li>`).join("")}</ul>`
          : "";
        const nota = item.nota
          ? `<p><strong>Aclaración:</strong> ${escapar(item.nota)}</p>` : "";
        return `<div class="comanda-producto"><strong>${escapar(item.cantidad || 0)}× ${escapar(item.nombre || "Producto")}</strong>${opciones}${nota}</div>`;
      }).join("")
      : "<p>Sin detalle disponible.</p>";

    const observaciones = String(pedido.observaciones || "").trim();
    const observacionesEl = modal.querySelector("#comandaObservaciones");
    observacionesEl.textContent = observaciones
      ? `Observaciones generales: ${observaciones}` : "";
    observacionesEl.hidden = !observaciones;
    modal.showModal();
  }

  document.addEventListener("click", (evento) => {
    const boton = evento.target.closest(".js-ver-comanda");
    if (boton) {
      evento.preventDefault();
      try { abrirComanda(JSON.parse(boton.dataset.pedido)); }
      catch (error) { console.warn("No se pudo abrir la comanda.", error); }
      return;
    }
    if (evento.target.closest("[data-cerrar-comanda]")) modal.close();
    if (evento.target.closest("[data-imprimir-comanda]")) {
      document.body.classList.add("comanda-imprimiendo");
      window.print();
    }
  });

  window.addEventListener("afterprint", () => {
    document.body.classList.remove("comanda-imprimiendo");
  });
  modal.addEventListener("close", () => {
    document.body.classList.remove("comanda-imprimiendo");
  });
})();
