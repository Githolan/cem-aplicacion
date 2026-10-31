"""H3: rutas de proveedores y órdenes de pago (`0049`). Datos SINTETICOS / PRUEBA / NO REAL.

Diseño: `doc/diseno/H3_Proveedor_Beneficiario_Cuenta.md` del repositorio CEM, «Rutas». Cada
negación lleva su control: el mismo pedido de quien sí puede, o el mismo dato sin lo que lo hace
inválido, pasa. Las reglas de la base se prueban en `test_orden_pago_neutra.py` y
`test_proveedor_0048.py`; acá, que las rutas las exponen sin abrir nada.
"""

import random
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.autorizacion.evaluador import Decision
from app.autorizacion.requiere import Permiso
from app.config import Config
from app.consorcios.padron import Discrepancia
from app.contexto import Identidad, obtener_identidad
from app.main import crear_app
from tests.conftest import clave_idempotencia, url_rol
from tests.test_contexto_mandatos import IP, denegaciones
from tests.test_orden_pago_neutra import (
    Escena,
    cbu_sintetico,
    escena_economica,
    nueva_sesion,
    nuevo_proveedor,
    otorgar,
    otra_cuenta_en_el_consejo,
)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db

ORDEN = {
    "importe_centavos": 1_234_500,
    "moneda": "ARS",
    "concepto": "SINTETICO / PRUEBA / NO REAL: reparación de la bomba",
    "comprobante": "Factura TEST-0001",
    "usa_fondo_reserva": False,
}
PROVEEDOR = {"razon_social": "Proveedor SINTETICO de prueba (NO REAL)", "rubro": "Plomería"}


@pytest.fixture
def e(motor_migrate, motor_app) -> Escena:
    return escena_economica(motor_migrate, motor_app)


def cliente(persona: UUID, sesion: UUID | None) -> TestClient:
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
    app.dependency_overrides[obtener_identidad] = lambda: Identidad(persona, sesion)
    return TestClient(app, client=IP)


def url(e: Escena, resto: str) -> str:
    return f"/api/v1/consorcios/{e.cons}/{resto}"


def get(e: Escena, quien: UUID, resto: str, sesion: UUID | None = None):
    with cliente(quien, sesion or e.sesion[quien]) as c:
        return c.get(url(e, resto))


def post(e: Escena, quien: UUID, resto: str, cuerpo: dict, clave: dict | None = None):
    with cliente(quien, e.sesion[quien]) as c:
        return c.post(url(e, resto), json=cuerpo, headers=clave or clave_idempotencia())


def codigo(r) -> tuple[int, str]:
    return r.status_code, r.json()["codigo"]


def preparar(e: Escena, **cambios) -> UUID:
    r = post(e, e.adm, "ordenes-pago", ORDEN | {"proveedor_id": str(e.proveedor)} | cambios)
    assert r.status_code == 201, r.json()
    assert r.json()["version"] == 1
    return UUID(r.json()["id"])


def enviada(e: Escena) -> UUID:
    o = preparar(e)
    r = post(e, e.adm, f"ordenes-pago/{o}/enviar-a-revision", {"version_esperada": 1})
    assert r.status_code == 200, r.json()
    return o


def huella(e: Escena, o: UUID, version: int) -> str:
    r = get(e, e.mc1, f"ordenes-pago/{o}/revision")
    assert r.status_code == 200, r.json()
    return next(v["huella"] for v in r.json()["versiones"] if v["numero"] == version)


def cuit_valido(base: str) -> str:
    """Un CUIT sintético con su dígito verificador (los pesos de `nucleo.cuit_valido`)."""
    pesos = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    resto = 11 - sum(int(c) * p for c, p in zip(base, pesos, strict=True)) % 11
    if resto == 10:
        raise ValueError("esa base no tiene dígito verificador")
    return base + str(0 if resto == 11 else resto)


# Proveedores ----------------------------------------------------------------------------------


def test_alta_de_proveedor_y_su_historia(e):
    r = post(e, e.adm, "proveedores", PROVEEDOR | {"cuenta_destino": cbu_sintetico(10)})
    assert r.status_code == 201, r.json()
    p = r.json()["id"]
    assert r.json()["version"] == 1
    assert p in {x["id"] for x in get(e, e.adm, "proveedores").json()["elementos"]}

    cambio = PROVEEDOR | {"cuenta_destino": cbu_sintetico(11), "estado": "activo",
                          "conservar_cuenta": False}  # fmt: skip
    r = post(e, e.adm, f"proveedores/{p}/versiones", cambio | {"version_esperada": 1})
    assert (r.status_code, r.json()["version"]) == (200, 2)
    detalle = get(e, e.adm, f"proveedores/{p}").json()
    assert detalle["proveedor"]["cuenta_ultimos_digitos"] == cbu_sintetico(11)[-4:]
    assert [(v["version"], v["cuenta_ultimos_digitos"]) for v in detalle["versiones"]] == [
        (1, cbu_sintetico(10)[-4:]), (2, cbu_sintetico(11)[-4:])]  # fmt: skip

    # La versión vieja ya no sirve, y la misma versión sin cambios tampoco.
    r = post(e, e.adm, f"proveedores/{p}/versiones", cambio | {"version_esperada": 1})
    assert codigo(r) == (409, "version_desactualizada")
    r = post(e, e.adm, f"proveedores/{p}/versiones", cambio | {"version_esperada": 2})
    assert codigo(r) == (409, "sin_cambios")


def test_quien_prepara_nunca_recibe_la_cuenta_completa(e):
    """Decisión del 30-10-2026: la cuenta sale recortada desde el backend, no se oculta en la
    pantalla. Ninguna respuesta de preparación lleva sus 22 dígitos; la revisión, sí."""
    cuenta = cbu_sintetico(1)  # la del proveedor de la escena
    o = enviada(e)
    preparacion = [
        get(e, e.adm, "proveedores"),
        get(e, e.adm, f"proveedores/{e.proveedor}"),
        get(e, e.adm, "ordenes-pago"),
        get(e, e.adm, f"ordenes-pago/{o}"),
    ]
    for r in preparacion:
        assert r.status_code == 200, r.json()
        assert cuenta not in r.text
        assert "cuenta_destino" not in r.text
    detalle = preparacion[3].json()
    assert detalle["versiones"][0]["cuenta_ultimos_digitos"] == cuenta[-4:]
    # Quien prepara no lee la revisión (es otra acción), y quien revisa sí ve la cuenta entera.
    assert codigo(get(e, e.adm, f"ordenes-pago/{o}/revision")) == (403, "sin_permiso")
    revision = get(e, e.mc1, f"ordenes-pago/{o}/revision")
    assert revision.json()["versiones"][0]["cuenta_destino"] == cuenta  # control


def test_un_consejero_que_preparo_la_orden_no_ve_la_cuenta_completa_al_revisarla(motor_migrate, e):
    """Revisión independiente de #172: un consejero que también prepara no aprueba lo que
    preparó, así que tampoco recibe la cuenta completa en la revisión de esa orden."""
    _mc2_tambien_prepara(motor_migrate, e)
    cuenta = cbu_sintetico(1)
    o = enviada(e)
    r = post(e, e.mc2, f"ordenes-pago/{o}/versiones",
             ORDEN | {"proveedor_id": str(e.proveedor), "importe_centavos": 777,
                      "version_esperada": 1})  # fmt: skip
    assert r.status_code == 200, r.json()
    revision = get(e, e.mc2, f"ordenes-pago/{o}/revision")
    assert revision.status_code == 200, revision.json()
    assert cuenta not in revision.text
    assert {v["cuenta_destino"] for v in revision.json()["versiones"]} == {None}
    assert {v["cuenta_ultimos_digitos"] for v in revision.json()["versiones"]} == {cuenta[-4:]}
    # Control: quien no preparó ninguna versión la ve entera.
    otro = get(e, e.mc1, f"ordenes-pago/{o}/revision").json()
    assert {v["cuenta_destino"] for v in otro["versiones"]} == {cuenta}


def test_un_error_de_la_base_llega_al_registro_sin_la_cuenta():
    """Dos capas: `hide_parameters` saca los valores que agrega SQLAlchemy, y `redactar` tapa la
    cuenta que PostgreSQL mismo escribe en su mensaje (`value "…" is out of range`). El registro
    pasa la excepción por `redactar` (`registros.py`)."""
    from sqlalchemy.exc import DBAPIError

    from app.db import crear_motor
    from app.registros import redactar

    cuenta = cbu_sintetico(3)
    motor = crear_motor(url_rol("app"))
    try:
        with pytest.raises(DBAPIError) as error, motor.connect() as c:
            c.execute(text("select cast(:x as integer)"), {"x": cuenta})
    finally:
        motor.dispose()
    texto = str(error.value)
    assert "SQL parameters hidden" in texto
    assert cuenta in texto  # control: el mensaje de PostgreSQL la trae
    assert cuenta not in redactar(texto)
    assert "[cuenta]" in redactar(texto)


def test_cambiar_un_proveedor_conservando_la_cuenta_que_no_ve(e, motor_migrate):
    p = post(e, e.adm, "proveedores", PROVEEDOR | {"cuenta_destino": cbu_sintetico(14)}).json()[
        "id"
    ]
    cambio = PROVEEDOR | {"rubro": "Gas", "estado": "activo", "version_esperada": 1}
    r = post(e, e.adm, f"proveedores/{p}/versiones", cambio | {"conservar_cuenta": True})
    assert (r.status_code, r.json()["version"]) == (200, 2)
    with motor_migrate.connect() as c:
        cuentas = c.execute(
            text("select numero, cuenta_destino from nucleo.proveedor_version"
                 " where proveedor_id = :p order by numero"), {"p": p},
        ).all()  # fmt: skip
    assert [tuple(x) for x in cuentas] == [(1, cbu_sintetico(14)), (2, cbu_sintetico(14))]
    # Conservar y mandar una cuenta a la vez no tiene sentido; con la versión vieja, 409.
    r = post(e, e.adm, f"proveedores/{p}/versiones",
             cambio | {"version_esperada": 2, "conservar_cuenta": True,
                       "cuenta_destino": cbu_sintetico(15)})  # fmt: skip
    assert codigo(r) == (422, "peticion_invalida")
    r = post(
        e, e.adm, f"proveedores/{p}/versiones", cambio | {"rubro": "Agua", "conservar_cuenta": True}
    )
    assert codigo(r) == (409, "version_desactualizada")
    # Sin decir si conserva la cuenta, 422: un cliente viejo o descuidado no la borra sin querer.
    agua = PROVEEDOR | {"rubro": "Agua", "estado": "activo", "version_esperada": 2}
    assert codigo(post(e, e.adm, f"proveedores/{p}/versiones", agua)) == (422, "peticion_invalida")
    # Control: sin conservar y sin cuenta, la versión nueva queda sin cuenta (como antes).
    r = post(e, e.adm, f"proveedores/{p}/versiones", agua | {"conservar_cuenta": False})
    assert r.status_code == 200, r.json()
    assert get(e, e.adm, f"proveedores/{p}").json()["proveedor"]["cuenta_ultimos_digitos"] is None


def test_cuenta_y_cuit_invalidos(e):
    bien = cbu_sintetico(12)
    mal = bien[:21] + str((int(bien[21]) + 1) % 10)
    r = post(e, e.adm, "proveedores", PROVEEDOR | {"cuenta_destino": mal})
    assert codigo(r) == (422, "peticion_invalida")
    cuit = cuit_valido("2012345678")
    otro_digito = cuit[:10] + str((int(cuit[10]) + 1) % 10)
    r = post(e, e.adm, "proveedores", PROVEEDOR | {"cuit": otro_digito})
    assert codigo(r) == (422, "cuit_invalido")
    # Control: el mismo CUIT con su dígito pasa, y repetido en el consorcio se niega.
    assert post(e, e.adm, "proveedores", PROVEEDOR | {"cuit": cuit}).status_code == 201
    r = post(e, e.adm, "proveedores", PROVEEDOR | {"cuit": cuit, "cuenta_destino": bien})
    assert codigo(r) == (409, "cuit_repetido")


def test_el_control_de_la_cuenta_coincide_con_la_base(motor_migrate):
    """El contrato acepta exactamente las mismas cuentas que `nucleo.cbu_valido`: lo que la base
    rechazaría es un 422, nunca un CHECK (500)."""
    from cem_contracts.egresos import _cuenta_valida

    azar = random.Random(48)
    casos = [cbu_sintetico(n) for n in range(40)]
    casos += ["".join(azar.choice("0123456789") for _ in range(22)) for _ in range(400)]
    casos += ["0000000" + cbu_sintetico(1)[7:], "0" * 22]

    def contrato(c: str) -> bool:
        try:
            _cuenta_valida(c)
        except ValueError:
            return False
        return True

    with motor_migrate.connect() as c:
        base = c.execute(
            text("select x, nucleo.cbu_valido(x) from unnest(cast(:c as text[])) x"), {"c": casos}
        ).all()
    assert [contrato(x) for x, _ in base] == [v for _, v in base]
    assert sum(v for _, v in base) >= 40  # control: hay válidas y no válidas
    assert sum(not v for _, v in base) >= 100


def test_el_alta_se_reintenta_sin_duplicar(e, motor_migrate):
    clave = clave_idempotencia()
    cuerpo = PROVEEDOR | {"cuenta_destino": cbu_sintetico(13)}
    primera = post(e, e.adm, "proveedores", cuerpo, clave)
    repetida = post(e, e.adm, "proveedores", cuerpo, clave)
    assert primera.status_code == repetida.status_code == 201
    assert primera.json() == repetida.json()
    assert repetida.headers.get("Idempotent-Replayed") == "true"
    assert "Idempotent-Replayed" not in primera.headers
    with motor_migrate.connect() as c:
        cuantos = c.execute(
            text("select count(*) from nucleo.proveedor_version where cuenta_destino = :k"),
            {"k": cbu_sintetico(13)},
        ).scalar_one()
    assert cuantos == 1
    otra = post(e, e.adm, "proveedores", cuerpo | {"rubro": "Otro"}, clave)
    assert codigo(otra) == (422, "idempotencia_reutilizada")


# Órdenes: preparar, enviar, aprobar y rechazar ------------------------------------------------


def test_preparar_enviar_y_aprobar(e):
    o = preparar(e)
    propias = get(e, e.adm, "ordenes-pago").json()["elementos"]
    assert [(x["id"], x["estado"]) for x in propias] == [(str(o), "borrador")]
    # Quien aprueba no ve los borradores.
    assert get(e, e.mc1, "ordenes-pago/revision").json()["elementos"] == []
    assert codigo(get(e, e.mc1, f"ordenes-pago/{o}/revision")) == (404, "no_encontrado")

    r = post(e, e.adm, f"ordenes-pago/{o}/enviar-a-revision", {"version_esperada": 1})
    assert r.status_code == 200, r.json()
    revision = get(e, e.mc1, f"ordenes-pago/{o}/revision").json()
    assert revision["orden"]["estado"] == "en_revision"
    (v,) = revision["versiones"]
    assert (v["razon_social"], v["cuenta_destino"]) == ("Proveedor SINTETICO (NO REAL)",
                                                         cbu_sintetico(1))  # fmt: skip
    assert v["condicion_beneficiario"] == "nuevo"
    assert v["aprobada_por"] == []

    r = post(e, e.mc1, f"ordenes-pago/{o}/aprobaciones", {"version": 1, "huella": v["huella"]})
    assert r.status_code == 201, r.json()
    orden = get(e, e.adm, f"ordenes-pago/{o}").json()
    assert (orden["orden"]["estado"], orden["orden"]["aprobaciones"]) == ("aprobada", 1)
    assert orden["versiones"][0]["aprobada_por"] == [str(e.mc1)]


def test_una_aprobacion_es_de_la_version_y_la_huella_vistas(e):
    o = enviada(e)
    h1 = huella(e, o, 1)
    r = post(e, e.adm, f"ordenes-pago/{o}/versiones",
             ORDEN | {"proveedor_id": str(e.proveedor), "importe_centavos": 999,
                      "version_esperada": 1})  # fmt: skip
    assert (r.status_code, r.json()["version"]) == (200, 2)
    r = post(e, e.mc1, f"ordenes-pago/{o}/aprobaciones", {"version": 1, "huella": h1})
    assert codigo(r) == (409, "version_desactualizada")
    r = post(e, e.mc1, f"ordenes-pago/{o}/aprobaciones", {"version": 2, "huella": h1})
    assert codigo(r) == (409, "huella_distinta")
    # Control: la versión vigente con su huella.
    r = post(e, e.mc1, f"ordenes-pago/{o}/aprobaciones", {"version": 2, "huella": huella(e, o, 2)})
    assert r.status_code == 201, r.json()


def test_rechazo_con_motivo(e):
    o = enviada(e)
    r = post(e, e.mc1, f"ordenes-pago/{o}/rechazar", {"version": 1, "motivo": "  "})
    assert codigo(r) == (422, "peticion_invalida")
    r = post(e, e.mc1, f"ordenes-pago/{o}/rechazar", {"version": 1, "motivo": "Falta factura"})
    assert r.status_code == 200, r.json()
    orden = get(e, e.adm, f"ordenes-pago/{o}").json()["orden"]
    assert (orden["estado"], orden["motivo_rechazo"]) == ("rechazada", "Falta factura")
    r = post(e, e.mc1, f"ordenes-pago/{o}/aprobaciones", {"version": 1, "huella": huella(e, o, 1)})
    assert codigo(r) == (409, "orden_no_admite")


def test_proveedor_inactivo_o_inexistente(e, motor_migrate):
    suspendido = nuevo_proveedor(motor_migrate, e.cons, e.adm, estado="suspendido")
    r = post(e, e.adm, "ordenes-pago", ORDEN | {"proveedor_id": str(suspendido)})
    assert codigo(r) == (409, "proveedor_inactivo")
    r = post(e, e.adm, "ordenes-pago", ORDEN | {"proveedor_id": str(uuid4())})
    assert codigo(r) == (422, "proveedor_inexistente")
    preparar(e)  # control: el proveedor activo


def test_una_orden_inexistente_es_404(e):
    assert codigo(get(e, e.adm, f"ordenes-pago/{uuid4()}")) == (404, "no_encontrado")
    assert codigo(get(e, e.adm, f"proveedores/{uuid4()}")) == (404, "no_encontrado")


# Quién puede qué --------------------------------------------------------------------------------


def test_cada_rol_en_su_lugar(e):
    """P3 81: el administrador prepara y no lee la revisión ni aprueba; el Consejo aprueba y no
    prepara. (Soporte no entra por estas rutas: actúa por `scripts/` y Access, sin sesión en el
    consorcio.)"""
    o = enviada(e)
    h = huella(e, o, 1)
    assert codigo(get(e, e.adm, "ordenes-pago/revision")) == (403, "sin_permiso")
    r = post(e, e.adm, f"ordenes-pago/{o}/aprobaciones", {"version": 1, "huella": h})
    assert codigo(r) == (403, "sin_permiso")
    assert codigo(get(e, e.mc1, "ordenes-pago")) == (403, "sin_permiso")
    assert codigo(post(e, e.mc1, "proveedores", PROVEEDOR)) == (403, "sin_permiso")
    r = post(e, e.mc1, "ordenes-pago", ORDEN | {"proveedor_id": str(e.proveedor)})
    assert codigo(r) == (403, "sin_permiso")
    # Controles: cada uno en lo suyo.
    assert get(e, e.adm, "ordenes-pago").status_code == 200
    assert get(e, e.mc1, "ordenes-pago/revision").status_code == 200


def test_quien_preparo_no_aprueba_con_otra_cuenta_del_consejo(motor_migrate, motor_app, e):
    o = enviada(e)
    h = huella(e, o, 1)
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)
    r = post(e, otra, f"ordenes-pago/{o}/aprobaciones", {"version": 1, "huella": h})
    assert codigo(r) == (403, "sin_permiso")
    motivos = [d["motivo"] for d in denegaciones(motor_app, e.cons, e.mc1, otra)]
    assert motivos == ["no_es_quien_preparo"]
    r = post(e, otra, f"ordenes-pago/{o}/rechazar", {"version": 1, "motivo": "No"})
    assert codigo(r) == (403, "sin_permiso")
    # Control: otro ser humano del Consejo sí.
    r = post(e, e.mc2, f"ordenes-pago/{o}/aprobaciones", {"version": 1, "huella": h})
    assert r.status_code == 201, r.json()


def test_r10_segundo_factor_y_sesion_recientes(e, motor_migrate):
    vieja_totp = nueva_sesion(motor_migrate, e.adm, horas_atras=1, totp_min_atras=11)
    r = get(e, e.adm, "ordenes-pago", sesion=vieja_totp)
    assert codigo(r) == (403, "segundo_factor_requerido")
    larga = nueva_sesion(motor_migrate, e.adm, horas_atras=13)
    assert codigo(get(e, e.adm, "ordenes-pago", sesion=larga)) == (403, "sesion_reciente_requerida")
    with cliente(e.adm, None) as c:
        assert codigo(c.get(url(e, "ordenes-pago"))) == (403, "segundo_factor_requerido")
    # Un 403 de R10 no revela nada a quien no tiene la facultad: recibe el genérico.
    sin_totp = nueva_sesion(motor_migrate, e.mc1, totp_min_atras=None)
    assert codigo(get(e, e.mc1, "ordenes-pago", sesion=sin_totp)) == (403, "sin_permiso")
    assert get(e, e.adm, "ordenes-pago").status_code == 200  # control: la sesión de la escena


def test_sin_un_modo_que_admita_egresos_no_hay_egresos(e, motor_migrate, motor_app):
    """Sin modo registrado se niega. El Modo Consejo, que tampoco admite egresos, está en la
    tabla de verdad (`modo_no_admitido`) y en `test_orden_pago_neutra.py`."""
    sin_modo = escena_economica(motor_migrate, motor_app, con_modo=False)
    assert codigo(get(sin_modo, sin_modo.adm, "ordenes-pago")) == (403, "sin_permiso")
    assert codigo(get(sin_modo, sin_modo.mc1, "ordenes-pago/revision")) == (403, "sin_permiso")
    assert get(e, e.adm, "ordenes-pago").status_code == 200  # control: con modo


def test_si_la_base_niega_y_el_evaluador_concede_falla_fuerte(e, monkeypatch):
    assert codigo(get(e, e.mc1, "ordenes-pago")) == (403, "sin_permiso")  # control
    monkeypatch.setattr(
        Permiso, "decidir", lambda self, entidad=None: Decision(True, 200, "concedido_por_facultad")
    )
    # El consejero no tiene `egresos.preparar_orden`: la base niega. Es una discrepancia (500),
    # nunca una lista vacía ni un 403.
    with pytest.raises(Discrepancia):
        get(e, e.mc1, "ordenes-pago")


def _rutas(motor) -> tuple[int, int]:
    """Funciones de lectura de `0049` que existen, y funciones de egresos que `cem_app` ejecuta."""
    with motor.connect() as c:
        return c.execute(
            text(
                "select count(*) filter (where right(p.proname, 8) = '_lectura'),"
                " count(*) filter (where has_function_privilege('cem_app', p.oid, 'EXECUTE'))"
                " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                " where n.nspname = 'nucleo' and (p.proname like '%orden%'"
                " or p.proname like '%proveedor%' or p.proname like '%egresos%')"
                " and p.proname <> 'fijar_aprobaciones_orden_pago'"
            )
        ).one()


def test_0049_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0048")
    assert _rutas(motor) == (0, 0)
    command.upgrade(cfg, "0049")
    assert _rutas(motor) == (4, 11)
    command.downgrade(cfg, "0048")
    assert _rutas(motor) == (0, 0)
    command.upgrade(cfg, "0049")  # control: vuelve a subir limpio
    assert _rutas(motor) == (4, 11)


def _mc2_tambien_prepara(motor_migrate, e: Escena) -> None:
    with motor_migrate.connect() as c:
        m, acta = c.execute(
            # P3 103: el mandato de Consejo vigente de mc2, no «el único que tenga».
            text("select id, decision_id from nucleo.mandato where consorcio_id = :c"
                 " and persona_id = :p and tipo = 'miembro_consejo' and finalizado_en is null"),
            {"c": e.cons, "p": e.mc2},
        ).one()  # fmt: skip
    otorgar(motor_migrate, e.cons, m, acta, "egresos.preparar_orden")


def test_quien_preparo_una_version_posterior_no_aprueba(motor_migrate, e):
    """El evaluador compara con quien creó la orden; quien preparó la versión 2 lo frena la base
    (`no_es_quien_preparo`), y la ruta lo dice."""
    _mc2_tambien_prepara(motor_migrate, e)
    o = enviada(e)
    r = post(e, e.mc2, f"ordenes-pago/{o}/versiones",
             ORDEN | {"proveedor_id": str(e.proveedor), "importe_centavos": 777,
                      "version_esperada": 1})  # fmt: skip
    assert r.status_code == 200, r.json()
    h2 = huella(e, o, 2)
    r = post(e, e.mc2, f"ordenes-pago/{o}/aprobaciones", {"version": 2, "huella": h2})
    assert codigo(r) == (403, "no_es_quien_preparo")
    r = post(e, e.mc1, f"ordenes-pago/{o}/aprobaciones", {"version": 2, "huella": h2})
    assert r.status_code == 201, r.json()  # control


def test_quien_cargo_la_cuenta_no_aprueba_el_pago(motor_migrate, e):
    _mc2_tambien_prepara(motor_migrate, e)
    r = post(e, e.mc2, f"proveedores/{e.proveedor}/versiones",
             {"razon_social": "Proveedor SINTETICO (NO REAL)", "rubro": "Otro rubro",
              "cuenta_destino": cbu_sintetico(1), "estado": "activo",
              "version_esperada": 1, "conservar_cuenta": False})  # fmt: skip
    assert r.status_code == 200, r.json()
    o = enviada(e)
    h = huella(e, o, 1)
    r = post(e, e.mc2, f"ordenes-pago/{o}/aprobaciones", {"version": 1, "huella": h})
    assert codigo(r) == (403, "no_es_quien_cargo_la_cuenta")
    r = post(e, e.mc1, f"ordenes-pago/{o}/aprobaciones", {"version": 1, "huella": h})
    assert r.status_code == 201, r.json()  # control


def test_el_consejo_no_lee_los_proveedores_por_su_cuenta(e):
    assert codigo(get(e, e.mc1, "proveedores")) == (403, "sin_permiso")
    assert codigo(get(e, e.mc1, f"proveedores/{e.proveedor}")) == (403, "sin_permiso")
    assert get(e, e.adm, f"proveedores/{e.proveedor}").status_code == 200  # control


def test_un_proveedor_inexistente_en_la_ruta_es_404(e):
    cuerpo = PROVEEDOR | {"estado": "activo", "version_esperada": 1, "conservar_cuenta": True}
    assert codigo(post(e, e.adm, f"proveedores/{uuid4()}/versiones", cuerpo)) == (
        404, "no_encontrado")  # fmt: skip
    r = post(e, e.adm, f"proveedores/{e.proveedor}/versiones", cuerpo)
    assert r.status_code == 200, r.json()  # control: el que existe


@pytest.mark.parametrize(
    "cuerpo",
    [{"version_esperada": 2**31}, {"version_esperada": 0}],
)
def test_lo_que_la_base_no_recibe_es_422(e, cuerpo):
    """Una versión fuera de `integer` o un carácter de control no llegan a la base (500)."""
    o = preparar(e)
    r = post(e, e.adm, f"ordenes-pago/{o}/enviar-a-revision", cuerpo)
    assert codigo(r) == (422, "peticion_invalida")
    r = post(e, e.adm, "ordenes-pago",
             ORDEN | {"proveedor_id": str(e.proveedor), "concepto": "Bomba\x00"})  # fmt: skip
    assert codigo(r) == (422, "peticion_invalida")
    r = post(e, e.adm, f"ordenes-pago/{o}/enviar-a-revision", {"version_esperada": 2**31 - 1})
    assert codigo(r) == (409, "version_desactualizada")  # control: el tope sí llega a la base
