"""Seguros y habilitaciones con vigencia en la ficha del proveedor (`0103`; RF50; documento 14,
§5.2): un documento vencido marca al proveedor y no lo excluye.

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. Cada negación lleva su
control: el mismo pedido de quien sí puede, o sobre una constancia que sí lo admite, pasa. Datos
SINTETICOS / PRUEBA / NO REAL: ninguna póliza ni matrícula existe.
"""

from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import clave_idempotencia
from tests.test_documentos_2c1 import (  # noqa: F401  (`bucket` y `firmas_de_la_imagen` son fixtures)
    analizar,
    bucket,
    firmas_de_la_imagen,
    pdf,
)
from tests.test_modo_operacion import hoy_de
from tests.test_orden_pago_neutra import escena_economica, llamar, niega
from tests.test_quitar_retirar_0089 import retirar
from tests.test_rutas_egresos import cliente, codigo, get, post, preparar
from tests.test_rutas_egresos import e as e  # noqa: F401  (es un fixture)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# Los fixtures importados no se redefinen con el parámetro de su nombre.
# ruff: noqa: F811

TITULO = "Poliza SINTETICA 0001 (NO REAL)"
DESCRIPCION = "ART del personal SINTETICA"
ACCION = "consorcio.proveedor.cargar_documentacion"
DIAS_POR_VENCER = 30


def subir(e, proveedor=None, vence: date | None = None, *, quien: UUID | None = None,
          tipo: str = "seguro", descripcion: str = DESCRIPCION, reemplaza: str | None = None,
          archivo: bytes | None = None, clave: dict | None = None):  # fmt: skip
    quien = quien or e.adm
    params = {"tipo": tipo, "descripcion": descripcion, "titulo": TITULO,
              "vence_el": (vence or date(2099, 12, 31)).isoformat()}  # fmt: skip
    if reemplaza is not None:
        params["reemplaza"] = reemplaza
    with cliente(quien, e.sesion.get(quien)) as c:
        return c.post(
            f"/api/v1/consorcios/{e.cons}/proveedores/{proveedor or e.proveedor}/documentacion",
            params=params,
            content=archivo or pdf(),
            headers={"Content-Type": "application/pdf"} | (clave or clave_idempotencia()),
        )


def cargada(motor_app, e, vence: date, **kw) -> dict:
    """Subida y aceptada por el antivirus: devuelve la respuesta de la subida."""
    r = subir(e, vence=vence, **kw)
    assert r.status_code == 201, r.text
    assert analizar(motor_app) == 1
    return r.json()


def ficha(e, proveedor=None, quien: UUID | None = None) -> dict:
    r = get(e, quien or e.adm, f"proveedores/{proveedor or e.proveedor}")
    assert r.status_code == 200, r.text
    return r.json()


def estados(e, proveedor=None) -> dict[str, str]:
    return {d["id"]: d["estado"] for d in ficha(e, proveedor)["documentacion"]}


def lista(e) -> dict:
    r = get(e, e.adm, "proveedores")
    assert r.status_code == 200, r.text
    return r.json()


def constancias(motor_migrate, proveedor) -> list[dict]:
    with motor_migrate.connect() as c:
        return [dict(f) for f in c.execute(
            text("select * from nucleo.documentacion_proveedor where proveedor_id = :p"
                 " order by cargada_en, id"), {"p": proveedor},
        ).mappings()]  # fmt: skip


def proveedor_entero(motor_migrate, p) -> tuple[list[dict], list[dict]]:
    with motor_migrate.connect() as c:
        return (
            [dict(f) for f in c.execute(text("select * from nucleo.proveedor where id = :p"),
                                        {"p": p}).mappings()],
            [dict(f) for f in c.execute(text("select * from nucleo.proveedor_version where"
                                             " proveedor_id = :p order by numero"),
                                        {"p": p}).mappings()],
        )  # fmt: skip


# El acto y el estado ---------------------------------------------------------------------------


def test_cuenta_recien_cuando_el_antivirus_la_acepta(motor_migrate, motor_app, e, bucket):
    r = subir(e)
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert cuerpo["archivo"]["estado"] == "en_cuarentena"
    assert estados(e) == {cuerpo["constancia_id"]: "en_revision"}

    assert analizar(motor_app) == 1
    f = ficha(e)
    [d] = f["documentacion"]
    assert (d["estado"], d["tipo"], d["descripcion"]) == ("vigente", "seguro", DESCRIPCION)
    assert d["documento_id"] == cuerpo["documento_id"]
    assert d["version_documento_id"] == cuerpo["archivo"]["id"]
    assert d["cargada_por_nombre"] and d["documento_vigente"] is True
    assert f["puede_cargar_documentacion"] is True
    # El documento nace en la categoría restringida, con la fecha del día del consorcio.
    with motor_migrate.connect() as c:
        doc = c.execute(text("select categoria, fecha from nucleo.documento where id = :d"),
                        {"d": cuerpo["documento_id"]}).one()  # fmt: skip
    assert (doc.categoria, doc.fecha) == ("contratos_proveedores", hoy_de(motor_app, e.cons))


def test_el_estado_sale_de_la_fecha_del_consorcio(motor_migrate, motor_app, e, bucket):
    hoy = hoy_de(motor_app, e.cons)
    casos = {
        hoy - timedelta(days=1): "vencida",
        hoy: "por_vencer",
        hoy + timedelta(days=DIAS_POR_VENCER): "por_vencer",
        hoy + timedelta(days=DIAS_POR_VENCER + 1): "vigente",
    }
    ids = {}
    for i, (vence, _) in enumerate(casos.items()):
        ids[vence] = cargada(
            motor_app,
            e,
            vence,
            descripcion=f"Seguro SINTETICO {i}",
            archivo=pdf(flujo=f"0.{i} g 0 0 9 9 re f".encode()),
        )["constancia_id"]
    vistos = estados(e)
    assert {vence: vistos[ids[vence]] for vence in casos} == casos


def test_vencido_marca_y_no_excluye(motor_migrate, motor_app, e, bucket):
    hoy = hoy_de(motor_app, e.cons)
    cargada(motor_app, e, hoy - timedelta(days=1))
    cargada(motor_app, e, hoy + timedelta(days=5), proveedor=e.otro)
    marcas = lista(e)
    assert marcas["documentacion_vencida"] == [str(e.proveedor)]
    assert marcas["documentacion_por_vencer"] == [str(e.otro)]

    # No excluye: se prepara, se envía y se ve una orden al proveedor marcado.
    o = preparar(e)
    r = post(e, e.adm, f"ordenes-pago/{o}/enviar-a-revision", {"version_esperada": 1})
    assert r.status_code == 200, r.text
    for quien in (e.adm, e.mc1):
        aviso = get(e, quien, f"pagos/{o}").json()["documentacion_proveedor"]
        assert [d["estado"] for d in aviso] == ["vencida"], quien
    # Control: a otro proveedor con todo vigente la ficha no le avisa nada.
    o2 = preparar(e, proveedor_id=str(e.otro))
    assert [d["estado"] for d in get(e, e.adm, f"pagos/{o2}").json()["documentacion_proveedor"]] \
        == ["por_vencer"]  # fmt: skip


def test_sin_constancias_no_marca(motor_migrate, motor_app, e, bucket):
    marcas = lista(e)
    assert (marcas["documentacion_vencida"], marcas["documentacion_por_vencer"]) == ([], [])
    assert ficha(e)["documentacion"] == []


def test_la_constancia_y_el_evento_dicen_quien_y_cuando(motor_migrate, motor_app, e, bucket):
    vence = hoy_de(motor_app, e.cons) + timedelta(days=90)
    cuerpo = cargada(motor_app, e, vence)
    [k] = constancias(motor_migrate, e.proveedor)
    assert (k["cargada_por"], k["vence_el"], k["reemplaza_a"]) == (e.adm, vence, None)
    with motor_migrate.connect() as c:
        [ev] = c.execute(text(
            "select actor_persona_id, despues from auditoria.eventos where accion = :a"
            " and entidad = 'proveedor' and entidad_id = :p"), {"a": ACCION, "p": e.proveedor},
        ).mappings().all()  # fmt: skip
    assert ev["actor_persona_id"] == e.adm
    assert ev["despues"]["constancia_id"] == cuerpo["constancia_id"]
    assert ev["despues"]["vence_el"] == vence.isoformat()
    # La descripción es texto libre: no va a la auditoría.
    assert DESCRIPCION not in str(ev["despues"])


def test_no_crea_version_del_proveedor(motor_migrate, motor_app, e, bucket):
    antes = proveedor_entero(motor_migrate, e.proveedor)
    cargada(motor_app, e, date(2099, 1, 1))
    assert proveedor_entero(motor_migrate, e.proveedor) == antes


# Reemplazar ------------------------------------------------------------------------------------


def test_reemplazar_deja_la_anterior_como_reemplazada(motor_migrate, motor_app, e, bucket):
    hoy = hoy_de(motor_app, e.cons)
    vieja = cargada(motor_app, e, hoy - timedelta(days=3))["constancia_id"]
    assert lista(e)["documentacion_vencida"] == [str(e.proveedor)]

    r = subir(e, vence=hoy + timedelta(days=365), reemplaza=vieja,
              archivo=pdf(flujo=b"0.7 g 0 0 9 9 re f"))  # fmt: skip
    assert r.status_code == 201, r.text
    nueva = r.json()["constancia_id"]
    # Mientras el reemplazo está en el antivirus vale la anterior, y no se acepta otro.
    assert estados(e) == {vieja: "vencida", nueva: "en_revision"}
    assert codigo(subir(e, reemplaza=vieja)) == (409, "documentacion_en_revision")

    assert analizar(motor_app) == 1
    assert estados(e) == {vieja: "reemplazada", nueva: "vigente"}
    assert lista(e)["documentacion_vencida"] == []
    assert constancias(motor_migrate, e.proveedor)[1]["reemplaza_a"] == UUID(vieja)
    # Lo reemplazado ya no se reemplaza.
    assert codigo(subir(e, reemplaza=vieja)) == (409, "documentacion_no_vigente")


def test_si_el_antivirus_rechaza_el_reemplazo_sigue_valiendo_la_anterior(motor_migrate, motor_app,
                                                                       e, bucket):  # fmt: skip
    vieja = cargada(motor_app, e, date(2099, 1, 1))["constancia_id"]
    r = subir(e, reemplaza=vieja, archivo=pdf(js="app.alert(1)"))
    assert r.status_code == 201, r.text
    while analizar(motor_app):
        pass
    assert estados(e) == {vieja: "vigente", r.json()["constancia_id"]: "rechazada"}
    # Control: se puede reemplazar de nuevo.
    assert subir(e, reemplaza=vieja).status_code == 201


def test_reemplazar_lo_que_no_vale_se_niega(motor_migrate, motor_app, e, bucket):
    seguro = cargada(motor_app, e, date(2099, 1, 1))["constancia_id"]
    de_otro = cargada(motor_app, e, date(2099, 1, 1), proveedor=e.otro,
                      archivo=pdf(flujo=b"0.8 g 0 0 9 9 re f"))["constancia_id"]  # fmt: skip
    # La última: el antivirus toma las subidas en orden y esta queda en revisión.
    en_revision = subir(e, descripcion="Otro SINTETICO").json()["constancia_id"]
    assert estados(e)[en_revision] == "en_revision"
    assert codigo(subir(e, reemplaza=en_revision)) == (409, "documentacion_no_vigente")
    assert codigo(subir(e, reemplaza=de_otro)) == (409, "documentacion_no_vigente")
    assert codigo(subir(e, reemplaza=str(uuid4()))) == (409, "documentacion_no_vigente")
    assert codigo(subir(e, reemplaza=seguro, tipo="habilitacion")) == (
        409, "documentacion_no_vigente",
    )  # fmt: skip
    # Control: la misma, del mismo tipo y del mismo proveedor, sí.
    assert subir(e, reemplaza=seguro).status_code == 201


# Quién puede -----------------------------------------------------------------------------------


def test_quien_no_prepara_no_carga(motor_migrate, motor_app, e, bucket):
    assert codigo(subir(e, quien=e.mc1)) == (403, "sin_permiso")
    assert constancias(motor_migrate, e.proveedor) == []
    assert subir(e).status_code == 201  # control: el administrador sí


def test_quien_revisa_ve_la_marca_pero_no_la_ficha(motor_migrate, motor_app, e, bucket):
    cargada(motor_app, e, hoy_de(motor_app, e.cons) - timedelta(days=1))
    assert codigo(get(e, e.mc1, f"proveedores/{e.proveedor}")) == (403, "sin_permiso")
    assert get(e, e.adm, f"proveedores/{e.proveedor}").status_code == 200  # control


def test_un_proveedor_de_otro_consorcio_da_404(motor_migrate, motor_app, e, bucket):
    otra = escena_economica(motor_migrate, motor_app)
    assert codigo(subir(e, proveedor=otra.proveedor)) == (404, "no_encontrado")
    assert codigo(subir(e, proveedor=uuid4())) == (404, "no_encontrado")
    assert subir(e).status_code == 201  # control


def test_una_descripcion_con_un_telefono_se_niega(motor_migrate, e, bucket):
    r = subir(e, descripcion="Llamar al 11 4444 5555")
    assert r.status_code == 422, r.text
    assert subir(e, descripcion="Responsabilidad civil").status_code == 201  # control


def test_reintentar_con_la_misma_clave_no_carga_dos_veces(motor_migrate, e, bucket):
    clave = clave_idempotencia()
    a = subir(e, clave=clave)
    b = subir(e, clave=clave)
    assert (a.status_code, b.status_code) == (201, 201)
    assert a.json() == b.json()
    assert len(constancias(motor_migrate, e.proveedor)) == 1


def test_el_documento_citado_ya_no_se_retira(motor_migrate, motor_app, e, bucket):
    cuerpo = cargada(motor_app, e, date(2099, 1, 1))
    assert codigo(retirar(e, cuerpo["documento_id"])) == (409, "documento_referenciado")


# La base ---------------------------------------------------------------------------------------

CARGAR = (
    "select nucleo.cargar_documentacion_proveedor(:proveedor, 'seguro', 'ART SINTETICA',"
    " current_date, null, 'Poliza SINTETICA', 'application/pdf', 1, repeat('a', 64), :actor,"
    " :origen, :sesion)"
)


def test_la_base_exige_la_facultad_de_preparar(motor_migrate, e):
    niega(motor_migrate, e, e.mc1, CARGAR, "42501")
    assert constancias(motor_migrate, e.proveedor) == []
    llamar(motor_migrate, e, e.adm, CARGAR)  # control
    assert len(constancias(motor_migrate, e.proveedor)) == 1


def test_cem_app_no_escribe_ni_cambia_la_constancia(motor_migrate, motor_app, e):
    with pytest.raises(DBAPIError) as error, motor_app.begin() as c:
        c.execute(text("insert into nucleo.documentacion_proveedor (consorcio_id, proveedor_id,"
                       " tipo, descripcion, vence_el, documento_id, version_documento_id,"
                       " cargada_por, identidad_carga) values (:c, :c, 'seguro', 'x',"
                       " current_date, :c, :c, :c, :c)"), {"c": e.cons})  # fmt: skip
    assert error.value.orig.diag.sqlstate == "42501"
    llamar(motor_migrate, e, e.adm, CARGAR)
    # Ni el dueño de las tablas la cambia ni la borra: es historia.
    for sentencia in ("update nucleo.documentacion_proveedor set vence_el = vence_el + 1",
                      "delete from nucleo.documentacion_proveedor"):  # fmt: skip
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            c.execute(text(sentencia))
        assert error.value.orig.diag.message_hint == "historia_inmutable", sentencia
    assert len(constancias(motor_migrate, e.proveedor)) == 1


def test_las_funciones_las_ejecuta_solo_cem_app(motor_migrate):
    with motor_migrate.connect() as c:
        filas = c.execute(text(
            "select p.proname, p.prosecdef, has_function_privilege('cem_app', p.oid, 'EXECUTE'),"
            " has_function_privilege('public', p.oid, 'EXECUTE'), array_to_string(p.proconfig, ',')"
            " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
            " where n.nspname = 'nucleo' and p.proname in ('cargar_documentacion_proveedor',"
            " 'documentacion_proveedor_lectura', 'estado_documentacion_proveedor',"
            " 'reemplazo_documentacion_en_revision') order by p.proname"
        )).all()  # fmt: skip
    camino = "search_path=pg_catalog, pg_temp"
    assert [tuple(f) for f in filas] == [
        ("cargar_documentacion_proveedor", True, True, False, camino),
        ("documentacion_proveedor_lectura", True, True, False, camino),
        ("estado_documentacion_proveedor", False, False, False, camino),
        ("reemplazo_documentacion_en_revision", False, False, False, camino),
    ]


def _definiciones(motor) -> dict[str, tuple]:
    funciones = ("nucleo.documento_referenciado(uuid, uuid)",
                 "nucleo.exportar_proveedores(uuid, uuid)")  # fmt: skip
    with motor.connect() as c:
        return {
            f: tuple(c.execute(
                text("select pg_get_functiondef(p.oid), coalesce(p.proacl::text, ''), p.prosecdef"
                     " from pg_proc p where p.oid = to_regprocedure(:f)"), {"f": f},
            ).one())
            for f in funciones
        }  # fmt: skip


def test_0103_ida_y_vuelta_deja_todo_como_estaba(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0102")
    antes = _definiciones(motor)
    command.upgrade(cfg, "0103")
    con = _definiciones(motor)
    assert all(con[f] != antes[f] for f in antes)  # control: la 0103 cambió las dos
    with motor.connect() as c:
        assert c.execute(text("select to_regclass('nucleo.documentacion_proveedor')")).scalar()
    command.downgrade(cfg, "0102")
    assert _definiciones(motor) == antes
    with motor.connect() as c:
        assert c.execute(text("select to_regclass('nucleo.documentacion_proveedor')")).scalar() \
            is None  # fmt: skip
    command.upgrade(cfg, "0103")
    assert _definiciones(motor) == con
