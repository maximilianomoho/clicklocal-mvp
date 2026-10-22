(() => {
  const tablero = document.querySelector(".kanban-pedidos, .pedidos-recibidos");
  const intervaloActualizacionMs = 30000;
  let gesto = null;
  let omitirClick = false;
  let actualizando = false;
  if (!tablero) return;
  const esPedidosRecibidos = tablero.classList.contains("pedidos-recibidos");
  const paginaPedidosRecibidos = Number(tablero.dataset.paginaActual || 0);
  let idsConocidos = new Set(
    Array.from(tablero.querySelectorAll("[data-pedido-id]"), (nodo) => nodo.dataset.pedidoId)
  );

  const permiteKanban = Boolean(tablero.querySelector(".kanban-columnas"));

  function informar(mensaje, esError = false) {
    const estado = tablero.querySelector(".kanban-actualizacion");
    if (!estado) return;
    estado.textContent = mensaje;
    estado.classList.toggle("kanban-actualizacion--error", esError);
  }

  function activarColumnaBase(clave) {
    const pestanas = tablero.querySelectorAll("[data-columna-base-tab]");
    const columnas = tablero.querySelectorAll("[data-columna-base]");
    if (!pestanas.length || !columnas.length) return;
    const existe = Array.from(columnas).some(
      (columna) => columna.dataset.columnaBase === clave,
    );
    const claveActiva = existe ? clave : columnas[0].dataset.columnaBase;
    pestanas.forEach((pestana) => {
      pestana.setAttribute(
        "aria-selected",
        String(pestana.dataset.columnaBaseTab === claveActiva),
      );
    });
    columnas.forEach((columna) => {
      columna.classList.toggle(
        "activa",
        columna.dataset.columnaBase === claveActiva,
      );
    });
  }

  function limpiarDrag() {
    document.querySelectorAll(".kanban-zona--activa").forEach((zona) => {
      zona.classList.remove("kanban-zona--activa");
    });
  }

  function accionParaMovimiento(estadoActual, estadoDestino) {
    const siguiente = {
      pendiente: "marchando", marchando: "preparado", preparado: "cerrado",
    };
    const anterior = {
      marchando: "pendiente", preparado: "marchando", cerrado: "preparado",
    };
    if (siguiente[estadoActual] === estadoDestino) return "avanzar";
    if (anterior[estadoActual] === estadoDestino) return "retroceder";
    return null;
  }

  async function solicitar(url, payload) {
    const respuesta = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const datos = await respuesta.json().catch(() => ({}));
    if (!respuesta.ok || !datos.ok) {
      throw new Error(datos.error || "No se pudo actualizar el pedido.");
    }
    return datos;
  }

  async function refrescarBandeja({ silencioso = false } = {}) {
    if (actualizando || gesto || document.hidden
      || document.querySelector("#pedidoDetalleModal")?.open) return;
    actualizando = true;
    const columnasActuales = tablero.querySelector(".kanban-columnas");
    const pestanasBaseActuales = tablero.querySelector(".pedidos-base-tabs");
    const listaBaseActual = tablero.querySelector(".pedidos-lista-base");
    const recibidosActuales = tablero.querySelector(".pedidos-recibidos-cuerpo");
    const columnaBaseActiva = tablero.querySelector(
      '[data-columna-base-tab][aria-selected="true"]',
    )?.dataset.columnaBaseTab;
    const scrollAnterior = columnasActuales?.scrollLeft || 0;
    try {
      const respuesta = await fetch(tablero.dataset.endpointBase, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      if (!respuesta.ok) throw new Error(
        esPedidosRecibidos
          ? "No se pudo refrescar la lista."
          : "No se pudo refrescar la bandeja.",
      );
      const documento = new DOMParser().parseFromString(
        await respuesta.text(), "text/html",
      );
      const columnasNuevas = documento.querySelector(".kanban-columnas");
      const pestanasBaseNuevas = documento.querySelector(".pedidos-base-tabs");
      const listaBaseNueva = documento.querySelector(".pedidos-lista-base");
      const recibidosNuevos = documento.querySelector(".pedidos-recibidos-cuerpo");
      if ((!columnasNuevas || !columnasActuales)
        && (!listaBaseNueva || !listaBaseActual)
        && (!recibidosNuevos || !recibidosActuales)) {
        throw new Error("La respuesta de la bandeja no es válida.");
      }
      const contenedorNuevo = columnasNuevas || listaBaseNueva || recibidosNuevos;
      const idsNuevos = new Set(Array.from(contenedorNuevo.querySelectorAll("[data-pedido-id]"), (nodo) => nodo.dataset.pedidoId));
      const llegoPedido = Array.from(idsNuevos).some((id) => !idsConocidos.has(id));
      if (recibidosNuevos) {
        recibidosActuales.replaceWith(recibidosNuevos);
      } else if (columnasNuevas) {
        columnasActuales.replaceWith(columnasNuevas);
        columnasNuevas.scrollLeft = scrollAnterior;
      } else {
        if (pestanasBaseActuales && pestanasBaseNuevas) {
          pestanasBaseActuales.replaceWith(pestanasBaseNuevas);
        }
        listaBaseActual.replaceWith(listaBaseNueva);
        activarColumnaBase(columnaBaseActiva);
      }
      idsConocidos = idsNuevos;
      if (llegoPedido) {
        informar("Llegó un pedido nuevo.");
        if (!esPedidosRecibidos || paginaPedidosRecibidos === 1) {
          window.ClickLocalAlertaPedido?.avisar();
        }
      }
      if (!silencioso) informar(
        esPedidosRecibidos ? "Lista actualizada." : "Bandeja actualizada.",
      );
    } catch (error) {
      informar(error.message, true);
    } finally {
      actualizando = false;
    }
  }

  async function cambiarEstado(tarjeta, accion) {
    if (actualizando) return;
    let confirmado = false;
    if (accion === "cancelar") {
      const numero = tarjeta.querySelector("strong")?.textContent || "este pedido";
      if (!window.confirm(`¿Cancelar ${numero}? El pedido quedará en el historial.`)) return;
      confirmado = true;
    }
    actualizando = true;
    tarjeta.classList.add("kanban-tarjeta--actualizando");
    try {
      await solicitar(
        `${tablero.dataset.endpointBase}/${tarjeta.dataset.pedidoId}/estado`,
        { accion, confirmado },
      );
      actualizando = false;
      await refrescarBandeja();
    } catch (error) {
      informar(error.message, true);
      actualizando = false;
      tarjeta.classList.remove("kanban-tarjeta--actualizando");
    }
  }

  async function cambiarPago(tarjeta, estadoPago) {
    if (actualizando) return;
    actualizando = true;
    tarjeta.classList.add("kanban-tarjeta--actualizando");
    try {
      await solicitar(
        `${tablero.dataset.endpointBase}/${tarjeta.dataset.pedidoId}/pago`,
        { estado_pago: estadoPago },
      );
      actualizando = false;
      await refrescarBandeja();
    } catch (error) {
      informar(error.message, true);
      actualizando = false;
      tarjeta.classList.remove("kanban-tarjeta--actualizando");
    }
  }

  function seleccionCobros() {
    return Array.from(
      tablero.querySelectorAll('[data-seleccionar-cobro]:checked'),
      (casilla) => casilla.closest("[data-pedido-id]"),
    ).filter(Boolean);
  }

  function actualizarResumenCobros() {
    const tarjetas = seleccionCobros();
    const total = tarjetas.reduce(
      (suma, tarjeta) => suma + Number(tarjeta.dataset.total || 0), 0,
    );
    const resumen = tablero.querySelector("[data-resumen-seleccion]");
    const boton = tablero.querySelector("[data-confirmar-cobros]");
    if (resumen) resumen.textContent = `${tarjetas.length} pedidos · ${total.toLocaleString("es-AR", { style: "currency", currency: "ARS" })}`;
    if (boton) boton.disabled = tarjetas.length === 0;
  }

  async function confirmarCobros(tarjetas) {
    if (actualizando || !tarjetas.length) return;
    const total = tarjetas.reduce(
      (suma, tarjeta) => suma + Number(tarjeta.dataset.total || 0), 0,
    );
    const detalle = `${tarjetas.length} pedido${tarjetas.length === 1 ? "" : "s"} por ${total.toLocaleString("es-AR", { style: "currency", currency: "ARS" })}`;
    if (!window.confirm(`¿Confirmar el cobro de ${detalle}?`)) return;
    actualizando = true;
    tarjetas.forEach((tarjeta) => tarjeta.classList.add("kanban-tarjeta--actualizando"));
    try {
      const respuesta = await fetch(tablero.dataset.endpointCobros, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pedido_ids: tarjetas.map((tarjeta) => tarjeta.dataset.pedidoId) }),
      });
      const datos = await respuesta.json().catch(() => ({}));
      if (!respuesta.ok && respuesta.status !== 207) {
        throw new Error(datos.error || "No se pudieron confirmar los cobros.");
      }
      const mensaje = `${datos.confirmados || 0} cobro(s) confirmado(s)`
        + (datos.con_error ? `; ${datos.con_error} con error.` : ".");
      informar(mensaje, Boolean(datos.con_error));
      actualizando = false;
      await refrescarBandeja({ silencioso: true });
    } catch (error) {
      informar(error.message, true);
      actualizando = false;
      tarjetas.forEach((tarjeta) => tarjeta.classList.remove("kanban-tarjeta--actualizando"));
    }
  }

  async function confirmarPedido(tarjeta) {
    if (actualizando) return;
    actualizando = true;
    tarjeta.classList.add("kanban-tarjeta--actualizando");
    try {
      await solicitar(
        `${tablero.dataset.endpointBase}/${tarjeta.dataset.pedidoId}/confirmacion`,
        {},
      );
      actualizando = false;
      await refrescarBandeja();
    } catch (error) {
      informar(error.message, true);
      actualizando = false;
      tarjeta.classList.remove("kanban-tarjeta--actualizando");
    }
  }

  async function marcarEntregado(tarjeta) {
    if (actualizando) return;
    actualizando = true;
    tarjeta.classList.add("kanban-tarjeta--actualizando");
    try {
      await solicitar(
        `${tablero.dataset.endpointBase}/${tarjeta.dataset.pedidoId}/entrega`,
        {},
      );
      actualizando = false;
      await refrescarBandeja();
    } catch (error) {
      informar(error.message, true);
      actualizando = false;
      tarjeta.classList.remove("kanban-tarjeta--actualizando");
    }
  }

  function actualizarContadores() {
    tablero.querySelectorAll(".kanban-columna").forEach((columna) => {
      const cantidad = columna.querySelectorAll(".kanban-tarjeta").length;
      const contador = columna.querySelector(".kanban-contador");
      if (contador) {
        contador.textContent = `${cantidad} pedido${cantidad === 1 ? "" : "s"}`;
      }
    });
  }

  function actualizarBotonesPrincipales(tarjeta, estado) {
    const acciones = tarjeta.querySelector(".kanban-acciones-principales");
    if (!acciones) return;
    const botonesPorEstado = {
      pendiente: [
        '<button type="button" data-accion-estado="avanzar">Marcar como marchando</button>',
        '<button type="button" class="cancelar" data-accion-estado="cancelar">Cancelar</button>',
      ],
      marchando: [
        '<button type="button" class="secundario" data-accion-estado="retroceder">Volver</button>',
        '<button type="button" data-accion-estado="avanzar">Marcar preparado</button>',
      ],
      preparado: [
        '<button type="button" class="secundario" data-accion-estado="retroceder">Volver</button>',
        '<button type="button" data-accion-estado="avanzar">Cerrar pedido</button>',
      ],
      cerrado: [
        '<button type="button" class="secundario" data-accion-estado="retroceder">Volver</button>',
        '<button type="button" data-accion-entrega>Entregado</button>',
      ],
    };
    acciones.innerHTML = (botonesPorEstado[estado] || []).join("");
  }

  async function moverPorDrag(tarjeta, destino, accion) {
    if (actualizando) return;
    const estadoAnterior = tarjeta.dataset.estado;
    const estadoNuevo = destino.dataset.dropzone;
    const padreAnterior = tarjeta.parentElement;
    const referenciaAnterior = tarjeta.nextElementSibling;
    const acciones = tarjeta.querySelector(".kanban-acciones-principales");
    const botonesAnteriores = acciones?.innerHTML || "";
    const marcadorVacio = destino.querySelector(".kanban-vacio");

    actualizando = true;
    tarjeta.classList.add("kanban-tarjeta--actualizando");
    destino.insertBefore(tarjeta, marcadorVacio);
    tarjeta.dataset.estado = estadoNuevo;
    actualizarBotonesPrincipales(tarjeta, estadoNuevo);
    actualizarContadores();

    try {
      await solicitar(
        `${tablero.dataset.endpointBase}/${tarjeta.dataset.pedidoId}/estado`,
        { accion, confirmado: false },
      );
      informar("Pedido actualizado.");
    } catch (error) {
      if (referenciaAnterior?.parentElement === padreAnterior) {
        padreAnterior.insertBefore(tarjeta, referenciaAnterior);
      } else {
        padreAnterior.appendChild(tarjeta);
      }
      tarjeta.dataset.estado = estadoAnterior;
      if (acciones) acciones.innerHTML = botonesAnteriores;
      actualizarContadores();
      informar(error.message, true);
    } finally {
      actualizando = false;
      tarjeta.classList.remove("kanban-tarjeta--actualizando");
    }
  }

  function terminarGesto(evento, cancelado = false) {
    if (!gesto || evento.pointerId !== gesto.pointerId) return;
    const actual = gesto;
    if (actual.tarjeta.hasPointerCapture(evento.pointerId)) {
      actual.tarjeta.releasePointerCapture(evento.pointerId);
    }
    actual.clon?.remove();
    actual.tarjeta.classList.remove("kanban-tarjeta--moviendo");
    limpiarDrag();
    gesto = null;
    if (!actual.arrastrando) return;
    omitirClick = true;
    requestAnimationFrame(() => { omitirClick = false; });
    if (cancelado || !actual.destino) return;
    const accion = accionParaMovimiento(
      actual.tarjeta.dataset.estado, actual.destino.dataset.dropzone,
    );
    if (!accion) {
      informar("Solo podés arrastrar el pedido a una etapa contigua.", true);
      return;
    }
    moverPorDrag(actual.tarjeta, actual.destino, accion);
  }

  tablero.addEventListener("pointerdown", (evento) => {
    if (!permiteKanban
      || !evento.isPrimary
      || (evento.pointerType === "mouse" && evento.button !== 0)
      || evento.target.closest("button,a,input,select,textarea")) return;
    const tarjeta = evento.target.closest(".kanban-tarjeta");
    if (!tarjeta) return;
    const rectangulo = tarjeta.getBoundingClientRect();
    gesto = {
      tarjeta, pointerId: evento.pointerId,
      inicioX: evento.clientX, inicioY: evento.clientY,
      offsetX: evento.clientX - rectangulo.left,
      offsetY: evento.clientY - rectangulo.top,
      ancho: rectangulo.width, alto: rectangulo.height,
      arrastrando: false, clon: null, destino: null,
    };
    tarjeta.setPointerCapture(evento.pointerId);
  });

  tablero.addEventListener("pointermove", (evento) => {
    if (!gesto || evento.pointerId !== gesto.pointerId) return;
    const distancia = Math.hypot(
      evento.clientX - gesto.inicioX, evento.clientY - gesto.inicioY,
    );
    if (!gesto.arrastrando && distancia < 5) return;
    evento.preventDefault();
    if (!gesto.arrastrando) {
      gesto.arrastrando = true;
      gesto.tarjeta.classList.add("kanban-tarjeta--moviendo");
      gesto.clon = gesto.tarjeta.cloneNode(true);
      gesto.clon.removeAttribute("data-pedido");
      gesto.clon.classList.add("kanban-tarjeta--clon");
      gesto.clon.style.width = `${gesto.ancho}px`;
      gesto.clon.style.height = `${gesto.alto}px`;
      document.body.appendChild(gesto.clon);
    }
    gesto.clon.style.left = `${evento.clientX - gesto.offsetX}px`;
    gesto.clon.style.top = `${evento.clientY - gesto.offsetY}px`;
    limpiarDrag();
    const elementoDebajo = document.elementFromPoint(evento.clientX, evento.clientY);
    gesto.destino = elementoDebajo?.closest(".kanban-zona") || null;
    gesto.destino?.classList.add("kanban-zona--activa");
  });

  tablero.addEventListener("pointerup", (evento) => terminarGesto(evento));
  tablero.addEventListener("pointercancel", (evento) => terminarGesto(evento, true));
  tablero.addEventListener("click", (evento) => {
    if (omitirClick) {
      evento.preventDefault();
      evento.stopPropagation();
      return;
    }
    const pestanaBase = evento.target.closest("[data-columna-base-tab]");
    if (pestanaBase) {
      activarColumnaBase(pestanaBase.dataset.columnaBaseTab);
      return;
    }
    const seleccionarTodos = evento.target.closest("[data-seleccionar-todos]");
    if (seleccionarTodos) {
      tablero.querySelectorAll("[data-seleccionar-cobro]").forEach((casilla) => {
        casilla.checked = seleccionarTodos.checked;
      });
      actualizarResumenCobros();
      return;
    }
    if (evento.target.closest("[data-confirmar-cobros]")) {
      confirmarCobros(seleccionCobros());
      return;
    }
    const tarjeta = evento.target.closest(".kanban-tarjeta");
    if (!tarjeta) return;
    const botonEstado = evento.target.closest("[data-accion-estado]");
    const botonConfirmar = evento.target.closest("[data-accion-confirmar]");
    const botonPago = evento.target.closest("button[data-estado-pago]");
    const botonEntrega = evento.target.closest("[data-accion-entrega]");
    const botonCobro = evento.target.closest("[data-confirmar-cobro]");
    if (botonCobro) {
      confirmarCobros([tarjeta]);
    } else if (botonConfirmar) {
      confirmarPedido(tarjeta);
    } else if (botonEntrega) {
      marcarEntregado(tarjeta);
    } else if (botonPago) {
      cambiarPago(tarjeta, botonPago.dataset.estadoPago);
    } else if (botonEstado) {
      cambiarEstado(tarjeta, botonEstado.dataset.accionEstado);
    }
  });

  tablero.addEventListener("change", (evento) => {
    if (evento.target.matches("[data-seleccionar-cobro]")) {
      actualizarResumenCobros();
    }
  });

  window.setInterval(
    () => refrescarBandeja({ silencioso: true }), intervaloActualizacionMs,
  );
})();
