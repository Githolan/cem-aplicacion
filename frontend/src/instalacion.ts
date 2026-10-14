import { useSyncExternalStore } from "react";

// Instalación de la PWA (documento 13, §14.2). Chrome y Edge en Android y en computadoras
// ofrecen el evento `beforeinstallprompt`, que permite instalar con un botón propio. Safari
// en iPhone no lo tiene: ahí solo sirve "Compartir → Agregar a inicio", y se explica.

interface EventoInstalacion extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

export type FormaDeInstalar =
  | "instalada" // ya se abrió desde el ícono o se acaba de instalar
  | "boton" // el navegador acepta instalar con un botón
  | "iphone" // iPhone o iPad: hay que usar "Agregar a inicio"
  | "menu"; // otro navegador: se instala desde su menú, si lo permite

let eventoGuardado: EventoInstalacion | null = null;
let recienInstalada = false;
const oyentes = new Set<() => void>();

function avisar() {
  for (const oyente of oyentes) oyente();
}

// Se registra al cargar el módulo, antes de montar React, porque el navegador dispara el
// evento una sola vez y temprano.
export function escucharInstalacion(ventana: Window = window) {
  ventana.addEventListener("beforeinstallprompt", (evento) => {
    evento.preventDefault();
    eventoGuardado = evento as EventoInstalacion;
    avisar();
  });
  ventana.addEventListener("appinstalled", () => {
    eventoGuardado = null;
    recienInstalada = true;
    avisar();
  });
}

function suscribir(oyente: () => void) {
  oyentes.add(oyente);
  return () => oyentes.delete(oyente);
}

export function esIphone(agente: string, puntosTactiles: number): boolean {
  // iPadOS se presenta como Mac; se distingue porque una Mac no tiene pantalla táctil.
  return /iPhone|iPad|iPod/.test(agente) || (/Macintosh/.test(agente) && puntosTactiles > 1);
}

function formaActual(): FormaDeInstalar {
  const standalone =
    window.matchMedia?.("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true;
  if (standalone || recienInstalada) return "instalada";
  if (eventoGuardado) return "boton";
  if (esIphone(navigator.userAgent, navigator.maxTouchPoints ?? 0)) return "iphone";
  return "menu";
}

export async function instalar(): Promise<void> {
  const evento = eventoGuardado;
  if (!evento) return;
  // El evento sirve una sola vez, acepte o no la persona.
  eventoGuardado = null;
  await evento.prompt();
  await evento.userChoice;
  avisar();
}

export function useInstalacion(): FormaDeInstalar {
  return useSyncExternalStore(suscribir, formaActual);
}

// Solo para pruebas: vuelve al estado inicial entre casos.
export function reiniciarInstalacion() {
  eventoGuardado = null;
  recienInstalada = false;
  avisar();
}
