"""Ámbito por mandato, aislamiento y denegaciones (H2b 2b-3, PR 3) contra PostgreSQL real.

Diseño: `doc/diseno/H2b_3_Tabla_de_verdad.md` del repositorio CEM, C3 a C5 y §6.5. Cada caso
lleva su control: el mismo pedido de alguien que sí tiene el ámbito o la facultad, o de alguien
que no, según lo que se pruebe. La doble barrera se prueba con una política de RLS "propios"
puesta a propósito: con ella la RLS sola dejaría ver mandatos de otro consorcio, y el filtro
explícito por `consorcio_id` de `require_scope` tiene que seguir alcanzando.
"""

from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Config
from app.contexto import Identidad, obtener_identidad, require_scope
from app.db import transaccion
from app.errores import ErrorApi
from app.main import crear_app
from tests.conftest import url_rol
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta, baja
from tests.test_modo_operacion import consorcio_con_persona, decidir

pytestmark = pytest.mark.db

IP = ("198.51.100.30", 40000)


def cliente(persona: UUID) -> TestClient:
    app = crear_app(
        Config(
            url_rol("app"),
            "0.1.0",
            "sha-imagen",
            "sha-contratos",
            redis_url="memory://",
            origen_publico="https://cem.ejemplo.com",
        )
    )
    app.dependency_overrides[obtener_identidad] = lambda: Identidad(persona)
    return TestClient(app, client=IP)


def otra_unidad(motor_migrate, cons: UUID, designacion: str = "2° B") -> tuple[UUID, UUID]:
    """Una segunda unidad del consorcio con su propietaria vigente desde 2020."""
    per = otra_persona(motor_migrate, cons)
    with motor_migrate.begin() as c:
        uni = c.execute(
            text(
                "insert into nucleo.unidad (consorcio_id, designacion) values (:c, :d) returning id"
            ),
            {"c": cons, "d": designacion},
        ).scalar_one()
        c.execute(
            text(
                "insert into nucleo.vinculo"
                " (consorcio_id, unidad_id, persona_id, tipo, vigente_desde)"
                " values (:c, :u, :p, 'propietario', '2020-01-01')"
            ),
            {"c": cons, "u": uni, "p": per},
        )
    return uni, per


def denegaciones(motor_app, cons: UUID, lector: UUID, persona: UUID) -> list[dict]:
    """Los eventos `autorizacion.denegada` de `persona`, leídos por `lector` (con ámbito)."""
    with transaccion(motor_app, persona_id=lector, consorcio_id=cons) as c:
        return list(
            c.execute(
                text(
                    "select despues from auditoria.eventos"
                    " where consorcio_id = :c and actor_persona_id = :p"
                    " and accion = 'autorizacion.denegada' order by id"
                ),
                {"c": cons, "p": persona},
            ).scalars()
        )


def invitacion(unidad: UUID, quien: str = "") -> dict:
    return {
        "unidad_id": str(unidad),
        "tipo": "inquilino",
        "correo": f"inv{quien}-{unidad.hex[:10]}@ejemplo.com",
        "nombre": "Beto Prueba",
    }


def corregir(motor_app, cons: UUID, actor: UUID, mandato: UUID) -> None:
    baja(motor_app, cons, actor, mandato, "error_carga",
         instrumento="Nota de corrección", descripcion="Persona equivocada")  # fmt: skip


@pytest.fixture
def edificio(motor_migrate, motor_app):
    """Consorcio con dos unidades y sus propietarias, una persona sin nada y un acta."""
    cons, u1, prop1 = consorcio_con_persona(motor_migrate)
    u2, prop2 = otra_unidad(motor_migrate, cons)
    return {
        "cons": cons,
        "u1": u1,
        "u2": u2,
        "prop1": prop1,
        "prop2": prop2,
        "nadie": otra_persona(motor_migrate, cons),
        "acta": decidir(motor_app, cons, prop1),
    }


def segundo_consorcio_con_mandato(motor_migrate, motor_app, persona: UUID) -> tuple[UUID, UUID]:
    """Otro consorcio donde `persona` es miembro del consejo."""
    cons_b, _, prop_b = consorcio_con_persona(motor_migrate)
    m_b = alta(motor_app, cons_b, prop_b, decidir(motor_app, cons_b, prop_b), persona=persona,
               tipo="miembro_consejo")  # fmt: skip
    return cons_b, m_b


# C3: un mandato vigente da ámbito ------------------------------------------------------------


def test_c3_administrador_sin_vinculo_entra_y_lista_unidades(motor_migrate, motor_app, edificio):
    e = edificio
    adm = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], e["prop1"], e["acta"], persona=adm)
    url = f"/api/v1/consorcios/{e['cons']}/unidades"
    with cliente(adm) as c:
        r = c.get(url)
    assert r.status_code == 200, r.text
    assert {u["id"] for u in r.json()["elementos"]} == {str(e["u1"]), str(e["u2"])}
    with cliente(e["nadie"]) as c:  # control: sin vínculo ni mandato no hay ámbito
        assert c.get(url).status_code == 404


def test_c3_un_mandato_corregido_por_error_de_carga_no_da_ambito(
    motor_migrate, motor_app, edificio
):
    e = edificio
    adm = otra_persona(motor_migrate, e["cons"])
    m = alta(motor_app, e["cons"], e["prop1"], e["acta"], persona=adm)
    url = f"/api/v1/consorcios/{e['cons']}/unidades"
    with cliente(adm) as c:
        assert c.get(url).status_code == 200  # control: antes de la corrección entra
        corregir(motor_app, e["cons"], e["prop1"], m)
        assert c.get(url).status_code == 404
    # El contexto mismo no lo trae (no solo el evaluador lo descarta por `causa_baja`).
    with pytest.raises(ErrorApi) as negado:
        require_scope(e["cons"], Identidad(adm), motor_app)
    assert negado.value.estado == 404


def test_c3_el_contexto_lleva_el_mandato_sus_facultades_y_el_administrador(
    motor_migrate, motor_app, edificio
):
    e = edificio
    adm = otra_persona(motor_migrate, e["cons"])
    m = alta(motor_app, e["cons"], e["prop1"], e["acta"], persona=adm)
    ctx = require_scope(e["cons"], Identidad(adm), motor_app)
    assert ctx.vinculos == ()
    assert [x.id for x in ctx.mandatos] == [m]
    assert ctx.facultades and {f.mandato_id for f in ctx.facultades} == {m}
    assert ctx.hay_administrador_vigente is True
    # Control: la propietaria entra por su vínculo, con su unidad (C5), y ve el administrador.
    ctx_prop = require_scope(e["cons"], Identidad(e["prop1"]), motor_app)
    assert [(v.tipo.value, v.unidad_id) for v in ctx_prop.vinculos] == [("propietario", e["u1"])]
    assert ctx_prop.mandatos == () and ctx_prop.facultades == ()
    assert ctx_prop.hay_administrador_vigente is True


def test_c3_sin_administrador_vigente_el_contexto_lo_dice(motor_migrate, motor_app, edificio):
    e = edificio
    ctx = require_scope(e["cons"], Identidad(e["prop1"]), motor_app)
    assert ctx.hay_administrador_vigente is False


# C4 y aislamiento ----------------------------------------------------------------------------


def test_c4_mandatos_en_dos_consorcios_el_contexto_solo_ve_el_de_la_ruta(
    motor_migrate, motor_app, edificio
):
    e = edificio
    adm = otra_persona(motor_migrate, e["cons"])
    m_a = alta(motor_app, e["cons"], e["prop1"], e["acta"], persona=adm)
    cons_b, m_b = segundo_consorcio_con_mandato(motor_migrate, motor_app, adm)
    ctx_a = require_scope(e["cons"], Identidad(adm), motor_app)
    ctx_b = require_scope(cons_b, Identidad(adm), motor_app)
    assert [m.id for m in ctx_a.mandatos] == [m_a]
    assert [m.id for m in ctx_b.mandatos] == [m_b]
    assert {f.mandato_id for f in ctx_a.facultades} == {m_a}
    assert {f.mandato_id for f in ctx_b.facultades} == {m_b}
    assert ctx_a.hay_administrador_vigente and not ctx_b.hay_administrador_vigente


def test_c4_un_mandato_en_a_no_da_ambito_en_b(motor_migrate, motor_app, edificio):
    e = edificio
    adm = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], e["prop1"], e["acta"], persona=adm)
    cons_b, _, _ = consorcio_con_persona(motor_migrate)
    with cliente(adm) as c:
        assert c.get(f"/api/v1/consorcios/{e['cons']}/unidades").status_code == 200  # control
        assert c.get(f"/api/v1/consorcios/{cons_b}/unidades").status_code == 404
        assert c.get(f"/api/v1/consorcios/{cons_b}/invitaciones").status_code == 404


def test_c4_me_consorcios_incluye_el_del_mandato_y_no_el_corregido(
    motor_migrate, motor_app, edificio
):
    e = edificio
    adm = otra_persona(motor_migrate, e["cons"])
    m = alta(motor_app, e["cons"], e["prop1"], e["acta"], persona=adm)
    with cliente(adm) as c:
        r = c.get("/api/v1/me/consorcios")
        assert r.status_code == 200, r.text
        assert [(x["id"], x["vinculos"]) for x in r.json()] == [(str(e["cons"]), [])]
        corregir(motor_app, e["cons"], e["prop1"], m)
        assert c.get("/api/v1/me/consorcios").json() == []
    with cliente(e["prop1"]) as c:  # control: la propietaria lo sigue viendo por su vínculo
        assert [(x["id"], x["vinculos"]) for x in c.get("/api/v1/me/consorcios").json()] == [
            (str(e["cons"]), ["propietario"])
        ]


# Facultades y denegaciones ------------------------------------------------------------------


def test_consejo_con_invitaciones_crear_invita_a_cualquier_unidad_y_las_lista(
    motor_migrate, motor_app, edificio
):
    e = edificio
    mc = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], e["prop1"], e["acta"], persona=mc, tipo="miembro_consejo")
    url = f"/api/v1/consorcios/{e['cons']}/invitaciones"
    with cliente(mc) as c:
        assert c.post(url, json=invitacion(e["u1"])).status_code == 201
        assert c.post(url, json=invitacion(e["u2"])).status_code == 201
        assert {i["unidad_id"] for i in c.get(url).json()["elementos"]} == {
            str(e["u1"]),
            str(e["u2"]),
        }
    with cliente(e["prop2"]) as c:  # control: la propietaria de u2 solo ve las de su unidad
        assert {i["unidad_id"] for i in c.get(url).json()["elementos"]} == {str(e["u2"])}


def test_un_mandato_sin_la_facultad_da_ambito_pero_no_invita_y_se_audita(
    motor_migrate, motor_app, edificio
):
    e = edificio
    mc = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], e["prop1"], e["acta"], persona=mc, tipo="miembro_consejo",
         facultades=["padron.ver"])  # fmt: skip
    url = f"/api/v1/consorcios/{e['cons']}/invitaciones"
    with cliente(mc) as c:
        assert c.get(f"/api/v1/consorcios/{e['cons']}/unidades").status_code == 200  # ámbito
        r = c.post(url, json=invitacion(e["u1"]))
    assert r.status_code == 403, r.text
    assert r.json()["codigo"] == "sin_permiso"
    assert denegaciones(motor_app, e["cons"], e["prop1"], mc) == [
        {"accion": "invitaciones.crear", "motivo": "sin_concesion"}
    ]
    with cliente(e["prop1"]) as c:  # control: la propietaria de u1 sí invita y no se audita
        assert c.post(url, json=invitacion(e["u1"], "p")).status_code == 201
    assert denegaciones(motor_app, e["cons"], e["prop1"], e["prop1"]) == []


def test_revocar_una_invitacion_de_otra_unidad_responde_404_y_se_audita(
    motor_migrate, motor_app, edificio
):
    e = edificio
    url = f"/api/v1/consorcios/{e['cons']}/invitaciones"
    with cliente(e["prop1"]) as c:
        inv = c.post(url, json=invitacion(e["u1"])).json()["id"]
    with cliente(e["prop2"]) as c:
        assert c.post(f"{url}/{inv}/revocar").status_code == 404
    assert [d["accion"] for d in denegaciones(motor_app, e["cons"], e["prop1"], e["prop2"])] == [
        "invitaciones.revocar"
    ]
    with cliente(e["prop1"]) as c:  # control: su propietaria la retira
        assert c.post(f"{url}/{inv}/revocar").status_code == 204


def test_sin_ambito_responde_404_y_no_se_audita_en_el_consorcio(motor_migrate, motor_app, edificio):
    e = edificio
    with cliente(e["nadie"]) as c:
        r = c.post(f"/api/v1/consorcios/{e['cons']}/invitaciones", json=invitacion(e["u1"]))
    assert r.status_code == 404
    assert denegaciones(motor_app, e["cons"], e["prop1"], e["nadie"]) == []


# E18: la fecha es la del consorcio -----------------------------------------------------------


def test_la_vigencia_usa_la_fecha_del_consorcio_y_no_la_de_la_base(motor_migrate, motor_app):
    """Un vínculo vigente hoy en la fecha del consorcio pero no en la de la base (GMT).

    Se elige la zona cuya fecha local difiere de la de GMT a esta hora: Kiritimati (GMT+14)
    desde las 10 GMT, `Etc/GMT+12` (GMT-12) antes. Control: la base confirma que con
    `current_date` el vínculo no estaría vigente.
    """
    with motor_migrate.begin() as c:
        hora_gmt = c.execute(text("select extract(hour from now() at time zone 'UTC')")).scalar()
        zona = "Pacific/Kiritimati" if hora_gmt >= 10 else "Etc/GMT+12"
        cons = c.execute(
            text("insert into nucleo.consorcio (nombre, zona_horaria) values ('Zona', :z)"
                 " returning id"), {"z": zona},
        ).scalar_one()  # fmt: skip
        hoy_local, hoy_base = c.execute(
            text("select nucleo.hoy_del_consorcio(:c), current_date"), {"c": cons}
        ).one()
        assert hoy_local != hoy_base
        # Adelantado: empieza hoy local. Atrasado: termina (exclusivo) hoy en GMT.
        desde, hasta = (hoy_local, None) if hoy_local > hoy_base else (date(2020, 1, 1), hoy_base)
        uni = c.execute(
            text("insert into nucleo.unidad (consorcio_id, designacion) values (:c, '1° A')"
                 " returning id"), {"c": cons},
        ).scalar_one()  # fmt: skip
        per = c.execute(
            text("insert into nucleo.persona (correo, nombre) values (:c, 'Zona') returning id"),
            {"c": f"zona-{cons}@ejemplo.com"},
        ).scalar_one()
        c.execute(
            text(
                "insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo,"
                " vigente_desde, vigente_hasta) values (:c, :u, :p, 'propietario', :d, :h)"
            ),
            {"c": cons, "u": uni, "p": per, "d": desde, "h": hasta},
        )
        assert not c.execute(
            text("select daterange(:d, :h) @> current_date"), {"d": desde, "h": hasta}
        ).scalar()
    with cliente(per) as c:
        assert [x["id"] for x in c.get("/api/v1/me/consorcios").json()] == [str(cons)]
        assert c.get(f"/api/v1/consorcios/{cons}/unidades").status_code == 200
        url = f"/api/v1/consorcios/{cons}/invitaciones"
        assert c.post(url, json=invitacion(uni)).status_code == 201
        assert [i["unidad_id"] for i in c.get(url).json()["elementos"]] == [str(uni)]


# Doble barrera -------------------------------------------------------------------------------


def test_doble_barrera_con_una_politica_propios_el_filtro_explicito_alcanza(
    motor_migrate, motor_app, edificio
):
    """Una política permisiva "propios" (lo que C4 prohíbe) se suma con OR a `por_consorcio` y
    la RLS sola dejaría ver los mandatos de la persona en otro consorcio. `require_scope` filtra
    por `consorcio_id` explícito, así que el contexto sigue siendo solo el de la ruta."""
    e = edificio
    adm = otra_persona(motor_migrate, e["cons"])
    m_a = alta(motor_app, e["cons"], e["prop1"], e["acta"], persona=adm)
    _, m_b = segundo_consorcio_con_mandato(motor_migrate, motor_app, adm)
    contar = text("select count(*) from nucleo.mandato where persona_id = :p")
    with transaccion(motor_app, persona_id=adm, consorcio_id=e["cons"]) as c:
        assert c.execute(contar, {"p": adm}).scalar_one() == 1  # sin la política: la RLS
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "create policy prueba_propios on nucleo.mandato for select to cem_app using"
                " (persona_id = nullif(current_setting('app.persona_id', true), '')::uuid)"
            )
        )
    try:
        with transaccion(motor_app, persona_id=adm, consorcio_id=e["cons"]) as c:
            # Control: con la política la RLS sola ya no alcanza.
            assert c.execute(contar, {"p": adm}).scalar_one() == 2
        ctx = require_scope(e["cons"], Identidad(adm), motor_app)
        assert [m.id for m in ctx.mandatos] == [m_a]
        assert m_b not in {f.mandato_id for f in ctx.facultades}
    finally:
        with motor_migrate.begin() as c:
            c.execute(text("drop policy prueba_propios on nucleo.mandato"))
