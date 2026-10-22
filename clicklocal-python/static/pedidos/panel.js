(function () {
  "use strict";

  const panel = document.querySelector(".pedidos-panel");
  if (!panel) return;
  let ultima = panel.dataset.estadoBase || "";
  let consultando = false;
  const estado = panel.querySelector(".pedidos-estado");
  const contador = panel.querySelector("[data-contador-nuevos]");
  const aviso = panel.querySelector("[data-aviso-pedido-nuevo]");

  function actualizarPendientes(cantidad) {
    contador.textContent = cantidad;
    window.ClickLocalAlertaPedido?.actualizarPendientes(cantidad);
  }

  async function consultar() {
    if (consultando || document.hidden) return;
    consultando = true;
    try {
      const respuesta = await fetch(panel.dataset.actualizacionUrl, { credentials: "same-origin" });
      const datos = await respuesta.json();
      if (!respuesta.ok || !datos.ok) throw new Error(datos.error || "No se pudo actualizar.");
      actualizarPendientes(datos.nuevos);
      if (ultima && datos.ultima && datos.ultima > ultima) {
        estado.textContent = "Llegó un pedido nuevo. Actualizá la bandeja para verlo.";
        aviso.hidden = false;
        window.ClickLocalAlertaPedido?.avisar();
      }
      ultima = datos.ultima || ultima;
    } catch (error) {
      estado.textContent = error.message;
    } finally {
      consultando = false;
    }
  }

  panel.querySelector("[data-actualizar-pedidos]")?.addEventListener("click", function () {
    window.location.reload();
  });
  panel.addEventListener("click", async function (event) {
    const boton = event.target.closest("[data-estado]");
    if (!boton) return;
    if (boton.dataset.estado === "cancelado" && !window.confirm("¿Cancelar este pedido?")) return;
    boton.disabled = true;
    try {
      const tarjeta = boton.closest("[data-pedido-id]");
      const respuesta = await fetch("/panel/pedidos/" + tarjeta.dataset.pedidoId + "/estado", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ estado: boton.dataset.estado }),
      });
      const datos = await respuesta.json();
      if (!respuesta.ok || !datos.ok) throw new Error(datos.error || "No se pudo cambiar el estado.");
      window.location.reload();
    } catch (error) {
      estado.textContent = error.message;
      boton.disabled = false;
    }
  });
  actualizarPendientes(contador.textContent);
  window.setInterval(consultar, 15000);
})();

(function () {
  "use strict";

  const aceptaDelivery = document.getElementById("acepta-delivery");
  const configuracion = document.getElementById("delivery-configuracion");
  const usarCostoFijo = document.getElementById("usar-costo-fijo");
  const costoFijo = document.getElementById("delivery-costo-fijo-bloque");
  const distancia = document.getElementById("delivery-distancia-bloque");
  if (!aceptaDelivery || !configuracion || !usarCostoFijo || !costoFijo || !distancia) return;

  function sincronizarModoDelivery() {
    const activo = aceptaDelivery.checked;
    configuracion.hidden = !activo;
    costoFijo.hidden = !usarCostoFijo.checked;
    distancia.hidden = usarCostoFijo.checked;
  }

  usarCostoFijo.addEventListener("change", sincronizarModoDelivery);
  aceptaDelivery.addEventListener("change", sincronizarModoDelivery);
  sincronizarModoDelivery();
})();
