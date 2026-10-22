"""Importado pendiente en RF02 (migración 0024) contra PostgreSQL real.

Decisión del propietario del 21-10-2026 (`doc/diseno/Decisiones_Propietario_2026-10-21.md` del
repositorio CEM, §2.4). Un vínculo con fuente `importacion`, sin confirmar y sin una aceptación
que lo nombre no da ámbito ni aparece en `/me/consorcios`. La persona solo ve y acepta las
invitaciones de vínculo dirigidas a su correo. Las pruebas que pidió:

- importado, autenticado y sin confirmar: estado neutro (`/me/consorcios` vacío y el consorcio
  responde 404);
- sin `padron.ver_propio` y sin `documentos.ver`;
- ve solo las invitaciones dirigidas a su correo;
- al aceptar, y por separado al confirmar, recién ahí recibe las concesiones normales;
- control: un vínculo `documento` sin confirmar conserva las acciones base.
"""

import os
from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest
from cem_contracts.estados import ModoOperacion
from cem_contracts.politicas import TipoRecurso
from sqlalchemy import text

from app.autorizacion.evaluador import Entidad, evaluar
from app.contexto import Identidad, require_scope
from app.db import transaccion
from app.errores import ErrorApi
from tests.test_condicion_habilitante_consejo import aceptar, unidad, vinculo
from tests.test_contexto_mandatos import cliente
from tests.test_mandatos import otra_persona
from tests.test_modo_operacion import consorcio_con_persona

pytestmark = [pytest.mark.db, pytest.mark.redis]


def correo_de(motor_migrate, persona: UUID) -> str:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": persona}
        ).scalar_one()


def invitar(motor_migrate, cons: UUID, uni: UUID, correo: str, *, vencida=False) -> UUID:
    """Una invitación de vínculo pendiente, como la deja `POST …/invitaciones` (sin el correo
    que la publica)."""
    with motor_migrate.begin() as c:
        return c.execute(
            text(
                "insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo, nombre,"
                " token_hash, creada_en, vence_en)"
                " values (:c, :u, 'propietario', :correo, 'Vecina', :h,"
                " now() - interval '2 days',"
                " case when :vencida then now() - interval '1 day'"
                "      else now() + interval '5 days' end) returning id"
            ),
            {"c": cons, "u": uni, "correo": correo, "h": os.urandom(32), "vencida": vencida},
        ).scalar_one()


@pytest.fixture
def importada(motor_migrate):
    """Consorcio con su propietaria y una vecina importada (propietaria de la 2° B, fuente
    `importacion`, sin confirmar ni aceptar)."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    vecina = otra_persona(motor_migrate, cons)
    uni = unidad(motor_migrate, cons, "2° B")
    v = vinculo(motor_migrate, cons, uni, vecina, fuente="importacion")
    return {"cons": cons, "prop": prop, "vecina": vecina, "uni": uni, "v": v}


def mis_consorcios(persona: UUID) -> list[str]:
    with cliente(persona) as c:
        r = c.get("/api/v1/me/consorcios")
        assert r.status_code == 200, r.text
        return [x["id"] for x in r.json()]


def unidades(persona: UUID, cons: UUID) -> int:
    with cliente(persona) as c:
        return c.get(f"/api/v1/consorcios/{cons}/unidades").status_code


def invitaciones_del_consorcio(persona: UUID, cons: UUID) -> int:
    with cliente(persona) as c:
        return c.get(f"/api/v1/consorcios/{cons}/invitaciones").status_code


def concede(motor_app, persona: UUID, cons: UUID, accion: str, uni: UUID) -> bool:
    """La concesión por vínculo con el `Contexto` real. El modo se fija en Consejo: estas
    escenas no registran un acta de arranque, y lo que se prueba es el vínculo."""
    ctx = require_scope(cons, Identidad(persona), motor_app)
    autorizacion = replace(ctx.autorizacion, modo=ModoOperacion.CONSEJO)
    entidad = Entidad(recursos={TipoRecurso.UNIDAD: str(uni)})
    return evaluar(accion, autorizacion, entidad).permitido


def opera_normal(motor_app, persona: UUID, cons: UUID, uni: UUID) -> None:
    """Las concesiones normales de una propietaria: ámbito, consorcio en su lista, unidades,
    padrón propio, documentos y las invitaciones de su unidad."""
    assert str(cons) in mis_consorcios(persona)
    assert unidades(persona, cons) == 200
    assert invitaciones_del_consorcio(persona, cons) == 200
    assert concede(motor_app, persona, cons, "padron.ver_propio", uni)
    assert concede(motor_app, persona, cons, "documentos.ver", uni)


def neutro(motor_app, persona: UUID, cons: UUID) -> None:
    """El estado neutro: sin el consorcio en su lista y con 404 en todo lo del consorcio."""
    assert str(cons) not in mis_consorcios(persona)
    assert unidades(persona, cons) == 404
    assert invitaciones_del_consorcio(persona, cons) == 404
    with pytest.raises(ErrorApi) as negado:
        require_scope(cons, Identidad(persona), motor_app)
    assert negado.value.estado == 404


# Estado neutro y su control ---------------------------------------------------------------------


def test_el_importado_pendiente_queda_en_estado_neutro(importada, motor_app):
    e = importada
    assert mis_consorcios(e["vecina"]) == []
    neutro(motor_app, e["vecina"], e["cons"])
    # La política `propios` le sigue dejando leer su vínculo: el estado neutro.
    with transaccion(motor_app, persona_id=e["vecina"]) as c:
        assert (
            c.execute(
                text("select count(*) from nucleo.vinculo where persona_id = :p"),
                {"p": e["vecina"]},
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize("fuente", ["documento", "declaracion"])
def test_control_un_vinculo_sin_confirmar_de_otra_fuente_conserva_las_acciones_base(
    motor_migrate, motor_app, fuente
):
    cons, _, _ = consorcio_con_persona(motor_migrate)
    vecina = otra_persona(motor_migrate, cons)
    uni = unidad(motor_migrate, cons, "2° B")
    vinculo(motor_migrate, cons, uni, vecina, fuente=fuente)
    opera_normal(motor_app, vecina, cons, uni)


def test_otro_vinculo_que_no_es_importado_le_da_el_consorcio(importada, motor_migrate, motor_app):
    """Solo se excluye el importado pendiente: con otro vínculo en el mismo consorcio entra por
    ese, con su unidad, y el importado no aparece en su `Contexto`."""
    e = importada
    otra = unidad(motor_migrate, e["cons"], "5° E")
    vinculo(motor_migrate, e["cons"], otra, e["vecina"], tipo="inquilino", fuente="documento")
    assert str(e["cons"]) in mis_consorcios(e["vecina"])
    ctx = require_scope(e["cons"], Identidad(e["vecina"]), motor_app)
    assert [(v.tipo.value, v.unidad_id) for v in ctx.vinculos] == [("inquilino", otra)]


# Invitaciones propias ---------------------------------------------------------------------------


def test_ve_solo_las_invitaciones_pendientes_dirigidas_a_su_correo(importada, motor_migrate):
    e = importada
    mia = invitar(motor_migrate, e["cons"], e["uni"], correo_de(motor_migrate, e["vecina"]))
    otra_uni = unidad(motor_migrate, e["cons"], "3° C")
    invitar(motor_migrate, e["cons"], otra_uni, correo_de(motor_migrate, e["vecina"]),
            vencida=True)  # fmt: skip
    ajena = invitar(motor_migrate, e["cons"], e["uni"], "otra-persona@ejemplo.com")
    with cliente(e["vecina"]) as c:
        r = c.get("/api/v1/me/invitaciones")
    assert r.status_code == 200, r.text
    assert [x["id"] for x in r.json()["elementos"]] == [str(mia)]
    assert r.json()["elementos"][0]["unidad"] == "2° B"
    # Control: la otra persona ve la suya, no la de la vecina.
    otra = otra_persona(motor_migrate, e["cons"])
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.persona set correo = 'otra-persona@ejemplo.com' where id = :p"),
            {"p": otra},
        )
    with cliente(otra) as c:
        assert [x["id"] for x in c.get("/api/v1/me/invitaciones").json()["elementos"]] == [
            str(ajena)
        ]


def test_al_aceptar_recien_ahi_recibe_las_concesiones_normales(importada, motor_migrate, motor_app):
    e = importada
    mia = invitar(motor_migrate, e["cons"], e["uni"], correo_de(motor_migrate, e["vecina"]))
    neutro(motor_app, e["vecina"], e["cons"])  # control: antes de aceptar
    with cliente(e["vecina"]) as c:
        r = c.post(f"/api/v1/me/invitaciones/{mia}/aceptar")
        assert r.status_code == 200, r.text
        assert r.json() == {"consorcio_id": str(e["cons"])}
        # Ya usada: responde como cualquier invitación que no sirve.
        otra_vez = c.post(f"/api/v1/me/invitaciones/{mia}/aceptar")
        assert (otra_vez.status_code, otra_vez.json()["codigo"]) == (404, "invitacion_invalida")
    opera_normal(motor_app, e["vecina"], e["cons"], e["uni"])
    # Reutilizó el vínculo importado (no creó otro) y el evento lo nombra.
    with motor_migrate.connect() as c:
        assert (
            c.execute(
                text("select count(*) from nucleo.vinculo where persona_id = :p"),
                {"p": e["vecina"]},
            ).scalar_one()
            == 1
        )
        assert (
            c.execute(
                text(
                    "select count(*) from auditoria.eventos where accion ="
                    " 'identidad.invitacion.aceptar' and despues ->> 'vinculo_id' = :v"
                ),
                {"v": str(e["v"])},
            ).scalar_one()
            == 1
        )


def test_al_confirmar_recien_ahi_recibe_las_concesiones_normales(
    importada, motor_migrate, motor_app
):
    e = importada
    neutro(motor_app, e["vecina"], e["cons"])  # control: antes de confirmar
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "update nucleo.vinculo set confirmado_por = :p, confirmado_en = now(),"
                " confirmacion_referencia = 'Escritura de prueba' where id = :v"
            ),
            {"p": e["prop"], "v": e["v"]},
        )
    opera_normal(motor_app, e["vecina"], e["cons"], e["uni"])


@pytest.mark.parametrize("caso", ["ajena", "vencida", "inexistente"])
def test_no_acepta_una_invitacion_que_no_es_suya_o_no_sirve(
    importada, motor_migrate, motor_app, caso
):
    e = importada
    if caso == "ajena":
        inv = invitar(motor_migrate, e["cons"], e["uni"], "otra-persona@ejemplo.com")
    elif caso == "vencida":
        inv = invitar(motor_migrate, e["cons"], e["uni"], correo_de(motor_migrate, e["vecina"]),
                      vencida=True)  # fmt: skip
    else:
        inv = "01a10000-0000-7000-8000-0000000000ff"
    with cliente(e["vecina"]) as c:
        r = c.post(f"/api/v1/me/invitaciones/{inv}/aceptar")
    assert (r.status_code, r.json()["codigo"]) == (404, "invitacion_invalida")
    neutro(motor_app, e["vecina"], e["cons"])
    if caso == "ajena":
        with motor_migrate.connect() as c:
            assert (
                c.execute(
                    text("select aceptada_en from nucleo.invitacion where id = :i"), {"i": inv}
                ).scalar_one()
                is None
            )


def test_la_aceptacion_de_otro_vinculo_no_lo_saca_de_pendiente(importada, motor_migrate, motor_app):
    """El mismo criterio de E62 (Codex en app #102): la aceptación tiene que nombrar a este
    vínculo. Una aceptada para otro período de la misma unidad y carácter no alcanza."""
    e = importada
    viejo = vinculo(motor_migrate, e["cons"], e["uni"], e["vecina"], desde=date(2018, 1, 1),
                    hasta=date(2019, 1, 1), fuente="documento")  # fmt: skip
    aceptar(motor_migrate, e["cons"], e["uni"], e["vecina"], viejo, "2018-01-02 12:00-03")
    neutro(motor_app, e["vecina"], e["cons"])
    # Control: la aceptación que lo nombra a él sí.
    aceptar(motor_migrate, e["cons"], e["uni"], e["vecina"], e["v"], "2026-01-02 12:00-03")
    opera_normal(motor_app, e["vecina"], e["cons"], e["uni"])


def test_las_funciones_solo_responden_por_la_persona_de_la_sesion(importada, motor_app):
    e = importada
    consulta = text("select count(*) from nucleo.vinculos_importados_pendientes()")
    with transaccion(motor_app, persona_id=e["vecina"]) as c:
        assert c.execute(consulta).scalar_one() == 1  # control
    with transaccion(motor_app, persona_id=e["prop"]) as c:
        assert c.execute(consulta).scalar_one() == 0
    with transaccion(motor_app, persona_id=None) as c:
        assert c.execute(consulta).scalar_one() == 0
