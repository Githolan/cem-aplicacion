"""0053: un cambio de cuenta del proveedor obliga a confirmar de nuevo su contrato.

Decisión del propietario del 31-10-2026 (P3 112). La confirmación es de una versión del proveedor;
si después cambia la cuenta destino, el contrato deja de respaldar lo rutinario hasta que otro ser
humano, que no cargó esa cuenta, lo confirme sobre la versión nueva. La confirmación vieja queda en
la historia y no se traslada.
"""

from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import verificar_en_el_consorcio
from tests.test_egresos_1a import (  # noqa: F401  (`e` y `base_aparte` son fixtures)
    CONFIRMAR_CONTRATO,
    _hoy,
    base_aparte,
    conocido,
    confirmado,
    contrato,
    e,
    enviada,
    franja,
    visto,
)
from tests.test_orden_pago_neutra import (
    PREPARAR,
    RAZON_SOCIAL,
    cbu_sintetico,
    llamar,
    niega,
    nuevo_proveedor,
    otra_cuenta_en_el_consejo,
)
from tests.test_rutas_egresos import get
from tests.test_vinculo_0014 import _consorcio_y_unidad, _persona


def version(motor_migrate, e, numero: int, cuenta: str, por: UUID, razon=RAZON_SOCIAL) -> None:
    """Versión `numero` del proveedor de la escena (como dueño de la base)."""
    with motor_migrate.begin() as c:
        c.execute(
            text("insert into nucleo.proveedor_version (consorcio_id, proveedor_id, numero,"
                 " razon_social, estado, cuenta_destino, huella, preparada_por,"
                 " identidad_preparo) values (:c, :p, :n, :r, 'activo', :cta,"
                 " nucleo.huella_proveedor(:p, :n, null, :r, :cta), :q,"
                 " nucleo.identidad_humana_de(:c, :q))"),
            {"c": e.cons, "p": e.proveedor, "n": numero, "r": razon, "cta": cuenta, "q": por},
        )  # fmt: skip


def leido(e) -> dict:
    (k,) = get(e, e.adm, "contratos").json()["elementos"]
    return k


def vigente(motor_migrate, e, k) -> bool:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select nucleo.contrato_vigente(:c, :k, :hoy) is not null"),
            {"c": e.cons, "k": k, "hoy": _hoy(motor_migrate, e)},
        ).scalar_one()


def confirmaciones(motor_migrate, k) -> list[tuple]:
    with motor_migrate.connect() as c:
        return [tuple(f) for f in c.execute(
            text("select proveedor_version, persona_id from nucleo.contrato_proveedor_confirmacion"
                 " where contrato_id = :k order by proveedor_version"), {"k": k},
        )]  # fmt: skip


def test_cambiar_la_cuenta_obliga_a_reconfirmar_sobre_la_version_nueva(motor_migrate, motor_app,
                                                                         e):  # fmt: skip
    conocido(motor_migrate, e)
    k = confirmado(motor_migrate, e)
    assert franja(motor_migrate, enviada(motor_migrate, e, contrato=k)) == "rutinaria"  # control
    viejo = visto(motor_migrate, k)
    version(motor_migrate, e, 2, cbu_sintetico(41), por=e.mc1)

    # Hasta reconfirmar, el contrato no rige: ni lo rutinario ni la lectura lo dan por vigente.
    assert not vigente(motor_migrate, e, k)
    niega(motor_migrate, e, e.adm, PREPARAR, "contrato_no_aplica", contrato=k)
    leida = leido(e)
    assert (leida["estado"], leida["proveedor_version"], leida["cuenta_ultimos_digitos"]) == (
        "pendiente_reconfirmacion", 2, cbu_sintetico(41)[-4:])  # fmt: skip

    # Confirmar lo visto antes del cambio no alcanza: hay que ver la versión nueva.
    niega(motor_migrate, e, e.mc2, CONFIRMAR_CONTRATO, "proveedor_cambiado", contrato=k, **viejo)
    nuevo = visto(motor_migrate, k)
    assert nuevo["version_k"] == 2
    # Quien cargó la cuenta nueva no confirma, tampoco con otra cuenta suya.
    niega(motor_migrate, e, e.mc1, CONFIRMAR_CONTRATO, "no_es_quien_cargo_la_cuenta", contrato=k,
          **nuevo)  # fmt: skip
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.mc1)
    niega(motor_migrate, e, otra, CONFIRMAR_CONTRATO, "no_es_quien_cargo_la_cuenta", contrato=k,
          **nuevo)  # fmt: skip
    llamar(motor_migrate, e, e.mc2, CONFIRMAR_CONTRATO, contrato=k, **nuevo)  # control
    niega(motor_migrate, e, e.mc2, CONFIRMAR_CONTRATO, "contrato_ya_confirmado", contrato=k,
          **nuevo)  # fmt: skip

    assert vigente(motor_migrate, e, k)
    assert leido(e)["estado"] == "vigente"
    # Vuelve a respaldar; la franja es la de la cuenta recién cambiada, que va antes que el contrato.
    assert franja(motor_migrate, enviada(motor_migrate, e, contrato=k)) == "cuenta_cambiada"
    # La historia no se toca: la confirmación de la versión 1 sigue, con quien la hizo.
    assert confirmaciones(motor_migrate, k) == [(1, e.mc1), (2, e.mc2)]


def test_un_cambio_que_no_toca_la_cuenta_no_pide_reconfirmar(motor_migrate, e):
    conocido(motor_migrate, e)
    k = confirmado(motor_migrate, e)
    version(motor_migrate, e, 2, cbu_sintetico(1), por=e.mc1, razon="Otro nombre SINTETICO")
    assert vigente(motor_migrate, e, k)
    assert leido(e)["estado"] == "vigente"
    assert franja(motor_migrate, enviada(motor_migrate, e, contrato=k)) == "rutinaria"
    niega(motor_migrate, e, e.mc2, CONFIRMAR_CONTRATO, "contrato_ya_confirmado", contrato=k,
          **visto(motor_migrate, k))  # fmt: skip
    version(motor_migrate, e, 3, cbu_sintetico(41), por=e.adm)  # control: la cuenta sí
    assert not vigente(motor_migrate, e, k)


def test_volver_a_la_cuenta_anterior_no_revive_la_confirmacion(motor_migrate, e):
    conocido(motor_migrate, e)
    k = confirmado(motor_migrate, e)
    version(motor_migrate, e, 2, cbu_sintetico(41), por=e.adm)
    version(motor_migrate, e, 3, cbu_sintetico(1), por=e.mc1)
    assert not vigente(motor_migrate, e, k)
    assert leido(e)["estado"] == "pendiente_reconfirmacion"
    niega(motor_migrate, e, e.adm, PREPARAR, "contrato_no_aplica", contrato=k)
    # mc1 cargó la versión 3, con la misma cuenta que la vigente: no confirma.
    niega(motor_migrate, e, e.mc1, CONFIRMAR_CONTRATO, "no_es_quien_cargo_la_cuenta", contrato=k,
          **visto(motor_migrate, k))  # fmt: skip
    llamar(motor_migrate, e, e.mc2, CONFIRMAR_CONTRATO, contrato=k, **visto(motor_migrate, k))
    assert vigente(motor_migrate, e, k)  # control


FUNCIONES_0053 = (
    "nucleo.contrato_vigente(uuid, uuid, date)",
    "nucleo.contratos_proveedor_lectura(uuid, text, uuid, text, uuid)",
)
CONFIRMAR_0052 = "nucleo.confirmar_contrato_proveedor(uuid, bytea, uuid, text, uuid)"
CONFIRMAR_0053 = "nucleo.confirmar_contrato_proveedor(uuid, bytea, integer, uuid, text, uuid)"


def _estado(motor) -> dict:
    with motor.connect() as c:
        defs = {
            f: c.execute(
                text("select pg_get_functiondef(to_regprocedure(:f))"), {"f": f}
            ).scalar()
            for f in (*FUNCIONES_0053, CONFIRMAR_0052, CONFIRMAR_0053)
        }
        defs["columnas"] = c.execute(
            text("select string_agg(attname, ',' order by attnum) from pg_attribute"
                 " where attrelid = 'nucleo.contrato_proveedor_confirmacion'::regclass"
                 " and attnum > 0 and not attisdropped")
        ).scalar_one()  # fmt: skip
        defs["permisos"] = c.execute(
            text("select string_agg(p.oid::regprocedure::text || '=' ||"
                 " has_function_privilege('cem_app', p.oid, 'EXECUTE'), ',' order by 1)"
                 " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                 " where n.nspname = 'nucleo' and p.proname in ('confirmar_contrato_proveedor',"
                 " 'contratos_proveedor_lectura', 'confirmacion_vale')")
        ).scalar_one()  # fmt: skip
    return defs


def test_0053_ida_y_vuelta(base_aparte):  # noqa: F811
    """Sube y baja dejando las funciones, la tabla y los permisos idénticos a los de 0052."""
    cfg, motor = base_aparte
    command.upgrade(cfg, "0052")
    antes = _estado(motor)
    assert antes[CONFIRMAR_0053] is None
    command.upgrade(cfg, "0053")
    despues = _estado(motor)
    assert (despues[CONFIRMAR_0052], despues["columnas"].endswith("proveedor_version")) == (
        None, True)  # fmt: skip
    for f in FUNCIONES_0053:
        assert despues[f] != antes[f], f  # control: la subida cambió cada una
        assert "confirmacion_vale(" in despues[f], f
    assert "confirmacion_vale(uuid,uuid)=false" in despues["permisos"].replace(" ", "")
    command.downgrade(cfg, "0052")
    assert _estado(motor) == antes
    command.upgrade(cfg, "0053")  # control: vuelve a subir limpio
    assert _estado(motor) == despues


def _contrato_confirmado_en_0052(motor) -> tuple[UUID, UUID, UUID]:
    """Un consorcio con un proveedor y su contrato confirmado, cargado como dueño en 0052."""
    cons, _ = _consorcio_y_unidad(motor)
    quien = _persona(motor, "Sintetica")
    otro = _persona(motor, "Sintetico")
    for persona in (quien, otro):
        verificar_en_el_consorcio(motor, cons, persona)
    p = nuevo_proveedor(motor, cons, quien, cuenta=cbu_sintetico(1))
    with motor.begin() as c:
        doc = c.execute(
            text("insert into nucleo.documento (consorcio_id, categoria, titulo, fecha,"
                 " cargado_por) values (:c, 'contratos_proveedores', 'Contrato SINTETICO',"
                 " current_date, :q) returning id"), {"c": cons, "q": quien},
        ).scalar_one()  # fmt: skip
        k = c.execute(
            text("insert into nucleo.contrato_proveedor (consorcio_id, proveedor_id, tipo,"
                 " alcance, importe_maximo_centavos, moneda, vigente_desde, vigente_hasta,"
                 " documento_id, huella, registrado_por, identidad_registro) values (:c, :p,"
                 " 'abono', 'SINTETICO', 100, 'ARS', current_date, current_date, :d,"
                 " sha256('SINTETICO'), :q, nucleo.identidad_humana_de(:c, :q)) returning id"),
            {"c": cons, "p": p, "d": doc, "q": quien},
        ).scalar_one()  # fmt: skip
        c.execute(
            text("insert into nucleo.contrato_proveedor_confirmacion (consorcio_id, contrato_id,"
                 " huella, persona_id, identidad_id) values (:c, :k, sha256('SINTETICO'), :o,"
                 " nucleo.identidad_humana_de(:c, :o))"), {"c": cons, "k": k, "o": otro},
        )  # fmt: skip
    return cons, p, k


def test_0053_toma_la_version_vigente_al_confirmar_y_baja_solo_sin_reconfirmaciones(
    base_aparte,  # noqa: F811
):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0052")
    cons, p, k = _contrato_confirmado_en_0052(motor)
    with motor.begin() as c:  # una versión posterior a la confirmación, con otra cuenta
        c.execute(
            text("insert into nucleo.proveedor_version (consorcio_id, proveedor_id, numero,"
                 " razon_social, estado, cuenta_destino, huella, preparada_por,"
                 " identidad_preparo) select consorcio_id, proveedor_id, 2, razon_social,"
                 " estado, :cta, huella, preparada_por, identidad_preparo"
                 " from nucleo.proveedor_version where proveedor_id = :p and numero = 1"),
            {"cta": cbu_sintetico(41), "p": p},
        )  # fmt: skip
    command.upgrade(cfg, "0053")
    with motor.connect() as c:
        tomada = c.execute(
            text("select proveedor_version from nucleo.contrato_proveedor_confirmacion"
                 " where contrato_id = :k"), {"k": k},
        ).scalar_one()  # fmt: skip
        vale = c.execute(text("select nucleo.confirmacion_vale(:c, :k)"),
                         {"c": cons, "k": k}).scalar_one()  # fmt: skip
    # La versión de cuando se confirmó, no la de hoy: el cambio posterior la deja sin efecto.
    assert (tomada, vale) == (1, False)

    with motor.begin() as c:  # una reconfirmación sobre la versión 2
        c.execute(
            text("insert into nucleo.contrato_proveedor_confirmacion (consorcio_id, contrato_id,"
                 " huella, persona_id, identidad_id, proveedor_id, proveedor_version)"
                 " select consorcio_id, contrato_id, huella, persona_id, identidad_id,"
                 " proveedor_id, 2 from nucleo.contrato_proveedor_confirmacion"
                 " where contrato_id = :k"), {"k": k},
        )  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0052")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
    with motor.begin() as c:
        c.execute(text("alter table nucleo.contrato_proveedor_confirmacion"
                       " disable trigger historia_contrato_proveedor_confirmacion"))  # fmt: skip
        c.execute(text("delete from nucleo.contrato_proveedor_confirmacion"
                       " where contrato_id = :k and proveedor_version = 2"), {"k": k})  # fmt: skip
        c.execute(text("alter table nucleo.contrato_proveedor_confirmacion"
                       " enable trigger historia_contrato_proveedor_confirmacion"))  # fmt: skip
    command.downgrade(cfg, "0052")  # control: con una sola confirmación, baja
