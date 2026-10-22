(() => {
  const modal = document.querySelector("#pedidoModalBase");
  const comanda = document.querySelector("#pedidoComandaBase");
  if (!modal || !comanda) return;

  let pedidoActual = null;
  const escapar = (valor) => String(valor ?? "").replace(
    /[&<>'"]/g,
    (caracter) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[caracter],
  );
  const texto = (valor) => String(valor ?? "").trim();
  const etiqueta = (valor) => texto(valor).replaceAll("_", " ");
  const dinero = (valor) => new Intl.NumberFormat("es-AR", {
    style: "currency", currency: "ARS", minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(Number(valor || 0));
  const nombreCliente = (pedido) => (
    `${texto(pedido.nombre_cliente)} ${texto(pedido.apellido_cliente)}`.trim()
    || "Sin informar"
  );
  const itemsPedido = (pedido) => (
    Array.isArray(pedido.detalle_items)
      ? pedido.detalle_items
      : (Array.isArray(pedido.detalle) ? pedido.detalle : [])
  );
  const modalidad = (pedido) => etiqueta(pedido.tipo_entrega) || "Sin informar";

  function filasDatos(pedido) {
    const filas = [
      ["Cliente", nombreCliente(pedido)],
      ["WhatsApp", texto(pedido.telefono_cliente) || "Sin informar"],
      ["Entrega", modalidad(pedido)],
    ];
    if (texto(pedido.tipo_entrega).toLowerCase() === "delivery") {
      filas.push(["Domicilio", texto(pedido.direccion_entrega) || "Sin informar"]);
      filas.push(["Referencia", texto(pedido.referencia_direccion) || "Sin referencia"]);
    }
    filas.push(["Pago", etiqueta(pedido.forma_pago) || "Sin informar"]);
    if (pedido.paga_con !== null && pedido.paga_con !== undefined && texto(pedido.paga_con)) {
      filas.push(["Importe entregado", dinero(pedido.paga_con)]);
    }
    return filas;
  }

  function htmlProductos(pedido) {
    const items = itemsPedido(pedido);
    if (!items.length) return "<p>Sin detalle disponible.</p>";
    return items.map((item) => {
      const opciones = Array.isArray(item.opciones) ? item.opciones : [];
      const extras = opciones.length
        ? `<ul>${opciones.map((opcion) => {
          const importe = Number(opcion.precio || opcion.precio_extra || 0);
          return `<li>${escapar(opcion.nombre || "Extra")}${importe ? ` (+ ${escapar(dinero(importe))})` : ""}</li>`;
        }).join("")}</ul>`
        : "";
      const nota = texto(item.nota)
        ? `<p><strong>Nota:</strong> ${escapar(item.nota)}</p>` : "";
      const subtotal = item.subtotal === null || item.subtotal === undefined
        ? "" : `<p>${escapar(dinero(item.subtotal))}</p>`;
      return `<article class="pedido-modal-base-producto"><strong>${escapar(item.cantidad || 0)} × ${escapar(item.nombre || "Producto")}</strong>${subtotal}${extras}${nota}</article>`;
    }).join("");
  }

  function filasTotales(pedido) {
    return [
      ["Subtotal", pedido.subtotal],
      ["Envío", pedido.costo_envio],
      ["Descuento", pedido.descuento],
      ["Total", pedido.total],
    ];
  }

  function prepararModal(pedido) {
    pedidoActual = pedido;
    modal.querySelector("#pedidoModalBaseTitulo").textContent = `Pedido #${pedido.numero_pedido}`;
    modal.querySelector("#pedidoModalBaseFecha").textContent = pedido.created_at_mostrar || pedido.created_at || "";
    modal.querySelector("#pedidoModalBaseDatos").innerHTML = filasDatos(pedido).map(
      ([clave, valor]) => `<div class="pedido-modal-base-dato"><small>${escapar(clave)}</small><strong>${escapar(valor)}</strong></div>`,
    ).join("");
    modal.querySelector("#pedidoModalBaseProductos").innerHTML = htmlProductos(pedido);
    const observaciones = texto(pedido.observaciones);
    const observacionesEl = modal.querySelector("#pedidoModalBaseObservaciones");
    observacionesEl.textContent = observaciones ? `Observaciones: ${observaciones}` : "";
    observacionesEl.hidden = !observaciones;
    modal.querySelector("#pedidoModalBaseTotales").innerHTML = filasTotales(pedido).map(
      ([clave, valor], indice) => `<div class="pedido-modal-base-total ${indice === 3 ? "pedido-modal-base-total--final" : ""}"><span>${escapar(clave)}</span><strong>${escapar(dinero(valor))}</strong></div>`,
    ).join("");
  }

  function prepararComanda(pedido) {
    const observaciones = texto(pedido.observaciones);
    comanda.innerHTML = `
      <h1>Pedido #${escapar(pedido.numero_pedido)}</h1>
      <p><strong>Fecha:</strong> ${escapar(pedido.created_at_mostrar || pedido.created_at || "Sin informar")}</p>
      ${filasDatos(pedido).map(([clave, valor]) => `<p><strong>${escapar(clave)}:</strong> ${escapar(valor)}</p>`).join("")}
      <h2>Productos</h2>
      ${htmlProductos(pedido)}
      ${observaciones ? `<h2>Observaciones</h2><p>${escapar(observaciones)}</p>` : ""}
      <h2>Importes</h2>
      ${filasTotales(pedido).map(([clave, valor], indice) => `<p class="pedido-comanda-base-fila ${indice === 3 ? "pedido-comanda-base-total" : ""}"><span>${escapar(clave)}</span><strong>${escapar(dinero(valor))}</strong></p>`).join("")}
    `;
  }

  function imprimir(pedido) {
    prepararComanda(pedido);
    document.body.classList.add("pedido-base-imprimiendo");
    window.print();
  }

  function mensajeWhatsApp(pedido) {
    const lineas = [
      `Pedido #${pedido.numero_pedido}`,
      `Fecha: ${pedido.created_at_mostrar || pedido.created_at || "Sin informar"}`,
      `Cliente: ${nombreCliente(pedido)}`,
      `WhatsApp: ${texto(pedido.telefono_cliente) || "Sin informar"}`,
      `Entrega: ${modalidad(pedido)}`,
    ];
    if (texto(pedido.tipo_entrega).toLowerCase() === "delivery") {
      lineas.push(`Domicilio: ${texto(pedido.direccion_entrega) || "Sin informar"}`);
      lineas.push(`Referencia: ${texto(pedido.referencia_direccion) || "Sin referencia"}`);
    }
    lineas.push(`Pago: ${etiqueta(pedido.forma_pago) || "Sin informar"}`);
    if (pedido.paga_con !== null && pedido.paga_con !== undefined && texto(pedido.paga_con)) {
      lineas.push(`Importe entregado: ${dinero(pedido.paga_con)}`);
    }
    lineas.push("", "Productos:");
    const items = itemsPedido(pedido);
    if (!items.length) lineas.push("Sin detalle disponible");
    items.forEach((item) => {
      lineas.push(`${item.cantidad || 0} × ${item.nombre || "Producto"}`);
      (Array.isArray(item.opciones) ? item.opciones : []).forEach((opcion) => {
        const importe = Number(opcion.precio || opcion.precio_extra || 0);
        lineas.push(`  + ${opcion.nombre || "Extra"}${importe ? ` (${dinero(importe)})` : ""}`);
      });
      if (texto(item.nota)) lineas.push(`  Nota: ${item.nota}`);
    });
    if (texto(pedido.observaciones)) lineas.push("", `Observaciones: ${pedido.observaciones}`);
    lineas.push(
      "",
      `Subtotal: ${dinero(pedido.subtotal)}`,
      `Envío: ${dinero(pedido.costo_envio)}`,
      `Descuento: ${dinero(pedido.descuento)}`,
      `Total: ${dinero(pedido.total)}`,
    );
    return lineas.join("\n");
  }

  function compartir(pedido) {
    const url = `https://wa.me/?text=${encodeURIComponent(mensajeWhatsApp(pedido))}`;
    window.open(url, "_blank", "noopener,noreferrer");
  }

  function pedidoDesdeBoton(boton) {
    return JSON.parse(boton.dataset.pedido);
  }

  document.addEventListener("click", (evento) => {
    const ver = evento.target.closest("[data-ver-pedido]");
    const imprimirBoton = evento.target.closest("[data-imprimir-pedido]");
    const compartirBoton = evento.target.closest("[data-compartir-pedido]");
    try {
      if (ver) {
        prepararModal(pedidoDesdeBoton(ver));
        modal.showModal();
      } else if (imprimirBoton) {
        imprimir(pedidoDesdeBoton(imprimirBoton));
      } else if (compartirBoton) {
        compartir(pedidoDesdeBoton(compartirBoton));
      } else if (evento.target.closest("[data-imprimir-modal]") && pedidoActual) {
        imprimir(pedidoActual);
      } else if (evento.target.closest("[data-compartir-modal]") && pedidoActual) {
        compartir(pedidoActual);
      } else if (evento.target.closest("[data-cerrar-pedido-modal]")) {
        modal.close();
      }
    } catch (error) {
      console.warn("No se pudo preparar la acción del pedido.", error);
    }
  });

  modal.addEventListener("click", (evento) => {
    if (evento.target === modal) modal.close();
  });
  window.addEventListener("afterprint", () => {
    document.body.classList.remove("pedido-base-imprimiendo");
  });
})();
