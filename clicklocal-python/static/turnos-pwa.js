(() => {
  let turnosInstallPrompt = null;
  const CLAVE_INSTALADA = "clicklocal_turnos_app_instalada";

  function estaEnModoAplicacion() {
    return window.matchMedia("(display-mode: standalone)").matches
      || window.navigator.standalone === true;
  }

  function estaInstalada() {
    return estaEnModoAplicacion()
      || localStorage.getItem(CLAVE_INSTALADA) === "1";
  }

  window.clickLocalTurnosEstadoInstalacion = function () {
    return {
      instalada: estaInstalada(),
      instalacionDirectaDisponible: Boolean(turnosInstallPrompt),
    };
  };

  window.clickLocalTurnosSolicitarInstalacion = async function () {
    if (!turnosInstallPrompt) {
      return {
        tipo: estaInstalada() ? "instalada" : "manual",
      };
    }

    const promptInstalacion = turnosInstallPrompt;
    turnosInstallPrompt = null;
    await promptInstalacion.prompt();
    const eleccion = await promptInstalacion.userChoice;

    return {
      tipo: "solicitada",
      resultado: eleccion.outcome,
    };
  };

  window.addEventListener("beforeinstallprompt", (evento) => {
    evento.preventDefault();
    turnosInstallPrompt = evento;
    window.dispatchEvent(
      new CustomEvent("clicklocalturnosinstallavailable")
    );
  });

  window.addEventListener("appinstalled", () => {
    turnosInstallPrompt = null;
    localStorage.setItem(CLAVE_INSTALADA, "1");
    window.dispatchEvent(new CustomEvent("clicklocalturnosinstalled"));
  });

  document.addEventListener("DOMContentLoaded", async () => {
    if (!("serviceWorker" in navigator)) return;

    try {
      await navigator.serviceWorker.register("/sw.js");
      console.log("ClickLocal Turnos: acceso instalado disponible");
    } catch (error) {
      console.log(
        "ClickLocal Turnos: no se pudo preparar la instalación",
        error
      );
    }
  });
})();
