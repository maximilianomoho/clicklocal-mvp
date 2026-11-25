(function () {
  "use strict";

  function fechaLocal(valor) {
    if (!valor) return "";
    const fecha = new Date(valor);
    if (Number.isNaN(fecha.getTime())) return "";
    return fecha.toLocaleString("es-AR", {
      dateStyle: "short",
      timeStyle: "short"
    });
  }

  const abrirModal = document.querySelector("[data-beneficios-modal-abrir]");
  const modal = document.querySelector("#beneficios-20-publico-modal");
  let focoAnterior = null;

  function cerrarModal() {
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    document.body.classList.remove("beneficios-20-publico-modal-abierto");
    if (abrirModal) abrirModal.setAttribute("aria-expanded", "false");
    if (focoAnterior) focoAnterior.focus();
  }

  if (abrirModal && modal) {
    abrirModal.addEventListener("click", function () {
      focoAnterior = document.activeElement;
      modal.hidden = false;
      document.body.classList.add("beneficios-20-publico-modal-abierto");
      abrirModal.setAttribute("aria-expanded", "true");
      modal.querySelector(".beneficios-20-publico-modal__panel").focus();
    });
    modal.querySelectorAll("[data-beneficios-modal-cerrar]").forEach(function (boton) {
      boton.addEventListener("click", cerrarModal);
    });
    document.addEventListener("keydown", function (evento) {
      if (evento.key === "Escape" && !modal.hidden) cerrarModal();
    });
  }

  const selector = document.querySelector("[data-beneficios-selector]");
  if (selector && modal) {
    selector.addEventListener("change", function () {
      modal.querySelectorAll("[data-beneficio-indice]").forEach(function (tarjeta) {
        tarjeta.hidden = tarjeta.dataset.beneficioIndice !== selector.value;
      });
    });
  }

  document.querySelectorAll("[data-beneficio]").forEach(function (tarjeta) {
    const boton = tarjeta.querySelector(".beneficios-20-publico__obtener");
    const estado = tarjeta.querySelector(".beneficios-20-publico__estado");
    const cupon = tarjeta.querySelector(".beneficios-20-publico__cupon");
    const copiar = tarjeta.querySelector(".beneficios-20-publico__copiar");
    if (!boton || !estado || !cupon) return;

    if (copiar) {
      copiar.addEventListener("click", async function () {
        const codigo = tarjeta.querySelector(".beneficios-20-publico__codigo").textContent.trim();
        if (!codigo) return;
        try {
          await navigator.clipboard.writeText(codigo);
          copiar.textContent = "Código copiado";
        } catch (error) {
          estado.textContent = "No pudimos copiarlo automáticamente. Mantené presionado el código para copiarlo.";
        }
      });
    }

    boton.addEventListener("click", async function () {
      if (boton.disabled) return;
      boton.disabled = true;
      boton.setAttribute("aria-busy", "true");
      estado.textContent = "Generando tu código...";

      try {
        const respuesta = await fetch(tarjeta.dataset.endpoint, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Accept": "application/json",
            "X-CSRF-Token": tarjeta.dataset.csrf || ""
          }
        });
        const datos = await respuesta.json();
        if (!respuesta.ok || !datos.ok) {
          throw new Error(datos.mensaje || "No pudimos generar el código.");
        }

        tarjeta.querySelector(".beneficios-20-publico__codigo").textContent = datos.codigo;
        const vencimiento = tarjeta.querySelector(".beneficios-20-publico__vencimiento");
        const fecha = fechaLocal(datos.vence_en);
        vencimiento.textContent = fecha ? "Válido hasta " + fecha : "Consultá la vigencia indicada.";

        const enviar = tarjeta.querySelector(".beneficios-20-publico__enviar");
        const presencial = tarjeta.querySelector(".beneficios-20-publico__presencial");
        if (datos.whatsapp_url) {
          enviar.href = datos.whatsapp_url;
          enviar.hidden = false;
        } else {
          enviar.hidden = true;
        }
        presencial.hidden = !datos.permite_presentar;
        presencial.textContent = datos.whatsapp_url
          ? "También podés mostrar este código en el comercio."
          : "Mostrá este código en el comercio para usar el beneficio.";

        cupon.hidden = false;
        estado.textContent = "Código generado. Podés enviarlo o mostrarlo en el comercio.";
        boton.textContent = "Código obtenido";
      } catch (error) {
        estado.textContent = error.message || "Beneficios no está disponible temporalmente.";
        boton.disabled = false;
      } finally {
        boton.removeAttribute("aria-busy");
      }
    });
  });
}());
