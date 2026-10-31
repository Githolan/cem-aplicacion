"""H3: el proveedor versionado, su cuenta destino y el beneficiario de la orden (`0048`). Datos
SINTETICOS / PRUEBA / NO REAL: los CBU son del banco 999, que no existe, con verificadores
válidos generados por la prueba.

Diseño: `doc/diseno/H3_Proveedor_Beneficiario_Cuenta.md` del repositorio CEM.
"""

from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_orden_pago_neutra import (  # noqa: F401  (`e` es un fixture)
    APROBAR,
    EMITIR,
    ENVIAR,
    MODIFICAR,
    PREPARAR,
    RAZON_SOCIAL,
    RECHAZAR,
    _aprobada,
    cbu_sintetico,
    e,
    escena_economica,
    eventos,
    huella,
    llamar,
    niega,
    nuevo_proveedor,
    orden,
    otorgar,
    otra_cuenta_en_el_consejo,
    preparada,
    versiones,
)
from tests.test_vinculo_0014 import (  # noqa: F401  (`base_aparte` es un fixture)
    _consorcio_y_unidad,
    _persona,
    base_aparte,
)

pytestmark = pytest.mark.db

CUIT = "30000000007"  # verificador válido, sintético
ALTA = (
    "select nucleo.alta_proveedor(:cuit, :razon, :rubro, :contacto, :documento, :cuenta, :actor,"
    " :origen, :sesion)"
)
VERSION = (
    "select nucleo.nueva_version_proveedor(:proveedor, :version, :razon, :rubro, :contacto,"
    " cast(:estado as nucleo.estado_proveedor), :documento, :cuenta, :actor, :origen, :sesion)"
)
DATOS = {
    "cuit": CUIT,
    "razon": "Plomería SINTETICA SA (NO REAL)",
    "rubro": "Plomería",
    "contacto": "contacto@cem-prueba.invalid",
    "documento": None,
    "cuenta": cbu_sintetico(7),
    "estado": "activo",
}


def alta(motor, e, actor=None, **cambios) -> UUID:  # noqa: F811
    return llamar(motor, e, actor or e.adm, ALTA, **{**DATOS, **cambios})


def proveedor_versiones(motor_migrate, p) -> list[dict]:
    with motor_migrate.connect() as c:
        return [
            dict(f)
            for f in c.execute(
                text("select * from nucleo.proveedor_version where proveedor_id = :p"
                     " order by numero"), {"p": p},
            ).mappings()
        ]  # fmt: skip


def eventos_de(motor_migrate, p) -> list[dict]:
    with motor_migrate.connect() as c:
        return [
            dict(f)
            for f in c.execute(
                text("select accion, actor_persona_id, despues, e::text as todo"
                     " from auditoria.eventos e where entidad = 'proveedor'"
                     " and entidad_id = :p order by secuencia"), {"p": p},
            ).mappings()
        ]  # fmt: skip


# CBU -----------------------------------------------------------------------------------------


def test_cbu_valido_comprueba_los_dos_verificadores(motor_migrate):
    bueno = cbu_sintetico(12345)
    malos = {
        "primer verificador": bueno[:7] + str((int(bueno[7]) + 1) % 10) + bueno[8:],
        "segundo verificador": bueno[:21] + str((int(bueno[21]) + 1) % 10),
        "21 dígitos": bueno[:21],
        "letras": "ABC" + bueno[3:],
        "alias": "PROVEEDOR.SINTETICO.CEM",
        "entidad y sucursal en cero": "0" * 22,  # los dos verificadores dan 0
    }
    with motor_migrate.connect() as c:

        def valido(x):
            return c.execute(text("select nucleo.cbu_valido(:x)"), {"x": x}).scalar_one()

        assert valido(bueno) is True  # control
        for caso, x in malos.items():
            assert valido(x) is False, caso


# Alta y versiones ----------------------------------------------------------------------------


def test_el_alta_crea_la_version_1_sin_el_tercero_en_el_evento(motor_migrate, e):  # noqa: F811
    p = alta(motor_migrate, e)
    [v] = proveedor_versiones(motor_migrate, p)
    assert (v["numero"], v["estado"], v["cuenta_destino"], v["razon_social"]) == (
        1, "activo", DATOS["cuenta"], DATOS["razon"],
    )  # fmt: skip
    # Desde 0052 la huella lleva una sal al azar: con todo lo demás y los últimos dígitos no se
    # puede probar candidatos de cuenta contra ella (revisión de seguridad de #175).
    with motor_migrate.connect() as c:
        sin_sal = c.execute(
            text("select sha256(convert_to(jsonb_build_object('proveedor', cast(:p as uuid),"
                 " 'version', 1, 'cuit', cast(:cuit as text), 'razon_social', cast(:r as text),"
                 " 'cuenta_destino', cast(:cta as text))::text, 'UTF8'))"),
            {"p": p, "cuit": CUIT, "r": DATOS["razon"], "cta": DATOS["cuenta"]},
        ).scalar_one()  # fmt: skip
        dos = c.execute(
            text("select nucleo.huella_proveedor(:p, 1, :cuit, :r, :cta)"
                 " from generate_series(1, 2)"),
            {"p": p, "cuit": CUIT, "r": DATOS["razon"], "cta": DATOS["cuenta"]},
        ).scalars().all()  # fmt: skip
    assert len(v["huella"]) == 32 and v["huella"] != sin_sal
    assert dos[0] != dos[1] and sin_sal not in dos
    [ev] = eventos_de(motor_migrate, p)
    assert (ev["accion"], ev["actor_persona_id"]) == ("consorcio.proveedor.alta", e.adm)
    assert ev["despues"]["con_cuenta"] is True and ev["despues"]["huella"] == v["huella"].hex()
    for dato in (DATOS["cuenta"], DATOS["razon"], DATOS["contacto"]):
        assert dato not in ev["todo"]


@pytest.mark.parametrize(
    ("cambio", "motivo"),
    [
        ({"cuit": "30000000008"}, "cuit_invalido"),
        ({"cuenta": cbu_sintetico(7)[:21] + "0"}, "cuenta_invalida"),
        ({"razon": "   "}, "razon_social_invalida"),
        ({"documento": uuid4()}, "documento_invalido"),
        ({"origen": "soporte"}, "origen_invalido"),
    ],
)
def test_el_alta_se_niega_sin_lo_que_exige(motor_migrate, e, cambio, motivo):  # noqa: F811
    niega(motor_migrate, e, e.adm, ALTA, motivo, **{**DATOS, **cambio})
    assert alta(motor_migrate, e) is not None  # control: con todo en orden, carga


def test_el_cuit_no_se_repite_en_el_consorcio(motor_migrate, e):  # noqa: F811
    alta(motor_migrate, e)
    niega(motor_migrate, e, e.adm, ALTA, "cuit_repetido", **DATOS)
    assert alta(motor_migrate, e, cuit=None) is not None  # control: sin CUIT no choca


def test_solo_quien_prepara_da_de_alta(motor_migrate, motor_app):
    esc = escena_economica(motor_migrate, motor_app)
    niega(motor_migrate, esc, esc.mc1, ALTA, "42501", **DATOS)  # el Consejo no prepara
    sin_modo = escena_economica(motor_migrate, motor_app, con_modo=False)
    niega(motor_migrate, sin_modo, sin_modo.adm, ALTA, "modo_no_admitido", **DATOS)


def test_una_version_nueva_cambia_la_cuenta_con_historia(motor_migrate, e):  # noqa: F811
    p = alta(motor_migrate, e)
    nueva = cbu_sintetico(8)
    base = {**DATOS, "proveedor": p}
    niega(motor_migrate, e, e.adm, VERSION, "sin_cambios", **base, version=1)
    niega(motor_migrate, e, e.adm, VERSION, "version_desactualizada",
          **{**base, "version": 2, "cuenta": nueva})  # fmt: skip
    assert llamar(motor_migrate, e, e.adm, VERSION, **{**base, "version": 1, "cuenta": nueva}) == 2
    v1, v2 = proveedor_versiones(motor_migrate, p)
    assert (v1["cuenta_destino"], v2["cuenta_destino"]) == (DATOS["cuenta"], nueva)
    assert v1["huella"] != v2["huella"]
    ev = eventos_de(motor_migrate, p)[-1]
    assert ev["accion"] == "consorcio.proveedor.modificar" and ev["despues"]["cambio_cuenta"]
    assert nueva not in ev["todo"] and DATOS["cuenta"] not in ev["todo"]


# El CASCADE arrastra la versión de la orden y sus aprobaciones (FK): sus triggers de TRUNCATE se
# apagan para que el que responda sea el del proveedor (revisión de app #165).
SIN_TRUNCATE_DE_ORDENES = (
    "alter table nucleo.orden_pago_version disable trigger historia_orden_pago_version_truncate;"
    " alter table nucleo.orden_pago_aprobacion disable trigger"
    " historia_orden_pago_aprobacion_truncate;"
)


def test_el_proveedor_y_sus_versiones_solo_se_agregan(motor_migrate, e):  # noqa: F811
    p = alta(motor_migrate, e)
    for sentencia in (
        "update nucleo.proveedor set cuit = null where id = :p",
        "delete from nucleo.proveedor where id = :p",
        "update nucleo.proveedor_version set cuenta_destino = null where proveedor_id = :p",
        "delete from nucleo.proveedor_version where proveedor_id = :p",
        # Cada trigger de TRUNCATE solo (como P3 68): se apagan los de las tablas que el
        # CASCADE arrastra; el error deshace también esos ALTER.
        SIN_TRUNCATE_DE_ORDENES + " truncate nucleo.proveedor_version cascade",
        SIN_TRUNCATE_DE_ORDENES + " alter table nucleo.proveedor_version disable trigger"
        " historia_proveedor_version_truncate;"
        " truncate nucleo.proveedor cascade",
    ):
        with pytest.raises(DBAPIError) as error:
            with motor_migrate.begin() as c:
                c.execute(text(sentencia), {"p": p})
        assert error.value.orig.diag.message_hint == "historia_inmutable", sentencia
    assert len(proveedor_versiones(motor_migrate, p)) == 1


# El proveedor en la orden --------------------------------------------------------------------


def _nueva_cuenta(motor_migrate, e, p, version, cuenta=None, estado="activo"):  # noqa: F811
    return llamar(motor_migrate, e, e.adm, VERSION, **{**DATOS, "proveedor": p,
                  "version": version, "cuenta": cuenta or cbu_sintetico(90 + version),
                  "estado": estado})  # fmt: skip


def test_la_orden_guarda_la_version_del_proveedor_y_su_condicion(motor_migrate, e):  # noqa: F811
    """`nuevo` sin órdenes aprobadas antes; `conocido` con una aprobada con la misma cuenta;
    `cuenta_cambiada` con una aprobada con otra cuenta."""
    primera = _aprobada(motor_migrate, e)
    [v] = versiones(motor_migrate, primera)
    assert (v["proveedor_id"], v["proveedor_version"], v["condicion_beneficiario"]) == (
        e.proveedor, 1, "nuevo",
    )  # fmt: skip
    segunda = preparada(motor_migrate, e)
    assert versiones(motor_migrate, segunda)[0]["condicion_beneficiario"] == "conocido"
    _nueva_cuenta(motor_migrate, e, e.proveedor, 1)
    tercera = preparada(motor_migrate, e)
    assert versiones(motor_migrate, tercera)[0]["condicion_beneficiario"] == "cuenta_cambiada"
    # Control: otro proveedor, sin órdenes aprobadas, es nuevo.
    cuarta = llamar(motor_migrate, e, e.adm, PREPARAR, proveedor=e.otro)
    assert versiones(motor_migrate, cuarta)[0]["condicion_beneficiario"] == "nuevo"


def test_cambiar_solo_la_razon_social_sigue_conocido(motor_migrate, e):  # noqa: F811
    """La condición mira la cuenta, no el número de versión."""
    _aprobada(motor_migrate, e)
    llamar(motor_migrate, e, e.adm, VERSION, **{**DATOS, "proveedor": e.proveedor, "version": 1,
           "razon": "Otra razón SINTETICA", "cuenta": cbu_sintetico(1), "cuit": None})  # fmt: skip
    o = preparada(motor_migrate, e)
    [v] = versiones(motor_migrate, o)
    assert (v["proveedor_version"], v["condicion_beneficiario"]) == (2, "conocido")


def test_un_borrador_no_hace_conocido_al_proveedor(motor_migrate, e):  # noqa: F811
    preparada(motor_migrate, e, en_revision=True)
    otra = preparada(motor_migrate, e)
    assert versiones(motor_migrate, otra)[0]["condicion_beneficiario"] == "nuevo"


def test_cambiar_la_cuenta_despues_de_aprobar_frena_la_emision(motor_migrate, e):  # noqa: F811
    """La cuenta no cambia en silencio: emitir se niega, la orden se modifica (otra versión del
    proveedor es un cambio), vuelve a revisión y se aprueba de nuevo."""
    o = _aprobada(motor_migrate, e)
    _nueva_cuenta(motor_migrate, e, e.proveedor, 1)
    niega(motor_migrate, e, e.adm, EMITIR, "proveedor_cambiado", orden=o, version=1)
    llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1)  # el mismo proveedor, v2
    v1, v2 = versiones(motor_migrate, o)
    assert (v1["proveedor_version"], v2["proveedor_version"]) == (1, 2)
    assert v1["huella"] != v2["huella"] and orden(motor_migrate, o)["estado"] == "en_revision"
    niega(motor_migrate, e, e.mc1, APROBAR, "huella_distinta", orden=o, version=2,
          huella=v1["huella"])  # fmt: skip
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=2, huella=v2["huella"])
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=2)  # control
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"


def test_sin_cambios_mira_tambien_la_version_del_proveedor(motor_migrate, e):  # noqa: F811
    o = preparada(motor_migrate, e)
    niega(motor_migrate, e, e.adm, MODIFICAR, "sin_cambios", orden=o, version=1)
    _nueva_cuenta(motor_migrate, e, e.proveedor, 1)
    assert llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1) == 2  # control


@pytest.mark.parametrize("estado", ["suspendido", "baja"])
def test_un_proveedor_inactivo_no_recibe_ordenes_ni_pagos(motor_migrate, e, estado):  # noqa: F811
    o = _aprobada(motor_migrate, e)
    _nueva_cuenta(motor_migrate, e, e.proveedor, 1, estado=estado)
    niega(motor_migrate, e, e.adm, PREPARAR, "proveedor_inactivo")
    niega(motor_migrate, e, e.adm, MODIFICAR, "proveedor_inactivo", orden=o, version=1)
    niega(motor_migrate, e, e.adm, EMITIR, "proveedor_cambiado", orden=o, version=1)
    assert llamar(motor_migrate, e, e.adm, PREPARAR, proveedor=e.otro) is not None  # control


def test_el_proveedor_tiene_que_ser_del_consorcio(motor_migrate, motor_app, e):  # noqa: F811
    ajena = escena_economica(motor_migrate, motor_app)
    niega(motor_migrate, e, e.adm, PREPARAR, "proveedor_inexistente", proveedor=ajena.proveedor)
    niega(motor_migrate, e, e.adm, PREPARAR, "proveedor_inexistente", proveedor=uuid4())
    assert preparada(motor_migrate, e) is not None  # control


def test_el_evento_de_la_orden_no_lleva_la_cuenta(motor_migrate, e):  # noqa: F811
    o = preparada(motor_migrate, e)
    for ev in eventos(motor_migrate, o):
        assert cbu_sintetico(1) not in ev["todo"] and RAZON_SOCIAL not in ev["todo"]


# Migración -----------------------------------------------------------------------------------

CAMBIADAS_0047 = (
    "nucleo.nueva_version_orden_pago(uuid, uuid, integer, bigint, text, text, text, text,"
    " boolean, uuid, uuid)",
    "nucleo.preparar_orden_pago(bigint, text, text, text, text, boolean, uuid, text, uuid)",
    "nucleo.modificar_orden_pago(uuid, integer, bigint, text, text, text, text, boolean, uuid,"
    " text, uuid)",
    "nucleo.emitir_orden_pago(uuid, integer, uuid, text, uuid)",
    "nucleo.registrar_aprobacion_orden_pago(uuid, integer, bytea, uuid, text, uuid)",
)


def _definiciones(motor, firmas) -> list[str]:
    with motor.connect() as c:
        return [
            c.execute(
                text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": f}
            ).scalar_one()
            for f in firmas
        ]


def _existe(motor) -> bool:
    with motor.connect() as c:
        return c.execute(text("select to_regclass('nucleo.proveedor') is not null")).scalar_one()


def _permisos_public(motor) -> int:
    """Funciones de órdenes y proveedores que `public` puede ejecutar."""
    with motor.connect() as c:
        return c.execute(
            text("select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                 " where n.nspname = 'nucleo' and (p.proname like '%orden_pago%'"
                 " or p.proname like '%proveedor%' or p.proname = 'cbu_valido')"
                 " and has_function_privilege('public', p.oid, 'EXECUTE')"
                 " and p.proname <> 'fijar_aprobaciones_orden_pago'")
        ).scalar_one()  # fmt: skip


def test_0048_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0047")
    en_0047 = _definiciones(motor, CAMBIADAS_0047)
    command.upgrade(cfg, "0048")
    assert _existe(motor) and _permisos_public(motor) == 0
    with motor.connect() as c:
        viejas = c.execute(
            text("select count(*) from pg_proc where proname in ('preparar_orden_pago',"
                 " 'modificar_orden_pago', 'nueva_version_orden_pago')"
                 " and pg_get_function_identity_arguments(oid) like '%beneficiario%'")
        ).scalar_one()  # fmt: skip
    assert viejas == 0  # las firmas con el texto se borraron, no quedan sobrecargas
    command.downgrade(cfg, "0047")
    assert not _existe(motor)
    assert _definiciones(motor, CAMBIADAS_0047) == en_0047  # vuelven exactamente
    command.upgrade(cfg, "0048")  # control: vuelve a subir limpio
    assert _existe(motor)


def test_0048_no_sube_con_ordenes(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0047")
    cons, _ = _consorcio_y_unidad(motor)
    quien = _persona(motor, "Sintetica")
    with motor.begin() as c:
        o = c.execute(
            text("insert into nucleo.orden_pago (consorcio_id, version_vigente, preparada_por)"
                 " values (:c, 1, :p) returning id"), {"c": cons, "p": quien},
        ).scalar_one()  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.upgrade(cfg, "0048")
    assert error.value.orig.diag.message_hint == "upgrade_con_datos"
    assert not _existe(motor)
    with motor.begin() as c:
        c.execute(text("delete from nucleo.orden_pago where id = :o"), {"o": o})
    command.upgrade(cfg, "0048")  # control
    assert _existe(motor)


# Quien cargó la cuenta no aprueba (revisión de app #165) --------------------------------------


def test_quien_cargo_la_cuenta_no_aprueba_el_pago(motor_migrate, motor_app, e):  # noqa: F811
    """El administrador carga el proveedor; otro prepara la orden; la otra cuenta del
    administrador en el Consejo no la aprueba. Tampoco después de que un tercero cambie el rubro
    sin tocar la cuenta. Control: otro consejero aprueba."""
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)
    with motor_migrate.connect() as c:
        m, acta = c.execute(
            text("select id, decision_id from nucleo.mandato where consorcio_id = :c"
                 " and persona_id = :p"), {"c": e.cons, "p": e.mc2},
        ).one()  # fmt: skip
    otorgar(motor_migrate, e.cons, m, acta, "egresos.preparar_orden")
    llamar(motor_migrate, e, e.mc2, VERSION, **{**DATOS, "proveedor": e.proveedor, "version": 1,
           "razon": RAZON_SOCIAL, "rubro": "Otro rubro", "cuenta": cbu_sintetico(1)})  # fmt: skip
    o = llamar(motor_migrate, e, e.mc2, PREPARAR)
    llamar(motor_migrate, e, e.mc2, ENVIAR, orden=o, version=1)
    h = huella(motor_migrate, o, 1)
    niega(motor_migrate, e, otra, APROBAR, "no_es_quien_cargo_la_cuenta", orden=o, version=1,
          huella=h)  # fmt: skip
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=h)  # control
    assert orden(motor_migrate, o)["estado"] == "aprobada"


def test_rechazar_sigue_abierto_para_quien_cargo_la_cuenta(motor_migrate, motor_app, e):  # noqa: F811
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)
    with motor_migrate.connect() as c:
        m, acta = c.execute(
            text("select id, decision_id from nucleo.mandato where consorcio_id = :c"
                 " and persona_id = :p"), {"c": e.cons, "p": e.mc2},
        ).one()  # fmt: skip
    otorgar(motor_migrate, e.cons, m, acta, "egresos.preparar_orden")
    o = llamar(motor_migrate, e, e.mc2, PREPARAR)
    llamar(motor_migrate, e, e.mc2, ENVIAR, orden=o, version=1)
    llamar(motor_migrate, e, otra, RECHAZAR, orden=o, version=1, motivo="SINTETICO")
    assert orden(motor_migrate, o)["estado"] == "rechazada"


def test_quien_firmo_una_version_con_la_cuenta_no_aprueba_aunque_no_prepare(
    motor_migrate,
    e,  # noqa: F811
):
    """La rama por cuenta: mc2 firma una versión que mantiene la cuenta (cambia el rubro); el
    administrador prepara; mc2 no aprueba. Control: mc1 aprueba."""
    with motor_migrate.connect() as c:
        m, acta = c.execute(
            text("select id, decision_id from nucleo.mandato where consorcio_id = :c"
                 " and persona_id = :p"), {"c": e.cons, "p": e.mc2},
        ).one()  # fmt: skip
    otorgar(motor_migrate, e.cons, m, acta, "egresos.preparar_orden")
    llamar(motor_migrate, e, e.mc2, VERSION, **{**DATOS, "proveedor": e.proveedor, "version": 1,
           "razon": RAZON_SOCIAL, "rubro": "Otro rubro", "cuenta": cbu_sintetico(1)})  # fmt: skip
    o = preparada(motor_migrate, e, en_revision=True)
    h = huella(motor_migrate, o, 1)
    niega(motor_migrate, e, e.mc2, APROBAR, "no_es_quien_cargo_la_cuenta", orden=o, version=1,
          huella=h)  # fmt: skip
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=h)  # control


def test_si_quien_cargo_la_cuenta_queda_sin_identidad_nadie_aprueba(motor_migrate, e):  # noqa: F811
    """Codex en #165: si soporte desvincula la identidad de quien cargó la cuenta, la separación
    se cierra (`identidad_no_verificada`), como `exigir_separacion_humana`. Prepara mc2 (no el
    administrador que cargó el proveedor), así que responde esta separación y no la de la orden.
    Control: antes de desvincular, mc1 aprueba otra orden igual."""
    with motor_migrate.connect() as c:
        m, acta = c.execute(
            text("select id, decision_id from nucleo.mandato where consorcio_id = :c"
                 " and persona_id = :p"), {"c": e.cons, "p": e.mc2},
        ).one()  # fmt: skip
    otorgar(motor_migrate, e.cons, m, acta, "egresos.preparar_orden")

    def preparada_por_mc2():
        o = llamar(motor_migrate, e, e.mc2, PREPARAR)
        llamar(motor_migrate, e, e.mc2, ENVIAR, orden=o, version=1)
        return o, huella(motor_migrate, o, 1)

    o1, h1 = preparada_por_mc2()
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o1, version=1, huella=h1)  # control
    o2, h2 = preparada_por_mc2()
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.identidad_persona set desvinculada_en = now()"
                 " where consorcio_id = :c and persona_id = :p and desvinculada_en is null"),
            {"c": e.cons, "p": e.adm},
        )  # fmt: skip
    niega(motor_migrate, e, e.mc1, APROBAR, "identidad_no_verificada", orden=o2, version=1,
          huella=h2)  # fmt: skip
