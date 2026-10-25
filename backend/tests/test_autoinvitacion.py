"""F9: nadie acepta su propia invitación (decisión del propietario del 24-10-2026, migración 0026).

Quien creó una invitación de vínculo no la acepta para sí: ni con la misma cuenta ni con otra
cuenta de la misma identidad humana verificada en el consorcio. Vale por el enlace y por
`/me/invitaciones`, para las manuales y las importadas; las del equipo no tienen quien invitó y
siguen igual. Y una autoinvitación ya aceptada no sostiene la condición del Consejo (0023).
"""

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import text

from app.db import transaccion
from scripts import invitar as script_invitar
from tests.conftest import url_rol
from tests.test_condicion_habilitante_consejo import (  # noqa: F401  (`consejo` es un fixture)
    consejo,
    opera,
    unidad,
    vinculo,
)
from tests.test_identidad import ORIGEN, app, csrf, navegador  # noqa: F401  (`app` es un fixture)
from tests.test_identidad_cierre_y_purga import entrar_como
from tests.test_identidad_humana import huella_al_azar, verificar_identidad, vincular_identidad
from tests.test_invitaciones import (
    ACEPTAR,
    correo_nuevo,
    edificio,
    invitar,
    persona_por_correo,
    token_invitacion,
)

pytestmark = [pytest.mark.db, pytest.mark.redis]

ACEPTADA = "2026-02-01 12:00-03"
"""Antes de la venta: así la condición no tiene hueco si la aceptación cuenta."""


def pendiente(motor_migrate, invitacion: str) -> bool:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select aceptada_en is null from nucleo.invitacion where id = :i"),
            {"i": invitacion},
        ).scalar_one()


def test_quien_invita_a_su_propio_correo_no_la_acepta(app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    ana = entrar_como(app, motor_migrate, e.propietaria)
    r = invitar(ana, e, e.propietaria)
    assert r.status_code == 201, r.text  # crearla se puede: lo que no se puede es aceptarla
    propia = r.json()
    token = token_invitacion(motor_migrate, e.propietaria)
    r = ana.post(ACEPTAR, json={"token": token}, headers=csrf(ana))
    assert (r.status_code, r.json()["codigo"]) == (409, "invitacion_propia")
    assert r.headers["cache-control"] == "no-store"
    assert pendiente(motor_migrate, propia["id"])
    # Ni por /me/invitaciones: no aparece en la lista y aceptarla también se niega.
    assert ana.get("/api/v1/me/invitaciones").json()["elementos"] == []
    r = ana.post(f"/api/v1/me/invitaciones/{propia['id']}/aceptar", headers=csrf(ana))
    assert (r.status_code, r.json()["codigo"]) == (409, "invitacion_propia")
    assert pendiente(motor_migrate, propia["id"])
    # Control: la misma invitación a otra persona se acepta.
    nuevo = correo_nuevo()
    invitar(ana, e, nuevo)
    assert (
        navegador(app)
        .post(ACEPTAR, json={"token": token_invitacion(motor_migrate, nuevo)})
        .status_code
        == 200
    )


def test_otra_cuenta_de_la_misma_identidad_tampoco_la_acepta(app, motor_app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    segundo = correo_nuevo()
    with motor_migrate.begin() as c:
        c.execute(
            text("insert into nucleo.persona (correo, nombre) values (:c, 'Ana otra')"),
            {"c": segundo},
        )
    ana_id = persona_por_correo(motor_migrate, e.propietaria).id
    otra_id = persona_por_correo(motor_migrate, segundo).id
    ana = entrar_como(app, motor_migrate, e.propietaria)
    assert invitar(ana, e, segundo).status_code == 201
    token = token_invitacion(motor_migrate, segundo)
    # Las dos cuentas son del mismo ser humano: soporte vio el mismo documento.
    huella = huella_al_azar()
    verificar_identidad(motor_app, e.consorcio, ana_id, huella)
    vincular_identidad(motor_app, e.consorcio, otra_id, huella)
    otra = entrar_como(app, motor_migrate, segundo)
    r = otra.post(ACEPTAR, json={"token": token}, headers=csrf(otra))
    assert (r.status_code, r.json()["codigo"]) == (409, "invitacion_propia")


def test_sin_identidad_verificada_la_otra_cuenta_no_se_detecta(app, motor_migrate):  # noqa: F811
    """El límite honesto de F9: sin la identidad humana verificada, otra cuenta es otra persona
    para el sistema. Lo que A13-C protege son las separaciones, que exigen la identidad."""
    e = edificio(motor_migrate)
    segundo = correo_nuevo()
    ana = entrar_como(app, motor_migrate, e.propietaria)
    invitar(ana, e, segundo)
    r = navegador(app).post(ACEPTAR, json={"token": token_invitacion(motor_migrate, segundo)})
    assert r.status_code == 200


def test_la_invitacion_del_equipo_se_acepta_como_siempre(app, motor_migrate, monkeypatch):  # noqa: F811
    e = edificio(motor_migrate)
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", ORIGEN)
    assert script_invitar.main([
        "--consorcio", str(e.consorcio), "--tipo", "propietario", "--correo", e.propietaria,
        "--unidad", str(e.u2), "--nombre", "Ana",
    ]) == 0  # fmt: skip
    token = token_invitacion(motor_migrate, e.propietaria)  # antes del correo para entrar
    ana = entrar_como(app, motor_migrate, e.propietaria)
    r = ana.post(ACEPTAR, json={"token": token}, headers=csrf(ana))
    assert r.status_code == 200, r.text


def _autoinvitacion_aceptada(motor_migrate, cons: UUID, uni: UUID, persona: UUID, v: UUID) -> None:
    """Lo que habría dejado, antes de F9, una invitación que la persona se creó y aceptó: su
    `invitada_por` es ella misma y el evento de aceptación nombra al vínculo."""
    with motor_migrate.begin() as c:
        invitacion = c.execute(
            text(
                "insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo, nombre,"
                " token_hash, invitada_por, creada_en, vence_en, aceptada_en, persona_id)"
                " select :c, :u, 'propietario', p.correo, 'Yo', sha256(gen_random_uuid()::text"
                "::bytea), :p, cast(:a as timestamptz), cast(:a as timestamptz) + interval '1 day',"
                " cast(:a as timestamptz) + interval '1 hour', :p"
                " from nucleo.persona p where p.id = :p returning id"
            ),
            {"c": cons, "u": uni, "p": persona, "a": ACEPTADA},
        ).scalar_one()
        c.execute(
            text(
                "update nucleo.vinculo set fuente = 'invitacion', origen = 'api',"
                " registrado_por = :p, creado_en = cast(:a as timestamptz) where id = :v"
            ),
            {"p": persona, "v": v, "a": ACEPTADA},
        )
        c.execute(
            text(
                "insert into auditoria.eventos (consorcio_id, actor_persona_id, origen, accion,"
                " entidad, entidad_id, despues, hash_previo, hash)"
                " values (:c, :p, 'api', 'identidad.invitacion.aceptar', 'invitacion', :i,"
                " jsonb_build_object('vinculo_id', cast(:v as uuid)), '', '')"
            ),
            {"c": cons, "p": persona, "i": invitacion, "v": v},
        )


def test_una_autoinvitacion_no_sostiene_al_consejo_despues_de_vender(consejo, motor_migrate):  # noqa: F811
    """El camino de F9: la persona del Consejo vende su unidad y, antes, se invita a sí misma
    como propietaria de otra. La aceptación ya no se puede (las pruebas de arriba); y una que
    hubiera quedado de antes tampoco cuenta para la condición."""
    e = consejo
    venta = date(2026, 3, 1)
    otra = unidad(motor_migrate, e["cons"], "7° G")
    v = vinculo(motor_migrate, e["cons"], otra, e["mc"], desde=date(2026, 2, 1))
    _autoinvitacion_aceptada(motor_migrate, e["cons"], otra, e["mc"], v)
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.vinculo set vigente_hasta = :h where id = :v"),
            {"h": venta, "v": e["v_prop"]},
        )
    assert not opera(motor_migrate, e["m"], e["hoy"])
    # Control: la misma invitación creada por otra persona sí la habilita.
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "update nucleo.invitacion set invitada_por = :o where persona_id = :p"
                " and invitada_por = :p"
            ),
            {"o": e["prop"], "p": e["mc"]},
        )
        c.execute(text("update nucleo.vinculo set registrado_por = :o where id = :v"),
                  {"o": e["prop"], "v": v})  # fmt: skip
    assert opera(motor_migrate, e["m"], e["hoy"])


def test_la_base_niega_la_autoaceptacion_aunque_no_pase_por_la_api(consejo, motor_app,  # noqa: F811
                                                                   motor_migrate):  # fmt: skip
    e = consejo
    otra = unidad(motor_migrate, e["cons"], "8° H")
    with motor_migrate.begin() as c:
        correo = c.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": e["mc"]}
        ).scalar_one()
        c.execute(
            text(
                "insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo, nombre,"
                " token_hash, invitada_por, vence_en)"
                " values (:c, :u, 'propietario', :correo, 'Yo', sha256('f9'::bytea), :p,"
                " now() + interval '1 day')"
            ),
            {"c": e["cons"], "u": otra, "correo": correo, "p": e["mc"]},
        )
    with transaccion(motor_app, persona_id=None) as c:
        resultado = c.execute(
            text(
                "select resultado from nucleo.aceptar_invitacion(sha256('f9'::bytea), :p, null,"
                " null, null, null, null, null)"
            ),
            {"p": e["mc"]},
        ).scalar_one()
    assert resultado == "invitacion_propia"
