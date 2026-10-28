"""Exporta el esquema JSON de cada modelo del contrato.

`esquemas.json` en la raíz es la instantánea aprobada; la prueba falla si el
código y la instantánea difieren. Para aprobar un cambio:
`python -m cem_contracts.esquemas > esquemas.json`.
"""

import json

from pydantic import BaseModel

from cem_contracts.consorcios import ConsorcioResumen, ListaUnidades, Unidad
from cem_contracts.contrasena import (
    CodigoEntrada,
    ContrasenaGuardada,
    EntradaContrasena,
    EstadoContrasena,
    FaltaSegundoFactor,
    NuevaContrasena,
    SesionAbierta,
)
from cem_contracts.errores import Error
from cem_contracts.identidad import (
    CanjeCodigo,
    CanjeEnlace,
    EnlacePedido,
    ListaSesiones,
    PedidoEnlace,
    SesionActual,
    SesionDispositivo,
    SesionesRevocadas,
)
from cem_contracts.importacion import (
    AnulacionVinculo,
    ImportacionAplicada,
    InformeImportacion,
    PlanillaPadron,
    VinculoAnulado,
)
from cem_contracts.invitaciones import (
    AceptacionInvitacion,
    Invitacion,
    InvitacionAceptada,
    InvitacionNueva,
    InvitacionRecibida,
    ListaInvitaciones,
    TokenInvitacion,
)
from cem_contracts.mandatos import (
    BajaMandato,
    CandidatoMandato,
    DecisionDetalle,
    DecisionNueva,
    DecisionRegistrada,
    DecisionResumen,
    ListaCandidatos,
    ListaDecisiones,
    ListaMandatos,
    MandatoDadoDeBaja,
    MandatoDetalle,
    MandatoNuevo,
    MandatoRegistrado,
    MandatoResumen,
)
from cem_contracts.mandatos_invitaciones import (
    AceptacionInvitacionMandato,
    Desasociacion,
    DesasociacionHecha,
    InvitacionMandato,
    InvitacionMandatoAceptada,
    InvitacionMandatoAprobada,
    InvitacionMandatoEmitida,
    InvitacionMandatoNueva,
    InvitacionMandatoRecibida,
    ListaInvitacionesMandato,
    TokenInvitacionMandato,
)
from cem_contracts.padron import (
    ConfirmacionVinculo,
    FilaPadron,
    FinVinculo,
    Padron,
    VinculoConfirmado,
    VinculoFinalizado,
)
from cem_contracts.privacidad import (
    CasoPrivacidad,
    ConfirmacionCaso,
    CopiaDatosPropios,
    ListaCasosPrivacidad,
    PedidoCopia,
)
from cem_contracts.salud import EstadoCola, Salud, SaludDependencias
from cem_contracts.segundo_factor import (
    AltaTotp,
    CodigosRecuperacion,
    CodigoTotp,
    EstadoSegundoFactor,
    SegundoFactorVerificado,
    VerificacionSegundoFactor,
)
from cem_contracts.soporte import SesionSoporte

MODELOS: tuple[type[BaseModel], ...] = (
    AceptacionInvitacion,
    AceptacionInvitacionMandato,
    AltaTotp,
    AnulacionVinculo,
    BajaMandato,
    CandidatoMandato,
    CanjeCodigo,
    CanjeEnlace,
    CodigoEntrada,
    CodigoTotp,
    CodigosRecuperacion,
    ConfirmacionVinculo,
    ConsorcioResumen,
    ContrasenaGuardada,
    DecisionDetalle,
    DecisionNueva,
    DecisionRegistrada,
    DecisionResumen,
    Desasociacion,
    DesasociacionHecha,
    EnlacePedido,
    EntradaContrasena,
    Error,
    EstadoCola,
    EstadoContrasena,
    EstadoSegundoFactor,
    FaltaSegundoFactor,
    FilaPadron,
    FinVinculo,
    ImportacionAplicada,
    InformeImportacion,
    Invitacion,
    InvitacionAceptada,
    InvitacionMandato,
    InvitacionMandatoAceptada,
    InvitacionMandatoAprobada,
    InvitacionMandatoEmitida,
    InvitacionMandatoNueva,
    InvitacionMandatoRecibida,
    InvitacionNueva,
    InvitacionRecibida,
    ListaCandidatos,
    ListaDecisiones,
    ListaInvitaciones,
    ListaInvitacionesMandato,
    ListaMandatos,
    ListaSesiones,
    ListaUnidades,
    MandatoDadoDeBaja,
    MandatoDetalle,
    MandatoNuevo,
    MandatoRegistrado,
    MandatoResumen,
    NuevaContrasena,
    Padron,
    PlanillaPadron,
    PedidoEnlace,
    Salud,
    SaludDependencias,
    SegundoFactorVerificado,
    SesionAbierta,
    SesionActual,
    SesionDispositivo,
    SesionSoporte,
    SesionesRevocadas,
    TokenInvitacion,
    TokenInvitacionMandato,
    Unidad,
    VerificacionSegundoFactor,
    VinculoAnulado,
    VinculoConfirmado,
    VinculoFinalizado,
    CasoPrivacidad,
    ConfirmacionCaso,
    CopiaDatosPropios,
    ListaCasosPrivacidad,
    PedidoCopia,
)


def esquemas() -> dict[str, object]:
    return {m.__name__: m.model_json_schema() for m in MODELOS}


def como_texto() -> str:
    return json.dumps(esquemas(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


if __name__ == "__main__":
    print(como_texto(), end="")
