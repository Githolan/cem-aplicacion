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

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.consorcios.importacion import leer_planilla
from app.db import transaccion
from scripts import anular_vinculo as script_anular
from tests.conftest import clave_idempotencia, url_rol
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


@pytest.mark.parametrize("caso", ["anulado", "otro_caracter", "otra_unidad"])
def test_si_su_vinculo_ya_no_corresponde_la_aceptacion_falla_explicita(escena, motor_migrate, caso):
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    futuro = hoy + timedelta(days=20)
    archivo = planilla(f"2° B,Pia,{correo},ocupante,{f(futuro)},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    [v] = vinculos_de(motor_migrate, correo)
    [inv] = invitaciones_de(motor_migrate, correo)
    if caso == "anulado":
        with cliente(e["admin"]) as c:
            r = c.post(
                f"/api/v1/consorcios/{e['cons']}/vinculos/{v['id']}/anular",
                json={"motivo": "Cargado por error"},
                headers=clave_idempotencia(),
            )
        assert r.status_code == 200, r.text
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
    cambiar(motor_app, e["cons"], e["prop1"], "administrador_aumentado", e["acta"])
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
    escena, motor_migrate, monkeypatch
):
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
    r = anular(e["admin"], e["cons"], v["id"])
    assert (r.status_code, r.json()["codigo"]) == (409, "vinculo_confirmado")
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))
    base = ["--consorcio", str(e["cons"]), "--vinculo", str(v["id"]), "--motivo", "Error"]
    assert script_anular.main(base + ["--referencia", "  "]) == 1  # sin respaldo, no
    assert script_anular.main(base + ["--referencia", "Escritura corregida 815"]) == 0
    [ev] = [x for x in eventos(motor_migrate, e["cons"], "consorcio.vinculo.anular")
            if x["entidad_id"] == v["id"]]  # fmt: skip
    assert (ev["origen"], ev["actor_persona_id"]) == ("soporte", None)
    assert ev["despues"] == {"referencia": "Escritura corregida 815"}
    assert ev["antes"]["confirmado"] is True


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
