"""Importación de unidades y relaciones con informe (RF02; 2b-5, corte 3; migración 0025).

Contra PostgreSQL real. Diseño: `doc/diseno/H2b_5_Diseno.md` del repositorio CEM, §4, con las
decisiones del propietario del 22-10-2026 (D1 a D4 en `doc/rf02_corte3_informe.md`). Cubre:

- la prueba de cierre del corte: un informe sobre un padrón con errores sembrados (duplicados,
  solapes y coeficientes incompletos) nombra cada uno y no escribe nada; el mismo padrón limpio
  se aplica;
- `ultimo_dia` inclusivo → `vigente_hasta = ultimo_dia + 1`; correo obligatorio; PA01;
- todo o nada, también con una falla de la base a mitad de la escritura;
- alcance: `padron.gestionar` por unidad, general para crear unidades, `invitaciones.crear`;
- el importado sigue pendiente y sin ámbito; la invitación se crea, nombra su vínculo y no se
  envía;
- reintento, reimportación idéntica y reimportación corregida (D1);
- la invitación pendiente previa (D4.2) e `invitacion_pendiente_unica`;
- la anulación de un vínculo no iniciado, por la API y por soporte;
- RLS de `importacion_padron` y la ida y vuelta 0024 ⇄ 0025.
"""

import hashlib
import json
import threading
import time
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from cem_contracts.importacion import CodigoProblema, Problema
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.consorcios.importacion import leer_planilla
from app.db import transaccion
from scripts import anular_vinculo as script_anular
from scripts import invitar as script_invitar
from tests.conftest import ADMIN_URL, BASE, clave_idempotencia, url_rol
from tests.test_coeficientes_0021 import registrar
from tests.test_contexto_mandatos import (  # noqa: F401  (`edificio` es un fixture)
    cliente,
    denegaciones,
    edificio,
    segundo_consorcio_con_mandato,
)
from tests.test_importado_pendiente import mis_consorcios, neutro
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import cambiar
from tests.test_padron_rutas import escena as escena_padron  # noqa: F401  (es un fixture)
from tests.test_vinculo_0014 import (  # noqa: F401  (`base_aparte` es un fixture)
    _catalogo,
    _hoy,
    base_aparte,
    cadena_rota,
    eventos,
)

pytestmark = [pytest.mark.db, pytest.mark.redis]

ENCABEZADO = "unidad,nombre,correo,caracter,vigente_desde,ultimo_dia"
TABLAS = (
    "nucleo.unidad",
    "nucleo.persona",
    "nucleo.vinculo",
    "nucleo.invitacion",
    "nucleo.importacion_padron",
    "auditoria.eventos",
    "plataforma.outbox",
    "nucleo.mandato",
    "nucleo.facultad_otorgada",
    "nucleo.esquema_coeficientes",
)


@pytest.fixture
def escena(escena_padron):  # noqa: F811
    """El edificio del padrón (`test_padron_rutas`): 1° A y 2° B con sus propietarias, un
    administrador con `padron.gestionar` e `invitaciones.crear` generales y un miembro del Consejo
    sin `padron.gestionar`, en modo administrador aumentado."""
    return escena_padron


def f(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def correo_nuevo() -> str:
    return f"imp-{uuid4().hex[:10]}@ejemplo.com"


def planilla(*filas: str, encabezado: str = ENCABEZADO) -> str:
    return "\n".join((encabezado, *filas)) + "\n"


def informe_url(cons) -> str:
    return f"/api/v1/consorcios/{cons}/padron/importacion/informe"


def aplicar_url(cons) -> str:
    return f"/api/v1/consorcios/{cons}/padron/importacion"


def pedir_informe(persona, cons, archivo):
    with cliente(persona) as c:
        return c.post(informe_url(cons), json={"archivo": archivo})


def pedir_aplicar(persona, cons, archivo, clave=None):
    with cliente(persona) as c:
        return c.post(
            aplicar_url(cons), json={"archivo": archivo}, headers=clave or clave_idempotencia()
        )


def conteos(motor_migrate) -> dict[str, int]:
    with motor_migrate.connect() as c:
        return {t: c.execute(text(f"select count(*) from {t}")).scalar_one() for t in TABLAS}


def problemas(cuerpo: dict) -> dict[int, set[str]]:
    """Los códigos de problema de cada línea."""
    return {
        fila["linea"]: {p["codigo"] for p in fila["problemas"]}
        for fila in cuerpo["filas"]
        if fila["problemas"]
    }


def vinculos_de(motor_migrate, correo: str) -> list[dict]:
    with motor_migrate.connect() as c:
        return [
            dict(x)
            for x in c.execute(
                text(
                    "select v.id, v.unidad_id, v.tipo::text as tipo, v.vigente_desde,"
                    " v.vigente_hasta, v.fuente::text as fuente, v.origen, v.registrado_por,"
                    " v.confirmado_en, v.importacion_id from nucleo.vinculo v"
                    " join nucleo.persona p on p.id = v.persona_id where p.correo = :c"
                    " order by v.vigente_desde"
                ),
                {"c": correo},
            ).mappings()
        ]


def invitaciones_de(motor_migrate, correo: str) -> list[dict]:
    with motor_migrate.connect() as c:
        return [
            dict(x)
            for x in c.execute(
                text(
                    "select id, unidad_id, tipo::text as tipo, vinculo_id, invitada_por,"
                    " vence_en - creada_en as vigencia, revocada_en, aceptada_en"
                    " from nucleo.invitacion where correo = :c order by creada_en"
                ),
                {"c": correo},
            ).mappings()
        ]


def persona_de(motor_migrate, correo: str):
    with motor_migrate.connect() as c:
        return c.execute(
            text("select id from nucleo.persona where correo = :c"), {"c": correo}
        ).scalar_one()


def restringir(motor_migrate, mandato, facultad: str, unidad) -> None:
    """Acota una facultad del mandato a una unidad (D6)."""
    with motor_migrate.begin() as c:
        n = c.execute(
            text(
                "update nucleo.facultad_otorgada set recurso = :r"
                " where mandato_id = :m and facultad = :f"
            ),
            {"r": f"unidad:{unidad}", "m": mandato, "f": facultad},
        ).rowcount
    assert n == 1


# Lectura de la planilla ------------------------------------------------------------------------


def test_ultimo_dia_inclusivo_se_convierte_en_vigente_hasta_exclusivo():
    p = leer_planilla(planilla("3° C,Ana,ana@ejemplo.com,inquilino,01-03-2026,31-12-2026"))
    assert p.problemas == []
    [fila] = p.filas
    assert fila.relacion.ultimo_dia == date(2026, 12, 31)
    assert fila.relacion.hasta == date(2027, 1, 1)
    # Sin fin: los dos nulos.
    p = leer_planilla(planilla("3° C,Ana,ana@ejemplo.com,inquilino,01-03-2026,"))
    assert p.filas[0].relacion.ultimo_dia is None and p.filas[0].relacion.hasta is None


def test_la_planilla_no_acepta_vigente_hasta():
    p = leer_planilla(
        planilla(
            "3° C,Ana,ana@ejemplo.com,inquilino,01-03-2026,31-12-2026,01-01-2027",
            encabezado=ENCABEZADO + ",vigente_hasta",
        )
    )
    assert [x.codigo for x in p.problemas] == ["columna_vigente_hasta"]


@pytest.mark.parametrize(
    ("fila", "codigo"),
    [
        ("3° C,Ana,,inquilino,01-03-2026,", "relacion_sin_correo"),
        ("3° C,,ana@ejemplo.com,inquilino,01-03-2026,", "relacion_incompleta"),
        ("3° C,Ana,ana.ejemplo.com,inquilino,01-03-2026,", "correo_invalido"),
        ("3° C,Ana,ana@ejemplo.com,dueño,01-03-2026,", "caracter_invalido"),
        ("3° C,Ana,ana@ejemplo.com,inquilino,31-02-2026,", "fecha_invalida"),
        ("3° C,Ana,ana@ejemplo.com,inquilino,2026-03-01,", "fecha_invalida"),
        ("3° C,Ana,ana@ejemplo.com,inquilino,01-03-2026,28-02-2026", "fecha_invalida"),
        (f"3° C,{'x' * 101},ana@ejemplo.com,inquilino,01-03-2026,", "nombre_invalido"),
        (",Ana,ana@ejemplo.com,inquilino,01-03-2026,", "unidad_vacia"),
        ("3° C,Ana,ana@ejemplo.com,inquilino", "fila_mal_formada"),
    ],
)
def test_cada_fila_invalida_tiene_su_problema_y_no_lleva_relacion(fila, codigo):
    [leida] = leer_planilla(planilla(fila)).filas
    assert codigo in {x.codigo for x in leida.problemas}
    assert leida.relacion is None


def test_control_la_misma_fila_valida_no_tiene_problemas():
    [leida] = leer_planilla(planilla("3° C, Ana ,ANA@Ejemplo.com,Inquilino,01-03-2026,")).filas
    assert leida.problemas == []
    assert leida.relacion.correo == "ana@ejemplo.com"  # normalizado, como `nucleo.persona`
    assert leida.relacion.nombre == "Ana"


@pytest.mark.parametrize(
    ("encabezado", "codigo"),
    [
        ("unidad,nombre,correo", "encabezado_invalido"),  # relación a medias
        ("unidad,piso", "encabezado_invalido"),
        ("unidad,unidad", "encabezado_invalido"),
        ("nombre,correo,caracter,vigente_desde,ultimo_dia", "encabezado_invalido"),
        ("unidad,coeficiente_Agua", "coeficiente_invalido"),
    ],
)
def test_el_encabezado_se_valida(encabezado, codigo):
    p = leer_planilla(encabezado + "\nx\n")
    assert codigo in {x.codigo for x in p.problemas}


def test_una_planilla_vacia_o_sin_filas_no_se_aplica():
    assert [x.codigo for x in leer_planilla("").problemas] == ["archivo_vacio"]
    assert [x.codigo for x in leer_planilla(ENCABEZADO + "\n").problemas] == ["archivo_vacio"]


def test_el_hash_es_del_archivo_y_la_marca_de_orden_de_bytes_se_ignora():
    texto = planilla("3° C,,,,,")
    con_bom = leer_planilla("﻿" + texto)
    assert con_bom.problemas == [] and con_bom.filas[0].designacion == "3° C"
    assert leer_planilla(texto).sha256 != con_bom.sha256  # otro archivo, otro hash


# Prueba de cierre del corte -----------------------------------------------------------------


def test_cierre_informe_con_errores_sembrados_no_escribe_nada_y_limpio_se_aplica(
    escena, motor_migrate, motor_app
):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    a, b, otra = correo_nuevo(), correo_nuevo(), correo_nuevo()
    enc = ENCABEZADO + ",coeficiente_expensas_comunes"
    sembrada = planilla(
        f"3° C,Ana,{a},propietario,{f(hoy)},,50.00",  # 2
        f"3° c,Ana,{a},propietario,{f(hoy)},,50.00",  # 3: designación duplicada (PA01)
        f"4° D,Beto,{b},inquilino,{f(hoy)},{f(hoy + timedelta(days=90))},25.5",  # 4
        f"4° D,Beto,{b},inquilino,{f(hoy)},{f(hoy + timedelta(days=90))},25.5",  # 5: duplicada
        f"4° D,Beto,{b},inquilino,{f(hoy + timedelta(days=30))},,",  # 6: solapa la 4
        f"5° E,Caro,{otra},ocupante,31-02-2026,,",  # 7: fecha inválida
        "6° F,,,,,,",  # 8: sin coeficiente (incompleto)
        encabezado=enc,
    )
    antes = conteos(motor_migrate)
    r = pedir_informe(e["admin"], e["cons"], sembrada)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["aplicable"] is False
    assert problemas(cuerpo) == {
        2: {"designacion_ambigua"},
        3: {"designacion_ambigua"},
        5: {"relacion_duplicada"},
        4: {"vinculo_solapado"},
        6: {"vinculo_solapado"},
        7: {"fecha_invalida"},
    }
    [coef] = cuerpo["coeficientes"]
    assert coef["grupo"] == "expensas_comunes"
    # Incompletos: sin porcentaje las unidades existentes y las nuevas sin valor. La suma se
    # informa tal cual dice la planilla, sin corregirla (D4): 50 + 50 + 25.5.
    assert {"1° A", "2° B", "5° E", "6° F"} <= set(coef["sin_coeficiente"])
    assert Decimal(coef["suma"]) == Decimal("125.50")
    assert conteos(motor_migrate) == antes  # no escribió nada
    r = pedir_aplicar(e["admin"], e["cons"], sembrada)
    assert (r.status_code, r.json()["codigo"]) == (422, "importacion_con_problemas")
    assert conteos(motor_migrate) == antes

    # Control: el mismo padrón, limpio, se aplica.
    limpia = planilla(
        f"3° C,Ana,{a},propietario,{f(hoy)},,50.00",
        f"4° D,Beto,{b},inquilino,{f(hoy)},{f(hoy + timedelta(days=90))},25.5",
        f"5° E,Caro,{otra},ocupante,{f(hoy)},,24.46",
        "6° F,,,,,,0",
        encabezado=enc,
    )
    r = pedir_informe(e["admin"], e["cons"], limpia)
    assert r.json()["aplicable"] is True and problemas(r.json()) == {}
    r = pedir_aplicar(e["admin"], e["cons"], limpia)
    assert r.status_code == 200, r.text
    assert {k: v for k, v in r.json().items() if k != "importacion_id"} == {
        "archivo_sha256": leer_planilla(limpia).sha256.hex(),
        "unidades_creadas": 4,
        "vinculos_creados": 3,
        "invitaciones_creadas": 3,
        "invitaciones_reemplazadas": 0,
    }
    despues = conteos(motor_migrate)
    assert despues["nucleo.unidad"] == antes["nucleo.unidad"] + 4
    assert despues["nucleo.vinculo"] == antes["nucleo.vinculo"] + 3
    # D3: los coeficientes no se escriben. Importar no crea mandatos ni facultades.
    for tabla in ("nucleo.esquema_coeficientes", "nucleo.mandato", "nucleo.facultad_otorgada"):
        assert despues[tabla] == antes[tabla]
    assert cadena_rota(motor_migrate, e["cons"]) == 0


# Fechas, identidad y PA01 ------------------------------------------------------------------


def test_ultimo_dia_se_guarda_como_vigente_hasta_y_el_informe_muestra_las_dos(
    escena, motor_migrate
):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    ultimo = hoy + timedelta(days=10)
    archivo = planilla(f"2° B,Dora,{correo},ocupante,{f(hoy)},{f(ultimo)}")
    [fila] = pedir_informe(e["admin"], e["cons"], archivo).json()["filas"]
    assert (fila["ultimo_dia"], fila["vigente_hasta"]) == (
        ultimo.isoformat(),
        (ultimo + timedelta(days=1)).isoformat(),
    )
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    [v] = vinculos_de(motor_migrate, correo)
    assert v["vigente_hasta"] == ultimo + timedelta(days=1)


def test_el_vinculo_entra_importado_sin_confirmar_con_quien_lo_cargo_y_su_importacion(
    escena, motor_migrate
):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    r = pedir_aplicar(e["admin"], e["cons"], planilla(f"2° B,Eva,{correo},ocupante,{f(hoy)},"))
    assert r.status_code == 200, r.text
    [v] = vinculos_de(motor_migrate, correo)
    assert (v["fuente"], v["origen"], v["registrado_por"], v["confirmado_en"]) == (
        "importacion",
        "api",
        e["admin"],
        None,
    )
    assert str(v["importacion_id"]) == r.json()["importacion_id"]
    with motor_migrate.connect() as c:
        imp = c.execute(
            text(
                "select archivo_sha256, registrada_por from nucleo.importacion_padron where id = :i"
            ),
            {"i": v["importacion_id"]},
        ).one()
    assert imp.archivo_sha256.hex() == r.json()["archivo_sha256"]
    assert imp.registrada_por == e["admin"]
    # La auditoría nombra el vínculo y su importación, sin correo ni nombre (E31).
    [ev] = [x for x in eventos(motor_migrate, e["cons"], "consorcio.vinculo.importar")
            if x["entidad_id"] == v["id"]]  # fmt: skip
    assert ev["despues"]["importacion_id"] == str(v["importacion_id"])
    assert correo not in str(ev) and "Eva" not in str(ev)


def test_una_persona_existente_se_reutiliza_por_su_correo(escena, motor_migrate):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    with motor_migrate.connect() as c:
        correo = c.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": e["prop1"]}
        ).scalar_one()
    personas = conteos(motor_migrate)["nucleo.persona"]
    archivo = planilla(f"2° B,Otro Nombre,{correo.upper()},ocupante,{f(hoy)},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    assert conteos(motor_migrate)["nucleo.persona"] == personas
    assert any(v["unidad_id"] == e["u2"] for v in vinculos_de(motor_migrate, correo))
    with motor_migrate.connect() as c:  # el nombre de la persona no se sobrescribe
        assert (
            c.execute(
                text("select nombre from nucleo.persona where id = :p"), {"p": e["prop1"]}
            ).scalar_one()
            != "Otro Nombre"
        )


def test_el_informe_no_dice_si_un_correo_tiene_cuenta_en_otro_consorcio(
    escena, motor_migrate, motor_app
):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    cons_b, _ = segundo_consorcio_con_mandato(motor_migrate, motor_app, e["mc"])
    with motor_migrate.connect() as c:
        ajeno = c.execute(
            text(
                "select p.correo from nucleo.vinculo v join nucleo.persona p on p.id = v.persona_id"
                " where v.consorcio_id = :c limit 1"
            ),
            {"c": cons_b},
        ).scalar_one()
    con_cuenta = pedir_informe(
        e["admin"], e["cons"], planilla(f"2° B,X,{ajeno},ocupante,{f(hoy)},")
    )
    sin_cuenta = pedir_informe(
        e["admin"], e["cons"], planilla(f"2° B,X,{correo_nuevo()},ocupante,{f(hoy)},")
    )
    sin_hash = [{k: v for k, v in r.json().items() if k != "archivo_sha256"}
                for r in (con_cuenta, sin_cuenta)]  # fmt: skip
    assert sin_hash[0] == sin_hash[1]


def test_pa01_una_unidad_duplicada_no_sobrescribe_nada(escena, motor_migrate):
    e = escena
    antes = conteos(motor_migrate)
    # «1° a» es la «1° A» que ya existe escrita de otra forma: no se crea otra.
    r = pedir_informe(e["admin"], e["cons"], planilla("1°   a,,,,,"))
    assert problemas(r.json()) == {2: {"designacion_ambigua"}}
    assert pedir_aplicar(e["admin"], e["cons"], planilla("1°   a,,,,,")).status_code == 422
    assert conteos(motor_migrate) == antes
    # Control: escrita igual, es la existente y no se escribe nada.
    r = pedir_informe(e["admin"], e["cons"], planilla("1° A,,,,,"))
    assert r.json()["filas"][0]["unidad"] == "existente" and r.json()["sin_cambios"] is True


# Todo o nada ------------------------------------------------------------------------------


def test_una_falla_de_la_base_a_mitad_de_la_escritura_no_deja_nada(escena, motor_migrate):
    """La última fila pasa el informe y la base la rechaza al escribir (un disparador de la
    prueba): las unidades, personas, vínculos e invitaciones de las filas anteriores tampoco
    quedan."""
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    ultima = correo_nuevo()
    archivo = planilla(
        f"7° G,Gabi,{correo_nuevo()},propietario,{f(hoy)},",
        f"8° H,Hugo,{correo_nuevo()},inquilino,{f(hoy)},",
        f"2° B,Iris,{ultima},ocupante,{f(hoy)},",
    )
    assert pedir_informe(e["admin"], e["cons"], archivo).json()["aplicable"] is True
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "create function falla_sembrada_en_prueba() returns trigger language plpgsql"
                " as $$ begin if new.unidad_id = '" + str(e["u2"]) + "'::uuid then"
                " raise exception 'falla sembrada'; end if; return new; end $$"
            )
        )
        c.execute(
            text(
                "create trigger falla_sembrada before insert on nucleo.vinculo"
                " for each row execute function falla_sembrada_en_prueba()"
            )
        )
    try:
        antes = conteos(motor_migrate)
        with pytest.raises(DBAPIError, match="falla sembrada"):
            pedir_aplicar(e["admin"], e["cons"], archivo)
        assert conteos(motor_migrate) == antes
    finally:
        with motor_migrate.begin() as c:
            c.execute(text("drop trigger falla_sembrada on nucleo.vinculo"))
            c.execute(text("drop function falla_sembrada_en_prueba()"))
    # Control: sin el disparador, la misma planilla se aplica entera.
    assert pedir_aplicar(e["admin"], e["cons"], archivo).json()["vinculos_creados"] == 3


# Alcance --------------------------------------------------------------------------------------


def test_sin_padron_gestionar_no_hay_informe_ni_aplicacion(escena, motor_migrate, motor_app):
    e = escena
    archivo = planilla("2° B,,,,,")
    assert pedir_informe(e["mc"], e["cons"], archivo).status_code == 403
    assert pedir_aplicar(e["mc"], e["cons"], archivo).status_code == 403
    assert [d["accion"] for d in denegaciones(motor_app, e["cons"], e["admin"], e["mc"])] == [
        "padron.gestionar",
        "padron.gestionar",
    ]
    assert pedir_informe(e["admin"], e["cons"], archivo).status_code == 200  # control


def test_una_fila_fuera_de_su_unidad_rechaza_el_archivo_entero(escena, motor_migrate, motor_app):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    restringir(motor_migrate, e["m_admin"], "padron.gestionar", e["u2"])
    archivo = planilla(
        f"2° B,Juan,{correo_nuevo()},ocupante,{f(hoy)},",
        f"1° A,Kati,{correo_nuevo()},ocupante,{f(hoy)},",
    )
    r = pedir_informe(e["admin"], e["cons"], archivo)
    assert problemas(r.json()) == {3: {"fuera_de_alcance"}}
    antes = conteos(motor_migrate)
    r = pedir_aplicar(e["admin"], e["cons"], archivo)
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    assert conteos(motor_migrate)["nucleo.vinculo"] == antes["nucleo.vinculo"]
    assert denegaciones(motor_app, e["cons"], e["admin"], e["admin"])[-1]["accion"] == (
        "padron.gestionar"
    )
    # Control: solo su unidad, se aplica.
    archivo = planilla(f"2° B,Juan,{correo_nuevo()},ocupante,{f(hoy)},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200


def test_crear_una_unidad_exige_el_alcance_general(escena, motor_migrate):
    e = escena
    restringir(motor_migrate, e["m_admin"], "padron.gestionar", e["u2"])
    r = pedir_informe(e["admin"], e["cons"], planilla("2° B,,,,,", "9° Z,,,,,"))
    assert problemas(r.json()) == {3: {"fuera_de_alcance"}}
    assert (
        pedir_aplicar(e["admin"], e["cons"], planilla("2° B,,,,,", "9° Z,,,,,")).status_code == 403
    )


def test_crear_una_invitacion_exige_ademas_invitaciones_crear(escena, motor_migrate, motor_app):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    restringir(motor_migrate, e["m_admin"], "invitaciones.crear", e["u1"])
    vigente = planilla(f"2° B,Lia,{correo_nuevo()},ocupante,{f(hoy)},")
    r = pedir_informe(e["admin"], e["cons"], vigente)
    assert problemas(r.json()) == {2: {"sin_facultad_invitar"}}
    r = pedir_aplicar(e["admin"], e["cons"], vigente)
    assert r.status_code == 403
    assert denegaciones(motor_app, e["cons"], e["admin"], e["admin"])[-1]["accion"] == (
        "invitaciones.crear"
    )
    # Control: un vínculo ya terminado no lleva invitación, y entonces alcanza padron.gestionar.
    terminado = planilla(
        f"2° B,Lia,{correo_nuevo()},ocupante,{f(hoy - timedelta(days=60))},"
        f"{f(hoy - timedelta(days=30))}"
    )
    r = pedir_aplicar(e["admin"], e["cons"], terminado)
    assert r.status_code == 200 and r.json()["invitaciones_creadas"] == 0


# El importado pendiente y su invitación ---------------------------------------------------------


def test_el_importado_queda_sin_ambito_y_su_invitacion_lo_nombra_sin_enviarse(
    escena, motor_migrate, motor_app
):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    outbox = conteos(motor_migrate)["plataforma.outbox"]
    r = pedir_aplicar(e["admin"], e["cons"], planilla(f"2° B,Nora,{correo},propietario,{f(hoy)},"))
    assert r.status_code == 200, r.text
    [v] = vinculos_de(motor_migrate, correo)
    [inv] = invitaciones_de(motor_migrate, correo)
    assert (inv["vinculo_id"], inv["unidad_id"], inv["tipo"], inv["invitada_por"]) == (
        v["id"],
        e["u2"],
        "propietario",
        e["admin"],
    )
    assert inv["vigencia"] == timedelta(days=7)  # el plazo de las manuales (VIGENCIA_INVITACION)
    assert conteos(motor_migrate)["plataforma.outbox"] == outbox  # no se envía
    nora = persona_de(motor_migrate, correo)
    neutro(motor_app, nora, e["cons"])  # importar no da ámbito
    with cliente(nora) as c:
        [mia] = c.get("/api/v1/me/invitaciones").json()["elementos"]
        assert mia["id"] == str(inv["id"])
        r = c.post(f"/api/v1/me/invitaciones/{inv['id']}/aceptar")
    assert r.status_code == 200, r.text
    assert str(e["cons"]) in mis_consorcios(nora)
    [ev] = [x for x in eventos(motor_migrate, e["cons"], "identidad.invitacion.aceptar")
            if x["entidad_id"] == inv["id"]]  # fmt: skip
    assert ev["despues"]["vinculo_id"] == str(v["id"])
    assert len(vinculos_de(motor_migrate, correo)) == 1  # no creó otro


def test_la_invitacion_acepta_exactamente_su_vinculo_aunque_haya_otro(escena, motor_migrate):
    """Con `limit 1` (0014) la aceptación podía nombrar cualquiera de dos vínculos de la misma
    persona, unidad y carácter. La que creó la importación nombra el suyo."""
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    persona = otra_persona(motor_migrate, e["cons"])
    with motor_migrate.begin() as c:
        correo = c.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": persona}
        ).scalar_one()
        # Un vínculo futuro de otra fuente, que también se superpone con [hoy, ∞).
        otro = c.execute(
            text(
                "insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo,"
                " vigente_desde, fuente, origen) values (:c, :u, :p, 'ocupante', :d,"
                " 'documento', 'soporte') returning id"
            ),
            {"c": e["cons"], "u": e["u2"], "p": persona, "d": hoy + timedelta(days=100)},
        ).scalar_one()
    archivo = planilla(f"2° B,Olga,{correo},ocupante,{f(hoy)},{f(hoy + timedelta(days=99))}")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    importado = next(v["id"] for v in vinculos_de(motor_migrate, correo) if v["id"] != otro)
    [inv] = invitaciones_de(motor_migrate, correo)
    with cliente(persona) as c:
        assert c.post(f"/api/v1/me/invitaciones/{inv['id']}/aceptar").status_code == 200
    [ev] = [x for x in eventos(motor_migrate, e["cons"], "identidad.invitacion.aceptar")
            if x["entidad_id"] == inv["id"]]  # fmt: skip
    assert ev["despues"]["vinculo_id"] == str(importado)


@pytest.mark.parametrize("caso", ["borrado", "otro_caracter", "otra_unidad"])
def test_si_su_vinculo_ya_no_corresponde_la_aceptacion_falla_explicita(escena, motor_migrate, caso):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    futuro = hoy + timedelta(days=20)
    archivo = planilla(f"2° B,Pia,{correo},ocupante,{f(futuro)},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    [v] = vinculos_de(motor_migrate, correo)
    [inv] = invitaciones_de(motor_migrate, correo)
    if caso == "borrado":
        # El vínculo ya no existe y su invitación sigue pendiente. La anulación de la API la
        # retira (ver la prueba de anular y recargar); acá se borra como dueño de la tabla, para
        # probar la barrera de `aceptar_invitacion` sola.
        with motor_migrate.begin() as c:
            c.execute(text("delete from nucleo.vinculo where id = :v"), {"v": v["id"]})
    else:
        columna = {"otro_caracter": "tipo = 'inquilino'", "otra_unidad": "unidad_id = :u1"}[caso]
        with motor_migrate.begin() as c:
            c.execute(
                text(f"update nucleo.invitacion set {columna} where id = :i"),
                {"i": inv["id"], "u1": e["u1"]},
            )
    vinculos = conteos(motor_migrate)["nucleo.vinculo"]
    with cliente(persona_de(motor_migrate, correo)) as c:
        r = c.post(f"/api/v1/me/invitaciones/{inv['id']}/aceptar")
    assert (r.status_code, r.json()["codigo"]) == (409, "invitacion_vinculo_no_corresponde")
    assert conteos(motor_migrate)["nucleo.vinculo"] == vinculos  # no creó ni reutilizó otro
    assert invitaciones_de(motor_migrate, correo)[0]["aceptada_en"] is None


def test_control_una_invitacion_manual_sin_vinculo_sigue_como_en_0014(escena, motor_migrate):
    e = escena
    correo = correo_nuevo()
    with cliente(e["admin"]) as c:
        r = c.post(
            f"/api/v1/consorcios/{e['cons']}/invitaciones",
            json={
                "unidad_id": str(e["u2"]),
                "tipo": "ocupante",
                "correo": correo,
                "nombre": "Quique",
            },  # fmt: skip
            headers=clave_idempotencia(),
        )
    assert r.status_code == 201, r.text
    [inv] = invitaciones_de(motor_migrate, correo)
    assert inv["vinculo_id"] is None
    assert vinculos_de(motor_migrate, correo) == []


def test_cem_app_no_completa_vinculo_id_en_una_invitacion(escena, motor_app):
    e = escena
    with pytest.raises(DBAPIError, match="permission denied"):
        with transaccion(motor_app, persona_id=e["admin"], consorcio_id=e["cons"]) as c:
            c.execute(
                text(
                    "insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo, nombre,"
                    " token_hash, invitada_por, vence_en, vinculo_id) values (:c, :u, 'ocupante',"
                    " 'x@ejemplo.com', 'X', :h, :p, now() + interval '1 day', :v)"
                ),
                {
                    "c": e["cons"],
                    "u": e["u2"],
                    "h": uuid4().bytes * 2,
                    "p": e["admin"],
                    "v": e["v_inq_admin"],
                },  # fmt: skip
            )


# Invitaciones pendientes previas (D4.2) ---------------------------------------------------------


def invitar_a_mano(motor_migrate, cons, unidad, correo, *, vencida: bool):
    with motor_migrate.begin() as c:
        return c.execute(
            text(
                "insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo, nombre,"
                " token_hash, creada_en, vence_en) values (:c, :u, 'ocupante', :co, 'Manual', :h,"
                " now() - interval '3 days', case when :vencida then now() - interval '1 day'"
                " else now() + interval '3 days' end) returning id"
            ),
            {"c": cons, "u": unidad, "co": correo, "h": uuid4().bytes * 2, "vencida": vencida},
        ).scalar_one()


def test_una_pendiente_vigente_que_no_nombra_el_vinculo_bloquea(escena, motor_migrate):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    manual = invitar_a_mano(motor_migrate, e["cons"], e["u2"], correo, vencida=False)
    archivo = planilla(f"2° B,Rita,{correo},ocupante,{f(hoy)},")
    assert problemas(pedir_informe(e["admin"], e["cons"], archivo).json()) == {
        2: {"invitacion_pendiente"}
    }
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 422
    [inv] = invitaciones_de(motor_migrate, correo)
    assert (inv["id"], inv["revocada_en"]) == (manual, None)  # no se retiró


def test_una_pendiente_vencida_se_anuncia_y_se_reemplaza_al_aplicar(escena, motor_migrate):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    vieja = invitar_a_mano(motor_migrate, e["cons"], e["u2"], correo, vencida=True)
    archivo = planilla(f"2° B,Sara,{correo},ocupante,{f(hoy)},")
    [fila] = pedir_informe(e["admin"], e["cons"], archivo).json()["filas"]
    assert fila["invitacion"] == "reemplazar_vencida"
    assert invitaciones_de(motor_migrate, correo)[0]["revocada_en"] is None  # el informe no toca
    r = pedir_aplicar(e["admin"], e["cons"], archivo)
    assert r.json()["invitaciones_reemplazadas"] == 1
    anterior, nueva = invitaciones_de(motor_migrate, correo)
    assert anterior["id"] == vieja and anterior["revocada_en"] is not None
    assert nueva["vinculo_id"] == vinculos_de(motor_migrate, correo)[0]["id"]
    reemplazos = eventos(motor_migrate, e["cons"], "identidad.invitacion.reemplazar")
    assert vieja in [x["entidad_id"] for x in reemplazos]


def test_la_base_tampoco_retira_una_pendiente_vigente(escena, motor_migrate, motor_app):
    """`crear_invitacion_importada` garantiza D4.2 aunque la aplicación se equivoque."""
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    pedir_aplicar(
        e["admin"], e["cons"],
        planilla(f"2° B,Tina,{correo},ocupante,{f(hoy - timedelta(days=60))},"
                 f"{f(hoy - timedelta(days=30))}"),
    )  # fmt: skip
    # El vínculo terminado no lleva invitación; uno vigente sí: lo creo a mano para la prueba.
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.vinculo set vigente_hasta = null where id = :v"),
                  {"v": vinculos_de(motor_migrate, correo)[0]["id"]})  # fmt: skip
    invitar_a_mano(motor_migrate, e["cons"], e["u2"], correo, vencida=False)
    with pytest.raises(DBAPIError, match="invitacion_pendiente"):
        with transaccion(motor_app, persona_id=e["admin"], consorcio_id=e["cons"]) as c:
            c.execute(
                text("select nucleo.crear_invitacion_importada(:v, 'Tina', :h, :p, :a)"),
                {
                    "v": vinculos_de(motor_migrate, correo)[0]["id"],
                    "h": uuid4().bytes * 2,
                    "p": timedelta(days=7),
                    "a": e["admin"],
                },  # fmt: skip
            )


def test_dos_vinculos_vigentes_o_futuros_nuevos_no_comparten_invitacion(escena, motor_migrate):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    archivo = planilla(
        f"2° B,Uma,{correo},ocupante,{f(hoy)},{f(hoy + timedelta(days=9))}",
        f"2° B,Uma,{correo},ocupante,{f(hoy + timedelta(days=10))},",
    )
    assert problemas(pedir_informe(e["admin"], e["cons"], archivo).json()) == {
        2: {"invitaciones_simultaneas"},
        3: {"invitaciones_simultaneas"},
    }


def test_un_vinculo_terminado_no_lleva_invitacion(escena, motor_migrate):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    archivo = planilla(
        f"2° B,Vera,{correo},ocupante,{f(hoy - timedelta(days=60))},{f(hoy - timedelta(days=1))}"
    )
    [fila] = pedir_informe(e["admin"], e["cons"], archivo).json()["filas"]
    assert (fila["vinculo"], fila["invitacion"]) == ("crear", "ninguna")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    assert invitaciones_de(motor_migrate, correo) == []


# Reintento y reimportación (D1) ----------------------------------------------------------------


def test_un_reintento_con_la_misma_clave_responde_lo_mismo_sin_repetir(escena, motor_migrate):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    archivo = planilla(f"2° B,Wen,{correo_nuevo()},ocupante,{f(hoy)},", "10° A,,,,,")
    clave = clave_idempotencia()
    primera = pedir_aplicar(e["admin"], e["cons"], archivo, clave)
    despues = conteos(motor_migrate)
    segunda = pedir_aplicar(e["admin"], e["cons"], archivo, clave)
    assert primera.status_code == segunda.status_code == 200
    assert segunda.json() == primera.json()
    assert segunda.headers["idempotent-replayed"] == "true"
    assert conteos(motor_migrate) == despues


def test_reimportar_el_mismo_contenido_no_escribe_nada(escena, motor_migrate):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    archivo = planilla(f"2° B,Xime,{correo_nuevo()},ocupante,{f(hoy)},", "11° A,,,,,")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).json()["vinculos_creados"] == 1
    despues = conteos(motor_migrate)
    r = pedir_informe(e["admin"], e["cons"], archivo)
    assert r.json()["sin_cambios"] is True
    assert {x["unidad"] for x in r.json()["filas"]} == {"existente"}
    r = pedir_aplicar(e["admin"], e["cons"], archivo)  # otra clave: no es un reintento
    assert r.status_code == 200
    assert r.json()["importacion_id"] is None and r.json()["vinculos_creados"] == 0
    assert conteos(motor_migrate) == despues


def test_un_archivo_que_corrige_un_vinculo_aplicado_se_rechaza_entero(escena, motor_migrate):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    original = planilla(f"2° B,Yago,{correo},ocupante,{f(hoy)},{f(hoy + timedelta(days=30))}")
    assert pedir_aplicar(e["admin"], e["cons"], original).status_code == 200
    corregido = planilla(
        f"2° B,Yago,{correo},ocupante,{f(hoy)},{f(hoy + timedelta(days=60))}",
        "12° A,,,,,",
    )
    assert problemas(pedir_informe(e["admin"], e["cons"], corregido).json()) == {
        2: {"corrige_existente"}
    }
    antes = conteos(motor_migrate)
    assert pedir_aplicar(e["admin"], e["cons"], corregido).status_code == 422
    assert conteos(motor_migrate) == antes  # tampoco creó la 12° A
    [v] = vinculos_de(motor_migrate, correo)
    assert v["vigente_hasta"] == hoy + timedelta(days=31)  # el aplicado no se tocó
    # Control: el mismo archivo con lo aplicado igual y una fila nueva aplica solo la nueva.
    r = pedir_aplicar(e["admin"], e["cons"], original + "12° A,,,,,\n")
    assert (r.json()["unidades_creadas"], r.json()["vinculos_creados"]) == (1, 0)


# Coeficientes (D3) -----------------------------------------------------------------------------


def test_los_coeficientes_se_informan_contra_el_esquema_vigente_y_no_se_escriben(
    edificio,  # noqa: F811
    motor_migrate,
    motor_app,
):
    e = edificio  # 1° A y 2° B, con una decisión del equipo
    admin = otra_persona(motor_migrate, e["cons"])
    cambiar(motor_app, e["cons"], "administrador_aumentado", e["acta"])
    alta(motor_app, e["cons"], None, e["acta"], persona=admin, origen="soporte")
    registrar(motor_app, e["cons"], {str(e["u1"]): "60.000000", str(e["u2"]): "39.960000"},
              decision=e["acta"])  # fmt: skip
    esquemas = conteos(motor_migrate)["nucleo.esquema_coeficientes"]
    enc = "unidad,coeficiente_expensas_comunes"
    r = pedir_informe(admin, e["cons"], planilla("1° A,60", "2° B,39.95", encabezado=enc))
    [coef] = r.json()["coeficientes"]
    assert coef == {
        "grupo": "expensas_comunes",
        "suma": "99.95",
        "sin_coeficiente": [],
        "distintos_del_vigente": ["2° B"],
        "hay_esquema_vigente": True,
    }
    assert r.json()["esquemas_a_revisar"] == []
    # Una unidad nueva no está en el esquema vigente: el reparto se bloquearía (PA02).
    r = pedir_informe(admin, e["cons"], planilla("1° A,60", "3° C,", encabezado=enc))
    assert r.json()["esquemas_a_revisar"] == ["expensas_comunes"]
    assert r.json()["coeficientes"][0]["sin_coeficiente"] == ["2° B", "3° C"]
    assert (
        pedir_aplicar(admin, e["cons"], planilla("1° A,60", "3° C,", encabezado=enc)).status_code
        == 200
    )
    assert conteos(motor_migrate)["nucleo.esquema_coeficientes"] == esquemas


def test_un_coeficiente_invalido_o_contradictorio_es_un_problema(escena, motor_migrate):
    e = escena
    enc = "unidad,coeficiente_agua"
    r = pedir_informe(
        e["admin"], e["cons"], planilla("13° A,10,5", "13° A,7", "14° A,1", encabezado=enc)
    )
    assert problemas(r.json()) == {2: {"fila_mal_formada"}}
    r = pedir_informe(e["admin"], e["cons"], planilla("13° A,10", "13° A,7", "14° A,100.5",
                                                     encabezado=enc))  # fmt: skip
    assert problemas(r.json()) == {3: {"coeficiente_contradictorio"}, 4: {"coeficiente_invalido"}}


# Anulación de un vínculo no iniciado -------------------------------------------------------------


def anular(persona, cons, vinculo, motivo="Fecha cargada por error"):
    with cliente(persona) as c:
        return c.post(
            f"/api/v1/consorcios/{cons}/vinculos/{vinculo}/anular",
            json={"motivo": motivo},
            headers=clave_idempotencia(),
        )


def futuro_importado(e, motor_migrate) -> dict:
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    archivo = planilla(f"2° B,Zoe,{correo},ocupante,{f(hoy + timedelta(days=15))},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    return vinculos_de(motor_migrate, correo)[0]


def test_anular_borra_el_vinculo_futuro_y_audita_sin_el_motivo(escena, motor_migrate):
    e = escena
    v = futuro_importado(e, motor_migrate)
    r = anular(e["admin"], e["cons"], v["id"], motivo="Motivo que no se audita")
    assert r.status_code == 200, r.text
    assert r.json() == {"vinculo_id": str(v["id"]), "unidad_id": str(e["u2"]), "tipo": "ocupante"}
    with motor_migrate.connect() as c:
        assert c.execute(text("select count(*) from nucleo.vinculo where id = :v"),
                         {"v": v["id"]}).scalar_one() == 0  # fmt: skip
    [ev] = [x for x in eventos(motor_migrate, e["cons"], "consorcio.vinculo.anular")
            if x["entidad_id"] == v["id"]]  # fmt: skip
    assert ev["antes"]["vigente_desde"] == v["vigente_desde"].isoformat()
    assert ev["antes"]["importacion_id"] == str(v["importacion_id"])
    assert ev["despues"] is None and "Motivo que no se audita" not in str(ev)
    assert cadena_rota(motor_migrate, e["cons"]) == 0


def test_un_vinculo_que_ya_empezo_no_se_anula(escena, motor_migrate):
    e = escena
    r = anular(e["admin"], e["cons"], e["v_inq_admin"])
    assert (r.status_code, r.json()["codigo"]) == (409, "vinculo_iniciado")


def test_un_vinculo_confirmado_no_se_anula_por_la_api_y_si_por_soporte(
    escena, motor_migrate, monkeypatch, capsys
):
    """Por soporte (`0034`): sin eventos de auditoría. La fila se borra y la invitación se
    retira; lo anulado y la referencia quedan solo en la salida del script, sin el motivo."""
    e = escena
    v = futuro_importado(e, motor_migrate)
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "update nucleo.vinculo set confirmado_por = :p, confirmado_en = now(),"
                " confirmacion_referencia = 'Escritura 1' where id = :v"
            ),
            {"p": e["prop2"], "v": v["id"]},
        )
        [inv] = c.execute(
            text("select id from nucleo.invitacion where vinculo_id = :v and revocada_en is null"),
            {"v": v["id"]},
        ).scalars()
    r = anular(e["admin"], e["cons"], v["id"])
    assert (r.status_code, r.json()["codigo"]) == (409, "vinculo_confirmado")
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))
    base = ["--consorcio", str(e["cons"]), "--vinculo", str(v["id"])]
    base += ["--motivo", "Motivo reservado"]
    capsys.readouterr()
    assert script_anular.main(base + ["--referencia", "  "]) == 1  # sin respaldo, no
    assert "anulado" not in capsys.readouterr().out  # rechazada: nada impreso como anulado
    assert script_anular.main(base + ["--referencia", "Escritura corregida 815"]) == 0
    salida = capsys.readouterr().out
    assert f"vínculo {v['id']} anulado" in salida
    assert f"vigente_desde: {v['vigente_desde']}" in salida and "confirmado: True" in salida
    assert "referencia: Escritura corregida 815" in salida
    assert f"invitaciones retiradas: {inv}" in salida
    assert "Motivo reservado" not in salida
    with motor_migrate.connect() as c:
        assert c.execute(text("select count(*) from nucleo.vinculo where id = :v"),
                         {"v": v["id"]}).scalar_one() == 0  # fmt: skip
        revocada = text("select revocada_en is not null from nucleo.invitacion where id = :i")
        assert c.execute(revocada, {"i": inv}).scalar_one()
    assert [x for x in eventos(motor_migrate, e["cons"], "consorcio.vinculo.anular")
            if x["entidad_id"] == v["id"]] == []  # fmt: skip
    assert [x for x in eventos(motor_migrate, e["cons"], "identidad.invitacion.revocar")
            if x["entidad_id"] == inv] == []  # fmt: skip
    assert cadena_rota(motor_migrate, e["cons"]) == 0


def test_anular_exige_padron_gestionar_sobre_la_unidad(escena, motor_migrate, motor_app):
    e = escena
    v = futuro_importado(e, motor_migrate)
    assert anular(e["mc"], e["cons"], v["id"]).status_code == 403
    restringir(motor_migrate, e["m_admin"], "padron.gestionar", e["u1"])
    assert anular(e["admin"], e["cons"], v["id"]).status_code == 403
    assert vinculos_de(motor_migrate, vinculos_correo(motor_migrate, v["id"]))


def vinculos_correo(motor_migrate, vinculo) -> str:
    with motor_migrate.connect() as c:
        return c.execute(
            text(
                "select p.correo from nucleo.vinculo v join nucleo.persona p on p.id = v.persona_id"
                " where v.id = :v"
            ),
            {"v": vinculo},
        ).scalar_one()


def test_un_vinculo_de_otro_consorcio_o_inexistente_responde_404(escena, motor_migrate, motor_app):
    e = escena
    cons_b, _ = segundo_consorcio_con_mandato(motor_migrate, motor_app, e["admin"])
    with motor_migrate.connect() as c:
        ajeno = c.execute(
            text("select id from nucleo.vinculo where consorcio_id = :c limit 1"), {"c": cons_b}
        ).scalar_one()
    for vinculo in (uuid4(), ajeno):
        assert anular(e["admin"], e["cons"], vinculo).status_code == 404


@pytest.mark.parametrize(
    ("referencia", "actor", "origen", "motivo_base"),
    [
        (None, None, "soporte", "referencia_requerida"),
        ("Nota", "sesion", "api", "origen_invalido"),
        (None, None, "api", "actor_requerido"),
        (None, "sesion", "otro", "origen_invalido"),
    ],
)
def test_anular_valida_origen_actor_y_referencia(
    escena, motor_migrate, motor_app, referencia, actor, origen, motivo_base
):
    e = escena
    v = futuro_importado(e, motor_migrate)
    sesion = e["admin"] if origen != "soporte" else None
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=sesion, consorcio_id=e["cons"]) as c:
            c.execute(
                text("select nucleo.anular_vinculo(:v, 'Error', :r, :a, :o)"),
                {"v": v["id"], "r": referencia, "a": sesion if actor else None, "o": origen},
            )
    assert error.value.orig.diag.message_hint == motivo_base


# RLS, privilegios y migración ------------------------------------------------------------------


def test_otro_consorcio_no_ve_las_importaciones(escena, motor_migrate, motor_app):
    e = escena
    archivo = planilla("15° A,,,,,")
    importacion = pedir_aplicar(e["admin"], e["cons"], archivo).json()["importacion_id"]
    cons_b, _ = segundo_consorcio_con_mandato(motor_migrate, motor_app, e["admin"])
    consulta = text("select count(*) from nucleo.importacion_padron where id = :i")
    with transaccion(motor_app, persona_id=e["admin"], consorcio_id=cons_b) as c:
        assert c.execute(consulta, {"i": importacion}).scalar_one() == 0
    with transaccion(motor_app, persona_id=e["admin"], consorcio_id=e["cons"]) as c:
        assert c.execute(consulta, {"i": importacion}).scalar_one() == 1  # control


@pytest.mark.parametrize(
    "sentencia",
    [
        "insert into nucleo.importacion_padron (consorcio_id, archivo_sha256, registrada_por)"
        " values (:c, sha256('x'), :p)",
        "update nucleo.importacion_padron set registrada_por = :p where consorcio_id = :c",
        "delete from nucleo.importacion_padron where consorcio_id = :c",
    ],
)
def test_cem_app_no_escribe_importaciones_directamente(escena, motor_app, sentencia):
    e = escena
    with pytest.raises(DBAPIError, match="permission denied"):
        with transaccion(motor_app, persona_id=e["admin"], consorcio_id=e["cons"]) as c:
            c.execute(text(sentencia), {"c": e["cons"], "p": e["admin"]})


def test_importar_vinculo_exige_una_importacion_del_actor_y_del_consorcio(
    escena, motor_migrate, motor_app
):
    e = escena
    importacion = pedir_aplicar(e["admin"], e["cons"], planilla("16° A,,,,,")).json()[
        "importacion_id"
    ]
    hoy = _hoy(motor_migrate, e["cons"])
    sql = text("select nucleo.importar_vinculo(:i, :u, :c, 'N', 'ocupante', :d, null, :a)")
    with pytest.raises(DBAPIError, match="importacion_invalida"):
        with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
            c.execute(sql, {"i": importacion, "u": e["u2"], "c": correo_nuevo(), "d": hoy,
                            "a": e["mc"]})  # fmt: skip
    with pytest.raises(DBAPIError, match="actor_no_coincide"):
        with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
            c.execute(sql, {"i": importacion, "u": e["u2"], "c": correo_nuevo(), "d": hoy,
                            "a": e["admin"]})  # fmt: skip


def test_downgrade_con_una_importacion_se_niega_y_sin_ella_vuelve_exacto(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0024")
    antes = _catalogo(motor)
    privilegios = """select string_agg(attname || ':' || coalesce(attacl::text, ''), ',' order by
        attname) from pg_attribute where attrelid = 'nucleo.invitacion'::regclass and attnum > 0
        and not attisdropped"""
    with motor.connect() as c:
        tabla_antes = c.execute(
            text("select relacl::text from pg_class where oid = 'nucleo.invitacion'::regclass")
        ).scalar_one()
        columnas_antes = c.execute(text(privilegios)).scalar_one()
    command.upgrade(cfg, "0025")
    with motor.begin() as c:
        cons = c.execute(
            text("insert into nucleo.consorcio (nombre) values ('Aparte') returning id")
        ).scalar_one()
        persona = c.execute(
            text("insert into nucleo.persona (correo, nombre) values (:c, 'P') returning id"),
            {"c": correo_nuevo()},
        ).scalar_one()
        c.execute(
            text(
                "insert into nucleo.importacion_padron (consorcio_id, archivo_sha256,"
                " registrada_por) values (:c, sha256('x'), :p)"
            ),
            {"c": cons, "p": persona},
        )
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0024")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
    with motor.begin() as c:
        c.execute(text("delete from nucleo.importacion_padron"))
    command.downgrade(cfg, "0024")
    assert _catalogo(motor) == antes
    with motor.connect() as c:
        assert (
            c.execute(
                text("select relacl::text from pg_class where oid = 'nucleo.invitacion'::regclass")
            ).scalar_one()
            == tabla_antes
        )
        assert c.execute(text(privilegios)).scalar_one() == columnas_antes
    command.upgrade(cfg, "head")


# Revisión independiente del corte 3 ------------------------------------------------------------


def nombres_del_padron(persona, cons) -> set[str]:
    with cliente(persona) as c:
        r = c.get(f"/api/v1/consorcios/{cons}/padron")
    assert r.status_code == 200, r.text
    return {x["nombre"] for x in r.json()["elementos"]}


def test_el_padron_no_delata_ni_muestra_el_nombre_de_una_cuenta_de_otro_consorcio(
    escena, motor_migrate, motor_app
):
    """P1: si el padrón mostrara el nombre de la persona, importar un correo serviría para saber
    si tiene cuenta en otro consorcio y cómo se llama."""
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    cons_b, _ = segundo_consorcio_con_mandato(motor_migrate, motor_app, e["mc"])
    with motor_migrate.begin() as c:
        ajena, correo = c.execute(
            text(
                "select p.id, p.correo from nucleo.vinculo v"
                " join nucleo.persona p on p.id = v.persona_id where v.consorcio_id = :c limit 1"
            ),
            {"c": cons_b},
        ).one()
        c.execute(
            text("update nucleo.persona set nombre = 'Nombre Real' where id = :p"), {"p": ajena}
        )
    archivo = planilla(f"2° B,Sonda,{correo},ocupante,{f(hoy + timedelta(days=30))},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    nombres = nombres_del_padron(e["admin"], e["cons"])
    assert "Sonda" in nombres and "Nombre Real" not in nombres
    # Control: aceptada su invitación, el vínculo deja de estar pendiente y muestra su nombre.
    [inv] = invitaciones_de(motor_migrate, correo)
    with cliente(ajena) as c:
        assert c.post(f"/api/v1/me/invitaciones/{inv['id']}/aceptar").status_code == 200
    nombres = nombres_del_padron(e["admin"], e["cons"])
    assert "Nombre Real" in nombres and "Sonda" not in nombres


def test_anular_retira_su_invitacion_y_se_puede_volver_a_cargar_corregido(escena, motor_migrate):
    """P2: la invitación del vínculo anulado no queda pendiente bloqueando la recarga."""
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    errado = planilla(f"2° B,Ivo,{correo},ocupante,{f(hoy + timedelta(days=15))},")
    assert pedir_aplicar(e["admin"], e["cons"], errado).status_code == 200
    [v] = vinculos_de(motor_migrate, correo)
    [inv] = invitaciones_de(motor_migrate, correo)
    assert anular(e["admin"], e["cons"], v["id"]).status_code == 200
    assert invitaciones_de(motor_migrate, correo)[0]["revocada_en"] is not None
    with cliente(persona_de(motor_migrate, correo)) as c:  # ya no la ve ni la acepta
        assert c.get("/api/v1/me/invitaciones").json()["elementos"] == []
        r = c.post(f"/api/v1/me/invitaciones/{inv['id']}/aceptar")
    assert (r.status_code, r.json()["codigo"]) == (404, "invitacion_invalida")
    revocadas = eventos(motor_migrate, e["cons"], "identidad.invitacion.revocar")
    assert inv["id"] in [x["entidad_id"] for x in revocadas]
    corregido = planilla(f"2° B,Ivo,{correo},ocupante,{f(hoy + timedelta(days=45))},")
    r = pedir_aplicar(e["admin"], e["cons"], corregido)
    assert r.status_code == 200, r.text
    assert r.json()["invitaciones_creadas"] == 1


@pytest.mark.parametrize(
    "archivo",
    [
        planilla("3° C,Ana,ana@ejemplo.com,inquilino,01-03-2026,31-12-9999"),
        # El byte nulo en un campo que no es fecha: en `ultimo_dia` lo frenaba la fecha inválida
        # y la prueba pasaba por otra razón (Codex en app #105).
        planilla("3° \x00C,,,,,"),
        planilla("3° C,A\x00na,ana@ejemplo.com,inquilino,01-03-2026,"),
        ENCABEZADO + "\n" + "x" * 140_000 + ",,,,,\n",
    ],
    ids=["anio_9999", "byte_nulo_en_la_unidad", "byte_nulo_en_el_nombre", "campo_enorme"],
)
def test_una_planilla_patologica_es_un_problema_y_no_un_500(escena, archivo):
    e = escena
    r = pedir_informe(e["admin"], e["cons"], archivo)
    assert r.status_code == 200, r.text
    assert r.json()["aplicable"] is False
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 422


def test_vinculos_de_correos_exige_padron_gestionar_en_la_base(escena, motor_app):
    e = escena
    consulta = text("select count(*) from nucleo.vinculos_de_correos(array['x@ejemplo.com'])")
    with pytest.raises(DBAPIError, match="sin padron.gestionar"):
        with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
            c.execute(consulta)
    with transaccion(motor_app, persona_id=e["admin"], consorcio_id=e["cons"]) as c:
        assert c.execute(consulta).scalar_one() == 0  # control


def test_si_el_padron_cambia_durante_la_escritura_responde_409_y_no_deja_nada(
    escena, motor_migrate
):
    """Otra ruta escribe entre el informe recalculado y la escritura (aquí, un disparador que
    responde lo mismo que la base ante un solape)."""
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    archivo = planilla("17° A,,,,,", f"2° B,Ema,{correo_nuevo()},ocupante,{f(hoy)},")
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "create function solape_sembrado_en_prueba() returns trigger language plpgsql"
                " as $$ begin raise exception 'solape' using errcode = 'P0001',"
                " hint = 'vinculo_solapado'; end $$"
            )
        )
        c.execute(
            text(
                "create trigger solape_sembrado before insert on nucleo.vinculo"
                " for each row execute function solape_sembrado_en_prueba()"
            )
        )
    try:
        antes = conteos(motor_migrate)
        r = pedir_aplicar(e["admin"], e["cons"], archivo)
        assert (r.status_code, r.json()["codigo"]) == (409, "importacion_con_problemas")
        assert conteos(motor_migrate) == antes
    finally:
        with motor_migrate.begin() as c:
            c.execute(text("drop trigger solape_sembrado on nucleo.vinculo"))
            c.execute(text("drop function solape_sembrado_en_prueba()"))


def test_bordes_de_fecha_un_solo_dia_y_terminado_ayer(escena, motor_migrate):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    un_dia, ayer = correo_nuevo(), correo_nuevo()
    archivo = planilla(
        f"2° B,Uno,{un_dia},ocupante,{f(hoy + timedelta(days=3))},{f(hoy + timedelta(days=3))}",
        f"2° B,Dos,{ayer},ocupante,{f(hoy - timedelta(days=10))},{f(hoy - timedelta(days=1))}",
    )
    filas = pedir_informe(e["admin"], e["cons"], archivo).json()["filas"]
    assert [x["invitacion"] for x in filas] == ["crear", "ninguna"]
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    [v] = vinculos_de(motor_migrate, un_dia)
    assert (v["vigente_desde"], v["vigente_hasta"]) == (
        hoy + timedelta(days=3),
        hoy + timedelta(days=4),
    )
    assert vinculos_de(motor_migrate, ayer)[0]["vigente_hasta"] == hoy  # ya no vale hoy
    assert invitaciones_de(motor_migrate, ayer) == []


def test_si_la_invitacion_llega_a_otra_persona_la_aceptacion_falla_explicita(escena, motor_migrate):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    assert (
        pedir_aplicar(
            e["admin"], e["cons"], planilla(f"2° B,Ada,{correo},ocupante,{f(hoy)},")
        ).status_code
        == 200
    )
    [inv] = invitaciones_de(motor_migrate, correo)
    otra = otra_persona(motor_migrate, e["cons"])
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "update nucleo.invitacion set correo = (select correo from nucleo.persona"
                " where id = :p) where id = :i"
            ),
            {"p": otra, "i": inv["id"]},
        )
    with cliente(otra) as c:
        r = c.post(f"/api/v1/me/invitaciones/{inv['id']}/aceptar")
    assert (r.status_code, r.json()["codigo"]) == (409, "invitacion_vinculo_no_corresponde")


def test_dos_importaciones_iguales_a_la_vez_crean_una_sola_vez(escena, motor_migrate):
    e = escena
    archivo = planilla("18° A,,,,,", "19° A,,,,,")
    barrera = threading.Barrier(2)
    respuestas = []

    def importar():
        barrera.wait()
        respuestas.append(pedir_aplicar(e["admin"], e["cons"], archivo))

    hilos = [threading.Thread(target=importar) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert [r.status_code for r in respuestas] == [200, 200]
    assert sorted(r.json()["unidades_creadas"] for r in respuestas) == [0, 2]
    with motor_migrate.connect() as c:
        assert (
            c.execute(
                text(
                    "select count(*) from nucleo.unidad where consorcio_id = :c"
                    " and designacion in ('18° A', '19° A')"
                ),
                {"c": e["cons"]},
            ).scalar_one()
            == 2
        )


def test_los_coeficientes_no_listan_unidades_fuera_de_su_alcance(escena, motor_migrate):
    e = escena
    restringir(motor_migrate, e["m_admin"], "padron.gestionar", e["u2"])
    r = pedir_informe(
        e["admin"], e["cons"], planilla("2° B,10", encabezado="unidad,coeficiente_agua")
    )
    assert r.json()["coeficientes"][0]["sin_coeficiente"] == []  # no nombra la 1° A


# Decisiones del propietario del 23-10-2026 ------------------------------------------------------


def invitar_manual(persona, cons, unidad, correo, tipo="ocupante"):
    with cliente(persona) as c:
        return c.post(
            f"/api/v1/consorcios/{cons}/invitaciones",
            json={"unidad_id": str(unidad), "tipo": tipo, "correo": correo, "nombre": "Manual"},
            headers=clave_idempotencia(),
        )


@pytest.mark.parametrize("vencida", [False, True], ids=["vigente", "vencida"])
def test_una_invitacion_manual_no_reemplaza_la_importada(escena, motor_migrate, vencida):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    archivo = planilla(f"2° B,Lola,{correo},ocupante,{f(hoy)},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    [importada] = invitaciones_de(motor_migrate, correo)
    if vencida:
        with motor_migrate.begin() as c:
            c.execute(
                text(
                    "update nucleo.invitacion set creada_en = now() - interval '8 days',"
                    " vence_en = now() - interval '1 day' where id = :i"
                ),
                {"i": importada["id"]},
            )
    outbox = conteos(motor_migrate)["plataforma.outbox"]
    r = invitar_manual(e["admin"], e["cons"], e["u2"], correo)
    assert (r.status_code, r.json()["codigo"]) == (409, "invitacion_importada_pendiente")
    [sigue] = invitaciones_de(motor_migrate, correo)  # no se creó otra
    assert (sigue["id"], sigue["revocada_en"], sigue["vinculo_id"]) == (
        importada["id"],
        None,
        importada["vinculo_id"],
    )
    assert conteos(motor_migrate)["plataforma.outbox"] == outbox
    # Control: otro carácter no es incompatible y se invita como siempre.
    assert (
        invitar_manual(e["admin"], e["cons"], e["u2"], correo, tipo="inquilino").status_code == 201
    )


def test_el_script_del_equipo_tampoco_reemplaza_la_importada(escena, motor_migrate, monkeypatch):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    archivo = planilla(f"2° B,Mora,{correo},ocupante,{f(hoy)},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", "https://cem.ejemplo.com")
    argumentos = ["--consorcio", str(e["cons"]), "--unidad", str(e["u2"]), "--tipo", "ocupante"]
    assert script_invitar.main([*argumentos, "--correo", correo, "--nombre", "Mora"]) == 1
    assert len(invitaciones_de(motor_migrate, correo)) == 1


def test_la_invitacion_de_un_vinculo_finalizado_quedo_obsoleta(escena, motor_migrate, motor_app):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    archivo = planilla(f"2° B,Nina,{correo},ocupante,{f(hoy - timedelta(days=10))},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    [v] = vinculos_de(motor_migrate, correo)
    [inv] = invitaciones_de(motor_migrate, correo)
    with cliente(e["admin"]) as c:
        r = c.post(
            f"/api/v1/consorcios/{e['cons']}/vinculos/{v['id']}/finalizar",
            json={"motivo": "Fin de la ocupación"},
            headers=clave_idempotencia(),
        )
    assert r.status_code == 200, r.text
    nina = persona_de(motor_migrate, correo)
    with cliente(nina) as c:
        r = c.post(f"/api/v1/me/invitaciones/{inv['id']}/aceptar")
    assert (r.status_code, r.json()["codigo"]) == (409, "invitacion_vinculo_no_corresponde")
    assert invitaciones_de(motor_migrate, correo)[0]["aceptada_en"] is None
    aceptaciones = eventos(motor_migrate, e["cons"], "identidad.invitacion.aceptar")
    assert inv["id"] not in [x["entidad_id"] for x in aceptaciones]
    with transaccion(motor_app, persona_id=nina) as c:  # sigue pendiente: ninguna aceptación
        assert (
            v["id"]
            in c.execute(text("select nucleo.vinculos_importados_pendientes()")).scalars().all()
        )
    neutro(motor_app, nina, e["cons"])
    assert len(vinculos_de(motor_migrate, correo)) == 1


# Renovación por reimportación (decisión del propietario del 23-10-2026, opción 3) ----------------


def vencer(motor_migrate, invitacion) -> None:
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "update nucleo.invitacion set creada_en = now() - interval '8 days',"
                " vence_en = now() - interval '1 day' where id = :i"
            ),
            {"i": invitacion},
        )


def test_reimportar_la_misma_fila_renueva_la_invitacion_vencida_de_un_importado_pendiente(
    escena, motor_migrate, motor_app
):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    archivo = planilla(f"2° B,Rosa,{correo},ocupante,{f(hoy)},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    [v] = vinculos_de(motor_migrate, correo)
    [vieja] = invitaciones_de(motor_migrate, correo)
    vencer(motor_migrate, vieja["id"])
    r = pedir_informe(e["admin"], e["cons"], archivo)
    [fila] = r.json()["filas"]
    assert (fila["vinculo"], fila["invitacion"]) == ("existente", "reemplazar_vencida")
    assert r.json()["sin_cambios"] is False
    vinculos = conteos(motor_migrate)["nucleo.vinculo"]
    r = pedir_aplicar(e["admin"], e["cons"], archivo)
    assert r.status_code == 200, r.text
    assert {k: r.json()[k] for k in ("vinculos_creados", "invitaciones_creadas",
                                       "invitaciones_reemplazadas")} == {
        "vinculos_creados": 0,
        "invitaciones_creadas": 1,
        "invitaciones_reemplazadas": 1,
    }  # fmt: skip
    assert conteos(motor_migrate)["nucleo.vinculo"] == vinculos  # el vínculo no se tocó
    anterior, nueva = invitaciones_de(motor_migrate, correo)
    assert anterior["id"] == vieja["id"] and anterior["revocada_en"] is not None
    assert (nueva["vinculo_id"], nueva["revocada_en"]) == (v["id"], None)
    # Idempotente: con la invitación renovada y vigente, la misma planilla no escribe nada.
    assert pedir_informe(e["admin"], e["cons"], archivo).json()["sin_cambios"] is True
    # Y la renovada se acepta y nombra el mismo vínculo.
    with cliente(persona_de(motor_migrate, correo)) as c:
        assert c.post(f"/api/v1/me/invitaciones/{nueva['id']}/aceptar").status_code == 200
    [ev] = [x for x in eventos(motor_migrate, e["cons"], "identidad.invitacion.aceptar")
            if x["entidad_id"] == nueva["id"]]  # fmt: skip
    assert ev["despues"]["vinculo_id"] == str(v["id"])


@pytest.mark.parametrize("caso", ["confirmado", "terminado"])
def test_no_se_renueva_si_el_vinculo_ya_no_esta_pendiente_o_termino(escena, motor_migrate, caso):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    archivo = planilla(f"2° B,Sol,{correo},ocupante,{f(hoy - timedelta(days=5))},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    [v] = vinculos_de(motor_migrate, correo)
    [inv] = invitaciones_de(motor_migrate, correo)
    vencer(motor_migrate, inv["id"])
    if caso == "confirmado":
        with motor_migrate.begin() as c:
            c.execute(
                text(
                    "update nucleo.vinculo set confirmado_por = :p, confirmado_en = now(),"
                    " confirmacion_referencia = 'Escritura 2' where id = :v"
                ),
                {"p": e["prop2"], "v": v["id"]},
            )
    else:
        with cliente(e["admin"]) as c:
            assert (
                c.post(
                    f"/api/v1/consorcios/{e['cons']}/vinculos/{v['id']}/finalizar",
                    json={"motivo": "Fin"},
                    headers=clave_idempotencia(),
                ).status_code
                == 200
            )
        hasta = _hoy(motor_migrate, e["cons"])
        archivo = planilla(
            f"2° B,Sol,{correo},ocupante,{f(hoy - timedelta(days=5))},"
            f"{f(hasta - timedelta(days=1))}"
        )
    r = pedir_informe(e["admin"], e["cons"], archivo)
    assert r.json()["filas"][0]["invitacion"] == "ninguna"
    assert r.json()["sin_cambios"] is True
    antes = conteos(motor_migrate)
    assert pedir_aplicar(e["admin"], e["cons"], archivo).json()["importacion_id"] is None
    assert conteos(motor_migrate) == antes


def test_renovar_tambien_exige_invitaciones_crear(escena, motor_migrate):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    archivo = planilla(f"2° B,Tea,{correo},ocupante,{f(hoy)},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    vencer(motor_migrate, invitaciones_de(motor_migrate, correo)[0]["id"])
    restringir(motor_migrate, e["m_admin"], "invitaciones.crear", e["u1"])
    assert problemas(pedir_informe(e["admin"], e["cons"], archivo).json()) == {
        2: {"sin_facultad_invitar"}
    }
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 403


def test_la_base_no_crea_otra_invitacion_para_un_vinculo_que_ya_no_esta_pendiente(
    escena, motor_migrate, motor_app
):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    archivo = planilla(f"2° B,Uri,{correo},ocupante,{f(hoy)},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    [v] = vinculos_de(motor_migrate, correo)
    [inv] = invitaciones_de(motor_migrate, correo)
    with cliente(persona_de(motor_migrate, correo)) as c:
        assert c.post(f"/api/v1/me/invitaciones/{inv['id']}/aceptar").status_code == 200
    with pytest.raises(DBAPIError, match="vinculo_no_pendiente"):
        with transaccion(motor_app, persona_id=e["admin"], consorcio_id=e["cons"]) as c:
            c.execute(
                text("select nucleo.crear_invitacion_importada(:v, 'Uri', :h, :p, :a)"),
                {"v": v["id"], "h": uuid4().bytes * 2, "p": timedelta(days=7), "a": e["admin"]},
            )


# Revisión de Codex en app #105 -----------------------------------------------------------------


def test_un_reintento_de_la_anulacion_repite_el_200_aunque_el_vinculo_ya_no_exista(
    escena, motor_migrate
):
    e = escena
    v = futuro_importado(e, motor_migrate)
    clave = clave_idempotencia()
    with cliente(e["admin"]) as c:
        url = f"/api/v1/consorcios/{e['cons']}/vinculos/{v['id']}/anular"
        primera = c.post(url, json={"motivo": "Cargado por error"}, headers=clave)
        segunda = c.post(url, json={"motivo": "Cargado por error"}, headers=clave)
        otra_clave = c.post(url, json={"motivo": "Cargado por error"}, headers=clave_idempotencia())
    assert primera.status_code == segunda.status_code == 200, segunda.text
    assert segunda.json() == primera.json()
    assert segunda.headers["idempotent-replayed"] == "true"
    # Control: con otra clave ya no es un reintento, y el vínculo no existe.
    assert otra_clave.status_code == 404
    anulaciones = eventos(motor_migrate, e["cons"], "consorcio.vinculo.anular")
    assert [x["entidad_id"] for x in anulaciones].count(v["id"]) == 1


def test_una_cuenta_borrada_no_es_la_persona_de_su_correo_en_el_informe(escena, motor_migrate):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    archivo = planilla(f"2° B,Vale,{correo},ocupante,{f(hoy)},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    [vieja] = vinculos_de(motor_migrate, correo)
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.persona set eliminado_en = now() where correo = :c"),
            {"c": correo},
        )
        # Su invitación también se retira: si no, la regla D4.2 bloquearía la fila por otra razón.
        c.execute(
            text("update nucleo.invitacion set revocada_en = now() where correo = :c"),
            {"c": correo},
        )
    # La misma fila no es un vínculo existente: la cuenta borrada no cuenta.
    [fila] = pedir_informe(e["admin"], e["cons"], archivo).json()["filas"]
    assert (fila["vinculo"], fila["problemas"]) == ("crear", [])
    r = pedir_aplicar(e["admin"], e["cons"], archivo)
    assert r.status_code == 200 and r.json()["vinculos_creados"] == 1
    personas = {v["id"] for v in vinculos_de(motor_migrate, correo)} - {vieja["id"]}
    assert len(personas) == 1  # un vínculo nuevo, de la persona nueva con ese correo
    with motor_migrate.connect() as c:
        assert (
            c.execute(
                text(
                    "select count(*) from nucleo.persona where correo = :c and eliminado_en is null"
                ),
                {"c": correo},
            ).scalar_one()
            == 1
        )


# Tanda única de correcciones sobre 4b978a1 (decisión del propietario del 23-10-2026) -------------


def test_una_invitacion_vencida_de_otro_vinculo_bloquea_y_no_se_reemplaza(
    escena, motor_migrate, motor_app
):
    """D4.2: la que nombra otro vínculo es un error bloqueante aunque haya vencido."""
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    primero = planilla(f"2° B,Ali,{correo},ocupante,{f(hoy)},{f(hoy + timedelta(days=9))}")
    assert pedir_aplicar(e["admin"], e["cons"], primero).status_code == 200
    [a] = vinculos_de(motor_migrate, correo)
    [inv_a] = invitaciones_de(motor_migrate, correo)
    vencer(motor_migrate, inv_a["id"])
    segundo = planilla(f"2° B,Ali,{correo},ocupante,{f(hoy + timedelta(days=20))},")
    assert problemas(pedir_informe(e["admin"], e["cons"], segundo).json()) == {
        2: {"invitacion_pendiente"}
    }
    antes = conteos(motor_migrate)
    assert pedir_aplicar(e["admin"], e["cons"], segundo).status_code == 422
    assert conteos(motor_migrate) == antes
    assert invitaciones_de(motor_migrate, correo)[0]["revocada_en"] is None  # no se retiró
    # La base defiende la misma regla: un vínculo B de la misma persona, unidad y carácter no
    # retira la invitación vencida que nombra a A.
    with motor_migrate.begin() as c:
        b = c.execute(
            text(
                "insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo,"
                " vigente_desde, fuente, origen, registrado_por, importacion_id,"
                " nombre_declarado) select consorcio_id, unidad_id, persona_id, tipo, :d,"
                " fuente, origen, registrado_por, importacion_id, 'Ali' from nucleo.vinculo"
                " where id = :a returning id"
            ),
            {"a": a["id"], "d": hoy + timedelta(days=20)},
        ).scalar_one()
    with pytest.raises(DBAPIError, match="invitacion_pendiente"):
        with transaccion(motor_app, persona_id=e["admin"], consorcio_id=e["cons"]) as c:
            c.execute(
                text("select nucleo.crear_invitacion_importada(:v, 'Ali', :h, :p, :a)"),
                {"v": b, "h": uuid4().bytes * 2, "p": timedelta(days=7), "a": e["admin"]},
            )


@pytest.mark.parametrize(
    "correo", ["ana@@ejemplo.com", "ana@ejemplo", "@ejemplo.com", "ana@ejemplo.com@x.com"]
)
def test_el_correo_tiene_el_formato_de_identidad(correo):
    [leida] = leer_planilla(planilla(f"3° C,Ana,{correo},inquilino,01-03-2026,")).filas
    assert "correo_invalido" in {x.codigo for x in leida.problemas}


def test_la_base_tampoco_crea_una_persona_con_un_correo_mal_formado(
    escena, motor_migrate, motor_app
):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    importacion = pedir_aplicar(e["admin"], e["cons"], planilla("20° A,,,,,")).json()[
        "importacion_id"
    ]
    sql = text("select nucleo.importar_vinculo(:i, :u, :c, 'Ana', 'ocupante', :d, null, :a)")
    for correo in ("ana@@ejemplo.com", "ana@ejemplo"):
        with pytest.raises(DBAPIError, match="correo_invalido"):
            with transaccion(motor_app, persona_id=e["admin"], consorcio_id=e["cons"]) as c:
                c.execute(sql, {"i": importacion, "u": e["u2"], "c": correo, "d": hoy,
                                "a": e["admin"]})  # fmt: skip
    with motor_migrate.connect() as c:
        assert (
            c.execute(
                text(
                    "select count(*) from nucleo.persona where correo in ('ana@@ejemplo.com',"
                    " 'ana@ejemplo')"
                )  # fmt: skip
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize(
    "renglon",
    ['"3° C"xyz,,,,,', '3° C,"Ana,ana@ejemplo.com,inquilino,01-03-2026,\n4° D,,,,,'],
    ids=["texto_despues_de_las_comillas", "comillas_sin_cerrar"],
)
def test_comillas_mal_formadas_son_un_problema_y_no_un_campo_alterado(renglon):
    p = leer_planilla(planilla(renglon))
    assert [x.codigo for x in p.problemas] == ["fila_mal_formada"]
    assert p.filas == []


def test_control_las_comillas_bien_formadas_se_leen():
    [leida] = leer_planilla(planilla('"3° C","Ana, la de arriba",ana@ejemplo.com,inquilino,'
                                     "01-03-2026,")).filas  # fmt: skip
    assert leida.problemas == [] and leida.relacion.nombre == "Ana, la de arriba"


def test_fuera_de_su_alcance_no_se_infiere_si_una_unidad_existe_ni_como_se_llama(
    escena, motor_migrate
):
    e = escena
    restringir(motor_migrate, e["m_admin"], "padron.gestionar", e["u2"])
    # Con una fila de su unidad (2° B), para que haya informe: una planilla solo con unidades
    # ajenas se niega entera (403), igual exista o no la unidad.
    respuestas = {
        d: pedir_informe(e["admin"], e["cons"], planilla("2° B,,,,,", f"{d},,,,,")).json()
        for d in ("1° A", "1°  a", "21° Z")  # existe fuera de su alcance, ambigua, no existe
    }
    filas = {d: r["filas"][1] for d, r in respuestas.items()}
    assert {
        (f["unidad"], tuple((x["codigo"], x["mensaje"]) for x in f["problemas"]))
        for f in filas.values()
    } == {(None, (("fuera_de_alcance", filas["21° Z"]["problemas"][0]["mensaje"]),))}
    assert "1° A" not in str(respuestas["1°  a"])  # no nombra la unidad que no ve
    for d in ("1° A", "1°  a", "21° Z"):  # y una planilla solo con la ajena: el mismo 403
        assert pedir_informe(e["admin"], e["cons"], planilla(f"{d},,,,,")).status_code == 403
    # Control: con alcance general, la ambigüedad sí se informa y nombra la existente.
    restringir_general(motor_migrate, e["m_admin"], "padron.gestionar")
    r = pedir_informe(e["admin"], e["cons"], planilla("1°  a,,,,,")).json()
    assert problemas(r) == {2: {"designacion_ambigua"}} and "«1° A»" in str(r)


def restringir_general(motor_migrate, mandato, facultad: str) -> None:
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "update nucleo.facultad_otorgada set recurso = null"
                " where mandato_id = :m and facultad = :f"
            ),
            {"m": mandato, "f": facultad},
        )


# Dos P2 de Codex sobre 69f1f77 (decisión del propietario del 23-10-2026) --------------------------


def test_un_encabezado_enorme_de_columnas_repetidas_se_valida_en_una_sola_pasada():
    """Con `columnas.count` por columna, 50 000 columnas tardaban segundos (y el tope de un
    millón de caracteres admite cientos de miles). La validación y la respuesta no cambian."""
    encabezado = "unidad," + ",".join(["x"] * 50_000)
    inicio = time.perf_counter()
    p = leer_planilla(encabezado + "\n")
    assert time.perf_counter() - inicio < 1
    assert (
        Problema(codigo=CodigoProblema.ENCABEZADO_INVALIDO, mensaje="Columnas repetidas: x.")
        in p.problemas
    )


def test_un_surrogate_suelto_es_una_planilla_invalida_y_no_un_500(escena, motor_migrate):
    """`"\\ud800"` en el JSON. Por la API, el contrato (Pydantic) ya lo rechaza antes del
    importador: 422 `peticion_invalida`, sin escribir nada (pasaba igual sobre 69f1f77; el 500 que
    señaló Codex no se reproduce por la ruta). `leer_planilla`, en cambio, fallaba con
    `UnicodeEncodeError` si se la llamaba con ese texto: ahora lo rechaza con un hash
    determinista."""
    e = escena
    archivo = planilla("3° \ud800C,,,,,")
    # `json.dumps` escribe el surrogate como `\ud800`, que es como llega en un JSON real (httpx
    # con `json=` no puede codificarlo).
    cuerpo = json.dumps({"archivo": archivo})
    assert "\\ud800" in cuerpo
    antes = conteos(motor_migrate)
    with cliente(e["admin"]) as c:
        for url in (informe_url(e["cons"]), aplicar_url(e["cons"])):
            r = c.post(
                url,
                content=cuerpo,
                headers={"content-type": "application/json", **clave_idempotencia()},
            )
            assert (r.status_code, r.json()["codigo"]) == (422, "peticion_invalida")
    assert conteos(motor_migrate) == antes
    # La función: un problema de la planilla, sin filas, con el hash de los bytes
    # `surrogatepass` (el mismo en cada llamada).
    p = leer_planilla(archivo)
    assert [x.codigo for x in p.problemas] == ["fila_mal_formada"] and p.filas == []
    esperado = hashlib.sha256(archivo.encode("utf-8", "surrogatepass")).digest()
    assert p.sha256 == leer_planilla(archivo).sha256 == esperado
    # Control: sin surrogates, el hash es el SHA-256 de siempre (UTF-8).
    limpio = planilla("3° C,,,,,")
    assert leer_planilla(limpio).sha256 == hashlib.sha256(limpio.encode("utf-8")).digest()


# Dos P2 de Codex sobre 70e889c y el P3 4 de la revisión sobre 69f1f77 ----------------------------


def test_muchos_solapes_dan_a_lo_sumo_un_diagnostico_por_fila(escena, motor_migrate):
    """400 filas de la misma persona, unidad y carácter que se superponen todas entre sí: antes,
    un diagnóstico por cada par (159 600)."""
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    filas = [f"2° B,Ema,{correo},ocupante,{f(hoy + timedelta(days=i))}," for i in range(400)]
    r = pedir_informe(e["admin"], e["cons"], planilla(*filas))
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["aplicable"] is False
    por_fila = [len(x["problemas"]) for x in cuerpo["filas"]]
    assert max(por_fila) == 1
    assert {p["codigo"] for x in cuerpo["filas"] for p in x["problemas"]} == {"vinculo_solapado"}
    assert sum(por_fila) == 400  # todas se superponen con otra: todas quedan marcadas


def esperar_que_alguien_espere_un_bloqueo(salvo_pid: int, cuantos: int = 1) -> None:
    """Con el rol administrador: `cem_migrate` no ve en qué espera un proceso de `cem_app`."""
    admin = create_engine(ADMIN_URL)
    try:
        for _ in range(400):
            with admin.connect() as c:
                if (
                    c.execute(
                        text(
                            "select count(*) from pg_stat_activity where datname = :base"
                            " and wait_event_type = 'Lock' and pid <> :p"
                        ),
                        {"base": BASE, "p": salvo_pid},
                    ).scalar_one()
                    >= cuantos
                ):
                    return
            time.sleep(0.05)
    finally:
        admin.dispose()
    raise AssertionError("la otra operación nunca quedó esperando un bloqueo")


def en_un_hilo(funcion) -> tuple[threading.Thread, dict]:
    resultado: dict = {}

    def correr():
        try:
            resultado["respuesta"] = funcion()
        except Exception as error:  # noqa: BLE001  (la prueba lo muestra si falla)
            resultado["error"] = error

    hilo = threading.Thread(target=correr)
    hilo.start()
    return hilo, resultado


def test_una_importacion_y_una_aceptacion_del_mismo_correo_no_se_traban(escena, motor_migrate):
    """Dos conexiones reales. La primera toma los bloqueos en el orden de `aceptar_invitacion`
    (persona y después la cadena de auditoría del consorcio) y la importación corre en otra. Si
    la importación tomara la auditoría antes que la persona, PostgreSQL detectaría un deadlock y
    abortaría una de las dos."""
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    archivo = planilla("22° A,,,,,", f"2° B,Abi,{correo},ocupante,{f(hoy)},")
    with motor_migrate.connect() as aceptacion:
        with aceptacion.begin():
            pid = aceptacion.execute(text("select pg_backend_pid()")).scalar_one()
            aceptacion.execute(
                text("select pg_advisory_xact_lock(hashtextextended('persona:' || :c, 0))"),
                {"c": correo},
            )
            hilo, resultado = en_un_hilo(lambda: pedir_aplicar(e["admin"], e["cons"], archivo))
            esperar_que_alguien_espere_un_bloqueo(pid)
            aceptacion.execute(
                text(
                    "insert into auditoria.eventos (consorcio_id, origen, accion, entidad,"
                    " hash_previo, hash) values (:c, 'soporte', 'prueba.orden_de_bloqueos',"
                    " 'prueba', '', '')"
                ),
                {"c": e["cons"]},
            )
    hilo.join(60)
    assert "error" not in resultado, resultado.get("error")
    assert resultado["respuesta"].status_code == 200, resultado["respuesta"].text
    assert resultado["respuesta"].json()["vinculos_creados"] == 1


def test_una_anulacion_y_una_aceptacion_del_mismo_vinculo_no_se_traban(escena, motor_migrate):
    """P3 4 de la revisión sobre 69f1f77. `aceptar_invitacion` bloquea la invitación y después
    el vínculo; la anulación hacía lo contrario. Dos conexiones reales, la primera en el orden de
    la aceptación."""
    e = escena
    v = futuro_importado(e, motor_migrate)
    [inv] = invitaciones_de(motor_migrate, vinculos_correo(motor_migrate, v["id"]))
    with motor_migrate.connect() as aceptacion:
        with aceptacion.begin():
            pid = aceptacion.execute(text("select pg_backend_pid()")).scalar_one()
            aceptacion.execute(
                text("select 1 from nucleo.invitacion where id = :i for update"), {"i": inv["id"]}
            )
            hilo, resultado = en_un_hilo(lambda: anular(e["admin"], e["cons"], v["id"]))
            esperar_que_alguien_espere_un_bloqueo(pid)
            aceptacion.execute(
                text("select 1 from nucleo.vinculo where id = :v for update"), {"v": v["id"]}
            )
    hilo.join(60)
    assert "error" not in resultado, resultado.get("error")
    assert resultado["respuesta"].status_code == 200, resultado["respuesta"].text


# Barrido de bloqueos y de diagnósticos sobre 7e28cd5 (decisión del propietario del 23-10-2026) ---


def test_una_renovacion_y_un_retiro_de_la_misma_invitacion_no_se_traban(escena, motor_migrate):
    """Codex en app #105. `revocar` bloquea la invitación y después audita; la renovación
    auditaba (la importación) antes de bloquear la invitación vencida en
    `crear_invitacion_importada`. Dos conexiones reales, la primera en el orden de `revocar`."""
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    archivo = planilla(f"2° B,Ivo,{correo},ocupante,{f(hoy)},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    [vieja] = invitaciones_de(motor_migrate, correo)
    vencer(motor_migrate, vieja["id"])
    with motor_migrate.connect() as retiro:
        with retiro.begin():
            pid = retiro.execute(text("select pg_backend_pid()")).scalar_one()
            retiro.execute(
                text("select 1 from nucleo.invitacion where id = :i for update"),
                {"i": vieja["id"]},
            )
            hilo, resultado = en_un_hilo(lambda: pedir_aplicar(e["admin"], e["cons"], archivo))
            esperar_que_alguien_espere_un_bloqueo(pid)
            retiro.execute(
                text(
                    "insert into auditoria.eventos (consorcio_id, origen, accion, entidad,"
                    " hash_previo, hash) values (:c, 'soporte', 'prueba.orden_de_bloqueos',"
                    " 'prueba', '', '')"
                ),
                {"c": e["cons"]},
            )
    hilo.join(60)
    assert "error" not in resultado, resultado.get("error")
    assert resultado["respuesta"].status_code == 200, resultado["respuesta"].text
    assert resultado["respuesta"].json()["invitaciones_reemplazadas"] == 1


def largo_de_los_diagnosticos(cuerpo: dict) -> int:
    return sum(len(p["mensaje"]) for x in cuerpo["filas"] for p in x["problemas"])


def test_las_invitaciones_simultaneas_se_informan_con_un_ejemplo_por_fila(escena, motor_migrate):
    """Codex en app #105: cada fila nombraba todas las demás (2000 filas, 22 millones de
    caracteres). Ahora, un ejemplo y el total."""
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    filas = [
        f"2° B,Lu,{correo},ocupante,{f(hoy + timedelta(days=2 * i))},"
        f"{f(hoy + timedelta(days=2 * i))}"
        for i in range(400)
    ]
    cuerpo = pedir_informe(e["admin"], e["cons"], planilla(*filas)).json()
    assert cuerpo["aplicable"] is False
    assert {p["codigo"] for x in cuerpo["filas"] for p in x["problemas"]} == {
        "invitaciones_simultaneas"
    }
    assert max(len(x["problemas"]) for x in cuerpo["filas"]) == 1
    assert max(len(p["mensaje"]) for x in cuerpo["filas"] for p in x["problemas"]) < 200
    assert "Hay 400 vínculos" in cuerpo["filas"][0]["problemas"][0]["mensaje"]


def test_una_designacion_con_muchas_escrituras_se_informa_con_un_ejemplo(escena):
    e = escena
    escrituras = [f"23°{' ' * (i + 1)}A" for i in range(300)]
    archivo = planilla(*(f"{d},,,,," for d in escrituras))
    cuerpo = pedir_informe(e["admin"], e["cons"], archivo).json()
    assert {p["codigo"] for x in cuerpo["filas"] for p in x["problemas"]} == {"designacion_ambigua"}
    # Cada mensaje nombra su designación y un ejemplo: lineal en el tamaño de la planilla. Antes,
    # cada fila nombraba las otras 299 (unas 150 veces el tamaño de la planilla).
    assert largo_de_los_diagnosticos(cuerpo) < 4 * len(archivo)


def test_las_listas_de_coeficientes_son_completas(escena, motor_migrate):
    """El contrato de 7e28cd5: cada lista nombra todas las unidades (decisión del propietario
    del 23-10-2026, sin recortes ni totales)."""
    e = escena
    with motor_migrate.connect() as c:
        existentes = c.execute(
            text(
                "select count(*) from nucleo.unidad where consorcio_id = :c"
                " and eliminado_en is null"
            ),
            {"c": e["cons"]},
        ).scalar_one()
    filas = [f"24-{i},," for i in range(50)]
    encabezado = "unidad,coeficiente_agua,coeficiente_gas"
    cuerpo = pedir_informe(e["admin"], e["cons"], planilla(*filas, encabezado=encabezado)).json()
    assert [g["grupo"] for g in cuerpo["coeficientes"]] == ["agua", "gas"]
    for grupo in cuerpo["coeficientes"]:
        assert len(grupo["sin_coeficiente"]) == 50 + existentes
        assert grupo["sin_coeficiente"] == sorted(grupo["sin_coeficiente"])


def test_una_planilla_con_25_grupos_de_coeficientes_se_informa_y_se_aplica(escena):
    """Más de 20 grupos no es causa de rechazo (decisión del propietario del 23-10-2026)."""
    e = escena
    grupos = [f"g{i:02d}" for i in range(25)]
    encabezado = "unidad," + ",".join(f"coeficiente_{g}" for g in grupos)
    archivo = planilla(
        "25° A," + ",".join("1.5" for _ in grupos),
        "25° B," + ",".join("" for _ in grupos),
        encabezado=encabezado,
    )
    cuerpo = pedir_informe(e["admin"], e["cons"], archivo).json()
    assert cuerpo["aplicable"] is True and cuerpo["problemas_archivo"] == []
    assert [g["grupo"] for g in cuerpo["coeficientes"]] == grupos
    for g in cuerpo["coeficientes"]:
        assert g["suma"] == "1.5" and "25° B" in g["sin_coeficiente"]
    r = pedir_aplicar(e["admin"], e["cons"], archivo)
    assert r.status_code == 200, r.text
    assert r.json()["unidades_creadas"] == 2


def test_un_vinculo_existente_se_encuentra_entre_varios_sin_recorrerlos_todos(
    escena, motor_migrate
):
    """La búsqueda binaria sobre los vínculos existentes de la misma persona, unidad y carácter:
    igual, superpuesto con el del medio (también con una vigencia vacía adentro), entre dos y
    después del último."""
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()

    def fila(desde: int, ultimo: int | None) -> str:
        fin = f(hoy + timedelta(days=ultimo)) if ultimo is not None else ""
        return f"2° B,Ro,{correo},ocupante,{f(hoy + timedelta(days=desde))},{fin}"

    # Ya terminados: así no llevan invitación (tres vigentes o futuros serían simultáneos).
    aplicada = planilla(fila(-100, -97), fila(-90, -81), fila(-70, -61))
    r = pedir_aplicar(e["admin"], e["cons"], aplicada)
    assert r.status_code == 200, r.text
    with motor_migrate.begin() as c:
        vacia = c.execute(
            text(
                "insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo,"
                " vigente_desde, vigente_hasta, fuente, origen, finalizado_en, motivo_fin)"
                " select consorcio_id, unidad_id, persona_id, tipo, :d, :d, 'documento',"
                " 'soporte', now(), 'Prueba' from nucleo.vinculo where id = :v returning id"
            ),
            {"d": hoy - timedelta(days=85), "v": vinculos_de(motor_migrate, correo)[0]["id"]},
        ).scalar_one()
    casos = {
        fila(-90, -81): ("existente", set()),
        fila(-84, -84): (None, {"corrige_existente"}),  # dentro del del medio, tras la vacía
        fila(-88, -88): (None, {"corrige_existente"}),
        fila(-79, -72): ("crear", set()),  # entre dos
        fila(-65, None): (None, {"corrige_existente"}),
        fila(-50, None): ("crear", set()),  # después del último
    }
    try:
        for renglon, (vinculo, codigos) in casos.items():
            [x] = pedir_informe(e["admin"], e["cons"], planilla(renglon)).json()["filas"]
            assert (x["vinculo"], {p["codigo"] for p in x["problemas"]}) == (
                vinculo,
                codigos,
            ), renglon
    finally:
        # La vigencia vacía dentro de otro vínculo es solo de esta prueba: `vaciar_mandatos`
        # (las pruebas que bajan migraciones) la convertiría en un día que se superpone.
        with motor_migrate.begin() as c:
            c.execute(text("delete from nucleo.vinculo where id = :v"), {"v": vacia})


def test_cada_grupo_se_compara_con_su_propio_esquema_vigente(
    edificio,  # noqa: F811
    motor_migrate,
    motor_app,
):
    """Los coeficientes guardados se leen en una sola consulta para todos los grupos con
    esquema vigente: cada grupo tiene que compararse con el suyo."""
    e = edificio
    admin = otra_persona(motor_migrate, e["cons"])
    cambiar(motor_app, e["cons"], "administrador_aumentado", e["acta"])
    alta(motor_app, e["cons"], None, e["acta"], persona=admin, origen="soporte")
    registrar(motor_app, e["cons"], {str(e["u1"]): "60.000000", str(e["u2"]): "40.000000"},
              decision=e["acta"], grupo="expensas_comunes")  # fmt: skip
    registrar(motor_app, e["cons"], {str(e["u1"]): "30.000000", str(e["u2"]): "70.000000"},
              decision=e["acta"], grupo="agua")  # fmt: skip
    encabezado = "unidad,coeficiente_expensas_comunes,coeficiente_agua,coeficiente_gas"
    archivo = planilla("1° A,60,70,1", "2° B,40,70,2", encabezado=encabezado)
    grupos = {
        g["grupo"]: g for g in pedir_informe(admin, e["cons"], archivo).json()["coeficientes"]
    }
    assert grupos["expensas_comunes"]["distintos_del_vigente"] == []
    assert grupos["agua"]["distintos_del_vigente"] == ["1° A"]
    assert (grupos["gas"]["hay_esquema_vigente"], grupos["gas"]["distintos_del_vigente"]) == (
        False,
        [],
    )


# Dos P2 de Codex sobre 3735371 (barrido de la clase) --------------------------------------------


def test_muchas_variantes_de_una_designacion_se_validan_en_una_pasada_por_grupo(escena):
    """Codex en app #105: 8000 variantes de mayúsculas de la misma designación tardaban unos 8
    segundos, porque cada fila copiaba y recorría el grupo entero."""
    e = escena
    base = "abcdefghijklm"
    variantes = [
        "".join(c.upper() if (i >> k) & 1 else c for k, c in enumerate(base)) for i in range(8000)
    ]
    archivo = planilla(*(f"{d},,,,," for d in variantes))
    inicio = time.perf_counter()
    cuerpo = pedir_informe(e["admin"], e["cons"], archivo).json()
    assert time.perf_counter() - inicio < 3
    assert {p["codigo"] for x in cuerpo["filas"] for p in x["problemas"]} == {"designacion_ambigua"}
    # Cada fila tiene un ejemplo distinto de su propia grafía.
    for x in cuerpo["filas"]:
        assert f"«{x['designacion']}» es la misma unidad que «" in x["problemas"][0]["mensaje"]
        assert x["problemas"][0]["mensaje"].count(f"«{x['designacion']}»") == 1


def test_una_importacion_y_una_invitacion_manual_nueva_del_mismo_correo_no_se_traban(
    escena, motor_migrate
):
    """Codex en app #105. Sin pendiente previa, el bloqueo de filas no toma nada: la manual
    insertaba y después auditaba, la importación auditaba y después insertaba, y se esperaban
    por `invitacion_pendiente_unica` y por la cadena de auditoría. Ahora las dos toman
    `persona:<correo>` antes de insertar o auditar.

    Tres conexiones reales y un orden determinista: la primera retiene la cadena de auditoría del
    consorcio; la importación llega primera a esperarla y la invitación manual después. Al
    soltarla, la importación sigue primero."""
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    archivo = planilla(f"2° B,Noa,{correo},ocupante,{f(hoy)},")
    with motor_migrate.connect() as cadena:
        with cadena.begin():
            pid = cadena.execute(text("select pg_backend_pid()")).scalar_one()
            cadena.execute(
                text("select pg_advisory_xact_lock(hashtextextended('auditoria:' || :c, 0))"),
                {"c": str(e["cons"])},
            )
            hilo_i, importacion = en_un_hilo(lambda: pedir_aplicar(e["admin"], e["cons"], archivo))
            esperar_que_alguien_espere_un_bloqueo(pid)
            hilo_m, manual = en_un_hilo(
                lambda: invitar_manual(e["admin"], e["cons"], e["u2"], correo)
            )
            esperar_que_alguien_espere_un_bloqueo(pid, cuantos=2)
    hilo_i.join(60)
    hilo_m.join(60)
    assert "error" not in importacion, importacion.get("error")
    assert "error" not in manual, manual.get("error")
    assert importacion["respuesta"].status_code == 200, importacion["respuesta"].text
    assert importacion["respuesta"].json()["invitaciones_creadas"] == 1
    r = manual["respuesta"]
    assert (r.status_code, r.json()["codigo"]) == (409, "invitacion_importada_pendiente")
    [inv] = invitaciones_de(motor_migrate, correo)
    assert inv["vinculo_id"] is not None


# P2 de Codex sobre 3f9c49b: la segunda lectura de la invitación manual no bloquea ---------------


def importado_sin_invitacion(e, motor_migrate) -> tuple[str, dict]:
    """Un vínculo importado vigente cuya invitación se retiró: la clave queda sin pendientes."""
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    archivo = planilla(f"2° B,Paz,{correo},ocupante,{f(hoy)},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.invitacion set revocada_en = now() where correo = :c"),
            {"c": correo},
        )
    return correo, vinculos_de(motor_migrate, correo)[0]


def test_una_invitacion_manual_una_importada_nueva_y_su_aceptacion_no_se_traban(
    escena, motor_migrate, motor_app
):
    """Tres transacciones intercaladas, con conexiones reales y un orden determinista:
    - una conexión retiene `persona:<correo>`;
    - la invitación manual (la ruta) lee sus pendientes (ninguna) y espera la persona;
    - una importación confirma una invitación nueva de la misma clave que nombra su vínculo;
    - la aceptación real (`aceptar_invitacion_propia`) bloquea esa invitación y espera la
      persona.
    Al soltar la persona, la manual sigue primero. Si su segunda lectura bloqueara la invitación
    nueva, esperaría a la aceptación, que espera a la manual: deadlock."""
    e = escena
    correo, v = importado_sin_invitacion(e, motor_migrate)
    persona = persona_de(motor_migrate, correo)
    with motor_migrate.connect() as retencion:
        with retencion.begin():
            pid = retencion.execute(text("select pg_backend_pid()")).scalar_one()
            retencion.execute(
                text("select pg_advisory_xact_lock(hashtextextended('persona:' || :c, 0))"),
                {"c": correo},
            )
            hilo_m, manual = en_un_hilo(
                lambda: invitar_manual(e["admin"], e["cons"], e["u2"], correo)
            )
            esperar_que_alguien_espere_un_bloqueo(pid)
            with motor_migrate.begin() as c:  # la importación, ya confirmada
                nueva = c.execute(
                    text(
                        "insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo,"
                        " nombre, token_hash, invitada_por, vence_en, vinculo_id) values (:co, :u,"
                        " 'ocupante', :c, 'Paz', :h, :p, now() + interval '7 days', :v)"
                        " returning id"
                    ),
                    {
                        "co": e["cons"],
                        "u": e["u2"],
                        "c": correo,
                        "h": uuid4().bytes * 2,
                        "p": e["admin"],
                        "v": v["id"],
                    },  # fmt: skip
                ).scalar_one()

            def aceptar():
                with transaccion(motor_app, persona_id=persona) as c:
                    return c.execute(
                        text("select resultado from nucleo.aceptar_invitacion_propia(:i, null)"),
                        {"i": nueva},
                    ).scalar_one()

            hilo_a, aceptacion = en_un_hilo(aceptar)
            esperar_que_alguien_espere_un_bloqueo(pid, cuantos=2)
    hilo_m.join(60)
    hilo_a.join(60)
    assert "error" not in manual, manual.get("error")
    assert "error" not in aceptacion, aceptacion.get("error")
    r = manual["respuesta"]
    assert (r.status_code, r.json()["codigo"]) == (409, "invitacion_importada_pendiente")
    assert aceptacion["respuesta"] == "ok"


def test_una_invitacion_manual_que_aparece_durante_la_espera_no_se_retira_fuera_de_orden(
    escena, motor_migrate
):
    """Si mientras se espera la persona otra invitación manual de la misma clave se registra, no
    se la bloquea ni se la retira: 409 para que el pedido se repita, sin escribir nada."""
    e = escena
    correo = correo_nuevo()
    with motor_migrate.connect() as retencion:
        with retencion.begin():
            pid = retencion.execute(text("select pg_backend_pid()")).scalar_one()
            retencion.execute(
                text("select pg_advisory_xact_lock(hashtextextended('persona:' || :c, 0))"),
                {"c": correo},
            )
            hilo, manual = en_un_hilo(
                lambda: invitar_manual(e["admin"], e["cons"], e["u2"], correo)
            )
            esperar_que_alguien_espere_un_bloqueo(pid)
            otra = invitar_a_mano(motor_migrate, e["cons"], e["u2"], correo, vencida=False)
    hilo.join(60)
    assert "error" not in manual, manual.get("error")
    r = manual["respuesta"]
    assert (r.status_code, r.json()["codigo"]) == (409, "idempotencia_en_curso")
    [inv] = invitaciones_de(motor_migrate, correo)
    assert (inv["id"], inv["revocada_en"]) == (otra, None)
    # Control: repetido, ya bloquea la otra en orden y la reemplaza.
    assert invitar_manual(e["admin"], e["cons"], e["u2"], correo).status_code == 201
