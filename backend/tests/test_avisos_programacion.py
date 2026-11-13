"""Cuándo sale el aviso de pendientes (pregunta 7 del propietario, 13-11-2026), sin base: la hora y
el tope salen de la configuración. Contra `3c2d638` no existen `CEM_AVISOS_*` ni `avisos_pagos`."""

from collections import Counter
from datetime import UTC, datetime, time, timedelta

import pytest

from app import config as configuracion
from app.config import Config, cargar
from app.consorcios import avisos_pagos
from app.plataforma.planificador import TareaPeriodica

BASE = Config("", "0.1.0", "sha", "sha", "memory://")


def con(**cambios) -> Config:
    return Config(**{**BASE.__dict__, **cambios})


def test_por_omision_una_vez_por_dia_a_las_12_utc():
    c = cargar()
    assert (c.avisos_hora_utc, c.avisos_tope_diario) == (time(12, 0), 1)
    assert (avisos_pagos.periodo(1), avisos_pagos.desfase(c)) == (timedelta(days=1),
                                                                  timedelta(hours=12))  # fmt: skip


def test_la_hora_y_el_tope_salen_del_entorno(monkeypatch):
    monkeypatch.setenv("CEM_AVISOS_HORA_UTC", "10:30")
    monkeypatch.setenv("CEM_AVISOS_TOPE_DIARIO", "2")
    c = cargar()
    assert (c.avisos_hora_utc, c.avisos_tope_diario) == (time(10, 30), 2)
    # Dos corridas por día: 10:30 y 22:30.
    tarea = TareaPeriodica(avisos_pagos.TIPO, avisos_pagos.periodo(2), avisos_pagos.desfase(c))
    dia = datetime(2026, 11, 13, tzinfo=UTC)
    ventanas = {tarea.ventana(dia + timedelta(minutes=m)) for m in range(0, 24 * 60, 15)}
    assert {v.time() for v in ventanas} == {time(10, 30), time(22, 30)}


@pytest.mark.parametrize("valor", ["9", "24:00", "12:60", "mediodía", " 12:00"])
def test_una_hora_mal_escrita_impide_el_arranque(monkeypatch, valor):
    monkeypatch.setenv("CEM_AVISOS_HORA_UTC", valor)
    with pytest.raises(ValueError):
        cargar()


def test_un_tope_negativo_impide_el_arranque(monkeypatch):
    monkeypatch.setenv("CEM_AVISOS_TOPE_DIARIO", "-1")
    with pytest.raises(ValueError):
        cargar()


def test_con_tope_cero_no_se_programa(monkeypatch):
    tareas: list = []
    monkeypatch.setattr(avisos_pagos, "periodica", lambda *a, **k: tareas.append(a))
    avisos_pagos.programar(con(avisos_tope_diario=0))
    assert tareas == []
    # Control: con 1 se programa una.
    avisos_pagos.programar(con(avisos_tope_diario=1))
    assert [t[0] for t in tareas] == [avisos_pagos.TIPO]


def test_el_texto_no_tutea_y_dice_cada_edificio():
    aviso = avisos_pagos.Aviso("x@ejemplo.com")
    aviso.pendientes["Torre Norte"] = Counter({"aprobar": 2, "conciliar": 1})
    aviso.pendientes["Prueba"] = Counter({"pagar": 1})
    texto = avisos_pagos.texto(aviso, "https://cem.ejemplo.com/pagos", 1)
    assert "- Prueba: un pago para hacer.\n" in texto
    assert "- Torre Norte: 2 órdenes esperan su aprobación y un pago para conciliar." in texto
    assert "como máximo una vez por día" in texto


def test_el_valor_por_omision_es_el_documentado():
    assert configuracion.AVISOS_HORA_UTC == time(12, 0)
    assert configuracion.AVISOS_TOPE_DIARIO == 1
