"""Qué se le dice a cada persona de una orden de pago: `situacion`, `paso` y `le_toca_a`.

Diseño: `doc/diseno/Rediseno_Circuito_Pagos_2026-11-06.md` del repositorio CEM, §2 y §3. Es puro:
los hechos y los sí o no llegan de `nucleo.acciones_de_ordenes_lectura` (`0072`), que los decide
con las reglas de la base. Acá solo se elige qué decir.

**Una declaración vigente manda.** §3 habla de una declaración confirmable y §9 de una vigente: se
toma la vigente, porque si faltan declaraciones de otras personas la orden tampoco se puede volver
a pagar y la ficha tiene que decir qué falta.

**Sin ejecución manual** (el riel) una orden en ejecución o en verificación queda `pagando`: §3 no
la nombra, y ninguna persona la resuelve.

**Una sola persona paga.** Con la orden en `pagando` y alguien que ya vio la cuenta, otro ejecutor
espera el comprobante: la base tampoco le da la cuenta (`pago_en_curso`, `0078`). Pasado el plazo,
puede tomar el pago (decisión 1 del propietario, 07-11-2026).

**Un pago sin salida no queda invisible.** En ejecución, sin nadie que haya visto la cuenta y sin
poder abrirla (la aprobación venció, por ejemplo), `renovar` no sirve: la base solo renueva una
orden aprobada. La salida es declarar que no se pagó, y después volver a pagar.

**`para_pagar` no tiene `espera_*`:** §2 no lo define: a quien no le toca pagar no le aparece.

**Qué se muestra lo dice la tabla de la pantalla** (`cem_contracts.pantalla_pagos`, informe de
fricción, parte 4): en qué lista va cada par situación y paso, cuál es la acción principal y cuáles
las demás, el nombre del estado y la barra. Una acción se ofrece solo si su `puede_*` es verdadero:
la tabla no concede nada.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from cem_contracts.egresos import (
    AccionOfrecida,
    AccionPago,
    AvisoRechazo,
    BarraPago,
    ElementoBandejaPagos,
    EstadoOrdenPago,
    EstadoVisible,
    Franja,
    GrupoPago,
    LeTocaA,
    LugarEnLaBarra,
    OrdenPago,
    OrdenParecida,
    PasoPago,
    SituacionPago,
    TipoAccion,
)
from cem_contracts.pantalla_pagos import FilaPantalla, cargar

PANTALLA = cargar()
"""La tabla de la pantalla de pagos. Inválida, el backend no arranca."""


@dataclass(frozen=True)
class HechosPago:
    """En qué anda la ejecución manual de la versión vigente."""

    manual: bool = False
    pago_sin_conciliar: bool = False
    pago_observado: bool = False
    declaracion_vigente: bool = False
    declaracion_confirmable: bool = False
    cuenta_vista: bool = False
    diferencia_sin_resolver: bool = False
    reclamo_abierto: bool = False
    pagada_de_menos: bool = False
    cotejada: bool = False
    """El pago de la orden pagada se cotejó con el banco y el cotejo sigue vigente (P-05)."""
    cerrada_sin_pago: bool = False
    """No se pagó y alguien la cerró porque ya no hace falta pagarla (decisión 12, `0096`)."""


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
    puede_resolver_diferencia: bool = False
    pago_de_otro: bool = False
    """Quien pide podría pagar, y otra persona vio la cuenta y no dijo nada."""
    puede_tomar: bool = False
    tomar_desde: datetime | None = None
    puede_corregir_diferencia: bool = False
    lectura_disputada: bool = False
    proveedor_cambiado: bool = False
    """Quien pide prepara, y el proveedor cambió después de aprobar la orden."""


SIN_ACCIONES = AccionesOrden()
"""La función de la base lee solo las órdenes en curso y las pagadas de menos: las demás no admiten
estas acciones."""

SE_VAN_SOLOS = {PasoPago.PREPARAR_FALTANTE}
"""Órdenes cerradas: nada las saca de la bandeja, así que quedan `CEM_PAGOS_DIAS_BANDEJA` días.
Una rechazada ya no está en la bandeja: se avisa una vez a quien la preparó (decisión 3)."""

SIN_PARECIDAS = {EstadoOrdenPago.RECHAZADA, EstadoOrdenPago.RETIRADA}
"""Lo que no se va a pagar no cuenta como otra orden igual, como en el fraccionamiento (`0093`)."""

MODIFICABLES = {
    EstadoOrdenPago.BORRADOR,
    EstadoOrdenPago.EN_REVISION,
    EstadoOrdenPago.APROBADA,
    EstadoOrdenPago.FALLIDA,
}
"""Los estados en que `modificar_orden_pago` (`0045`) acepta una versión nueva."""

SITUACION_POR_ESTADO = {
    EstadoOrdenPago.BORRADOR: SituacionPago.BORRADOR,
    EstadoOrdenPago.EN_REVISION: SituacionPago.EN_REVISION,
    EstadoOrdenPago.RECHAZADA: SituacionPago.RECHAZADA,
    EstadoOrdenPago.RETIRADA: SituacionPago.RETIRADA,
    EstadoOrdenPago.APROBADA: SituacionPago.PARA_PAGAR,
    EstadoOrdenPago.PAGADA: SituacionPago.PAGADA,
    EstadoOrdenPago.FALLIDA: SituacionPago.NO_SE_PAGO,
    EstadoOrdenPago.CONCILIADA: SituacionPago.PAGADA,
}


def situacion_de(estado: EstadoOrdenPago, hechos: HechosPago) -> SituacionPago:
    """§3. Una diferencia manda: lo que pasó de verdad ya se dijo, y un registro observado o una
    declaración anteriores quedaron atrás."""
    if estado in SITUACION_POR_ESTADO:
        situacion = SITUACION_POR_ESTADO[estado]
        if situacion == SituacionPago.PAGADA and hechos.cotejada:
            return SituacionPago.PAGADA_Y_COTEJADA
        return situacion
    if hechos.diferencia_sin_resolver:
        return SituacionPago.CON_DIFERENCIA
    if hechos.reclamo_abierto:
        return SituacionPago.RECLAMO
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


def reabre_cuenta(situacion: SituacionPago, a: AccionesOrden) -> bool:
    """Si la ficha ofrece abrir de nuevo una cuenta ya vista. En `pagando`, a quien la vio; con una
    declaración vigente es «Retomar el pago» (§9), y solo si nadie más la vio sin declarar: si no,
    la misma orden se podría transferir dos veces."""
    if not a.puede_pagar:
        return False
    if situacion == SituacionPago.PAGANDO:
        return a.puede_registrar
    if situacion == SituacionPago.NO_PAGO_DECLARADO:
        return a.declaracion_confirmable or a.falta_que_declares
    return False


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
        case SituacionPago.RETIRADA:
            # Sale de la bandeja de todos (comentario 23): no le toca ni espera nada a nadie.
            return None
        case SituacionPago.PARA_PAGAR:
            if a.puede_renovar:
                return PasoPago.RENOVAR
            if a.proveedor_cambiado:
                return PasoPago.MODIFICAR
            return PasoPago.PAGAR if ofrece_pagar(situacion, a) else None
        case SituacionPago.PAGANDO:
            if a.puede_subir_comprobante:
                return PasoPago.SUBIR_COMPROBANTE
            if a.pago_sin_salida:
                return PasoPago.DECLARAR_NO_PAGO if a.puede_declarar else PasoPago.ESPERA_DECLARAR
            if ofrece_pagar(situacion, a):
                return PasoPago.PAGAR
            if a.puede_tomar:
                return PasoPago.TOMAR_PAGO
            return PasoPago.ESPERA_COMPROBANTE if a.puede_pagar or a.pago_de_otro else None
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
            return PasoPago.VOLVER_A_PAGAR if prepara and not a.cerrada_sin_pago else None
        case SituacionPago.PAGADA | SituacionPago.PAGADA_Y_COTEJADA:
            # Cotejada es terminada (§4.2, regla 3), con una excepción explícita en la tabla
            # (decisión 2 del 13-11): pagada de menos conserva preparar lo que falta.
            return PasoPago.PREPARAR_FALTANTE if prepara and a.pagada_de_menos else None
        case SituacionPago.CON_DIFERENCIA | SituacionPago.RECLAMO:
            return (
                PasoPago.RESOLVER_DIFERENCIA
                if a.puede_resolver_diferencia
                else PasoPago.ESPERA_RESOLVER
            )


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


AVISABLES = {
    PasoPago.APROBAR: "aprobar",
    PasoPago.PAGAR: "pagar",
    PasoPago.SUBIR_COMPROBANTE: "pagar",
    PasoPago.CONCILIAR: "conciliar",
}
"""Pregunta 7 del propietario (13-11-2026): el correo diario va a quien tiene algo para aprobar,
pagar o conciliar. Subir el comprobante es terminar de pagar."""


def para_avisar(estado: EstadoOrdenPago, a: AccionesOrden, prepara: bool) -> str | None:
    """Qué tiene que hacer con la orden quien la lee, si es algo que se avisa por correo. La misma
    regla que la bandeja: el paso, que le toque a esa persona y que la orden esté en una lista."""
    situacion = situacion_de(estado, a)
    paso = paso_de(situacion, a, prepara)
    if paso not in AVISABLES or le_toca_a(paso, a) != LeTocaA.VOS:
        return None
    if fila_de(situacion, paso).lista is None:
        return None
    return AVISABLES[paso]


def estado_visible(situacion: SituacionPago) -> EstadoVisible:
    return PANTALLA.situaciones[situacion].estado


def barra_de(situacion: SituacionPago, orden: OrdenPago, ahora: datetime) -> BarraPago | None:
    """La barra depende de la orden, no de quien mira (§4.2, regla 4). Con la aprobación vencida,
    Pagar sigue siendo el paso actual, con el aviso."""
    barra = PANTALLA.situaciones[situacion].barra
    vencida = orden.aprobacion_vence_en is not None and orden.aprobacion_vence_en <= ahora
    if barra is not None and situacion == SituacionPago.PARA_PAGAR and vencida:
        return barra.model_copy(update={"actual": LugarEnLaBarra.ALERTA})
    return barra


def fila_de(situacion: SituacionPago, paso: PasoPago | None) -> FilaPantalla:
    return PANTALLA.fila(situacion, paso)


def acciones_ofrecidas(
    fila: FilaPantalla, puede: dict[AccionPago, bool], comprobante_no_coincide: bool = False
) -> list[AccionOfrecida]:
    """Las acciones de la fila que la base acepta hoy, en el orden en que se dibujan. Sin el sí de
    la base, una acción no se ofrece aunque la tabla la nombre."""
    botones = fila
    if comprobante_no_coincide and fila.si_el_comprobante_no_coincide is not None:
        botones = fila.si_el_comprobante_no_coincide
    ofrecidas = []
    for tipo, codigos in (
        (TipoAccion.PRINCIPAL, [botones.principal] if botones.principal else []),
        (TipoAccion.SECUNDARIA, botones.secundarias),
        (TipoAccion.NEGATIVA, botones.negativas),
    ):
        ofrecidas += [AccionOfrecida(codigo=c, tipo=tipo) for c in codigos if puede.get(c, False)]
    return ofrecidas


def cuenta_para_parecidas(o: OrdenPago) -> bool:
    """Lo que se compara al buscar órdenes parecidas (decisiones 5 y 4 del propietario, 12 y
    13-11-2026). Lo que no se va a pagar no cuenta, como en el fraccionamiento. Una orden rutinaria,
    respaldada por un contrato o abono, tampoco: repetirse es lo que se espera de ella."""
    return o.estado not in SIN_PARECIDAS and o.franja != Franja.RUTINARIA


type ClaveParecida = tuple[UUID, int, str]


def clave_parecida(proveedor_id: UUID, importe_centavos: int, moneda: str) -> ClaveParecida:
    return (proveedor_id, importe_centavos, moneda)


def _la_mas_cercana(momento: datetime, candidatas: list[OrdenPago], ventana: timedelta):
    cerca = [x for x in candidatas if abs(x.creada_en - momento) <= ventana]
    return min(cerca, key=lambda y: (abs(y.creada_en - momento), str(y.id)), default=None)


def parecidas(
    ordenes: list[OrdenPago], situaciones: dict[UUID, SituacionPago], dias: int
) -> dict[UUID, OrdenParecida]:
    """Decisión 5 del propietario (12-11-2026): otra orden del mismo proveedor, el mismo importe y
    la misma moneda preparada con menos de `dias` días de diferencia. De varias, la más cercana en
    el tiempo. Solo avisa: dos pagos iguales pueden ser legítimos.

    Se agrupa por proveedor, importe y moneda y, dentro de cada grupo ordenado por fecha, la más
    cercana a una orden es su vecina de un lado o del otro: n log n, no todos los pares."""
    ventana = timedelta(days=dias)
    grupos: dict[ClaveParecida, list[OrdenPago]] = {}
    for o in ordenes:
        if cuenta_para_parecidas(o):
            clave = clave_parecida(o.proveedor_id, o.importe_centavos, o.moneda)
            grupos.setdefault(clave, []).append(o)
    resultado = {}
    for grupo in grupos.values():
        grupo.sort(key=lambda y: (y.creada_en, str(y.id)))
        for i, o in enumerate(grupo):
            vecinas = grupo[max(i - 1, 0) : i] + grupo[i + 1 : i + 2]
            x = _la_mas_cercana(o.creada_en, vecinas, ventana)
            if x is not None:
                resultado[o.id] = OrdenParecida(
                    orden_id=x.id,
                    creada_en=x.creada_en,
                    estado_visible=estado_visible(situaciones[x.id]),
                )
    return resultado


def parecida_a(
    clave: ClaveParecida,
    momento: datetime,
    ordenes: list[OrdenPago],
    dias: int,
    salvo: UUID | None = None,
) -> OrdenPago | None:
    """La orden parecida a una que todavía no se guardó (o a una versión nueva de `salvo`), para
    avisar antes de guardar (decisión 4 del 13-11). Solo mira las de su grupo."""
    candidatas = [
        o
        for o in ordenes
        if o.id != salvo
        and cuenta_para_parecidas(o)
        and clave_parecida(o.proveedor_id, o.importe_centavos, o.moneda) == clave
    ]
    return _la_mas_cercana(momento, candidatas, timedelta(days=dias))


def avisos_de_rechazo(
    ordenes: list[OrdenPago], prepara: bool, para_avisar: set[UUID]
) -> list[AvisoRechazo]:
    """Las rechazadas que se avisan arriba de la bandeja (decisión 3): `para_avisar` son las que
    preparó quien pide y todavía no vio. De la más nueva a la más vieja."""
    if not prepara or not fila_de(SituacionPago.RECHAZADA, PasoPago.RECHAZADA).aviso_una_vez:
        return []
    avisos = [
        AvisoRechazo(
            orden_id=o.id,
            proveedor=o.razon_social,
            concepto=o.concepto,
            importe_centavos=o.importe_centavos,
            moneda=o.moneda,
            proveedor_id=o.proveedor_id,
            motivo=o.motivo_rechazo or "",
            creada_en=o.creada_en,
            rechazada_en=o.actualizada_en,
        )  # fmt: skip
        for o in ordenes
        if o.estado == EstadoOrdenPago.RECHAZADA and o.id in para_avisar
    ]
    return sorted(avisos, key=lambda a: a.rechazada_en, reverse=True)


@dataclass(frozen=True)
class Plazos:
    cerradas_desde: datetime
    """Lo pagado de menos antes de esto queda solo en la consulta."""
    viejos_desde: datetime
    """Un borrador sin movimiento desde antes pasa a «Borradores viejos» (decisión 4)."""
    dias_repetida: int
    """La ventana del aviso de orden repetida (decisión 5)."""


def bandeja(
    ordenes: list[OrdenPago],
    acciones: dict,
    prepara: bool,
    plazos: Plazos,
) -> list[ElementoBandejaPagos]:
    """Lo que quien pide tiene que ver ahora, por grupo y de lo más viejo a lo más nuevo. La lista
    de cada orden la dice la tabla de la pantalla; sin lista, la orden no está en la bandeja."""
    grupos = list(GrupoPago)
    situaciones = {o.id: situacion_de(o.estado, acciones.get(o.id, SIN_ACCIONES)) for o in ordenes}
    repetidas = parecidas(ordenes, situaciones, plazos.dias_repetida)
    filas = []
    for o in ordenes:
        a = acciones.get(o.id, SIN_ACCIONES)
        situacion = situaciones[o.id]
        paso = paso_de(situacion, a, prepara)
        if paso is None or (paso in SE_VAN_SOLOS and o.actualizada_en < plazos.cerradas_desde):
            continue
        fila = fila_de(situacion, paso)
        grupo = fila.lista
        if grupo is None:
            continue
        if fila.lista_si_viejo is not None and o.actualizada_en < plazos.viejos_desde:
            grupo = fila.lista_si_viejo
        filas.append((grupos.index(grupo), o.actualizada_en, ElementoBandejaPagos(
            orden_id=o.id, proveedor=o.razon_social, concepto=o.concepto,
            importe_centavos=o.importe_centavos, moneda=o.moneda, paso=paso, grupo=grupo,
            le_toca_a=le_toca_a(paso, a),
            lectura_disputada=a.lectura_disputada and paso == PasoPago.CONCILIAR,
            situacion=situacion, estado_visible=estado_visible(situacion),
            principal=fila.principal, pendiente=PANTALLA.listas[grupo].pendiente,
            creada_en=o.creada_en, actualizada_en=o.actualizada_en,
            posible_duplicada_de=repetidas.get(o.id),
        )))  # fmt: skip
    filas.sort(key=lambda f: f[:2])
    return [f[2] for f in filas]
