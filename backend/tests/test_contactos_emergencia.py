"""Contactos de emergencia (H2b 2b-7; RF06, primera parte; migración 0031).

Diseño: `doc/diseno/H2b_7_Diseno.md` del repositorio CEM, con la decisión del propietario del
25-10-2026 (§1.1). Cada negación lleva su control: el mismo pedido de quien sí puede, o el mismo
dato sin lo que lo hace inválido, pasa.
"""

from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.autorizacion.evaluador import Decision
from app.autorizacion.requiere import Permiso
from app.consorcios.padron import Discrepancia
from app.db import transaccion
from tests.conftest import clave_idempotencia
from tests.test_contexto_mandatos import cliente, denegaciones
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import cambiar, consorcio_con_persona, decidir_equipo

pytestmark = pytest.mark.db

EMPRESA = {
    "funcion": "emergencias",
    "naturaleza": "empresa",
    "nombre": "Ascensores del Sur",
    "telefono": "0800-555-1234",
    "indicaciones": "Ascensor detenido: llamar las 24 horas",
}
PROFESIONAL = {
    "funcion": "encargado",
    "naturaleza": "profesional",
    "nombre": "Encargado del edificio",
    "telefono": "11 4000 0000",
    "consentimiento_referencia": "Nota firmada del 20-10",
}
LECTURA = {
    "id", "funcion", "naturaleza", "nombre", "telefono", "indicaciones", "vigente_desde",
    "vigente_hasta", "actualizado_en", "verificado_en", "observado",
}  # fmt: skip
CAMPOS_AUDITORIA = {"funcion", "naturaleza", "tiene_telefono", "vigente_desde", "vigente_hasta"}


@pytest.fixture
def escena(motor_migrate, motor_app):
    """Consorcio en Modo Consejo con su propietaria (sin mandato) y un miembro del Consejo."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    cambiar(motor_app, cons, "consejo", acta)
    mc = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, None, acta, persona=mc, tipo="miembro_consejo", origen="soporte")
    return {"cons": cons, "prop": prop, "mc": mc, "acta": acta}


def url(cons, resto=""):
    return f"/api/v1/consorcios/{cons}/contactos{resto}"


def crear(persona, cons, **datos):
    with cliente(persona) as c:
        return c.post(url(cons), json=EMPRESA | datos, headers=clave_idempotencia())


def accion(persona, cons, contacto, verbo, cuerpo=None):
    with cliente(persona) as c:
        return c.post(url(cons, f"/{contacto}/{verbo}"), json=cuerpo or {},
                      headers=clave_idempotencia())  # fmt: skip


def leer(persona, cons, resto=""):
    with cliente(persona) as c:
        return c.get(url(cons, resto))


def eventos(motor_migrate, cons):
    with motor_migrate.connect() as c:
        return c.execute(
            text("select accion, antes, despues from auditoria.eventos where consorcio_id = :c"
                 " and accion like 'consorcio.contacto.%' order by secuencia"),
            {"c": cons},
        ).all()  # fmt: skip


@pytest.mark.redis
def test_el_consejo_agrega_y_todos_leen_lo_publicado(escena):
    e = escena
    r = crear(e["mc"], e["cons"])
    assert r.status_code == 201, r.text
    r = leer(e["prop"], e["cons"])
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    [k] = r.json()["elementos"]
    assert set(k) == LECTURA  # sin quién cambió qué, la observación ni la constancia
    assert (k["nombre"], k["telefono"], k["observado"]) == ("Ascensores del Sur", "0800-555-1234",
                                                            False)  # fmt: skip
    assert r.json()["actualizada_en"] == k["actualizado_en"]
    assert r.json()["zona_horaria"] == "America/Argentina/Buenos_Aires"


@pytest.mark.redis
def test_un_contacto_vencido_o_futuro_no_figura_como_vigente(escena):
    e = escena
    vigente = crear(e["mc"], e["cons"]).json()["id"]
    futuro = crear(e["mc"], e["cons"], nombre="Guardia nueva",
                   vigente_desde=str(date.today() + timedelta(days=30))).json()["id"]  # fmt: skip
    # Control: el vigente figura; el futuro, solo en la lista de gestión.
    assert [k["id"] for k in leer(e["prop"], e["cons"]).json()["elementos"]] == [vigente]
    gestion = {k["id"] for k in leer(e["mc"], e["cons"], "/gestion").json()["elementos"]}
    assert gestion == {vigente, futuro}
    antes = leer(e["prop"], e["cons"]).json()["actualizada_en"]
    assert accion(e["mc"], e["cons"], vigente, "retirar").status_code == 200
    despues = leer(e["prop"], e["cons"]).json()
    assert despues["elementos"] == []
    # El retiro también cambia la lista: su fecha avanza aunque ya no quede ninguno.
    assert despues["actualizada_en"] > antes
    # Retirado no se toca más: se agrega otro.
    r = accion(e["mc"], e["cons"], vigente, "verificar")
    assert (r.status_code, r.json()["codigo"]) == (409, "contacto_terminado")
    # Retirar uno futuro hace que nunca llegue a figurar.
    assert accion(e["mc"], e["cons"], futuro, "retirar").status_code == 200
    assert leer(e["mc"], e["cons"], "/gestion").json()["elementos"] == []


@pytest.mark.redis
def test_cada_cambio_queda_auditado_sin_datos_de_contacto(escena, motor_migrate):
    e = escena
    k = crear(e["mc"], e["cons"]).json()["id"]
    with cliente(e["mc"]) as c:
        r = c.put(url(e["cons"], f"/{k}"), json=EMPRESA | {"telefono": "0800-555-9999"},
                  headers=clave_idempotencia())  # fmt: skip
    assert r.status_code == 200, r.text
    assert accion(e["mc"], e["cons"], k, "senalar", {"motivo": "La guardia cambió"}).status_code \
        == 200  # fmt: skip
    [g] = leer(e["mc"], e["cons"], "/gestion").json()["elementos"]
    assert (g["observacion"], g["observado_por"]) == ("La guardia cambió", str(e["mc"]))
    assert leer(e["prop"], e["cons"]).json()["elementos"][0]["observado"] is True
    assert accion(e["mc"], e["cons"], k, "verificar").status_code == 200
    [g] = leer(e["mc"], e["cons"], "/gestion").json()["elementos"]
    assert (g["observacion"], g["verificado_por"]) == (None, str(e["mc"]))
    assert accion(e["mc"], e["cons"], k, "retirar").status_code == 200
    filas = eventos(motor_migrate, e["cons"])
    assert [f.accion.rsplit(".", 1)[1] for f in filas] == [
        "crear", "corregir", "senalar", "verificar", "retirar"]  # fmt: skip
    assert all(set(f.despues) == CAMPOS_AUDITORIA for f in filas)
    assert filas[1].antes is not None and set(filas[1].antes) == CAMPOS_AUDITORIA
    crudo = str([(f.antes, f.despues) for f in filas])
    assert "555" not in crudo and "Ascensores" not in crudo and "guardia" not in crudo
    # Los cinco entran en la proyección funcional de RF35-B.
    with motor_migrate.connect() as c:
        n = c.execute(text("select count(*) from auditoria.proyeccion"
                           " where accion like 'consorcio.contacto.%'")).scalar_one()  # fmt: skip
    assert n == 5


@pytest.mark.redis
@pytest.mark.parametrize(
    "datos",
    [
        PROFESIONAL | {"consentimiento_referencia": None},  # sin constancia
        PROFESIONAL | {"telefono": None},  # sin su número profesional
        EMPRESA | {"consentimiento_referencia": "Nota del 20-10"},  # constancia sin profesional
    ],
)
def test_el_profesional_va_siempre_con_su_numero_y_su_constancia(escena, datos):
    e = escena
    with cliente(e["mc"]) as c:
        r = c.post(url(e["cons"]), json=datos, headers=clave_idempotencia())
    assert r.status_code == 422, r.text
    # Control: con número y constancia, pasa.
    assert crear(e["mc"], e["cons"], **PROFESIONAL).status_code == 201


@pytest.mark.redis
@pytest.mark.parametrize(
    "campo,valor",
    [
        ("nombre", "Juan 11 2345-6789"),
        ("indicaciones", "Si no atiende, llamar al (011) 4567.8901"),
        ("indicaciones", "Póliza vigente hasta 31-12-2026"),
        ("indicaciones", "Escribir a guardia@ejemplo.com"),
        ("indicaciones", "Llamar al 11 - 2345 - 6789"),  # separadores de tres caracteres
        ("nombre", "Guardia 11 . 2345 . 6789"),
    ],
)
def test_ningun_telefono_ni_correo_fuera_del_campo_telefono(escena, campo, valor):
    e = escena
    r = crear(e["mc"], e["cons"], **{campo: valor})
    assert r.status_code == 422, r.text
    # Control: una dirección, un piso y una fecha con palabras pasan.
    assert crear(e["mc"], e["cons"], indicaciones="Av. Corrientes 1234, 5° piso; póliza vigente"
                 " hasta el 31 de diciembre de 2026").status_code == 201  # fmt: skip
    # Control: un horario también pasa.
    r = crear(e["mc"], e["cons"], indicaciones="Atiende de 9:00/13:00, 14:00/18:00")
    assert r.status_code == 201, r.text


def test_la_base_repite_la_regla_del_texto_libre_y_la_del_profesional(escena, motor_migrate):
    e = escena
    with motor_migrate.connect() as c:
        assert c.execute(text(
            "select nucleo.sin_contacto_libre('llamar al 11 2345 6789'),"
            " nucleo.sin_contacto_libre('a@b.com'), nucleo.sin_contacto_libre('31-12-2026'),"
            " nucleo.sin_contacto_libre('Corrientes 1234, piso 5'),"
            " nucleo.sin_contacto_libre('11 - 2345 - 6789'),"
            " nucleo.sin_contacto_libre('9:00/13:00, 14:00/18:00')")).one() == (
            False, False, False, True, False, True)  # fmt: skip
    with pytest.raises(IntegrityError), motor_migrate.begin() as c:
        c.execute(
            text("insert into nucleo.contacto_emergencia (consorcio_id, funcion, naturaleza,"
                 " nombre, telefono, vigente_desde) values (:c, 'encargado', 'profesional',"
                 " 'Encargado', '11 4000 0000', '2026-01-01')"),
            {"c": e["cons"]},
        )  # fmt: skip


@pytest.mark.redis
def test_la_propietaria_no_gestiona_y_queda_auditado(escena, motor_app):
    e = escena
    r = crear(e["prop"], e["cons"])
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    r = leer(e["prop"], e["cons"], "/gestion")
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    motivos = [(d["accion"], d["motivo"]) for d in denegaciones(motor_app, e["cons"], e["mc"],
                                                                e["prop"])]  # fmt: skip
    assert motivos == [("contactos.gestionar", "sin_concesion")] * 2
    # Control: el Consejo lee la lista de gestión, con la constancia.
    crear(e["mc"], e["cons"], **PROFESIONAL)
    [g] = leer(e["mc"], e["cons"], "/gestion").json()["elementos"]
    assert g["consentimiento_referencia"] == "Nota firmada del 20-10"


@pytest.mark.redis
def test_un_contacto_inexistente_da_404_solo_a_quien_gestiona(escena):
    e = escena
    otro = uuid4()
    r = accion(e["mc"], e["cons"], otro, "verificar")
    assert (r.status_code, r.json()["codigo"]) == (404, "no_encontrado")
    # Sin la facultad, el mismo 403 exista o no: sin oráculo.
    assert accion(e["prop"], e["cons"], otro, "verificar").status_code == 403


@pytest.mark.redis
def test_sin_modo_se_lee_y_no_se_gestiona(motor_migrate, motor_app):
    cons, _, prop = consorcio_con_persona(motor_migrate)
    mc = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, None, decidir_equipo(motor_app, cons), persona=mc,
         tipo="miembro_consejo", origen="soporte")  # fmt: skip
    r = crear(mc, cons)
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    [d] = denegaciones(motor_app, cons, mc, mc)
    assert (d["accion"], d["motivo"]) == ("contactos.gestionar", "sin_modo_registrado")
    # Control: leer sí, también sin modo (son de emergencia).
    assert leer(prop, cons).status_code == 200


@pytest.mark.redis
def test_un_mandato_de_otro_consorcio_no_gestiona_en_este(escena, motor_migrate, motor_app):
    e = escena
    otro, _, _ = consorcio_con_persona(motor_migrate)
    mc_otro = otra_persona(motor_migrate, otro)
    alta(motor_app, otro, None, decidir_equipo(motor_app, otro), persona=mc_otro,
         tipo="miembro_consejo", origen="soporte")  # fmt: skip
    assert crear(mc_otro, e["cons"]).status_code == 404


def test_el_administrador_externo_no_recibe_la_facultad(motor_migrate):
    with motor_migrate.connect() as c:
        tipos = c.execute(
            text(
                "select tipo::text from nucleo.paquete_facultad"
                " where facultad = 'contactos.gestionar' order by 1"
            )
        ).scalars()
        assert list(tipos) == ["administrador", "miembro_consejo"]


def test_la_base_niega_sin_ambito_o_sin_facultad(escena, motor_migrate, motor_app):
    e = escena
    # La propietaria tiene ámbito pero no la facultad: la base no la deja escribir ni gestionar.
    with pytest.raises(DBAPIError, match="sin contactos.gestionar"):
        with transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as c:
            c.execute(text("select nucleo.crear_contacto('otro', 'empresa', 'X', null, null,"
                           " null, null, :a, 'api')"), {"a": e["prop"]})  # fmt: skip
    with pytest.raises(DBAPIError, match="sin contactos.gestionar"):
        with transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as c:
            c.execute(text("select * from nucleo.contactos_del_consorcio(true)")).all()
    # Un `gestion` nulo no esquiva las dos barreras (Codex en #121).
    ajena = otra_persona(motor_migrate, e["cons"])
    with pytest.raises(DBAPIError, match="sin ámbito"):
        with transaccion(motor_app, persona_id=ajena, consorcio_id=e["cons"]) as c:
            c.execute(text("select * from nucleo.contactos_del_consorcio(null)")).all()
    # Quien no tiene ámbito no lee ni la lista general.
    with pytest.raises(DBAPIError, match="sin ámbito"):
        with transaccion(motor_app, persona_id=ajena, consorcio_id=e["cons"]) as c:
            c.execute(text("select * from nucleo.contactos_del_consorcio(false)")).all()
    # Por la API, el actor tiene que ser quien está en la sesión.
    with pytest.raises(DBAPIError, match="sin contactos.gestionar"):
        with transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as c:
            c.execute(text("select nucleo.crear_contacto('otro', 'empresa', 'X', null, null,"
                           " null, null, :a, 'api')"), {"a": e["mc"]})  # fmt: skip
    # Control: el Consejo, con la facultad, escribe directo en la base y lee la gestión.
    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
        c.execute(text("select nucleo.crear_contacto('otro', 'empresa', 'X', null, null, null,"
                       " null, :a, 'api')"), {"a": e["mc"]})  # fmt: skip
        assert len(c.execute(text("select * from nucleo.contactos_del_consorcio(true)")).all()) \
            == 1  # fmt: skip


def test_cem_app_no_toca_la_tabla_directo(escena, motor_app):
    e = escena
    with pytest.raises(DBAPIError, match="permission denied"):
        with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
            c.execute(text("select * from nucleo.contacto_emergencia")).all()


@pytest.mark.redis
def test_el_telefono_y_la_constancia_se_recortan(escena):
    e = escena
    r = crear(e["mc"], e["cons"], **PROFESIONAL | {"telefono": " 11 4000 0000 ",
              "consentimiento_referencia": "  Nota firmada  "})  # fmt: skip
    assert r.status_code == 201, r.text
    [g] = leer(e["mc"], e["cons"], "/gestion").json()["elementos"]
    assert (g["telefono"], g["consentimiento_referencia"]) == ("11 4000 0000", "Nota firmada")
    # Solo espacios es como no haberlo puesto: 422, nunca un CHECK de la base (500).
    r = crear(e["mc"], e["cons"], **PROFESIONAL | {"consentimiento_referencia": "   "})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "columna,valor",
    [
        ("nombre", "'   '"),
        ("nombre", "'Juan 11 2345 6789'"),
        ("telefono", "'llamar ya'"),
        ("indicaciones", "'a@b.com'"),
        ("vigente_hasta", "'2025-12-31'"),  # antes de vigente_desde
        ("observacion", "'Mal cargado'"),  # sin observado_en
        ("verificado_por", "(select id from nucleo.persona limit 1)"),  # sin verificado_en
    ],
)
def test_la_base_rechaza_cada_fila_invalida(escena, motor_migrate, columna, valor):
    e = escena
    base = {"nombre": "'Guardia'", "telefono": "'0800-555-1234'"}
    fila = base | {columna: valor}
    sql = ("insert into nucleo.contacto_emergencia (consorcio_id, funcion, naturaleza,"
           " vigente_desde, {c})"
           " values (:c, 'emergencias', 'empresa', '2026-01-01', {v})")  # fmt: skip
    with pytest.raises(IntegrityError), motor_migrate.begin() as c:
        c.execute(text(sql.format(c=", ".join(fila), v=", ".join(fila.values()))),
                  {"c": e["cons"]})  # fmt: skip
    # Control: la fila base entra.
    with motor_migrate.begin() as c:
        c.execute(text(sql.format(c=", ".join(base), v=", ".join(base.values()))),
                  {"c": e["cons"]})  # fmt: skip


def administracion(motor_migrate, motor_app, modo, tipo, **datos):
    """Consorcio en `modo` con su propietaria y un mandato de administración de `tipo`."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    cambiar(motor_app, cons, modo, acta)
    quien = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, None, acta, tipo=tipo, persona=quien, origen="soporte", **datos)
    return cons, prop, quien


@pytest.mark.redis
def test_el_administrador_gestiona_y_el_externo_no(motor_migrate, motor_app):
    cons, _, externo = administracion(motor_migrate, motor_app, "consejo",
                                      "administrador_externo", nombre="Estudio",
                                      cuit="20111111112")  # fmt: skip
    r = crear(externo, cons)
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    # El externo lee la lista general, como todo el que tiene ámbito; la de gestión, no.
    assert leer(externo, cons).status_code == 200
    assert leer(externo, cons, "/gestion").status_code == 403
    # Control: el administrador aumentado agrega, y la propietaria lo lee.
    cons, prop, ad = administracion(motor_migrate, motor_app, "administrador_aumentado",
                                    "administrador")  # fmt: skip
    assert crear(ad, cons).status_code == 201
    assert len(leer(prop, cons).json()["elementos"]) == 1


def test_si_la_base_niega_y_el_evaluador_concede_falla_fuerte(escena, monkeypatch):
    e = escena
    monkeypatch.setattr(
        Permiso, "decidir", lambda self, entidad=None: Decision(True, 200, "concedido_por_facultad")
    )
    # La propietaria tiene ámbito pero no la facultad: la base niega la gestión. Es una
    # discrepancia (500), nunca una lista vacía ni un 403.
    with cliente(e["prop"]) as c, pytest.raises(Discrepancia):
        c.get(url(e["cons"], "/gestion"))


@pytest.mark.parametrize("texto", ["Llamar 1234²5678", "Llamar 1234½5678", "Llamar 12345678"])
def test_lo_que_la_base_rechaza_el_contrato_tambien(motor_migrate, texto):
    """El contrato es igual o más estricto que la base: nunca un CHECK de la base (500)."""
    from cem_contracts.contactos import _sin_contacto_libre

    with motor_migrate.connect() as c:
        en_la_base = c.execute(text("select nucleo.sin_contacto_libre(:t)"), {"t": texto}).scalar()
    assert en_la_base is False
    with pytest.raises(ValueError):
        _sin_contacto_libre(texto)
    # Control: un horario y una dirección pasan en los dos.
    for bueno in ("Atiende 9:00/13:00", "Corrientes 1234, piso 5"):
        with motor_migrate.connect() as c:
            assert c.execute(text("select nucleo.sin_contacto_libre(:t)"), {"t": bueno}).scalar()
        assert _sin_contacto_libre(bueno) == bueno


@pytest.mark.redis
def test_un_contacto_futuro_no_mueve_la_fecha_de_la_lista(escena):
    e = escena
    crear(e["mc"], e["cons"])
    antes = leer(e["prop"], e["cons"]).json()["actualizada_en"]
    crear(e["mc"], e["cons"], nombre="Guardia nueva",
          vigente_desde=str(date.today() + timedelta(days=30)))  # fmt: skip
    assert leer(e["prop"], e["cons"]).json()["actualizada_en"] == antes
    # Control: uno vigente sí la mueve.
    crear(e["mc"], e["cons"], nombre="Plomería")
    assert leer(e["prop"], e["cons"]).json()["actualizada_en"] > antes
