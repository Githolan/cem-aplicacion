"""Rutas de 2b-4 (PR 2): padrón, confirmación de vínculos y correo de las invitaciones (P6).

Diseño: `doc/diseno/H2b_4_Diseno.md` de CEM, §6.5, §10 y §0 (P4, P5, P6).
"""

import asyncio
import threading
import time
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from app.autorizacion.evaluador import Decision
from app.autorizacion.requiere import Permiso
from app.config import Config
from app.consorcios.padron import Discrepancia
from app.contexto import Identidad, obtener_identidad
from app.main import crear_app
from app.plataforma.idempotencia import ENCABEZADO_REPETIDA
from tests.conftest import REDIS_URL, clave_idempotencia, url_rol
from tests.test_contexto_mandatos import (  # noqa: F401  (`edificio` es un fixture)
    cliente,
    denegaciones,
    edificio,
    invitacion,
    segundo_consorcio_con_mandato,
)
from tests.test_identidad import (  # noqa: F401  (`app` es un fixture)
    app,
    csrf,
    navegador,
)
from tests.test_identidad_cierre_y_purga import entrar_como
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import cambiar
from tests.test_vinculo_0014 import invitar_y_aceptar

pytestmark = pytest.mark.db

REFERENCIA = "Escritura 1234, registro notarial 56"
COLUMNAS_D6 = {"vinculo_id", "unidad_id", "designacion", "nombre", "tipo", "vigente_desde",
               "vigente_hasta", "confirmado"}  # fmt: skip
COLUMNAS_CONFIRMADO = {"vinculo_id", "unidad_id", "tipo", "confirmado", "confirmado_en"}


def padron_url(cons) -> str:
    return f"/api/v1/consorcios/{cons}/padron"


def confirmar_url(cons, vinculo) -> str:
    return f"/api/v1/consorcios/{cons}/vinculos/{vinculo}/confirmar"


def invitaciones_url(cons) -> str:
    return f"/api/v1/consorcios/{cons}/invitaciones"


@pytest.fixture
def escena(motor_migrate, motor_app, edificio):  # noqa: F811
    """El edificio con modo, un administrador y un miembro del Consejo. El inquilino de u1 lo
    invitó el Consejo; el de u2, el administrador."""
    e = dict(edificio)
    cambiar(motor_app, e["cons"], e["prop1"], "administrador_aumentado", e["acta"])
    e["admin"] = otra_persona(motor_migrate, e["cons"])
    e["mc"] = otra_persona(motor_migrate, e["cons"])
    # Por soporte: el padrón no depende de cómo se cargó el mandato (la vía de la API, desde
    # 0018, agregaría un vínculo confirmado que el padrón mostraría).
    e["m_admin"] = alta(motor_app, e["cons"], None, e["acta"], persona=e["admin"], origen="soporte")
    alta(motor_app, e["cons"], None, e["acta"], persona=e["mc"], tipo="miembro_consejo",
         origen="soporte")  # fmt: skip
    e["v_inq_mc"] = invitar_y_aceptar(motor_migrate, e["cons"], e["u1"], e["mc"])
    e["v_inq_admin"] = invitar_y_aceptar(motor_migrate, e["cons"], e["u2"], e["admin"])
    with motor_migrate.connect() as c:
        e["v_prop1"] = c.execute(
            text("select id from nucleo.vinculo where persona_id = :p"), {"p": e["prop1"]}
        ).scalar_one()
    return e


def confirmar(c, cons, vinculo, *, referencia=REFERENCIA, clave=None):
    return c.post(
        confirmar_url(cons, vinculo),
        json={"referencia": referencia},
        headers=clave or clave_idempotencia(),
    )


def fila_vinculo(motor_migrate, vinculo) -> dict:
    with motor_migrate.connect() as c:
        return dict(
            c.execute(
                text(
                    "select confirmado_por, confirmado_en, confirmacion_referencia"
                    " from nucleo.vinculo where id = :v"
                ),
                {"v": vinculo},
            )
            .mappings()
            .one()
        )


def eventos_confirmar(motor_migrate, vinculo) -> list[dict]:
    with motor_migrate.connect() as c:
        return [
            dict(f._mapping)
            for f in c.execute(
                text(
                    "select actor_persona_id, despues, e::text as todo from auditoria.eventos e"
                    " where accion = 'consorcio.vinculo.confirmar' and entidad_id = :v"
                ),
                {"v": vinculo},
            )
        ]


def acotar(motor_migrate, mandato, unidad) -> None:
    """Las facultades de padrón del mandato pasan a alcanzar solo esa unidad (recurso A8)."""
    with motor_migrate.begin() as c:
        n = c.execute(
            text(
                "update nucleo.facultad_otorgada set recurso = :r where mandato_id = :m"
                " and facultad in ('padron.ver', 'padron.confirmar_vinculo')"
            ),
            {"r": f"unidad:{unidad}", "m": mandato},
        ).rowcount
    assert n == 2


# Padrón -------------------------------------------------------------------------------------


def test_el_padron_trae_solo_d6_de_vigentes_y_futuros(motor_migrate, escena):
    e = escena
    with cliente(e["admin"]) as c:
        r = c.get(padron_url(e["cons"]))
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    filas = r.json()["elementos"]
    assert {f["vinculo_id"] for f in filas} == {
        str(e["v_prop1"]), str(e["v_inq_mc"]), str(e["v_inq_admin"]),
        *(str(v) for v in vinculos_de(motor_migrate, e["prop2"])),
    }  # fmt: skip
    assert all(set(f) == COLUMNAS_D6 for f in filas)
    assert "@" not in r.text  # ningún correo


def vinculos_de(motor_migrate, persona) -> list[UUID]:
    with motor_migrate.connect() as c:
        return list(
            c.execute(
                text("select id from nucleo.vinculo where persona_id = :p"), {"p": persona}
            ).scalars()
        )


def test_sin_padron_ver_responde_403_y_audita(motor_app, escena):
    e = escena
    with cliente(e["prop1"]) as c:
        r = c.get(padron_url(e["cons"]))
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    [d] = denegaciones(motor_app, e["cons"], e["admin"], e["prop1"])
    assert d["accion"] == "padron.ver"
    with cliente(e["mc"]) as c:  # control: el Consejo tiene padron.ver
        assert c.get(padron_url(e["cons"])).status_code == 200


def test_el_padron_de_otro_consorcio_responde_404(motor_migrate, motor_app, escena):
    e = escena
    cons_b, _ = segundo_consorcio_con_mandato(motor_migrate, motor_app, e["mc"])
    with cliente(e["admin"]) as c:
        assert c.get(padron_url(cons_b)).status_code == 404
    with cliente(e["mc"]) as c:  # control: en B tiene mandato y ve el padrón de B, no el de A
        r = c.get(padron_url(cons_b))
    assert r.status_code == 200
    assert str(e["v_inq_mc"]) not in r.text


def test_una_facultad_acotada_ve_solo_su_unidad(motor_migrate, escena):
    e = escena
    acotar(motor_migrate, e["m_admin"], e["u2"])
    with cliente(e["admin"]) as c:
        filas = c.get(padron_url(e["cons"])).json()["elementos"]
    assert filas and {f["unidad_id"] for f in filas} == {str(e["u2"])}


def test_si_la_base_niega_y_el_evaluador_concede_falla_fuerte(escena, monkeypatch):
    e = escena
    monkeypatch.setattr(
        Permiso,
        "verificar",
        lambda self, entidad=None, **kw: Decision(True, 200, "concedido_por_facultad"),
    )
    with cliente(e["prop1"]) as c, pytest.raises(Discrepancia):
        c.get(padron_url(e["cons"]))


def test_padron_sin_sesion_responde_401(app, motor_migrate, escena):  # noqa: F811
    assert navegador(app).get(padron_url(escena["cons"])).status_code == 401


# Confirmación -------------------------------------------------------------------------------


def test_confirmar_responde_lo_minimo_guarda_y_audita_sin_la_referencia(motor_migrate, escena):
    e = escena
    with cliente(e["admin"]) as c:
        r = confirmar(c, e["cons"], e["v_inq_mc"])
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert set(cuerpo) == COLUMNAS_CONFIRMADO
    assert (cuerpo["vinculo_id"], cuerpo["unidad_id"], cuerpo["confirmado"]) == (
        str(e["v_inq_mc"]), str(e["u1"]), True,
    )  # fmt: skip
    assert REFERENCIA not in r.text
    fila = fila_vinculo(motor_migrate, e["v_inq_mc"])
    assert (fila["confirmado_por"], fila["confirmacion_referencia"]) == (e["admin"], REFERENCIA)
    [evento] = eventos_confirmar(motor_migrate, e["v_inq_mc"])
    assert evento["actor_persona_id"] == e["admin"]
    assert "Escritura" not in evento["todo"]
    with cliente(e["admin"]) as c:  # el padrón lo muestra confirmado
        filas = {f["vinculo_id"]: f for f in c.get(padron_url(e["cons"])).json()["elementos"]}
    assert filas[str(e["v_inq_mc"])]["confirmado"] is True
    assert filas[str(e["v_inq_admin"])]["confirmado"] is False


def test_un_reintento_con_la_misma_clave_no_repite_el_efecto(motor_migrate, escena):
    e = escena
    clave = clave_idempotencia()
    with cliente(e["admin"]) as c:
        primera = confirmar(c, e["cons"], e["v_prop1"], clave=clave)
        segunda = confirmar(c, e["cons"], e["v_prop1"], clave=clave)
        otra = confirmar(c, e["cons"], e["v_prop1"], referencia="Otra escritura", clave=clave)
        nueva = confirmar(c, e["cons"], e["v_prop1"])
    assert primera.status_code == segunda.status_code == 200
    assert segunda.headers[ENCABEZADO_REPETIDA] == "true"
    assert segunda.json() == primera.json()
    assert len(eventos_confirmar(motor_migrate, e["v_prop1"])) == 1
    assert (otra.status_code, otra.json()["codigo"]) == (422, "idempotencia_reutilizada")
    # Control: con otra clave ya no es un reintento, y el vínculo ya está confirmado.
    assert (nueva.status_code, nueva.json()["codigo"]) == (409, "vinculo_ya_confirmado")


def test_quien_registro_el_vinculo_no_lo_confirma(motor_migrate, motor_app, escena):
    e = escena
    with cliente(e["admin"]) as c:
        r = confirmar(c, e["cons"], e["v_inq_admin"])
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    [d] = denegaciones(motor_app, e["cons"], e["admin"], e["admin"])
    assert (d["accion"], d["motivo"]) == ("padron.confirmar_vinculo", "no_es_quien_registro")
    assert fila_vinculo(motor_migrate, e["v_inq_admin"])["confirmado_en"] is None
    with cliente(e["admin"]) as c:  # control: el que registró el Consejo sí lo confirma
        assert confirmar(c, e["cons"], e["v_inq_mc"]).status_code == 200


def test_nadie_confirma_su_propio_vinculo(motor_migrate, motor_app, escena):
    e = escena
    propio = invitar_y_aceptar(motor_migrate, e["cons"], e["u1"], e["mc"], persona=e["admin"])
    with cliente(e["admin"]) as c:
        r = confirmar(c, e["cons"], propio)
    assert r.status_code == 403
    [d] = denegaciones(motor_app, e["cons"], e["admin"], e["admin"])
    assert d["motivo"] == "no_es_su_propio_vinculo"


def test_un_vinculo_del_equipo_lo_confirma_quien_tiene_la_facultad(motor_migrate, escena):
    e = escena
    with cliente(e["admin"]) as c:
        r = confirmar(c, e["cons"], e["v_prop1"])
    assert r.status_code == 200, r.text
    assert fila_vinculo(motor_migrate, e["v_prop1"])["confirmado_por"] == e["admin"]


def test_sin_la_facultad_o_fuera_de_su_unidad_responde_403(motor_migrate, motor_app, escena):
    e = escena
    with cliente(e["mc"]) as c:  # el Consejo no tiene padron.confirmar_vinculo
        assert confirmar(c, e["cons"], e["v_prop1"]).status_code == 403
    with cliente(e["prop2"]) as c:
        assert confirmar(c, e["cons"], e["v_prop1"]).status_code == 403
    acotar(motor_migrate, e["m_admin"], e["u2"])
    with cliente(e["admin"]) as c:
        r = confirmar(c, e["cons"], e["v_prop1"])  # u1, fuera de su recurso
    assert r.status_code == 403
    [d] = denegaciones(motor_app, e["cons"], e["admin"], e["admin"])
    assert d["motivo"] == "recurso_no_alcanza"
    assert fila_vinculo(motor_migrate, e["v_prop1"])["confirmado_en"] is None


def test_un_vinculo_inexistente_o_de_otro_consorcio_responde_404(motor_migrate, motor_app, escena):
    """También el vínculo propio de la persona en otro consorcio, que la RLS `propios` sí le deja
    leer: la lectura filtra por el consorcio de la ruta (sin el filtro sería un 403)."""
    e = escena
    cons_b, _ = segundo_consorcio_con_mandato(motor_migrate, motor_app, e["admin"])
    with motor_migrate.connect() as c:
        ajeno, unidad_b = c.execute(
            text("select id, unidad_id from nucleo.vinculo where consorcio_id = :c limit 1"),
            {"c": cons_b},
        ).one()
    propio_en_b = invitar_y_aceptar(motor_migrate, cons_b, unidad_b, None, persona=e["admin"])
    with cliente(e["admin"]) as c:
        for vinculo in (uuid4(), ajeno, propio_en_b):
            r = confirmar(c, e["cons"], vinculo)
            assert (r.status_code, r.json()["codigo"]) == (404, "no_encontrado")
    assert fila_vinculo(motor_migrate, ajeno)["confirmado_en"] is None


def test_un_vinculo_terminado_responde_409(motor_migrate, escena):
    e = escena
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.vinculo set vigente_hasta = '2021-01-01' where id = :v"),
            {"v": e["v_prop1"]},
        )
    with cliente(e["admin"]) as c:
        r = confirmar(c, e["cons"], e["v_prop1"])
    assert (r.status_code, r.json()["codigo"]) == (409, "vinculo_finalizado")


@pytest.mark.parametrize("referencia", ["", "   ", "a\nb", "x" * 301])
def test_la_referencia_es_obligatoria_y_acotada(motor_migrate, escena, referencia):
    e = escena
    with cliente(e["admin"]) as c:
        r = confirmar(c, e["cons"], e["v_prop1"], referencia=referencia)
    assert (r.status_code, r.json()["codigo"]) == (422, "peticion_invalida")
    assert fila_vinculo(motor_migrate, e["v_prop1"])["confirmado_en"] is None


def test_confirmar_pide_la_clave_de_reintento(motor_migrate, escena):
    e = escena
    with cliente(e["admin"]) as c:
        r = c.post(confirmar_url(e["cons"], e["v_prop1"]), json={"referencia": REFERENCIA})
    assert r.status_code == 422
    assert fila_vinculo(motor_migrate, e["v_prop1"])["confirmado_en"] is None


def test_confirmar_pide_sesion_y_csrf(app, motor_migrate, escena):  # noqa: F811
    e = escena
    url = confirmar_url(e["cons"], e["v_prop1"])
    cuerpo = {"referencia": REFERENCIA}
    assert navegador(app).post(url, json=cuerpo, headers=clave_idempotencia()).status_code == 401
    with motor_migrate.connect() as c:
        correo = c.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": e["admin"]}
        ).scalar_one()
    admin = entrar_como(app, motor_migrate, correo)
    assert admin.post(url, json=cuerpo, headers=clave_idempotencia()).status_code == 403
    assert fila_vinculo(motor_migrate, e["v_prop1"])["confirmado_en"] is None
    r = admin.post(url, json=cuerpo, headers=csrf(admin) | clave_idempotencia())  # control
    assert r.status_code == 200, r.text


# P6: el correo de las invitaciones -----------------------------------------------------------


def listar(persona, cons) -> dict[str, dict]:
    with cliente(persona) as c:
        r = c.get(invitaciones_url(cons))
    assert r.status_code == 200, r.text
    return {i["id"]: i for i in r.json()["elementos"]}


def invitar(persona, cons, unidad, quien="") -> dict:
    with cliente(persona) as c:
        r = c.post(invitaciones_url(cons), json=invitacion(unidad, quien),
                   headers=clave_idempotencia())  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


def test_p6_quien_creo_la_pendiente_ve_su_correo(escena):
    e = escena
    inv = invitar(e["prop1"], e["cons"], e["u1"])
    assert inv["correo"]
    assert listar(e["prop1"], e["cons"])[inv["id"]]["correo"] == inv["correo"]


def test_p6_otro_autorizado_que_no_la_creo_recibe_null(escena):
    e = escena
    inv = invitar(e["prop1"], e["cons"], e["u1"])
    fila = listar(e["admin"], e["cons"])[inv["id"]]  # facultad general, no la creó
    assert fila["correo"] is None and fila["estado"] == "pendiente"


def test_p6_la_propietaria_de_la_unidad_que_no_invito_recibe_null(escena):
    e = escena
    inv = invitar(e["admin"], e["cons"], e["u1"])
    assert listar(e["prop1"], e["cons"])[inv["id"]]["correo"] is None
    assert listar(e["admin"], e["cons"])[inv["id"]]["correo"] == inv["correo"]  # control


@pytest.mark.parametrize("estado", ["aceptada", "vencida", "revocada"])
def test_p6_aceptada_vencida_o_revocada_es_null_tambien_para_quien_la_creo(
    motor_migrate, escena, estado
):
    e = escena
    inv = invitar(e["prop1"], e["cons"], e["u1"])
    cambio = {
        "aceptada": ("aceptada_en = now(), persona_id = :p", {"p": e["prop2"]}),
        "vencida": ("creada_en = now() - interval '8 days', vence_en = now() - interval '1 day'",
                    {}),
        "revocada": ("revocada_en = now()", {}),
    }[estado]  # fmt: skip
    with motor_migrate.begin() as c:
        c.execute(
            text(f"update nucleo.invitacion set {cambio[0]} where id = :i"),
            {"i": inv["id"]} | cambio[1],
        )
    fila = listar(e["prop1"], e["cons"])[inv["id"]]
    assert (fila["estado"], fila["correo"]) == (estado, None)


def test_p6_otro_consorcio_no_la_ve(motor_migrate, motor_app, escena):
    e = escena
    inv = invitar(e["admin"], e["cons"], e["u1"])
    cons_b, _ = segundo_consorcio_con_mandato(motor_migrate, motor_app, e["mc"])
    assert inv["id"] not in listar(e["mc"], cons_b)
    with cliente(e["nadie"]) as c:
        assert c.get(invitaciones_url(e["cons"])).status_code == 404


def test_invitar_con_la_misma_clave_no_manda_otro_correo_ni_gasta_cupo(
    motor_migrate, escena, monkeypatch
):
    from limits import parse

    monkeypatch.setattr(
        "app.limite.LIMITES_INVITACIONES_PERSONA", (parse("1/hour"), parse("50/day"))
    )
    e = escena
    clave = clave_idempotencia()
    cuerpo = invitacion(e["u1"], "idem")
    url = invitaciones_url(e["cons"])
    with cliente(e["prop1"]) as c:
        primera = c.post(url, json=cuerpo, headers=clave)
        segunda = c.post(url, json=cuerpo, headers=clave)  # el cupo de 1 ya se gastó
        otra = c.post(url, json=invitacion(e["u1"], "otra"), headers=clave_idempotencia())
    assert primera.status_code == segunda.status_code == 201, segunda.text
    assert segunda.headers[ENCABEZADO_REPETIDA] == "true"
    assert segunda.json() == primera.json()
    assert segunda.json()["correo"] == cuerpo["correo"]  # excepción acotada de P6
    assert otra.status_code == 429  # control: un pedido nuevo sí choca con el cupo
    with motor_migrate.connect() as c:
        enviados = c.execute(
            text("select count(*) from plataforma.outbox where carga->>'para' = :p"),
            {"p": cuerpo["correo"]},
        ).scalar_one()
    assert enviados == 1


def test_dos_pedidos_a_la_vez_con_la_misma_clave_gastan_un_solo_cupo(
    motor_migrate, escena, monkeypatch
):
    """El segundo llega mientras el primero sigue en la transacción: espera el bloqueo de la
    clave y recibe la respuesta guardada, sin chocar con el cupo de 1 que ya gastó el primero."""
    from limits import parse

    from app.identidad import invitaciones as modulo

    monkeypatch.setattr(
        "app.limite.LIMITES_INVITACIONES_PERSONA", (parse("1/hour"), parse("50/day"))
    )
    original = modulo.crear
    dentro = threading.Event()

    def crear_lento(*args, **kwargs):
        dentro.set()
        time.sleep(0.5)  # el segundo pedido llega con el primero todavía en curso
        return original(*args, **kwargs)

    monkeypatch.setattr(modulo, "crear", crear_lento)
    e = escena
    clave = clave_idempotencia()
    cuerpo = invitacion(e["u1"], "a-la-vez")
    url = invitaciones_url(e["cons"])
    respuestas = {}
    with cliente(e["prop1"]) as c:

        def pedir(nombre):
            respuestas[nombre] = c.post(url, json=cuerpo, headers=clave)

        primero = threading.Thread(target=pedir, args=("primero",))
        primero.start()
        assert dentro.wait(5)
        pedir("segundo")
        primero.join()
    assert respuestas["primero"].status_code == 201, respuestas["primero"].text
    assert respuestas["segundo"].status_code == 201, respuestas["segundo"].text
    assert respuestas["segundo"].headers[ENCABEZADO_REPETIDA] == "true"
    assert respuestas["segundo"].json() == respuestas["primero"].json()
    with motor_migrate.connect() as conn:
        enviados = conn.execute(
            text("select count(*) from plataforma.outbox where carga->>'para' = :p"),
            {"p": cuerpo["correo"]},
        ).scalar_one()
    assert enviados == 1


def test_dos_pedidos_en_el_mismo_bucle_con_la_misma_clave_no_se_traban(
    motor_migrate, escena, monkeypatch, redis_limpio
):
    """Los dos pedidos corren en el mismo bucle de eventos, con el cupo en Redis de verdad: el
    segundo no puede frenar el bucle esperando el bloqueo de la clave mientras el primero
    espera a Redis (Codex). Los dos reciben 201 (el segundo, repetido), con un solo correo."""
    from limits import parse

    from app import limite

    monkeypatch.setattr(
        "app.limite.LIMITES_INVITACIONES_PERSONA", (parse("5/hour"), parse("50/day"))
    )
    original = limite.consumir

    async def consumir_lento(*args, **kwargs):
        # El primero espera el cupo (sin frenar el bucle) cuando llega el segundo.
        await asyncio.sleep(0.5)
        return await original(*args, **kwargs)

    monkeypatch.setattr(limite, "consumir", consumir_lento)
    e = escena
    aplicacion = crear_app(
        Config(url_rol("app"), "0.1.0", "sha", "sha", redis_url=REDIS_URL,
               origen_publico="https://cem.ejemplo.com")
    )  # fmt: skip
    aplicacion.dependency_overrides[obtener_identidad] = lambda: Identidad(e["prop1"])
    clave = clave_idempotencia()
    cuerpo = invitacion(e["u1"], "mismo-bucle")

    async def dos_pedidos():
        transporte = httpx.ASGITransport(app=aplicacion, client=("198.51.100.31", 40000))
        async with httpx.AsyncClient(transport=transporte, base_url="http://cem") as c:
            url = invitaciones_url(e["cons"])
            primero = asyncio.create_task(c.post(url, json=cuerpo, headers=clave))
            await asyncio.sleep(0.1)
            segundo = await c.post(url, json=cuerpo, headers=clave)
            return await primero, segundo

    primero, segundo = asyncio.run(dos_pedidos())
    assert (primero.status_code, segundo.status_code) == (201, 201), segundo.text
    assert segundo.headers[ENCABEZADO_REPETIDA] == "true"
    assert segundo.json() == primero.json()
    with motor_migrate.connect() as conn:
        enviados = conn.execute(
            text("select count(*) from plataforma.outbox where carga->>'para' = :p"),
            {"p": cuerpo["correo"]},
        ).scalar_one()
    assert enviados == 1


def test_invitar_pide_la_clave_de_reintento(motor_migrate, escena):
    e = escena
    cuerpo = invitacion(e["u1"], "sin")
    with cliente(e["prop1"]) as c:
        r = c.post(invitaciones_url(e["cons"]), json=cuerpo)
    assert r.status_code == 422
    with motor_migrate.connect() as c:
        assert (
            c.execute(
                text("select count(*) from nucleo.invitacion where correo = :c"),
                {"c": cuerpo["correo"]},
            ).scalar_one()
            == 0
        )
