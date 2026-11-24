// Worker de correo `cem-pagos-por-correo` (staging: `cem-pagos-por-correo-staging`), corte PI-S5
// del diseño «Pagos informados» del repositorio CEM (regla PC-2).
//
// Cloudflare Email Routing le entrega cada correo que llega a `pagos@cem-pagos[-staging].holancloud.com`.
// El Worker no lee el correo: lo pasa entero a `cem-backend` (`POST /api/v1/entrada/correo-pagos`)
// con la clave compartida, y el backend decide. El plan gratis da poco procesador por correo: nada
// de analizar adjuntos acá.
//
// - 200: recibido. El correo no se reenvía a nadie.
// - 401 o 422: se rechaza el correo y el remitente recibe el rebote con el texto que mandó el
//   backend (`mensaje`), o uno genérico si no vino.
// - Cualquier otra respuesta, o la red caída: el Worker falla. **SIN VERIFICAR** qué contesta
//   Cloudflare al servidor que envía en ese caso (la documentación no lo dice): se comprueba en
//   staging con el backend apagado.
//
// Secretos del Worker (`secret_text`, nunca en este archivo):
//   CEM_CORREO_PAGOS_CLAVE   la misma que la variable de `cem-backend[-staging]` en Coolify
//   CF_ACCESS_CLIENT_ID      el token de servicio de Cloudflare Access (staging está detrás de Access)
//   CF_ACCESS_CLIENT_SECRET
// Variable de texto:
//   CEM_ENTRADA_URL          https://cem-staging.holancloud.com/api/v1/entrada/correo-pagos

const REBOTE_GENERICO =
  "No se pudo recibir el comprobante. El pago se puede informar desde la aplicación Consorcio en Mano.";

export default {
  async email(message, env) {
    const crudo = await new Response(message.raw).arrayBuffer();
    const encabezados = {
      "Content-Type": "message/rfc822",
      "X-CEM-Clave-Correo": env.CEM_CORREO_PAGOS_CLAVE,
    };
    if (env.CF_ACCESS_CLIENT_ID) {
      encabezados["CF-Access-Client-Id"] = env.CF_ACCESS_CLIENT_ID;
      encabezados["CF-Access-Client-Secret"] = env.CF_ACCESS_CLIENT_SECRET;
    }
    const respuesta = await fetch(env.CEM_ENTRADA_URL, {
      method: "POST",
      headers: encabezados,
      body: crudo,
    });
    if (respuesta.ok) {
      return;
    }
    if (respuesta.status === 401 || respuesta.status === 422) {
      let mensaje = REBOTE_GENERICO;
      if (respuesta.status === 422) {
        const cuerpo = await respuesta.json().catch(() => null);
        if (cuerpo && typeof cuerpo.mensaje === "string" && cuerpo.mensaje) {
          mensaje = cuerpo.mensaje;
        }
      }
      message.setReject(mensaje);
      return;
    }
    throw new Error(`cem-backend respondió ${respuesta.status}`);
  },
};
