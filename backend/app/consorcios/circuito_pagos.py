"""Qué se le dice a cada persona de una orden de pago: `situacion`, `paso` y `le_toca_a`.

Diseño: `doc/diseno/Rediseno_Circuito_Pagos_2026-11-06.md` del repositorio CEM, §2 y §3. Es puro:
los hechos y los sí o no llegan de `nucleo.acciones_de_ordenes_lectura` (`0072`), que los decide
con las reglas de la base. Acá solo se elige qué decir.

**Una declaración vigente manda.** §3 habla de una declaración confirmable y §9 de una vigente: se
toma la vigente, porque si faltan declaraciones de otras personas la orden tampoco se puede volver
a pagar y la ficha tiene que decir qué falta.

**Sin ejecución manual** (el riel) una orden en ejecución o en verificación queda `pagando`: §3 no
la nombra, y ninguna persona la resuelve.

**Una sola persona paga.** Con la orden en `pagando` y alguien que ya vio la cuenta, a otro ejecutor
no se le ofrece pagar aunque la base se lo acepte: espera el comprobante. Evita la doble
transferencia (decisión del hilo principal, 06-11-2026).

**Un pago sin salida no queda invisible.** En ejecución, sin nadie que haya visto la cuenta y sin
poder abrirla (la aprobación venció, por ejemplo), `renovar` no sirve: la base solo renueva una
orden aprobada. La salida es declarar que no se pagó, y después volver a pagar.

**`para_pagar` no tiene `espera_*`:** §2 no lo define: a quien no le toca pagar no le aparece.
"""

from dataclasses import dataclass
from datetime import datetime

from cem_contracts.egresos import (
    GRUPO_DE_PASO,
    ElementoBandejaPagos,
    EstadoOrdenPago,
    GrupoPago,
    LeTocaA,
    OrdenPago,
    PasoPago,
    SituacionPago,
)


@dataclass(frozen=True)
class HechosPago:
    """En qué anda la ejecución manual de la versión vigente."""

    manual: bool = False
    pago_sin_conciliar: bool = False
    pago_observado: bool = False
    declaracion_vigente: bool = False
    declaracion_confirmable: bool = False
    cuenta_vista: bool = False


@dataclass(frozen=True)
class AccionesOrden(HechosPago):
    """Lo que la base va a aceptar de quien pide. Las órdenes cerradas no tienen ninguna."""

    puede_aprobar: bool = False
    puede_rechazar: bool = False
    puede_pagar: bool = False
    puede_renovar: bool = False
    puede_registrar: bool = False
    puede_declarar: bool = False
    puede_subir_comprobante: bool = False
    puede_corregir: bool = False
    puede_conciliar: bool = False
    puede_confirmar: bool = False
    falta_que_declares: bool = False
    pago_sin_salida: bool = False


SIN_ACCIONES = AccionesOrden()
"""La función de la base lee solo las órdenes en curso: las demás no admiten estas acciones."""

SITUACION_POR_ESTADO = {
    EstadoOrdenPago.BORRADOR: SituacionPago.BORRADOR,
    EstadoOrdenPago.EN_REVISION: SituacionPago.EN_REVISION,
    EstadoOrdenPago.RECHAZADA: SituacionPago.RECHAZADA,
    EstadoOrdenPago.APROBADA: SituacionPago.PARA_PAGAR,
    EstadoOrdenPago.PAGADA: SituacionPago.PAGADA,
    EstadoOrdenPago.FALLIDA: SituacionPago.NO_SE_PAGO,
    EstadoOrdenPago.CONCILIADA: SituacionPago.PAGADA,
}


def situacion_de(estado: EstadoOrdenPago, hechos: HechosPago) -> SituacionPago:
    """§3. Las que dependen de «Pagué otra cosa» todavía no tienen datos que las produzcan."""
    if estado in SITUACION_POR_ESTADO:
        return SITUACION_POR_ESTADO[estado]
    if hechos.declaracion_vigente:
        return SituacionPago.NO_PAGO_DECLARADO
    if hechos.pago_sin_conciliar:
        return SituacionPago.FALTA_CONCILIAR
    if hechos.pago_observado:
        return SituacionPago.OBSERVADO
    return SituacionPago.PAGANDO


def ofrece_pagar(situacion: SituacionPago, a: AccionesOrden) -> bool:
    """Si se le ofrece pagar: la base lo acepta, la orden está por pagarse y nadie vio la cuenta."""
    if situacion == SituacionPago.PAGANDO:
        return a.puede_pagar and not a.cuenta_vista
    return a.puede_pagar and situacion == SituacionPago.PARA_PAGAR


def paso_de(situacion: SituacionPago, a: AccionesOrden, prepara: bool) -> PasoPago | None:
    """§2. `prepara`: si quien pide lee la orden como quien prepara. Nulo si no le toca ni espera
    nada."""
    match situacion:
        case SituacionPago.BORRADOR:
            return PasoPago.ENVIAR if prepara else None
        case SituacionPago.EN_REVISION:
            return PasoPago.APROBAR if a.puede_aprobar else PasoPago.ESPERA_APROBAR
        case SituacionPago.RECHAZADA:
            return PasoPago.RECHAZADA if prepara else None
        case SituacionPago.PARA_PAGAR:
            if a.puede_renovar:
                return PasoPago.RENOVAR
            return PasoPago.PAGAR if ofrece_pagar(situacion, a) else None
        case SituacionPago.PAGANDO:
            if a.puede_subir_comprobante:
                return PasoPago.SUBIR_COMPROBANTE
            if a.pago_sin_salida:
                return PasoPago.DECLARAR_NO_PAGO if a.puede_declarar else PasoPago.ESPERA_DECLARAR
            if ofrece_pagar(situacion, a):
                return PasoPago.PAGAR
            return PasoPago.ESPERA_COMPROBANTE if a.puede_pagar else None
        case SituacionPago.FALTA_CONCILIAR:
            return PasoPago.CONCILIAR if a.puede_conciliar else PasoPago.ESPERA_CONCILIAR
        case SituacionPago.OBSERVADO:
            return PasoPago.CORREGIR if a.puede_corregir else PasoPago.ESPERA_CORREGIR
        case SituacionPago.NO_PAGO_DECLARADO:
            if a.puede_confirmar:
                return PasoPago.CONFIRMAR_NO_PAGO
            if a.falta_que_declares and a.puede_subir_comprobante:
                return PasoPago.SUBIR_COMPROBANTE
            return PasoPago.ESPERA_CONFIRMAR
        case SituacionPago.NO_SE_PAGO:
            return PasoPago.VOLVER_A_PAGAR if prepara else None
        case SituacionPago.PAGADA:
            return None
        case SituacionPago.CON_DIFERENCIA | SituacionPago.RECLAMO:
            raise ValueError(f"situación sin datos que la produzcan: {situacion}")


def le_toca_a(paso: PasoPago, hechos: HechosPago) -> LeTocaA:
    match paso:
        case PasoPago.ESPERA_CORREGIR:
            return LeTocaA.QUIEN_REGISTRO
        case PasoPago.ESPERA_DECLARAR:
            return LeTocaA.QUIEN_EJECUTA
        case PasoPago.ESPERA_COMPROBANTE:
            return LeTocaA.QUIEN_VIO_LA_CUENTA
        case PasoPago.ESPERA_CONFIRMAR if not hechos.declaracion_confirmable:
            return LeTocaA.QUIEN_VIO_LA_CUENTA
        case (
            PasoPago.ESPERA_APROBAR
            | PasoPago.ESPERA_CONCILIAR
            | PasoPago.ESPERA_CONFIRMAR
            | PasoPago.ESPERA_RESOLVER
        ):
            return LeTocaA.CONSEJO
        case _:
            return LeTocaA.VOS


def bandeja(
    ordenes: list[OrdenPago],
    acciones: dict,
    prepara: bool,
    rechazadas_desde: datetime,
) -> list[ElementoBandejaPagos]:
    """Lo que quien pide tiene que ver ahora, por grupo y de lo más viejo a lo más nuevo. Lo
    rechazado antes de `rechazadas_desde` queda solo en la consulta."""
    grupos = list(GrupoPago)
    filas = []
    for o in ordenes:
        a = acciones.get(o.id, SIN_ACCIONES)
        paso = paso_de(situacion_de(o.estado, a), a, prepara)
        if paso is None or (paso == PasoPago.RECHAZADA and o.actualizada_en < rechazadas_desde):
            continue
        grupo = GRUPO_DE_PASO[paso]
        filas.append((grupos.index(grupo), o.actualizada_en, ElementoBandejaPagos(
            orden_id=o.id, proveedor=o.razon_social, concepto=o.concepto,
            importe_centavos=o.importe_centavos, moneda=o.moneda, paso=paso, grupo=grupo,
            le_toca_a=le_toca_a(paso, a),
        )))  # fmt: skip
    filas.sort(key=lambda f: f[:2])
    return [f[2] for f in filas]
