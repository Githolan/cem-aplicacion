"""Las dos órdenes de pago manual que quedaban trabadas (`0067`, P3 179 y 180; opción A del
propietario, 03-11-2026).

La escena es la de `test_pago_manual_0055`: la administración prepara y ejecuta, mc1 aprueba y mc2
concilia. Datos SINTETICOS / NO REAL.
"""

import importlib.util
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.test_cobertura_comprobantes import mandato_de
from tests.test_egresos_1a import _hoy, envejecer
from tests.test_mandatos import otra_persona
from tests.test_orden_pago_neutra import llamar, niega, nueva_sesion, orden
from tests.test_pago_manual_0055 import (
    CONCILIAR,
    CONFIRMAR,
    CUENTA,
    DECLARAR,
    REGISTRAR,
    emitida,
    evidencia,
    otro_ejecutor,
    pagos_de,
    registrada,
    segunda_apertura,
    ver,
)
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_rutas_egresos import cliente, codigo, get, post
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db

MOTIVO = "SINTETICO: no llegué a transferir"
POR_ACLARAR = "select * from nucleo.pagos_por_aclarar(:actor, :origen, :sesion)"
VENCIDA = timedelta(days=8)
"""Una aprobación de hace 8 días: venció el día 7 (`0050`)."""


def dar_de_baja(motor_migrate, motor_app, e, persona) -> None:
    """La administración renuncia: deja de tener `egresos.ejecutar_pago`. La baja revoca sus
    sesiones (RN22): vuelve a entrar, con el segundo factor."""
    with transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c:
        c.execute(
            text("select nucleo.baja_mandato(:m, 'renuncia', null, 'Nota SINTETICA',"
                 " 'Prueba de P3 179', null, 'soporte')"),
            {"m": mandato_de(motor_migrate, e, persona)},
        )  # fmt: skip
    e.sesion[persona] = nueva_sesion(motor_migrate, persona)


def por_aclarar(motor, e, actor, sesion=None) -> list[dict]:
    with transaccion(motor, persona_id=actor) as c:
        return [dict(f) for f in c.execute(text(POR_ACLARAR), {
            "actor": actor, "origen": "api", "sesion": sesion or e.sesion[actor],
        }).mappings()]  # fmt: skip


def declaraciones(motor_migrate, o) -> list[tuple]:
    with motor_migrate.connect() as c:
        return [tuple(f) for f in c.execute(
            text("select d.declarado_por, d.sin_facultad"
                 " from nucleo.declaracion_pago_no_realizado d"
                 " join nucleo.ejecucion_orden_pago x on x.id = d.ejecucion_id"
                 " where x.orden_id = :o order by d.paso"), {"o": o},
        )]  # fmt: skip


def trabada(motor_migrate, motor_app, e):
    """El caso A del P3 172: la administración vio la cuenta, renunció antes de registrar y la
    aprobación venció. Antes de `0067`, nadie podía declarar ni registrar."""
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    dar_de_baja(motor_migrate, motor_app, e, e.adm)
    envejecer(motor_migrate, o, VENCIDA)
    return o


# A: declara quien vio la cuenta, aunque ya no ejecute -----------------------------------------


def test_quien_vio_la_cuenta_y_renuncio_declara_y_otro_consejero_confirma(
    motor_migrate, motor_app, e
):
    o = trabada(motor_migrate, motor_app, e)
    d = llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1, motivo=MOTIVO)
    assert declaraciones(motor_migrate, o) == [(e.adm, True)]
    llamar(motor_migrate, e, e.mc1, CONFIRMAR, orden=o, declaracion=d)
    assert orden(motor_migrate, o)["estado"] == "fallida"


def test_con_la_facultad_la_declaracion_no_queda_marcada(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1, motivo=MOTIVO)
    assert declaraciones(motor_migrate, o) == [(e.adm, False)]


def test_sin_la_facultad_declara_solo_quien_vio_la_cuenta_de_esa_orden(motor_migrate, motor_app, e):
    """mc2 (del Consejo, sin `ejecutar_pago`) y una persona cualquiera del consorcio no vieron la
    cuenta: la base les niega lo mismo que antes, sin decir si la orden existe."""
    o = trabada(motor_migrate, motor_app, e)
    x = otra_persona(motor_migrate, e.cons)
    e.sesion[x] = nueva_sesion(motor_migrate, x)
    for quien in (e.mc2, x):
        niega(motor_migrate, e, quien, DECLARAR, "42501", orden=o, version=1, motivo=MOTIVO)
    llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1, motivo=MOTIVO)  # control


def test_haber_visto_la_cuenta_de_otra_orden_no_alcanza(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    otra = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, otra)
    dar_de_baja(motor_migrate, motor_app, e, e.adm)
    niega(motor_migrate, e, e.adm, DECLARAR, "42501", orden=o, version=1, motivo=MOTIVO)
    llamar(motor_migrate, e, e.adm, DECLARAR, orden=otra, version=1, motivo=MOTIVO)  # control


@pytest.mark.parametrize(
    ("horas_atras", "totp_min_atras", "motivo"),
    [
        (1, 11, "requiere_segundo_factor_reciente"),
        (1, None, "requiere_segundo_factor_reciente"),
        # Sin sesión de 12 horas desde 0091: una sesión larga también pide el gesto.
        (20 * 24, 11, "requiere_segundo_factor_reciente"),
    ],
)
def test_sin_la_facultad_la_declaracion_exige_r10(
    motor_migrate, motor_app, e, horas_atras, totp_min_atras, motivo
):
    o = trabada(motor_migrate, motor_app, e)
    vieja = nueva_sesion(motor_migrate, e.adm, horas_atras=horas_atras,
                         totp_min_atras=totp_min_atras)  # fmt: skip
    niega(motor_migrate, e, e.adm, DECLARAR, motivo, sesion=vieja, orden=o, version=1,
          motivo=MOTIVO)  # fmt: skip
    niega(motor_migrate, e, e.adm, DECLARAR, "sesion_invalida", sesion=e.sesion[e.mc1], orden=o,
          version=1, motivo=MOTIVO)  # fmt: skip
    llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1, motivo=MOTIVO)  # control


def test_sin_la_facultad_la_declaracion_exige_la_identidad_verificada(motor_migrate, motor_app, e):
    """Sin la identidad, la base no sabe que vio la cuenta: niega sin decir nada más (42501).
    Soporte vuelve a vincularla (reparación técnica) y recién ahí declara."""
    o = trabada(motor_migrate, motor_app, e)
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.identidad_persona set desvinculada_en = now()"
                 " where consorcio_id = :c and persona_id = :p and desvinculada_en is null"),
            {"c": e.cons, "p": e.adm},
        )  # fmt: skip
    niega(motor_migrate, e, e.adm, DECLARAR, "42501", orden=o, version=1, motivo=MOTIVO)


def test_quien_vio_la_cuenta_y_aprobo_tampoco_declara_sin_la_facultad(motor_migrate, motor_app, e):
    """Quien aprobó no declara, con o sin la facultad (`0055`). La apertura de mc1 la escribe la
    prueba (como aprobador, la base no le da la cuenta); después mc1 deja el Consejo."""
    o = emitida(motor_migrate, e)
    with motor_migrate.begin() as c:
        c.execute(
            text("insert into nucleo.apertura_cuenta_pago_manual (consorcio_id, ejecucion_id,"
                 " abierta_por, identidad_abrio) select x.consorcio_id, x.id, :p,"
                 " nucleo.identidad_humana_de(x.consorcio_id, :p)"
                 " from nucleo.ejecucion_orden_pago x where x.orden_id = :o"),
            {"p": e.mc1, "o": o},
        )  # fmt: skip
    dar_de_baja(motor_migrate, motor_app, e, e.mc1)
    niega(motor_migrate, e, e.mc1, DECLARAR, "no_es_quien_aprobo", orden=o, version=1,
          motivo=MOTIVO)  # fmt: skip


def test_por_aclarar_lista_a_quien_vio_la_cuenta_y_ya_no_ejecuta(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    assert por_aclarar(motor_migrate, e, e.adm) == []  # con la facultad, la ve en su pantalla
    dar_de_baja(motor_migrate, motor_app, e, e.adm)
    [fila] = por_aclarar(motor_migrate, e, e.adm)
    assert (fila["consorcio_id"], fila["orden_id"], fila["version"]) == (e.cons, o, 1)
    assert "cuenta" not in fila and all(CUENTA not in str(v) for v in fila.values())
    assert por_aclarar(motor_migrate, e, e.mc2) == []  # control: no vio la cuenta
    llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1, motivo=MOTIVO)
    assert por_aclarar(motor_migrate, e, e.adm) == []  # ya declaró


def test_por_aclarar_exige_la_sesion_propia(motor_migrate, motor_app, e):
    o = trabada(motor_migrate, motor_app, e)
    with pytest.raises(DBAPIError) as error:
        por_aclarar(motor_migrate, e, e.adm, sesion=e.sesion[e.mc1])
    assert error.value.orig.diag.message_hint == "sesion_invalida"
    with pytest.raises(DBAPIError) as error, transaccion(motor_migrate, persona_id=e.mc1) as c:
        c.execute(text(POR_ACLARAR), {"actor": e.adm, "origen": "api", "sesion": e.sesion[e.adm]})
    assert error.value.orig.diag.sqlstate == "42501"
    assert [f["orden_id"] for f in por_aclarar(motor_migrate, e, e.adm)] == [o]  # control


# B: el pago fuera de término -------------------------------------------------------------------


def test_la_transferencia_despues_del_vencimiento_se_registra_y_se_concilia_con_motivo(
    motor_migrate, e
):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)  # la cuenta se ve mientras la aprobación rige
    envejecer(motor_migrate, o, VENCIDA)
    p = llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o))
    assert pagos_de(motor_migrate, o)[0]["fuera_de_termino"] is True
    datos = {"orden": o, "pago": p, "resultado": "conforme"}
    for motivo in (None, "  "):
        niega(motor_migrate, e, e.mc2, CONCILIAR, "conciliacion_invalida", **datos, motivo=motivo)
    llamar(motor_migrate, e, e.mc2, CONCILIAR, **datos, motivo="SINTETICO: el banco demoró")
    assert orden(motor_migrate, o)["estado"] == "pagada"


def test_en_termino_no_queda_marcado_y_conforme_sigue_sin_motivo(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    assert pagos_de(motor_migrate, o)[0]["fuera_de_termino"] is False
    niega(motor_migrate, e, e.mc2, CONCILIAR, "conciliacion_invalida", orden=o, pago=p,
          resultado="conforme", motivo="x")  # fmt: skip
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="conforme",
           motivo=None)  # fmt: skip


def test_fuera_de_termino_la_fecha_sigue_sin_ser_futura_ni_anterior_a_la_ejecucion(
    motor_migrate, e
):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    envejecer(motor_migrate, o, VENCIDA)
    hoy = _hoy(motor_migrate, e)
    for fecha in (hoy + timedelta(days=1), hoy - timedelta(days=1), None):
        niega(motor_migrate, e, e.adm, REGISTRAR, "fecha_operacion_invalida",
              **evidencia(motor_migrate, e, o, fecha=fecha))  # fmt: skip
    llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o))  # control


def test_fuera_de_termino_observado_sigue_pidiendo_el_motivo(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    envejecer(motor_migrate, o, VENCIDA)
    p = llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o))
    niega(motor_migrate, e, e.mc2, CONCILIAR, "conciliacion_invalida", orden=o, pago=p,
          resultado="observado", motivo=None)  # fmt: skip
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="observado",
           motivo="SINTETICO: no coincide")  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"


# La migración ----------------------------------------------------------------------------------


def migracion_0067():
    ruta = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    spec = importlib.util.spec_from_file_location("migracion_0067",
                                                  ruta / "0067_ordenes_trabadas.py")  # fmt: skip
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_0067_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    m = migracion_0067()
    funciones = ("nucleo.exigir_acto_economico(uuid, uuid, uuid, text, text)", m.REGISTRO,
                 m.CONCILIAR, m.DECLARAR, m.LECTURA)  # fmt: skip

    def estado() -> tuple:
        with motor.connect() as c:
            defs = tuple(
                c.execute(
                    text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": f}
                ).scalar_one()  # fmt: skip
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
                ).scalar_one()  # fmt: skip
                for f in funciones
            )
            nuevas = tuple(
                c.execute(text("select to_regprocedure(:f) is not null"), {"f": f}).scalar_one()
                for f in (m.SESION, m.POR_ACLARAR)
            )
            chequeo = c.execute(text(
                "select pg_get_constraintdef(oid) from pg_constraint"
                " where conname = 'conciliacion_pago_manual_check'"
            )).scalar_one()  # fmt: skip
            columnas = c.execute(text(
                "select count(*) from information_schema.columns where table_schema = 'nucleo'"
                " and column_name in ('fuera_de_termino', 'sin_facultad')"
            )).scalar_one()  # fmt: skip
        return defs, permisos, chequeo, nuevas, columnas

    command.upgrade(cfg, "0066")
    antes = estado()
    assert antes[3:] == ((False, False), 0)
    command.upgrade(cfg, "0067")
    despues = estado()
    assert despues[1] == antes[1]  # los mismos permisos
    assert despues[3:] == ((True, True), 2) and despues[2] != antes[2]
    assert all(a != d for a, d in zip(antes[0], despues[0], strict=True))
    command.downgrade(cfg, "0066")
    assert estado() == antes
    command.upgrade(cfg, "0067")  # control
    assert estado()[3:] == ((True, True), 2)


def test_0067_no_baja_con_un_pago_fuera_de_termino(motor_migrate, e):
    """La guarda, sobre la base de la escena: con un pago fuera de término, se niega."""
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    envejecer(motor_migrate, o, VENCIDA)
    llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o))
    m = migracion_0067()
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(text(m.GUARDA_DOWNGRADE))
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"


# Por HTTP --------------------------------------------------------------------------------------


def lista_por_aclarar(e, quien) -> list[dict]:
    with cliente(quien, e.sesion[quien]) as c:
        r = c.get("/api/v1/pagos-por-aclarar")
    assert r.status_code == 200, r.text
    return r.json()["elementos"]


def en_revision(e, o) -> dict:
    """La ejecución de la orden, como la ve el Consejo."""
    elementos = get(e, e.mc1, "pagos-manuales/revision").json()["elementos"]
    [elemento] = [x for x in elementos if x["orden"]["id"] == str(o)]
    return elemento["ejecucion"]


def test_por_http_quien_renuncio_ve_la_orden_y_declara_y_el_consejo_confirma(
    motor_migrate, motor_app, e
):
    o = trabada(motor_migrate, motor_app, e)
    [fila] = lista_por_aclarar(e, e.adm)
    assert (fila["orden_id"], fila["version"]) == (str(o), 1)
    assert get(e, e.adm, f"ordenes-pago/{o}").status_code == 403  # ya no la lee
    ruta = f"pagos-por-aclarar/{o}/no-realizado"
    r = post(e, e.adm, ruta, {"version": 1, "motivo": MOTIVO})
    assert r.status_code == 201, r.text
    assert lista_por_aclarar(e, e.adm) == []
    [d] = en_revision(e, o)["declaraciones"]
    assert d["confirmable"] is True
    r = post(e, e.mc1, f"ordenes-pago/{o}/pago-manual/no-realizado/{d['id']}/confirmacion", {})
    assert (r.status_code, r.json()["estado"]) == (200, "fallida"), r.text


def test_por_http_a_quien_no_vio_la_cuenta_le_responde_404(motor_migrate, motor_app, e):
    o = trabada(motor_migrate, motor_app, e)
    x = otra_persona(motor_migrate, e.cons)
    e.sesion[x] = nueva_sesion(motor_migrate, x)
    ruta = f"pagos-por-aclarar/{o}/no-realizado"
    for quien in (e.mc2, x):
        assert codigo(post(e, quien, ruta, {"version": 1, "motivo": MOTIVO})) == (
            404, "no_encontrado",
        )  # fmt: skip
        assert lista_por_aclarar(e, quien) == []
    otro = e.cons
    e.cons = uuid4()  # un consorcio que no existe
    assert codigo(post(e, e.adm, ruta, {"version": 1, "motivo": MOTIVO})) == (404, "no_encontrado")
    e.cons = otro
    r = post(e, e.adm, ruta, {"version": 1, "motivo": MOTIVO})  # control
    assert r.status_code == 201, r.text


def test_por_http_un_extrano_con_el_segundo_factor_viejo_recibe_404_igual(
    motor_migrate, motor_app, e
):
    """Sin identidad en el consorcio y sin el segundo factor reciente: 404, no 403. Antes de saber
    si vio la cuenta, la base no evalúa su R10 ni el modo (revisión independiente de #196)."""
    o = trabada(motor_migrate, motor_app, e)
    x = otra_persona(motor_migrate, e.cons)
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.identidad_persona set desvinculada_en = now()"
                 " where consorcio_id = :c and persona_id = :p and desvinculada_en is null"),
            {"c": e.cons, "p": x},
        )  # fmt: skip
    e.sesion[x] = nueva_sesion(motor_migrate, x, horas_atras=1, totp_min_atras=None)
    ruta = f"pagos-por-aclarar/{o}/no-realizado"
    assert codigo(post(e, x, ruta, {"version": 1, "motivo": MOTIVO})) == (404, "no_encontrado")
    # Una orden que no existe, para quien sí vio otra: lo mismo.
    otra = f"pagos-por-aclarar/{uuid4()}/no-realizado"
    assert codigo(post(e, e.adm, otra, {"version": 1, "motivo": MOTIVO})) == (404, "no_encontrado")
    assert post(e, e.adm, ruta, {"version": 1, "motivo": MOTIVO}).status_code == 201  # control


def test_por_aclarar_no_lista_lo_que_otro_ya_registro(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    x = otro_ejecutor(motor_migrate, motor_app, e)
    ver(motor_migrate, e, e.adm, o)
    dar_de_baja(motor_migrate, motor_app, e, e.adm)
    assert [f["orden_id"] for f in por_aclarar(motor_migrate, e, e.adm)] == [o]  # control
    segunda_apertura(motor_migrate, e, x, o)
    llamar(motor_migrate, e, x, REGISTRAR, **evidencia(motor_migrate, e, o))
    assert por_aclarar(motor_migrate, e, e.adm) == []


def test_por_http_sin_segundo_factor_reciente_pide_renovarlo(motor_migrate, motor_app, e):
    o = trabada(motor_migrate, motor_app, e)
    e.sesion[e.adm] = nueva_sesion(motor_migrate, e.adm, horas_atras=1, totp_min_atras=11)
    ruta = f"pagos-por-aclarar/{o}/no-realizado"
    assert codigo(post(e, e.adm, ruta, {"version": 1, "motivo": MOTIVO})) == (
        403, "segundo_factor_requerido",
    )  # fmt: skip


def test_por_http_fuera_de_termino_se_ve_y_conforme_pide_el_motivo(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    envejecer(motor_migrate, o, VENCIDA)
    p = llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o))
    [pago] = en_revision(e, o)["pagos"]
    assert pago["fuera_de_termino"] is True
    ruta = f"ordenes-pago/{o}/pago-manual/{p}/conciliacion"
    assert codigo(post(e, e.mc2, ruta, {"resultado": "conforme"})) == (422, "peticion_invalida")
    r = post(e, e.mc2, ruta, {"resultado": "conforme", "motivo": "SINTETICO: demora del banco"})
    assert (r.status_code, r.json()["estado"]) == (200, "pagada"), r.text


def test_por_http_en_termino_conforme_con_motivo_no_pasa(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    ruta = f"ordenes-pago/{o}/pago-manual/{p}/conciliacion"
    assert codigo(post(e, e.mc2, ruta, {"resultado": "conforme", "motivo": "x"})) == (
        422, "peticion_invalida",
    )  # fmt: skip
    r = post(e, e.mc2, ruta, {"resultado": "conforme"})  # control
    assert r.status_code == 200, r.text
