"""Lectura de mandatos y decisiones y destinatario del mandato (migración 0018, 2b-4c).

Diseño: `doc/diseno/H2b_4c_Diseno.md` del repositorio CEM. Cada barrera se prueba con su caso
negado y su control (la misma lectura o el mismo alta, con lo que falta, pasa).
"""

from datetime import date, timedelta

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.test_mandatos import otra_persona, vinculo_confirmado
from tests.test_mandatos_0012 import CUIT_X, alta, baja, externo
from tests.test_modo_operacion import consorcio_con_persona, decidir, decidir_equipo
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (fixture)

pytestmark = pytest.mark.db

LECTURAS = {
    "mandatos_del_consorcio": "mandatos.ver",
    "decisiones_del_consorcio": "decisiones.ver",
    "candidatos_a_mandato": "mandatos.registrar",
}
COLUMNAS_LISTA = {
    "id", "tipo", "nombre", "cuit", "vigente_desde", "vigente_hasta", "estado", "con_cuenta",
}  # fmt: skip
SIN_LECTURA = ["padron.ver", "documentos.ver"]
"""Un paquete de Consejo achicado por el acta, sin `mandatos.ver` ni `decisiones.ver`."""


@pytest.fixture
def escena(motor_migrate, motor_app):
    """Consorcio con su propietaria, un miembro del Consejo con el paquete completo (con
    `mandatos.ver` y `decisiones.ver` desde 0018) y un administrador externo sin cuenta."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    mc = otra_persona(motor_migrate, cons)
    m_mc = alta(motor_app, cons, None, acta, tipo="miembro_consejo", persona=mc, origen="soporte")
    m_ae = externo(motor_app, cons, None, acta, origen="soporte")
    return {"cons": cons, "prop": prop, "acta": acta, "mc": mc, "m_mc": m_mc, "m_ae": m_ae}


def leer(motor_app, cons, quien, funcion, *argumentos) -> list[dict]:
    lista = ", ".join(f":a{i}" for i in range(len(argumentos)))
    with transaccion(motor_app, persona_id=quien, consorcio_id=cons) as c:
        return [
            dict(f)
            for f in c.execute(
                text(f"select * from nucleo.{funcion}({lista})"),
                {f"a{i}": a for i, a in enumerate(argumentos)},
            ).mappings()
        ]


def _negado(motor_app, cons, quien, funcion, *argumentos) -> str:
    with pytest.raises(DBAPIError) as error:
        leer(motor_app, cons, quien, funcion, *argumentos)
    return error.value.orig.sqlstate


def _motivo(error) -> str:
    return error.value.orig.diag.message_hint


def _alta_api(motor_app, cons, actor, decision, persona, tipo="miembro_consejo", **kw):
    """Un alta por la API sin el vínculo que agregan los ayudantes de escena."""
    return alta(motor_app, cons, actor, decision, tipo=tipo, persona=persona, con_vinculo=False,
                **kw)  # fmt: skip


# Catálogo, paquetes y privilegios -----------------------------------------------------------


def test_las_facultades_nuevas_estan_en_el_paquete_de_mc_y_ad_y_no_en_el_del_ae(motor_migrate):
    with motor_migrate.connect() as c:
        catalogo = dict(
            c.execute(
                text(
                    "select codigo, economica from nucleo.facultad"
                    " where codigo in ('mandatos.ver', 'decisiones.ver')"
                )
            ).all()
        )
        paquetes = set(
            c.execute(
                text(
                    "select tipo::text, facultad from nucleo.paquete_facultad"
                    " where facultad in ('mandatos.ver', 'decisiones.ver')"
                )
            ).all()
        )
    assert catalogo == {"mandatos.ver": False, "decisiones.ver": False}
    assert paquetes == {
        (t, f)
        for t in ("miembro_consejo", "administrador")
        for f in ("mandatos.ver", "decisiones.ver")
    }


def test_las_lecturas_son_security_definer_y_solo_las_ejecuta_cem_app(motor_migrate):
    with motor_migrate.connect() as c:
        filas = c.execute(
            text(
                "select p.proname, p.prosecdef,"
                " array_to_string(p.proconfig, ',') like '%search_path=pg_catalog, pg_temp%',"
                " has_function_privilege('cem_app', p.oid, 'execute'),"
                " has_function_privilege('public', p.oid, 'execute')"
                " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                " where n.nspname = 'nucleo' and p.proname in ('mandatos_del_consorcio',"
                " 'mandato_detalle', 'decisiones_del_consorcio', 'decision_detalle',"
                " 'candidatos_a_mandato', 'tiene_facultad_vigente', 'estado_mandato')"
            )
        ).all()
    publicas = {"mandatos_del_consorcio", "mandato_detalle", "decisiones_del_consorcio",
                "decision_detalle", "candidatos_a_mandato"}  # fmt: skip
    assert {f[0] for f in filas} == publicas | {"tiene_facultad_vigente", "estado_mandato"}
    for nombre, definer, camino, app, public in filas:
        if nombre in publicas:
            assert (definer, camino, app, public) == (True, True, True, False), nombre
        else:  # internas: las llaman las de arriba, no cem_app
            assert (camino, app, public) == (True, False, False), nombre


# Lectura: segunda barrera -------------------------------------------------------------------


@pytest.mark.parametrize("funcion", list(LECTURAS))
def test_sin_la_facultad_la_base_niega_y_con_ella_lee(escena, motor_app, funcion):
    e = escena
    assert _negado(motor_app, e["cons"], e["prop"], funcion) == "42501"
    leer(motor_app, e["cons"], e["mc"], funcion)  # control: el Consejo lee


@pytest.mark.parametrize("funcion", ["mandatos_del_consorcio", "decisiones_del_consorcio"])
def test_un_paquete_achicado_sin_la_lectura_no_lee(escena, motor_migrate, motor_app, funcion):
    e = escena
    otro = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], None, e["acta"], tipo="miembro_consejo", persona=otro,
         facultades=SIN_LECTURA, origen="soporte")  # fmt: skip
    assert _negado(motor_app, e["cons"], otro, funcion) == "42501"


@pytest.mark.parametrize("funcion", list(LECTURAS))
def test_un_mandato_vencido_no_da_lectura(escena, motor_migrate, motor_app, funcion):
    e = escena
    otro = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], None, e["acta"], tipo="miembro_consejo", persona=otro,
         hasta=date.today() - timedelta(days=1), origen="soporte")  # fmt: skip
    assert _negado(motor_app, e["cons"], otro, funcion) == "42501"


def test_el_externo_no_tiene_lectura_general(escena, motor_migrate, motor_app):
    """Un AE con cuenta (cargado por soporte en otro consorcio: en este ya hay AE, A14)."""
    otro, _, _ = consorcio_con_persona(motor_migrate)
    persona = otra_persona(motor_migrate, otro)
    externo(motor_app, otro, None, decidir_equipo(motor_app, otro), persona=persona,
            origen="soporte")  # fmt: skip
    for funcion in LECTURAS:
        assert _negado(motor_app, otro, persona, funcion) == "42501", funcion


def test_un_mandato_en_otro_consorcio_no_da_lectura_en_este(escena, motor_migrate, motor_app):
    e = escena
    otro, _, _ = consorcio_con_persona(motor_migrate)
    assert _negado(motor_app, otro, e["mc"], "mandatos_del_consorcio") == "42501"


# Lectura: lo mínimo -------------------------------------------------------------------------


def test_la_lista_de_mandatos_trae_lo_minimo_y_el_cuit_solo_del_externo(
    escena, motor_migrate, motor_app
):
    e = escena
    # Un Consejo cargado con CUIT: el CUIT no sale, porque solo se muestra el del externo.
    con_cuit = alta(motor_app, e["cons"], None, e["acta"], tipo="miembro_consejo",
                    persona=otra_persona(motor_migrate, e["cons"]), cuit=CUIT_X,
                    origen="soporte")  # fmt: skip
    filas = {f["id"]: f for f in leer(motor_app, e["cons"], e["mc"], "mandatos_del_consorcio")}
    assert set(filas) == {e["m_mc"], e["m_ae"], con_cuit}
    assert filas[con_cuit]["cuit"] is None
    assert all(set(f) == COLUMNAS_LISTA for f in filas.values())
    mc, ae = filas[e["m_mc"]], filas[e["m_ae"]]
    assert (mc["tipo"], mc["nombre"], mc["cuit"], mc["con_cuenta"], mc["estado"]) == (
        "miembro_consejo", "Otra", None, True, "vigente")  # fmt: skip
    assert (ae["tipo"], ae["nombre"], ae["cuit"], ae["con_cuenta"]) == (
        "administrador_externo", "Estudio X", CUIT_X, False)  # fmt: skip
    assert "@" not in repr(filas)  # ningún correo


def test_el_estado_sale_de_la_baja_y_de_las_fechas(escena, motor_migrate, motor_app):
    e = escena
    hoy = date.today()
    casos = {}
    for estado, kw in {
        "futuro": {"desde": hoy + timedelta(days=10)},
        "terminado": {"hasta": hoy - timedelta(days=1)},
        "termina_hoy": {"hasta": hoy},  # A1: ese día ya no vale
        "vigente_ultimo_dia": {"hasta": hoy + timedelta(days=1)},
    }.items():
        persona = otra_persona(motor_migrate, e["cons"])
        casos[estado] = alta(motor_app, e["cons"], None, e["acta"], tipo="miembro_consejo",
                             persona=persona, origen="soporte", **kw)  # fmt: skip
    con_baja = alta(motor_app, e["cons"], None, e["acta"], tipo="miembro_consejo",
                    persona=otra_persona(motor_migrate, e["cons"]), origen="soporte")  # fmt: skip
    baja(motor_app, e["cons"], e["prop"], con_baja)
    otra = otra_persona(motor_migrate, e["cons"])
    error_carga = alta(motor_app, e["cons"], None, e["acta"], tipo="miembro_consejo",
                       persona=otra, origen="soporte")  # fmt: skip
    baja(motor_app, e["cons"], e["prop"], error_carga, "error_carga", descripcion="Error")
    estados = {f["id"]: f["estado"]
               for f in leer(motor_app, e["cons"], e["mc"], "mandatos_del_consorcio")}  # fmt: skip
    assert (estados[casos["futuro"]], estados[casos["terminado"]], estados[casos["termina_hoy"]],
            estados[casos["vigente_ultimo_dia"]], estados[con_baja], estados[error_carga]) == (
        "futuro", "terminado", "terminado", "vigente", "dado_de_baja", "error_carga")  # fmt: skip


def test_el_detalle_suma_facultades_y_la_baja_y_lo_inexistente_no_sale(escena, motor_app):
    e = escena
    baja(motor_app, e["cons"], e["prop"], e["m_ae"], instrumento="Nota 7", descripcion="Renuncia")
    [ae] = leer(motor_app, e["cons"], e["mc"], "mandato_detalle", e["m_ae"])
    assert (ae["causa_baja"], ae["instrumento_referencia"], ae["motivo_fin"], ae["estado"]) == (
        "renuncia", "Nota 7", "Renuncia", "dado_de_baja")  # fmt: skip
    assert ae["facultades"] == ["documentos.cargar"]
    [mc] = leer(motor_app, e["cons"], e["mc"], "mandato_detalle", e["m_mc"])
    assert {"mandatos.ver", "decisiones.ver"} <= set(mc["facultades"])
    assert leer(motor_app, e["cons"], e["mc"], "mandato_detalle", e["acta"]) == []


def test_las_decisiones_salen_sin_referencia_y_el_detalle_la_suma(escena, motor_app):
    e = escena
    propia = decidir(motor_app, e["cons"], e["mc"])
    filas = leer(motor_app, e["cons"], e["mc"], "decisiones_del_consorcio")
    assert {f["id"] for f in filas} == {e["acta"], propia}
    assert all(set(f) == {"id", "tipo", "fecha", "descripcion"} for f in filas)
    [d] = leer(motor_app, e["cons"], e["mc"], "decision_detalle", propia)
    assert (d["referencia_acta"], d["origen"], d["registrada_por"]) == (
        "Acta 12, libro 3, folio 40", "api", e["mc"])  # fmt: skip
    assert leer(motor_app, e["cons"], e["mc"], "decision_detalle", e["m_mc"]) == []


# Candidatos ---------------------------------------------------------------------------------


def _vinculo(motor_migrate, cons, persona, *, confirmado=True, desde="2020-01-01", hasta=None):
    with motor_migrate.begin() as c:
        unidad = c.execute(
            text(
                "insert into nucleo.unidad (consorcio_id, designacion)"
                " values (:c, 'UF ' || left(md5(random()::text), 6)) returning id"
            ),
            {"c": cons},
        ).scalar_one()
        confirma = otra_persona(motor_migrate, cons) if confirmado else None
        c.execute(
            text(
                "insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo,"
                " vigente_desde, vigente_hasta, fuente, origen, confirmado_por, confirmado_en,"
                " confirmacion_referencia) values (:c, :u, :p, 'inquilino', :d, :h, 'documento',"
                " 'soporte', :k, case when cast(:k as uuid) is null then null else now() end,"
                " case when cast(:k as uuid) is null then null else 'Contrato' end)"
            ),
            {"c": cons, "u": unidad, "p": persona, "d": desde, "h": hasta, "k": confirma},
        )


def test_los_candidatos_son_los_vinculos_confirmados_vigentes_o_futuros(
    escena, motor_migrate, motor_app
):
    e = escena
    hoy = date.today()
    personas = {n: otra_persona(motor_migrate, e["cons"]) for n in
                ("vigente", "futuro", "sin_confirmar", "terminado", "baja")}  # fmt: skip
    _vinculo(motor_migrate, e["cons"], personas["vigente"])
    _vinculo(motor_migrate, e["cons"], personas["futuro"], desde=hoy + timedelta(days=5))
    _vinculo(motor_migrate, e["cons"], personas["sin_confirmar"], confirmado=False)
    _vinculo(motor_migrate, e["cons"], personas["terminado"], hasta=hoy)
    _vinculo(motor_migrate, e["cons"], personas["baja"])
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.persona set eliminado_en = now() where id = :p"),
                  {"p": personas["baja"]})  # fmt: skip
    otro, _, _ = consorcio_con_persona(motor_migrate)
    ajena = otra_persona(motor_migrate, otro)
    _vinculo(motor_migrate, otro, ajena)
    filas = leer(motor_app, e["cons"], e["mc"], "candidatos_a_mandato")
    ids = {f["persona_id"] for f in filas}
    assert {personas["vigente"], personas["futuro"]} <= ids
    assert ids.isdisjoint({personas["sin_confirmar"], personas["terminado"], personas["baja"],
                           ajena, e["prop"]})  # fmt: skip
    assert all(set(f) == {"persona_id", "nombre", "unidades"} for f in filas)
    assert "@" not in repr(filas)


# Destinatario -------------------------------------------------------------------------------


@pytest.mark.parametrize("tipo", ["miembro_consejo", "administrador"])
def test_por_la_api_sin_vinculo_confirmado_no_hay_mandato(escena, motor_migrate, motor_app, tipo):
    e = escena
    sin_vinculo = otra_persona(motor_migrate, e["cons"])
    sin_confirmar = otra_persona(motor_migrate, e["cons"])
    _vinculo(motor_migrate, e["cons"], sin_confirmar, confirmado=False)
    terminado = otra_persona(motor_migrate, e["cons"])
    _vinculo(motor_migrate, e["cons"], terminado, hasta=date.today())
    otro, _, _ = consorcio_con_persona(motor_migrate)
    de_otro = otra_persona(motor_migrate, otro)
    _vinculo(motor_migrate, otro, de_otro)
    inexistente = "01a10000-0000-7000-8000-000000000001"
    for persona in (sin_vinculo, sin_confirmar, terminado, de_otro, inexistente):
        with pytest.raises(DBAPIError) as error:
            _alta_api(motor_app, e["cons"], e["prop"], e["acta"], persona, tipo=tipo)
        assert _motivo(error) == "persona_sin_vinculo", persona
    # Control: con un vínculo confirmado futuro, el alta pasa.
    futuro = otra_persona(motor_migrate, e["cons"])
    _vinculo(motor_migrate, e["cons"], futuro, desde=date.today() + timedelta(days=5))
    if tipo == "administrador":  # A14: el AE de la escena ocupa la administración
        baja(motor_app, e["cons"], e["prop"], e["m_ae"], "error_carga", descripcion="Error")
    _alta_api(motor_app, e["cons"], e["prop"], e["acta"], futuro, tipo=tipo)


def test_por_la_api_el_externo_entra_sin_cuenta(escena, motor_migrate, motor_app):
    otro, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, otro)
    persona = otra_persona(motor_migrate, otro)
    vinculo_confirmado(motor_app, otro, persona)
    with pytest.raises(DBAPIError) as error:
        externo(motor_app, otro, prop, acta, persona=persona)
    assert _motivo(error) == "externo_por_invitacion"
    externo(motor_app, otro, prop, acta)  # control: sin cuenta entra


def test_soporte_conserva_la_via_excepcional(escena, motor_migrate, motor_app):
    """Soporte da un mandato sin vínculo (vía documentada y auditada) y sigue rechazando una
    cuenta que no existe con su motivo de 0016."""
    e = escena
    sin_vinculo = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], None, e["acta"], tipo="miembro_consejo", persona=sin_vinculo,
         origen="soporte")  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        alta(motor_app, e["cons"], None, e["acta"], tipo="miembro_consejo",
             persona="01a10000-0000-7000-8000-000000000001", origen="soporte")  # fmt: skip
    assert _motivo(error) == "persona_invalida"


def test_el_origen_se_valida_antes_que_la_persona(escena, motor_app):
    """Un origen inválido no revela si la cuenta existe: el motivo es el del origen."""
    e = escena
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as c:
            c.execute(
                text(
                    "select nucleo.alta_mandato('miembro_consejo', :p, null, null, null, :d,"
                    " '2026-01-01', null, null, :a, 'trabajos')"
                ),
                {"p": "01a10000-0000-7000-8000-000000000001", "d": e["acta"], "a": e["prop"]},
            )
    assert _motivo(error) == "origen_invalido"


# Downgrade ----------------------------------------------------------------------------------


def test_el_downgrade_de_0018_se_niega_con_facultades_de_lectura_otorgadas(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0018")
    command.downgrade(cfg, "0017")
    command.upgrade(cfg, "0018")  # ida y vuelta sin datos
    with motor.begin() as c:
        cons = c.execute(text("insert into nucleo.consorcio (nombre) values ('A') returning id"))
        cons = cons.scalar_one()
        dec = c.execute(
            text("insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                 " referencia_acta, origen) values (:c, 'asamblea', '2026-01-01', 'x', 'Acta',"
                 " 'soporte') returning id"), {"c": cons},
        ).scalar_one()  # fmt: skip
        per = c.execute(
            text("insert into nucleo.persona (correo, nombre) values ('v@e.com', 'V')"
                 " returning id")).scalar_one()  # fmt: skip
        m = c.execute(
            text("insert into nucleo.mandato (consorcio_id, tipo, persona_id, decision_id,"
                 " vigente_desde, origen) values (:c, 'miembro_consejo', :p, :d, '2026-01-01',"
                 " 'soporte') returning id"), {"c": cons, "p": per, "d": dec},
        ).scalar_one()  # fmt: skip
        c.execute(
            text("insert into nucleo.facultad_otorgada (consorcio_id, mandato_id, facultad,"
                 " decision_id) values (:c, :m, 'mandatos.ver', :d)"),
            {"c": cons, "m": m, "d": dec},
        )  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0017")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
    with motor.connect() as c:
        version = c.execute(text("select version_num from plataforma.alembic_version"))
        assert version.scalar_one() == "0018"
