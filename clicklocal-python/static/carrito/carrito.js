(function () {
  "use strict";

  const PLACEHOLDER = "/static/img/mate1.jpg";

  function iniciar(config) {
    if (!config || !config.comercioId || !config.endpoint) {
      return;
    }

    const claveStorage = "clicklocal_carrito_" + config.comercioId;
    const claveIntento = "clicklocal_carrito_intento_" + config.comercioId;
    let carrito = leerCarrito();
    let abierto = false;
    let cotizacionEnvio = null;
    let configuracion = {acepta_retiro: true, acepta_delivery: false, acepta_efectivo: true, acepta_transferencia: true};

    const interfaz = crearInterfaz();
    document.body.appendChild(interfaz.raiz);
    document.body.appendChild(interfaz.botonFlotante);
    registrarBotonesProducto();
    renderizar();
    cargarConfiguracion();

    async function cargarConfiguracion() {
      if (!config.configuracionEndpoint) return;
      try {
        const respuesta = await fetch(config.configuracionEndpoint);
        const resultado = await respuesta.json();
        if (respuesta.ok && resultado.ok) {
          configuracion = Object.assign(configuracion, resultado.configuracion || {});
          aplicarConfiguracion();
        }
      } catch (error) { /* Retiro queda como opción segura. */ }
    }

    function leerCarrito() {
      try {
        const guardado = JSON.parse(localStorage.getItem(claveStorage) || "{}");
        if (!guardado || typeof guardado !== "object" || Array.isArray(guardado)) {
          return {};
        }

        return Object.fromEntries(
          Object.entries(guardado).filter(function (entrada) {
            const item = entrada[1];
            return item && item.id && Number.isInteger(item.cantidad) && item.cantidad > 0;
          })
        );
      } catch (error) {
        return {};
      }
    }

    function guardarCarrito() {
      try {
        localStorage.setItem(claveStorage, JSON.stringify(carrito));
      } catch (error) {
        // El carrito continúa funcionando en memoria si Storage no está disponible.
      }
    }

    function obtenerIdempotencyKey() {
      let key = sessionStorage.getItem(claveIntento);
      if (/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(key || "")) {
        return key;
      }
      key = crypto.randomUUID();
      sessionStorage.setItem(claveIntento, key);
      return key;
    }

    function limpiarIdempotencyKey() {
      sessionStorage.removeItem(claveIntento);
    }

    function registrarBotonesProducto() {
      document.querySelectorAll("[data-carrito-producto]").forEach(function (boton) {
        const control = document.createElement("div");
        control.className = "cl-carrito-producto-control";
        Object.keys(boton.dataset).forEach(function (clave) {
          control.dataset[clave] = boton.dataset[clave];
        });
        boton.replaceWith(control);

        const idActual = String(control.dataset.productoId || "").trim();
        const precioActual = Number(control.dataset.productoPrecio);
        if (carrito[idActual] && Number.isFinite(precioActual) && precioActual >= 0) {
          carrito[idActual].nombre = String(
            control.dataset.productoNombre || "Producto"
          ).trim();
          carrito[idActual].precio = precioActual;
          carrito[idActual].imagen = String(
            control.dataset.productoImagen || PLACEHOLDER
          ).trim() || PLACEHOLDER;
        }

        control.addEventListener("click", function (evento) {
          evento.preventDefault();
          evento.stopPropagation();

          const id = String(control.dataset.productoId || "").trim();
          const nombre = String(control.dataset.productoNombre || "Producto").trim();
          const precio = Number(control.dataset.productoPrecio);
          const imagen = String(control.dataset.productoImagen || "").trim();
          if (!id || !Number.isFinite(precio) || precio < 0) {
            return;
          }

          const accion = evento.target.closest("[data-carrito-cantidad-accion]");
          if (accion) {
            cambiarCantidad(id, accion.dataset.carritoCantidadAccion === "sumar" ? 1 : -1);
            return;
          }

          if (carrito[id]) {
            return;
          }
          carrito[id] = {
            id: id,
            nombre: nombre,
            precio: precio,
            imagen: imagen || PLACEHOLDER,
            cantidad: 1
          };

          guardarCarrito();
          renderizar();
          abrir();
        });
      });
      guardarCarrito();
    }

    function renderizarControlesProducto() {
      document.querySelectorAll(".cl-carrito-producto-control").forEach(function (control) {
        const id = String(control.dataset.productoId || "").trim();
        const item = carrito[id];
        control.replaceChildren();

        if (!item) {
          const agregar = document.createElement("button");
          agregar.type = "button";
          agregar.className = "cl-carrito-producto-accion";
          agregar.textContent = "Agregar al carrito";
          control.appendChild(agregar);
          return;
        }

        const nombre = String(control.dataset.productoNombre || "Producto").trim();
        const restar = document.createElement("button");
        restar.type = "button";
        restar.className = "cl-carrito-producto-cantidad";
        restar.dataset.carritoCantidadAccion = "restar";
        restar.setAttribute("aria-label", "Quitar una unidad de " + nombre);
        restar.textContent = "−";

        const cantidad = document.createElement("strong");
        cantidad.className = "cl-carrito-producto-valor";
        cantidad.textContent = String(item.cantidad);
        cantidad.setAttribute("aria-label", item.cantidad + " unidades de " + nombre);

        const sumar = document.createElement("button");
        sumar.type = "button";
        sumar.className = "cl-carrito-producto-cantidad";
        sumar.dataset.carritoCantidadAccion = "sumar";
        sumar.setAttribute("aria-label", "Agregar una unidad de " + nombre);
        sumar.textContent = "+";
        control.append(restar, cantidad, sumar);
      });
    }

    function crearInterfaz() {
      const botonFlotante = document.createElement("button");
      botonFlotante.type = "button";
      botonFlotante.className = "cl-carrito-flotante";
      botonFlotante.addEventListener("click", abrir);

      const raiz = document.createElement("div");
      raiz.className = "cl-carrito-overlay";
      raiz.hidden = true;
      raiz.innerHTML = [
        '<div class="cl-carrito-panel" role="dialog" aria-modal="true" aria-labelledby="cl-carrito-titulo">',
        '  <header class="cl-carrito-header">',
        '    <div><span>MI PEDIDO</span><h2 id="cl-carrito-titulo">Carrito</h2></div>',
        '    <button type="button" class="cl-carrito-cerrar" aria-label="Cerrar carrito">×</button>',
        '  </header>',
        '  <div class="cl-carrito-contenido">',
        '    <div class="cl-carrito-items"></div>',
        '    <div class="cl-carrito-resumen"></div>',
        '    <button type="button" class="cl-carrito-continuar">Continuar pedido</button>',
        '    <button type="button" class="cl-carrito-vaciar">Vaciar carrito</button>',
        '    <a class="cl-carrito-galeria">Ir a la galería</a>',
        '    <form class="cl-carrito-checkout" hidden>',
        '      <h3>Datos del pedido</h3>',
        '      <section class="cl-carrito-ticket" aria-label="Resumen del pedido">',
        '        <div class="cl-carrito-ticket-items"></div>',
        '        <div class="cl-carrito-ticket-totales">',
        '          <p><span>Productos</span><strong data-total-productos></strong></p>',
        '          <p><span>Envío</span><strong data-total-envio></strong></p>',
        '          <p class="cl-carrito-ticket-total"><span>TOTAL</span><strong data-total-final></strong></p>',
        '        </div>',
        '      </section>',
        '      <div class="cl-carrito-datos-personales">',
        '        <label>Nombre *<input name="nombre" maxlength="49" autocomplete="given-name" required></label>',
        '        <label>Apellido *<input name="apellido" maxlength="50" autocomplete="family-name" required></label>',
        '        <label class="cl-carrito-whatsapp">WhatsApp *<input name="whatsapp" inputmode="tel" autocomplete="tel" required placeholder="Ej: 343 6123456"></label>',
        '      </div>',
        '      <fieldset><legend>Forma de entrega</legend>',
        '        <label class="cl-carrito-opcion"><input type="radio" name="entrega" value="retiro" checked> Retiro</label>',
        '        <label class="cl-carrito-opcion"><input type="radio" name="entrega" value="delivery" disabled> Delivery</label>',
        '      </fieldset>',
        '      <div class="cl-carrito-direccion" hidden>',
        '        <div class="cl-carrito-direccion-fila"><label>Calle *<input name="direccion" maxlength="250"></label><label>Número *<input name="numero" maxlength="30"></label></div>',
        '        <label>Referencia / piso / departamento<input name="referencia" maxlength="300" placeholder="Opcional"></label>',
        '        <button type="button" class="cl-carrito-cotizar">Calcular envío</button>',
        '        <p class="cl-carrito-envio" role="status"></p>',
        '      </div>',
        '      <fieldset><legend>Forma de pago</legend>',
        '        <label class="cl-carrito-opcion"><input type="radio" name="pago" value="efectivo" checked> Efectivo</label>',
        '        <label class="cl-carrito-opcion"><input type="radio" name="pago" value="transferencia"> Transferencia</label>',
        '      </fieldset>',
        '      <label class="cl-carrito-paga-con"><strong class="cl-carrito-total-pagar"></strong><span>¿Con cuánto pagás?</span><input name="paga_con" inputmode="decimal" placeholder="Opcional"></label>',
        '      <label>Observaciones<textarea name="observaciones" maxlength="500" rows="3" placeholder="Opcional"></textarea></label>',
        '      <div class="cl-carrito-error" role="alert" hidden></div>',
        '      <button type="submit" class="cl-carrito-confirmar">Confirmar y abrir WhatsApp</button>',
        '      <button type="button" class="cl-carrito-volver">Volver al carrito</button>',
        '    </form>',
        '  </div>',
        '</div>'
      ].join("");

      raiz.addEventListener("click", function (evento) {
        if (evento.target === raiz) {
          cerrar();
        }
      });
      raiz.querySelector(".cl-carrito-cerrar").addEventListener("click", cerrar);
      raiz.querySelector(".cl-carrito-continuar").addEventListener("click", mostrarCheckout);
      raiz.querySelector(".cl-carrito-vaciar").addEventListener("click", vaciar);
      const enlaceGaleria = raiz.querySelector(".cl-carrito-galeria");
      enlaceGaleria.href = config.galeriaUrl;
      enlaceGaleria.addEventListener("click", cerrar);
      raiz.querySelector(".cl-carrito-volver").addEventListener("click", mostrarCarrito);
      raiz.querySelector(".cl-carrito-checkout").addEventListener("submit", confirmar);
      raiz.querySelector(".cl-carrito-cotizar").addEventListener("click", cotizarEnvio);
      ["whatsapp", "direccion", "numero"].forEach(function (nombre) {
        raiz.querySelector('[name="' + nombre + '"]').addEventListener("input", invalidarCotizacionEnvio);
      });
      raiz.querySelectorAll('input[name="pago"]').forEach(function (input) {
        input.addEventListener("change", actualizarPago);
      });
      raiz.querySelectorAll('input[name="entrega"]').forEach(function (input) {
        input.addEventListener("change", actualizarEntrega);
      });

      return {
        raiz: raiz,
        botonFlotante: botonFlotante,
        items: raiz.querySelector(".cl-carrito-items"),
        resumen: raiz.querySelector(".cl-carrito-resumen"),
        continuar: raiz.querySelector(".cl-carrito-continuar"),
        vaciar: raiz.querySelector(".cl-carrito-vaciar"),
        checkout: raiz.querySelector(".cl-carrito-checkout"),
        ticketItems: raiz.querySelector(".cl-carrito-ticket-items"),
        error: raiz.querySelector(".cl-carrito-error")
      };
    }

    function crearBoton(texto, clase, accion) {
      const boton = document.createElement("button");
      boton.type = "button";
      boton.className = clase;
      boton.textContent = texto;
      boton.addEventListener("click", accion);
      return boton;
    }

    function renderizar() {
      const items = Object.values(carrito);
      const unidades = items.reduce(function (total, item) {
        return total + item.cantidad;
      }, 0);
      const total = items.reduce(function (acumulado, item) {
        return acumulado + item.precio * item.cantidad;
      }, 0);

      renderizarControlesProducto();
      interfaz.botonFlotante.textContent = "🛒 Carrito (" + unidades + ")";
      interfaz.items.replaceChildren();

      if (!items.length) {
        const vacio = document.createElement("p");
        vacio.className = "cl-carrito-vacio";
        vacio.textContent = "Tu carrito está vacío.";
        interfaz.items.appendChild(vacio);
      }

      items.forEach(function (item) {
        const fila = document.createElement("article");
        fila.className = "cl-carrito-item";

        const imagen = document.createElement("img");
        imagen.src = item.imagen || PLACEHOLDER;
        imagen.alt = item.nombre;

        const datos = document.createElement("div");
        datos.className = "cl-carrito-item-datos";
        const nombre = document.createElement("strong");
        nombre.textContent = item.nombre;
        const precio = document.createElement("span");
        precio.textContent = formatearPrecio(item.precio * item.cantidad);
        datos.append(nombre, precio);

        const controles = document.createElement("div");
        controles.className = "cl-carrito-controles";
        controles.append(
          crearBoton("−", "cl-carrito-cantidad", function () { cambiarCantidad(item.id, -1); }),
          document.createTextNode(String(item.cantidad)),
          crearBoton("+", "cl-carrito-cantidad", function () { cambiarCantidad(item.id, 1); }),
          crearBoton("Quitar", "cl-carrito-quitar", function () { quitar(item.id); })
        );

        fila.append(imagen, datos, controles);
        interfaz.items.appendChild(fila);
      });

      interfaz.resumen.textContent = "Total: " + formatearPrecio(total);
      actualizarResumenCheckout();
      interfaz.continuar.disabled = items.length === 0;
      interfaz.vaciar.hidden = items.length === 0;
      if (!items.length) {
        mostrarCarrito();
      }
    }

    function formatearPrecio(valor) {
      return new Intl.NumberFormat("es-AR", {
        style: "currency",
        currency: "ARS",
        minimumFractionDigits: Number.isInteger(valor) ? 0 : 2
      }).format(valor);
    }

    function cambiarCantidad(id, cambio) {
      if (!carrito[id]) {
        return;
      }
      carrito[id].cantidad = Math.min(99, carrito[id].cantidad + cambio);
      if (carrito[id].cantidad <= 0) {
        delete carrito[id];
      }
      guardarCarrito();
      renderizar();
    }

    function quitar(id) {
      delete carrito[id];
      guardarCarrito();
      renderizar();
    }

    function vaciar() {
      carrito = {};
      guardarCarrito();
      renderizar();
    }

    function abrir() {
      abierto = true;
      interfaz.raiz.hidden = false;
      document.body.classList.add("cl-carrito-abierto");
      mostrarCarrito();
    }

    function cerrar() {
      abierto = false;
      interfaz.raiz.hidden = true;
      document.body.classList.remove("cl-carrito-abierto");
    }

    function mostrarCheckout() {
      if (!Object.keys(carrito).length) {
        return;
      }
      interfaz.items.hidden = true;
      interfaz.resumen.hidden = true;
      interfaz.continuar.hidden = true;
      interfaz.vaciar.hidden = true;
      interfaz.checkout.hidden = false;
      interfaz.checkout.querySelector('[name="nombre"]').focus();
      actualizarEntrega(false);
      actualizarPago();
      actualizarResumenCheckout();
    }

    function mostrarCarrito() {
      interfaz.items.hidden = false;
      interfaz.resumen.hidden = false;
      interfaz.continuar.hidden = false;
      interfaz.vaciar.hidden = Object.keys(carrito).length === 0;
      interfaz.checkout.hidden = true;
      ocultarError();
    }

    function actualizarPago() {
      const pago = interfaz.checkout.querySelector('input[name="pago"]:checked');
      const pagaCon = interfaz.checkout.querySelector(".cl-carrito-paga-con");
      pagaCon.hidden = !pago || pago.value !== "efectivo";
      actualizarResumenCheckout();
    }

    function aplicarConfiguracion() {
      const form = interfaz.checkout;
      const grupos = [
        ["entrega", "retiro", configuracion.acepta_retiro],
        ["entrega", "delivery", configuracion.acepta_delivery],
        ["pago", "efectivo", configuracion.acepta_efectivo],
        ["pago", "transferencia", configuracion.acepta_transferencia]
      ];
      grupos.forEach(function (g) { form.querySelector('[name="' + g[0] + '"][value="' + g[1] + '"]').disabled = !g[2]; });
      ["entrega", "pago"].forEach(function (nombre) {
        const actual = form.querySelector('[name="' + nombre + '"]:checked');
        if (!actual || actual.disabled) form.querySelector('[name="' + nombre + '"]:not(:disabled)')?.click();
      });
      actualizarEntrega(); actualizarPago();
    }

    function actualizarEntrega(invalidar) {
      const delivery = interfaz.checkout.querySelector('[name="entrega"]:checked')?.value === "delivery";
      const bloque = interfaz.checkout.querySelector(".cl-carrito-direccion");
      bloque.hidden = !delivery;
      bloque.querySelector('[name="direccion"]').required = delivery;
      bloque.querySelector('[name="numero"]').required = delivery;
      if (invalidar !== false) invalidarCotizacionEnvio();
      else actualizarResumenCheckout();
    }

    function subtotalProductos() {
      return Object.values(carrito).reduce(function (total, item) {
        return total + item.precio * item.cantidad;
      }, 0);
    }

    function actualizarResumenCheckout() {
      if (!interfaz || !interfaz.checkout) return;
      interfaz.ticketItems.replaceChildren();
      Object.values(carrito).forEach(function (item) {
        const fila = document.createElement("div");
        fila.className = "cl-carrito-ticket-item";
        const datos = document.createElement("div");
        const nombre = document.createElement("strong");
        nombre.textContent = item.nombre;
        const detalle = document.createElement("span");
        detalle.textContent = item.cantidad + " × " + formatearPrecio(item.precio);
        const subtotal = document.createElement("b");
        subtotal.textContent = formatearPrecio(item.cantidad * item.precio);
        datos.append(nombre, detalle);
        fila.append(datos, subtotal);
        interfaz.ticketItems.appendChild(fila);
      });

      const productos = subtotalProductos();
      const entrega = interfaz.checkout.querySelector('[name="entrega"]:checked')?.value;
      const pendiente = entrega === "delivery" && !cotizacionEnvio;
      const envio = entrega === "delivery" && cotizacionEnvio ? cotizacionEnvio.costo : 0;
      const total = productos + envio;
      interfaz.checkout.querySelector("[data-total-productos]").textContent = formatearPrecio(productos);
      interfaz.checkout.querySelector("[data-total-envio]").textContent = pendiente ? "Calculá el envío" : formatearPrecio(envio);
      interfaz.checkout.querySelector("[data-total-final]").textContent = pendiente ? "Pendiente" : formatearPrecio(total);
      interfaz.checkout.querySelector(".cl-carrito-total-pagar").textContent = pendiente
        ? "Total a pagar: calculá el envío"
        : "Total a pagar: " + formatearPrecio(total);
    }

    function invalidarCotizacionEnvio() {
      cotizacionEnvio = null;
      const estado = interfaz.checkout.querySelector(".cl-carrito-envio");
      estado.textContent = "";
      actualizarResumenCheckout();
    }

    async function cotizarEnvio() {
      ocultarError();
      const form = interfaz.checkout;
      const whatsapp = form.querySelector('[name="whatsapp"]');
      const direccion = form.querySelector('[name="direccion"]');
      const numero = form.querySelector('[name="numero"]');
      if (![whatsapp, direccion, numero].every(function (campo) { return campo.reportValidity(); })) return;
      const boton = form.querySelector(".cl-carrito-cotizar");
      const estado = form.querySelector(".cl-carrito-envio");
      boton.disabled = true;
      estado.textContent = "Calculando envío…";
      try {
        const respuesta = await fetch(config.cotizacionEndpoint, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({
          whatsapp: whatsapp.value.trim(), direccion: direccion.value.trim(), numero: numero.value.trim()
        })});
        const resultado = await respuesta.json();
        if (!respuesta.ok || !resultado.ok) throw new Error(resultado.error || "No pudimos calcular el envío.");
        cotizacionEnvio = {costo: Number(resultado.costo_envio || 0), token: resultado.cotizacion_token || null};
        estado.textContent = "Envío: " + formatearPrecio(cotizacionEnvio.costo);
        actualizarResumenCheckout();
      } catch (error) {
        invalidarCotizacionEnvio();
        mostrarError(error.message || "No pudimos calcular el envío.");
      } finally {
        boton.disabled = false;
      }
    }

    function aplicarCotizacion(payload) {
      if (payload.entrega !== "delivery") return;
      if (!cotizacionEnvio) throw new Error("Calculá el envío antes de confirmar.");
      payload.cotizacion_token = cotizacionEnvio.token;
    }

    function mostrarError(mensaje) {
      interfaz.error.textContent = mensaje;
      interfaz.error.hidden = false;
    }

    function ocultarError() {
      interfaz.error.textContent = "";
      interfaz.error.hidden = true;
    }

    async function confirmar(evento) {
      evento.preventDefault();
      ocultarError();

      const form = interfaz.checkout;
      const boton = form.querySelector(".cl-carrito-confirmar");
      const datos = new FormData(form);
      const payload = {
        nombre: String(datos.get("nombre") || "").trim(),
        apellido: String(datos.get("apellido") || "").trim(),
        whatsapp: String(datos.get("whatsapp") || "").trim(),
        entrega: String(datos.get("entrega") || ""),
        pago: String(datos.get("pago") || ""),
        paga_con: String(datos.get("paga_con") || "").trim(),
        observaciones: String(datos.get("observaciones") || "").trim(),
        direccion: datos.get("entrega") === "delivery" ? String(datos.get("direccion") || "").trim() : "",
        numero: datos.get("entrega") === "delivery" ? String(datos.get("numero") || "").trim() : "",
        referencia: datos.get("entrega") === "delivery" ? String(datos.get("referencia") || "").trim() : "",
        idempotency_key: obtenerIdempotencyKey(),
        items: Object.values(carrito).map(function (item) {
          return {id: item.id, cantidad: item.cantidad};
        })
      };

      boton.disabled = true;
      boton.textContent = "Validando pedido...";
      try {
        aplicarCotizacion(payload);
        const respuesta = await fetch(config.endpoint, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        });
        const resultado = await respuesta.json();
        if (!respuesta.ok || !resultado.ok) {
          throw new Error(resultado.error || "No pudimos preparar el pedido.");
        }

        limpiarIdempotencyKey();
        vaciar();
        cerrar();
        if (resultado.whatsapp_url) window.location.href = resultado.whatsapp_url;
        else window.alert(resultado.aviso || "El pedido quedó guardado.");
      } catch (error) {
        mostrarError(error.message || "No pudimos preparar el pedido.");
      } finally {
        boton.disabled = false;
        boton.textContent = "Confirmar y abrir WhatsApp";
      }
    }

    document.addEventListener("keydown", function (evento) {
      if (abierto && evento.key === "Escape") {
        cerrar();
      }
    });
  }

  window.ClickLocalCarrito = {iniciar: iniciar};
  if (window.ClickLocalCarritoConfig) {
    iniciar(window.ClickLocalCarritoConfig);
  }
})();
