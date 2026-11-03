"""Quien transfirió y perdió la facultad registra el pago y carga el comprobante (`0068`, P3 184;
opción A del propietario, 03-11-2026).

La escena es la de `test_pago_manual_0055`: la administración prepara y ejecuta, mc1 aprueba y mc2
concilia. Datos SINTETICOS / NO REAL.
"""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import clave_idempotencia
from tests.test_comprobantes_0063 import CREAR, TITULO
from tests.test_documentos_2c1 import (  # noqa: F401  (`bucket` y `firmas_de_la_imagen` son fixtures)
    analizar,
    bucket,
    firmas_de_la_imagen,
    pdf,
)
from tests.test_egresos_1a import _hoy, envejecer
from tests.test_mandatos import otra_persona
from tests.test_orden_pago_neutra import huella, llamar, niega, nueva_sesion, orden
from tests.test_ordenes_trabadas_0067 import (
    VENCIDA,
    dar_de_baja,
    lista_por_aclarar,
    por_aclarar,
    trabada,
)
from tests.test_pago_manual_0055 import (
    CONCILIAR,
    CUENTA,
    IMPORTE,
    REGISTRAR,
    emitida,
    evidencia,
    pagos_de,
    ver,
)
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_rutas_egresos import cliente, codigo, post
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db

VERSION = (
    "select id from nucleo.nueva_version_de_comprobante(:orden, :documento, 'application/pdf', 1,"
    " repeat('d', 64), :actor, :origen, :sesion)"
)


def analizada(motor_migrate, version) -> None:
    """Lo que hace el antivirus con un archivo limpio."""
    with motor_migrate.begin() as c:
        n = c.execute(
            text("update nucleo.version_documento set estado = 'disponible',"
                 " antivirus = 'ClamAV SINTETICO', firmas_en = now(), analizada_en = now()"
                 " where id = :v"), {"v": version},
        ).rowcount  # fmt: skip
    assert n == 1


def comprobante_sin_facultad(motor_migrate, e, o) -> dict:
    """Quien renunció crea el comprobante desde el pago, sube el archivo y el antivirus lo deja
    disponible."""
    doc = llamar(motor_migrate, e, e.adm, CREAR, orden=o, titulo=TITULO)
    v = llamar(motor_migrate, e, e.adm, VERSION, orden=o, documento=doc)
    analizada(motor_migrate, v)
    return {"documento": doc, "version_archivo": v}


def pagos_sin_facultad(motor_migrate, o) -> list[tuple]:
    return [(p["registrado_por"], p["sin_facultad"], p["fuera_de_termino"])
            for p in pagos_de(motor_migrate, o)]  # fmt: skip


def test_quien_renuncio_despues_de_transferir_registra_y_otro_consejero_concilia(
    motor_migrate, motor_app, e
):
    o = trabada(motor_migrate, motor_app, e)  # vio la cuenta, renunció, aprobación vencida
    doc = comprobante_sin_facultad(motor_migrate, e, o)["documento"]
    p = llamar(motor_migrate, e, e.adm, REGISTRAR,
               **evidencia(motor_migrate, e, o, documento=doc))  # fmt: skip
    assert pagos_sin_facultad(motor_migrate, o) == [(e.adm, True, True)]
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="conforme",
           motivo="SINTETICO: transferencia hecha por la administración anterior")  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "pagada"


def test_con_la_facultad_el_pago_no_queda_marcado(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o))
    assert pagos_sin_facultad(motor_migrate, o) == [(e.adm, False, False)]


def test_sin_la_facultad_solo_quien_vio_la_cuenta_carga_y_registra(motor_migrate, motor_app, e):
    """mc2 (sin `ejecutar_pago`) y una persona cualquiera no vieron la cuenta: la base les niega
    crear el comprobante, subir el archivo y registrar, sin decir si la orden existe."""
    o = trabada(motor_migrate, motor_app, e)
    datos = comprobante_sin_facultad(motor_migrate, e, o)
    x = otra_persona(motor_migrate, e.cons)
    e.sesion[x] = nueva_sesion(motor_migrate, x)
    for quien in (e.mc2, x):
        niega(motor_migrate, e, quien, CREAR, "42501", orden=o, titulo=TITULO)
        niega(motor_migrate, e, quien, VERSION, "42501", orden=o, documento=datos["documento"])
        niega(motor_migrate, e, quien, REGISTRAR, "42501",
              **evidencia(motor_migrate, e, o, documento=datos["documento"]))  # fmt: skip
    llamar(
        motor_migrate,
        e,
        e.adm,
        REGISTRAR,
        **evidencia(motor_migrate, e, o, documento=datos["documento"]),
    )  # control


def test_un_extrano_con_el_segundo_factor_viejo_recibe_lo_mismo(motor_migrate, motor_app, e):
    """Primero si vio la cuenta: un extraño sin el segundo factor reciente recibe 42501, no el
    pedido de renovarlo."""
    o = trabada(motor_migrate, motor_app, e)
    x = otra_persona(motor_migrate, e.cons)
    e.sesion[x] = nueva_sesion(motor_migrate, x, horas_atras=1, totp_min_atras=None)
    niega(motor_migrate, e, x, CREAR, "42501", orden=o, titulo=TITULO)
    niega(motor_migrate, e, x, REGISTRAR, "42501", **evidencia(motor_migrate, e, o))


@pytest.mark.parametrize(
    ("horas_atras", "totp_min_atras", "motivo"),
    [(1, 11, "requiere_segundo_factor_reciente"), (13, 0, "requiere_sesion_reciente")],
)
def test_sin_la_facultad_cargar_y_registrar_exigen_r10(
    motor_migrate, motor_app, e, horas_atras, totp_min_atras, motivo
):
    o = trabada(motor_migrate, motor_app, e)
    datos = comprobante_sin_facultad(motor_migrate, e, o)
    vieja = nueva_sesion(motor_migrate, e.adm, horas_atras=horas_atras,
                         totp_min_atras=totp_min_atras)  # fmt: skip
    niega(motor_migrate, e, e.adm, CREAR, motivo, sesion=vieja, orden=o, titulo=TITULO)
    niega(motor_migrate, e, e.adm, VERSION, motivo, sesion=vieja, orden=o,
          documento=datos["documento"])  # fmt: skip
    niega(motor_migrate, e, e.adm, REGISTRAR, motivo, sesion=vieja,
          **evidencia(motor_migrate, e, o, documento=datos["documento"]))  # fmt: skip
    llamar(
        motor_migrate,
        e,
        e.adm,
        REGISTRAR,
        **evidencia(motor_migrate, e, o, documento=datos["documento"]),
    )  # control


def test_el_archivo_solo_para_un_comprobante_de_esa_orden(motor_migrate, motor_app, e):
    """El documento tiene que ser un comprobante cargado desde el pago de esa misma orden."""
    o = emitida(motor_migrate, e)
    otra = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    ver(motor_migrate, e, e.adm, otra)
    doc_otra = llamar(motor_migrate, e, e.adm, CREAR, orden=otra, titulo=TITULO)
    dar_de_baja(motor_migrate, motor_app, e, e.adm)
    niega(motor_migrate, e, e.adm, VERSION, "42501", orden=o, documento=doc_otra)
    niega(motor_migrate, e, e.adm, VERSION, "42501", orden=o, documento=uuid4())
    assert llamar(motor_migrate, e, e.adm, VERSION, orden=otra, documento=doc_otra)  # control


def test_por_aclarar_trae_la_huella_y_el_comprobante_propio(motor_migrate, motor_app, e):
    o = trabada(motor_migrate, motor_app, e)
    [fila] = por_aclarar(motor_migrate, e, e.adm)
    assert fila["huella"] == huella(motor_migrate, o, 1).hex()
    assert (fila["comprobante_id"], fila["comprobante_estado"]) == (None, None)
    doc = llamar(motor_migrate, e, e.adm, CREAR, orden=o, titulo=TITULO)
    v = llamar(motor_migrate, e, e.adm, VERSION, orden=o, documento=doc)
    [fila] = por_aclarar(motor_migrate, e, e.adm)
    assert (fila["comprobante_id"], fila["comprobante_estado"]) == (doc, "en_cuarentena")
    analizada(motor_migrate, v)
    [fila] = por_aclarar(motor_migrate, e, e.adm)
    assert fila["comprobante_estado"] == "disponible"
    llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o, documento=doc))
    assert por_aclarar(motor_migrate, e, e.adm) == []  # registrado: ya no está para aclarar
    assert all(CUENTA not in str(v) for v in fila.values())


def test_un_pago_observado_vuelve_a_la_lista_y_se_corrige(motor_migrate, motor_app, e):
    o = trabada(motor_migrate, motor_app, e)
    doc = comprobante_sin_facultad(motor_migrate, e, o)["documento"]
    p = llamar(motor_migrate, e, e.adm, REGISTRAR,
               **evidencia(motor_migrate, e, o, documento=doc))  # fmt: skip
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="observado",
           motivo="SINTETICO: el número no coincide")  # fmt: skip
    assert [f["orden_id"] for f in por_aclarar(motor_migrate, e, e.adm)] == [o]
    v = llamar(motor_migrate, e, e.adm, VERSION, orden=o, documento=doc)
    analizada(motor_migrate, v)
    llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o, documento=doc))
    assert [x[1] for x in pagos_sin_facultad(motor_migrate, o)] == [True, True]


def test_en_termino_sin_la_facultad_no_queda_fuera_de_termino(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    dar_de_baja(motor_migrate, motor_app, e, e.adm)
    doc = comprobante_sin_facultad(motor_migrate, e, o)["documento"]
    llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o, documento=doc))
    assert pagos_sin_facultad(motor_migrate, o) == [(e.adm, True, False)]
    assert IMPORTE > 0 and VENCIDA.days == 8 and envejecer  # las de la escena


# La migración ----------------------------------------------------------------------------------


def migracion_0068():
    ruta = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    archivo = ruta / "0068_registro_sin_facultad.py"
    spec = importlib.util.spec_from_file_location("migracion_0068", archivo)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_0068_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    m = migracion_0068()
    funciones = (m.REGISTRO, m.CREAR, m.POR_ACLARAR)

    def estado() -> tuple:
        with motor.connect() as c:
            defs = tuple(
                c.execute(
                    text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": f}
                ).scalar_one()
                for f in funciones
            )
            permisos = tuple(
                c.execute(
                    text(
                        "select (has_function_privilege('public', cast(:f as regprocedure),"
                        " 'execute'), has_function_privilege('cem_app',"
                        " cast(:f as regprocedure), 'execute'))::text"
                    ),
                    {"f": f},
                ).scalar_one()
                for f in funciones
            )
            version = c.execute(
                text("select to_regprocedure(:f) is not null"), {"f": m.VERSION_SIN_FACULTAD}
            ).scalar_one()
            columna = c.execute(
                text(
                    "select count(*) from information_schema.columns where table_schema = 'nucleo'"
                    " and table_name = 'pago_manual' and column_name = 'sin_facultad'"
                )
            ).scalar_one()
        return defs, permisos, version, columna

    command.upgrade(cfg, "0067")
    antes = estado()
    assert antes[2:] == (False, 0)
    command.upgrade(cfg, "0068")
    despues = estado()
    assert despues[1] == antes[1]  # los mismos permisos
    assert despues[2:] == (True, 1)
    assert all(a != d for a, d in zip(antes[0], despues[0], strict=True))
    command.downgrade(cfg, "0067")
    assert estado() == antes
    command.upgrade(cfg, "0068")  # control
    assert estado()[2:] == (True, 1)


def test_0068_no_baja_con_un_pago_sin_la_facultad(motor_migrate, motor_app, e):
    o = trabada(motor_migrate, motor_app, e)
    doc = comprobante_sin_facultad(motor_migrate, e, o)["documento"]
    llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o, documento=doc))
    m = migracion_0068()
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(text(m.GUARDA_DOWNGRADE))
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"


# Por HTTP --------------------------------------------------------------------------------------


def subir_comprobante(e, quien, o, doc, contenido: bytes):
    with cliente(quien, e.sesion[quien]) as c:
        return c.post(
            f"/api/v1/consorcios/{e.cons}/pagos-por-aclarar/{o}/comprobante/{doc}/versiones",
            content=contenido, headers={"Content-Type": "application/pdf"} | clave_idempotencia(),
        )  # fmt: skip


def registro(motor_migrate, e, fila, doc) -> dict:
    """Lo que escribe quien transfirió: la cuenta la tipea, el resto sale de su comprobante."""
    return {
        "version": fila["version"], "huella": fila["huella"], "numero_operacion": "0001-234567",
        "fecha_operacion": str(_hoy(motor_migrate, e)), "importe_centavos": IMPORTE,
        "moneda": "ARS", "cuenta_destino": CUENTA, "documento_id": doc,
    }  # fmt: skip


def test_por_http_quien_renuncio_carga_sube_registra_y_el_consejo_concilia(
    motor_migrate,
    motor_app,
    e,
    bucket,  # noqa: F811
):
    o = trabada(motor_migrate, motor_app, e)
    base = f"pagos-por-aclarar/{o}"
    r = post(e, e.adm, f"{base}/comprobante", {"titulo": TITULO, "fecha": "2026-11-03"})
    assert r.status_code == 201, r.text
    doc = r.json()["id"]
    r = subir_comprobante(e, e.adm, o, doc, pdf())
    assert (r.status_code, r.json()["estado"]) == (201, "en_cuarentena"), r.text
    [fila] = lista_por_aclarar(e, e.adm)
    assert (fila["comprobante_id"], fila["comprobante_estado"]) == (doc, "en_cuarentena")
    assert analizar(motor_app) == 1
    [fila] = lista_por_aclarar(e, e.adm)
    assert fila["comprobante_estado"] == "disponible"
    r = post(e, e.adm, f"{base}/pago", registro(motor_migrate, e, fila, doc))
    assert r.status_code == 201, r.text
    assert lista_por_aclarar(e, e.adm) == []
    assert pagos_sin_facultad(motor_migrate, o) == [(e.adm, True, True)]
    r = post(e, e.mc2, f"ordenes-pago/{o}/pago-manual/{r.json()['id']}/conciliacion",
             {"resultado": "conforme", "motivo": "SINTETICO: administración anterior"})  # fmt: skip
    assert (r.status_code, r.json()["estado"]) == (200, "pagada"), r.text


def test_por_http_a_quien_no_vio_la_cuenta_cada_ruta_le_responde_404(
    motor_migrate,
    motor_app,
    e,
    bucket,  # noqa: F811
):
    o = trabada(motor_migrate, motor_app, e)
    x = otra_persona(motor_migrate, e.cons)
    e.sesion[x] = nueva_sesion(motor_migrate, x)
    base = f"pagos-por-aclarar/{o}"
    nuevo = {"titulo": TITULO, "fecha": "2026-11-03"}
    for quien in (e.mc2, x):
        assert codigo(post(e, quien, f"{base}/comprobante", nuevo)) == (404, "no_encontrado")
    r = post(e, e.adm, f"{base}/comprobante", nuevo)  # control
    assert r.status_code == 201, r.text
    doc = r.json()["id"]
    for quien in (e.mc2, x):
        assert codigo(subir_comprobante(e, quien, o, doc, pdf())) == (404, "no_encontrado")
    assert subir_comprobante(e, e.adm, o, doc, pdf()).status_code == 201  # control
    assert analizar(motor_app) == 1
    [fila] = lista_por_aclarar(e, e.adm)
    cuerpo = registro(motor_migrate, e, fila, doc)
    for quien in (e.mc2, x):
        assert codigo(post(e, quien, f"{base}/pago", cuerpo)) == (404, "no_encontrado")
    assert pagos_sin_facultad(motor_migrate, o) == []
    assert post(e, e.adm, f"{base}/pago", cuerpo).status_code == 201  # control
