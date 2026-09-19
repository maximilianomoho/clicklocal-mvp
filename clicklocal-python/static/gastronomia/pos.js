(function () {
  "use strict";

  const raiz = document.querySelector("[data-pos]");
  if (!raiz) {
    return;
  }

  const carrito = new Map();
  const itemsEl = raiz.querySelector("[data-carrito-items]");
  const subtotalEl = raiz.querySelector("[data-subtotal]");
  const totalEl = raiz.querySelector("[data-total]");
  const vaciarEl = raiz.querySelector("[data-vaciar-carrito]");
  const confirmarEl = raiz.querySelector("[data-confirmar-venta]");
  const formaPagoEl = raiz.querySelector("[data-forma-pago]");
  const confirmacionEl = raiz.querySelector("[data-confirmacion-venta]");
  let confirmandoVenta = false;

  function formatearPesos(valor) {
    return "$" + Math.round(Number(valor) || 0).toLocaleString("es-AR");
  }

  function escaparHtml(valor) {
    const nodo = document.createElement("div");
    nodo.textContent = String(valor || "");
    return nodo.innerHTML;
  }

  function normalizarCategoria(valor) {
    return String(valor || "").trim().toLocaleLowerCase("es");
  }

  function opcionesSeleccionadas(modal) {
    return Array.from(modal.querySelectorAll("[data-opcion]:checked")).map(
      function (input) {
        return {
          id: input.dataset.opcionId || "",
          nombre: input.dataset.opcionNombre || "",
          precio: Number(input.dataset.opcionPrecio || 0),
        };
      }
    );
  }

  function precioUnitarioSeleccionado(modal) {
    const precioBase = Number(modal.dataset.productoPrecio || 0);
    const precioExtras = opcionesSeleccionadas(modal).reduce(
      function (total, opcion) {
        return total + opcion.precio;
      },
      0
    );
    return precioBase + precioExtras;
  }

  function cantidadSeleccionada(modal) {
    const cantidadEl = modal.querySelector("[data-cantidad-modal]");
    return Math.max(1, Number(cantidadEl.textContent) || 1);
  }

  function reiniciarCantidadModal(modal) {
    modal.querySelector("[data-cantidad-modal]").textContent = "1";
  }

  function actualizarResumenModal(modal) {
    const opciones = opcionesSeleccionadas(modal);
    const listaEl = modal.querySelector("[data-resumen-opciones]");
    const vacioEl = modal.querySelector("[data-resumen-sin-opciones]");
    const totalEl = modal.querySelector("[data-resumen-total]");

    listaEl.replaceChildren.apply(
      listaEl,
      opciones.map(function (opcion) {
        const item = document.createElement("li");
        const nombre = document.createElement("span");
        const precio = document.createElement("strong");
        nombre.textContent = opcion.nombre;
        precio.textContent = "+" + formatearPesos(opcion.precio);
        item.append(nombre, precio);
        return item;
      })
    );
    listaEl.hidden = opciones.length === 0;
    vacioEl.hidden = opciones.length > 0;
    totalEl.textContent = formatearPesos(precioUnitarioSeleccionado(modal));
  }

  function validarOpciones(modal) {
    const errorEl = modal.querySelector("[data-modal-error]");
    let mensaje = "";

    modal.querySelectorAll("[data-grupo-opciones]").forEach(function (grupo) {
      if (mensaje) {
        return;
      }
      const cantidad = grupo.querySelectorAll("[data-opcion]:checked").length;
      const minimo = Number(grupo.dataset.grupoMinimo || 0);
      const maximo = Number(grupo.dataset.grupoMaximo || 0);
      const nombre = grupo.dataset.grupoNombre || "Opciones";

      if (cantidad < minimo) {
        mensaje = "En " + nombre + " tenés que elegir al menos " + minimo + ".";
      } else if (maximo > 0 && cantidad > maximo) {
        mensaje = "En " + nombre + " podés elegir hasta " + maximo + ".";
      }
    });

    errorEl.textContent = mensaje;
    errorEl.hidden = !mensaje;
    return !mensaje;
  }

  function cerrarModal(modal) {
    modal.querySelectorAll("[data-opcion]").forEach(function (input) {
      input.checked = false;
    });
    reiniciarCantidadModal(modal);
    actualizarResumenModal(modal);
    modal.hidden = true;
    document.body.style.overflow = "";
    const errorEl = modal.querySelector("[data-modal-error]");
    errorEl.hidden = true;
    errorEl.textContent = "";
  }

  function renderizarCarrito() {
    let subtotal = 0;

    if (carrito.size === 0) {
      itemsEl.innerHTML = '<p class="pos-carrito-vacio">El carrito está vacío.</p>';
    } else {
      itemsEl.innerHTML = Array.from(carrito.values()).map(function (item) {
        const subtotalItem = item.precioUnitario * item.cantidad;
        subtotal += subtotalItem;
        const opciones = item.opciones.length
          ? '<p class="pos-carrito-opciones">' +
            item.opciones.map(function (opcion) {
              return escaparHtml(opcion.nombre);
            }).join(" · ") +
            "</p>"
          : "";

        return (
          '<article class="pos-carrito-item" data-item-key="' + escaparHtml(item.key) + '">' +
            '<div class="pos-carrito-item-cabecera">' +
              "<strong>" + escaparHtml(item.nombre) + "</strong>" +
              "<span>" + formatearPesos(subtotalItem) + "</span>" +
            "</div>" +
            opciones +
            '<div class="pos-carrito-item-controles">' +
              '<div class="pos-cantidad">' +
                '<button type="button" data-cantidad-cambio="-1" aria-label="Restar uno">−</button>' +
                "<span>" + item.cantidad + "</span>" +
                '<button type="button" data-cantidad-cambio="1" aria-label="Sumar uno">+</button>' +
              "</div>" +
              '<button type="button" class="pos-eliminar" data-eliminar-item>Eliminar</button>' +
            "</div>" +
          "</article>"
        );
      }).join("");
    }

    subtotalEl.textContent = formatearPesos(subtotal);
    totalEl.textContent = formatearPesos(subtotal);
    vaciarEl.hidden = carrito.size === 0;
    confirmarEl.disabled = carrito.size === 0 || confirmandoVenta;
  }

  function mostrarConfirmacion(mensaje, esError) {
    confirmacionEl.textContent = mensaje;
    confirmacionEl.classList.toggle("pos-confirmacion--error", Boolean(esError));
    confirmacionEl.hidden = !mensaje;
  }

  async function confirmarVenta() {
    if (confirmandoVenta || carrito.size === 0) {
      return;
    }

    const formaPago = formaPagoEl.value;
    const tipoVentaEl = raiz.querySelector("[data-tipo-venta]:checked");
    if (!formaPago) {
      mostrarConfirmacion("Elegí una forma de pago.", true);
      formaPagoEl.focus();
      return;
    }
    if (!tipoVentaEl) {
      mostrarConfirmacion("Elegí un tipo de venta.", true);
      return;
    }

    confirmandoVenta = true;
    confirmarEl.textContent = "Registrando…";
    mostrarConfirmacion("", false);
    renderizarCarrito();

    const detalle = Array.from(carrito.values()).map(function (item) {
      return {
        id: item.id,
        cantidad: item.cantidad,
        opciones: item.opciones.map(function (opcion) {
          return {id: opcion.id};
        }),
      };
    });

    try {
      const respuesta = await fetch(raiz.dataset.confirmarUrl, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          detalle: detalle,
          forma_pago: formaPago,
          tipo_venta: tipoVentaEl.value,
        }),
      });
      const datos = await respuesta.json();
      if (!respuesta.ok || !datos.ok) {
        throw new Error(datos.error || "No se pudo registrar la venta.");
      }

      carrito.clear();
      const numero = datos.numero_pedido ? " · Pedido #" + datos.numero_pedido : "";
      mostrarConfirmacion("Venta registrada" + numero, false);
    } catch (error) {
      mostrarConfirmacion(error.message || "No se pudo registrar la venta.", true);
    } finally {
      confirmandoVenta = false;
      confirmarEl.textContent = "Confirmar venta";
      renderizarCarrito();
    }
  }

  raiz.addEventListener("change", function (event) {
    const opcion = event.target.closest("[data-opcion]");
    if (!opcion) {
      return;
    }
    const grupo = opcion.closest("[data-grupo-opciones]");
    const maximo = Number(grupo.dataset.grupoMaximo || 0);
    if (opcion.checked && maximo > 0 && grupo.querySelectorAll("[data-opcion]:checked").length > maximo) {
      opcion.checked = false;
      const errorEl = opcion.closest("[data-modal-producto]").querySelector("[data-modal-error]");
      errorEl.textContent = "En " + (grupo.dataset.grupoNombre || "Opciones") + " podés elegir hasta " + maximo + ".";
      errorEl.hidden = false;
    }
    actualizarResumenModal(opcion.closest("[data-modal-producto]"));
  });

  raiz.addEventListener("click", function (event) {
    const filtro = event.target.closest("[data-filtro-categoria]");
    if (filtro) {
      const categoriaActiva = normalizarCategoria(filtro.dataset.filtroCategoria);

      raiz.querySelectorAll("[data-filtro-categoria]").forEach(function (boton) {
        const activo = boton === filtro;
        boton.classList.toggle("activo", activo);
        boton.setAttribute("aria-pressed", String(activo));
      });

      raiz.querySelectorAll(".pos-producto").forEach(function (producto) {
        producto.hidden = Boolean(categoriaActiva) &&
          normalizarCategoria(producto.dataset.categoria) !== categoriaActiva;
      });
      return;
    }

    const abrir = event.target.closest("[data-abrir-producto]");
    if (abrir) {
      const modal = document.getElementById(abrir.dataset.abrirProducto);
      if (modal) {
        reiniciarCantidadModal(modal);
        actualizarResumenModal(modal);
        modal.hidden = false;
        document.body.style.overflow = "hidden";
      }
      return;
    }

    const cerrar = event.target.closest("[data-cerrar-modal]");
    if (cerrar) {
      cerrarModal(cerrar.closest("[data-modal-producto]"));
      return;
    }

    const modal = event.target.closest("[data-modal-producto]");
    if (modal && event.target === modal) {
      cerrarModal(modal);
      return;
    }

    const cantidadModalCambio = event.target.closest("[data-cantidad-modal-cambio]");
    if (cantidadModalCambio) {
      const productoModal = cantidadModalCambio.closest("[data-modal-producto]");
      const cantidadEl = productoModal.querySelector("[data-cantidad-modal]");
      const cambio = Number(cantidadModalCambio.dataset.cantidadModalCambio || 0);
      cantidadEl.textContent = String(Math.max(1, cantidadSeleccionada(productoModal) + cambio));
      return;
    }

    const agregar = event.target.closest("[data-agregar-producto]");
    if (agregar) {
      const productoModal = agregar.closest("[data-modal-producto]");
      if (!validarOpciones(productoModal)) {
        return;
      }
      const opciones = opcionesSeleccionadas(productoModal);
      const opcionesKey = opciones.map(function (opcion) {
        return opcion.id;
      }).sort().join(",");
      const productoId = productoModal.dataset.productoId || "";
      const key = productoId + "::" + opcionesKey;
      const cantidad = cantidadSeleccionada(productoModal);

      if (carrito.has(key)) {
        carrito.get(key).cantidad += cantidad;
      } else {
        carrito.set(key, {
          key: key,
          id: productoId,
          nombre: productoModal.dataset.productoNombre || "Producto",
          precioUnitario: precioUnitarioSeleccionado(productoModal),
          opciones: opciones,
          cantidad: cantidad,
        });
      }
      productoModal.querySelectorAll("[data-opcion]").forEach(function (input) {
        input.checked = false;
      });
      cerrarModal(productoModal);
      renderizarCarrito();
      return;
    }

    const itemEl = event.target.closest("[data-item-key]");
    if (!itemEl) {
      return;
    }
    const item = carrito.get(itemEl.dataset.itemKey);
    if (!item) {
      return;
    }

    const cambioEl = event.target.closest("[data-cantidad-cambio]");
    if (cambioEl) {
      item.cantidad += Number(cambioEl.dataset.cantidadCambio || 0);
      if (item.cantidad <= 0) {
        carrito.delete(item.key);
      }
      renderizarCarrito();
      return;
    }

    if (event.target.closest("[data-eliminar-item]")) {
      carrito.delete(item.key);
      renderizarCarrito();
    }
  });

  vaciarEl.addEventListener("click", function () {
    carrito.clear();
    renderizarCarrito();
  });

  confirmarEl.addEventListener("click", confirmarVenta);

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }
    const modal = raiz.querySelector("[data-modal-producto]:not([hidden])");
    if (modal) {
      cerrarModal(modal);
    }
  });

  renderizarCarrito();
})();
