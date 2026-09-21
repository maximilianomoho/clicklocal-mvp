(() => {
  const area = document.getElementById("cincoArea");
  const mensaje = document.getElementById("cincoMensaje");
  const instruccion = document.getElementById("cincoInstruccion");
  const icono = document.getElementById("cincoIcono");
  const botonVisual = document.getElementById("cincoBotonVisual");
  const estadoTexto = document.getElementById("cincoEstado");
  const resultado = document.getElementById("cincoResultado");
  const otraVez = document.getElementById("jugarOtraVez");
  let estado = "reposo";
  let nonce = null;
  let startedAt = null;

  function mostrarEstado(nombre, titulo, detalle, emoji, accion = "") {
    estado = nombre;
    area.className = `cinco-area estado-${nombre}`;
    mensaje.textContent = titulo;
    instruccion.textContent = detalle;
    icono.textContent = emoji;
    botonVisual.textContent = accion;
    botonVisual.hidden = !accion;
  }

  function formatoSegundos(ms) {
    return `${(Number(ms) / 1000).toLocaleString("es-AR", {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    })} s`;
  }

  function formatoDiferencia(ms) {
    const valor = Number(ms);
    const signo = valor > 0 ? "+" : valor < 0 ? "−" : "";
    return `${signo}${formatoSegundos(Math.abs(valor))} respecto de 5,00 s`;
  }

  function formatoError(ms) {
    return `${Math.round(Number(ms))} ms de error`;
  }

  async function empezar() {
    if (estado === "preparando" || estado === "corriendo" || estado === "procesando") return;
    nonce = null;
    startedAt = null;
    resultado.hidden = true;
    otraVez.hidden = true;
    estadoTexto.textContent = "";
    mostrarEstado("preparando", "Preparando…", "Un instante.", "⏱");
    try {
      const data = await ClickJuegos.json("/jugar/api/5-segundos/intentos", {
        method: "POST", body: "{}",
      });
      nonce = data.nonce;
      mostrarEstado(
        "corriendo",
        "El tiempo está corriendo",
        "Cuando creas que pasaron 5 segundos, tocá otra vez.",
        "⏱",
      );
      startedAt = performance.now();
      ClickJuegos.audio.playReady();
    } catch (error) {
      mostrarEstado("error", "No pudimos iniciar", error.message, "↻", "Intentar de nuevo");
    }
  }

  async function finalizar() {
    if (!nonce || startedAt === null) return;
    const elapsedMs = performance.now() - startedAt;
    const nonceActual = nonce;
    nonce = null;
    startedAt = null;
    mostrarEstado("procesando", "Calculando…", "Estamos validando tu tiempo.", "⏱");
    try {
      const data = await ClickJuegos.json("/jugar/api/5-segundos/partidas", {
        method: "POST",
        body: JSON.stringify({ nonce: nonceActual, elapsed_ms: elapsedMs }),
      });
      document.getElementById("cincoTiempo").textContent = formatoSegundos(data.elapsed_ms);
      document.getElementById("cincoDiferencia").textContent = formatoDiferencia(data.difference_ms);
      document.getElementById("cincoMejor").textContent = formatoError(data.personal_best);
      document.getElementById("cincoRecordMensaje").textContent = data.is_new_record ? "⏱ Nuevo récord personal" : "";
      document.getElementById("cincoPuesto").textContent = data.weekly_position ? `Puesto semanal: #${data.weekly_position}` : "";
      resultado.hidden = false;
      otraVez.hidden = false;
      mostrarEstado("resultado", formatoSegundos(data.elapsed_ms), formatoError(data.score), "✓", "Jugar otra vez");
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
    if (estado === "reposo" || estado === "resultado" || estado === "error") empezar();
    else if (estado === "corriendo") finalizar();
  });
  otraVez.addEventListener("click", empezar);

  const rankingPanel = document.getElementById("rankingPanel");
  async function cargarRanking(periodo = "semana") {
    rankingPanel.hidden = false;
    const lista = document.getElementById("rankingLista");
    const posicion = document.getElementById("rankingPosicion");
    lista.innerHTML = "<li>Cargando...</li>";
    try {
      const data = await ClickJuegos.json(`/jugar/api/5-segundos/ranking?periodo=${encodeURIComponent(periodo)}`);
      lista.replaceChildren(...data.entries.map((entry) => {
        const li = document.createElement("li");
        const nombre = document.createElement("span");
        const marca = document.createElement("strong");
        nombre.textContent = `#${entry.position} ${entry.alias}`;
        marca.textContent = formatoError(entry.score);
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
