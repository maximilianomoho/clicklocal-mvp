(() => {
  const modal = document.querySelector("#ticketModal");
  if (!modal) return;

  const escapar = (valor) => String(valor ?? "").replace(
    /[&<>'"]/g,
    (caracter) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[caracter],
  );
  const texto = (valor) => String(valor || "").trim();
  const etiqueta = (valor) => texto(valor).replaceAll("_", " ");
  const modalidades = {
    delivery: "Delivery", retiro: "Retiro", mesa: "Salón", mostrador: "Mostrador",
  };
  const dinero = (valor) => new Intl.NumberFormat("es-AR", {
    style: "currency", currency: "ARS", minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(Number(valor || 0));

  function abrirTicket(pedido) {
    modal.querySelector("#ticketPedido").textContent = `Pedido #${pedido.numero_pedido}`;

    const datos = [
      ["Fecha", pedido.created_at_mostrar || pedido.created_at],
      ["Modalidad", modalidades[texto(pedido.tipo_entrega).toLowerCase()] || etiqueta(pedido.tipo_entrega)],
    ].filter(([, valor]) => texto(valor));
    modal.querySelector("#ticketDatos").innerHTML = datos.map(
      ([nombre, valor]) => `<p><span>${escapar(nombre)}</span><strong>${escapar(valor)}</strong></p>`,
    ).join("");

    const items = Array.isArray(pedido.detalle_items)
      ? pedido.detalle_items
      : (Array.isArray(pedido.detalle) ? pedido.detalle : []);
    modal.querySelector("#ticketProductos").innerHTML = items.length
      ? items.map((item) => {
        const opciones = Array.isArray(item.opciones) && item.opciones.length
          ? `<ul>${item.opciones.map((opcion) => {
            const importe = Number(opcion.precio || opcion.precio_extra || 0);
            return `<li>+ ${escapar(opcion.nombre || "Extra")}${importe ? ` (${escapar(dinero(importe))})` : ""}</li>`;
          }).join("")}</ul>`
          : "";
        const subtotalItem = item.subtotal == null
          ? "" : `<strong>${escapar(dinero(item.subtotal))}</strong>`;
        return `<div class="ticket-producto"><div><span>${escapar(item.cantidad || 0)}× ${escapar(item.nombre || "Producto")}</span>${subtotalItem}</div>${opciones}</div>`;
      }).join("")
      : "<p>Sin productos disponibles.</p>";

    const subtotal = Number(pedido.subtotal || 0);
    const total = Number(pedido.total || 0);
    const envio = Number(pedido.costo_envio || 0);
    const descuento = Number(pedido.descuento || 0);
    const subtotalDifiere = Math.round(subtotal * 100) !== Math.round(total * 100);
    const totales = [];
    if (envio > 0 || descuento > 0 || subtotalDifiere) {
      totales.push(["Subtotal", subtotal]);
    }
    if (envio > 0) totales.push(["Envío", envio]);
    if (descuento > 0) totales.push(["Descuento", -descuento]);
    totales.push(["Total", pedido.total]);
    modal.querySelector("#ticketTotales").innerHTML = totales.map(
      ([nombre, valor], indice) => `<p${indice === totales.length - 1 ? ' class="ticket-total-final"' : ""}><span>${escapar(nombre)}</span><strong>${escapar(dinero(valor))}</strong></p>`,
    ).join("");

    const pagos = texto(pedido.forma_pago)
      ? [["Pago", etiqueta(pedido.forma_pago)]] : [];
    modal.querySelector("#ticketPago").innerHTML = pagos.map(
      ([nombre, valor]) => `<p><span>${escapar(nombre)}</span><strong>${escapar(valor)}</strong></p>`,
    ).join("");
    modal.showModal();
  }

  document.addEventListener("click", (evento) => {
    const boton = evento.target.closest(".js-ver-ticket");
    if (boton) {
      evento.preventDefault();
      try { abrirTicket(JSON.parse(boton.dataset.pedido)); }
      catch (error) { console.warn("No se pudo abrir el ticket.", error); }
      return;
    }
    if (evento.target.closest("[data-cerrar-ticket]")) modal.close();
    if (evento.target.closest("[data-imprimir-ticket]")) {
      document.body.classList.add("ticket-imprimiendo");
      window.print();
    }
  });

  window.addEventListener("afterprint", () => {
    document.body.classList.remove("ticket-imprimiendo");
  });
  modal.addEventListener("close", () => {
    document.body.classList.remove("ticket-imprimiendo");
  });
})();
