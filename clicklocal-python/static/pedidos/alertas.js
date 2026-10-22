(function () {
  "use strict";

  const CLAVE_SONIDO = "clicklocal_pedidos_sonido";
  const CLAVE_VOLUMEN = "clicklocal_pedidos_volumen";
  const INTERVALO_REPETICION = 45000;
  const GANANCIA_MAXIMA = 0.7;
  const GANANCIA_MAXIMA_BASE = 0.85;
  const PATRON_ALERTA = [
    { frecuencia: 660, inicio: 0, duracion: 0.24 },
    { frecuencia: 880, inicio: 0.32, duracion: 0.25 },
    { frecuencia: 740, inicio: 0.68, duracion: 0.25 },
    { frecuencia: 1040, inicio: 1.04, duracion: 0.32 },
  ];
  const PATRON_ALERTA_BASE = [
    { frecuencia: 740, inicio: 0, duracion: 0.28 },
    { frecuencia: 1080, inicio: 0.34, duracion: 0.30 },
    { frecuencia: 740, inicio: 0.72, duracion: 0.28 },
    { frecuencia: 1080, inicio: 1.06, duracion: 0.30 },
    { frecuencia: 740, inicio: 1.44, duracion: 0.28 },
    { frecuencia: 1080, inicio: 1.78, duracion: 0.30 },
    { frecuencia: 880, inicio: 2.16, duracion: 0.36 },
  ];
  const esGastronomiaBase = Boolean(document.querySelector(".pedidos-recibidos"));

  let habilitado = false;
  let contexto = null;
  let nuevos = 0;
  let temporizadorRepeticion = null;
  let volumen = leerVolumen();

  function leerVolumen() {
    const guardadoTexto = window.localStorage.getItem(CLAVE_VOLUMEN);
    const guardado = Number(guardadoTexto);
    if (guardadoTexto !== null && Number.isFinite(guardado) && guardado >= 0 && guardado <= 100) return guardado;
    const inicial = Number(document.querySelector("[data-volumen-alerta]")?.value);
    return Number.isFinite(inicial) && inicial >= 0 && inicial <= 100 ? inicial : 70;
  }

  function actualizarEstado(mensaje, esError) {
    document.querySelectorAll("[data-estado-sonido]").forEach(function (estado) {
      estado.textContent = mensaje;
      estado.classList.toggle("pedidos-alerta-estado--error", Boolean(esError));
    });
  }

  function obtenerContexto() {
    if (!contexto) {
      const AudioContexto = window.AudioContext || window.webkitAudioContext;
      if (!AudioContexto) return null;
      contexto = new AudioContexto();
    }
    const reanudacion = contexto.resume();
    if (reanudacion && typeof reanudacion.catch === "function") {
      reanudacion.catch(function () {
        actualizarEstado("El navegador bloqueó el audio. Activá la alarma mediante un clic.", true);
      });
    }
    return contexto;
  }

  function reproducirAlerta(muestraCorta) {
    if (!habilitado || document.hidden) return;
    const audio = obtenerContexto();
    if (!audio || volumen === 0) return;
    const patron = esGastronomiaBase ? PATRON_ALERTA_BASE : PATRON_ALERTA;
    const gananciaMaxima = esGastronomiaBase ? GANANCIA_MAXIMA_BASE : GANANCIA_MAXIMA;
    const gananciaObjetivo = (volumen / 100) * gananciaMaxima;
    const tonos = muestraCorta && !esGastronomiaBase ? patron.slice(0, 2) : patron;
    tonos.forEach(function (tono) {
      const inicio = audio.currentTime + tono.inicio;
      const fin = inicio + tono.duracion;
      const oscilador = audio.createOscillator();
      const envolvente = audio.createGain();
      oscilador.type = "triangle";
      oscilador.frequency.setValueAtTime(tono.frecuencia, inicio);
      envolvente.gain.setValueAtTime(0.0001, inicio);
      envolvente.gain.exponentialRampToValueAtTime(gananciaObjetivo, inicio + 0.035);
      envolvente.gain.exponentialRampToValueAtTime(0.0001, fin);
      oscilador.connect(envolvente);
      envolvente.connect(audio.destination);
      oscilador.start(inicio);
      oscilador.stop(fin + 0.02);
    });
  }

  function detenerRepeticion() {
    if (temporizadorRepeticion !== null) {
      window.clearTimeout(temporizadorRepeticion);
      temporizadorRepeticion = null;
    }
  }

  function programarRepeticion() {
    if (!habilitado || nuevos <= 0 || temporizadorRepeticion !== null) return;
    temporizadorRepeticion = window.setTimeout(function () {
      temporizadorRepeticion = null;
      if (habilitado && nuevos > 0 && !document.hidden) reproducirAlerta(false);
      programarRepeticion();
    }, INTERVALO_REPETICION);
  }

  function actualizarBotones() {
    document.querySelectorAll("[data-activar-sonido]").forEach(function (boton) {
      if (habilitado) boton.textContent = "🔕 Desactivar sonido";
      else if (window.localStorage.getItem(CLAVE_SONIDO) === "1") boton.textContent = "🔔 Reactivar sonido";
      else boton.textContent = "🔔 Activar sonido";
      boton.setAttribute("aria-pressed", habilitado ? "true" : "false");
    });
  }

  function alternarSonido() {
    habilitado = !habilitado;
    window.localStorage.setItem(CLAVE_SONIDO, habilitado ? "1" : "0");
    if (habilitado) {
      obtenerContexto();
      reproducirAlerta(true);
      programarRepeticion();
      actualizarEstado("Alarma activada. Se reprodujo una prueba de sonido.", false);
    } else {
      detenerRepeticion();
      actualizarEstado("Alarma desactivada.", false);
    }
    actualizarBotones();
  }

  window.ClickLocalAlertaPedido = {
    avisar: function () {
      reproducirAlerta(false);
      programarRepeticion();
    },
    actualizarPendientes: function (cantidad) {
      nuevos = Math.max(0, Number(cantidad) || 0);
      if (nuevos === 0) detenerRepeticion();
      else programarRepeticion();
    },
  };

  document.querySelectorAll("[data-activar-sonido]").forEach(function (boton) {
    boton.addEventListener("click", alternarSonido);
  });
  document.querySelectorAll("[data-volumen-alerta]").forEach(function (control) {
    const salida = control.closest("label")?.querySelector("[data-volumen-alerta-valor]");
    control.value = String(volumen);
    if (salida) salida.textContent = volumen + "%";
    control.addEventListener("input", function () {
      volumen = Math.min(100, Math.max(0, Number(control.value) || 0));
      window.localStorage.setItem(CLAVE_VOLUMEN, String(volumen));
      if (salida) salida.textContent = volumen + "%";
    });
    control.addEventListener("change", function () {
      if (!habilitado) {
        habilitado = true;
        window.localStorage.setItem(CLAVE_SONIDO, "1");
        actualizarBotones();
      }
      reproducirAlerta(true);
      programarRepeticion();
      actualizarEstado("Volumen guardado. Se reprodujo una prueba de sonido.", false);
    });
  });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) programarRepeticion();
  });
  actualizarBotones();
})();
