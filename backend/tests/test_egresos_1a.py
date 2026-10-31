"""H3, decisión 1A (`0050`): franjas, umbral por acta, contratos que respaldan lo rutinario,
vigencia de 7 días de la aprobación, emisión y decisión 6A. Datos SINTETICOS / PRUEBA / NO REAL.

Diseño: `doc/diseno/H3_Proveedor_Beneficiario_Cuenta.md` del repositorio CEM, «Decisión 1A», con
el mensaje del propietario del 30-10-2026. Cada negación lleva su control: el mismo pedido sin lo
que lo hace inválido pasa. Las reglas de 0045 a 0049 se prueban en `test_orden_pago_neutra.py`,
`test_proveedor_0048.py` y `test_rutas_egresos.py`.
"""

from datetime import timedelta
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.conftest import clave_idempotencia
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import decidir_equipo
from tests.test_orden_pago_neutra import (
    APROBAR,
    EMITIR,
    ENVIAR,
    ESENCIALES,
    MODIFICAR,
    PREPARAR,
    Escena,
    cbu_sintetico,
    escena_economica,
    exigir,
    huella,
    llamar,
    niega,
    nuevo_proveedor,
    orden,
    otorgar,
    otra_cuenta_en_el_consejo,
    versiones,
)
from tests.test_rutas_egresos import codigo, get, post
from tests.test_vinculo_0014 import (  # noqa: F401  (`base_aparte` es un fixture)
    _consorcio_y_unidad,
    _persona,
    base_aparte,
)

pytestmark = pytest.mark.db

IMPORTE = ESENCIALES["importe"]
RENOVAR = "select nucleo.renovar_orden_vencida(:orden, :version, :actor, :origen, :sesion)"
REGISTRAR_CONTRATO = (
    "select nucleo.registrar_contrato_proveedor(:proveedor, :tipo, :alcance, :tope, :moneda,"
    " :desde, :hasta, :documento, :decision, :actor, :origen, :sesion)"
)
CONFIRMAR_CONTRATO = (
    "select nucleo.confirmar_contrato_proveedor(:contrato, :huella_k, :actor, :origen, :sesion)"
)
BAJA_CONTRATO = "select nucleo.baja_contrato_proveedor(:contrato, :actor, :origen, :sesion)"


@pytest.fixture
def e(motor_migrate, motor_app) -> Escena:
    return escena_economica(motor_migrate, motor_app)


def umbral(motor_app, cons, importe: int, moneda="ARS", tipo="asamblea") -> UUID:
    """Como soporte (`scripts/umbral_orden_pago.py`), con el acta: rige desde hoy."""
    acta = decidir_equipo(motor_app, cons, tipo)
    with transaccion(motor_app, persona_id=None, consorcio_id=cons) as c:
        return c.execute(
            text("select nucleo.fijar_umbral_orden_pago(:i, :m, nucleo.hoy_del_consorcio(:c),"
                 " :a, null, 'soporte')"),
            {"i": importe, "m": moneda, "c": cons, "a": acta},
        ).scalar_one()  # fmt: skip


def franja(motor_migrate, o, numero=1) -> str:
    return versiones(motor_migrate, o)[numero - 1]["franja"]


def enviada(motor_migrate, e, **extra) -> UUID:
    o = llamar(motor_migrate, e, e.adm, PREPARAR, **extra)
    llamar(motor_migrate, e, e.adm, ENVIAR, orden=o, version=1)
    return o


def aprobada(motor_migrate, e, **extra) -> UUID:
    """Enviada y, si la franja lo pide, aprobada por mc1 (alcanza sin configuración)."""
    o = enviada(motor_migrate, e, **extra)
    if orden(motor_migrate, o)["estado"] == "en_revision":
        llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1,
               huella=huella(motor_migrate, o, 1))  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "aprobada"
    return o


def conocido(motor_migrate, e) -> UUID:
    """Una orden aprobada antes al proveedor de la escena: desde ahí es `conocido`."""
    return aprobada(motor_migrate, e)


def documento_de_contrato(motor_migrate, e, categoria="contratos_proveedores",
                          version="disponible") -> UUID:  # fmt: skip
    """Un documento con una versión en el estado dado (`None`: sin versiones). Desde 0052 solo
    respalda un contrato si tiene una versión `disponible` (pasó el antivirus)."""
    with motor_migrate.begin() as c:
        doc = c.execute(
            text("insert into nucleo.documento (consorcio_id, categoria, titulo, fecha,"
                 " cargado_por) values (:c, :k, 'Contrato SINTETICO (NO REAL)', current_date,"
                 " :p) returning id"),
            {"c": e.cons, "k": categoria, "p": e.adm},
        ).scalar_one()  # fmt: skip
        if version is not None:
            analizada = version != "en_cuarentena"
            c.execute(
                text("insert into nucleo.version_documento (consorcio_id, documento_id, estado,"
                     " motivo_rechazo, tipo, tamano, sha256, clave_objeto, antivirus, firmas_en,"
                     " subida_por, analizada_en) values (:c, :d, :estado, :motivo,"
                     " 'application/pdf', 1, repeat('a', 64), :clave, :av,"
                     " case when :estado = 'disponible' then now() end, :p,"
                     " case when :estado <> 'en_cuarentena' then now() end)"),
                {"c": e.cons, "d": doc, "estado": version, "p": e.adm,
                 "motivo": "virus" if version == "rechazado" else None,
                 "clave": f"sintetico/{doc}", "av": "ClamAV SINTETICO" if analizada else None},
            )  # fmt: skip
        return doc


def contrato(motor_migrate, e, *, tope=IMPORTE, moneda="ARS", proveedor=None,
             documento=None, dias=365) -> UUID:  # fmt: skip
    hoy = _hoy(motor_migrate, e)
    return llamar(
        motor_migrate, e, e.adm, REGISTRAR_CONTRATO,
        proveedor=proveedor or e.proveedor, tipo="abono",
        alcance="SINTETICO: mantenimiento mensual del ascensor", tope=tope, moneda=moneda,
        desde=hoy, hasta=hoy + timedelta(days=dias),
        documento=documento or documento_de_contrato(motor_migrate, e), decision=None,
    )  # fmt: skip


def huella_contrato(motor_migrate, k) -> bytes:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select huella from nucleo.contrato_proveedor where id = :k"), {"k": k}
        ).scalar_one()


def confirmado(motor_migrate, e, **kw) -> UUID:
    k = contrato(motor_migrate, e, **kw)
    llamar(motor_migrate, e, e.mc1, CONFIRMAR_CONTRATO, contrato=k,
           huella_k=huella_contrato(motor_migrate, k))  # fmt: skip
    return k


def _hoy(motor_migrate, e):
    with motor_migrate.connect() as c:
        return c.execute(text("select nucleo.hoy_del_consorcio(:c)"), {"c": e.cons}).scalar_one()


def nueva_cuenta(motor_migrate, e, semilla: int) -> None:
    """Versión 2 del proveedor de la escena con otra cuenta (como dueño de la base)."""
    with motor_migrate.begin() as c:
        c.execute(
            text("insert into nucleo.proveedor_version (consorcio_id, proveedor_id, numero,"
                 " razon_social, estado, cuenta_destino, huella, preparada_por,"
                 " identidad_preparo) values (:c, :p, 2, 'Proveedor SINTETICO (NO REAL)',"
                 " 'activo', :cta, nucleo.huella_proveedor(:p, 2, null,"
                 " 'Proveedor SINTETICO (NO REAL)', :cta), :q,"
                 " nucleo.identidad_humana_de(:c, :q))"),
            {"c": e.cons, "p": e.proveedor, "cta": cbu_sintetico(semilla), "q": e.adm},
        )  # fmt: skip


def envejecer(motor_migrate, o, delta: timedelta) -> None:
    """Corre hacia atrás el momento en que la orden llegó a Aprobada."""
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.orden_pago set aprobada_en = now() - :d where id = :o"),
            {"d": delta, "o": o},
        )


# Franjas -------------------------------------------------------------------------------------


def test_sin_umbral_todo_lo_no_rutinario_pide_aprobacion(motor_migrate, e):
    conocido(motor_migrate, e)
    o = enviada(motor_migrate, e)
    assert franja(motor_migrate, o) == "sin_umbral"
    assert orden(motor_migrate, o)["estado"] == "en_revision"
    assert versiones(motor_migrate, o)[0]["umbral_id"] is None


def test_un_umbral_por_moneda(motor_migrate, motor_app, e):
    conocido(motor_migrate, e)
    umbral(motor_app, e.cons, IMPORTE * 10, moneda="USD")
    assert franja(motor_migrate, enviada(motor_migrate, e)) == "sin_umbral"
    # El de pesos, registrado el mismo día, rige para las órdenes en pesos sin apagar el otro.
    umbral(motor_app, e.cons, IMPORTE * 10)
    assert franja(motor_migrate, enviada(motor_migrate, e)) == "bajo_umbral"
    assert franja(motor_migrate, enviada(motor_migrate, e, moneda="USD")) == "bajo_umbral"


def test_proveedor_nuevo_pide_aprobacion_aunque_este_bajo_el_umbral(motor_migrate, motor_app, e):
    umbral(motor_app, e.cons, IMPORTE * 10)
    o = enviada(motor_migrate, e)
    assert (franja(motor_migrate, o), orden(motor_migrate, o)["estado"]) == (
        "beneficiario_nuevo", "en_revision")  # fmt: skip
    # Control: aprobada esa, la siguiente al mismo proveedor y cuenta ya es conocida.
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=huella(motor_migrate, o, 1))
    otra = enviada(motor_migrate, e)
    assert (franja(motor_migrate, otra), orden(motor_migrate, otra)["estado"]) == (
        "bajo_umbral", "aprobada")  # fmt: skip


def test_conocido_bajo_y_sobre_el_umbral(motor_migrate, motor_app, e):
    conocido(motor_migrate, e)
    umbral(motor_app, e.cons, IMPORTE)
    igual = enviada(motor_migrate, e)  # igual al umbral: no lo supera
    assert (franja(motor_migrate, igual), orden(motor_migrate, igual)["estado"]) == (
        "bajo_umbral", "aprobada")  # fmt: skip
    assert orden(motor_migrate, igual)["aprobada_en"] is not None
    arriba = enviada(motor_migrate, e, importe=IMPORTE + 1)
    assert (franja(motor_migrate, arriba), orden(motor_migrate, arriba)["estado"]) == (
        "sobre_umbral", "en_revision")  # fmt: skip


@pytest.mark.parametrize("importe", [IMPORTE, IMPORTE * 100])
def test_cuenta_cambiada_pide_aprobacion_bajo_y_sobre_el_umbral(motor_migrate, motor_app, e,
                                                                importe):  # fmt: skip
    conocido(motor_migrate, e)
    umbral(motor_app, e.cons, IMPORTE * 10)
    # Declarar un contrato confirmado del proveedor tampoco la hace rutinaria: la cuenta cambiada
    # gana.
    k = confirmado(motor_migrate, e, tope=IMPORTE * 100)
    nueva_cuenta(motor_migrate, e, 77)
    o = enviada(motor_migrate, e, importe=importe, contrato=k)
    assert (franja(motor_migrate, o), orden(motor_migrate, o)["estado"]) == (
        "cuenta_cambiada", "en_revision")  # fmt: skip


def test_el_fondo_de_reserva_siempre_pide_aprobacion(motor_migrate, motor_app, e):
    conocido(motor_migrate, e)
    umbral(motor_app, e.cons, IMPORTE * 10)
    k = confirmado(motor_migrate, e, tope=IMPORTE * 10)
    o = enviada(motor_migrate, e, reserva=True, contrato=k)
    assert (franja(motor_migrate, o), orden(motor_migrate, o)["estado"]) == (
        "fondo_reserva", "en_revision")  # fmt: skip
    # Control: sin el fondo, la misma orden es rutinaria.
    assert franja(motor_migrate, enviada(motor_migrate, e, contrato=k)) == "rutinaria"


# Contratos y lo rutinario -------------------------------------------------------------------


def test_rutinaria_solo_con_un_contrato_declarado_confirmado_y_que_la_cubre(motor_migrate, e):
    conocido(motor_migrate, e)
    k = contrato(motor_migrate, e, tope=IMPORTE)
    # Registrado y sin confirmar no respalda: escribir «rutinario» no alcanza.
    niega(motor_migrate, e, e.adm, PREPARAR, "contrato_no_aplica", contrato=k)
    llamar(motor_migrate, e, e.mc1, CONFIRMAR_CONTRATO, contrato=k,
           huella_k=huella_contrato(motor_migrate, k))  # fmt: skip
    o = enviada(motor_migrate, e, contrato=k)
    assert (franja(motor_migrate, o), orden(motor_migrate, o)["estado"]) == (
        "rutinaria", "aprobada")  # fmt: skip
    assert versiones(motor_migrate, o)[0]["contrato_id"] == k
    # Sin declararlo no es rutinaria aunque el proveedor tenga el contrato (revisión de #169).
    assert franja(motor_migrate, enviada(motor_migrate, e)) == "sin_umbral"
    # Fuera de sus condiciones se niega: otro importe, otra moneda, otro proveedor.
    for fuera in ({"importe": IMPORTE + 1}, {"moneda": "USD"}, {"proveedor": e.otro}):
        niega(motor_migrate, e, e.adm, PREPARAR, "contrato_no_aplica", contrato=k, **fuera)


def test_un_proveedor_nuevo_con_contrato_igual_pide_aprobacion(motor_migrate, e):
    k = confirmado(motor_migrate, e)
    o = enviada(motor_migrate, e, contrato=k)
    assert (franja(motor_migrate, o), orden(motor_migrate, o)["estado"]) == (
        "beneficiario_nuevo", "en_revision")  # fmt: skip


def test_un_contrato_vencido_o_dado_de_baja_no_respalda(motor_migrate, e):
    conocido(motor_migrate, e)
    k = confirmado(motor_migrate, e)
    rutinaria = enviada(motor_migrate, e, contrato=k)
    assert franja(motor_migrate, rutinaria) == "rutinaria"
    llamar(motor_migrate, e, e.adm, BAJA_CONTRATO, contrato=k)
    niega(motor_migrate, e, e.adm, PREPARAR, "contrato_no_aplica", contrato=k)
    # La orden que ya era rutinaria no se emite con un contrato que ya no rige.
    niega(motor_migrate, e, e.adm, EMITIR, "contrato_no_vigente", orden=rutinaria, version=1)
    niega(motor_migrate, e, e.adm, BAJA_CONTRATO, "contrato_dado_de_baja", contrato=k)

    # Vencido: un contrato confirmado cuya vigencia terminó ayer (la base no deja registrarlo así:
    # se carga como dueño para simular el paso del tiempo).
    vencido = confirmado(motor_migrate, e)
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.contrato_proveedor disable trigger"
                       " historia_contrato_proveedor"))  # fmt: skip
        c.execute(
            text("update nucleo.contrato_proveedor set vigente_desde = vigente_desde - 30,"
                 " vigente_hasta = nucleo.hoy_del_consorcio(consorcio_id) - 1 where id = :k"),
            {"k": vencido},
        )  # fmt: skip
        c.execute(text("alter table nucleo.contrato_proveedor enable trigger"
                       " historia_contrato_proveedor"))  # fmt: skip
    niega(motor_migrate, e, e.adm, PREPARAR, "contrato_no_aplica", contrato=vencido)


def test_una_rutinaria_con_el_contrato_de_baja_se_renueva_sin_contrato(motor_migrate, e):
    conocido(motor_migrate, e)
    k = confirmado(motor_migrate, e)
    o = enviada(motor_migrate, e, contrato=k)
    assert orden(motor_migrate, o)["estado"] == "aprobada"
    # Control: con el contrato vigente y dentro de los 7 días, no se renueva.
    niega(motor_migrate, e, e.adm, RENOVAR, "aprobacion_vigente", orden=o, version=1)
    llamar(motor_migrate, e, e.adm, BAJA_CONTRATO, contrato=k)
    niega(motor_migrate, e, e.adm, EMITIR, "contrato_no_vigente", orden=o, version=1)
    # Con el contrato de baja sí, antes de los 7 días: la versión nueva va sin contrato, deja de
    # ser rutinaria y vuelve a revisión, sin las aprobaciones de antes.
    assert llamar(motor_migrate, e, e.adm, RENOVAR, orden=o, version=1) == 2
    nueva = versiones(motor_migrate, o)[1]
    assert (nueva["contrato_id"], nueva["franja"]) == (None, "sin_umbral")
    assert orden(motor_migrate, o)["estado"] == "en_revision"


def test_registrar_un_contrato_exige_su_documento_y_una_vigencia_valida(motor_migrate, e):
    hoy = _hoy(motor_migrate, e)
    base = {"proveedor": e.proveedor, "tipo": "contrato", "alcance": "SINTETICO", "tope": 100,
            "moneda": "ARS", "desde": hoy, "hasta": hoy, "decision": None}  # fmt: skip
    otro_doc = documento_de_contrato(motor_migrate, e, categoria="seguros")
    niega(motor_migrate, e, e.adm, REGISTRAR_CONTRATO, "documento_invalido",
          documento=otro_doc, **base)  # fmt: skip
    doc = documento_de_contrato(motor_migrate, e)
    pasado = {"desde": hoy - timedelta(days=10), "hasta": hoy - timedelta(days=1)}
    niega(motor_migrate, e, e.adm, REGISTRAR_CONTRATO, "fecha_invalida", documento=doc,
          **base | pasado)  # fmt: skip
    niega(motor_migrate, e, e.mc1, REGISTRAR_CONTRATO, "42501", documento=doc, **base)
    # 0052: un documento sin ninguna versión que pasó el antivirus no respalda nada.
    for version in (None, "en_cuarentena", "rechazado"):
        sin_archivo = documento_de_contrato(motor_migrate, e, version=version)
        niega(motor_migrate, e, e.adm, REGISTRAR_CONTRATO, "documento_invalido",
              documento=sin_archivo, **base)  # fmt: skip
    assert llamar(motor_migrate, e, e.adm, REGISTRAR_CONTRATO, documento=doc, **base)  # control


def test_confirmar_un_contrato_exige_otro_ser_humano_y_la_huella_vista(motor_migrate, motor_app,
                                                                       e):  # fmt: skip
    k = contrato(motor_migrate, e)
    h = huella_contrato(motor_migrate, k)
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)
    niega(motor_migrate, e, otra, CONFIRMAR_CONTRATO, "no_es_quien_registro", contrato=k,
          huella_k=h)  # fmt: skip
    niega(motor_migrate, e, e.adm, CONFIRMAR_CONTRATO, "42501", contrato=k, huella_k=h)
    niega(motor_migrate, e, e.mc1, CONFIRMAR_CONTRATO, "huella_distinta", contrato=k,
          huella_k=bytes(32))  # fmt: skip
    llamar(motor_migrate, e, e.mc1, CONFIRMAR_CONTRATO, contrato=k, huella_k=h)  # control
    niega(motor_migrate, e, e.mc2, CONFIRMAR_CONTRATO, "contrato_ya_confirmado", contrato=k,
          huella_k=h)  # fmt: skip


# Umbral --------------------------------------------------------------------------------------


def test_el_umbral_lo_registra_soporte_con_un_acta(motor_migrate, motor_app, e):
    def fijar(decision, origen="soporte", desde="hoy"):
        with transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c:
            c.execute(
                text("select nucleo.fijar_umbral_orden_pago(100, 'ARS',"
                     " case when :d = 'hoy' then nucleo.hoy_del_consorcio(:c)"
                     " else nucleo.hoy_del_consorcio(:c) - 1 end, :a, null, :o)"),
                {"d": desde, "c": e.cons, "a": decision, "o": origen},
            )  # fmt: skip

    def motivo(**kw) -> str:
        with pytest.raises(DBAPIError) as error:
            fijar(**kw)
        return error.value.orig.diag.message_hint

    acta = decidir_equipo(motor_app, e.cons, "consejo")
    otra = decidir_equipo(motor_app, e.cons, "contrato_administracion")
    assert motivo(decision=otra) == "decision_no_es_acta"
    assert motivo(decision=acta, origen="api") == "origen_invalido"
    assert motivo(decision=acta, desde="ayer") == "fecha_invalida"
    fijar(decision=acta)  # control
    assert motivo(decision=acta) == "vigencia_repetida"


def test_un_umbral_nuevo_no_cambia_la_orden_ya_preparada(motor_migrate, motor_app, e):
    conocido(motor_migrate, e)
    # Un umbral que regía desde antes (cargado como dueño: la función no admite el pasado).
    acta = decidir_equipo(motor_app, e.cons)
    with motor_migrate.begin() as c:
        viejo = c.execute(
            text("insert into nucleo.umbral_orden_pago (consorcio_id, importe_centavos, moneda,"
                 " vigente_desde, decision_id) values (:c, :i, 'ARS',"
                 " nucleo.hoy_del_consorcio(:c) - 10, :a) returning id"),
            {"c": e.cons, "i": IMPORTE * 10, "a": acta},
        ).scalar_one()  # fmt: skip
    antes = llamar(motor_migrate, e, e.adm, PREPARAR)
    nuevo = umbral(motor_app, e.cons, IMPORTE - 1)
    despues = llamar(motor_migrate, e, e.adm, PREPARAR)
    v_antes, v_despues = versiones(motor_migrate, antes)[0], versiones(motor_migrate, despues)[0]
    assert (v_antes["umbral_id"], v_antes["franja"]) == (viejo, "bajo_umbral")
    assert (v_despues["umbral_id"], v_despues["franja"]) == (nuevo, "sobre_umbral")
    # Enviarla después no la vuelve a evaluar: rige lo que guardó al prepararse.
    llamar(motor_migrate, e, e.adm, ENVIAR, orden=antes, version=1)
    assert orden(motor_migrate, antes)["estado"] == "aprobada"


def test_modificar_crea_una_version_y_la_vuelve_a_evaluar(motor_migrate, motor_app, e):
    conocido(motor_migrate, e)
    umbral(motor_app, e.cons, IMPORTE)
    o = aprobada(motor_migrate, e)
    assert franja(motor_migrate, o) == "bajo_umbral"
    llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1, importe=IMPORTE + 1)
    o_ = orden(motor_migrate, o)
    assert (o_["version_vigente"], o_["estado"], o_["aprobada_en"]) == (2, "en_revision", None)
    assert [v["franja"] for v in versiones(motor_migrate, o)] == ["bajo_umbral", "sobre_umbral"]
    assert huella(motor_migrate, o, 1) != huella(motor_migrate, o, 2)


# Fraccionamiento -----------------------------------------------------------------------------


def test_fraccionar_bajo_el_umbral_genera_una_alerta_sin_bloquear(motor_migrate, motor_app, e):
    conocido(motor_migrate, e)
    umbral(motor_app, e.cons, IMPORTE * 3)
    alertas = []
    for _ in range(3):
        o = enviada(motor_migrate, e)
        assert orden(motor_migrate, o)["estado"] == "aprobada"  # no se bloquea
        alertas.append(versiones(motor_migrate, o)[0]["alerta_fraccionamiento"])
    # La anterior aprobada (conocido) más estas: 2, 3 y 4 veces el importe contra 3 veces.
    assert alertas == [False, False, True]
    # Control: otro proveedor no suma.
    aprobada(motor_migrate, e, proveedor=e.otro)
    o = enviada(motor_migrate, e, proveedor=e.otro)
    assert versiones(motor_migrate, o)[0]["alerta_fraccionamiento"] is False


def test_lo_rutinario_del_mismo_contrato_en_el_mes_alerta_al_pasar_el_maximo(motor_migrate, e):
    """Revisión de #169: sin la alerta, varias órdenes rutinarias justo bajo el máximo pasarían
    sin que el Consejo lo note."""
    conocido(motor_migrate, e)
    k = confirmado(motor_migrate, e, tope=IMPORTE)
    alertas = []
    for _ in range(2):
        o = enviada(motor_migrate, e, contrato=k)
        assert orden(motor_migrate, o)["estado"] == "aprobada"  # no se bloquea
        alertas.append(versiones(motor_migrate, o)[0]["alerta_fraccionamiento"])
    assert alertas == [False, True]


def test_la_alerta_mira_la_ventana_de_fraccionamiento_no_el_mes(motor_migrate, motor_app, e):
    """0052: la ventana es un parámetro técnico (`ventana_fraccionamiento`, 30 días). Con una
    ventana de cero, las órdenes anteriores no suman y no hay alerta; nunca se bloquea."""
    conocido(motor_migrate, e)  # una orden previa del mismo proveedor, aprobada
    umbral(motor_app, e.cons, IMPORTE + 1)  # cada orden queda bajo el umbral; dos lo superan
    with motor_migrate.connect() as c:
        dias = c.execute(text("select extract(day from nucleo.ventana_fraccionamiento())")).scalar()
    assert dias == 30
    with motor_migrate.begin() as c:
        c.execute(text("create or replace function nucleo.ventana_fraccionamiento()"
                       " returns interval language sql stable"
                       " set search_path = pg_catalog, pg_temp"
                       " as $$ select interval '0' $$"))  # fmt: skip
    try:
        o = enviada(motor_migrate, e)
        assert orden(motor_migrate, o)["estado"] == "aprobada"
        assert versiones(motor_migrate, o)[0]["alerta_fraccionamiento"] is False
    finally:
        with motor_migrate.begin() as c:
            c.execute(text("create or replace function nucleo.ventana_fraccionamiento()"
                           " returns interval language sql stable"
                           " set search_path = pg_catalog, pg_temp"
                           " as $$ select interval '30 days' $$"))  # fmt: skip
    # Control: con la ventana de 30 días, las previas suman y la misma orden sí alerta.
    o = enviada(motor_migrate, e)
    assert versiones(motor_migrate, o)[0]["alerta_fraccionamiento"] is True


def _preparada_hace(motor_migrate, o, delta: timedelta) -> None:
    """Corre hacia atrás el momento en que se preparó la versión de la orden (como dueño)."""
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.orden_pago_version disable trigger"
                       " historia_orden_pago_version"))  # fmt: skip
        c.execute(
            text("update nucleo.orden_pago_version set creada_en = now() - :d where orden_id = :o"),
            {"d": delta, "o": o},
        )
        c.execute(text("alter table nucleo.orden_pago_version enable trigger"
                       " historia_orden_pago_version"))  # fmt: skip


def test_la_ventana_suma_29_dias_atras_y_no_31(motor_migrate, motor_app, e):
    """Revisión de #175: el borde de la ventana con órdenes reales, también cruzando de mes."""
    previa = conocido(motor_migrate, e)
    umbral(motor_app, e.cons, IMPORTE + 1)  # sola, cada orden queda bajo el umbral
    _preparada_hace(motor_migrate, previa, timedelta(days=31))
    o = enviada(motor_migrate, e)
    assert versiones(motor_migrate, o)[0]["alerta_fraccionamiento"] is False
    _preparada_hace(motor_migrate, o, timedelta(days=31))  # que esta tampoco sume
    _preparada_hace(motor_migrate, previa, timedelta(days=29))
    o = enviada(motor_migrate, e)
    assert versiones(motor_migrate, o)[0]["alerta_fraccionamiento"] is True


# Vigencia de la aprobación y emisión ---------------------------------------------------------


def test_la_vigencia_es_la_del_contrato(motor_migrate):
    from cem_contracts.egresos import VIGENCIA_APROBACION

    with motor_migrate.connect() as c:
        texto = c.execute(
            text("select prosrc from pg_proc where proname = 'exigir_orden_emitible'")
        ).scalar_one()
    assert f"make_interval(days => {VIGENCIA_APROBACION.days})" in texto
    assert VIGENCIA_APROBACION == timedelta(days=7)


def test_una_aprobacion_vencida_no_se_emite_y_se_renueva(motor_migrate, e):
    o = aprobada(motor_migrate, e)
    envejecer(motor_migrate, o, timedelta(days=7, minutes=1))
    niega(motor_migrate, e, e.adm, EMITIR, "aprobacion_vencida", orden=o, version=1)
    assert llamar(motor_migrate, e, e.adm, RENOVAR, orden=o, version=1) == 2
    o_ = orden(motor_migrate, o)
    assert (o_["estado"], o_["version_vigente"], o_["aprobada_en"]) == ("en_revision", 2, None)
    # Las aprobaciones viejas no pasan a la versión nueva: hay que aprobar de nuevo.
    niega(motor_migrate, e, e.adm, EMITIR, "estado_invalido", orden=o, version=2)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=2, huella=huella(motor_migrate, o, 2))
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=2)  # control
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"


def test_una_aprobacion_dentro_del_plazo_se_emite_y_no_se_renueva(motor_migrate, e):
    o = aprobada(motor_migrate, e)
    envejecer(motor_migrate, o, timedelta(days=7) - timedelta(hours=1))
    niega(motor_migrate, e, e.adm, RENOVAR, "aprobacion_vigente", orden=o, version=1)
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=1)
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"


def test_una_rutinaria_la_emite_quien_la_preparo_sin_aprobadores(motor_migrate, e):
    conocido(motor_migrate, e)
    o = enviada(motor_migrate, e, contrato=confirmado(motor_migrate, e))
    assert franja(motor_migrate, o) == "rutinaria"
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=1)
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"


def test_sin_cuenta_destino_no_se_emite(motor_migrate, e):
    sin_cuenta = nuevo_proveedor(motor_migrate, e.cons, e.adm, razon="Sin cuenta SINTETICO")
    o = aprobada(motor_migrate, e, proveedor=sin_cuenta)
    niega(motor_migrate, e, e.adm, EMITIR, "proveedor_sin_cuenta", orden=o, version=1)
    llamar(motor_migrate, e, e.adm, EMITIR, orden=aprobada(motor_migrate, e), version=1)  # control


def test_las_aprobaciones_insuficientes_no_se_emiten_aunque_la_orden_diga_aprobada(
    motor_migrate, motor_app, e
):
    """Mutación de la invariante: la orden en Aprobada sin las aprobaciones que pide su franja
    (forzada como dueño) no se emite."""
    exigir(motor_app, e.cons, 2)
    o = enviada(motor_migrate, e)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=huella(motor_migrate, o, 1))
    assert orden(motor_migrate, o)["estado"] == "en_revision"
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.orden_pago set estado = 'aprobada', aprobada_en = now()"
                       " where id = :o"), {"o": o})  # fmt: skip
    niega(motor_migrate, e, e.adm, EMITIR, "aprobaciones_insuficientes", orden=o, version=1)


# Decisión 6A y separación --------------------------------------------------------------------


def test_6a_administrador_y_consejero_aprueba_lo_ajeno_y_nunca_lo_propio(motor_migrate, motor_app,
                                                                         e):  # fmt: skip
    """La misma persona tiene los dos mandatos: recibe las facultades de cada uno, pero no
    aprueba una orden que preparó."""
    with motor_migrate.connect() as c:
        acta = c.execute(
            text("select decision_id from nucleo.mandato where consorcio_id = :c"
                 " and persona_id = :p"), {"c": e.cons, "p": e.adm},
        ).scalar_one()  # fmt: skip
    alta(motor_app, e.cons, e.prop, acta, tipo="miembro_consejo", persona=e.adm)
    propia = enviada(motor_migrate, e)
    niega(motor_migrate, e, e.adm, APROBAR, "no_es_quien_preparo", orden=propia, version=1,
          huella=huella(motor_migrate, propia, 1))  # fmt: skip
    # La que preparó otro (mc2, con la facultad de preparar), a un proveedor cuya cuenta no cargó
    # el administrador, sí la aprueba.
    with motor_migrate.connect() as c:
        m = c.execute(
            text("select id, decision_id from nucleo.mandato where consorcio_id = :c"
                 " and persona_id = :p"), {"c": e.cons, "p": e.mc2},
        ).one()  # fmt: skip
    otorgar(motor_migrate, e.cons, m.id, m.decision_id, "egresos.preparar_orden")
    ajeno = nuevo_proveedor(motor_migrate, e.cons, e.mc2, cuenta=cbu_sintetico(91))
    ajena = llamar(motor_migrate, e, e.mc2, PREPARAR, proveedor=ajeno)
    llamar(motor_migrate, e, e.mc2, ENVIAR, orden=ajena, version=1)
    llamar(motor_migrate, e, e.adm, APROBAR, orden=ajena, version=1,
           huella=huella(motor_migrate, ajena, 1))  # fmt: skip
    assert orden(motor_migrate, ajena)["estado"] == "aprobada"
    # Y quien aprobó no la emite (A5), aunque tenga `egresos.ejecutar_pago` por su mandato.
    niega(motor_migrate, e, e.adm, EMITIR, "no_es_quien_aprobo", orden=ajena, version=1)


def test_la_misma_identidad_en_dos_cuentas_no_suma_dos_aprobaciones(motor_migrate, motor_app, e):
    exigir(motor_app, e.cons, 2)
    o = enviada(motor_migrate, e)
    h = huella(motor_migrate, o, 1)
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.mc1)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=h)
    niega(motor_migrate, e, otra, APROBAR, "aprobacion_repetida", orden=o, version=1, huella=h)
    assert orden(motor_migrate, o)["estado"] == "en_revision"
    llamar(motor_migrate, e, e.mc2, APROBAR, orden=o, version=1, huella=h)  # control
    assert orden(motor_migrate, o)["estado"] == "aprobada"


# Rutas -----------------------------------------------------------------------------------------


def test_rutas_de_contratos_franja_y_emision(motor_migrate, e):
    conocido(motor_migrate, e)
    hoy = _hoy(motor_migrate, e)
    cuerpo = {"proveedor_id": str(e.proveedor), "tipo": "abono", "alcance": "SINTETICO: limpieza",
              "importe_maximo_centavos": IMPORTE, "moneda": "ARS",
              "vigente_desde": hoy.isoformat(),
              "vigente_hasta": (hoy + timedelta(days=30)).isoformat(),
              "documento_id": str(documento_de_contrato(motor_migrate, e))}  # fmt: skip
    r = post(e, e.adm, "contratos", cuerpo)
    assert r.status_code == 201, r.json()
    k = r.json()["id"]
    (visto,) = get(e, e.mc1, "contratos/revision").json()["elementos"]
    assert (visto["id"], visto["estado"]) == (k, "pendiente_confirmacion")
    r = post(e, e.adm, f"contratos/{k}/confirmacion", {"huella": visto["huella"]})
    assert codigo(r) == (403, "sin_permiso")
    r = post(e, e.mc1, f"contratos/{k}/confirmacion", {"huella": visto["huella"]})
    assert r.status_code == 201, r.json()
    assert get(e, e.adm, "contratos").json()["elementos"][0]["estado"] == "vigente"

    r = post(e, e.adm, "ordenes-pago", {"importe_centavos": IMPORTE, "moneda": "ARS",
             "concepto": "SINTETICO: limpieza de octubre", "proveedor_id": str(e.proveedor),
             "usa_fondo_reserva": False, "contrato_id": k})  # fmt: skip
    o = r.json()["id"]
    assert post(e, e.adm, f"ordenes-pago/{o}/enviar-a-revision",
                {"version_esperada": 1}).status_code == 200  # fmt: skip
    detalle = get(e, e.adm, f"ordenes-pago/{o}").json()
    assert (detalle["orden"]["estado"], detalle["orden"]["franja"]) == ("aprobada", "rutinaria")
    assert detalle["orden"]["aprobacion_vence_en"] is not None
    assert detalle["versiones"][0]["contrato_id"] == k

    emision = {"version": 1, "huella": detalle["versiones"][0]["huella"]}
    assert codigo(post(e, e.adm, f"ordenes-pago/{o}/emitir", emision | {"huella": "0" * 64})) == (
        409, "huella_distinta")  # fmt: skip
    clave = clave_idempotencia()
    primera = post(e, e.adm, f"ordenes-pago/{o}/emitir", emision, clave)
    repetida = post(e, e.adm, f"ordenes-pago/{o}/emitir", emision, clave)
    assert primera.status_code == repetida.status_code == 200, primera.json()
    assert repetida.headers.get("Idempotent-Replayed") == "true"
    otra = emision | {"version": 2}
    assert codigo(post(e, e.adm, f"ordenes-pago/{o}/emitir", otra, clave)) == (
        422, "idempotencia_reutilizada")  # fmt: skip
    assert get(e, e.adm, f"ordenes-pago/{o}").json()["orden"]["estado"] == "en_ejecucion"
    # Con otra clave, la orden ya emitida no se vuelve a emitir.
    assert codigo(post(e, e.adm, f"ordenes-pago/{o}/emitir", emision)) == (
        409, "orden_no_admite")  # fmt: skip

    # Si soporte borra el documento de respaldo, la lectura informa lo mismo que la verificación
    # al escribir (`contrato_vigente`): el contrato ya no respalda nada.
    with motor_migrate.begin() as c:
        mandato = c.execute(text("select id from nucleo.mandato where consorcio_id = :c"
                                 " and persona_id = :p limit 1"), {"c": e.cons, "p": e.adm},
                            ).scalar_one()  # fmt: skip
        c.execute(text("update nucleo.documento set estado = 'borrado', borrado_en = now(),"
                       " borrado_motivo = 'SINTETICO', borrado_referencia = 'prueba',"
                       " borrado_solicitado_por = :p, borrado_mandato = :m,"
                       " borrado_fecha_pedido = current_date where id = :d"),
                  {"p": e.adm, "m": mandato, "d": cuerpo["documento_id"]})  # fmt: skip
    (k_,) = get(e, e.adm, "contratos").json()["elementos"]
    assert (k_["id"], k_["estado"]) == (k, "documento_no_vigente")


def test_ruta_de_renovar_y_emitir_vencida(motor_migrate, e):
    o = aprobada(motor_migrate, e)
    envejecer(motor_migrate, o, timedelta(days=8))
    emision = {"version": 1, "huella": huella(motor_migrate, o, 1).hex()}
    assert codigo(post(e, e.adm, f"ordenes-pago/{o}/emitir", emision)) == (
        409, "aprobacion_vencida")  # fmt: skip
    r = post(e, e.adm, f"ordenes-pago/{o}/renovar", {"version_esperada": 1})
    assert (r.status_code, r.json()["version"]) == (200, 2)
    assert codigo(post(e, e.adm, f"ordenes-pago/{o}/renovar", {"version_esperada": 2})) == (
        409, "orden_no_admite")  # fmt: skip


def test_quien_aprueba_no_emite_por_la_ruta(motor_migrate, e):
    o = aprobada(motor_migrate, e)
    emision = {"version": 1, "huella": huella(motor_migrate, o, 1).hex()}
    assert codigo(post(e, e.mc1, f"ordenes-pago/{o}/emitir", emision)) == (403, "sin_permiso")
    assert post(e, e.adm, f"ordenes-pago/{o}/emitir", emision).status_code == 200


# Ida y vuelta --------------------------------------------------------------------------------


def _tablas(motor) -> int:
    with motor.connect() as c:
        return c.execute(
            text("select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace"
                 " where n.nspname = 'nucleo' and c.relname in ('umbral_orden_pago',"
                 " 'contrato_proveedor', 'contrato_proveedor_confirmacion',"
                 " 'contrato_proveedor_baja')")
        ).scalar_one()  # fmt: skip


def test_0050_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0049")
    assert _tablas(motor) == 0
    command.upgrade(cfg, "0050")
    assert _tablas(motor) == 4
    command.downgrade(cfg, "0049")
    assert _tablas(motor) == 0
    with motor.connect() as c:
        restos = c.execute(
            text("select count(*) from pg_proc where proname in ('evaluar_orden_pago',"
                 " 'aprobar_por_franja', 'fijar_umbral_orden_pago', 'renovar_orden_vencida')")
        ).scalar_one()  # fmt: skip
        emitir = c.execute(
            text("select has_function_privilege('cem_app', 'nucleo.emitir_orden_pago(uuid,"
                 " integer, uuid, text, uuid)', 'EXECUTE')")
        ).scalar_one()  # fmt: skip
    assert (restos, emitir) == (0, False)
    command.upgrade(cfg, "0050")  # control: vuelve a subir limpio
    assert _tablas(motor) == 4


def test_0050_no_sube_con_ordenes(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0049")
    cons, _ = _consorcio_y_unidad(motor)
    quien = _persona(motor, "Sintetica")
    with motor.begin() as c:
        o = c.execute(
            text("insert into nucleo.orden_pago (consorcio_id, version_vigente, preparada_por)"
                 " values (:c, 1, :p) returning id"), {"c": cons, "p": quien},
        ).scalar_one()  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.upgrade(cfg, "0050")
    assert error.value.orig.diag.message_hint == "upgrade_con_datos"
    with motor.begin() as c:
        c.execute(text("delete from nucleo.orden_pago where id = :o"), {"o": o})
    command.upgrade(cfg, "0050")  # control: sin la orden, sube
    assert _tablas(motor) == 4


FUNCIONES_0052 = (
    "nucleo.evaluar_orden_pago(uuid, uuid, nucleo.proveedor_version, bigint, text, boolean, uuid)",
    "nucleo.contrato_vigente(uuid, uuid, date)",
    "nucleo.registrar_contrato_proveedor(uuid, text, text, bigint, text, date, date, uuid, uuid,"
    " uuid, text, uuid)",
    "nucleo.contratos_proveedor_lectura(uuid, text, uuid, text, uuid)",
)


def _definiciones(motor) -> dict[str, str]:
    with motor.connect() as c:
        return {
            f: c.execute(
                text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": f}
            ).scalar_one()
            for f in FUNCIONES_0052
        }


def _funciones_nuevas(motor) -> int:
    with motor.connect() as c:
        return c.execute(
            text("select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                 " where n.nspname = 'nucleo' and p.proname in ('ventana_fraccionamiento',"
                 " 'documento_respalda')")
        ).scalar_one()  # fmt: skip


def test_0052_ida_y_vuelta(base_aparte):  # noqa: F811
    """Sube cambiando las cuatro funciones y baja dejándolas idénticas a las de 0051."""
    cfg, motor = base_aparte
    command.upgrade(cfg, "0051")
    antes = _definiciones(motor)
    assert _funciones_nuevas(motor) == 0
    command.upgrade(cfg, "0052")
    despues = _definiciones(motor)
    assert _funciones_nuevas(motor) == 2
    for f in FUNCIONES_0052:
        assert despues[f] != antes[f], f  # control: la subida cambió cada una
    assert "ventana_fraccionamiento()" in despues[FUNCIONES_0052[0]]
    assert all("documento_respalda(" in despues[f] for f in FUNCIONES_0052[1:])
    command.downgrade(cfg, "0051")
    assert _definiciones(motor) == antes
    assert _funciones_nuevas(motor) == 0
    command.upgrade(cfg, "0052")  # control: vuelve a subir limpio
    assert _definiciones(motor) == despues


def test_un_contrato_anterior_a_0052_sin_version_analizada_no_respalda(motor_migrate, e):
    """Un contrato cargado antes de 0052 pudo citar un documento sin ninguna versión `disponible`.
    Desde 0052 no rige ni se lee como vigente. Se lo simula como dueño, cambiando el documento de
    un contrato confirmado por uno en cuarentena."""
    conocido(motor_migrate, e)
    k = confirmado(motor_migrate, e)
    rutinaria = enviada(motor_migrate, e, contrato=k)
    assert franja(motor_migrate, rutinaria) == "rutinaria"  # control: con respaldo, rige
    assert get(e, e.adm, "contratos").json()["elementos"][0]["estado"] == "vigente"
    en_cuarentena = documento_de_contrato(motor_migrate, e, version="en_cuarentena")
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.contrato_proveedor disable trigger"
                       " historia_contrato_proveedor"))  # fmt: skip
        c.execute(
            text("update nucleo.contrato_proveedor set documento_id = :d where id = :k"),
            {"d": en_cuarentena, "k": k},
        )
        c.execute(text("alter table nucleo.contrato_proveedor enable trigger"
                       " historia_contrato_proveedor"))  # fmt: skip
    niega(motor_migrate, e, e.adm, PREPARAR, "contrato_no_aplica", contrato=k)
    niega(motor_migrate, e, e.adm, EMITIR, "contrato_no_vigente", orden=rutinaria, version=1)
    assert get(e, e.adm, "contratos").json()["elementos"][0]["estado"] == "documento_no_vigente"
