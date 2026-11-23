"""Base de prueba real con las políticas de seguridad por fila activas (documento 13, §8.5).

Las pruebas marcadas `db` crean una base descartable en el servidor de
`CEM_TEST_ADMIN_URL` (un superusuario local), crean los roles con
`scripts/crear_roles.sql`, aplican las migraciones como `cem_migrate` y consultan como
`cem_app`. Si el servidor no responde, fallan: no se saltean en silencio.
Las marcadas `redis` usan el Redis de `CEM_TEST_REDIS_URL` y borran solo las claves `cem:rl:*`.
Para correr solo las pruebas sin servicios: `pytest -m "not db and not redis"`.
"""

import os
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from redis import Redis
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

RAIZ = Path(__file__).resolve().parent.parent
ADMIN_URL = os.environ.get(
    "CEM_TEST_ADMIN_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
)
REDIS_URL = os.environ.get("CEM_TEST_REDIS_URL", "redis://localhost:6379/0")
BASE = "cem_prueba"
CLAVES = {
    "migrate": "migrate_prueba",
    "app": "app_prueba",
    "ro": "ro_prueba",
    "backup": "backup_prueba",
    "soporte_documentos": "soporte_documentos_prueba",
    "copia_continua": "copia_continua_prueba",
    "soporte_exportacion": "soporte_exportacion_prueba",
    "soporte_privacidad": "soporte_privacidad_prueba",
}


def repartir(archivos: dict[str, int], partes: int) -> list[set[str]]:
    """Reparte los archivos de prueba en `partes` grupos de cantidad de pruebas parecida.

    Determinista: de mayor a menor cantidad (y por nombre a igual cantidad), cada archivo va al
    grupo con menos pruebas hasta ahí. Un archivo entero va a un solo grupo: sus fixtures de
    módulo y su orden no cambian.
    """
    grupos: list[set[str]] = [set() for _ in range(partes)]
    cargas = [0] * partes
    for archivo, cantidad in sorted(archivos.items(), key=lambda x: (-x[1], x[0])):
        i = min(range(partes), key=lambda j: (cargas[j], j))
        grupos[i].add(archivo)
        cargas[i] += cantidad
    return grupos


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """`CEM_PRUEBAS_PARTE=k/n` corre solo la parte k de n (ci.ps1 corre las n a la vez, cada una
    con su PostgreSQL, Redis y demás servicios: los roles de PostgreSQL son del clúster y dos
    partes en el mismo clúster se pisarían). Sin la variable corre todo."""
    parte = os.environ.get("CEM_PRUEBAS_PARTE")
    if not parte:
        return
    k_texto, _, n_texto = parte.partition("/")
    if not (k_texto.isdigit() and n_texto.isdigit() and 1 <= int(k_texto) <= int(n_texto)):
        raise pytest.UsageError(f"CEM_PRUEBAS_PARTE tiene que ser k/n con 1 <= k <= n: {parte!r}")
    k, n = int(k_texto), int(n_texto)
    archivos: dict[str, int] = {}
    for item in items:
        archivos[item.nodeid.split("::")[0]] = archivos.get(item.nodeid.split("::")[0], 0) + 1
    mios = repartir(archivos, n)[k - 1]
    quedan = [i for i in items if i.nodeid.split("::")[0] in mios]
    fuera = [i for i in items if i.nodeid.split("::")[0] not in mios]
    if fuera:
        config.hook.pytest_deselected(items=fuera)
    items[:] = quedan


def url_rol(rol: str) -> str:
    return (
        make_url(ADMIN_URL)
        .set(username=f"cem_{rol}", password=CLAVES[rol], database=BASE)
        .render_as_string(hide_password=False)
    )


def alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url_rol("migrate").replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def base_de_prueba() -> None:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"drop database if exists {BASE} with (force)"))
    admin.dispose()
    libpq = make_url(ADMIN_URL).set(drivername="postgresql").render_as_string(False)
    subprocess.run(
        [
            "psql", libpq, "-q",
            "-v", f"base={BASE}",
            "-v", f"clave_migrate={CLAVES['migrate']}",
            "-v", f"clave_app={CLAVES['app']}",
            "-v", f"clave_ro={CLAVES['ro']}",
            "-v", f"clave_backup={CLAVES['backup']}",
            "-v", f"clave_soporte_documentos={CLAVES['soporte_documentos']}",
            "-v", f"clave_copia_continua={CLAVES['copia_continua']}",
            "-v", f"clave_soporte_exportacion={CLAVES['soporte_exportacion']}",
            "-v", f"clave_soporte_privacidad={CLAVES['soporte_privacidad']}",
            "-f", str(RAIZ / "scripts" / "crear_roles.sql"),
        ],
        check=True,
    )  # fmt: skip
    # Ida, vuelta e ida: la migración se puede revertir y volver a aplicar.
    cfg = alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def vaciar_mandatos(motor_migrate: Engine) -> None:
    """Borra los mandatos de la base **descartable** de la sesión antes de bajar migraciones.

    El downgrade de 0012 se niega si existe cualquier mandato (`downgrade_con_datos`), y está
    bien que así sea en una base real. Las pruebas que bajan la cadena por debajo de 0012 llaman
    a esto primero; `cem_migrate` es el dueño de las tablas.

    Lo mismo con 0014, que se niega a bajar si hay un vínculo confirmado y, al volver a subir,
    si algún vínculo no tiene su evento de aceptación (`vinculo_sin_procedencia`). Los datos de
    prueba cargan vínculos a mano: aquí se les da una invitación y un evento de aceptación
    sintéticos (correo `.invalid`), solo en esta base. Y con 0015, que se niega a bajar si hay
    invitaciones o desasociaciones de mandato (si la base está en 0015 o más arriba). Y con
    0020, que se niega a bajar si hay un fin de vínculo registrado: se borra el fin y, si dejó la
    vigencia vacía, se le devuelve un día (solo en esta base). Y con 0021, que se niega a bajar
    si hay esquemas de coeficientes: se borran. Y con 0023, que se niega a bajar si hay
    revisiones de mandatos: se borran. Y con 0025, que se niega a bajar si hay importaciones de
    padrón o invitaciones que nombran su vínculo: se les quita y se borran. Y con 0026, que se
    niega a bajar si hay identidades humanas: se borran con sus vínculos y verificaciones. Y con
    0031, que se niega a bajar si hay contactos de emergencia: se borran. Y con 0035, que se
    niega a bajar si hay documentos: se borran con sus versiones. Y con 0038, que se niega a bajar
    si hay exportaciones registradas (y cuya constancia nombra un mandato): se borran. Y con
    0039, que se niega a bajar si hay casos de privacidad (que nombran personas): se borran. Y
    con 0108, que se niega a bajar si hay solicitudes formales: se borran primero. Y
    con 0057, que se niega a bajar si hay liquidaciones: se borran con sus gastos. Y con 0063, cuyos
    comprobantes citan su orden: se les quita antes de borrar las órdenes. Y con 0099, cuya
    constancia de la factura adjunta cita la versión de la orden y el documento: se borra antes. Y
    con 0100, que se niega a bajar si el equipo habilitó una moneda: se borran las monedas. Y con
    0122, que se niega a bajar si hay comunicados: se borran primero. Y con 0123, que se niega a
    bajar si hay reclamos, que citan importaciones y solicitudes formales: se borran antes que todo.
    Y con 0124, que se niega a bajar si hay reparaciones, que citan importaciones, documentos y
    decisiones: se borran también antes que todo. Y con 0125, que se niega a bajar si hay un
    consorcio con perfil normativo o una búsqueda de enlaces, que citan consorcios e importaciones:
    se borran también antes que todo (el perfil, que es de la jurisdicción, lo borra su downgrade).
    Y con 0127, que se niega a bajar si hay autorizaciones de ayuda o eventos hechos ayudando
    (`vaciar_ayudas`). Y con 0128, que se niega a bajar si hay presupuestos, que citan personas:
    se borran también antes que todo, los rubros primero.
    Y con 0129, que se niega a bajar si hay pagos informados o comprobantes subidos, que citan
    documentos, unidades y personas: se borran también antes que todo, los pagos primero.
    Y con 0130, que suma la historia de los estados de esos pagos: se borra antes que los pagos.
    """
    reconstruir_indices_gist(motor_migrate)
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "do $$ begin"
                " if to_regclass('plataforma.caso_privacidad') is not null then"
                "   delete from plataforma.caso_privacidad;"
                " end if;"
                " if to_regclass('plataforma.exportacion') is not null then"
                "   delete from plataforma.exportacion;"
                " end if;"
                " end $$"
            )
        )
        vaciar_ayudas(c)
        c.execute(
            text(
                "do $$ begin"
                # El perfil vinculado y las búsquedas de enlaces (0125) citan consorcios e
                # importaciones, solo se agregan y niegan su downgrade: van primero.
                " if to_regclass('nucleo.consorcio_perfil_normativo') is not null then"
                "   alter table nucleo.busqueda_enlaces_liquidacion disable trigger user;"
                "   alter table nucleo.consorcio_perfil_normativo disable trigger user;"
                "   delete from nucleo.busqueda_enlaces_liquidacion;"
                "   delete from nucleo.consorcio_perfil_normativo;"
                "   alter table nucleo.busqueda_enlaces_liquidacion enable trigger user;"
                "   alter table nucleo.consorcio_perfil_normativo enable trigger user;"
                " end if;"
                # Los pagos informados (0129) citan comprobantes, documentos, unidades y personas,
                # solo se agregan y niegan su downgrade: van primero, los pagos antes que los
                # comprobantes que citan.
                " if to_regclass('nucleo.pago_informado') is not null then"
                "   alter table nucleo.pago_informado disable trigger user;"
                "   alter table nucleo.comprobante_pago_subido disable trigger user;"
                # 0130: la historia de los estados cita los pagos; se borra antes.
                "   if to_regclass('nucleo.pago_informado_estado') is not null then"
                "     alter table nucleo.pago_informado_estado disable trigger user;"
                "     delete from nucleo.pago_informado_estado;"
                "     alter table nucleo.pago_informado_estado enable trigger user;"
                "   end if;"
                "   delete from nucleo.pago_informado;"
                "   delete from nucleo.comprobante_pago_subido;"
                "   alter table nucleo.pago_informado enable trigger user;"
                "   alter table nucleo.comprobante_pago_subido enable trigger user;"
                " end if;"
                # Las reparaciones (0124) citan importaciones, documentos, decisiones, unidades y
                # personas, solo se agregan y niegan su downgrade: van primero, de la última tabla
                # a la primera.
                # Los presupuestos (0128) citan personas, solo se agregan y niegan su downgrade:
                # van primero, los rubros antes que la versión que citan.
                " if to_regclass('nucleo.presupuesto') is not null then"
                "   alter table nucleo.presupuesto_rubro disable trigger user;"
                "   alter table nucleo.presupuesto disable trigger user;"
                "   delete from nucleo.presupuesto_rubro;"
                "   delete from nucleo.presupuesto;"
                "   alter table nucleo.presupuesto_rubro enable trigger user;"
                "   alter table nucleo.presupuesto enable trigger user;"
                " end if;"
                " if to_regclass('nucleo.reparacion') is not null then"
                "   alter table nucleo.reparacion_estado disable trigger user;"
                "   alter table nucleo.reparacion_gestion disable trigger user;"
                "   alter table nucleo.reparacion_adjunto disable trigger user;"
                "   alter table nucleo.reparacion disable trigger user;"
                "   delete from nucleo.reparacion_estado;"
                "   delete from nucleo.reparacion_gestion;"
                "   delete from nucleo.reparacion_adjunto;"
                "   delete from nucleo.reparacion;"
                "   alter table nucleo.reparacion_estado enable trigger user;"
                "   alter table nucleo.reparacion_gestion enable trigger user;"
                "   alter table nucleo.reparacion_adjunto enable trigger user;"
                "   alter table nucleo.reparacion enable trigger user;"
                " end if;"
                # Los reclamos (0123) citan importaciones, unidades, personas y solicitudes
                # formales, solo se agregan y niegan su downgrade: van primero, el historial antes.
                " if to_regclass('nucleo.reclamo_liquidacion') is not null then"
                "   alter table nucleo.reclamo_liquidacion_estado disable trigger user;"
                "   alter table nucleo.reclamo_liquidacion disable trigger user;"
                "   delete from nucleo.reclamo_liquidacion_estado;"
                "   delete from nucleo.reclamo_liquidacion;"
                "   alter table nucleo.reclamo_liquidacion_estado enable trigger user;"
                "   alter table nucleo.reclamo_liquidacion enable trigger user;"
                " end if;"
                # Los comunicados (0122) citan personas, solo se agregan y niegan su downgrade:
                # van primero, de la última tabla a la primera.
                " if to_regclass('nucleo.comunicado') is not null then"
                "   alter table nucleo.comunicado_envio disable trigger user;"
                "   alter table nucleo.comunicado_destinatario disable trigger user;"
                "   alter table nucleo.comunicado disable trigger user;"
                "   delete from nucleo.comunicado_envio;"
                "   delete from nucleo.comunicado_destinatario;"
                "   delete from nucleo.comunicado;"
                "   alter table nucleo.comunicado_envio enable trigger user;"
                "   alter table nucleo.comunicado_destinatario enable trigger user;"
                "   alter table nucleo.comunicado enable trigger user;"
                " end if;"
                # Las solicitudes formales (0108) citan mandatos, personas, identidades y
                # documentos, y niegan su downgrade: van primero, de la última tabla a la primera.
                " if to_regclass('nucleo.solicitud_formal') is not null then"
                "   if to_regclass('nucleo.solicitud_formal_envio_comprobante') is not null then"
                "     alter table nucleo.solicitud_formal_envio_comprobante disable trigger user;"
                "     delete from nucleo.solicitud_formal_envio_comprobante;"
                "     alter table nucleo.solicitud_formal_envio_comprobante enable trigger user;"
                "   end if;"
                "   alter table nucleo.solicitud_formal_retiro disable trigger user;"
                "   alter table nucleo.solicitud_formal_respuesta disable trigger user;"
                "   alter table nucleo.solicitud_formal_envio disable trigger user;"
                "   alter table nucleo.solicitud_formal_adjunto disable trigger user;"
                "   alter table nucleo.solicitud_formal_emision disable trigger user;"
                "   alter table nucleo.solicitud_formal_firma disable trigger user;"
                "   alter table nucleo.solicitud_formal disable trigger user;"
                "   delete from nucleo.solicitud_formal_retiro;"
                "   delete from nucleo.solicitud_formal_respuesta;"
                "   delete from nucleo.solicitud_formal_envio;"
                "   delete from nucleo.solicitud_formal_adjunto;"
                "   delete from nucleo.solicitud_formal_emision;"
                "   delete from nucleo.solicitud_formal_firma;"
                "   delete from nucleo.solicitud_formal;"
                "   alter table nucleo.solicitud_formal_retiro enable trigger user;"
                "   alter table nucleo.solicitud_formal_respuesta enable trigger user;"
                "   alter table nucleo.solicitud_formal_envio enable trigger user;"
                "   alter table nucleo.solicitud_formal_adjunto enable trigger user;"
                "   alter table nucleo.solicitud_formal_emision enable trigger user;"
                "   alter table nucleo.solicitud_formal_firma enable trigger user;"
                "   alter table nucleo.solicitud_formal enable trigger user;"
                " end if;"
                # Los contratos (0050) citan documentos, y las versiones de la orden citan
                # contratos y umbrales: van antes que los documentos, con las órdenes.
                # Los salteos del bloqueo por documentación legal (0106) y sus usos citan las
                # versiones de las órdenes, los proveedores y las identidades: van primero.
                " if to_regclass('nucleo.salteo_documentacion_uso') is not null then"
                "   alter table nucleo.salteo_documentacion_uso disable trigger user;"
                "   alter table nucleo.salteo_documentacion_proveedor disable trigger user;"
                "   delete from nucleo.salteo_documentacion_uso;"
                "   delete from nucleo.salteo_documentacion_proveedor;"
                "   alter table nucleo.salteo_documentacion_uso enable trigger user;"
                "   alter table nucleo.salteo_documentacion_proveedor enable trigger user;"
                " end if;"
                " if to_regclass('nucleo.contrato_proveedor') is not null then"
                "   alter table nucleo.orden_pago_aprobacion disable trigger user;"
                "   alter table nucleo.orden_pago_version disable trigger user;"
                "   alter table nucleo.contrato_proveedor_baja disable trigger user;"
                "   alter table nucleo.contrato_proveedor_confirmacion disable trigger user;"
                "   alter table nucleo.contrato_proveedor disable trigger user;"
                "   alter table nucleo.umbral_orden_pago disable trigger user;"
                # Las ejecuciones (0051) citan las versiones de la orden: van antes.
                "   if to_regclass('nucleo.ejecucion_orden_pago') is not null then"
                # Los gastos de la liquidación (0057) citan órdenes y ejecuciones: van primero.
                "     if to_regclass('nucleo.liquidacion') is not null then"
                "       alter table nucleo.liquidacion_gasto_quitado disable trigger user;"
                "       alter table nucleo.liquidacion_gasto disable trigger user;"
                "       alter table nucleo.liquidacion disable trigger user;"
                "       delete from nucleo.liquidacion_gasto_quitado;"
                "       delete from nucleo.liquidacion_gasto;"
                "       delete from nucleo.liquidacion;"
                "       alter table nucleo.liquidacion_gasto_quitado enable trigger user;"
                "       alter table nucleo.liquidacion_gasto enable trigger user;"
                "       alter table nucleo.liquidacion enable trigger user;"
                "     end if;"
                # Los pagos manuales (0055) citan las ejecuciones y los documentos: van antes.
                "     if to_regclass('nucleo.pago_manual') is not null then"
                # El cotejo (0074) cita los pagos y las diferencias: va antes.
                "       if to_regclass('nucleo.cotejo_pago') is not null then"
                "         alter table nucleo.cotejo_pago disable trigger user;"
                "         alter table nucleo.movimiento_extracto disable trigger user;"
                "         alter table nucleo.importacion_extracto disable trigger user;"
                "         delete from nucleo.cotejo_pago;"
                # «No es el pago de una orden» (0098) cita el débito: va antes.
                "         if to_regclass('nucleo.movimiento_no_es_pago') is not null then"
                "           alter table nucleo.movimiento_no_es_pago disable trigger user;"
                "           delete from nucleo.movimiento_no_es_pago;"
                "           alter table nucleo.movimiento_no_es_pago enable trigger user;"
                "         end if;"
                "         delete from nucleo.movimiento_extracto;"
                "         delete from nucleo.importacion_extracto;"
                "         delete from nucleo.columnas_extracto;"
                "         alter table nucleo.cotejo_pago enable trigger user;"
                "         alter table nucleo.movimiento_extracto enable trigger user;"
                "         alter table nucleo.importacion_extracto enable trigger user;"
                "       end if;"
                "       if to_regclass('nucleo.lectura_comprobante') is not null then"
                "         alter table nucleo.lectura_comprobante disable trigger user;"
                "         delete from nucleo.lectura_comprobante;"
                "         alter table nucleo.lectura_comprobante enable trigger user;"
                "       end if;"
                "       if to_regclass('nucleo.toma_pago_manual') is not null then"
                "         alter table nucleo.toma_pago_manual disable trigger user;"
                "         delete from nucleo.toma_pago_manual;"
                "         alter table nucleo.toma_pago_manual enable trigger user;"
                "       end if;"
                "       if to_regclass('nucleo.diferencia_pago_manual') is not null then"
                "         alter table nucleo.diferencia_pago_manual disable trigger user;"
                "         delete from nucleo.diferencia_pago_manual;"
                "         alter table nucleo.diferencia_pago_manual enable trigger user;"
                "       end if;"
                "       alter table nucleo.conciliacion_pago_manual disable trigger user;"
                "       alter table nucleo.confirmacion_pago_no_realizado disable trigger user;"
                "       alter table nucleo.declaracion_pago_no_realizado disable trigger user;"
                "       alter table nucleo.pago_manual disable trigger user;"
                "       alter table nucleo.operacion_pago_manual disable trigger user;"
                "       alter table nucleo.apertura_cuenta_pago_manual disable trigger user;"
                "       alter table nucleo.comprobante_pago_manual disable trigger user;"
                "       delete from nucleo.conciliacion_pago_manual;"
                "       delete from nucleo.confirmacion_pago_no_realizado;"
                "       delete from nucleo.declaracion_pago_no_realizado;"
                "       delete from nucleo.pago_manual;"
                "       delete from nucleo.operacion_pago_manual;"
                "       delete from nucleo.comprobante_pago_manual;"
                "       delete from nucleo.apertura_cuenta_pago_manual;"
                "       delete from nucleo.riel_de_pago_habilitado;"
                "       alter table nucleo.conciliacion_pago_manual enable trigger user;"
                "       alter table nucleo.confirmacion_pago_no_realizado enable trigger user;"
                "       alter table nucleo.declaracion_pago_no_realizado enable trigger user;"
                "       alter table nucleo.pago_manual enable trigger user;"
                "       alter table nucleo.operacion_pago_manual enable trigger user;"
                "       alter table nucleo.apertura_cuenta_pago_manual enable trigger user;"
                "       alter table nucleo.comprobante_pago_manual enable trigger user;"
                "     end if;"
                "     alter table nucleo.resultado_ejecucion_orden_pago disable trigger user;"
                "     alter table nucleo.ejecucion_orden_pago disable trigger user;"
                "     delete from nucleo.resultado_ejecucion_orden_pago;"
                "     delete from nucleo.ejecucion_orden_pago;"
                "     alter table nucleo.resultado_ejecucion_orden_pago enable trigger user;"
                "     alter table nucleo.ejecucion_orden_pago enable trigger user;"
                "   end if;"
                # Los comprobantes cargados desde el pago (0063) citan su orden: se les quita.
                "   if exists (select 1 from information_schema.columns"
                "               where table_schema = 'nucleo' and table_name = 'documento'"
                "                 and column_name = 'orden_comprobante') then"
                "     alter table nucleo.documento disable trigger orden_comprobante_fija;"
                "     update nucleo.documento set orden_comprobante = null"
                "      where orden_comprobante is not null;"
                "     alter table nucleo.documento enable trigger orden_comprobante_fija;"
                "   end if;"
                # El «Entendido» de un rechazo (0096) cita la orden: va antes.
                "   if to_regclass('nucleo.rechazo_visto') is not null then"
                "     alter table nucleo.rechazo_visto disable trigger user;"
                "     delete from nucleo.rechazo_visto;"
                "     alter table nucleo.rechazo_visto enable trigger user;"
                "   end if;"
                # La factura adjunta después de aprobar (0099) cita la versión y el documento:
                # va antes que las dos.
                "   if to_regclass('nucleo.factura_adjunta_orden_pago') is not null then"
                "     alter table nucleo.factura_adjunta_orden_pago disable trigger user;"
                "     delete from nucleo.factura_adjunta_orden_pago;"
                "     alter table nucleo.factura_adjunta_orden_pago enable trigger user;"
                "   end if;"
                # Las monedas que habilitó el equipo (0100) niegan su downgrade: se borran. Las
                # de la migración se recalculan al volver a subir.
                "   if to_regclass('nucleo.moneda_consorcio') is not null then"
                "     alter table nucleo.moneda_consorcio disable trigger user;"
                "     delete from nucleo.moneda_consorcio;"
                "     alter table nucleo.moneda_consorcio enable trigger user;"
                "   end if;"
                "   delete from nucleo.orden_pago_aprobacion;"
                "   delete from nucleo.orden_pago_version;"
                "   delete from nucleo.orden_pago;"
                "   delete from nucleo.contrato_proveedor_baja;"
                "   delete from nucleo.contrato_proveedor_confirmacion;"
                "   delete from nucleo.contrato_proveedor;"
                "   delete from nucleo.umbral_orden_pago;"
                "   alter table nucleo.orden_pago_aprobacion enable trigger user;"
                "   alter table nucleo.orden_pago_version enable trigger user;"
                "   alter table nucleo.contrato_proveedor_baja enable trigger user;"
                "   alter table nucleo.contrato_proveedor_confirmacion enable trigger user;"
                "   alter table nucleo.contrato_proveedor enable trigger user;"
                "   alter table nucleo.umbral_orden_pago enable trigger user;"
                " end if;"
                " if to_regclass('nucleo.documento') is not null then"
                # Las versiones del proveedor (0048) pueden citar un documento; se borran más
                # abajo, después de las órdenes: acá solo se les quita el documento.
                "   if to_regclass('nucleo.proveedor_version') is not null then"
                "     alter table nucleo.proveedor_version disable trigger user;"
                "     update nucleo.proveedor_version set documento_id = null"
                "      where documento_id is not null;"
                "     alter table nucleo.proveedor_version enable trigger user;"
                "   end if;"
                # Los seguros y las habilitaciones del proveedor (0103) citan el documento, su
                # versión y el proveedor: van antes que los tres.
                "   if to_regclass('nucleo.documentacion_proveedor') is not null then"
                "     alter table nucleo.documentacion_proveedor disable trigger user;"
                "     delete from nucleo.documentacion_proveedor;"
                "     alter table nucleo.documentacion_proveedor enable trigger user;"
                "   end if;"
                # Los rubros legales del proveedor y la configuración (0104) niegan su downgrade y
                # citan el proveedor: se borran acá.
                # Los pedidos de salteo y los motivos de los retiros (0110) citan el proveedor
                # y el rubro: van antes.
                "   if to_regclass('nucleo.rubro_legal_retiro_motivo') is not null then"
                "     alter table nucleo.rubro_legal_retiro_motivo disable trigger user;"
                "     delete from nucleo.rubro_legal_retiro_motivo;"
                "     alter table nucleo.rubro_legal_retiro_motivo enable trigger user;"
                "     alter table nucleo.pedido_salteo_documentacion disable trigger user;"
                "     delete from nucleo.pedido_salteo_documentacion;"
                "     alter table nucleo.pedido_salteo_documentacion enable trigger user;"
                "   end if;"
                "   if to_regclass('nucleo.rubro_legal_proveedor') is not null then"
                "     alter table nucleo.rubro_legal_proveedor disable trigger user;"
                "     delete from nucleo.rubro_legal_proveedor;"
                "     alter table nucleo.rubro_legal_proveedor enable trigger user;"
                "     alter table nucleo.configuracion_proveedores disable trigger user;"
                "     delete from nucleo.configuracion_proveedores;"
                "     alter table nucleo.configuracion_proveedores enable trigger user;"
                "   end if;"
                # Las autorizaciones del Consejo para ver los proveedores (0105) niegan su
                # downgrade y citan a las personas: se borran acá.
                "   if to_regclass('nucleo.autorizacion_ver_proveedores') is not null then"
                "     alter table nucleo.autorizacion_ver_proveedores disable trigger user;"
                "     delete from nucleo.autorizacion_ver_proveedores;"
                "     alter table nucleo.autorizacion_ver_proveedores enable trigger user;"
                "   end if;"
                # Lo que leyó la IA de un documento para carga (0088) cita sus versiones.
                "   if to_regclass('nucleo.lectura_carga') is not null then"
                "     alter table nucleo.lectura_carga disable trigger user;"
                "     delete from nucleo.lectura_carga;"
                "     alter table nucleo.lectura_carga enable trigger user;"
                "   end if;"
                # Las liquidaciones importadas y lo leído (0113) citan sus versiones y unidades.
                "   if to_regclass('nucleo.importacion_liquidacion') is not null then"
                # El gasto en IA de cada lectura (0114) cita la importación: va antes.
                "     if to_regclass('plataforma.gasto_ia') is not null then"
                "       alter table plataforma.gasto_ia disable trigger user;"
                "       delete from plataforma.gasto_ia;"
                "       alter table plataforma.gasto_ia enable trigger user;"
                "     end if;"
                # Los enlaces y QR de cada importación (0118) la citan: van antes.
                "     if to_regclass('nucleo.enlace_liquidacion') is not null then"
                "       alter table nucleo.enlace_liquidacion disable trigger user;"
                "       delete from nucleo.enlace_liquidacion;"
                "       alter table nucleo.enlace_liquidacion enable trigger user;"
                "     end if;"
                # Las correspondencias del recálculo (0116): con alguna guardada, 0116 no baja.
                "     if to_regclass('nucleo.correspondencia_grupo_liquidacion') is not null then"
                "       alter table nucleo.correspondencia_grupo_liquidacion disable trigger user;"
                "       delete from nucleo.correspondencia_grupo_liquidacion;"
                "       alter table nucleo.correspondencia_grupo_liquidacion enable trigger user;"
                "     end if;"
                "     alter table nucleo.campo_preliminar disable trigger user;"
                "     delete from nucleo.campo_preliminar;"
                "     alter table nucleo.campo_preliminar enable trigger user;"
                "     alter table nucleo.importacion_liquidacion disable trigger user;"
                "     delete from nucleo.importacion_liquidacion;"
                "     alter table nucleo.importacion_liquidacion enable trigger user;"
                "   end if;"
                "   delete from nucleo.version_documento;"
                "   delete from nucleo.documento;"
                " end if;"
                " if to_regclass('nucleo.contacto_emergencia') is not null then"
                "   delete from nucleo.contacto_emergencia;"
                " end if;"
                # Las órdenes y su historia solo se agregan (0045, 0046): en la base descartable
                # se apagan esos triggers para vaciarlas, antes de las identidades que citan.
                " if to_regclass('nucleo.orden_pago') is not null then"
                "   alter table nucleo.orden_pago_aprobacion disable trigger user;"
                "   alter table nucleo.orden_pago_version disable trigger user;"
                # El «Entendido» de un rechazo (0096) cita la orden: va antes.
                "   if to_regclass('nucleo.rechazo_visto') is not null then"
                "     alter table nucleo.rechazo_visto disable trigger user;"
                "     delete from nucleo.rechazo_visto;"
                "     alter table nucleo.rechazo_visto enable trigger user;"
                "   end if;"
                "   delete from nucleo.orden_pago_aprobacion;"
                "   delete from nucleo.orden_pago_version;"
                "   delete from nucleo.orden_pago;"
                "   alter table nucleo.orden_pago_aprobacion enable trigger user;"
                "   alter table nucleo.orden_pago_version enable trigger user;"
                " end if;"
                # Los proveedores (0048), después de las órdenes que los citan.
                " if to_regclass('nucleo.proveedor') is not null then"
                "   alter table nucleo.proveedor_version disable trigger user;"
                "   alter table nucleo.proveedor disable trigger user;"
                "   delete from nucleo.proveedor_version;"
                "   delete from nucleo.proveedor;"
                "   alter table nucleo.proveedor_version enable trigger user;"
                "   alter table nucleo.proveedor enable trigger user;"
                " end if;"
                " if to_regclass('nucleo.aprobaciones_orden_pago') is not null then"
                "   alter table nucleo.aprobaciones_orden_pago disable trigger user;"
                "   delete from nucleo.aprobaciones_orden_pago;"
                "   alter table nucleo.aprobaciones_orden_pago enable trigger user;"
                " end if;"
                " if to_regclass('nucleo.identidad_humana') is not null then"
                "   delete from nucleo.verificacion_identidad;"
                "   delete from nucleo.identidad_persona;"
                "   delete from nucleo.identidad_humana;"
                " end if;"
                " if to_regclass('nucleo.importacion_padron') is not null then"
                "   update nucleo.vinculo set importacion_id = null, nombre_declarado = null"
                "    where importacion_id is not null;"
                "   update nucleo.invitacion set vinculo_id = null where vinculo_id is not null;"
                "   delete from nucleo.importacion_padron;"
                " end if;"
                " if to_regclass('nucleo.revision_mandato') is not null then"
                "   delete from nucleo.revision_mandato;"
                " end if;"
                " if to_regclass('nucleo.esquema_coeficientes') is not null then"
                "   delete from nucleo.regla_reparto;"
                "   delete from nucleo.coeficiente_juridico;"
                "   delete from nucleo.esquema_coeficientes;"
                " end if;"
                " if to_regclass('nucleo.invitacion_mandato') is not null then"
                "   delete from nucleo.invitacion_mandato;"
                "   delete from nucleo.desasociacion_mandato;"
                " end if; end $$"
            )
        )
        c.execute(
            text(
                "do $$ begin"
                " if exists (select 1 from information_schema.columns"
                "            where table_schema = 'nucleo' and table_name = 'vinculo'"
                "              and column_name = 'finalizado_en') then"
                "   update nucleo.vinculo set finalizado_por = null, finalizado_en = null,"
                "          motivo_fin = null,"
                "          vigente_hasta = case when vigente_hasta = vigente_desde"
                "                          then vigente_desde + 1 else vigente_hasta end"
                "    where finalizado_en is not null;"
                " end if; end $$"
            )
        )
        c.execute(text("delete from nucleo.facultad_otorgada"))
        c.execute(text("delete from nucleo.mandato"))
        c.execute(
            text(
                "update nucleo.vinculo set confirmado_por = null, confirmado_en = null,"
                " confirmacion_referencia = null where confirmado_en is not null"
            )
        )
    dar_procedencia_sintetica(motor_migrate)
    # Ya con su evento sintético, la procedencia de esos vínculos es la invitación: así el
    # downgrade de 0014 no se niega por vínculos de otra fuente (si la base está en 0014).
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "do $$ begin"
                " if exists (select 1 from information_schema.columns"
                "            where table_schema = 'nucleo' and table_name = 'vinculo'"
                "              and column_name = 'fuente') then"
                "   update nucleo.vinculo set fuente = 'invitacion', origen = 'soporte',"
                "          registrado_por = null where fuente <> 'invitacion';"
                " end if; end $$"
            )
        )
    faltan = sin_procedencia(motor_migrate)
    assert not faltan, f"vínculos sin procedencia antes de bajar (E58): {faltan}"


AYUDA_SIN_EVENTOS = """
do $$
declare
  c uuid;
  e record;
  previo text;
  h text;
begin
  if to_regclass('nucleo.autorizacion_ayuda') is null then
    return;
  end if;
  for c in select distinct x.consorcio_id from auditoria.eventos x
            where x.autorizacion_id is not null loop
    delete from auditoria.eventos x where x.consorcio_id = c and x.autorizacion_id is not null;
    previo := '';
    for e in select * from auditoria.eventos x where x.consorcio_id = c order by x.secuencia loop
      h := encode(sha256(convert_to(concat_ws('|',
        previo, e.consorcio_id, e.id, e.secuencia,
        to_char(e.creado_en at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        e.actor_persona_id, e.origen, e.accion, e.entidad, e.entidad_id,
        e.antes, e.despues, e.motivo, e.ip, e.en_representacion_de, e.autorizacion_id),
        'UTF8')), 'hex');
      update auditoria.eventos x set hash_previo = previo, hash = h
       where x.consorcio_id = e.consorcio_id and x.id = e.id and x.creado_en = e.creado_en;
      previo := h;
    end loop;
  end loop;
  alter table nucleo.autorizacion_ayuda_revocacion disable trigger user;
  alter table nucleo.autorizacion_ayuda disable trigger user;
  delete from nucleo.autorizacion_ayuda_revocacion;
  delete from nucleo.autorizacion_ayuda;
  alter table nucleo.autorizacion_ayuda_revocacion enable trigger user;
  alter table nucleo.autorizacion_ayuda enable trigger user;
end $$
"""


def vaciar_ayudas(c) -> None:
    """La ayuda (0127) niega su downgrade si hay autorizaciones o eventos hechos ayudando. En la
    base **descartable**: se borran esos eventos, se vuelve a encadenar el resto de la cadena de
    cada consorcio afectado (con la misma fórmula de `auditoria.encadenar`, así las pruebas de la
    cadena siguen pasando) y se borran las autorizaciones."""
    c.exec_driver_sql(AYUDA_SIN_EVENTOS)


def dar_procedencia_sintetica(motor_migrate: Engine) -> None:
    """Invitación aceptada y evento de aceptación sintéticos (correo `.invalid`) para cada
    vínculo de la base **descartable** que no tiene su evento, como los que cargan a mano los
    datos de prueba. Sin eso, el upgrade a 0014 se niega (`vinculo_sin_procedencia`, criterio
    9), como debe hacerlo en una base real. Sirve desde 0007, que trae las invitaciones.
    """
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "with sin as ("
                "  select v.* from nucleo.vinculo v where not exists ("
                "    select 1 from auditoria.eventos e"
                "     where e.accion = 'identidad.invitacion.aceptar'"
                "       and e.despues ->> 'vinculo_id' = v.id::text)),"
                " inv as ("
                "  insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo, nombre,"
                "    token_hash, vence_en, aceptada_en, persona_id)"
                "  select s.consorcio_id, s.unidad_id, s.tipo,"
                "    'sintetica-' || s.id || '@ejemplo.invalid', 'Sintética',"
                "    sha256(s.id::text::bytea), now() + interval '1 day', now(), s.persona_id"
                "    from sin s"
                "  returning id, consorcio_id, persona_id, token_hash)"
                " insert into auditoria.eventos (consorcio_id, actor_persona_id, origen, accion,"
                "   entidad, entidad_id, despues, hash_previo, hash)"
                " select i.consorcio_id, i.persona_id, 'api', 'identidad.invitacion.aceptar',"
                "   'invitacion', i.id, jsonb_build_object('vinculo_id', s.id), '', ''"
                "   from inv i join sin s on sha256(s.id::text::bytea) = i.token_hash"
            )
        )
        # Bajar por debajo de 0007 borra las invitaciones y deja sus eventos: se recrean con
        # el mismo id, para que el primer evento de cada vínculo vuelva a tener su invitación.
        c.execute(
            text(
                "insert into nucleo.invitacion (id, consorcio_id, unidad_id, tipo, correo, nombre,"
                "   token_hash, vence_en, aceptada_en, persona_id)"
                " select distinct on (e.entidad_id) e.entidad_id, v.consorcio_id, v.unidad_id,"
                "   v.tipo, 'recreada-' || e.entidad_id || '@ejemplo.invalid', 'Recreada',"
                "   sha256(e.entidad_id::text::bytea), now() + interval '1 day', now(),"
                "   v.persona_id"
                "  from auditoria.eventos e"
                "  join nucleo.vinculo v on v.id::text = e.despues ->> 'vinculo_id'"
                " where e.accion = 'identidad.invitacion.aceptar'"
                "   and not exists (select 1 from nucleo.invitacion i where i.id = e.entidad_id)"
            )
        )


def sin_procedencia(motor_migrate: Engine) -> list[dict]:
    """Los vínculos a los que el upgrade a 0014 no les encontraría procedencia, con la misma
    consulta que esa migración (el primer evento de aceptación que nombra al vínculo, unido a su
    invitación del mismo consorcio), y con lo que hace falta para entender por qué (E58)."""
    with motor_migrate.connect() as c:
        return [
            dict(f)
            for f in c.execute(
                text(
                    "with primera as ("
                    "  select distinct on ((e.despues ->> 'vinculo_id')::uuid)"
                    "    (e.despues ->> 'vinculo_id')::uuid as vinculo_id, e.consorcio_id,"
                    "    e.entidad_id, e.entidad, e.secuencia"
                    "   from auditoria.eventos e"
                    "  where e.accion = 'identidad.invitacion.aceptar' and e.entidad = 'invitacion'"
                    "    and (e.despues ->> 'vinculo_id') is not null"
                    "  order by (e.despues ->> 'vinculo_id')::uuid, e.secuencia)"
                    " select v.id, v.consorcio_id, v.creado_en, p.consorcio_id as consorcio_evento,"
                    "   p.entidad_id as invitacion, p.secuencia,"
                    "   (select count(*) from nucleo.invitacion i where i.id = p.entidad_id)"
                    "     as invitaciones_con_ese_id,"
                    "   (select count(*) from auditoria.eventos e"
                    "     where e.despues ->> 'vinculo_id' = v.id::text) as eventos_que_lo_nombran"
                    "  from nucleo.vinculo v left join primera p on p.vinculo_id = v.id"
                    " where not exists (select 1 from primera p2"
                    "   join nucleo.invitacion i on i.id = p2.entidad_id"
                    "    and i.consorcio_id = p2.consorcio_id"
                    "  where p2.vinculo_id = v.id and p2.consorcio_id = v.consorcio_id)"
                )
            ).mappings()
        ]


def reconstruir_indices_gist(motor_migrate: Engine) -> None:
    """Reconstruye los índices GiST de `nucleo` en la base **descartable** de la sesión (E58).

    Con PostgreSQL 18.4 y 18.6 la suite deja, de vez en cuando, la entrada GiST de un vínculo
    vivo marcada como muerta (LP_DEAD): la búsqueda por `consorcio_id` se lo saltea, el
    `UPDATE ... FROM` de 0014 lo deja sin procedencia y la subida se niega
    (`vinculo_sin_procedencia`). Visto con `pageinspect` el 21-10-2026; un `REINDEX` lo corrige.
    Coincide con el fallo de PostgreSQL "GiST: Invalidate killed items consistently" (GiST no
    vaciaba `killedItems[]` al reescanear; la restricción de exclusión reescanea en cada alta),
    arreglado el 19-08-2026 hasta la 14, después de 18.6. Que sea exactamente ese fallo NO está
    verificado. Cuando `pruebas/compose.yml` y producción tengan la versión menor siguiente y la
    suite pase sin esto, se borra.
    """
    with motor_migrate.connect() as c:
        indices = (
            c.execute(
                text(
                    "select format('%I.%I', n.nspname, i.relname) from pg_index x"
                    " join pg_class i on i.oid = x.indexrelid"
                    " join pg_namespace n on n.oid = i.relnamespace"
                    " join pg_am a on a.oid = i.relam"
                    " where n.nspname = 'nucleo' and a.amname = 'gist'"
                )
            )
            .scalars()
            .all()
        )
        for indice in indices:
            c.exec_driver_sql(f"reindex index {indice}")
        c.commit()


def volver_a_subir(cfg, motor_migrate: Engine) -> None:
    """Sube a `head` después de bajar por debajo de 0014: reconstruye los GiST (E58) y, si se bajó
    por debajo de 0007, que se lleva las invitaciones, les devuelve su procedencia."""
    command.upgrade(cfg, "0013")
    reconstruir_indices_gist(motor_migrate)
    dar_procedencia_sintetica(motor_migrate)
    faltan = sin_procedencia(motor_migrate)
    assert not faltan, f"vínculos sin procedencia antes de subir a 0014 (E58): {faltan}"
    command.upgrade(cfg, "head")
    descartar_conexiones_de_la_sesion()


MOTORES_DE_LA_SESION: list[Engine] = []
"""Los motores que viven toda la sesión (`motor_migrate`, `motor_app`)."""


def descartar_conexiones_de_la_sesion() -> None:
    """Bajar y volver a subir la base recrea sus tipos y funciones con OID nuevos. Las conexiones
    que quedaron en los pools de la sesión tienen sentencias preparadas (psycopg prepara una
    consulta desde su quinta ejecución) con los OID viejos, y la próxima falla con «cache lookup
    failed for type» según qué pruebas hayan corrido antes en esa parte. Se cierran: las siguientes
    pruebas abren conexiones nuevas."""
    for motor in MOTORES_DE_LA_SESION:
        motor.dispose()


@pytest.fixture
def redis_limpio() -> Iterator[Redis]:
    """Redis real sin claves del límite de tasa de pruebas anteriores."""
    cliente = Redis.from_url(REDIS_URL)

    def limpiar() -> None:
        for clave in cliente.scan_iter("cem:rl:*"):
            cliente.delete(clave)

    limpiar()
    yield cliente
    limpiar()
    cliente.close()


@pytest.fixture(scope="session")
def motor_migrate(base_de_prueba) -> Engine:
    motor = create_engine(url_rol("migrate"))
    MOTORES_DE_LA_SESION.append(motor)
    return motor


@pytest.fixture(scope="session")
def motor_app(base_de_prueba) -> Engine:
    motor = create_engine(url_rol("app"))
    MOTORES_DE_LA_SESION.append(motor)
    return motor


@dataclass(frozen=True)
class Datos:
    consorcio_a: UUID
    consorcio_b: UUID
    unidad_a1: UUID
    unidad_a2: UUID
    unidad_b1: UUID
    persona_a: UUID
    """Propietaria en A."""
    persona_b: UUID
    """Inquilina en B."""
    persona_ab: UUID
    """Propietaria en A e inquilina en B."""
    persona_vencida: UUID
    """Tuvo un vínculo en A que ya terminó."""


@pytest.fixture(scope="session")
def datos(motor_migrate: Engine) -> Datos:
    """Carga sintética con cem_migrate (BYPASSRLS)."""

    def uno(conn, sql: str, **p) -> UUID:
        return conn.execute(text(sql + " returning id"), p).scalar_one()

    with motor_migrate.begin() as c:
        ca = uno(c, "insert into nucleo.consorcio (nombre) values ('Edificio Alvear 100')")
        cb = uno(c, "insert into nucleo.consorcio (nombre) values ('Edificio Belgrano 200')")
        ins_u = "insert into nucleo.unidad (consorcio_id, designacion) values (:c, :d)"
        a1 = uno(c, ins_u, c=ca, d="1° A")
        a2 = uno(c, ins_u, c=ca, d="1° B")
        b1 = uno(c, ins_u, c=cb, d="PB")
        ins_p = "insert into nucleo.persona (correo, nombre) values (:c, :n)"
        pa = uno(c, ins_p, c="ana@ejemplo.com", n="Ana")
        pb = uno(c, ins_p, c="beto@ejemplo.com", n="Beto")
        pab = uno(c, ins_p, c="carla@ejemplo.com", n="Carla")
        pv = uno(c, ins_p, c="dario@ejemplo.com", n="Darío")
        ins_v = (
            "insert into nucleo.vinculo"
            " (consorcio_id, unidad_id, persona_id, tipo, vigente_desde, vigente_hasta, fuente,"
            " origen)"
            " values (:c, :u, :p, :t, :desde, :hasta, 'documento', 'soporte')"
        )
        for cons, uni, per, tipo, desde, hasta in [
            (ca, a1, pa, "propietario", "2020-01-01", None),
            (cb, b1, pb, "inquilino", "2024-03-01", None),
            (ca, a2, pab, "propietario", "2019-05-01", None),
            (cb, b1, pab, "inquilino", "2025-01-01", None),
            (ca, a2, pv, "inquilino", "2018-01-01", "2019-01-01"),
        ]:
            uno(c, ins_v, c=cons, u=uni, p=per, t=tipo, desde=desde, hasta=hasta)
    return Datos(ca, cb, a1, a2, b1, pa, pb, pab, pv)


def verificar_en_el_consorcio(motor_migrate: Engine, consorcio: UUID, persona: UUID) -> UUID:
    """Datos de prueba: la cuenta queda verificada en el consorcio (A13-C) con un documento que
    nadie más tiene. Escribe directo como `cem_migrate`, sin el script de soporte ni su evento
    de auditoría; lo que hace soporte se prueba en `test_identidad_humana.py`."""
    with motor_migrate.begin() as c:
        identidad = c.execute(
            text(
                "insert into nucleo.identidad_humana (consorcio_id, huella, clave_version)"
                " values (:c, :h, 1) returning id"
            ),
            {"c": consorcio, "h": os.urandom(32)},
        ).scalar_one()
        c.execute(
            text(
                "insert into nucleo.identidad_persona (consorcio_id, persona_id, identidad_id)"
                " values (:c, :p, :i)"
            ),
            {"c": consorcio, "p": persona, "i": identidad},
        )
    return identidad


def misma_identidad(motor_migrate: Engine, consorcio: UUID, de: UUID, cuenta: UUID) -> None:
    """Datos de prueba: `cuenta` es otra cuenta del mismo ser humano que `de`, que tiene que
    estar verificada en el consorcio (lo que hace `vincular` de soporte)."""
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "insert into nucleo.identidad_persona (consorcio_id, persona_id, identidad_id)"
                " select consorcio_id, :p, identidad_id from nucleo.identidad_persona"
                " where consorcio_id = :c and persona_id = :de and desvinculada_en is null"
            ),
            {"c": consorcio, "p": cuenta, "de": de},
        )


def clave_idempotencia() -> dict[str, str]:
    """Encabezado `Idempotency-Key` nuevo: un intento lógico distinto por llamada."""
    return {"Idempotency-Key": str(uuid4())}
