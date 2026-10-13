from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from cem_contracts.consorcios import ConsorcioResumen
from cem_contracts.errores import CodigoError, Error
from cem_contracts.esquemas import como_texto

INSTANTANEA = Path(__file__).resolve().parent.parent / "esquemas.json"


def test_esquemas_coinciden_con_la_instantanea_aprobada():
    assert INSTANTANEA.read_text(encoding="utf-8") == como_texto(), (
        "El contrato cambió. Si el cambio es intencional, regenerar con "
        "`python -m cem_contracts.esquemas > esquemas.json` y revisarlo en el PR."
    )


def test_error_rechaza_codigos_desconocidos():
    with pytest.raises(ValidationError):
        Error(codigo="otro", mensaje="x", id_correlacion="c")
    assert Error(codigo=CodigoError.NO_ENCONTRADO, mensaje="x", id_correlacion="c")


def test_vinculo_desconocido_se_rechaza():
    with pytest.raises(ValidationError):
        ConsorcioResumen(id=uuid4(), nombre="Edificio", vinculos=["vecino"])
