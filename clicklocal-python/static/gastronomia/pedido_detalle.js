(() => {
  const modal = document.querySelector("#pedidoDetalleModal");
  if (!modal) return;

  const escapar = (valor) => String(valor ?? "").replace(
    /[&<>'"]/g,
    (caracter) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[caracter],
  );
  const dinero = (valor) => new Intl.NumberFormat("es-AR", {
    style: "currency", currency: "ARS", maximumFractionDigits: 0,
  }).format(Number(valor || 0));

  function abrirDetalle(pedido) {
    modal.querySelector("#pedidoModalTitulo").textContent = `Pedido #${pedido.numero_pedido}`;
    modal.querySelector("#pedidoModalEstados").textContent = `${pedido.estado} · Pago ${pedido.estado_pago || "pendiente"}`;
    const datos = [
      ["Cliente", `${pedido.nombre_cliente || ""} ${pedido.apellido_cliente || ""}`.trim() || "Sin informar"],
      ["WhatsApp", pedido.telefono_cliente || "Sin informar"],
      ["Origen", pedido.origen], ["Modalidad", pedido.tipo_entrega],
      ["Forma de pago", pedido.forma_pago || "Sin informar"],
      ["Dirección", pedido.direccion_entrega || "No corresponde"],
      ["Referencia", pedido.referencia_direccion || "Sin referencia"],
      ["Fecha", pedido.created_at_mostrar || pedido.created_at],
    ];
    modal.querySelector("#pedidoModalDatos").innerHTML = datos.map(
      ([clave, valor]) => `<div class="pedido-modal-dato"><small>${escapar(clave)}</small><strong>${escapar(valor)}</strong></div>`,
    ).join("");
    const items = Array.isArray(pedido.detalle_items)
      ? pedido.detalle_items : (Array.isArray(pedido.detalle) ? pedido.detalle : []);
    modal.querySelector("#pedidoModalProductos").innerHTML = items.length
      ? items.map((item) => {
        const opciones = Array.isArray(item.opciones) && item.opciones.length
          ? `<ul>${item.opciones.map((opcion) => `<li>${escapar(opcion.nombre || "Extra")}${opcion.precio ? ` (+${escapar(dinero(opcion.precio))})` : ""}</li>`).join("")}</ul>` : "";
        const nota = item.nota ? `<p>Aclaración: ${escapar(item.nota)}</p>` : "";
        return `<div class="pedido-modal-producto"><strong>${escapar(item.cantidad || 0)}× ${escapar(item.nombre || "Producto")}</strong>${opciones}${nota}</div>`;
      }).join("") : "<p>Sin detalle disponible.</p>";
    const totales = [
      ["Subtotal", pedido.subtotal], ["Envío", pedido.costo_envio],
      ["Descuento", pedido.descuento], ["Total", pedido.total],
    ];
    modal.querySelector("#pedidoModalTotales").innerHTML = totales.map(
      ([clave, valor], indice) => `<div class="pedido-modal-total ${indice === 3 ? "pedido-modal-total--final" : ""}"><span>${clave}</span><strong>${escapar(dinero(valor))}</strong></div>`,
    ).join("");
    modal.querySelector("#pedidoModalObservaciones").textContent = pedido.observaciones
      ? `Observaciones: ${pedido.observaciones}` : "";
    modal.querySelector("#pedidoModalAnulacion").innerHTML = pedido.estado === "cancelado"
      ? `<strong>ANULADA</strong><p>Motivo: ${escapar(pedido.motivo_cancelacion || "Sin informar")}</p><p>Fecha: ${escapar(pedido.cancelado_at_mostrar || pedido.cancelado_at || "Sin informar")}</p>`
      : "";
    modal.showModal();
  }

  document.addEventListener("click", (evento) => {
    const botonDetalle = evento.target.closest(".js-ver-detalle");
    if (botonDetalle) {
      evento.preventDefault();
      try { abrirDetalle(JSON.parse(botonDetalle.dataset.pedido)); }
      catch (error) { console.warn("No se pudo abrir el detalle.", error); }
    }
    if (evento.target.closest("[data-cerrar-modal]")) modal.close();
  });
  modal.addEventListener("click", (evento) => {
    if (evento.target === modal) modal.close();
  });
})();
