(() => {
  const STORAGE_KEY = "clickjuegos_sonido";
  let enabled = true;
  let audioContext = null;
  let interacted = false;

  try {
    enabled = window.localStorage.getItem(STORAGE_KEY) !== "off";
  } catch (_) {}

  function context() {
    if (!enabled || !interacted) return null;
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return null;
    if (!audioContext) audioContext = new AudioContextClass();
    if (audioContext.state === "suspended") audioContext.resume().catch(() => {});
    return audioContext;
  }

  function tone(frequency, duration, delay = 0, volume = 0.045, type = "sine") {
    const ctx = context();
    if (!ctx) return;
    const startsAt = ctx.currentTime + delay;
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.type = type;
    oscillator.frequency.setValueAtTime(frequency, startsAt);
    gain.gain.setValueAtTime(0.0001, startsAt);
    gain.gain.exponentialRampToValueAtTime(volume, startsAt + 0.008);
    gain.gain.exponentialRampToValueAtTime(0.0001, startsAt + duration);
    oscillator.connect(gain);
    gain.connect(ctx.destination);
    oscillator.start(startsAt);
    oscillator.stop(startsAt + duration + 0.015);
  }

  function updateControls() {
    document.querySelectorAll("[data-clickjuegos-sonido]").forEach((button) => {
      button.setAttribute("aria-pressed", String(enabled));
      button.textContent = enabled ? "🔊 Sonido" : "🔇 Sonido";
      button.title = enabled ? "Silenciar sonidos de ClickJuegos" : "Activar sonidos de ClickJuegos";
    });
  }

  const audio = {
    unlock() {
      interacted = true;
      context();
    },
    isEnabled() {
      return enabled;
    },
    setEnabled(value) {
      enabled = Boolean(value);
      try {
        window.localStorage.setItem(STORAGE_KEY, enabled ? "on" : "off");
      } catch (_) {}
      if (enabled) this.unlock();
      updateControls();
    },
    toggle() {
      this.setEnabled(!enabled);
    },
    playReady() {
      tone(660, 0.075, 0, 0.05, "sine");
    },
    playSuccess() {
      tone(440, 0.09, 0, 0.035, "sine");
      tone(554, 0.11, 0.075, 0.04, "sine");
    },
    playError() {
      tone(210, 0.1, 0, 0.035, "triangle");
      tone(165, 0.12, 0.08, 0.03, "triangle");
    },
    playRecord() {
      tone(659, 0.08, 0.13, 0.035, "sine");
      tone(784, 0.09, 0.2, 0.04, "sine");
      tone(988, 0.13, 0.28, 0.04, "sine");
    },
  };

  window.ClickJuegos = {
  async json(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const data = await response.json().catch(() => ({ error: "Respuesta inválida del servidor." }));
    if (!response.ok) throw new Error(data.error || "No se pudo completar la operación.");
    return data;
  },
  score(value) {
    return Number(value).toLocaleString("es-AR", {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    }) + " %";
  },
  scoreMs(value) {
    return `${Math.round(Number(value))} ms`;
  },
    audio,
  };

  document.querySelectorAll("[data-clickjuegos-sonido]").forEach((button) => {
    button.addEventListener("click", () => audio.toggle());
  });
  updateControls();
})();
