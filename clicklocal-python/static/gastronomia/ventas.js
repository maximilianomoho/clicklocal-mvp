(() => {
  const pantalla = document.querySelector(".ventas-dia");
  if (!pantalla) return;

  pantalla.addEventListener("click", async (evento) => {
    const botonCierre = evento.target.closest("[data-cerrar-caja-url]");
    if (botonCierre && !botonCierre.disabled) {
      const confirmado = window.confirm(
        "¿Cerrar la caja?\nSe guardará el resumen actual de ventas.",
      );
      if (!confirmado) return;

      botonCierre.disabled = true;
      try {
        const respuesta = await fetch(botonCierre.dataset.cerrarCajaUrl, {
          method: "POST",
          credentials: "same-origin",
        });
        const datos = await respuesta.json().catch(() => ({}));
        if (!respuesta.ok || !datos.ok) {
          throw new Error(datos.error || "No se pudo cerrar la caja.");
        }
        window.alert(datos.mensaje || "Caja cerrada correctamente.");
        window.location.reload();
      } catch (error) {
        window.alert(error.message || "No se pudo cerrar la caja.");
        botonCierre.disabled = false;
      }
      return;
    }

    const boton = evento.target.closest("[data-anular-venta-url]");
    if (!boton || boton.disabled) return;

    const confirmado = window.confirm(
      "¿Seguro que querés anular esta venta?\nLa operación seguirá registrada en el historial.",
    );
    if (!confirmado) return;

    const motivo = window.prompt("Motivo de la anulación (máximo 200 caracteres):", "");
    if (motivo === null) return;
    if (!motivo.trim()) {
      window.alert("Ingresá el motivo de anulación.");
      return;
    }

    boton.disabled = true;
    try {
      const respuesta = await fetch(boton.dataset.anularVentaUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmado: true, motivo: motivo.trim() }),
      });
      const datos = await respuesta.json().catch(() => ({}));
      if (!respuesta.ok || !datos.ok) {
        throw new Error(datos.error || "No se pudo anular la venta.");
      }
      window.location.reload();
    } catch (error) {
      window.alert(error.message || "No se pudo anular la venta.");
      boton.disabled = false;
    }
  });
})();
