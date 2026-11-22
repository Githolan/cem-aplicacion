"""Evaluador puro de permisos (tabla de verdad, §1 y §2; plan de implementación, PR 1).

Complementa la tabla de verdad (`test_tabla_de_verdad.py`) con las propiedades que la tabla
no puede expresar fila por fila: que ningún filtro concede, que toda económica se niega, que
la carga de políticas falla fuerte, y los controles de cada regla.

Por omisión `YO` y `OTRO` son dos seres humanos verificados en el consorcio (A13-C) y la
sesión tiene el segundo factor reciente y menos de 12 horas (R10).
"""

import json
from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest
from cem_contracts.estados import ModoOperacion, TipoVinculo
from cem_contracts.politicas import CodigoSeparacion, Politica, TipoRecurso
from pydantic import ValidationError

from app.autorizacion.evaluador import (
    CAUSA_ERROR_CARGA,
    ContextoAutorizacion,
    Entidad,
    Facultad,
    Mandato,
    Vinculo,
    alcanza,
    evaluar,
    por_otra_cuenta,
)
from app.autorizacion.politicas import POLITICAS, PoliticasInvalidas, cargar
from app.autorizacion.requiere import YA_PARTICIPO, YA_PARTICIPO_OTRA_CUENTA
from tests.escenarios_autorizacion import CATALOGO, CONSORCIO, HOY, OTRO_CONSORCIO, SIMBOLOS

YO = UUID("0192f0c4-0000-7000-8000-000000000001")
OTRO = UUID("0192f0c4-0000-7000-8000-000000000002")
U3A = UUID(SIMBOLOS["3A"])
MANDATO = UUID(int=1)
IDENTIDADES = {YO: UUID(int=101), OTRO: UUID(int=102)}


def mandato(consorcio: UUID = CONSORCIO, tipo: str | None = None) -> Mandato:
    return Mandato(id=MANDATO, consorcio_id=consorcio, vigente_desde=date(2026, 1, 1), tipo=tipo)


def contexto(
    *,
    vinculos: tuple[Vinculo, ...] = (),
    facultades: tuple[str, ...] | None = None,
    modo: ModoOperacion | None = ModoOperacion.CONSEJO,
    consorcio_mandato: UUID = CONSORCIO,
    administrador: bool = True,
    persona: UUID | None = YO,
    identidades: dict[UUID, UUID] = IDENTIDADES,
    segundo_factor_reciente: bool = True,
    tipo_mandato: str | None = None,
    roles_configurados: dict[str, frozenset[str]] | None = None,
) -> ContextoAutorizacion:
    """`facultades=None`: sin mandato; `()`: un mandato vigente sin ninguna facultad."""
    mandatos = () if facultades is None else (mandato(consorcio_mandato, tipo_mandato),)
    return ContextoAutorizacion(
        persona_id=persona,
        consorcio_id=CONSORCIO,
        hoy=HOY,
        vinculos=vinculos,
        modo=modo,
        mandatos=mandatos,
        facultades=tuple(Facultad(c, None, MANDATO) for c in facultades or ()),
        hay_administrador_vigente=administrador,
        identidades=identidades,
        segundo_factor_reciente=segundo_factor_reciente,
        roles_configurados=roles_configurados or {},
    )


def P(unidad: UUID = U3A) -> Vinculo:
    return Vinculo(TipoVinculo.PROPIETARIO, unidad)


def I(unidad: UUID = U3A) -> Vinculo:  # noqa: E743
    return Vinculo(TipoVinculo.INQUILINO, unidad)


def O(unidad: UUID = U3A) -> Vinculo:  # noqa: E743
    return Vinculo(TipoVinculo.OCUPANTE, unidad)


# Entidad en la que todos los filtros pasan: otra persona preparó, aprobó, solicitó, propuso,
# registró y emitió; sin firmas previas; con respaldo; recursos de todos los tipos.
TODO_PASA = Entidad(
    recursos={
        TipoRecurso.UNIDAD: str(U3A),
        TipoRecurso.CUENTA: SIMBOLOS["X"],
        TipoRecurso.GRUPO: "agua",
    },
    preparado_por=OTRO,
    aprobado_por=(OTRO,),
    solicitado_por=OTRO,
    propuesto_por=OTRO,
    persona_objetivo=OTRO,
    registrado_por=OTRO,
    emitido_por=OTRO,
    ejecutado_por=OTRO,
    decision_registrada_por=OTRO,
    respaldo_presente=True,
)


def modo_que_pasa(p: Politica) -> ModoOperacion | None:
    return p.modos[0] if p.modos else None


# --- Ningún filtro concede (criterio de aceptación del PR 1) -------------------------------


@pytest.mark.parametrize(
    "accion", sorted(a for a, p in POLITICAS.items() if not p.concede_por_ambito)
)
def test_ningun_filtro_concede(accion):
    p = POLITICAS[accion]
    # En el ámbito por un mandato vigente sin facultades y sin vínculo: no hay concesión.
    ctx = contexto(facultades=(), modo=modo_que_pasa(p))
    d = evaluar(accion, ctx, TODO_PASA)
    assert (d.permitido, d.estado, d.motivo_interno) == (False, 403, "sin_concesion")


@pytest.mark.parametrize("accion", sorted(a for a, p in POLITICAS.items() if p.facultad))
def test_control_con_su_facultad_la_concesion_pasa(accion):
    # Control de la prueba anterior: con la facultad exacta, lo que decide son los filtros.
    p = POLITICAS[accion]
    d = evaluar(accion, contexto(facultades=(p.facultad,), modo=modo_que_pasa(p)), TODO_PASA)
    assert d.motivo_interno == "concedido_por_facultad"


@pytest.mark.parametrize("accion", sorted(a for a, p in POLITICAS.items() if p.economica))
def test_toda_economica_exige_identidad_y_los_controles_de_r10(accion):
    todas = tuple(CATALOGO)
    modo = ModoOperacion.AUTOGESTION
    # La que se concede por configuración (`egresos.saltear_documentacion`, 0106): un mandato del
    # Consejo, con la configuración que lo lista.
    por_configuracion = POLITICAS[accion].concede_por_configuracion
    extra = (
        {"tipo_mandato": "miembro_consejo",
         "roles_configurados": {accion: frozenset({"miembro_consejo"})}}
        if por_configuracion
        else {}
    )  # fmt: skip
    casos = {
        "identidad_no_verificada": contexto(
            facultades=todas, modo=modo, identidades={OTRO: IDENTIDADES[OTRO]}, **extra
        ),
        "requiere_segundo_factor_reciente": contexto(
            facultades=todas, modo=modo, segundo_factor_reciente=False, **extra
        ),
    }
    for motivo, ctx in casos.items():
        d = evaluar(accion, ctx, TODO_PASA)
        assert (d.estado, d.motivo_interno) == (403, motivo)
    # Control: con todo, pasa.
    d = evaluar(accion, contexto(facultades=todas, modo=modo, **extra), TODO_PASA)
    concedido = "concedido_por_configuracion" if por_configuracion else "concedido_por_facultad"
    assert (d.estado, d.motivo_interno) == (200, concedido)


def test_r10_no_alcanza_a_las_que_no_son_economicas():
    p = POLITICAS["fondo_reserva.solicitar"]
    assert not p.economica
    ctx = contexto(
        facultades=(p.facultad,), modo=modo_que_pasa(p), identidades={},
        segundo_factor_reciente=False,
    )  # fmt: skip
    assert evaluar("fondo_reserva.solicitar", ctx, TODO_PASA).motivo_interno == (
        "concedido_por_facultad"
    )


def test_modo_cambiar_no_lo_concede_nadie():
    todas = tuple(CATALOGO)
    for ctx in (
        contexto(vinculos=(P(), I(), O()), facultades=todas),
        contexto(vinculos=(P(),), facultades=todas, modo=None),
    ):
        d = evaluar("modo.cambiar", ctx, TODO_PASA)
        assert (d.estado, d.motivo_interno) == (403, "sin_concesion")


# --- Ámbito y aislamiento (R2, R11) --------------------------------------------------------


def test_sin_vinculo_ni_mandato_es_404_y_con_mandato_entra():
    assert evaluar("unidades.listar", contexto(), TODO_PASA).estado == 404
    assert evaluar("unidades.listar", contexto(facultades=()), TODO_PASA).estado == 200


def test_facultad_de_otro_consorcio_no_cuenta():
    otro = contexto(vinculos=(P(),), facultades=("padron.ver",), consorcio_mandato=OTRO_CONSORCIO)
    assert evaluar("padron.ver", otro, TODO_PASA).motivo_interno == "sin_concesion"
    # Sin vínculo, el mandato de otro consorcio ni siquiera da ámbito.
    solo = contexto(facultades=("padron.ver",), consorcio_mandato=OTRO_CONSORCIO)
    assert evaluar("padron.ver", solo, TODO_PASA).estado == 404
    # Control: el mismo mandato en este consorcio concede.
    aca = contexto(vinculos=(P(),), facultades=("padron.ver",))
    assert evaluar("padron.ver", aca, TODO_PASA).motivo_interno == "concedido_por_facultad"


def test_facultad_de_un_mandato_que_no_esta_en_el_contexto_no_cuenta():
    ctx = contexto(vinculos=(P(),), facultades=())
    huerfana = Facultad("padron.ver", None, UUID(int=99))
    ctx = replace(ctx, facultades=(huerfana,))
    assert evaluar("padron.ver", ctx, TODO_PASA).motivo_interno == "sin_concesion"


def test_accion_no_declarada_se_niega():
    d = evaluar("padron.inventada", contexto(vinculos=(P(),)), TODO_PASA)
    assert (d.estado, d.motivo_interno) == (403, "accion_no_declarada")


# --- Concesión por ámbito (unidades.listar) -------------------------------------------------


def test_unidades_listar_por_ambito_y_control_sin_la_declaracion():
    ad_sin_vinculo = contexto(facultades=("documentos.cargar",))
    d = evaluar("unidades.listar", ad_sin_vinculo, Entidad())
    assert (d.estado, d.motivo_interno) == (200, "concedido_por_ambito")
    politicas = dict(POLITICAS)
    politicas["unidades.listar"] = POLITICAS["unidades.listar"].model_copy(
        update={"concede_por_ambito": False}
    )
    d = evaluar("unidades.listar", ad_sin_vinculo, Entidad(), politicas)
    assert (d.estado, d.motivo_interno) == (403, "sin_concesion")


def test_solo_unidades_listar_los_eventos_propios_y_los_contactos_conceden_por_ambito():
    # RF35-B (2b-6c): cada persona lee sus propios eventos en el consorcio. 2b-7: los contactos
    # de emergencia publicados, que no son personales (decisión del propietario del 25-10).
    # §10.5: lo que la interfaz muestra a cada persona, que no concede nada (05-11-2026).
    # RF49 (0108): responder una solicitud formal lo concede ser la persona del mandato
    # destinatario, que comprueba la base; la política solo deja pasar el ámbito.
    assert sorted(a for a, p in POLITICAS.items() if p.concede_por_ambito) == [
        "auditoria.mis_eventos", "consorcios.mis_acciones", "contactos.ver",
        "solicitud_formal.responder", "unidades.listar"]  # fmt: skip


# --- Una acción, una vista; A6 --------------------------------------------------------------


def test_p_ve_el_resumen_y_no_el_panel_completo():
    # H5 D3-A: el resumen pide el vínculo confirmado, como la base (`0119`).
    ctx = contexto(vinculos=(Vinculo(TipoVinculo.PROPIETARIO, U3A, confirmado=True),))
    assert evaluar("fiscalizacion.ver_resumen", ctx, TODO_PASA).permitido
    assert not evaluar("fiscalizacion.ver_resumen", contexto(vinculos=(P(),)), TODO_PASA).permitido
    assert not evaluar("fiscalizacion.ver_panel", ctx, TODO_PASA).permitido
    # Control: con la facultad del panel, lo ve.
    mc = contexto(facultades=("fiscalizacion.ver_panel",))
    assert evaluar("fiscalizacion.ver_panel", mc, TODO_PASA).permitido


@pytest.mark.parametrize("vinculo", [I, O])
def test_inquilino_u_ocupante_no_ven_documentos_comunes(vinculo):
    d = evaluar("documentos.ver", contexto(vinculos=(vinculo(),)), TODO_PASA)
    assert (d.estado, d.motivo_interno) == (403, "sin_concesion")
    # Control: con un mandato con documentos.ver, lo ve por la facultad.
    con_fac = contexto(vinculos=(vinculo(),), facultades=("documentos.ver",))
    assert evaluar("documentos.ver", con_fac, TODO_PASA).motivo_interno == "concedido_por_facultad"


def test_control_p_ve_documentos_comunes():
    d = evaluar("documentos.ver", contexto(vinculos=(P(),)), TODO_PASA)
    assert d.motivo_interno == "concedido_por_vinculo"


# --- Modos (R6, A3) -------------------------------------------------------------------------


@pytest.mark.parametrize("accion", ["solicitud_formal.preparar", "solicitud_formal.firmar"])
@pytest.mark.parametrize(
    "modo,permitido",
    [
        (ModoOperacion.CONSEJO, True),
        (ModoOperacion.ADMINISTRADOR_AUMENTADO, True),
        (ModoOperacion.AUTOGESTION, False),
        (None, False),
    ],
)
def test_solicitud_formal_solo_en_consejo_y_administrador_aumentado(accion, modo, permitido):
    d = evaluar(accion, contexto(facultades=(POLITICAS[accion].facultad,), modo=modo), Entidad())
    assert d.permitido is permitido


# --- Destinatario único (A14) ---------------------------------------------------------------


def test_segunda_firma_sin_administrador_vigente():
    ctx = contexto(facultades=("solicitud_formal.firmar",), administrador=False)
    segunda = Entidad(firmas=(OTRO,))
    d = evaluar("solicitud_formal.firmar", ctx, segunda)
    assert (d.estado, d.motivo_interno) == (403, "sin_administrador_vigente")
    # Control: la primera firma no necesita destinatario, y con administrador emite.
    assert evaluar("solicitud_formal.firmar", ctx, Entidad()).permitido
    con_ad = contexto(facultades=("solicitud_formal.firmar",), administrador=True)
    assert evaluar("solicitud_formal.firmar", con_ad, segunda).permitido


# --- Separación por persona (R8, A4, A5) ----------------------------------------------------


def test_actor_nulo_se_niega_y_control_con_persona_pasa():
    sin_actor = contexto(facultades=("liquidacion.revisar",), modo=ModoOperacion.AUTOGESTION,
                         persona=None)  # fmt: skip
    d = evaluar("liquidacion.revisar", sin_actor, TODO_PASA)
    assert d.motivo_interno == "identidad_indeterminada"
    con_actor = contexto(facultades=("liquidacion.revisar",), modo=ModoOperacion.AUTOGESTION)
    assert evaluar("liquidacion.revisar", con_actor, TODO_PASA).permitido


def test_respaldo_ausente_niega_y_control_presente_pasa():
    ctx = contexto(facultades=("decisiones.registrar",))
    assert evaluar("decisiones.registrar", ctx, Entidad()).motivo_interno == "sin_respaldo"
    assert evaluar("decisiones.registrar", ctx, Entidad(respaldo_presente=True)).permitido


# --- Políticas: fuente normativa, catálogo y carga ------------------------------------------


def test_toda_politica_tiene_fuente_normativa():
    assert all(p.fuente_normativa.strip() for p in POLITICAS.values())
    datos = POLITICAS["padron.ver"].model_dump(mode="json")
    del datos["fuente_normativa"]
    with pytest.raises(ValidationError, match="fuente_normativa"):
        Politica.model_validate(datos)
    with pytest.raises(ValidationError, match="fuente_normativa"):
        Politica.model_validate({**datos, "fuente_normativa": " "})


def test_las_politicas_usan_el_catalogo_de_d2_y_su_marca_economica():
    for accion, p in POLITICAS.items():
        if p.facultad is not None:
            assert p.facultad in CATALOGO, accion
            assert p.economica == CATALOGO[p.facultad], accion
    # Las 23 facultades del catálogo (21 de D2 y 2 de `0018`) tienen alguna acción que las usa.
    assert {p.facultad for p in POLITICAS.values()} - {None} == set(CATALOGO)


def test_las_acciones_de_la_tabla():
    # 5 de §2.1, 24 de §2.2, las 5 de A7 (2b-4, PR 4), las 3 de lectura (2b-4c) y la revisión
    # del mandato pendiente (E62), la historia de modos (RF41-C, 2b-6b), las 2 de la auditoría
    # (RF35-B, 2b-6c), las 2 de la lectura de coeficientes (RF05) y las 2 de documentos de 2c-2
    # (`documentos.ver_restringidos` y `documentos.cargar_operativos`) y la lectura de la revisión
    # de órdenes de pago (`egresos.revisar_orden`, H3) y la conciliación del pago manual
    # (`egresos.conciliar_pago`, 0055) y lo que muestra la interfaz (`consorcios.mis_acciones`)
    # y la consulta de proveedores (`proveedores.ver`, 0104) y quién autoriza a un habitante a
    # verlos (`proveedores.autorizar`, 0105) y el salteo con motivo del bloqueo por documentación
    # legal (`egresos.saltear_documentacion`, 0106) y los cuatro actos nuevos de la solicitud
    # formal (`solicitud_formal.retirar`, `.registrar_envio`, `.registrar_respuesta` y
    # `.responder`, 0108) e importar la liquidación del administrador (`liquidacion.importar`,
    # 0113) y las dos lecturas de la liquidación oficial (`liquidacion.ver_importada` y
    # `cuenta.ver_propia`, 0119) y las dos de los comunicados oficiales (`comunicados.emitir` y
    # `comunicados.ver`, 0122)
    assert len(POLITICAS) == 63


def _archivo(tmp_path, nombre, politicas):
    ruta = tmp_path / nombre
    ruta.write_text(json.dumps({"politicas": politicas}), encoding="utf-8")
    return ruta


def test_carga_falla_con_acciones_duplicadas(tmp_path):
    p = POLITICAS["padron.ver"].model_dump(mode="json")
    a = _archivo(tmp_path, "a.json", [p])
    b = _archivo(tmp_path, "b.json", [p])
    assert set(cargar([a])) == {"padron.ver"}  # control
    with pytest.raises(PoliticasInvalidas, match="duplicada"):
        cargar([a, b])


def test_carga_falla_con_una_politica_invalida(tmp_path):
    p = POLITICAS["padron.ver"].model_dump(mode="json")
    mala = _archivo(tmp_path, "mala.json", [{**p, "modo": ["consejo"]}])
    with pytest.raises(PoliticasInvalidas, match="mala.json"):
        cargar([mala])
    roto = tmp_path / "roto.json"
    roto.write_text("{", encoding="utf-8")
    with pytest.raises(PoliticasInvalidas, match="roto.json"):
        cargar([roto])
    with pytest.raises(PoliticasInvalidas):
        cargar([])


@pytest.mark.parametrize("marca", ["sin_decision", "decision_del_equipo"])
def test_a15_una_marca_no_tapa_a_quien_registro(marca):
    """Si la decisión tiene quién la registró, se compara aunque venga una marca (A15)."""
    p = POLITICAS["mandatos.alta"]
    entidad = replace(TODO_PASA, decision_registrada_por=YO, **{marca: True})
    d = evaluar("mandatos.alta", contexto(facultades=(p.facultad,), modo=modo_que_pasa(p)), entidad)
    assert (d.estado, d.motivo_interno) == (403, "no_registro_la_decision")


def test_por_otra_cuenta_distingue_la_cuenta_que_pide_de_otra_del_mismo_ser_humano():
    """Tanda 4 (sí del propietario del 14-11-2026): para el texto, si la preparó esta cuenta u
    otra cuenta de la misma persona. La decisión es la misma en los dos casos."""
    p = POLITICAS["egresos.aprobar_orden"]
    codigo = CodigoSeparacion.NO_ES_QUIEN_PREPARO
    gemela = UUID("0192f0c4-0000-7000-8000-000000000003")
    misma = {**IDENTIDADES, gemela: IDENTIDADES[YO]}
    ctx = contexto(facultades=(p.facultad,), modo=modo_que_pasa(p), identidades=misma)
    por_gemela = replace(TODO_PASA, preparado_por=gemela)
    por_mi = replace(TODO_PASA, preparado_por=YO)
    assert evaluar(p.accion, ctx, por_gemela).motivo_interno == codigo
    assert evaluar(p.accion, ctx, por_mi).motivo_interno == codigo
    assert por_otra_cuenta(codigo, ctx, por_gemela) is True
    # Control: la preparó la cuenta que pide.
    assert por_otra_cuenta(codigo, ctx, por_mi) is False


def test_cada_motivo_tiene_su_texto_para_otra_cuenta_de_la_misma_persona():
    assert YA_PARTICIPO_OTRA_CUENTA.keys() == YA_PARTICIPO.keys()
    for clave, texto in YA_PARTICIPO_OTRA_CUENTA.items():
        assert "otra cuenta de la misma persona" in texto.lower(), clave
        assert "esta cuenta" not in texto.lower(), clave
    assert (
        YA_PARTICIPO_OTRA_CUENTA[("egresos.aprobar_orden", CodigoSeparacion.NO_ES_QUIEN_PREPARO)]
        == "La preparó otra cuenta de la misma persona: la aprueba otra persona del Consejo."
    )


# --- Concesión por configuración (RF50 configurable, 0104) ----------------------------------

VER = "proveedores.ver"
ROLES = {VER: frozenset({"miembro_consejo", "administrador"})}


def _con_tipo(tipo: str | None, consorcio: UUID = CONSORCIO, **cambios) -> ContextoAutorizacion:
    m = replace(mandato(consorcio), tipo=tipo, **cambios)
    return replace(contexto(facultades=()), mandatos=(m,), roles_configurados=ROLES)


def test_por_configuracion_concede_al_rol_listado_y_control_sin_el_rol():
    d = evaluar(VER, _con_tipo("miembro_consejo"), Entidad())
    assert (d.permitido, d.motivo_interno) == (True, "concedido_por_configuracion")
    assert alcanza(VER, _con_tipo("miembro_consejo"))
    # Control: un tipo que la configuración no lista, o ningún rol configurado, no concede.
    for ctx in (
        _con_tipo("administrador_externo"),
        replace(_con_tipo("miembro_consejo"), roles_configurados={}),
        _con_tipo(None),
    ):
        assert evaluar(VER, ctx, Entidad()).motivo_interno == "sin_concesion"
        assert not alcanza(VER, ctx)


def test_por_configuracion_el_mandato_que_no_vale_no_cuenta():
    con_vinculo = {"vinculos": (P(),)}  # para que el ámbito no tape lo que se prueba
    for ctx in (
        _con_tipo("miembro_consejo", OTRO_CONSORCIO),
        _con_tipo("miembro_consejo", vigente_hasta=HOY),
        _con_tipo("miembro_consejo", causa_baja=CAUSA_ERROR_CARGA),
    ):
        ctx = replace(ctx, **con_vinculo)
        assert evaluar(VER, ctx, Entidad()).motivo_interno == "sin_concesion"
    # Control: el mismo con el mandato vigente sí.
    assert evaluar(VER, replace(_con_tipo("miembro_consejo"), **con_vinculo), Entidad()).permitido


def test_por_configuracion_un_tipo_de_vinculo_listado_concede():
    ctx = replace(contexto(vinculos=(P(),)), roles_configurados={VER: frozenset({"propietario"})})
    assert evaluar(VER, ctx, Entidad()).motivo_interno == "concedido_por_configuracion"
    # Control: el inquilino no está en la lista.
    inquilino = replace(ctx, vinculos=(Vinculo(TipoVinculo.INQUILINO, U3A),))
    assert evaluar(VER, inquilino, Entidad()).motivo_interno == "sin_concesion"


def test_la_configuracion_no_concede_una_accion_que_no_la_declara():
    roles = {"padron.ver": frozenset({"miembro_consejo"})}
    ctx = replace(_con_tipo("miembro_consejo"), roles_configurados=roles)
    assert evaluar("padron.ver", ctx, Entidad()).motivo_interno == "sin_concesion"
