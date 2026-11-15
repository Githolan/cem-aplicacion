"""RF50 configurable (`0104`; respuestas del propietario del 15-11-2026 a P1-P5): quién consulta
los proveedores, los días de «por vencer», lo que exige la ley por rubro y qué hace cuando falta,
y «No vence». Todo por consorcio, con valores por defecto, cambiado por el equipo.

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. Cada negación lleva su
control. Datos SINTETICOS / PRUEBA / NO REAL.
"""

import json
from datetime import timedelta
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from scripts import configurar_proveedores
from tests.conftest import RAIZ, clave_idempotencia, url_rol
from tests.test_documentacion_proveedor_0103 import cargada, constancias, ficha, lista
from tests.test_documentos_2c1 import (  # noqa: F401  (`bucket` y `firmas_de_la_imagen` son fixtures)
    analizar,
    bucket,
    firmas_de_la_imagen,
    pdf,
)
from tests.test_modo_operacion import hoy_de
from tests.test_orden_pago_neutra import niega, nueva_sesion
from tests.test_rutas_egresos import ORDEN, cliente, codigo, get, post, preparar
from tests.test_rutas_egresos import e as e  # noqa: F401  (es un fixture)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

ASCENSORES = ("ascensores.permiso_conservador", "ascensores.representante_tecnico",
              "ascensores.seguro_rc")  # fmt: skip
TIPO_DE = {
    "ascensores.permiso_conservador": "habilitacion",
    "ascensores.representante_tecnico": "habilitacion",
    "ascensores.seguro_rc": "seguro",
}


def configurar(motor_app, e, **valores) -> None:
    v = {"dias": None, "modo": None, "no_vence": None, "roles": None, "agregados": None,
         "omitidos": None} | valores  # fmt: skip
    with transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c:
        c.execute(text("select nucleo.configurar_proveedores(:dias, :modo, :no_vence, :roles,"
                       " :agregados, :omitidos, null, 'soporte')"), v)  # fmt: skip


def rubro(e, nombre: str, *, asignar=True, quien: UUID | None = None, proveedor=None):
    resto = f"proveedores/{proveedor or e.proveedor}/rubros-legales/{nombre}"
    quien = quien or e.adm
    with cliente(quien, e.sesion[quien]) as c:
        if asignar:
            return c.post(f"/api/v1/consorcios/{e.cons}/{resto}", headers=clave_idempotencia())
        return c.delete(f"/api/v1/consorcios/{e.cons}/{resto}", headers=clave_idempotencia())


def subir_con(e, **params):
    """Una subida con parámetros a mano (la de 0103 siempre manda la fecha)."""
    base = {"tipo": "seguro", "descripcion": "Seguro SINTETICO", "titulo": "Poliza SINTETICA"}
    archivo = params.pop("archivo", None) or pdf()
    with cliente(e.adm, e.sesion[e.adm]) as c:
        return c.post(
            f"/api/v1/consorcios/{e.cons}/proveedores/{e.proveedor}/documentacion",
            params=base | params,
            content=archivo,
            headers={"Content-Type": "application/pdf"} | clave_idempotencia(),
        )


# Valores por defecto ---------------------------------------------------------------------------


def test_los_valores_por_defecto(motor_migrate, motor_app, e, bucket):
    f = ficha(e)
    assert f["configuracion"] == {"dias_por_vencer": 30, "modo_exigencia": "bloquear",
                                  "admite_no_vence": True}  # fmt: skip
    exigido = {r["codigo"]: r["exigido"] for r in f["requisitos"]}
    assert exigido["general.seguro_rc"] is True
    # Las «SIN VERIFICAR» no se exigen; control: están en la matriz.
    assert exigido["electricidad.matricula"] is False
    assert all(r["fuente"].strip() for r in f["requisitos"])
    assert (f["rubros_legales"], f["faltantes"]) == ([], [])
    assert lista(e)["modo_exigencia"] == "bloquear"


def test_los_valores_por_defecto_son_los_del_contrato():
    from cem_contracts import egresos

    from tests.test_quitar_retirar_0089 import _migracion

    m = _migracion(
        RAIZ / "migrations" / "versions" / "0104_documentacion_proveedor_configurable.py"
    )
    assert (m.DIAS_POR_VENCER, m.MODO_EXIGENCIA, m.ADMITE_NO_VENCE, m.ROLES_QUE_VEN) == (
        egresos.DIAS_POR_VENCER, egresos.MODO_EXIGENCIA.value, egresos.ADMITE_NO_VENCE,
        egresos.ROLES_QUE_VEN_PROVEEDORES,
    )  # fmt: skip


# Quién consulta (P1, P2) ----------------------------------------------------------------------


def test_el_consejo_consulta_sin_la_cuenta_y_el_propietario_no(motor_migrate, motor_app, e,
                                                               bucket):  # fmt: skip
    r = get(e, e.mc1, "consulta-proveedores")
    assert r.status_code == 200, r.text
    elementos = r.json()["elementos"]
    assert {p["id"] for p in elementos} == {str(e.proveedor), str(e.otro)}
    assert not any("cuenta" in clave or "huella" in clave for p in elementos for clave in p)
    assert get(e, e.mc1, f"consulta-proveedores/{e.proveedor}").status_code == 200
    # El propietario no está en la configuración por defecto.
    e.sesion[e.prop] = nueva_sesion(motor_migrate, e.prop)
    assert codigo(get(e, e.prop, "consulta-proveedores")) == (403, "sin_permiso")
    # La base dice lo mismo, sin la API.
    niega(motor_migrate, e, e.prop,
          "select count(*) from nucleo.proveedores_consulta(null, :actor, :origen, :sesion)",
          "42501")  # fmt: skip
    # Control: el equipo agrega a los propietarios («habitantes autorizados» por tipo).
    configurar(motor_app, e, roles=["miembro_consejo", "administrador", "propietario"])
    assert get(e, e.prop, "consulta-proveedores").status_code == 200


def test_sin_el_consejo_en_la_configuracion_la_api_y_la_base_niegan(motor_migrate, motor_app, e):
    configurar(motor_app, e, roles=["administrador"])
    assert codigo(get(e, e.mc1, "consulta-proveedores")) == (403, "sin_permiso")
    niega(motor_migrate, e, e.mc1,
          "select count(*) from nucleo.proveedores_consulta(null, :actor, :origen, :sesion)",
          "42501")  # fmt: skip
    assert get(e, e.adm, "consulta-proveedores").status_code == 200  # control


def test_la_consulta_muestra_seguros_y_faltantes(motor_migrate, motor_app, e, bucket):
    cargada(motor_app, e, hoy_de(motor_app, e.cons) - timedelta(days=1))
    assert rubro(e, "general").status_code == 200
    f = get(e, e.mc1, f"consulta-proveedores/{e.proveedor}").json()
    assert [d["estado"] for d in f["documentacion"]] == ["vencida"]
    assert f["rubros_legales"] == ["general"]
    assert [x["codigo"] for x in f["faltantes"]] == ["general.seguro_rc"]
    lista_consulta = get(e, e.mc1, "consulta-proveedores").json()
    assert lista_consulta["documentacion_vencida"] == [str(e.proveedor)]
    assert lista_consulta["documentacion_faltante"] == [str(e.proveedor)]


# Días (P3) y «No vence» (P5) ------------------------------------------------------------------


def test_los_dias_por_vencer_se_configuran(motor_migrate, motor_app, e, bucket):
    hoy = hoy_de(motor_app, e.cons)
    a = cargada(motor_app, e, hoy + timedelta(days=20))["constancia_id"]
    assert {d["id"]: d["estado"] for d in ficha(e)["documentacion"]} == {a: "por_vencer"}
    configurar(motor_app, e, dias=10)
    assert {d["id"]: d["estado"] for d in ficha(e)["documentacion"]} == {a: "vigente"}
    assert ficha(e)["configuracion"]["dias_por_vencer"] == 10
    # Control: a 10 días sí está por vencer.
    b = cargada(motor_app, e, hoy + timedelta(days=10), descripcion="Otro SINTETICO",
                archivo=pdf(flujo=b"0.3 g 0 0 9 9 re f"))["constancia_id"]  # fmt: skip
    assert {d["id"]: d["estado"] for d in ficha(e)["documentacion"]}[b] == "por_vencer"


def test_no_vence_con_la_fecha_en_blanco_vale_siempre(motor_migrate, motor_app, e, bucket):
    r = subir_con(e, tipo="habilitacion", descripcion="Matricula SINTETICA", no_vence="true")
    assert r.status_code == 201, r.text
    assert analizar(motor_app) == 1
    [d] = ficha(e)["documentacion"]
    assert (d["estado"], d["vence_el"], d["no_vence"]) == ("vigente", None, True)
    [k] = constancias(motor_migrate, e.proveedor)
    assert (k["vence_el"], k["no_vence"]) == (None, True)


def test_la_fecha_o_no_vence_nunca_las_dos_ni_ninguna(motor_migrate, motor_app, e, bucket):
    hoy = hoy_de(motor_app, e.cons).isoformat()
    assert codigo(subir_con(e, no_vence="true", vence_el=hoy)) == (422, "vencimiento_invalido")
    assert codigo(subir_con(e)) == (422, "vencimiento_invalido")
    assert constancias(motor_migrate, e.proveedor) == []
    assert subir_con(e, vence_el=hoy).status_code == 201  # control
    # La base lo exige aunque alguien llegue a la tabla.
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.documentacion_proveedor disable trigger user"))
        c.execute(text("update nucleo.documentacion_proveedor set no_vence = true"))
    assert error.value.orig.diag.constraint_name == "vencimiento_definido"


def test_si_el_consorcio_no_admite_no_vence_se_niega(motor_migrate, motor_app, e, bucket):
    configurar(motor_app, e, no_vence=False)
    assert codigo(subir_con(e, no_vence="true")) == (422, "no_vence_no_admitido")
    assert ficha(e)["configuracion"]["admite_no_vence"] is False
    hoy = hoy_de(motor_app, e.cons).isoformat()
    assert subir_con(e, vence_el=hoy).status_code == 201  # control


# Lo que exige la ley (P4) ----------------------------------------------------------------------


def test_el_rubro_legal_pide_sus_requisitos_y_bloquea_una_orden_nueva(motor_migrate, motor_app, e,
                                                                       bucket):  # fmt: skip
    vieja = preparar(e)  # antes del rubro
    r = rubro(e, "ascensores")
    assert r.status_code == 200, r.text
    assert sorted(x["codigo"] for x in r.json()["faltantes"]) == list(ASCENSORES)
    assert lista(e)["documentacion_faltante"] == [str(e.proveedor)]
    # Bloquea preparar una orden nueva a ese proveedor...
    r = post(e, e.adm, "ordenes-pago", ORDEN | {"proveedor_id": str(e.proveedor)})
    assert codigo(r) == (422, "documentacion_legal_faltante")
    # ...y cambiar una orden a ese proveedor.
    otra = preparar(e, proveedor_id=str(e.otro))  # control: otro proveedor sin rubro, sí
    r = post(e, e.adm, f"ordenes-pago/{otra}/versiones",
             ORDEN | {"proveedor_id": str(e.proveedor), "version_esperada": 1})  # fmt: skip
    assert codigo(r) == (422, "documentacion_legal_faltante")
    # Lo que ya existe sigue: la orden vieja se modifica con el mismo proveedor y se envía.
    r = post(e, e.adm, f"ordenes-pago/{vieja}/versiones",
             ORDEN | {"proveedor_id": str(e.proveedor), "version_esperada": 1,
                      "concepto": "SINTETICO: otro concepto"})  # fmt: skip
    assert r.status_code == 200, r.text
    r = post(e, e.adm, f"ordenes-pago/{vieja}/enviar-a-revision", {"version_esperada": 2})
    assert r.status_code == 200, r.text

    # Con lo que pide la ley cargado, la orden nueva se prepara.
    for i, requisito in enumerate(ASCENSORES):
        cargada(motor_app, e, hoy_de(motor_app, e.cons) + timedelta(days=200),
                tipo=TIPO_DE[requisito], descripcion=f"Requisito SINTETICO {i}",
                archivo=pdf(flujo=f"0.{i + 1} g 0 0 9 9 re f".encode()),
                requisito=requisito)  # fmt: skip
    assert ficha(e)["faltantes"] == []
    assert lista(e)["documentacion_faltante"] == []
    preparar(e)


def test_vencido_vuelve_a_faltar_y_en_modo_avisar_no_bloquea(motor_migrate, motor_app, e, bucket):
    assert rubro(e, "general").status_code == 200
    cargada(motor_app, e, hoy_de(motor_app, e.cons) - timedelta(days=1),
            requisito="general.seguro_rc")  # fmt: skip
    assert [x["codigo"] for x in ficha(e)["faltantes"]] == ["general.seguro_rc"]
    r = post(e, e.adm, "ordenes-pago", ORDEN | {"proveedor_id": str(e.proveedor)})
    assert codigo(r) == (422, "documentacion_legal_faltante")
    configurar(motor_app, e, modo="avisar")
    assert lista(e)["modo_exigencia"] == "avisar"
    assert lista(e)["documentacion_faltante"] == [str(e.proveedor)]  # marca igual
    preparar(e)


def test_omitir_un_requisito_o_agregar_uno_sin_verificar(motor_migrate, motor_app, e, bucket):
    assert rubro(e, "general").status_code == 200
    configurar(motor_app, e, omitidos=["general.seguro_rc"])
    assert ficha(e)["faltantes"] == []
    preparar(e)  # nada que falte
    assert rubro(e, "electricidad").status_code == 200
    assert ficha(e)["faltantes"] == []  # la matrícula no está verificada
    configurar(motor_app, e, agregados=["electricidad.matricula"])
    assert [x["codigo"] for x in ficha(e)["faltantes"]] == ["electricidad.matricula"]


def test_los_rubros_se_asignan_y_retiran_con_sus_controles(motor_migrate, motor_app, e, bucket):
    assert codigo(rubro(e, "inventado")) == (422, "rubro_legal_invalido")
    assert codigo(rubro(e, "gas", quien=e.mc1)) == (403, "sin_permiso")
    assert codigo(rubro(e, "gas", asignar=False)) == (409, "rubro_legal_sin_cambios")
    assert rubro(e, "gas").status_code == 200
    assert codigo(rubro(e, "gas")) == (409, "rubro_legal_sin_cambios")
    assert ficha(e)["rubros_legales"] == ["gas"]
    r = rubro(e, "gas", asignar=False)
    assert r.status_code == 200, r.text
    assert (r.json()["rubros_legales"], r.json()["faltantes"]) == ([], [])
    with motor_migrate.connect() as c:
        filas = c.execute(text("select accion, registrado_por from nucleo.rubro_legal_proveedor"
                               " where proveedor_id = :p order by numero"),
                          {"p": e.proveedor}).all()  # fmt: skip
        eventos = c.execute(text("select count(*) from auditoria.eventos where accion ="
                                 " 'consorcio.proveedor.rubro_legal' and entidad_id = :p"),
                            {"p": e.proveedor}).scalar_one()  # fmt: skip
    assert [tuple(f) for f in filas] == [("asignar", e.adm), ("retirar", e.adm)]
    assert eventos == 2


def test_un_requisito_de_otro_tipo_se_niega(motor_migrate, motor_app, e, bucket):
    hoy = hoy_de(motor_app, e.cons).isoformat()
    r = subir_con(e, tipo="habilitacion", vence_el=hoy, requisito="general.seguro_rc")
    assert codigo(r) == (422, "requisito_invalido")
    assert codigo(subir_con(e, vence_el=hoy, requisito="no.existe")) == (422, "requisito_invalido")
    assert subir_con(e, vence_el=hoy, requisito="general.seguro_rc").status_code == 201


# La configuración del equipo -------------------------------------------------------------------


def test_solo_el_equipo_configura_y_cada_cambio_queda(motor_migrate, motor_app, e):
    sql = ("select nucleo.configurar_proveedores(10, null, null, null, null, null, :actor,"
           " :origen)")  # fmt: skip
    for persona, actor, origen in ((e.adm, e.adm, "api"), (None, None, "api"),
                                   (e.adm, None, "soporte")):  # fmt: skip
        with pytest.raises(DBAPIError) as error:
            with transaccion(motor_app, persona_id=persona, consorcio_id=e.cons) as c:
                c.execute(text(sql), {"actor": actor, "origen": origen})
        assert error.value.orig.diag.message_hint == "origen_invalido"
    for valores, motivo in (({"roles": ["vecino"]}, "rol_invalido"),
                            ({"dias": 0}, "dias_invalidos"),
                            ({"agregados": ["no.existe"]}, "requisito_invalido")):  # fmt: skip
        with pytest.raises(DBAPIError) as error:
            configurar(motor_app, e, **valores)
        assert error.value.orig.diag.message_hint == motivo
    configurar(motor_app, e, dias=10)  # control
    with pytest.raises(DBAPIError) as error:
        configurar(motor_app, e, dias=10)
    assert error.value.orig.diag.message_hint == "configuracion_sin_cambios"
    configurar(motor_app, e, modo="avisar")  # lo que no se pasa queda
    with motor_migrate.connect() as c:
        filas = c.execute(text("select dias_por_vencer, modo_exigencia from"
                               " nucleo.configuracion_proveedores where consorcio_id = :c"
                               " order by numero"), {"c": e.cons}).all()  # fmt: skip
    assert [tuple(f) for f in filas] == [(10, "bloquear"), (10, "avisar")]
    for sentencia in ("update nucleo.configuracion_proveedores set dias_por_vencer = 5",
                      "delete from nucleo.configuracion_proveedores"):  # fmt: skip
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            c.execute(text(sentencia))
        assert error.value.orig.diag.message_hint == "historia_inmutable"


@pytest.fixture
def correr(monkeypatch, capsys):
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))

    def _correr(*argumentos: str) -> tuple[int, str, str]:
        resultado = configurar_proveedores.main(list(argumentos))
        salida = capsys.readouterr()
        return resultado, salida.out, salida.err

    return _correr


def test_el_script_del_equipo(motor_migrate, motor_app, e, correr):
    resultado, salida, errores = correr("--consorcio", str(e.cons))
    assert (resultado, errores) == (0, "")
    assert json.loads(salida)["registrada_en"] is None  # los valores por defecto
    resultado, salida, errores = correr(
        "--consorcio",
        str(e.cons),
        "--modo-exigencia",
        "avisar",
        "--roles-que-ven",
        "miembro_consejo,propietario",
    )
    assert (resultado, errores) == (0, "")
    impreso = json.loads(salida)
    assert (impreso["modo_exigencia"], impreso["roles_que_ven"], impreso["dias_por_vencer"]) == (
        "avisar", ["miembro_consejo", "propietario"], 30)  # fmt: skip
    resultado, salida, errores = correr("--consorcio", str(e.cons), "--roles-que-ven", "vecino")
    assert (resultado, salida) == (1, "")
    assert "rol_invalido" in errores


# La base: privilegios y migración --------------------------------------------------------------


def test_cem_app_no_toca_las_tablas(motor_migrate):
    with motor_migrate.connect() as c:
        privilegios = {
            t: [c.execute(text("select has_table_privilege('cem_app', :t, :p)"),
                          {"t": t, "p": p}).scalar_one()
                for p in ("SELECT", "INSERT", "UPDATE", "DELETE")]
            for t in ("nucleo.configuracion_proveedores", "nucleo.rubro_legal_proveedor",
                      "nucleo.requisito_legal")
        }  # fmt: skip
    assert all(v == [False] * 4 for v in privilegios.values()), privilegios


def test_las_internas_no_las_ejecuta_cem_app(motor_migrate):
    internas = ("configuracion_proveedores_vigente", "requisitos_exigidos", "rubros_legales_de",
                "requisitos_faltantes", "exigir_documentacion_legal", "puede_ver_proveedores",
                "exigir_lectura_proveedores")  # fmt: skip
    rutas = ("roles_configurados", "rubros_legales_lectura", "requisitos_faltantes_lectura",
             "requisitos_legales_lectura")  # fmt: skip
    with motor_migrate.connect() as c:
        filas = dict(c.execute(text(
            "select p.proname, has_function_privilege('cem_app', p.oid, 'EXECUTE')"
            " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
            " where n.nspname = 'nucleo' and p.proname = any(:f)"),
            {"f": list(internas + rutas)}).all())  # fmt: skip
    assert {f: filas[f] for f in internas} == dict.fromkeys(internas, False)
    assert {f: filas[f] for f in rutas} == dict.fromkeys(rutas, True)  # control


def _definiciones(motor) -> dict[str, tuple]:
    funciones = (
        "nucleo.estado_documentacion_proveedor(uuid, uuid)",
        "nucleo.exportar_proveedores(uuid, uuid)",
        "nucleo.documentacion_proveedor_lectura(uuid, text, uuid, text, uuid)",
    )
    with motor.connect() as c:
        return {
            f: tuple(c.execute(
                text("select pg_get_functiondef(p.oid), coalesce(p.proacl::text, ''), p.prosecdef"
                     " from pg_proc p where p.oid = to_regprocedure(:f)"), {"f": f},
            ).one())
            for f in funciones
        }  # fmt: skip


def test_0104_ida_y_vuelta_deja_todo_como_estaba(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0103")
    antes = _definiciones(motor)
    command.upgrade(cfg, "0104")
    con = _definiciones(motor)
    assert all(con[f] != antes[f] for f in antes)  # control: la 0104 cambió las tres
    with motor.connect() as c:
        assert c.execute(text("select count(*) from nucleo.requisito_legal")).scalar() > 0
    command.downgrade(cfg, "0103")
    assert _definiciones(motor) == antes
    with motor.connect() as c:
        assert c.execute(text("select to_regclass('nucleo.requisito_legal')")).scalar() is None
        firma = c.execute(text(
            "select to_regprocedure('nucleo.cargar_documentacion_proveedor(uuid, text, text, date,"
            " uuid, text, text, integer, text, uuid, text, uuid)')")).scalar()  # fmt: skip
        assert firma is not None
    command.upgrade(cfg, "0104")
    assert _definiciones(motor) == con
