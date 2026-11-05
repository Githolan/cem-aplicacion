import { useSyncExternalStore } from "react";

// Instalación de la PWA (documento 13, §14.2). Chrome y Edge en Android y en computadoras
// ofrecen el evento `beforeinstallprompt`, que permite instalar con un botón propio. Safari
// en iPhone no lo tiene: ahí solo sirve "Compartir → Agregar a inicio", y se explica.

interface EventoInstalacion extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

export type FormaDeInstalar =
  | "instalada" // ya se abrió desde el ícono
  | "recien_instalada" // se acaba de instalar desde esta pestaña
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
  if (standalone) return "instalada";
  if (recienInstalada) return "recien_instalada";
  if (eventoGuardado) return "boton";
  if (esIphone(navigator.userAgent, navigator.maxTouchPoints ?? 0)) return "iphone";
  return "menu";
}

/** Abre la instalación del navegador y dice qué eligió la persona; un fallo del navegador sube. */
export async function instalar(): Promise<"accepted" | "dismissed"> {
  const evento = eventoGuardado;
  if (!evento) throw new Error("no hay instalación ofrecida");
  // El evento sirve una sola vez, acepte o no la persona.
  eventoGuardado = null;
  avisar();
  await evento.prompt();
  const { outcome } = await evento.userChoice;
  return outcome;
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
