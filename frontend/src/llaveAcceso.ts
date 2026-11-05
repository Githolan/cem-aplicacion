import { api } from "./api/cliente";
import { encabezadoCsrf } from "./csrf";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";

// Llave de acceso (WebAuthn): el PIN, la huella o la cara del dispositivo. El backend arma las
// opciones y comprueba la respuesta; acá solo se traducen los binarios entre base64url y los
// `ArrayBuffer` que pide `navigator.credentials`. Diseño: Llave_de_Acceso_Diseno.md (repositorio CEM).

type Json = Record<string, unknown>;
type Descriptor = { id: string; type: PublicKeyCredentialType; transports?: AuthenticatorTransport[] };

export type Resultado<T> = { ok: true; valor: T } | { ok: false; mensaje: string };

const CANCELADO = "No se confirmó. Probá de nuevo cuando quieras.";
const NO_CONFIRMADO = "No pudimos confirmar tu llave. Probá de nuevo.";
const FALTA_CONFIRMAR = "Primero confirmá que sos vos con tu llave o con un código.";
const YA_REGISTRADA = "Este dispositivo ya tiene una llave en tu cuenta.";

/** Si el navegador sabe usar llaves de acceso. Si no, se sigue ofreciendo el código. */
export function llaveDisponible(): boolean {
  return "PublicKeyCredential" in window && navigator.credentials !== undefined;
}

export function aBytes(texto: string): ArrayBuffer {
  const base64 = texto.replace(/-/g, "+").replace(/_/g, "/");
  const binario = atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, "="));
  return Uint8Array.from(binario, (c) => c.charCodeAt(0)).buffer;
}

export function aTexto(datos: ArrayBuffer): string {
  const binario = String.fromCharCode(...new Uint8Array(datos));
  return btoa(binario).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function descriptores(lista: unknown): PublicKeyCredentialDescriptor[] {
  return ((lista ?? []) as Descriptor[]).map((d) => ({ ...d, id: aBytes(d.id) }));
}

export function opcionesDeAlta(opciones: Json): PublicKeyCredentialCreationOptions {
  const usuario = opciones.user as { id: string; name: string; displayName: string };
  return {
    ...(opciones as unknown as PublicKeyCredentialCreationOptions),
    challenge: aBytes(opciones.challenge as string),
    user: { ...usuario, id: aBytes(usuario.id) },
    excludeCredentials: descriptores(opciones.excludeCredentials),
  };
}

export function opcionesDeVerificacion(opciones: Json): PublicKeyCredentialRequestOptions {
  return {
    ...(opciones as unknown as PublicKeyCredentialRequestOptions),
    challenge: aBytes(opciones.challenge as string),
    allowCredentials: descriptores(opciones.allowCredentials),
  };
}

function credencialDeAlta(credencial: PublicKeyCredential): Json {
  const respuesta = credencial.response as AuthenticatorAttestationResponse;
  return {
    id: credencial.id,
    rawId: aTexto(credencial.rawId),
    type: credencial.type,
    clientExtensionResults: credencial.getClientExtensionResults(),
    response: {
      clientDataJSON: aTexto(respuesta.clientDataJSON),
      attestationObject: aTexto(respuesta.attestationObject),
      transports: respuesta.getTransports(),
    },
  };
}

function credencialDeVerificacion(credencial: PublicKeyCredential): Json {
  const respuesta = credencial.response as AuthenticatorAssertionResponse;
  return {
    id: credencial.id,
    rawId: aTexto(credencial.rawId),
    type: credencial.type,
    clientExtensionResults: credencial.getClientExtensionResults(),
    response: {
      clientDataJSON: aTexto(respuesta.clientDataJSON),
      authenticatorData: aTexto(respuesta.authenticatorData),
      signature: aTexto(respuesta.signature),
      userHandle: respuesta.userHandle === null ? null : aTexto(respuesta.userHandle),
    },
  };
}

function mensajeDeRechazo(estado: number, error: unknown): string {
  const codigo = codigoDe(error);
  if (codigo === "segundo_factor_requerido") return FALTA_CONFIRMAR;
  if (codigo === "segundo_factor_invalido") return NO_CONFIRMADO;
  return mensajeDeFalla(estado, codigo);
}

/** Lo que dice el navegador cuando el gesto no se completó. Todo lo demás es falla de conexión. */
function mensajeDelDispositivo(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "InvalidStateError") return YA_REGISTRADA;
    if (error.name === "NotAllowedError" || error.name === "AbortError") return CANCELADO;
    return NO_CONFIRMADO;
  }
  return SIN_CONEXION;
}

/** Un nombre que la persona reconozca en su lista, sin pedirle que escriba nada. */
export function nombreDelDispositivo(agente: string): string {
  if (/iPhone|iPad/.test(agente)) return "iPhone o iPad";
  if (/Android/.test(agente)) return "Teléfono Android";
  if (/Windows/.test(agente)) return "Computadora con Windows";
  if (/Mac OS/.test(agente)) return "Mac";
  return "Este dispositivo";
}

/** Registra una llave en este dispositivo. Devuelve los códigos de recuperación, si hay nuevos. */
export async function registrarLlave(): Promise<Resultado<string[] | null>> {
  try {
    const pedido = await api.POST("/api/v1/auth/llave/alta/opciones", { headers: encabezadoCsrf() });
    if (pedido.data === undefined)
      return { ok: false, mensaje: mensajeDeRechazo(pedido.response.status, pedido.error) };
    const credencial = (await navigator.credentials.create({
      publicKey: opcionesDeAlta(pedido.data.opciones),
    })) as PublicKeyCredential;
    const alta = await api.POST("/api/v1/auth/llave/alta", {
      body: { credencial: credencialDeAlta(credencial), nombre: nombreDelDispositivo(navigator.userAgent) },
      headers: encabezadoCsrf(),
    });
    if (alta.data === undefined)
      return { ok: false, mensaje: mensajeDeRechazo(alta.response.status, alta.error) };
    return { ok: true, valor: alta.data.codigos_recuperacion ?? null };
  } catch (error) {
    return { ok: false, mensaje: mensajeDelDispositivo(error) };
  }
}

/** Confirma con la llave: la sesión queda verificada durante 10 minutos. */
export async function confirmarConLlave(): Promise<Resultado<null>> {
  try {
    const pedido = await api.POST("/api/v1/auth/llave/verificar/opciones", {
      headers: encabezadoCsrf(),
    });
    if (pedido.data === undefined)
      return { ok: false, mensaje: mensajeDeRechazo(pedido.response.status, pedido.error) };
    const credencial = (await navigator.credentials.get({
      publicKey: opcionesDeVerificacion(pedido.data.opciones),
    })) as PublicKeyCredential;
    const verificacion = await api.POST("/api/v1/auth/llave/verificar", {
      body: { credencial: credencialDeVerificacion(credencial) },
      headers: encabezadoCsrf(),
    });
    if (verificacion.data === undefined)
      return { ok: false, mensaje: mensajeDeRechazo(verificacion.response.status, verificacion.error) };
    return { ok: true, valor: null };
  } catch (error) {
    return { ok: false, mensaje: mensajeDelDispositivo(error) };
  }
}
