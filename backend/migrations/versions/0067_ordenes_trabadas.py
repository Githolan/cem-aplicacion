"""Las dos órdenes de pago manual que quedaban trabadas (P3 179 y 180; decisión del propietario,
03-11-2026: opción A en las dos).

El mapa de causas del P3 172 (`doc/diseno/Orden_Trabada_P3_172_2026-11-03.md` del repositorio
CEM) encontró dos estados sin salida segura con las reglas de `0055`:

- **A. Quien vio la cuenta dejó de ejecutar** (renunció o lo reemplazaron) antes de registrar el
  pago. Para confirmar «no se pagó» tiene que declararlo cada persona que vio la cuenta, y
  declarar exigía `egresos.ejecutar_pago` vigente. Ahora quien vio la cuenta en el acto de la
  ejecución abierta declara aunque ya no tenga la facultad, con R10 y su identidad humana
  verificada en el consorcio (`nucleo.exigir_sesion_economica`): sesión válida y reciente,
  segundo factor reciente y el modo que admite egresos. Nadie más declara sin la facultad: a
  cualquier otra persona la base le niega lo mismo que antes (`42501`), sin decir si la orden
  existe. Lo confirma otro consejero, como siempre.
- **B. La transferencia se hizo después de que venció la aprobación.** La cuenta se vio mientras
  la aprobación regía, pero el registro exigía una fecha de operación dentro de esa vigencia
  (`fecha_operacion_invalida`): la plata ya había salido y no se podía registrar. Ahora se
  registra con `fuera_de_termino` y el Consejo lo concilia explícitamente: para darlo por
  conforme tiene que escribir por qué (`conciliacion_invalida` sin motivo). La fecha sigue sin
  poder ser anterior a la ejecución ni futura.

`nucleo.exigir_acto_economico` queda igual por fuera: comprueba la facultad y llama a
`nucleo.exigir_sesion_economica`, que tiene el resto de las barreras de `0045` sin cambios. Las
lecturas de los pagos agregan `fuera_de_termino`. `nucleo.pagos_por_aclarar` lista a quien vio la
cuenta y ya no ejecuta las órdenes que esperan su declaración, en cualquier consorcio.

Downgrade: se niega (`downgrade_con_datos`) si hay algún pago fuera de término o alguna
declaración hecha sin la facultad: bajar borraría que se registró tarde o dejaría una declaración
que las reglas de `0055` no admiten. Sin eso, vuelve a las definiciones anteriores.

Revisión: 0067
Anterior: 0066
"""

from alembic import op
from sqlalchemy import text

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None

VIGENCIA_APROBACION_DIAS = 7
"""La de `0050` y `0055`."""
REGISTRO = (
    "nucleo.registrar_pago_manual(uuid, integer, bytea, text, date, bigint, text, text, uuid,"
    " uuid, text, uuid)"
)
CONCILIAR = "nucleo.conciliar_pago_manual(uuid, uuid, text, text, uuid, text, uuid)"
DECLARAR = "nucleo.declarar_pago_no_realizado(uuid, integer, text, uuid, text, uuid)"
LECTURA = "nucleo.pagos_manuales_lectura(uuid, text, uuid, text, uuid)"
SESION = "nucleo.exigir_sesion_economica(uuid, uuid, uuid, text)"
POR_ACLARAR = "nucleo.pagos_por_aclarar(uuid, text, uuid)"
VENCE = (
    f"((o.aprobada_en + make_interval(days => {VIGENCIA_APROBACION_DIAS}))\n"
    "                   at time zone tz)::date"
)

# --- Las barreras de R10, sin la facultad -------------------------------------------------------

SESION_ECONOMICA = f"""
-- R10 de un acto económico, sin la facultad: quien actúa es la persona de la petición, el modo
-- admite egresos, la sesión es suya, válida y reciente, con el segundo factor reciente, y tiene
-- identidad humana verificada en el consorcio. Devuelve esa identidad (0067).
create function {SESION.split("(")[0]}(c uuid, actor uuid, sesion uuid, origen_pedido text)
  returns uuid
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
    s nucleo.sesion;
    identidad uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido is distinct from 'api' then
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    if actor is null or actor is distinct from plataforma.persona_actual() then
      raise exception 'la persona no es la de la petición' using errcode = '42501';
    end if;
    if not exists (
         select 1 from nucleo.modo_operacion m
          where m.consorcio_id = c and m.vigente_desde <= hoy
            and (m.vigente_hasta is null or m.vigente_hasta > hoy)
            and m.modo in ('administrador_aumentado', 'autogestion')) then
      raise exception 'el modo vigente no admite egresos' using errcode = 'P0001',
        hint = 'modo_no_admitido';
    end if;
    select * into s from nucleo.sesion x
     where x.id = sesion and x.persona_id = actor and x.revocada_en is null
       and x.vence_en > now();
    if not found then
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;
    if s.creada_en <= now() - interval '12 hours' then
      raise exception 'hace falta una sesión reciente' using errcode = 'P0001',
        hint = 'requiere_sesion_reciente';
    end if;
    if s.segundo_factor_en is null or s.segundo_factor_en <= now() - interval '10 minutes' then
      raise exception 'hace falta el segundo factor reciente' using errcode = 'P0001',
        hint = 'requiere_segundo_factor_reciente';
    end if;
    -- El vínculo queda bloqueado hasta el fin de la transacción: la identidad que se registra es
    -- la que se comprobó.
    perform 1 from nucleo.identidad_persona ip
     where ip.consorcio_id = c and ip.persona_id = actor and ip.desvinculada_en is null
       for share;
    identidad := nucleo.identidad_humana_de(c, actor);
    if identidad is null then
      raise exception 'hace falta la identidad humana verificada' using errcode = 'P0001',
        hint = 'identidad_no_verificada';
    end if;
    return identidad;
  end
  $$;
revoke all on function {SESION} from public;
"""

ACTO_CON_SESION = """
-- R10 y las políticas de egresos. Devuelve la identidad humana de quien actúa. La facultad acá;
-- el resto, en nucleo.exigir_sesion_economica (0067).
create or replace function nucleo.exigir_acto_economico(c uuid, actor uuid, sesion uuid,
                                                        codigo text, origen_pedido text)
  returns uuid
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido is distinct from 'api' then
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if actor is null or actor is distinct from plataforma.persona_actual()
       or not nucleo.tiene_facultad_vigente(c, actor, codigo, nucleo.hoy_del_consorcio(c)) then
      raise exception 'sin % en este consorcio', codigo using errcode = '42501';
    end if;
    return nucleo.exigir_sesion_economica(c, actor, sesion, origen_pedido);
  end
  $$;
"""

ACTO_0045 = """
-- R10 y las políticas de egresos. Devuelve la identidad humana de quien actúa.
create or replace function nucleo.exigir_acto_economico(c uuid, actor uuid, sesion uuid,
                                                        codigo text, origen_pedido text)
  returns uuid
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
    s nucleo.sesion;
    identidad uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido is distinct from 'api' then
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    if actor is null or actor is distinct from plataforma.persona_actual()
       or not nucleo.tiene_facultad_vigente(c, actor, codigo, hoy) then
      raise exception 'sin % en este consorcio', codigo using errcode = '42501';
    end if;
    if not exists (
         select 1 from nucleo.modo_operacion m
          where m.consorcio_id = c and m.vigente_desde <= hoy
            and (m.vigente_hasta is null or m.vigente_hasta > hoy)
            and m.modo in ('administrador_aumentado', 'autogestion')) then
      raise exception 'el modo vigente no admite egresos' using errcode = 'P0001',
        hint = 'modo_no_admitido';
    end if;
    select * into s from nucleo.sesion x
     where x.id = sesion and x.persona_id = actor and x.revocada_en is null
       and x.vence_en > now();
    if not found then
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;
    if s.creada_en <= now() - interval '12 hours' then
      raise exception 'hace falta una sesión reciente' using errcode = 'P0001',
        hint = 'requiere_sesion_reciente';
    end if;
    if s.segundo_factor_en is null or s.segundo_factor_en <= now() - interval '10 minutes' then
      raise exception 'hace falta el segundo factor reciente' using errcode = 'P0001',
        hint = 'requiere_segundo_factor_reciente';
    end if;
    -- El vínculo queda bloqueado hasta el fin de la transacción: la identidad que se registra es
    -- la que se comprobó.
    perform 1 from nucleo.identidad_persona ip
     where ip.consorcio_id = c and ip.persona_id = actor and ip.desvinculada_en is null
       for share;
    identidad := nucleo.identidad_humana_de(c, actor);
    if identidad is null then
      raise exception 'hace falta la identidad humana verificada' using errcode = 'P0001',
        hint = 'identidad_no_verificada';
    end if;
    return identidad;
  end
  $$;
"""

# --- B: el pago fuera de término ----------------------------------------------------------------

COLUMNA = """
-- Transferido después de que venció la aprobación (P3 180, 0067). Lo concilia el Consejo con un
-- motivo escrito, también para darlo por conforme.
alter table nucleo.pago_manual add column fuera_de_termino boolean not null default false;
-- Lo observado siempre lleva motivo; lo conforme, solo si se pagó fuera de término (lo exige
-- nucleo.conciliar_pago_manual, que ve el pago).
alter table nucleo.conciliacion_pago_manual
  drop constraint conciliacion_pago_manual_check,
  add constraint conciliacion_pago_manual_check
    check (resultado <> 'observado' or motivo is not null);
"""
COLUMNA_ATRAS = """
alter table nucleo.conciliacion_pago_manual
  drop constraint conciliacion_pago_manual_check,
  add constraint conciliacion_pago_manual_check
    check ((resultado = 'observado') = (motivo is not null));
alter table nucleo.pago_manual drop column fuera_de_termino;
"""

REGISTRO_CAMBIOS = (
    ("    pago uuid;\n  begin\n", "    pago uuid;\n    tarde boolean;\n  begin\n"),
    (
        f"       or fecha > nucleo.hoy_del_consorcio(c)\n       or fecha > {VENCE} then\n",
        "       or fecha > nucleo.hoy_del_consorcio(c) then\n",
    ),
    (
        "        hint = 'fecha_operacion_invalida';\n    end if;\n",
        "        hint = 'fecha_operacion_invalida';\n    end if;\n"
        "    -- Después de que venció la aprobación: la cuenta se vio mientras regía (el acto de\n"
        "    -- ejecución la exige) y la plata ya salió. Se registra, marcado (P3 180, 0067).\n"
        f"    tarde := fecha > {VENCE};\n",
    ),
    (
        "       identidad_registro)\n"
        "    values (c, e.id, anterior.id, n, fecha, importe, moneda_pago, documento, archivo,"
        " actor,\n            identidad)\n",
        "       identidad_registro, fuera_de_termino)\n"
        "    values (c, e.id, anterior.id, n, fecha, importe, moneda_pago, documento, archivo,"
        " actor,\n            identidad, tarde)\n",
    ),
    (
        "                         'archivo_comprobante', archivo));\n",
        "                         'archivo_comprobante', archivo, 'fuera_de_termino', tarde));\n",
    ),
)

CONCILIAR_CAMBIOS = (
    (
        "       or (resultado_nuevo = 'observado') <> (nullif(btrim(motivo), '') is not null)"
        " then\n",
        "       -- Fuera de término, el motivo va también para darlo por conforme (0067).\n"
        "       or (resultado_nuevo = 'observado' or p.fuera_de_termino)\n"
        "          <> (nullif(btrim(motivo), '') is not null) then\n",
    ),
    (
        "                         'resultado', resultado_nuevo));\n",
        "                         'resultado', resultado_nuevo,"
        " 'fuera_de_termino', p.fuera_de_termino));\n",
    ),
)


def _lectura(con_termino: bool) -> str:
    columna = ", fuera_de_termino boolean" if con_termino else ""
    valor = ", p.fuera_de_termino" if con_termino else ""
    return f"""
drop function {LECTURA};
create function nucleo.pagos_manuales_lectura(orden uuid, facultad text, actor uuid,
                                              origen_pedido text, sesion uuid)
  returns table (id uuid, ejecucion_id uuid, orden_id uuid, corrige_a uuid,
                 numero_operacion text, fecha_operacion date, importe_centavos bigint,
                 moneda text, documento_id uuid, version_documento_id uuid,
                 registrado_por uuid, registrado_en timestamptz, conciliacion text,
                 motivo_conciliacion text, conciliado_por uuid, conciliado_en timestamptz{columna})
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select p.id, p.ejecucion_id, e.orden_id, p.corrige_a, p.numero_operacion,
             p.fecha_operacion, p.importe_centavos, p.moneda, p.documento_id,
             p.version_documento_id, p.registrado_por, p.registrado_en, k.resultado, k.motivo,
             k.conciliado_por, k.conciliado_en{valor}
        from nucleo.pago_manual p
        join nucleo.ejecucion_orden_pago e on e.consorcio_id = c and e.id = p.ejecucion_id
        left join nucleo.conciliacion_pago_manual k on k.pago_id = p.id
       where p.consorcio_id = c and (orden is null or e.orden_id = orden)
       order by p.paso;
  end
  $$;
revoke all on function {LECTURA} from public;
grant execute on function {LECTURA} to cem_app;
"""


# --- A: declara quien vio la cuenta, aunque ya no ejecute ---------------------------------------

COLUMNA_DECLARACION = """
-- La declaró quien vio la cuenta y ya no tenía egresos.ejecutar_pago (P3 179, 0067).
alter table nucleo.declaracion_pago_no_realizado
  add column sin_facultad boolean not null default false;
"""

DECLARAR_CAMBIOS = (
    (
        "    e nucleo.ejecucion_orden_pago;\n    d uuid;\n  begin\n",
        "    e nucleo.ejecucion_orden_pago;\n    d uuid;\n    sin_facultad boolean := false;\n"
        "  begin\n",
    ),
    (
        "    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.ejecutar_pago',\n"
        "                                              origen_pedido);\n",
        "    if c is null or nucleo.tiene_facultad_vigente(c, actor, 'egresos.ejecutar_pago',\n"
        "                                                  nucleo.hoy_del_consorcio(c)) then\n"
        "      identidad := nucleo.exigir_acto_economico(c, actor, sesion,"
        " 'egresos.ejecutar_pago',\n"
        "                                                origen_pedido);\n"
        "    else\n"
        "      -- Quien vio la cuenta de la ejecución abierta y dejó de ejecutar declara igual,\n"
        "      -- con R10 y su identidad verificada (P3 179, 0067). Primero si la vio: a nadie\n"
        "      -- más se le dice nada del consorcio ni de la orden (ni el modo ni su R10).\n"
        "      if actor is null or actor is distinct from plataforma.persona_actual()\n"
        "         or not nucleo.abrio_la_cuenta_de_la_orden(c, orden, actor) then\n"
        "        raise exception 'sin egresos.ejecutar_pago en este consorcio'"
        " using errcode = '42501';\n"
        "      end if;\n"
        "      identidad := nucleo.exigir_sesion_economica(c, actor, sesion, origen_pedido);\n"
        "      sin_facultad := true;\n"
        "    end if;\n",
    ),
    (
        "      (consorcio_id, ejecucion_id, motivo, declarado_por, identidad_declaro)\n"
        "    values (c, e.id, btrim(motivo), actor, identidad)\n",
        "      (consorcio_id, ejecucion_id, motivo, declarado_por, identidad_declaro,"
        " sin_facultad)\n"
        "    values (c, e.id, btrim(motivo), actor, identidad, sin_facultad)\n",
    ),
    (
        "                         'declaracion', d));\n",
        "                         'declaracion', d, 'sin_facultad', sin_facultad));\n",
    ),
)

POR_ACLARAR_SQL = f"""
-- Las órdenes que esperan que quien pide declare si pagó: vio la cuenta en el acto de la ejecución
-- manual abierta y ya no tiene egresos.ejecutar_pago en ese consorcio. En cualquier consorcio: sin
-- la facultad, ya no tiene ámbito en él (0067). Sin la cuenta.
create function {POR_ACLARAR.split("(")[0]}(actor uuid, origen_pedido text, sesion uuid)
  returns table (consorcio_id uuid, consorcio text, orden_id uuid, version integer,
                 concepto text, importe_centavos bigint, moneda text, cuenta_vista_en timestamptz)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if origen_pedido is distinct from 'api' then
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if actor is null or actor is distinct from plataforma.persona_actual() then
      raise exception 'la persona no es la de la petición' using errcode = '42501';
    end if;
    if not exists (select 1 from nucleo.sesion x
                    where x.id = sesion and x.persona_id = actor and x.revocada_en is null
                      and x.vence_en > now()) then
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;
    -- Su última apertura de cada ejecución; ya no figura si declaró después de ella.
    return query
      select k.id, k.nombre, o.id, e.version, v.concepto, e.importe_centavos, e.moneda,
             u.abierta_en
        from nucleo.ejecucion_orden_pago e
        join nucleo.orden_pago o on o.consorcio_id = e.consorcio_id and o.id = e.orden_id
        join nucleo.orden_pago_version v on v.orden_id = e.orden_id and v.numero = e.version
        join nucleo.consorcio k on k.id = e.consorcio_id and k.eliminado_en is null
        cross join lateral (
          select a.abierta_en, a.paso, a.identidad_abrio
            from nucleo.apertura_cuenta_pago_manual a
           where a.consorcio_id = e.consorcio_id and a.ejecucion_id = e.id
             and a.identidad_abrio = nucleo.identidad_humana_de(e.consorcio_id, actor)
           order by a.paso desc limit 1) u
       where e.modalidad = 'manual' and e.version = o.version_vigente
         and nucleo.abrio_la_cuenta_de_la_orden(e.consorcio_id, o.id, actor)
         -- Sin un pago vigente: si otro ya lo registró, no hay nada que declarar.
         and (select x.id is null or nucleo.pago_manual_observado(x.id)
                from nucleo.ultimo_pago_manual(e.id) x)
         and not nucleo.tiene_facultad_vigente(e.consorcio_id, actor, 'egresos.ejecutar_pago',
                                               nucleo.hoy_del_consorcio(e.consorcio_id))
         and not exists (select 1 from nucleo.declaracion_pago_no_realizado d
                          where d.ejecucion_id = e.id and d.identidad_declaro = u.identidad_abrio
                            and d.paso > u.paso)
       order by u.abierta_en;
  end
  $$;
revoke all on function {POR_ACLARAR} from public;
grant execute on function {POR_ACLARAR} to cem_app;
"""

GUARDA_DOWNGRADE = """
-- Hasta el fin del downgrade nadie registra ni declara: la guarda vale para lo que se borra.
lock table nucleo.pago_manual, nucleo.declaracion_pago_no_realizado in share row exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.pago_manual where fuera_de_termino)
     or exists (select 1 from nucleo.declaracion_pago_no_realizado where sin_facultad) then
    raise exception '0067 no baja con pagos fuera de término ni declaraciones sin la facultad'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""


def _definicion(funcion: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )


def _reemplazar(funcion: str, cambios, *, al_reves: bool = False) -> None:
    definicion = _definicion(funcion)
    for antes, despues in cambios:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0067: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    op.execute(definicion)


def upgrade() -> None:
    op.execute(SESION_ECONOMICA)
    op.execute(ACTO_CON_SESION)
    op.execute(COLUMNA)
    _reemplazar(REGISTRO, REGISTRO_CAMBIOS)
    _reemplazar(CONCILIAR, CONCILIAR_CAMBIOS)
    op.execute(_lectura(con_termino=True))
    op.execute(COLUMNA_DECLARACION)
    _reemplazar(DECLARAR, DECLARAR_CAMBIOS)
    op.execute(POR_ACLARAR_SQL)


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    op.execute(f"drop function {POR_ACLARAR}")
    _reemplazar(DECLARAR, DECLARAR_CAMBIOS, al_reves=True)
    op.execute("alter table nucleo.declaracion_pago_no_realizado drop column sin_facultad")
    op.execute(_lectura(con_termino=False))
    _reemplazar(CONCILIAR, CONCILIAR_CAMBIOS, al_reves=True)
    _reemplazar(REGISTRO, REGISTRO_CAMBIOS, al_reves=True)
    op.execute(COLUMNA_ATRAS)
    op.execute(ACTO_0045)
    op.execute(f"drop function {SESION}")
