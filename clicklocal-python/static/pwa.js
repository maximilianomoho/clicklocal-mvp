let clickLocalInstallPrompt = null;

function clickLocalIsStandalone() {
  return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
}

async function clickLocalRegisterServiceWorker() {
  if ("serviceWorker" in navigator) {
    try {
      await navigator.serviceWorker.register("/sw.js");
      console.log("ClickLocal PWA: service worker registrado");
    } catch (error) {
      console.log("ClickLocal PWA: error registrando service worker", error);
    }
  }
}

function clickLocalMostrarBotonInstalar() {
  if (clickLocalIsStandalone()) return;
  if (document.getElementById("clicklocal-install-float")) return;

  const boton = document.createElement("button");
  boton.id = "clicklocal-install-float";
  boton.type = "button";
  boton.textContent = "📱 Instalar App";
  boton.style.cssText = `
    position: fixed;
    right: 14px;
    bottom: 14px;
    z-index: 9999;
    border: none;
    border-radius: 999px;
    padding: 12px 16px;
    background: #ff7a00;
    color: #ffffff;
    font-weight: 800;
    box-shadow: 0 8px 24px rgba(0,0,0,.22);
    font-family: inherit;
  `;

  boton.addEventListener("click", async () => {
    if (clickLocalInstallPrompt) {
      clickLocalInstallPrompt.prompt();
      await clickLocalInstallPrompt.userChoice;
      clickLocalInstallPrompt = null;
      boton.remove();
    } else {
      alert("Para instalar ClickLocal:\n\nAndroid: menú ⋮ → Instalar app.\n\niPhone: Compartir → Agregar a pantalla de inicio.");
    }
  });

  document.body.appendChild(boton);
}

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  clickLocalInstallPrompt = event;
  clickLocalMostrarBotonInstalar();
});

window.addEventListener("appinstalled", () => {
  console.log("ClickLocal PWA: app instalada");
  localStorage.setItem("clicklocal_app_instalada", "1");
  const boton = document.getElementById("clicklocal-install-float");
  if (boton) boton.remove();
});

document.addEventListener("DOMContentLoaded", () => {
  clickLocalRegisterServiceWorker();

  const path = window.location.pathname;
  const mostrarEnEstaPagina =
    path === "/" ||
    path.includes("index") ||
    path.includes("panel");

  if (mostrarEnEstaPagina && !clickLocalIsStandalone()) {
    clickLocalMostrarBotonInstalar();
  }
});
