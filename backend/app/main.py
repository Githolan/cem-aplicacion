from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from redis import Redis

from app import ayuda, errores, limite, llm, registros
from app.acceso import crear_verificador
from app.config import Config, cargar
from app.consorcios.auditoria import router as auditoria
from app.consorcios.ayudas import router as ayudas
from app.consorcios.coeficientes import router as coeficientes
from app.consorcios.comunicados import router as comunicados
from app.consorcios.contactos import router as contactos
from app.consorcios.cotejo import router as cotejo
from app.consorcios.documentos import router as documentos
from app.consorcios.egresos import router as egresos
from app.consorcios.fiscalizacion import router as fiscalizacion
from app.consorcios.importacion import router as importacion
from app.consorcios.liquidaciones import router as liquidaciones
from app.consorcios.mandatos import router as mandatos
from app.consorcios.mandatos_invitaciones import router as mandatos_invitaciones
from app.consorcios.padron import router as padron
from app.consorcios.reclamos import router as reclamos
from app.consorcios.reparaciones import router as reparaciones
from app.consorcios.router import router as consorcios
from app.contexto import exigir_ruta_con_ayuda, require_scope
from app.db import crear_motor
from app.identidad import sesiones
from app.identidad.contrasena import router as contrasena
from app.identidad.invitaciones import router as invitaciones
from app.identidad.llave_acceso import router as llave_acceso
from app.identidad.privacidad import router as privacidad
from app.identidad.router import router as identidad
from app.identidad.router import validar_origen
from app.identidad.segundo_factor import router as segundo_factor
from app.liquidacion.importacion import router as importacion_liquidacion
from app.liquidacion.oficial import router as liquidacion_oficial
from app.plataforma.router import SIN_LIMITE_DE_TASA, CacheDependencias
from app.plataforma.router import router as plataforma
from app.soporte.router import router as soporte


def crear_app(config: Config | None = None) -> FastAPI:
    # Solo el arranque real exige el modelo; las pruebas y el OpenAPI pasan su propia Config.
    if config is None:
        llm.exigir_variables()
        config = cargar()
    if not config.redis_url:
        raise RuntimeError("CEM_REDIS_URL es obligatoria: sin Redis no hay límite de tasa (TB-8)")
    if config.origen_publico:
        validar_origen(config.origen_publico)
    if config.codigo_clave:
        sesiones.clave_codigo(config.codigo_clave)
    # RF53: el encabezado de la ayuda, solo donde se evalúa (`exigir_ruta_con_ayuda`).
    app = FastAPI(
        title="Consorcio en Mano",
        version=config.version,
        dependencies=[Depends(exigir_ruta_con_ayuda)],
    )
    app.state.config = config
    app.state.motor = crear_motor(config.database_url) if config.database_url else None
    # `memory://` solo lo usan las pruebas que no miden el límite: ahí no hay Redis que sondear.
    app.state.redis = (
        Redis.from_url(
            config.redis_url,
            socket_connect_timeout=limite.ESPERA_REDIS_S,
            socket_timeout=limite.ESPERA_REDIS_S,
        )
        if config.redis_url.startswith(("redis://", "rediss://"))
        else None
    )
    app.state.access = crear_verificador(config.access_equipo, config.access_aud)
    app.state.salud_dependencias = CacheDependencias(config.salud_cache_segundos)
    errores.instalar(app)
    app.state.limitador = limite.crear_limitador(config.redis_url)
    # RF53: la ayuda pedida en el encabezado, para toda la petición (`app/ayuda.py`). Va por
    # dentro del límite de tasa: un pedido con `X-CEM-Ayuda` gasta cupo y, si se pasa, recibe el
    # 429 antes de que se lea la ayuda.
    app.add_middleware(ayuda.AyudaDePeticion)
    app.add_middleware(
        limite.LimiteDeTasa, limitador=app.state.limitador, exentas=SIN_LIMITE_DE_TASA
    )
    # El último agregado queda por fuera: registra también los 429 y 503 del límite de tasa.
    app.add_middleware(registros.RegistroDePeticiones)
    app.include_router(plataforma)
    app.include_router(identidad)
    app.include_router(invitaciones)
    app.include_router(segundo_factor)
    app.include_router(llave_acceso)
    app.include_router(contrasena)
    app.include_router(privacidad)
    app.include_router(consorcios)
    app.include_router(padron)
    app.include_router(coeficientes)
    app.include_router(importacion)
    app.include_router(mandatos)
    app.include_router(auditoria)
    app.include_router(mandatos_invitaciones)
    app.include_router(contactos)
    app.include_router(documentos)
    app.include_router(cotejo)  # antes que `egresos`: `/pagos/{orden}` tomaría `/pagos/cotejo`
    app.include_router(egresos)
    app.include_router(liquidaciones)
    app.include_router(importacion_liquidacion)
    app.include_router(liquidacion_oficial)
    app.include_router(fiscalizacion)
    app.include_router(comunicados)
    app.include_router(reclamos)
    app.include_router(reparaciones)
    app.include_router(ayudas)
    app.include_router(soporte)
    app.state.rutas_con_ayuda = ayuda.rutas_que_admiten(
        (metodo, r.path, require_scope in _llamadas(r.dependant))
        for r in _rutas(app.routes)
        for metodo in r.methods
    )
    return app


def _llamadas(dependant) -> list:
    out = []
    for d in dependant.dependencies:
        out.append(d.call)
        out.extend(_llamadas(d))
    return out


def _rutas(routes) -> list[APIRoute]:
    """Las rutas de la app, bajando por los routers incluidos (FastAPI 0.141 los anida)."""
    out = []
    for r in routes:
        if isinstance(r, APIRoute):
            out.append(r)
        elif hasattr(r, "original_router"):
            out.extend(_rutas(r.original_router.routes))
    return out
