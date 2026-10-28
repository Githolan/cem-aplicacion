"""Caminos técnicos A y B para entrar en Modo Autogestión, con los scripts del equipo.

Preparación de Autogestión (CEM `doc/diseno/Autogestion_Preparacion_2026-10-28.md`, §2.1 y §3) y
decisión del propietario del 28-10-2026: probar los dos caminos sin elegir ninguno para el piloto.
Todo es sintético: un consorcio, personas y actas de prueba. Ninguna prueba activa un consorcio
real.

- **Camino A:** un solo `alta_mandato` con decisión, `modo_inicial: autogestion` y el mandato del
  administrador propietario, con la fecha del acta.
- **Camino B:** un primer `alta_mandato` en Consejo y, con otra decisión de asamblea, un segundo
  con `modo_inicial: autogestion` desde hoy y el administrador.
- **La secuencia completa:** `alta_consorcio`, `invitar` y la aceptación por la API, y después el
  arranque.

Cada negación lleva su control, y una negación no deja nada escrito (la transacción del script se
deshace entera).
"""

import json
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from scripts import alta_consorcio, alta_mandato
from scripts import invitar as script_invitar
from tests.conftest import url_rol
from tests.test_identidad import ORIGEN, app, navegador  # noqa: F401  (`app` es un fixture)
from tests.test_invitaciones import ACEPTAR, correo_nuevo, token_invitacion
from tests.test_mandatos import otra_persona
from tests.test_modo_operacion import consorcio_con_persona

pytestmark = pytest.mark.db

ACTA_1 = "Acta 1 (sintética), libro 1, folio 1"
ACTA_2 = "Acta 2 (sintética), libro 1, folio 2"
CUIT_EXTERNO = "20-12345678-6"


@pytest.fixture
def correr(tmp_path, monkeypatch, capsys):
    """Corre `alta_mandato` con un archivo, como `cem_app` y origen soporte (el equipo)."""
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))

    def _correr(datos: dict, *extra: str) -> tuple[int, str, str]:
        ruta = tmp_path / f"{uuid4().hex}.json"
        ruta.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
        codigo = alta_mandato.main(["--archivo", str(ruta), *extra])
        salida = capsys.readouterr()
        return codigo, salida.out, salida.err

    return _correr


def hoy(motor_migrate, cons: UUID) -> date:
    with motor_migrate.connect() as c:
        return c.execute(text("select nucleo.hoy_del_consorcio(:c)"), {"c": cons}).scalar_one()


def arranque(cons, acta: str, fecha: date, modo: str, desde: date, mandatos: list[dict]) -> dict:
    return {
        "consorcio_id": str(cons),
        "decision": {"tipo": "asamblea", "fecha": fecha.isoformat(),
                     "descripcion": "Decisión sintética de prueba", "referencia_acta": acta},
        "modo_inicial": {"modo": modo, "desde": desde.isoformat()},
        "mandatos": mandatos,
    }  # fmt: skip


def administrador(persona: UUID, desde: date) -> dict:
    return {"tipo": "administrador", "persona_id": str(persona), "desde": desde.isoformat()}


def consejero(persona: UUID, desde: date) -> dict:
    return {"tipo": "miembro_consejo", "persona_id": str(persona), "desde": desde.isoformat()}


def externo(desde: date, hasta: date | None = None) -> dict:
    m = {"tipo": "administrador_externo", "mandatario_nombre": "Administración sintética",
         "mandatario_cuit": CUIT_EXTERNO, "desde": desde.isoformat()}  # fmt: skip
    if hasta is not None:
        m["hasta"] = hasta.isoformat()
    return m


def periodos(motor_migrate, cons) -> list[tuple[str, date, date | None]]:
    with motor_migrate.connect() as c:
        return [
            tuple(f)
            for f in c.execute(
                text(
                    "select modo::text, vigente_desde, vigente_hasta from nucleo.modo_operacion"
                    " where consorcio_id = :c order by vigente_desde"
                ),
                {"c": cons},
            )
        ]


def filas(motor_migrate, cons) -> tuple[int, int, int]:
    """Decisiones, períodos de modo y mandatos del consorcio."""
    with motor_migrate.connect() as c:
        return c.execute(
            text(
                "select (select count(*) from nucleo.decision_formal where consorcio_id = :c),"
                " (select count(*) from nucleo.modo_operacion where consorcio_id = :c),"
                " (select count(*) from nucleo.mandato where consorcio_id = :c)"
            ),
            {"c": cons},
        ).one()


def mandato_administrador(motor_migrate, cons) -> dict:
    with motor_migrate.connect() as c:
        m = (
            c.execute(
                text(
                    "select id, persona_id, vigente_desde, origen from nucleo.mandato"
                    " where consorcio_id = :c and tipo = 'administrador'"
                ),
                {"c": cons},
            )
            .mappings()
            .one()
        )
        facultades = set(
            c.execute(
                text("select facultad from nucleo.facultad_otorgada where mandato_id = :m"),
                {"m": m["id"]},
            ).scalars()
        )
        paquete = set(
            c.execute(
                text("select facultad from nucleo.paquete_facultad where tipo = 'administrador'")
            ).scalars()
        )
    return dict(m) | {"facultades": facultades, "paquete": paquete}


def negado(r: tuple[int, str, str], motivo: str) -> None:
    codigo, _, err = r
    assert codigo == 1, err
    assert "no se escribió nada" in err and motivo in err, err


# Camino A ------------------------------------------------------------------------------------


def test_camino_a_primer_modo_autogestion_con_el_administrador(correr, motor_migrate):
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = hoy(motor_migrate, cons) - timedelta(days=10)
    datos = arranque(cons, ACTA_1, acta, "autogestion", acta, [administrador(prop, acta)])
    codigo, out, _ = correr(datos, "--simular")
    assert codigo == 0 and out.startswith("SIMULACIÓN")
    assert filas(motor_migrate, cons) == (0, 0, 0)
    codigo, out, err = correr(datos)
    assert codigo == 0, err
    assert ACTA_1 in out and "autogestion" in out
    assert periodos(motor_migrate, cons) == [("autogestion", acta, None)]
    m = mandato_administrador(motor_migrate, cons)
    assert (m["persona_id"], m["vigente_desde"], m["origen"]) == (prop, acta, "soporte")
    # El paquete entero del administrador, y ninguna facultad de aprobar dinero (decisión 7).
    assert m["facultades"] == m["paquete"]
    assert "egresos.preparar_orden" in m["facultades"]
    assert (
        not {"egresos.aprobar_orden", "liquidacion.aprobar", "coeficientes.aprobar"}
        & m["facultades"]
    )


@pytest.mark.parametrize(
    ("ajuste", "motivo"),
    [
        # El modo empieza antes de su decisión.
        (lambda d, a: d["modo_inicial"].update(desde=(a - timedelta(days=1)).isoformat()),
         "fecha_invalida"),
        # El modo empieza en el futuro.
        (lambda d, a: d["modo_inicial"].update(desde=(a + timedelta(days=30)).isoformat()),
         "fecha_invalida"),
    ],
    ids=["antes_de_la_decision", "futuro"],
)  # fmt: skip
def test_camino_a_negativos_no_dejan_nada(correr, motor_migrate, ajuste, motivo):
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = hoy(motor_migrate, cons) - timedelta(days=10)
    datos = arranque(cons, ACTA_1, acta, "autogestion", acta, [administrador(prop, acta)])
    ajuste(datos, acta)
    negado(correr(datos), motivo)
    assert filas(motor_migrate, cons) == (0, 0, 0)
    # Control: el mismo arranque sin el defecto pasa.
    limpio = arranque(cons, ACTA_1, acta, "autogestion", acta, [administrador(prop, acta)])
    assert correr(limpio)[0] == 0


def test_camino_a_dos_administradores_a_la_vez_se_niega(correr, motor_migrate):
    """La administración es única (0012): un segundo administrador en el mismo arranque."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = hoy(motor_migrate, cons) - timedelta(days=10)
    otra = otra_persona(motor_migrate, cons)
    datos = arranque(cons, ACTA_1, acta, "autogestion", acta,
                     [administrador(prop, acta), administrador(otra, acta)])  # fmt: skip
    codigo, _, err = correr(datos)
    assert codigo == 1 and "no se escribió nada" in err, err
    assert filas(motor_migrate, cons) == (0, 0, 0)


def test_camino_a_no_vuelve_a_consejo(correr, motor_migrate):
    """AG → Consejo no está en el diagrama 12."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    h = hoy(motor_migrate, cons)
    acta = h - timedelta(days=10)
    inicial = arranque(cons, ACTA_1, acta, "autogestion", acta, [administrador(prop, acta)])
    assert correr(inicial)[0] == 0
    otra = otra_persona(motor_migrate, cons)
    negado(correr(arranque(cons, ACTA_2, h, "consejo", h, [consejero(otra, h)])), "diagrama 12")
    assert periodos(motor_migrate, cons) == [("autogestion", acta, None)]
    assert filas(motor_migrate, cons) == (1, 1, 1)


# Camino B ------------------------------------------------------------------------------------


def en_consejo_desde(correr, motor_migrate, dias: int, extra=lambda h: []):
    """Un consorcio en Consejo desde hace `dias`, con un consejero; `extra(hoy)` agrega mandatos."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    h = hoy(motor_migrate, cons)
    inicio = h - timedelta(days=dias)
    otra = otra_persona(motor_migrate, cons)
    datos = arranque(cons, ACTA_1, inicio, "consejo", inicio,
                     [consejero(otra, inicio), *extra(h)])  # fmt: skip
    codigo, _, err = correr(datos)
    assert codigo == 0, err
    return cons, prop, h, inicio


def segundo_tiempo(cons, prop, h) -> dict:
    return arranque(cons, ACTA_2, h, "autogestion", h, [administrador(prop, h)])


def test_camino_b_consejo_y_despues_autogestion_desde_hoy(correr, motor_migrate):
    cons, prop, h, inicio = en_consejo_desde(correr, motor_migrate, 30)
    assert filas(motor_migrate, cons) == (1, 1, 1)
    codigo, _, err = correr(segundo_tiempo(cons, prop, h), "--simular")
    assert codigo == 0, err
    assert filas(motor_migrate, cons) == (1, 1, 1)
    codigo, out, err = correr(segundo_tiempo(cons, prop, h))
    assert codigo == 0, err
    assert ACTA_2 in out
    # La historia queda completa: Consejo cerrado hoy y Autogestión desde hoy, cada uno con su
    # decisión.
    assert periodos(motor_migrate, cons) == [("consejo", inicio, h), ("autogestion", h, None)]
    with motor_migrate.connect() as c:
        decisiones = c.execute(
            text(
                "select count(distinct decision_id) from nucleo.modo_operacion"
                " where consorcio_id = :c"
            ),
            {"c": cons},
        ).scalar_one()
    assert decisiones == 2
    m = mandato_administrador(motor_migrate, cons)
    assert (m["persona_id"], m["vigente_desde"]) == (prop, h)
    assert m["facultades"] == m["paquete"]


def test_camino_b_la_misma_decision_no_respalda_dos_modos(motor_migrate, motor_app, correr):
    """El script registra siempre una decisión nueva; reutilizar la primera solo se puede por la
    función, y se niega (`decision_ya_usada`)."""
    from app.db import transaccion  # noqa: PLC0415

    cons, prop, h, _ = en_consejo_desde(correr, motor_migrate, 30)
    with motor_migrate.connect() as c:
        primera = c.execute(
            text("select decision_id from nucleo.modo_operacion where consorcio_id = :c"),
            {"c": cons},
        ).scalar_one()
    with pytest.raises(Exception, match="decision_ya_usada"):
        with transaccion(motor_app, persona_id=None, consorcio_id=cons) as c:
            c.execute(
                text("select nucleo.cambiar_modo('autogestion', :d, :h, null, 'soporte')"),
                {"d": primera, "h": h},
            )
    assert len(periodos(motor_migrate, cons)) == 1
    # Control: con su propia decisión (el segundo tiempo del script), pasa.
    assert correr(segundo_tiempo(cons, prop, h))[0] == 0


def test_camino_b_consejo_iniciado_hoy_no_cambia_el_mismo_dia(correr, motor_migrate):
    cons, prop, h, _ = en_consejo_desde(correr, motor_migrate, 0)
    negado(correr(segundo_tiempo(cons, prop, h)), "fecha_invalida")
    assert filas(motor_migrate, cons) == (1, 1, 1)


def test_camino_b_con_administrador_externo_vigente_se_niega(correr, motor_migrate):
    cons, prop, h, _ = en_consejo_desde(
        correr, motor_migrate, 30, extra=lambda h: [externo(h - timedelta(days=30))]
    )
    negado(correr(segundo_tiempo(cons, prop, h)), "transicion_requiere_fin_de_mandato")
    assert filas(motor_migrate, cons) == (1, 1, 2)


def test_camino_b_con_administrador_externo_futuro_se_niega(correr, motor_migrate):
    cons, prop, h, _ = en_consejo_desde(
        correr, motor_migrate, 30, extra=lambda h: [externo(h + timedelta(days=15))]
    )
    negado(correr(segundo_tiempo(cons, prop, h)), "transicion_requiere_fin_de_mandato")
    assert filas(motor_migrate, cons) == (1, 1, 2)


def test_camino_b_con_un_externo_que_ya_termino_pasa(correr, motor_migrate):
    """Control de los dos anteriores: un externo cuyo mandato terminó antes de hoy no bloquea."""
    cons, prop, h, _ = en_consejo_desde(
        correr, motor_migrate, 30,
        extra=lambda h: [externo(h - timedelta(days=30), hasta=h - timedelta(days=1))],
    )  # fmt: skip
    codigo, _, err = correr(segundo_tiempo(cons, prop, h))
    assert codigo == 0, err


def test_camino_b_decision_posterior_al_modo_se_niega(correr, motor_migrate):
    """Una decisión fechada después del día en que rige el cambio no lo respalda."""
    cons, prop, h, _ = en_consejo_desde(correr, motor_migrate, 30)
    datos = segundo_tiempo(cons, prop, h)
    datos["decision"]["fecha"] = (h + timedelta(days=1)).isoformat()
    codigo, _, err = correr(datos)
    assert codigo != 0, err
    assert filas(motor_migrate, cons) == (1, 1, 1)


def test_camino_b_mandato_incoherente_se_niega(correr, motor_migrate):
    """El administrador del segundo tiempo con un fin anterior a su inicio."""
    cons, prop, h, _ = en_consejo_desde(correr, motor_migrate, 30)
    datos = segundo_tiempo(cons, prop, h)
    datos["mandatos"][0]["hasta"] = (h - timedelta(days=1)).isoformat()
    codigo, _, err = correr(datos)
    assert codigo != 0, err
    assert filas(motor_migrate, cons) == (1, 1, 1)


# La secuencia completa -----------------------------------------------------------------------


@pytest.mark.redis
def test_secuencia_alta_invitar_aceptar_y_camino_a(
    app,  # noqa: F811
    correr,
    motor_migrate,
    monkeypatch,
    tmp_path,
    capsys,
):
    """Lo que corre el equipo con el sí del propietario (§3), con datos sintéticos: alta del
    consorcio, invitación al propietario designado, aceptación por la API y el arranque."""
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", ORIGEN)
    cid = uuid4()
    edificio = {
        "consorcio": {"id": str(cid), "nombre": "Edificio sintético de Autogestión",
                      "zona_horaria": "America/Argentina/Buenos_Aires"},
        "unidades": [{"designacion": "PB"}, {"designacion": "1° A"}],
    }  # fmt: skip
    ruta = tmp_path / "edificio.json"
    ruta.write_text(json.dumps(edificio, ensure_ascii=False), encoding="utf-8")
    assert alta_consorcio.main(["--archivo", str(ruta)]) == 0
    capsys.readouterr()
    with motor_migrate.connect() as c:
        unidad = c.execute(
            text("select id from nucleo.unidad where consorcio_id = :c and designacion = 'PB'"),
            {"c": cid},
        ).scalar_one()
    correo = correo_nuevo()
    invitacion = ["--consorcio", str(cid), "--unidad", str(unidad), "--tipo", "propietario",
                  "--correo", correo, "--nombre", "Dueña"]  # fmt: skip
    assert script_invitar.main(invitacion) == 0
    capsys.readouterr()
    r = navegador(app).post(ACEPTAR, json={"token": token_invitacion(motor_migrate, correo)})
    assert r.status_code == 200, r.text
    with motor_migrate.connect() as c:
        persona = c.execute(
            text("select id from nucleo.persona where correo = :c"), {"c": correo}
        ).scalar_one()
    acta = hoy(motor_migrate, cid) - timedelta(days=2)
    codigo, _, err = correr(
        arranque(cid, ACTA_1, acta, "autogestion", acta, [administrador(persona, acta)])
    )
    assert codigo == 0, err
    assert periodos(motor_migrate, cid) == [("autogestion", acta, None)]
    assert mandato_administrador(motor_migrate, cid)["persona_id"] == persona


# Pendientes de una decisión del propietario --------------------------------------------------


@pytest.mark.skip(
    reason="espera la decisión del propietario sobre el RPA (§3 del documento de RPA)"
)
def test_rpa_un_administrador_en_autogestion_sin_matricula_se_niega():
    raise AssertionError("se escribe con la decisión")


@pytest.mark.skip(reason="espera la decisión del propietario: administrador propietario (§4)")
def test_un_administrador_en_autogestion_sin_vinculo_propietario_se_niega():
    raise AssertionError("se escribe con la decisión")
