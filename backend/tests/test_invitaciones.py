"""Invitaciones y alta de personas (RF03; migración 0007) contra PostgreSQL 18 y Redis 7 reales.

Cada prueba arma su propio edificio (consorcio, dos unidades, una propietaria y un inquilino),
así nada depende del orden ni de los datos de las otras pruebas. Los navegadores y el camino de
entrada (pedir y canjear el enlace) son los de `test_identidad.py`; el enlace de la invitación
se saca del evento que quedó en el outbox, como lo leería el trabajo de correo.
"""

import re
import threading
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy.exc
from sqlalchemy import text

from app.identidad import eventos, invitaciones, sesiones
from app.limite import FAMILIAS
from scripts import invitar as script_invitar
from tests.conftest import clave_idempotencia, url_rol
from tests.test_identidad import (  # noqa: F401  (`app` es un fixture)
    ORIGEN,
    SESION,
    app,
    csrf,
    h,
    navegador,
)
from tests.test_identidad_cierre_y_purga import entrar_como

pytestmark = [pytest.mark.db, pytest.mark.redis]

CONSULTAR = "/api/v1/auth/invitacion/consultar"
ACEPTAR = "/api/v1/auth/invitacion/aceptar"


def correo_nuevo() -> str:
    return f"i{uuid4().hex[:12]}@ejemplo.com"


@dataclass(frozen=True)
class Edificio:
    consorcio: UUID
    u1: UUID
    u2: UUID
    propietaria: str
    """Correo de la propietaria vigente de u1."""
    inquilino: str
    """Correo del inquilino vigente de u1."""


def edificio(motor_migrate) -> Edificio:
    prop, inq = correo_nuevo(), correo_nuevo()
    with motor_migrate.begin() as c:

        def uno(sql: str, **p) -> UUID:
            return c.execute(text(sql + " returning id"), p).scalar_one()

        co = uno(
            "insert into nucleo.consorcio (nombre) values (:n)", n=f"Edificio {uuid4().hex[:6]}"
        )
        u1 = uno("insert into nucleo.unidad (consorcio_id, designacion) values (:c, '3° B')", c=co)
        u2 = uno("insert into nucleo.unidad (consorcio_id, designacion) values (:c, '4° A')", c=co)
        for correo, nombre, tipo in ((prop, "Ana", "propietario"), (inq, "Iván", "inquilino")):
            p = uno(
                "insert into nucleo.persona (correo, nombre) values (:c, :n)", c=correo, n=nombre
            )
            uno(
                "insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo,"
                " vigente_desde, fuente, origen)"
                " values (:c, :u, :p, :t, '2020-01-01', 'documento', 'soporte')",
                c=co, u=u1, p=p, t=tipo,
            )  # fmt: skip
    return Edificio(co, u1, u2, prop, inq)


def ruta(e: Edificio, resto: str = "") -> str:
    return f"/api/v1/consorcios/{e.consorcio}/invitaciones{resto}"


def invitar(c, e: Edificio, correo: str, *, unidad=None, tipo="inquilino", nombre="Beto"):
    return c.post(
        ruta(e),
        json={"unidad_id": str(unidad or e.u1), "tipo": tipo, "correo": correo, "nombre": nombre},
        headers=csrf(c) | clave_idempotencia(),
    )


def correos_a(motor_migrate, para: str) -> list[dict]:
    with motor_migrate.connect() as conn:
        return [
            dict(f._mapping)
            for f in conn.execute(
                text(
                    "select consorcio_id, carga from plataforma.outbox"
                    " where carga->>'para' = :p order by id"
                ),
                {"p": para},
            )
        ]


def token_invitacion(motor_migrate, para: str) -> str:
    texto = correos_a(motor_migrate, para)[-1]["carga"]["texto"]
    [token] = re.findall(rf"{re.escape(ORIGEN)}/invitacion#token=([A-Za-z0-9_-]{{43}})", texto)
    return token


def auditoria_de(motor_migrate, entidad_id) -> list[dict]:
    with motor_migrate.connect() as conn:
        return [
            dict(f._mapping)
            for f in conn.execute(
                text(
                    "select actor_persona_id, origen, accion, despues, e::text as todo"
                    " from auditoria.eventos e where entidad_id = :i order by secuencia"
                ),
                {"i": entidad_id},
            )
        ]


def persona_por_correo(motor_migrate, correo: str):
    with motor_migrate.connect() as conn:
        return conn.execute(
            text("select id, nombre from nucleo.persona where correo = :c"), {"c": correo}
        ).one_or_none()


# Crear ------------------------------------------------------------------------------------


def test_la_propietaria_invita_y_sale_el_correo_con_el_enlace(app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    ana = entrar_como(app, motor_migrate, e.propietaria)
    nuevo = correo_nuevo()
    r = invitar(ana, e, nuevo.upper())  # el correo se guarda normalizado
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert (cuerpo["unidad"], cuerpo["tipo"], cuerpo["correo"], cuerpo["estado"]) == (
        "3° B", "inquilino", nuevo, "pendiente",
    )  # fmt: skip
    [evento] = correos_a(motor_migrate, nuevo)
    assert evento["consorcio_id"] == e.consorcio
    carga = evento["carga"]
    assert carga["asunto"].startswith("Te invitaron a Edificio ")
    assert "Ana te invitó" in carga["texto"] and "3° B" in carga["texto"]
    assert "inquilino/a" in carga["texto"]
    token = token_invitacion(motor_migrate, nuevo)
    with motor_migrate.connect() as conn:
        fila = conn.execute(
            text("select i::text as todo, token_hash from nucleo.invitacion i where id = :i"),
            {"i": cuerpo["id"]},
        ).one()
    assert fila.token_hash == h(token)
    assert token not in fila.todo  # solo el hash
    [aud] = auditoria_de(motor_migrate, UUID(cuerpo["id"]))
    assert (aud["accion"], aud["origen"]) == ("identidad.invitacion.crear", "api")
    assert nuevo not in aud["todo"]  # la auditoría no se borra: sin datos de contacto


def test_el_inquilino_no_puede_invitar(app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    ivan = entrar_como(app, motor_migrate, e.inquilino)
    nuevo = correo_nuevo()
    r = invitar(ivan, e, nuevo)
    assert r.status_code == 403
    assert r.json()["codigo"] == "sin_permiso"
    assert correos_a(motor_migrate, nuevo) == []


def test_la_propietaria_no_invita_a_una_unidad_que_no_es_suya(app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    otro = edificio(motor_migrate)
    ana = entrar_como(app, motor_migrate, e.propietaria)
    nuevo = correo_nuevo()
    assert invitar(ana, e, nuevo, unidad=e.u2).status_code == 403
    # Una unidad de otro consorcio en la ruta del propio: la RLS ni la deja ver.
    assert invitar(ana, e, nuevo, unidad=otro.u1).status_code == 403
    # La ruta de otro consorcio: 404, como si no existiera.
    r = ana.post(
        ruta(otro),
        json={"unidad_id": str(otro.u1), "tipo": "inquilino", "correo": nuevo, "nombre": "X"},
        headers=csrf(ana) | clave_idempotencia(),
    )
    assert r.status_code == 404
    assert correos_a(motor_migrate, nuevo) == []


def test_invitar_pide_csrf_y_sesion(app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    ana = entrar_como(app, motor_migrate, e.propietaria)
    cuerpo = {"unidad_id": str(e.u1), "tipo": "inquilino", "correo": correo_nuevo(), "nombre": "B"}
    assert ana.post(ruta(e), json=cuerpo, headers=clave_idempotencia()).status_code == 403
    assert (
        navegador(app).post(ruta(e), json=cuerpo, headers=clave_idempotencia()).status_code == 401
    )


@pytest.mark.parametrize("nombre", ["", "   ", "Beto\nAsunto: otro", "x" * 101])
def test_nombres_que_no_sirven_para_el_correo(app, motor_migrate, nombre):  # noqa: F811
    e = edificio(motor_migrate)
    ana = entrar_como(app, motor_migrate, e.propietaria)
    assert invitar(ana, e, correo_nuevo(), nombre=nombre).status_code == 422


def test_invitar_de_nuevo_retira_la_anterior(app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    ana = entrar_como(app, motor_migrate, e.propietaria)
    nuevo = correo_nuevo()
    primera = invitar(ana, e, nuevo).json()
    viejo = token_invitacion(motor_migrate, nuevo)
    segunda = invitar(ana, e, nuevo).json()
    assert navegador(app).post(CONSULTAR, json={"token": viejo}).status_code == 404
    lista = ana.get(ruta(e)).json()["elementos"]
    estados = {x["id"]: x["estado"] for x in lista}
    assert estados[primera["id"]] == "revocada" and estados[segunda["id"]] == "pendiente"
    assert [a["accion"] for a in auditoria_de(motor_migrate, UUID(primera["id"]))] == [
        "identidad.invitacion.crear",
        "identidad.invitacion.reemplazar",
    ]
    # Otro vínculo para el mismo correo es otra invitación: no retira la pendiente.
    tercera = invitar(ana, e, nuevo, tipo="ocupante").json()
    estados = {x["id"]: x["estado"] for x in ana.get(ruta(e)).json()["elementos"]}
    assert estados[segunda["id"]] == estados[tercera["id"]] == "pendiente"


def test_la_lista_es_solo_de_las_unidades_propias(app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    ana = entrar_como(app, motor_migrate, e.propietaria)
    invitar(ana, e, correo_nuevo())
    assert len(ana.get(ruta(e)).json()["elementos"]) == 1
    ivan = entrar_como(app, motor_migrate, e.inquilino)
    assert ivan.get(ruta(e)).json()["elementos"] == []


def test_el_cupo_de_invitaciones_es_por_persona(app, motor_migrate, monkeypatch):  # noqa: F811
    from limits import parse

    monkeypatch.setattr(
        "app.limite.LIMITES_INVITACIONES_PERSONA", (parse("2/hour"), parse("50/day"))
    )
    e = edificio(motor_migrate)
    ana = entrar_como(app, motor_migrate, e.propietaria)
    assert invitar(ana, e, correo_nuevo()).status_code == 201
    assert invitar(ana, e, correo_nuevo()).status_code == 201
    r = invitar(ana, e, correo_nuevo())
    assert r.status_code == 429 and "retry-after" in r.headers


# Consultar y aceptar ----------------------------------------------------------------------


def test_correo_sin_cuenta_acepta_y_queda_dentro(app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    ana = entrar_como(app, motor_migrate, e.propietaria)
    nuevo = correo_nuevo()
    invitacion = invitar(ana, e, nuevo).json()
    token = token_invitacion(motor_migrate, nuevo)
    beto = navegador(app)
    vista = beto.post(CONSULTAR, json={"token": token})
    assert vista.status_code == 200
    assert vista.headers["cache-control"] == "no-store"
    v = vista.json()
    assert (v["unidad"], v["tipo"], v["correo"], v["nombre"], v["invitada_por"]) == (
        "3° B", "inquilino", nuevo, "Beto", "Ana",
    )  # fmt: skip
    # Consultar no la gasta.
    assert beto.post(CONSULTAR, json={"token": token}).status_code == 200

    r = beto.post(ACEPTAR, json={"token": token, "nombre": "  Alberto  "})
    assert r.status_code == 200, r.text
    assert r.json()["cuenta_nueva"] is True
    persona = persona_por_correo(motor_migrate, nuevo)
    assert persona.nombre == "Alberto"
    # Queda con sesión y ve su consorcio con el vínculo nuevo.
    assert beto.get(SESION).json()["persona_id"] == str(persona.id)
    [mio] = beto.get("/api/v1/me/consorcios").json()
    assert (mio["id"], mio["vinculos"]) == (str(e.consorcio), ["inquilino"])
    # Un solo uso (ahora con sesión, así que con su CSRF).
    assert beto.post(ACEPTAR, json={"token": token}, headers=csrf(beto)).status_code == 404
    assert beto.post(CONSULTAR, json={"token": token}).json()["codigo"] == "invitacion_invalida"
    [lista] = [x for x in ana.get(ruta(e)).json()["elementos"] if x["id"] == invitacion["id"]]
    assert lista["estado"] == "aceptada"
    acciones = [a["accion"] for a in auditoria_de(motor_migrate, UUID(invitacion["id"]))]
    assert acciones == ["identidad.invitacion.crear", "identidad.invitacion.aceptar"]
    aceptar = auditoria_de(motor_migrate, UUID(invitacion["id"]))[-1]
    assert aceptar["actor_persona_id"] == persona.id
    assert aceptar["despues"]["cuenta_nueva"] is True


def test_sin_nombre_queda_el_de_la_invitacion(app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    nuevo = correo_nuevo()
    invitar(entrar_como(app, motor_migrate, e.propietaria), e, nuevo, nombre="Beto Gómez")
    r = navegador(app).post(ACEPTAR, json={"token": token_invitacion(motor_migrate, nuevo)})
    assert r.status_code == 200
    assert persona_por_correo(motor_migrate, nuevo).nombre == "Beto Gómez"


def test_correo_con_cuenta_pide_su_propia_sesion(app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    otro = edificio(motor_migrate)
    ana = entrar_como(app, motor_migrate, e.propietaria)
    # Invita a la propietaria de otro edificio, que ya tiene cuenta.
    invitar(ana, e, otro.propietaria, tipo="ocupante")
    token = token_invitacion(motor_migrate, otro.propietaria)

    sin_sesion = navegador(app).post(ACEPTAR, json={"token": token})
    assert sin_sesion.status_code == 409
    assert sin_sesion.json()["codigo"] == "invitacion_requiere_sesion"
    assert sesiones.COOKIE_SESION not in sin_sesion.cookies

    # Con la sesión de otra persona (la propia Ana), tampoco.
    ajena = ana.post(ACEPTAR, json={"token": token}, headers=csrf(ana))
    assert ajena.status_code == 409
    assert ajena.json()["codigo"] == "invitacion_de_otra_persona"

    duena = entrar_como(app, motor_migrate, otro.propietaria)
    # Con sesión pide el CSRF, como toda petición que cambia estado.
    assert duena.post(ACEPTAR, json={"token": token}).status_code == 403
    # Los rechazos no gastaron la invitación.
    r = duena.post(ACEPTAR, json={"token": token, "nombre": "Otro"}, headers=csrf(duena))
    assert r.status_code == 200, r.text
    assert r.json()["cuenta_nueva"] is False
    assert sesiones.COOKIE_SESION not in r.cookies  # sigue con su sesión
    # Conserva su nombre: el de la aceptación es solo para cuentas nuevas.
    assert persona_por_correo(motor_migrate, otro.propietaria).nombre == "Ana"
    ids = {x["id"]: x["vinculos"] for x in duena.get("/api/v1/me/consorcios").json()}
    assert ids == {str(otro.consorcio): ["propietario"], str(e.consorcio): ["ocupante"]}


def test_una_cuenta_nueva_no_acepta_con_la_sesion_de_otra_persona(app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    ana = entrar_como(app, motor_migrate, e.propietaria)
    nuevo = correo_nuevo()
    invitar(ana, e, nuevo)
    r = ana.post(ACEPTAR, json={"token": token_invitacion(motor_migrate, nuevo)}, headers=csrf(ana))
    assert r.status_code == 409
    assert r.json()["codigo"] == "invitacion_de_otra_persona"
    assert persona_por_correo(motor_migrate, nuevo) is None


def test_si_ya_tenia_ese_vinculo_no_se_duplica(app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    ana = entrar_como(app, motor_migrate, e.propietaria)
    invitar(ana, e, e.inquilino)  # el inquilino de u1, otra vez como inquilino de u1
    token = token_invitacion(motor_migrate, e.inquilino)
    ivan = entrar_como(app, motor_migrate, e.inquilino)
    assert ivan.post(ACEPTAR, json={"token": token}, headers=csrf(ivan)).status_code == 200
    with motor_migrate.connect() as conn:
        assert (
            conn.execute(
                text(
                    "select count(*) from nucleo.vinculo v"
                    " join nucleo.persona p on p.id = v.persona_id"
                    " where p.correo = :c and v.unidad_id = :u"
                ),
                {"c": e.inquilino, "u": e.u1},
            ).scalar_one()
            == 1
        )


def test_vencida_inexistente_o_retirada_responden_igual(app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    ana = entrar_como(app, motor_migrate, e.propietaria)
    vence, retira = correo_nuevo(), correo_nuevo()
    invitar(ana, e, vence)
    retirada = invitar(ana, e, retira).json()
    t_vence, t_retira = (token_invitacion(motor_migrate, x) for x in (vence, retira))
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "update nucleo.invitacion set creada_en = now() - interval '8 days',"
                " vence_en = now() - interval '1 day' where token_hash = :t"
            ),
            {"t": h(t_vence)},
        )
    assert ana.post(ruta(e, f"/{retirada['id']}/revocar"), headers=csrf(ana)).status_code == 204
    c = navegador(app)
    respuestas = [
        c.post(x, json={"token": t})
        for x in (CONSULTAR, ACEPTAR)
        for t in (t_vence, t_retira, sesiones.nuevo_token())
    ]
    assert {r.status_code for r in respuestas} == {404}
    assert len({r.json()["mensaje"] for r in respuestas}) == 1
    assert {r.json()["codigo"] for r in respuestas} == {"invitacion_invalida"}
    assert persona_por_correo(motor_migrate, vence) is None


def test_aceptar_desde_otro_sitio_se_rechaza(app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    nuevo = correo_nuevo()
    invitar(entrar_como(app, motor_migrate, e.propietaria), e, nuevo)
    token = token_invitacion(motor_migrate, nuevo)
    c = navegador(app)
    r = c.post(ACEPTAR, json={"token": token}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    # Un formulario de otro sitio no puede mandar JSON.
    assert c.post(ACEPTAR, data={"token": token}).status_code == 422
    assert persona_por_correo(motor_migrate, nuevo) is None
    ok = c.post(ACEPTAR, json={"token": token}, headers={"Sec-Fetch-Site": "same-origin"})
    assert ok.status_code == 200


def test_las_rutas_de_aceptar_estan_en_la_familia_de_autenticacion():
    prefijos = [f.prefijo for f in FAMILIAS]
    for camino in (CONSULTAR, ACEPTAR):
        assert any(camino.startswith(p) for p in prefijos)


# Retirar ----------------------------------------------------------------------------------


def test_retirar(app, motor_migrate):  # noqa: F811
    e = edificio(motor_migrate)
    ana = entrar_como(app, motor_migrate, e.propietaria)
    pendiente = invitar(ana, e, correo_nuevo()).json()
    nuevo = correo_nuevo()
    aceptada = invitar(ana, e, nuevo).json()
    navegador(app).post(ACEPTAR, json={"token": token_invitacion(motor_migrate, nuevo)})

    url = ruta(e, f"/{pendiente['id']}/revocar")
    assert ana.post(url).status_code == 403  # sin CSRF
    assert ana.post(url, headers=csrf(ana)).status_code == 204
    assert ana.post(url, headers=csrf(ana)).status_code == 204  # repetir responde igual
    acciones = [a["accion"] for a in auditoria_de(motor_migrate, UUID(pendiente["id"]))]
    assert acciones == ["identidad.invitacion.crear", "identidad.invitacion.revocar"]

    r = ana.post(ruta(e, f"/{aceptada['id']}/revocar"), headers=csrf(ana))
    assert r.status_code == 409 and r.json()["codigo"] == "invitacion_no_pendiente"

    ivan = entrar_como(app, motor_migrate, e.inquilino)
    assert ivan.post(ruta(e, f"/{pendiente['id']}/revocar"), headers=csrf(ivan)).status_code == 404
    assert ana.post(ruta(e, f"/{uuid4()}/revocar"), headers=csrf(ana)).status_code == 404


def test_otra_propietaria_retira_y_reemplaza_lo_que_invito_la_primera(app, motor_migrate):  # noqa: F811
    """Dos propietarias vigentes de la misma unidad pasan las dos `puede_invitar` y las dos ven
    la lista de esa unidad. Cualquiera de las dos retira o reemplaza lo que invitó la otra. La
    política pide que `invitada_por` sea la persona del ámbito solo al crear."""
    e = edificio(motor_migrate)
    segunda = correo_nuevo()
    with motor_migrate.begin() as c:
        berta_id = c.execute(
            text("insert into nucleo.persona (correo, nombre) values (:c, 'Berta') returning id"),
            {"c": segunda},
        ).scalar_one()
        c.execute(
            text(
                "insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo,"
                " vigente_desde, fuente, origen)"
                " values (:c, :u, :p, 'propietario', '2020-01-01', 'documento', 'soporte')"
            ),
            {"c": e.consorcio, "u": e.u1, "p": berta_id},
        )
    ana = entrar_como(app, motor_migrate, e.propietaria)
    berta = entrar_como(app, motor_migrate, segunda)
    a_retirar, a_reemplazar = correo_nuevo(), correo_nuevo()
    retirada = invitar(ana, e, a_retirar).json()
    reemplazada = invitar(ana, e, a_reemplazar).json()

    r = berta.post(ruta(e, f"/{retirada['id']}/revocar"), headers=csrf(berta))
    assert r.status_code == 204, r.text
    nueva = invitar(berta, e, a_reemplazar)
    assert nueva.status_code == 201, nueva.text
    estados = {x["id"]: x["estado"] for x in ana.get(ruta(e)).json()["elementos"]}
    assert estados[retirada["id"]] == estados[reemplazada["id"]] == "revocada"
    assert estados[nueva.json()["id"]] == "pendiente"
    with motor_migrate.connect() as conn:
        por = conn.execute(
            text("select invitada_por from nucleo.invitacion where id = :i"),
            {"i": nueva.json()["id"]},
        ).scalar_one()
    assert por == berta_id  # quien crea sigue siendo quien está en el ámbito
    acciones = [a["accion"] for a in auditoria_de(motor_migrate, UUID(retirada["id"]))]
    assert acciones == ["identidad.invitacion.crear", "identidad.invitacion.revocar"]


def test_la_propietaria_retira_lo_que_cargo_el_equipo(app, motor_migrate, monkeypatch):  # noqa: F811
    """Una invitación del script (sin persona en `invitada_por`) a una unidad con propietaria:
    ella la ve y la puede retirar."""
    e = edificio(motor_migrate)
    nuevo = correo_nuevo()
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", ORIGEN)
    argumentos = ["--consorcio", str(e.consorcio), "--unidad", str(e.u1), "--tipo", "inquilino"]
    assert script_invitar.main([*argumentos, "--correo", nuevo, "--nombre", "Eva"]) == 0
    ana = entrar_como(app, motor_migrate, e.propietaria)
    [cargada] = [x for x in ana.get(ruta(e)).json()["elementos"] if x["nombre"] == "Eva"]
    assert cargada["correo"] is None  # P6: una carga del equipo no muestra el correo a nadie
    r = ana.post(ruta(e, f"/{cargada['id']}/revocar"), headers=csrf(ana))
    assert r.status_code == 204, r.text


# Base: RLS, privilegios y topes -----------------------------------------------------------


def test_pa15_otro_consorcio_no_ve_ni_crea_invitaciones(app, motor_migrate, motor_app):  # noqa: F811
    e = edificio(motor_migrate)
    otro = edificio(motor_migrate)
    invitar(entrar_como(app, motor_migrate, e.propietaria), e, correo_nuevo())
    with motor_migrate.connect() as conn:
        persona_otro = conn.execute(
            text("select id from nucleo.persona where correo = :c"), {"c": otro.propietaria}
        ).scalar_one()

    def contar(consorcio) -> int:
        with motor_app.begin() as conn:
            conn.execute(
                text("select set_config('app.consorcio_id', :c, true)"),
                {"c": str(consorcio) if consorcio else ""},
            )
            return conn.execute(text("select count(*) from nucleo.invitacion")).scalar_one()

    assert contar(e.consorcio) == 1  # control
    assert contar(otro.consorcio) == 0
    assert contar(None) == 0  # sin ámbito, nada
    # Insertar en el consorcio ajeno con el ámbito propio: la política lo rechaza.
    with pytest.raises(sqlalchemy.exc.ProgrammingError, match="row-level security"):
        with motor_app.begin() as conn:
            conn.execute(
                text(
                    "select set_config('app.consorcio_id', :c, true),"
                    " set_config('app.persona_id', :p, true)"
                ),
                {"c": str(otro.consorcio), "p": str(persona_otro)},
            )
            conn.execute(
                text(
                    "insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo,"
                    " nombre, token_hash, invitada_por, vence_en) values"
                    " (:c, :u, 'inquilino', 'x@ejemplo.com', 'X', :h, :p, now() + interval '1 day')"
                ),
                {"c": e.consorcio, "u": e.u1, "h": h(sesiones.nuevo_token()), "p": persona_otro},
            )


def test_la_politica_exige_que_invitada_por_sea_la_persona_del_ambito(motor_migrate, motor_app):
    e = edificio(motor_migrate)
    with motor_migrate.connect() as conn:
        ana = conn.execute(
            text("select id from nucleo.persona where correo = :c"), {"c": e.propietaria}
        ).scalar_one()
    with pytest.raises(sqlalchemy.exc.ProgrammingError, match="row-level security"):
        with motor_app.begin() as conn:
            conn.execute(
                text("select set_config('app.consorcio_id', :c, true)"), {"c": str(e.consorcio)}
            )
            conn.execute(
                text(
                    "insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo,"
                    " nombre, token_hash, invitada_por, vence_en) values"
                    " (:c, :u, 'inquilino', 'x@ejemplo.com', 'X', :h, :p, now() + interval '1 day')"
                ),
                {"c": e.consorcio, "u": e.u1, "h": h(sesiones.nuevo_token()), "p": ana},
            )


def test_cem_app_solo_cambia_revocada_en(motor_app):
    with pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"):
        with motor_app.begin() as conn:
            conn.execute(text("update nucleo.invitacion set aceptada_en = now()"))
    with pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"):
        with motor_app.begin() as conn:
            conn.execute(text("delete from nucleo.invitacion"))


def test_la_base_impone_siete_dias_como_maximo(motor_migrate):
    e = edificio(motor_migrate)
    with pytest.raises(sqlalchemy.exc.IntegrityError, match="check"):
        with motor_migrate.begin() as conn:
            conn.execute(
                text(
                    "insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo,"
                    " nombre, token_hash, vence_en) values"
                    " (:c, :u, 'inquilino', 'x@ejemplo.com', 'X', :h, now() + :v)"
                ),
                {
                    "c": e.consorcio,
                    "u": e.u1,
                    "h": h(sesiones.nuevo_token()),
                    "v": invitaciones.VIGENCIA_INVITACION + timedelta(seconds=1),
                },
            )


def test_dos_invitaciones_al_mismo_correo_nuevo_aceptadas_a_la_vez_crean_una_persona(
    app,  # noqa: F811
    motor_migrate,
    motor_app,
):
    """Dos unidades, el mismo correo sin cuenta, dos aceptaciones simultáneas: una crea la
    persona; la otra la encuentra y pide sesión, sin error."""
    e = edificio(motor_migrate)
    nuevo = correo_nuevo()
    ana = entrar_como(app, motor_migrate, e.propietaria)
    invitar(ana, e, nuevo)
    with motor_migrate.begin() as conn:  # Ana también es propietaria de u2
        conn.execute(
            text(
                "insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo,"
                " vigente_desde, fuente, origen)"
                " select :c, :u, id, 'propietario', '2020-01-01', 'documento', 'soporte'"
                " from nucleo.persona where correo = :p"
            ),
            {"c": e.consorcio, "u": e.u2, "p": e.propietaria},
        )
    invitar(ana, e, nuevo, unidad=e.u2)
    tokens = [
        re.search(r"#token=([A-Za-z0-9_-]{43})", x["carga"]["texto"]).group(1)
        for x in correos_a(motor_migrate, nuevo)
    ]
    barrera = threading.Barrier(2)
    resultados: list[str] = []

    def aceptar(token: str) -> None:
        with motor_app.begin() as conn:
            barrera.wait()
            resultados.append(
                conn.execute(
                    text(
                        "select resultado from nucleo.aceptar_invitacion(:t, null, null, :s,"
                        " :d, null, '192.0.2.1', interval '30 days')"
                    ),
                    {"t": h(token), "s": h(sesiones.nuevo_token()), "d": uuid4()},
                ).scalar_one()
            )

    hilos = [threading.Thread(target=aceptar, args=(t,)) for t in tokens]
    for x in hilos:
        x.start()
    for x in hilos:
        x.join()
    assert sorted(resultados) == ["ok", "requiere_sesion"]
    with motor_migrate.connect() as conn:
        assert (
            conn.execute(
                text("select count(*) from nucleo.persona where correo = :c"), {"c": nuevo}
            ).scalar_one()
            == 1
        )


# Alta por el equipo de la plataforma -----------------------------------------------------


def test_el_script_invita_sin_persona_y_con_origen_soporte(motor_migrate, monkeypatch, capsys):
    e = edificio(motor_migrate)
    nuevo = correo_nuevo()
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", ORIGEN)
    argumentos = ["--consorcio", str(e.consorcio), "--tipo", "propietario", "--correo", nuevo]
    assert script_invitar.main([*argumentos, "--unidad", str(e.u2), "--nombre", "Eva"]) == 0
    assert token_invitacion(motor_migrate, nuevo) not in capsys.readouterr().out
    carga = correos_a(motor_migrate, nuevo)[-1]["carga"]
    assert "El equipo de Consorcio en Mano te invitó" in carga["texto"]
    with motor_migrate.connect() as conn:
        id_, por = conn.execute(
            text("select id, invitada_por from nucleo.invitacion where correo = :c"), {"c": nuevo}
        ).one()
    assert por is None
    [aud] = auditoria_de(motor_migrate, id_)
    assert (aud["origen"], aud["actor_persona_id"]) == ("soporte", None)
    # Una unidad de otro consorcio: no la ve, no crea nada.
    otro = edificio(motor_migrate)
    assert script_invitar.main([*argumentos, "--unidad", str(otro.u1), "--nombre", "Eva"]) == 1


def test_el_texto_del_correo_queda_en_una_linea_por_dato():
    capturado = {}

    def solicitar(conn, **kw):
        capturado.update(kw)

    import app.identidad.eventos as ev

    original = ev.correo.solicitar
    ev.correo.solicitar = solicitar
    try:
        eventos.solicitar_invitacion(
            None,
            consorcio_id=uuid4(),
            para="x@ejemplo.com",
            nombre="Beto",
            quien="Ana\nPérez",
            consorcio="Edificio\r\nAlvear",
            unidad="3° B",
            tipo="ocupante",
            enlace="https://x/invitacion#token=t",
            vence="01/01/2027 a las 10:00",
        )
    finally:
        ev.correo.solicitar = original
    assert capturado["asunto"] == "Te invitaron a Edificio Alvear en Consorcio en Mano"
    assert "Ana Pérez te invitó a sumarte a Edificio Alvear" in capturado["texto"]
