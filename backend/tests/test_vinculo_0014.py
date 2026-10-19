"""Migración 0014 (2b-4, PR 1) contra PostgreSQL real.

Diseño: `doc/diseno/H2b_4_Diseno.md` del repositorio CEM, §6 y §11.1, con las decisiones del
propietario del 19-10-2026 (P4 y P5) y E31. Cada regla tiene su caso positivo, su negativo y una
mutación en `scripts` de la revisión que demuestra que la prueba la detecta. La migración de ida
y vuelta corre en una base aparte, para no depender de los datos de la sesión.
"""

import os
import threading
from datetime import timedelta
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.db import transaccion
from tests.conftest import ADMIN_URL, CLAVES, alembic_config
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import CUIT_X, _insertar_mandato, alta, baja
from tests.test_modo_operacion import consorcio_con_persona, decidir_equipo

pytestmark = pytest.mark.db

REFERENCIA = "Escritura 1234 del registro notarial 56"
COLUMNAS_PADRON = [
    "vinculo_id", "unidad_id", "designacion", "nombre", "tipo", "vigente_desde",
    "vigente_hasta", "confirmado",
]  # fmt: skip
RESTRICCIONES = {
    "vinculo_origen_valido",
    "vinculo_registrado_segun_origen",
    "vinculo_confirmacion_completa",
    "vinculo_confirmacion_referencia",
    "vinculo_no_confirma_el_propio",
    "vinculo_no_confirma_quien_registro",
}
CLAVES_E31 = ("mandatario_nombre", "mandatario_cuit", "instrumento_referencia", "motivo",
              "referencia_acta")  # fmt: skip


# Ayudantes ----------------------------------------------------------------------------------


def invitar_y_aceptar(motor, cons, unidad, invita, *, tipo="inquilino", persona=None) -> UUID:
    """Invitación cargada en la base y aceptada con `aceptar_invitacion`. Devuelve el vínculo.

    `invita` nulo es una invitación del equipo (soporte). `persona` es la cuenta que acepta con
    su sesión; sin ella, el correo es nuevo y la función crea la persona.
    """
    token = os.urandom(32)
    with motor.begin() as c:
        correo = (
            c.execute(
                text("select correo from nucleo.persona where id = :p"), {"p": persona}
            ).scalar_one()
            if persona
            else f"v14-{os.urandom(5).hex()}@ejemplo.com"
        )
        invitacion = c.execute(
            text(
                "insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo, nombre,"
                " token_hash, invitada_por, vence_en) values (:c, :u, :t, :co, 'Invitada',"
                " sha256(:tok), :i, now() + interval '1 day') returning id"
            ),
            {"c": cons, "u": unidad, "t": tipo, "co": correo, "tok": token, "i": invita},
        ).scalar_one()
        fila = c.execute(
            text(
                "select resultado, persona from nucleo.aceptar_invitacion(sha256(:tok), :ps,"
                " null, :st, gen_random_uuid(), 'pytest', '127.0.0.1', interval '1 day')"
            ),
            {"tok": token, "ps": persona, "st": os.urandom(32)},
        ).one()
        assert fila.resultado == "ok", fila
        # El vínculo que creó o reutilizó esta aceptación, según su evento (no por la tupla).
        return c.execute(
            text(
                "select (despues ->> 'vinculo_id')::uuid from auditoria.eventos"
                " where accion = 'identidad.invitacion.aceptar' and entidad_id = :i"
            ),
            {"i": invitacion},
        ).scalar_one()


SESION = object()


def confirmar(motor_app, cons, sesion, vinculo, *, referencia=REFERENCIA, actor=SESION,
              origen="api") -> None:  # fmt: skip
    with transaccion(motor_app, persona_id=sesion, consorcio_id=cons) as c:
        c.execute(
            text("select nucleo.confirmar_vinculo(:v, :r, :a, :o)"),
            {"v": vinculo, "r": referencia, "a": sesion if actor is SESION else actor,
             "o": origen},
        )  # fmt: skip


def vinculo(motor_migrate, vinculo_id) -> dict:
    with motor_migrate.connect() as c:
        return dict(
            c.execute(
                text(
                    "select persona_id, fuente::text, origen, registrado_por, confirmado_por,"
                    " confirmado_en, confirmacion_referencia from nucleo.vinculo where id = :v"
                ),
                {"v": vinculo_id},
            )
            .mappings()
            .one()
        )


def padron(motor_app, cons, persona) -> list[dict]:
    with transaccion(motor_app, persona_id=persona, consorcio_id=cons) as c:
        return [dict(f) for f in c.execute(text("select * from nucleo.padron_del_consorcio()"))
                .mappings()]  # fmt: skip


def eventos(motor_migrate, cons, accion) -> list[dict]:
    with motor_migrate.connect() as c:
        return [
            dict(f)
            for f in c.execute(
                text(
                    "select actor_persona_id, origen, entidad_id, antes, despues"
                    " from auditoria.eventos where consorcio_id = :c and accion = :a"
                    " order by secuencia"
                ),
                {"c": cons, "a": accion},
            ).mappings()
        ]


def cadena_rota(motor_migrate, cons) -> int:
    with motor_migrate.connect() as c:
        return c.execute(
            text(
                "select count(*) from (select hash_previo, lag(hash) over (order by secuencia)"
                " as previo from auditoria.eventos where consorcio_id = :c) x"
                " where hash_previo is distinct from coalesce(previo, '')"
            ),
            {"c": cons},
        ).scalar_one()


@pytest.fixture
def escena(motor_migrate, motor_app):
    """Consorcio con su propietaria (vínculo de soporte), un administrador y un miembro del
    Consejo con mandato, y un inquilino invitado por el administrador."""
    cons, u1, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    admin = otra_persona(motor_migrate, cons)
    mc = otra_persona(motor_migrate, cons)
    m_admin = alta(motor_app, cons, prop, acta, tipo="administrador", persona=admin)
    m_mc = alta(motor_app, cons, prop, acta, tipo="miembro_consejo", persona=mc)
    inq = invitar_y_aceptar(motor_migrate, cons, u1, admin)
    with motor_migrate.connect() as c:
        v_prop = c.execute(
            text("select id from nucleo.vinculo where persona_id = :p"), {"p": prop}
        ).scalar_one()
    return {"cons": cons, "u1": u1, "prop": prop, "acta": acta, "admin": admin, "mc": mc,
            "m_admin": m_admin, "m_mc": m_mc, "v_inq": inq, "v_prop": v_prop}  # fmt: skip


# Catálogo y permisos -------------------------------------------------------------------------


def test_restricciones_con_nombre_y_valores_de_fuente(motor_migrate):
    with motor_migrate.connect() as c:
        nombres = set(
            c.execute(
                text(
                    "select conname from pg_constraint where conrelid = 'nucleo.vinculo'::regclass"
                )
            ).scalars()
        )
        valores = c.execute(text("select enum_range(null::nucleo.fuente_vinculo)::text")).scalar()
        nulos = dict(
            c.execute(
                text(
                    "select column_name, is_nullable from information_schema.columns"
                    " where table_schema = 'nucleo' and table_name = 'vinculo'"
                )
            ).all()
        )
    assert RESTRICCIONES <= nombres
    assert valores == "{invitacion,importacion,declaracion,documento}"
    assert (nulos["fuente"], nulos["origen"]) == ("NO", "NO")
    assert nulos["registrado_por"] == nulos["confirmado_por"] == "YES"


def test_cem_app_solo_lee_el_vinculo_y_ejecuta_las_funciones(motor_migrate):
    with motor_migrate.connect() as c:
        tabla = (
            c.execute(
                text(
                    "select has_table_privilege('cem_app', 'nucleo.vinculo', p)"
                    " from unnest(array['select', 'insert', 'update', 'delete']) p"
                )
            )
            .scalars()
            .all()
        )
        columnas = dict(
            c.execute(
                text(
                    "select column_name,"
                    " has_column_privilege('cem_app', 'nucleo.vinculo', column_name, 'select')"
                    " from information_schema.columns"
                    " where table_schema = 'nucleo' and table_name = 'vinculo'"
                )
            ).all()
        )
        funciones = c.execute(
            text(
                "select p.proname, p.prosecdef, p.proconfig, pg_get_userbyid(p.proowner),"
                " has_function_privilege('cem_app', p.oid, 'execute'),"
                " has_function_privilege('public', p.oid, 'execute')"
                " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                " where n.nspname = 'nucleo'"
                " and p.proname in ('confirmar_vinculo', 'padron_del_consorcio',"
                " 'aceptar_invitacion') order by 1"
            )
        ).all()
    # Lee por columnas: todas menos la referencia del título.
    assert tabla == [False, False, False, False]
    assert columnas.pop("confirmacion_referencia") is False
    assert columnas and all(columnas.values()), columnas
    camino = ["search_path=pg_catalog, pg_temp"]
    assert [tuple(f) for f in funciones] == [
        ("aceptar_invitacion", True, camino, "cem_migrate", True, False),
        ("confirmar_vinculo", True, camino, "cem_migrate", True, False),
        ("padron_del_consorcio", True, camino, "cem_migrate", True, False),
    ]


def test_cem_app_no_lee_la_referencia_del_titulo(escena, motor_app):
    e = escena
    with transaccion(motor_app, persona_id=e["admin"], consorcio_id=e["cons"]) as c:
        assert c.execute(text("select confirmado_en from nucleo.vinculo where id = :v"),
                         {"v": e["v_inq"]}).scalar_one() is None  # fmt: skip
    with pytest.raises(ProgrammingError, match="permission denied"):
        with transaccion(motor_app, persona_id=e["admin"], consorcio_id=e["cons"]) as c:
            c.execute(text("select confirmacion_referencia from nucleo.vinculo"))


def test_cem_app_no_escribe_el_vinculo_directo(escena, motor_app):
    e = escena
    with transaccion(motor_app, persona_id=e["admin"], consorcio_id=e["cons"]) as c:
        assert c.execute(text("select count(*) from nucleo.vinculo")).scalar_one() == 2  # control
    for sql in (
        "update nucleo.vinculo set confirmado_en = now() where id = :v",
        "delete from nucleo.vinculo where id = :v",
        "insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo, vigente_desde,"
        " fuente, origen) select consorcio_id, unidad_id, persona_id, 'ocupante', '2030-01-01',"
        " 'documento', 'soporte' from nucleo.vinculo where id = :v",
    ):
        with pytest.raises(ProgrammingError, match="permission denied"):
            with transaccion(motor_app, persona_id=e["admin"], consorcio_id=e["cons"]) as c:
                c.execute(text(sql), {"v": e["v_inq"]})


# Procedencia al aceptar ----------------------------------------------------------------------


def test_aceptar_carga_la_procedencia_de_quien_invito(escena, motor_migrate):
    e = escena
    v = vinculo(motor_migrate, e["v_inq"])
    assert (v["fuente"], v["origen"], v["registrado_por"]) == ("invitacion", "api", e["admin"])
    assert v["confirmado_por"] is v["confirmado_en"] is v["confirmacion_referencia"] is None


def test_una_invitacion_del_equipo_queda_como_soporte_sin_quien_registro(escena, motor_migrate):
    e = escena
    v = vinculo(motor_migrate, invitar_y_aceptar(motor_migrate, e["cons"], e["u1"], None,
                                                 tipo="ocupante"))  # fmt: skip
    assert (v["fuente"], v["origen"], v["registrado_por"]) == ("invitacion", "soporte", None)


def test_aceptar_con_cem_app_sigue_funcionando_tras_el_revoke(escena, motor_migrate, motor_app):
    """`aceptar_invitacion` es `security definer`: el `revoke` a `cem_app` no la afecta."""
    e = escena
    token = os.urandom(32)
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo, nombre,"
                " token_hash, invitada_por, vence_en) values (:c, :u, 'ocupante', :co, 'Oc',"
                " sha256(:t), :i, now() + interval '1 day')"
            ),
            {"c": e["cons"], "u": e["u1"], "co": f"oc-{os.urandom(4).hex()}@ejemplo.com",
             "t": token, "i": e["mc"]},
        )  # fmt: skip
    with motor_app.begin() as c:
        fila = c.execute(
            text(
                "select resultado, persona from nucleo.aceptar_invitacion(sha256(:t), null, null,"
                " :s, gen_random_uuid(), 'pytest', '127.0.0.1', interval '1 day')"
            ),
            {"t": token, "s": os.urandom(32)},
        ).one()
    assert fila.resultado == "ok"
    with motor_migrate.connect() as c:
        assert (
            c.execute(
                text("select registrado_por from nucleo.vinculo where persona_id = :p"),
                {"p": fila.persona},
            ).scalar_one()
            == e["mc"]
        )


# Confirmar -----------------------------------------------------------------------------------


def test_confirmar_guarda_quien_cuando_y_la_referencia_y_audita_sin_ella(
    escena, motor_migrate, motor_app
):
    e = escena
    confirmar(motor_app, e["cons"], e["mc"], e["v_inq"], referencia=f"  {REFERENCIA}  ")
    v = vinculo(motor_migrate, e["v_inq"])
    assert v["confirmado_por"] == e["mc"] and v["confirmado_en"] is not None
    assert v["confirmacion_referencia"] == REFERENCIA
    [ev] = eventos(motor_migrate, e["cons"], "consorcio.vinculo.confirmar")
    assert (ev["actor_persona_id"], ev["origen"], ev["entidad_id"]) == (e["mc"], "api", e["v_inq"])
    assert ev["antes"] == {"confirmado": False}
    assert ev["despues"] == {"confirmado": True, "unidad_id": str(e["u1"]), "tipo": "inquilino"}
    assert REFERENCIA not in str(ev)
    assert cadena_rota(motor_migrate, e["cons"]) == 0


@pytest.mark.parametrize(
    ("caso", "quien", "actor", "origen", "referencia", "motivo"),
    [
        ("soporte no confirma", None, None, "soporte", REFERENCIA, "origen_invalido"),
        ("trabajos", "mc", "mc", "trabajos", REFERENCIA, "origen_invalido"),
        ("sin actor", "mc", None, "api", REFERENCIA, "actor_requerido"),
        ("actor ajeno", "mc", "admin", "api", REFERENCIA, "actor_no_coincide"),
        ("sin referencia", "mc", "mc", "api", None, "respaldo_requerido"),
        ("referencia en blanco", "mc", "mc", "api", "   ", "respaldo_requerido"),
        ("referencia de 301", "mc", "mc", "api", "x" * 301, "respaldo_requerido"),
        ("quien registró", "admin", "admin", "api", REFERENCIA, "confirmar_lo_que_registro"),
    ],
)
def test_confirmar_rechaza_con_su_motivo_y_no_toca_nada(
    escena, motor_migrate, motor_app, caso, quien, actor, origen, referencia, motivo
):
    e = escena
    with pytest.raises(DBAPIError, match=motivo):
        confirmar(motor_app, e["cons"], e.get(quien), e["v_inq"], referencia=referencia,
                  actor=e.get(actor), origen=origen)  # fmt: skip
    assert vinculo(motor_migrate, e["v_inq"])["confirmado_en"] is None, caso
    assert eventos(motor_migrate, e["cons"], "consorcio.vinculo.confirmar") == []


def test_nadie_confirma_su_propio_vinculo(escena, motor_migrate, motor_app):
    e = escena
    with pytest.raises(DBAPIError, match="confirmar_propio_vinculo"):
        confirmar(motor_app, e["cons"], e["prop"], e["v_prop"])
    confirmar(motor_app, e["cons"], e["admin"], e["v_prop"])  # control: otra persona sí
    assert vinculo(motor_migrate, e["v_prop"])["confirmado_por"] == e["admin"]


def test_un_vinculo_del_equipo_lo_confirma_cualquiera_que_no_sea_el_titular(
    escena, motor_migrate, motor_app
):
    """15 ter: sin `registrado_por`, P4-c no restringe."""
    e = escena
    assert vinculo(motor_migrate, e["v_prop"])["registrado_por"] is None
    confirmar(motor_app, e["cons"], e["mc"], e["v_prop"])
    assert vinculo(motor_migrate, e["v_prop"])["confirmado_por"] == e["mc"]


def test_vinculo_inexistente_o_de_otro_consorcio_responden_igual(escena, motor_migrate, motor_app):
    e = escena
    otro, u_otro, _ = consorcio_con_persona(motor_migrate)
    ajeno = invitar_y_aceptar(motor_migrate, otro, u_otro, None)
    for v in (ajeno, UUID(int=1)):
        with pytest.raises(DBAPIError, match="vinculo_invalido"):
            confirmar(motor_app, e["cons"], e["mc"], v)
    assert vinculo(motor_migrate, ajeno)["confirmado_en"] is None


def test_un_vinculo_confirmado_no_se_confirma_de_nuevo(escena, motor_app):
    e = escena
    confirmar(motor_app, e["cons"], e["mc"], e["v_inq"])
    with pytest.raises(DBAPIError, match="vinculo_ya_confirmado"):
        confirmar(motor_app, e["cons"], e["prop"], e["v_inq"])


def _poner_vigencia(motor_migrate, vinculo_id, desde, hasta) -> None:
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.vinculo set vigente_desde = :d, vigente_hasta = :h where id = :v"),
            {"d": desde, "h": hasta, "v": vinculo_id},
        )


def _hoy(motor_migrate, cons):
    with motor_migrate.connect() as c:
        return c.execute(text("select nucleo.hoy_del_consorcio(:c)"), {"c": cons}).scalar_one()


def test_un_vinculo_terminado_no_se_confirma_y_uno_futuro_si(escena, motor_migrate, motor_app):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    # `vigente_hasta` es exclusivo: terminado hoy ya no vale.
    _poner_vigencia(motor_migrate, e["v_inq"], hoy - timedelta(days=30), hoy)
    with pytest.raises(DBAPIError, match="vinculo_finalizado"):
        confirmar(motor_app, e["cons"], e["mc"], e["v_inq"])
    _poner_vigencia(motor_migrate, e["v_inq"], hoy - timedelta(days=30), hoy + timedelta(days=1))
    confirmar(motor_app, e["cons"], e["mc"], e["v_inq"])  # control: termina mañana, vale
    _poner_vigencia(motor_migrate, e["v_prop"], hoy + timedelta(days=10), None)
    confirmar(motor_app, e["cons"], e["mc"], e["v_prop"])  # futuro: se confirma


def test_las_restricciones_valen_aunque_escriba_el_duenio_de_la_tabla(escena, motor_migrate):
    e = escena
    casos = [
        ("persona_id", REFERENCIA, "vinculo_no_confirma_el_propio"),
        ("registrado_por", REFERENCIA, "vinculo_no_confirma_quien_registro"),
        (None, REFERENCIA, "vinculo_confirmacion_completa"),
        ("'" + str(e["mc"]) + "'::uuid", "   ", "vinculo_confirmacion_referencia"),
    ]
    for quien, ref, restriccion in casos:
        por = quien if quien else "null"
        en = "now()"
        with pytest.raises(DBAPIError, match=restriccion):
            with motor_migrate.begin() as c:
                c.execute(
                    text(
                        f"update nucleo.vinculo set confirmado_por = {por}, confirmado_en = {en},"
                        " confirmacion_referencia = :r where id = :v"
                    ),
                    {"r": ref, "v": e["v_inq"]},
                )
    with pytest.raises(DBAPIError, match="vinculo_registrado_segun_origen"):
        with motor_migrate.begin() as c:
            c.execute(text("update nucleo.vinculo set registrado_por = null where id = :v"),
                      {"v": e["v_inq"]})  # fmt: skip
    # Control: una confirmación válida del dueño pasa, y un vínculo de soporte sin confirmar
    # (los dos nulos) es válido.
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "update nucleo.vinculo set confirmado_por = :m, confirmado_en = now(),"
                " confirmacion_referencia = :r where id = :v"
            ),
            {"m": e["mc"], "r": REFERENCIA, "v": e["v_inq"]},
        )
    assert vinculo(motor_migrate, e["v_prop"])["registrado_por"] is None


def test_dos_confirmadores_a_la_vez_uno_confirma_y_el_otro_espera_y_se_rechaza(
    escena, motor_migrate, motor_app
):
    e = escena
    tomado = threading.Event()
    resultado = {}

    def segundo():
        tomado.wait(10)
        try:
            confirmar(motor_app, e["cons"], e["prop"], e["v_inq"])
            resultado["segundo"] = "confirmó"
        except DBAPIError as error:
            resultado["segundo"] = str(error.orig)

    hilo = threading.Thread(target=segundo)
    hilo.start()
    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
        c.execute(text("select nucleo.confirmar_vinculo(:v, :r, :a, 'api')"),
                  {"v": e["v_inq"], "r": REFERENCIA, "a": e["mc"]})  # fmt: skip
        tomado.set()
        hilo.join(1.5)
        assert hilo.is_alive(), "el segundo no esperó el bloqueo de la fila"
    hilo.join(10)
    assert "vinculo_ya_confirmado" in resultado["segundo"]
    assert vinculo(motor_migrate, e["v_inq"])["confirmado_por"] == e["mc"]
    assert len(eventos(motor_migrate, e["cons"], "consorcio.vinculo.confirmar")) == 1


# Padrón --------------------------------------------------------------------------------------


def test_el_padron_trae_solo_las_columnas_d6_sin_correo(escena, motor_app):
    e = escena
    filas = padron(motor_app, e["cons"], e["admin"])
    assert filas and list(filas[0]) == COLUMNAS_PADRON
    assert not any("@" in str(v) for f in filas for v in f.values())


def test_el_padron_lo_ven_administrador_y_consejo_y_no_la_propietaria(escena, motor_app):
    e = escena
    esperados = {e["v_prop"], e["v_inq"]}
    assert {f["vinculo_id"] for f in padron(motor_app, e["cons"], e["admin"])} == esperados
    assert {f["vinculo_id"] for f in padron(motor_app, e["cons"], e["mc"])} == esperados
    with pytest.raises(DBAPIError, match="sin padron.ver"):
        padron(motor_app, e["cons"], e["prop"])
    with pytest.raises(DBAPIError, match="sin ámbito"):
        padron(motor_app, e["cons"], None)


def test_el_padron_muestra_vigentes_y_futuros_y_no_terminados(escena, motor_migrate, motor_app):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    _poner_vigencia(motor_migrate, e["v_inq"], hoy - timedelta(days=30), hoy)
    _poner_vigencia(motor_migrate, e["v_prop"], hoy + timedelta(days=3), None)
    ids = {f["vinculo_id"] for f in padron(motor_app, e["cons"], e["admin"])}
    assert ids == {e["v_prop"]}
    _poner_vigencia(motor_migrate, e["v_inq"], hoy - timedelta(days=30), hoy + timedelta(days=1))
    assert {f["vinculo_id"] for f in padron(motor_app, e["cons"], e["admin"])} == {
        e["v_prop"], e["v_inq"]}  # fmt: skip


def test_el_padron_no_muestra_otro_consorcio(escena, motor_migrate, motor_app):
    e = escena
    otro, u_otro, prop_otro = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, otro)
    admin_otro = otra_persona(motor_migrate, otro)
    alta(motor_app, otro, prop_otro, acta, tipo="administrador", persona=admin_otro)
    filas_otro = padron(motor_app, otro, admin_otro)
    assert {f["unidad_id"] for f in filas_otro} == {u_otro}
    # El administrador de A, con el ámbito en B, no tiene mandato ahí.
    with pytest.raises(DBAPIError, match="sin padron.ver"):
        padron(motor_app, otro, e["admin"])


def test_un_mandato_que_no_vale_no_da_padron(escena, motor_app):
    e = escena
    baja(motor_app, e["cons"], e["prop"], e["m_mc"], "error_carga",
         instrumento="Error de carga", descripcion="Se cargó mal")  # fmt: skip
    with pytest.raises(DBAPIError, match="sin padron.ver"):
        padron(motor_app, e["cons"], e["mc"])
    assert padron(motor_app, e["cons"], e["admin"])  # control: el del administrador sigue


def test_el_padron_no_muestra_unidades_ni_personas_dadas_de_baja(escena, motor_migrate, motor_app):
    e = escena
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.persona set eliminado_en = now() where id = "
                       "(select persona_id from nucleo.vinculo where id = :v)"),
                  {"v": e["v_inq"]})  # fmt: skip
    assert {f["vinculo_id"] for f in padron(motor_app, e["cons"], e["admin"])} == {e["v_prop"]}
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.unidad set eliminado_en = now() where id = :u"),
                  {"u": e["u1"]})  # fmt: skip
    assert padron(motor_app, e["cons"], e["admin"]) == []


def test_la_vigencia_del_padron_usa_la_fecha_del_consorcio(motor_migrate, motor_app):
    """El vínculo termina (exclusivo) hoy en la fecha del consorcio y no en la de la base."""
    with motor_migrate.begin() as c:
        hora_gmt = c.execute(text("select extract(hour from now() at time zone 'UTC')")).scalar()
    zona = "Pacific/Kiritimati" if hora_gmt >= 10 else "Etc/GMT+12"
    cons, u1, prop = consorcio_con_persona(motor_migrate, zona)
    acta = decidir_equipo(motor_app, cons)
    admin = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, prop, acta, tipo="administrador", persona=admin,
         desde=_hoy(motor_migrate, cons) - timedelta(days=400))  # fmt: skip
    with motor_migrate.connect() as c:
        hoy_local, hoy_base = c.execute(
            text("select nucleo.hoy_del_consorcio(:c), current_date"), {"c": cons}
        ).one()
        v = c.execute(text("select id from nucleo.vinculo where persona_id = :p"),
                      {"p": prop}).scalar_one()  # fmt: skip
    assert hoy_local != hoy_base
    # Termina en el más tardío de los dos "hoy": para uno ya terminó y para el otro no.
    hasta = max(hoy_local, hoy_base)
    _poner_vigencia(motor_migrate, v, hasta - timedelta(days=30), hasta)
    visible = {f["vinculo_id"] for f in padron(motor_app, cons, admin)} == {v}
    assert visible == (hoy_local < hasta)


# E31 ------------------------------------------------------------------------------------------


def test_e31_los_eventos_de_mandatos_y_decisiones_no_llevan_texto_libre_ni_nombres(
    escena, motor_migrate, motor_app
):
    e = escena
    # El externo va en otro consorcio: en este ya hay administrador (A14).
    otro, _, prop_otro = consorcio_con_persona(motor_migrate)
    acta_otro = decidir_equipo(motor_app, otro)
    externo = alta(motor_app, otro, prop_otro, acta_otro, tipo="administrador_externo",
                   nombre="Administradora Ejemplo SRL", cuit="30500010912")  # fmt: skip
    baja(motor_app, e["cons"], e["prop"], e["m_mc"], instrumento="Nota de renuncia 7",
         descripcion="Renuncia por mudanza")  # fmt: skip
    textos = ("Administradora Ejemplo SRL", "30500010912", "Nota de renuncia 7",
              "Renuncia por mudanza", "Acta 12, libro 3, folio 40")  # fmt: skip
    todos = [
        ev
        for cons in (e["cons"], otro)
        for accion in ("consorcio.mandato.alta", "consorcio.mandato.baja",
                       "consorcio.decision.registrar")
        for ev in eventos(motor_migrate, cons, accion)
    ]  # fmt: skip
    assert len(todos) == 6  # dos altas y una decisión en la escena, la baja, el externo y su acta
    for ev in todos:
        for clave in CLAVES_E31:
            assert clave not in (ev["despues"] or {}), (clave, ev)
        assert not any(t in str(ev) for t in textos), ev
    [alta_ext] = [ev for ev in todos if ev["entidad_id"] == externo]
    assert alta_ext["despues"]["tipo"] == "administrador_externo"
    # Control: la fila conserva todo.
    with motor_migrate.connect() as c:
        fila = c.execute(
            text(
                "select string_agg(concat_ws('|', mandatario_nombre, mandatario_cuit,"
                " instrumento_referencia, motivo_fin), '|') from nucleo.mandato"
                " where consorcio_id in (:c, :o)"
            ),
            {"c": e["cons"], "o": otro},
        ).scalar_one()
        acta = c.execute(
            text("select referencia_acta from nucleo.decision_formal where id = :d"),
            {"d": e["acta"]},
        ).scalar_one()
    assert all(t in fila for t in textos[:4]) and acta == textos[4]


def funciones_que_auditan_claves_e31(motor) -> list[str]:
    with motor.connect() as c:
        return sorted(
            c.execute(
                text(
                    "select distinct p.proname from pg_proc p"
                    " join pg_namespace n on n.oid = p.pronamespace"
                    " where n.nspname = 'nucleo' and p.prosrc ilike '%auditoria.eventos%'"
                    " and p.prosrc ~* :claves"
                ),
                {"claves": "'(" + "|".join(CLAVES_E31) + ")'"},
            ).scalars()
        )


def test_e31_ninguna_funcion_activa_audita_esas_claves(motor_migrate):
    assert funciones_que_auditan_claves_e31(motor_migrate) == []


# Migración de ida y vuelta (base aparte) -----------------------------------------------------

BASE_APARTE = "cem_prueba_0014"


def _url(rol: str) -> str:
    return (
        make_url(ADMIN_URL)
        .set(username=f"cem_{rol}", password=CLAVES[rol], database=BASE_APARTE)
        .render_as_string(hide_password=False)
    )


@pytest.fixture
def base_aparte(base_de_prueba):
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"drop database if exists {BASE_APARTE} with (force)"))
        c.execute(text(f"create database {BASE_APARTE} owner cem_migrate"))
        c.execute(text(f"revoke all on database {BASE_APARTE} from public"))
        c.execute(text(f"grant connect on database {BASE_APARTE} to cem_app, cem_ro, cem_backup"))
    cfg = alembic_config()
    cfg.set_main_option("sqlalchemy.url", _url("migrate").replace("%", "%%"))
    motor = create_engine(_url("migrate"))
    yield cfg, motor
    motor.dispose()
    with admin.connect() as c:
        c.execute(text(f"drop database if exists {BASE_APARTE} with (force)"))
    admin.dispose()


def _catalogo(motor) -> dict:
    with motor.connect() as c:
        return {
            "columnas": sorted(
                c.execute(
                    text(
                        "select column_name || ' ' || is_nullable from information_schema.columns"
                        " where table_schema = 'nucleo' and table_name = 'vinculo'"
                    )
                ).scalars()
            ),
            "restricciones": sorted(
                c.execute(
                    text(
                        "select conname || ' ' || pg_get_constraintdef(oid) from pg_constraint"
                        " where conrelid = 'nucleo.vinculo'::regclass"
                    )
                ).scalars()
            ),
            "funciones": sorted(
                c.execute(
                    text(
                        "select p.oid::regprocedure::text || md5(p.prosrc)"
                        " || coalesce(array_to_string(p.proacl, ','), '')"
                        " || coalesce(array_to_string(p.proconfig, ','), '') from pg_proc p"
                        " join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'nucleo'"
                    )
                ).scalars()
            ),
            "permisos": c.execute(
                text(
                    "select string_agg(privilege_type, ',' order by privilege_type)"
                    " from information_schema.role_table_grants where grantee = 'cem_app'"
                    " and table_schema = 'nucleo' and table_name = 'vinculo'"
                )
            ).scalar(),
            "permisos_columna": sorted(
                c.execute(
                    text(
                        "select attname || coalesce(array_to_string(attacl, ','), '')"
                        " from pg_attribute where attrelid = 'nucleo.vinculo'::regclass"
                        " and attnum > 0 and not attisdropped"
                    )
                ).scalars()
            ),
            "tipos": c.execute(
                text("select count(*) from pg_type where typname = 'fuente_vinculo'")
            ).scalar(),
        }


def _consorcio_y_unidad(motor) -> tuple[UUID, UUID]:
    with motor.begin() as c:
        cons = c.execute(
            text("insert into nucleo.consorcio (nombre) values ('Aparte') returning id")
        ).scalar_one()
        uni = c.execute(
            text("insert into nucleo.unidad (consorcio_id, designacion) values (:c, '1° A')"
                 " returning id"), {"c": cons},
        ).scalar_one()  # fmt: skip
    return cons, uni


def _persona(motor, nombre: str) -> UUID:
    with motor.begin() as c:
        return c.execute(
            text("insert into nucleo.persona (correo, nombre) values (:c, :n) returning id"),
            {"c": f"{nombre.lower()}-{os.urandom(4).hex()}@ejemplo.com", "n": nombre},
        ).scalar_one()


def test_upgrade_deriva_la_procedencia_del_primer_evento_y_el_downgrade_vuelve_exacto(
    base_aparte,
):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0013")
    en_0013 = _catalogo(motor)
    cons, uni = _consorcio_y_unidad(motor)
    ana, beto = _persona(motor, "Ana"), _persona(motor, "Beto")
    # Vínculo 1: lo creó la invitación de Ana. Una segunda aceptación (invitación de Beto al
    # mismo correo, unidad y tipo) lo reutiliza y también lo nombra: no cambia quién lo registró.
    v1 = invitar_y_aceptar(motor, cons, uni, ana)
    with motor.connect() as c:
        quien = c.execute(text("select persona_id from nucleo.vinculo where id = :v"),
                          {"v": v1}).scalar_one()  # fmt: skip
    assert invitar_y_aceptar(motor, cons, uni, beto, persona=quien) == v1
    # Vínculo 2: invitación del equipo.
    v2 = invitar_y_aceptar(motor, cons, uni, None, tipo="ocupante")
    # Vínculo 3: la misma tupla que un vínculo viejo terminado de otra época, cada uno con su
    # invitador. Unir por la tupla los confundiría.
    viejo = invitar_y_aceptar(motor, cons, uni, beto, tipo="propietario")
    with motor.begin() as c:
        titular = c.execute(text("select persona_id from nucleo.vinculo where id = :v"),
                            {"v": viejo}).scalar_one()  # fmt: skip
        c.execute(text("update nucleo.vinculo set vigente_desde = '2019-01-01',"
                       " vigente_hasta = '2020-01-01' where id = :v"), {"v": viejo})  # fmt: skip
    v3 = invitar_y_aceptar(motor, cons, uni, ana, tipo="propietario", persona=titular)
    assert v3 != viejo

    command.upgrade(cfg, "0014")
    procedencia = {}
    with motor.connect() as c:
        for v in (v1, v2, viejo, v3):
            procedencia[v] = tuple(c.execute(
                text("select fuente::text, origen, registrado_por from nucleo.vinculo"
                     " where id = :v"), {"v": v}).one())  # fmt: skip
    assert procedencia[v1] == ("invitacion", "api", ana)
    assert procedencia[v2] == ("invitacion", "soporte", None)
    assert procedencia[viejo] == ("invitacion", "api", beto)
    assert procedencia[v3] == ("invitacion", "api", ana)
    assert funciones_que_auditan_claves_e31(motor) == []
    en_0014 = _catalogo(motor)
    assert en_0014["permisos"] is None and en_0014["tipos"] == 1

    command.downgrade(cfg, "0013")
    assert _catalogo(motor) == en_0013
    # Control del instrumento: en 0013 las funciones sí auditaban esas claves.
    assert funciones_que_auditan_claves_e31(motor) == [
        "alta_mandato", "baja_mandato", "registrar_decision"]  # fmt: skip
    command.upgrade(cfg, "0014")
    assert _catalogo(motor) == en_0014


def test_upgrade_con_un_vinculo_sin_evento_se_niega_y_no_cambia_nada(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0013")
    cons, uni = _consorcio_y_unidad(motor)
    invitar_y_aceptar(motor, cons, uni, None)
    with motor.begin() as c:  # cargado a mano en 0013, sin evento de aceptación
        c.execute(
            text("insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo,"
                 " vigente_desde) values (:c, :u, :p, 'ocupante', '2026-01-01')"),
            {"c": cons, "u": uni, "p": _persona(motor, "Sin")},
        )  # fmt: skip
    antes = _catalogo(motor)
    with pytest.raises(DBAPIError) as error:
        command.upgrade(cfg, "0014")
    # El texto del error incluye la migración entera: se compara el `hint`, no el mensaje.
    assert error.value.orig.diag.message_hint == "vinculo_sin_procedencia"
    assert _catalogo(motor) == antes
    with motor.connect() as c:
        assert c.execute(text("select version_num from plataforma.alembic_version")).scalar() == (
            "0013"
        )


def test_downgrade_con_un_vinculo_confirmado_se_niega(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0014")
    cons, uni = _consorcio_y_unidad(motor)
    v = invitar_y_aceptar(motor, cons, uni, None)
    otra = _persona(motor, "Conf")
    with motor.begin() as c:
        c.execute(
            text("update nucleo.vinculo set confirmado_por = :p, confirmado_en = now(),"
                 " confirmacion_referencia = :r where id = :v"),
            {"p": otra, "r": REFERENCIA, "v": v},
        )  # fmt: skip
    antes = _catalogo(motor)
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0013")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
    assert _catalogo(motor) == antes
    with motor.begin() as c:  # control: sin la confirmación, baja
        c.execute(
            text("update nucleo.vinculo set confirmado_por = null, confirmado_en = null,"
                 " confirmacion_referencia = null where id = :v"), {"v": v},
        )  # fmt: skip
    command.downgrade(cfg, "0013")


def test_con_mandatos_y_sin_confirmaciones_baja_a_0012_pero_no_a_0011(base_aparte):
    """La barrera de staging (P3) se mide guarda por guarda: un mandato frena por debajo de
    `0012`, no `0014 -> 0012`. Una confirmación frena por debajo de `0014` (prueba anterior)."""
    cfg, motor = base_aparte
    command.upgrade(cfg, "0014")
    _insertar_mandato(motor, mandatario_cuit=CUIT_X)
    cons, uni = _consorcio_y_unidad(motor)
    v = invitar_y_aceptar(motor, cons, uni, None)
    command.downgrade(cfg, "0012")
    command.upgrade(cfg, "0014")
    assert vinculo(motor, v)["fuente"] == "invitacion"
    command.downgrade(cfg, "0012")
    antes = _catalogo(motor)
    with pytest.raises(DBAPIError, match="downgrade de 0012 se niega"):
        command.downgrade(cfg, "0011")
    assert _catalogo(motor) == antes
    with motor.connect() as c:
        assert c.execute(text("select version_num from plataforma.alembic_version")).scalar() == (
            "0012"
        )
        assert c.execute(text("select count(*) from nucleo.mandato")).scalar() == 1


def test_downgrade_con_un_vinculo_que_no_entro_por_invitacion_se_niega(base_aparte):
    """Su procedencia no sale de ningún evento: bajar la borraría para siempre (Codex)."""
    cfg, motor = base_aparte
    command.upgrade(cfg, "0014")
    cons, uni = _consorcio_y_unidad(motor)
    persona = _persona(motor, "Doc")
    with motor.begin() as c:
        v = c.execute(
            text(
                "insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo,"
                " vigente_desde, fuente, origen) values (:c, :u, :p, 'propietario',"
                " '2020-01-01', 'documento', 'soporte') returning id"
            ),
            {"c": cons, "u": uni, "p": persona},
        ).scalar_one()
    antes = _catalogo(motor)
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0013")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
    assert _catalogo(motor) == antes
    with motor.begin() as c:  # control: sin ese vínculo, baja
        c.execute(text("delete from nucleo.vinculo where id = :v"), {"v": v})
    command.downgrade(cfg, "0013")
