// Las rutas del circuito de pagos: una bandeja y una ficha (`Rediseno_Circuito_Pagos_2026-11-06.md`
// del repositorio CEM, §1). `/pagos/por-aclarar` está en `egresos/PagosPorAclarar.tsx`.

export const RUTA_PAGOS = "/pagos";
export const RUTA_NUEVA = "/pagos/nueva";
export const RUTA_CONSULTA = "/pagos/consulta";
export const RUTA_COTEJO = "/pagos/cotejo";

export const rutaFicha = (orden: string) => `${RUTA_PAGOS}/${orden}`;
