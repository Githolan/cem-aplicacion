"""Pagos informados por los vecinos, primer corte (`0129`; PI-S1 del diseño «Pagos informados»,
reglas PI-1, PI-2, PI-3 y PI-6; PA07).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. La escena es la de `0119`
(Modo Consejo): un miembro del Consejo y la administración sin vínculo, la propietaria confirmada
de «1-A» y de otra unidad, una propietaria sin confirmar de «1-A» y una inquilina confirmada de
«PB-A»; se suman una ocupante confirmada de «PB-A» y una cuenta sin vínculo que va a ayudar. Cada
negación lleva su control: el mismo pedido de quien sí puede pasa. Datos SINTETICOS / NO REAL.
"""

import importlib.util
import json
import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from cem_contracts import pagos_informados as contrato
from cem_contracts.ayuda import ENCABEZADO_AYUDA
from psycopg.errors import CheckViolation, ForeignKeyViolation, InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import transaccion
from scripts import exportar_consorcio
from tests.conftest import clave_idempotencia
from tests.test_documentos_2c1 import analizar, pdf
from tests.test_exportacion_2c3 import (  # noqa: F401  (`claves` y `con_url` son fixtures)
    argumentos,
    claves,
    con_url,
    descifrar,
    entidades,
    verificar_manifiesto,
)
from tests.test_liquidacion_oficial_0119 import (  # noqa: F401  (los fixtures se importan)
    armar_escena,
    bucket,
    esperar_trabado,
    firmas_de_la_imagen,
    persona_con_vinculo,
    ruta,
)
from tests.test_mandatos import otra_persona
from tests.test_modo_operacion import cambiar, decidir_equipo, hoy_de
from tests.test_orden_pago_neutra import cbu_sintetico, nueva_sesion
from tests.test_reclamos_liquidacion_0123 import correo_de
from tests.test_rutas_egresos import cliente
from tests.test_vinculo_0014 import base_aparte

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["base_aparte", "bucket", "firmas_de_la_imagen"]

MIGRACION = Path(__file__).resolve().parent.parent / "migrations/versions/0129_pagos_informados.py"
TABLAS = ("nucleo.comprobante_pago_subido", "nucleo.pago_informado")
NUMERO = "AB-12.345 678"
NUMERO_OTRA_FORMA = "ab12345678"
"""El mismo número sin guiones, puntos ni espacios y en minúsculas (PI-3)."""
INFORMAR = text(
    "select * from nucleo.informar_pago(:u, :i, :f, :m, :n, :comp, null, :a, 'api', :s)"
)
"""Con la firma de `0130` (suma `corrige`, nula: sin corrección)."""
MIGRACION_0130 = MIGRACION.with_name("0130_pagos_informados_confirmar.py")


def _migracion(ruta: Path = MIGRACION):
    spec = importlib.util.spec_from_file_location(ruta.stem, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def armar(motor_migrate, motor_app) -> dict:
    e = armar_escena(motor_migrate, motor_app)
    e["oc"] = persona_con_vinculo(motor_migrate, e["cons"], e["u"]["PB-A"], "ocupante",
                                  confirmado=True)  # fmt: skip
    e["ayu"] = otra_persona(motor_migrate, e["cons"], verificada=False)
    for quien in ("oc", "ayu"):
        e[f"sesion_{quien}"] = nueva_sesion(motor_migrate, e[quien])
    e["hoy"] = hoy_de(motor_app, e["cons"])
    with motor_migrate.connect() as c:
        e["otra_de_prop"] = c.execute(text(
            "select unidad_id from nucleo.vinculo where consorcio_id = :c and persona_id = :p"
            " and unidad_id <> :u"), {"c": e["cons"], "p": e["prop"], "u": e["u"]["1-A"]},
        ).scalar_one()  # fmt: skip
    return e


@pytest.fixture
def e(motor_migrate, motor_app, bucket) -> dict:
    return armar(motor_migrate, motor_app)


def archivo(marca: str) -> bytes:
    """Un PDF distinto por marca y el mismo para la misma marca."""
    return pdf(flujo=f"BT (Comprobante SINTETICO {marca}) Tj ET".encode())


def subir(e: dict, quien: str, unidad, contenido: bytes, encabezados: dict | None = None):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.post(ruta(e, "pagos-informados", "comprobantes"),
                      params={"unidad_id": str(unidad)}, content=contenido,
                      headers={"Content-Type": "application/pdf"} | clave_idempotencia()
                      | (encabezados or {}))  # fmt: skip


def comprobante(e: dict, quien: str, unidad, contenido: bytes) -> str:
    r = subir(e, quien, unidad, contenido)
    assert r.status_code == 201, r.text
    assert r.json()["archivo"]["estado"] == "en_cuarentena"
    return r.json()["archivo"]["id"]


def datos(unidad, comprobante_id: str | None, numero: str | None = NUMERO, **cambios) -> dict:
    base = {"unidad_id": str(unidad), "importe": "45678.90", "fecha_pago": "2026-11-02",
            "medio": "transferencia", "numero_operacion": numero,
            "comprobante_version_id": comprobante_id}  # fmt: skip
    return base | cambios


def informar(e: dict, quien: str, cuerpo: dict, clave: dict | None = None,
             encabezados: dict | None = None):  # fmt: skip
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.post(ruta(e, "pagos-informados"), json=cuerpo,
                      headers=(clave or clave_idempotencia()) | (encabezados or {}))  # fmt: skip


def listar(e: dict, quien: str, encabezados: dict | None = None):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.get(ruta(e, "pagos-informados"), headers=encabezados or {})


def ver_comprobante(e: dict, quien: str, pago: str):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.get(ruta(e, "pagos-informados", pago, "comprobante"))


def filas(motor_migrate, cons) -> int:
    with motor_migrate.connect() as c:
        return c.execute(text("select count(*) from nucleo.pago_informado where consorcio_id = :c"),
                         {"c": cons}).scalar_one()  # fmt: skip


def codigo(r) -> tuple[int, str]:
    return r.status_code, r.json()["codigo"]


def ayuda(e: dict, motor_migrate, alcances: list[str]) -> str:
    with cliente(e["prop"], e["sesion_prop"]) as c:
        r = c.post(
            ruta(e, "ayudas"),
            headers=clave_idempotencia(),
            json={"correo": correo_de(motor_migrate, e["ayu"]), "alcances": alcances},
        )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- PA07: un solo registro por pago -----------------------------------------------------------


def test_pa07_el_mismo_pago_informado_dos_veces_queda_en_un_solo_registro(e, motor_migrate):
    """PA07 («Recibir confirmación de pago duplicada → una sola imputación»), app + app: el mismo
    número escrito de otra forma, el mismo archivo subido de nuevo y otra persona de la misma unidad
    dan el mismo registro, con 200 y `ya_informado`; la primera vez, 201."""
    contenido = archivo("pa07")
    primero = informar(e, "prop", datos(e["u"]["1-A"], comprobante(e, "prop", e["u"]["1-A"],
                                                                   contenido)))  # fmt: skip
    assert primero.status_code == 201, primero.text
    pago = primero.json()
    assert (pago["ya_informado"], pago["estado"], pago["origen"]) == (False, "informado", "app")
    assert (pago["numero_operacion"], Decimal(pago["importe"])) == (NUMERO, Decimal("45678.90"))
    # El mismo número con otro espaciado, sin separadores y en minúsculas, con otro archivo.
    otro_archivo = comprobante(e, "prop", e["u"]["1-A"], archivo("otro"))
    r = informar(e, "prop", datos(e["u"]["1-A"], otro_archivo, NUMERO_OTRA_FORMA,
                                  importe="1.00"))  # fmt: skip
    assert (r.status_code, r.json()["id"], r.json()["ya_informado"]) == (200, pago["id"], True)
    assert Decimal(r.json()["importe"]) == Decimal("45678.90")  # el existente, no el nuevo
    # En mayúsculas y con espacios, también (la base compara en minúsculas).
    r = informar(
        e,
        "prop",
        datos(
            e["u"]["1-A"], comprobante(e, "prop", e["u"]["1-A"], archivo("mayus")), "AB 12 345 678"
        ),
    )
    assert (r.status_code, r.json()["id"], r.json()["ya_informado"]) == (200, pago["id"], True)
    # El mismo archivo, subido otra vez (otra versión), con otro número.
    de_nuevo = comprobante(e, "prop", e["u"]["1-A"], contenido)
    assert de_nuevo != pago["comprobante_version_id"]
    r = informar(e, "prop", datos(e["u"]["1-A"], de_nuevo, "ZZ-999"))
    assert (r.status_code, r.json()["id"], r.json()["ya_informado"]) == (200, pago["id"], True)
    # Otra persona con vínculo confirmado en la misma unidad: el mismo pago, el mismo registro.
    e["co"] = persona_con_vinculo(motor_migrate, e["cons"], e["u"]["1-A"], "ocupante",
                                  confirmado=True)  # fmt: skip
    e["sesion_co"] = nueva_sesion(motor_migrate, e["co"])
    r = informar(e, "co", datos(e["u"]["1-A"], comprobante(e, "co", e["u"]["1-A"], archivo("co")),
                                " ab 12-345.678 "))  # fmt: skip
    assert (r.status_code, r.json()["id"], r.json()["propio"]) == (200, pago["id"], False)
    assert filas(motor_migrate, e["cons"]) == 1
    # Control: otro número y otro archivo en la misma unidad es otro pago.
    nuevo = comprobante(e, "prop", e["u"]["1-A"], archivo("nuevo"))
    r = informar(e, "prop", datos(e["u"]["1-A"], nuevo, "CD-1"))
    assert (r.status_code, r.json()["ya_informado"]) == (201, False)
    # Control: el mismo número en otra unidad es otro pago (la clave lleva la unidad).
    r = informar(e, "inq", datos(e["u"]["PB-A"], comprobante(e, "inq", e["u"]["PB-A"],
                                                             archivo("pb")), NUMERO))  # fmt: skip
    assert (r.status_code, r.json()["ya_informado"]) == (201, False)
    assert filas(motor_migrate, e["cons"]) == 3
    # El mismo archivo informado para otra unidad: no se devuelve ese pago (sería una unidad
    # ajena), se niega.
    ajeno = comprobante(e, "prop", e["otra_de_prop"], contenido)
    r = informar(e, "prop", datos(e["otra_de_prop"], ajeno, "QQ-1"))
    assert codigo(r) == (409, "comprobante_de_otra_unidad")
    assert filas(motor_migrate, e["cons"]) == 3


def test_pa07_el_reintento_con_la_misma_clave_responde_lo_mismo(e, motor_migrate):
    clave = clave_idempotencia()
    cuerpo = datos(e["u"]["1-A"], comprobante(e, "prop", e["u"]["1-A"], archivo("reintento")))
    primero = informar(e, "prop", cuerpo, clave)
    segundo = informar(e, "prop", cuerpo, clave)
    assert primero.status_code == segundo.status_code == 201
    assert primero.json() == segundo.json()
    assert filas(motor_migrate, e["cons"]) == 1
    # Control: con otra clave, la base encuentra el mismo pago (PA07) y responde 200.
    tercero = informar(e, "prop", cuerpo)
    assert (tercero.status_code, tercero.json()["id"]) == (200, primero.json()["id"])


def test_pa07_dos_informes_a_la_vez_dejan_un_solo_registro(e, motor_app, motor_migrate):
    """El candado de los pagos del consorcio: el segundo espera al primero y encuentra su fila."""
    unidad = e["u"]["1-A"]
    p = {"u": unidad, "i": Decimal("10.00"), "f": e["hoy"], "m": "efectivo", "n": NUMERO,
         "comp": None}  # fmt: skip
    segundo: dict = {}
    with transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as primera:
        uno = primera.execute(INFORMAR, p | {"a": e["prop"], "s": e["sesion_prop"]}).one()
        pid = primera.execute(text("select pg_backend_pid()")).scalar_one()

        def otra() -> None:
            with transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as c:
                segundo["fila"] = c.execute(
                    INFORMAR, p | {"n": NUMERO_OTRA_FORMA, "a": e["prop"], "s": e["sesion_prop"]}
                ).one()

        hilo = threading.Thread(target=otra)
        hilo.start()
        assert esperar_trabado(motor_migrate, pid) == 1
    hilo.join()
    assert tuple(uno) == (uno.pago_id, False)
    assert tuple(segundo["fila"]) == (uno.pago_id, True)
    assert filas(motor_migrate, e["cons"]) == 1


def test_los_indices_unicos_cuentan_solo_los_estados_que_cuentan(e, motor_migrate):
    """Un pago rechazado o anulado (PI-S2) no impide informarlo corregido: el predicado de los
    índices parciales excluye esos estados, y `corrige_a` apunta al corregido."""
    m = _migracion()
    assert m.ESTADOS_QUE_NO_CUENTAN == ("rechazado", "anulado")
    with motor_migrate.connect() as c:
        definiciones = dict(
            c.execute(
                text(
                    "select indexname, indexdef from pg_indexes where schemaname = 'nucleo'"
                    " and indexname in ('pago_informado_un_numero', 'pago_informado_un_archivo')"
                )
            ).all()
        )
    no_cuentan = "(estado <> ALL (ARRAY['rechazado'::text, 'anulado'::text]))"
    assert definiciones == {
        "pago_informado_un_numero": (
            "CREATE UNIQUE INDEX pago_informado_un_numero ON nucleo.pago_informado USING btree"
            " (consorcio_id, unidad_id, numero_operacion_normalizado) WHERE"
            f" ((numero_operacion_normalizado IS NOT NULL) AND {no_cuentan})"),
        "pago_informado_un_archivo": (
            "CREATE UNIQUE INDEX pago_informado_un_archivo ON nucleo.pago_informado USING btree"
            f" (consorcio_id, comprobante_sha256) WHERE ((comprobante_sha256 IS NOT NULL) AND"
            f" {no_cuentan})"),
    }  # fmt: skip
    # El comportamiento, en una transacción que se deshace: con el check del estado ampliado como
    # lo hará PI-S2, un rechazado y su corrección con el mismo número conviven; dos que cuentan no.
    alta = text(
        "insert into nucleo.pago_informado (consorcio_id, unidad_id, importe, fecha_pago, medio,"
        " numero_operacion, origen, informado_por, estado, corrige_a)"
        " values (:c, :u, 1, :f, 'efectivo', :n, 'app', :p, :estado, :corrige) returning id"
    )
    base = {"c": e["cons"], "u": e["u"]["1-A"], "f": e["hoy"], "p": e["prop"]}
    with motor_migrate.connect() as c, c.begin() as t:
        c.execute(text("alter table nucleo.pago_informado drop constraint"
                       " pago_informado_estado_check"))  # fmt: skip
        rechazado = c.execute(alta, base | {"n": NUMERO, "estado": "rechazado",
                                            "corrige": None}).scalar_one()  # fmt: skip
        c.execute(alta, base | {"n": NUMERO_OTRA_FORMA, "estado": "informado",
                                "corrige": rechazado})  # fmt: skip
        with pytest.raises(IntegrityError) as error, c.begin_nested():
            c.execute(alta, base | {"n": NUMERO, "estado": "confirmado", "corrige": None})
        assert error.value.orig.diag.constraint_name == "pago_informado_un_numero"
        # `corrige_a` cita un pago del mismo consorcio.
        with pytest.raises(IntegrityError) as error, c.begin_nested():
            c.execute(alta, base | {"n": "X-1", "estado": "informado", "corrige": uuid4()})
        assert isinstance(error.value.orig, ForeignKeyViolation)
        t.rollback()
    assert filas(motor_migrate, e["cons"]) == 0


# --- Quién informa (PI-1) ----------------------------------------------------------------------


def test_informan_p_i_y_o_confirmados_por_su_unidad_y_nadie_mas(e, motor_migrate):
    for quien, unidad in (("prop", "1-A"), ("inq", "PB-A"), ("oc", "PB-A")):
        r = informar(
            e,
            quien,
            datos(
                e["u"][unidad], comprobante(e, quien, e["u"][unidad], archivo(quien)), f"N-{quien}"
            ),
        )
        assert (r.status_code, r.json()["propio"]) == (201, True), (quien, r.text)
    # Sin confirmar, otra unidad, el Consejo y la administración sin vínculo: no (ni el
    # comprobante). Las negaciones no escriben nada.
    antes = filas(motor_migrate, e["cons"])
    for quien, unidad in (("sin_confirmar", "1-A"), ("inq", "1-A"), ("mc", "1-A"),
                          ("ad", "1-A")):  # fmt: skip
        assert subir(e, quien, e["u"][unidad], archivo("no")).status_code == 403, quien
        cuerpo = datos(e["u"][unidad], None, medio="efectivo")
        assert informar(e, quien, cuerpo).status_code == 403, quien
    assert filas(motor_migrate, e["cons"]) == antes
    # Un comprobante de otra persona, o de otra unidad, no sirve.
    del_inq = comprobante(e, "inq", e["u"]["PB-A"], archivo("ajeno"))
    r = informar(e, "oc", datos(e["u"]["PB-A"], del_inq, "N-2"))
    assert codigo(r) == (422, "comprobante_invalido")
    de_otra = comprobante(e, "prop", e["otra_de_prop"], archivo("otra"))
    r = informar(e, "prop", datos(e["u"]["1-A"], de_otra, "N-3"))
    assert codigo(r) == (422, "comprobante_invalido")
    assert filas(motor_migrate, e["cons"]) == antes


def test_la_base_repite_la_barrera_de_quien_informa(e, motor_app):
    p = {"u": e["u"]["1-A"], "i": Decimal("1.00"), "f": e["hoy"], "m": "efectivo", "n": None,
         "comp": None}  # fmt: skip
    for quien in ("sin_confirmar", "mc", "inq"):
        with (
            pytest.raises(DBAPIError) as error,
            transaccion(motor_app, persona_id=e[quien], consorcio_id=e["cons"]) as c,
        ):
            c.execute(INFORMAR, p | {"a": e[quien], "s": e[f"sesion_{quien}"]})
        assert isinstance(error.value.orig, InsufficientPrivilege), quien
    # La sesión de otra persona no vale.
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(INFORMAR, p | {"a": e["prop"], "s": e["sesion_inq"]})
    assert error.value.orig.diag.message_hint == "sesion_invalida"
    with transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as c:  # control
        assert c.execute(INFORMAR, p | {"a": e["prop"], "s": e["sesion_prop"]}).one()


@pytest.mark.parametrize("modo", ["administrador_aumentado", "autogestion"])
def test_se_informa_en_los_tres_modos(motor_migrate, motor_app, bucket, modo):
    e = armar(motor_migrate, motor_app)
    cambiar(motor_app, e["cons"], modo, decidir_equipo(motor_app, e["cons"]), e["hoy"])
    r = informar(e, "prop", datos(e["u"]["1-A"], None, medio="efectivo", numero_operacion=None))
    assert (r.status_code, r.json()["ya_informado"]) == (201, False), r.text
    r = informar(e, "inq", datos(e["u"]["PB-A"], comprobante(e, "inq", e["u"]["PB-A"],
                                                             archivo(modo))))  # fmt: skip
    assert r.status_code == 201, r.text
    assert len(listar(e, "mc").json()["elementos"]) == 2


def test_quien_ayuda_informa_solo_con_preparar_reclamos(e, motor_migrate):
    solo_ver = ayuda(e, motor_migrate, ["consultar_expensas"])
    con = {ENCABEZADO_AYUDA: solo_ver}
    assert subir(e, "ayu", e["u"]["1-A"], archivo("ayu"), con).status_code == 403
    r = informar(e, "ayu", datos(e["u"]["1-A"], None, medio="efectivo"), encabezados=con)
    assert r.status_code == 403
    # Ver, sí, con consultar_expensas: lo de la unidad de la asistida.
    assert listar(e, "ayu", con).json()["elementos"] == []
    with cliente(e["prop"], e["sesion_prop"]) as c:
        assert c.post(ruta(e, "ayudas", solo_ver, "revocacion"),
                      headers=clave_idempotencia()).status_code == 200  # fmt: skip
    prepara = ayuda(e, motor_migrate, ["consultar_expensas", "preparar_reclamos"])
    con = {ENCABEZADO_AYUDA: prepara}
    r = subir(e, "ayu", e["u"]["1-A"], archivo("ayu"), con)
    assert r.status_code == 201, r.text
    r = informar(e, "ayu", datos(e["u"]["1-A"], r.json()["archivo"]["id"]), encabezados=con)
    assert r.status_code == 201, r.text
    # Queda a nombre de la asistida; la auditoría dice quién actuó y por quién.
    assert (r.json()["propio"], r.json()["unidad_id"]) == (True, str(e["u"]["1-A"]))
    with motor_migrate.connect() as cx:
        evento = cx.execute(text(
            "select actor_persona_id, en_representacion_de from auditoria.eventos"
            " where consorcio_id = :c and accion = 'consorcio.pago_informado.informar'"),
            {"c": e["cons"]}).one()  # fmt: skip
    assert tuple(evento) == (e["ayu"], e["prop"])
    # Ni ayudando se informa por una unidad que no es de la asistida.
    r = informar(e, "ayu", datos(e["u"]["PB-A"], None, medio="efectivo"), encabezados=con)
    assert r.status_code == 403
    assert len(listar(e, "ayu", con).json()["elementos"]) == 1


# --- Los datos (PI-2) --------------------------------------------------------------------------


def test_efectivo_sin_comprobante_si_y_transferencia_sin_comprobante_no(e, motor_app):
    r = informar(e, "prop", datos(e["u"]["1-A"], None, None, medio="efectivo"))
    assert r.status_code == 201, r.text
    assert (r.json()["comprobante_version_id"], r.json()["comprobante_estado"]) == (None, None)
    for medio in ("transferencia", "deposito", "mercado_pago", "otro"):
        r = informar(e, "prop", datos(e["u"]["1-A"], None, f"T-{medio}", medio=medio))
        assert r.status_code == 422, medio
    # Sin número de operación, solo en efectivo.
    con = comprobante(e, "prop", e["u"]["1-A"], archivo("sin-numero"))
    assert informar(e, "prop", datos(e["u"]["1-A"], con, None)).status_code == 422
    # La base repite la regla del medio.
    p = {"u": e["u"]["1-A"], "i": Decimal("1.00"), "f": e["hoy"], "m": "transferencia",
         "n": "T-1", "comp": None}  # fmt: skip
    with (
        pytest.raises(IntegrityError) as error,
        transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(INFORMAR, p | {"a": e["prop"], "s": e["sesion_prop"]})
    assert error.value.orig.diag.constraint_name == "pago_informado_comprobante_del_medio"


@pytest.mark.parametrize("importe", ["0", "0.00", "-1.00", "1.001"])
def test_el_importe_es_mayor_que_cero_con_dos_decimales(e, importe, motor_migrate):
    r = informar(e, "prop", datos(e["u"]["1-A"], None, medio="efectivo", importe=importe))
    assert r.status_code == 422
    assert filas(motor_migrate, e["cons"]) == 0
    # Control: un centavo.
    r = informar(e, "prop", datos(e["u"]["1-A"], None, medio="efectivo", importe="0.01"))
    assert r.status_code == 201


def test_la_base_niega_un_importe_cero(e, motor_app):
    p = {"u": e["u"]["1-A"], "i": Decimal("0"), "f": e["hoy"], "m": "efectivo", "n": None,
         "comp": None}  # fmt: skip
    with (
        pytest.raises(IntegrityError) as error,
        transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(INFORMAR, p | {"a": e["prop"], "s": e["sesion_prop"]})
    assert isinstance(error.value.orig, CheckViolation)
    assert error.value.orig.diag.constraint_name == "pago_informado_importe_check"


def test_la_fecha_no_es_futura_y_el_numero_no_es_una_cuenta(e):
    manana = e["hoy"] + timedelta(days=1)
    r = informar(e, "prop", datos(e["u"]["1-A"], None, medio="efectivo",
                                  fecha_pago=manana.isoformat()))  # fmt: skip
    assert codigo(r) == (422, "fecha_futura")
    assert r.json()["detalle"] == {"campos": ["body.fecha_pago"]}
    r = informar(e, "prop", datos(e["u"]["1-A"], None, cbu_sintetico(7), medio="efectivo"))
    assert codigo(r) == (422, "texto_con_cuenta")
    # Control: hoy y un número que no es una cuenta.
    r = informar(e, "prop", datos(e["u"]["1-A"], None, medio="efectivo",
                                  fecha_pago=e["hoy"].isoformat()))  # fmt: skip
    assert r.status_code == 201, r.text


# --- Lectura (PI-6) y el comprobante -------------------------------------------------------------


def test_cada_uno_ve_lo_de_su_unidad_y_el_panel_ve_todo(e, motor_app, motor_migrate):
    del_1a = informar(e, "prop", datos(e["u"]["1-A"], comprobante(
        e, "prop", e["u"]["1-A"], archivo("1a")))).json()  # fmt: skip
    del_pb = informar(e, "inq", datos(e["u"]["PB-A"], None, None, medio="efectivo")).json()

    def ids(quien: str) -> set[str]:
        r = listar(e, quien)
        assert r.status_code == 200, (quien, r.text)
        return {p["id"] for p in r.json()["elementos"]}

    assert ids("prop") == {del_1a["id"]}
    assert ids("inq") == ids("oc") == {del_pb["id"]}
    assert ids("mc") == ids("ad") == {del_1a["id"], del_pb["id"]}
    assert listar(e, "sin_confirmar").status_code == 403
    # El comprobante: en cuarentena, solo quien lo subió sabe que existe (409); nadie más lo ve.
    assert ver_comprobante(e, "prop", del_1a["id"]).status_code == 409
    assert ver_comprobante(e, "mc", del_1a["id"]).status_code == 404
    assert analizar(motor_app) >= 1
    vista = {p["id"]: p for p in listar(e, "mc").json()["elementos"]}
    assert vista[del_1a["id"]]["comprobante_estado"] == "disponible"
    for quien in ("prop", "mc", "ad"):
        r = ver_comprobante(e, quien, del_1a["id"])
        assert r.status_code == 200, quien
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.content == archivo("1a")
    for quien in ("inq", "oc"):
        assert ver_comprobante(e, quien, del_1a["id"]).status_code == 404, quien
    # Sin comprobante (efectivo): no hay nada que ver.
    assert ver_comprobante(e, "mc", del_pb["id"]).status_code == 404


def test_rls_forzada_y_otro_consorcio_no_ve_los_pagos(e, motor_app, motor_migrate):
    otro = armar(motor_migrate, motor_app)
    pago = informar(e, "prop", datos(e["u"]["1-A"], None, medio="efectivo")).json()
    assert listar(otro, "mc").json()["elementos"] == []
    assert [p["id"] for p in listar(e, "mc").json()["elementos"]] == [pago["id"]]  # control
    with transaccion(motor_app, persona_id=otro["mc"], consorcio_id=otro["cons"]) as c:
        assert c.execute(text("select * from nucleo.pagos_informados_lectura(:a, 'api', :s)"),
                         {"a": otro["mc"], "s": otro["sesion_mc"]}).all() == []  # fmt: skip
    # Informar por una unidad de otro edificio: no es una unidad con vínculo en este.
    r = informar(otro, "prop", datos(e["u"]["1-A"], None, medio="efectivo"))
    assert r.status_code == 403
    with motor_migrate.connect() as cx:
        rls = cx.execute(text(
            "select c.oid::regclass::text, c.relrowsecurity, c.relforcerowsecurity,"
            " (select p.qual from pg_policies p where p.schemaname = 'nucleo'"
            "   and p.tablename = c.relname and p.policyname = 'por_consorcio')"
            " from pg_class c where c.oid = any(cast(:t as regclass[])) order by 1"),
            {"t": list(TABLAS)}).all()  # fmt: skip
        politica = "(consorcio_id = plataforma.consorcio_actual())"
        assert [tuple(f) for f in rls] == [(t, True, True, politica) for t in sorted(TABLAS)]
        for tabla in TABLAS:
            assert not cx.execute(
                text("select has_table_privilege('cem_app', :t, 'select, insert, update, delete')"),
                {"t": tabla}).scalar_one(), tabla  # fmt: skip
    # cem_app no lee la tabla ni con el ámbito del consorcio.
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(text("select * from nucleo.pago_informado")).all()
    assert isinstance(error.value.orig, InsufficientPrivilege)


def test_la_historia_no_se_cambia_ni_se_borra(e, motor_migrate):
    informar(e, "prop", datos(e["u"]["1-A"], comprobante(e, "prop", e["u"]["1-A"],
                                                         archivo("h"))))  # fmt: skip
    for sentencia in ("update nucleo.pago_informado set importe = 2",
                      "delete from nucleo.pago_informado",
                      "update nucleo.comprobante_pago_subido set subido_en = now()",
                      "delete from nucleo.comprobante_pago_subido"):  # fmt: skip
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            c.execute(text(sentencia))
        assert error.value.orig.diag.message_hint == "historia_inmutable", sentencia
    # El comprobante citado ya no se quita (`documento_referenciado`, 0089).
    with motor_migrate.connect() as c:
        doc = c.execute(
            text(
                "select comprobante_documento_id from nucleo.pago_informado where consorcio_id = :c"
            ),
            {"c": e["cons"]},
        ).scalar_one()
        assert c.execute(text("select nucleo.documento_referenciado(:c, :d)"),
                         {"c": e["cons"], "d": doc}).scalar_one() is True  # fmt: skip


# --- Contrato, privilegios, exportación y migración ---------------------------------------------


def test_la_migracion_copia_las_listas_y_limites_del_contrato():
    m = _migracion()
    assert m.MEDIOS == tuple(contrato.MedioPago)
    assert m.MEDIO_SIN_COMPROBANTE == contrato.MEDIO_SIN_COMPROBANTE
    # `0130` suma los otros tres (lo compara `test_pagos_informados_0130.py`).
    assert m.ESTADOS == (contrato.EstadoPagoInformado.INFORMADO,)
    assert m.ESTADOS_QUE_NO_CUENTAN == contrato.ESTADOS_QUE_NO_CUENTAN
    assert m.ORIGENES == tuple(contrato.OrigenPagoInformado)
    assert m.LARGO_NUMERO_OPERACION == contrato.LARGO_NUMERO_OPERACION


def test_las_rutas_son_solo_para_cem_app_y_las_internas_para_nadie(motor_migrate):
    m = _migracion()
    # Las que `0130` borra y crea con otra firma o con otro resultado las prueba su archivo.
    reemplazadas = {f for f, _ in _migracion(MIGRACION_0130).REEMPLAZADAS}
    assert reemplazadas < set(m.RUTAS)  # control: las nombra igual que esta migración
    puede = text("select has_function_privilege(:r, :f, 'EXECUTE')")
    with motor_migrate.connect() as c:
        for f in set(m.RUTAS) - reemplazadas:
            assert c.execute(puede, {"r": "cem_app", "f": f}).scalar_one() is True, f
            assert c.execute(puede, {"r": "cem_ro", "f": f}).scalar_one() is False, f
        for f in m.INTERNAS:
            assert c.execute(puede, {"r": "cem_app", "f": f}).scalar_one() is False, f


def test_la_exportacion_lleva_los_pagos_informados_sin_identificadores(
    e, motor_migrate, con_url, claves, tmp_path
):
    con = informar(e, "prop", datos(e["u"]["1-A"], comprobante(
        e, "prop", e["u"]["1-A"], archivo("exp")))).json()  # fmt: skip
    sin = informar(e, "inq", datos(e["u"]["PB-A"], None, None, medio="efectivo")).json()
    salida = tmp_path / f"exportacion-{uuid4().hex[:8]}.zip.age"
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 0
    paquete = descifrar(salida, claves["privada"])
    assert verificar_manifiesto(paquete) == []
    datos_ = entidades(paquete)
    assert [
        (x["id"], x["unidad_designacion"], x["numero_operacion"], x["medio"])
        for x in datos_["pago_informado"]
    ] == [(con["id"], "1-A", NUMERO, "transferencia"), (sin["id"], "PB-A", None, "efectivo")]
    assert [x["version_documento_id"] for x in datos_["comprobante_pago"]] == [
        con["comprobante_version_id"]
    ]
    assert {x["informado_por_nombre"] for x in datos_["pago_informado"]} == {
        con["informado_por_nombre"], sin["informado_por_nombre"]}  # fmt: skip
    crudo = json.dumps(datos_["pago_informado"]) + json.dumps(datos_["comprobante_pago"])
    for persona in ("prop", "inq"):
        assert str(e[persona]) not in crudo
        assert correo_de(motor_migrate, e[persona]) not in crudo


def test_0129_ida_y_vuelta_y_no_baja_con_datos(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0128")

    def tablas() -> list[str]:
        with motor.connect() as c:
            return sorted(c.execute(text(
                "select to_regclass(t)::text from unnest(cast(:t as text[])) t"
                " where to_regclass(t) is not null"), {"t": list(TABLAS)}).scalars())  # fmt: skip

    def fijar() -> str:
        with motor.connect() as c:
            return c.execute(text("select pg_get_functiondef("
                                  "'nucleo.fijar_version_objeto(uuid, text, uuid, text)'"
                                  "::regprocedure)")).scalar_one()  # fmt: skip

    antes = fijar()
    assert tablas() == []
    command.upgrade(cfg, "0129")
    assert tablas() == sorted(TABLAS)
    assert "comprobante_pago_de" in fijar()
    command.downgrade(cfg, "0128")  # sin datos: baja y deja las funciones como estaban
    assert (tablas(), fijar()) == ([], antes)
    command.upgrade(cfg, "0129")  # control: vuelve a subir limpio
    with motor.begin() as c:
        cons = c.execute(text("insert into nucleo.consorcio (nombre) values ('A') returning id"))\
            .scalar_one()  # fmt: skip
        uni = c.execute(
            text(
                "insert into nucleo.unidad (consorcio_id, designacion)"
                " values (:c, '1') returning id"
            ),
            {"c": cons},
        ).scalar_one()
        per = c.execute(text(
            "insert into nucleo.persona (correo, nombre) values (:c, 'P') returning id"),
            {"c": f"p-{uuid4().hex[:8]}@e.com"}).scalar_one()  # fmt: skip
        c.execute(text(
            "insert into nucleo.pago_informado (consorcio_id, unidad_id, importe, fecha_pago,"
            " medio, origen, informado_por) values (:c, :u, 1, '2026-11-01', 'efectivo', 'app',"
            " :p)"), {"c": cons, "u": uni, "p": per})  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0128")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
    assert tablas() == sorted(TABLAS)
