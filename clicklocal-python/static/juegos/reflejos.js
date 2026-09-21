(() => {
  const area = document.getElementById("reflejosArea");
  const mensaje = document.getElementById("reflejosMensaje");
  const instruccion = document.getElementById("reflejosInstruccion");
  const icono = document.getElementById("reflejosIcono");
  const botonVisual = document.getElementById("reflejosBotonVisual");
  const estadoTexto = document.getElementById("reflejosEstado");
  const resultado = document.getElementById("reflejosResultado");
  const otraVez = document.getElementById("jugarOtraVez");
  let estado = "reposo";
  let nonce = null;
  let stimulusStartedAt = null;
  let activacionTimer = null;

  function mostrarEstado(nombre, titulo, detalle, emoji, accion = "") {
    estado = nombre;
    area.className = `reflejos-area estado-${nombre}`;
    mensaje.textContent = titulo;
    instruccion.textContent = detalle;
    icono.textContent = emoji;
    botonVisual.textContent = accion;
    botonVisual.hidden = !accion;
  }

  async function empezar() {
    if (estado === "preparando" || estado === "espera" || estado === "activo") return;
    window.clearTimeout(activacionTimer);
    nonce = null;
    stimulusStartedAt = null;
    resultado.hidden = true;
    otraVez.hidden = true;
    estadoTexto.textContent = "";
    mostrarEstado("preparando", "Preparando…", "Un instante.", "⚡");
    try {
      const data = await ClickJuegos.json("/jugar/api/reflejos/intentos", {
        method: "POST", body: "{}",
      });
      nonce = data.nonce;
      mostrarEstado("espera", "Esperá…", "No toques hasta que cambie la pantalla.", "✋");
      activacionTimer = window.setTimeout(() => {
        if (estado !== "espera" || !nonce) return;
        mostrarEstado("activo", "¡AHORA!", "Tocá lo más rápido que puedas.", "⚡");
        stimulusStartedAt = performance.now();
        ClickJuegos.audio.playReady();
      }, data.delay_ms);
    } catch (error) {
      mostrarEstado("error", "No pudimos iniciar", error.message, "↻", "Intentar de nuevo");
    }
  }

  function adelantado() {
    const nonceActual = nonce;
    nonce = null;
    window.clearTimeout(activacionTimer);
    mostrarEstado("adelantado", "Te adelantaste 😅", "Esperá a que cambie la pantalla.", "✋", "Intentar de nuevo");
    estadoTexto.textContent = "Este intento no cuenta.";
    ClickJuegos.audio.playError();
    if (nonceActual) {
      ClickJuegos.json("/jugar/api/reflejos/intentos/cancelar", {
        method: "POST", body: JSON.stringify({ nonce: nonceActual }),
      }).catch(() => {});
    }
  }

  async function finalizar() {
    if (!nonce || stimulusStartedAt === null) return;
    const reactionMs = performance.now() - stimulusStartedAt;
    const nonceActual = nonce;
    nonce = null;
    mostrarEstado("procesando", "Calculando…", "Estamos validando tu tiempo.", "⚡");
    try {
      const data = await ClickJuegos.json("/jugar/api/reflejos/partidas", {
        method: "POST",
        body: JSON.stringify({ nonce: nonceActual, reaction_ms: reactionMs }),
      });
      document.getElementById("reflejosScore").textContent = ClickJuegos.scoreMs(data.score);
      document.getElementById("reflejosMejor").textContent = ClickJuegos.scoreMs(data.personal_best);
      document.getElementById("reflejosRecordMensaje").textContent = data.is_new_record ? "⚡ Nuevo récord personal" : "";
      document.getElementById("reflejosPuesto").textContent = data.weekly_position ? `Puesto semanal: #${data.weekly_position}` : "";
      resultado.hidden = false;
      otraVez.hidden = false;
      mostrarEstado("resultado", ClickJuegos.scoreMs(data.score), "¡Buen intento!", "✓", "Jugar otra vez");
      ClickJuegos.audio.playSuccess();
      if (data.is_new_record) ClickJuegos.audio.playRecord();
    } catch (error) {
      otraVez.hidden = false;
      mostrarEstado("error", "Intento no válido", error.message, "↻", "Intentar de nuevo");
    }
  }

  area.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    ClickJuegos.audio.unlock();
    if (estado === "reposo" || estado === "resultado" || estado === "error" || estado === "adelantado") empezar();
    else if (estado === "espera") adelantado();
    else if (estado === "activo") finalizar();
  });
  otraVez.addEventListener("click", empezar);

  const rankingPanel = document.getElementById("rankingPanel");
  async function cargarRanking(periodo = "semana") {
    rankingPanel.hidden = false;
    const lista = document.getElementById("rankingLista");
    const posicion = document.getElementById("rankingPosicion");
    lista.innerHTML = "<li>Cargando...</li>";
    try {
      const data = await ClickJuegos.json(`/jugar/api/reflejos/ranking?periodo=${encodeURIComponent(periodo)}`);
      lista.replaceChildren(...data.entries.map((entry) => {
        const li = document.createElement("li");
        const nombre = document.createElement("span");
        const marca = document.createElement("strong");
        nombre.textContent = `#${entry.position} ${entry.alias}`;
        marca.textContent = ClickJuegos.scoreMs(entry.score);
        li.append(nombre, marca);
        return li;
      }));
      if (!data.entries.length) lista.innerHTML = "<li>Todavía no hay marcas para este período.</li>";
      posicion.textContent = data.player_position ? `Tu posición: #${data.player_position}` : "";
    } catch (error) {
      lista.innerHTML = "";
      posicion.textContent = error.message;
    }
  }
  document.getElementById("verRanking").addEventListener("click", () => cargarRanking("semana"));
  document.querySelectorAll("[data-periodo]").forEach((boton) => boton.addEventListener("click", () => {
    document.querySelectorAll("[data-periodo]").forEach((b) => b.classList.toggle("activo", b === boton));
    cargarRanking(boton.dataset.periodo);
  }));
  document.getElementById("aliasForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const estadoAlias = document.getElementById("aliasEstado");
    try {
      const data = await ClickJuegos.json("/jugar/api/jugador/alias", {
        method: "POST", body: JSON.stringify({ alias: document.getElementById("aliasInput").value }),
      });
      estadoAlias.textContent = `Nombre guardado: ${data.alias}`;
      await cargarRanking("semana");
      window.setTimeout(() => { document.getElementById("aliasPanel").hidden = true; }, 700);
    } catch (error) {
      estadoAlias.textContent = error.message;
    }
  });
})();
