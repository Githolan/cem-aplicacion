// Qué navegaciones atiende el service worker yendo a la red (vite.config.ts).
//
// El problema (staging, 21-10-2026): con `navigateFallback`, el service worker respondía toda
// navegación con el index.html de su caché, sin pasar por la red. Staging está detrás de
// Cloudflare Access: cuando vencía la sesión de Access, cada pedido a /health o /api recibía la
// redirección al login de Access (otro origen), que fetch no sigue, y la página mostraba "No
// pudimos contactar al servicio". Recargar no lo arreglaba, porque la recarga también salía de la
// caché y nunca llegaba a Access para renovar la sesión; tampoco llegaba la versión nueva.
//
// Ahora la navegación va a la red: con conexión responde el servidor (o la redirección de
// Access, que el navegador sigue). Solo si la red falla se usa el index.html de la precaché, el
// de la versión instalada. Sin plazo de espera: una red lenta espera a la red y nunca sirve una
// versión vieja (el problema de /entrar del 14-10-2026).
//
// Workbox copia esta función como texto dentro de sw.js: no puede usar nada de afuera, ni
// siquiera las constantes de enlace.ts. swNavegacion.test.ts comprueba que excluye exactamente
// las rutas de esas constantes.

/**
 * Una navegación de la aplicación. Quedan afuera /api y /health (nunca salen de caché, RN25) y los
 * enlaces del correo (/entrar, /invitacion, /invitacion-mandato), que siempre van a la red sin
 * pasar por el service worker. Se compara la ruta más la consulta, sin el fragmento.
 */
export function esNavegacionDeLaApp({ request, url }: { request: Request; url: URL }): boolean {
  return (
    request.mode === "navigate" &&
    !/^\/(?:api\/|health|entrar(?:[/?]|$)|invitacion(?:[/?]|$)|invitacion-mandato(?:[/?]|$))/.test(
      url.pathname + url.search,
    )
  );
}
