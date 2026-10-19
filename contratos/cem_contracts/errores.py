"""Cuerpo uniforme de error (documento 13, §11.1)."""

from enum import StrEnum

from pydantic import BaseModel, Field


class CodigoError(StrEnum):
    """Identificadores estables que el frontend traduce. Nunca cambian de significado."""

    NO_AUTENTICADO = "no_autenticado"
    NO_ENCONTRADO = "no_encontrado"
    """También se usa para recursos de otro consorcio: 403 y 404 no se distinguen hacia afuera."""
    PETICION_INVALIDA = "peticion_invalida"
    DEMASIADAS_PETICIONES = "demasiadas_peticiones"
    """Límite de tasa superado (documento 13, §10.6); la respuesta lleva `Retry-After`."""
    ERROR_INTERNO = "error_interno"
    SERVICIO_NO_DISPONIBLE = "servicio_no_disponible"
    """Falta una dependencia sin la cual no se atiende (Redis del límite, claves de Access)."""
    IDEMPOTENCIA_REUTILIZADA = "idempotencia_reutilizada"
    """La `Idempotency-Key` ya se usó en esta ruta con otro pedido (documento 13, §7.4)."""
    IDEMPOTENCIA_EN_CURSO = "idempotencia_en_curso"
    """Otro pedido con la misma `Idempotency-Key` sigue en curso; se reintenta más tarde."""
    ENLACE_INVALIDO = "enlace_invalido"
    """El enlace de acceso no existe, venció, ya se usó o se abrió en otro navegador (§10.3).
    Las cuatro causas responden igual: la diferencia solo va a los registros."""
    CODIGO_INVALIDO = "codigo_invalido"
    """El código por correo no sirve: no hay cuenta, no es el del último pedido, venció, ya se
    usó, se agotaron sus cinco intentos o es incorrecto (§10.3). Todas las causas responden
    igual: la diferencia solo va a los registros."""
    CSRF_INVALIDO = "csrf_invalido"
    """Falta el encabezado `X-CSRF-Token` o no corresponde a la sesión (documento 13, §10.4)."""
    SIN_PERMISO = "sin_permiso"
    """La persona está en el consorcio pero su vínculo no le da esa acción (documento 08, §4)."""
    INVITACION_INVALIDA = "invitacion_invalida"
    """La invitación no existe, venció, ya se aceptó o la retiraron (RF03). Todas las causas
    responden igual: la diferencia solo va a los registros."""
    INVITACION_REQUIERE_SESION = "invitacion_requiere_sesion"
    """El correo invitado ya tiene cuenta: hay que entrar con él y volver a abrir la invitación."""
    INVITACION_DE_OTRA_PERSONA = "invitacion_de_otra_persona"
    """La sesión abierta es de otra persona que la del correo invitado."""
    INVITACION_NO_PENDIENTE = "invitacion_no_pendiente"
    """Se pidió retirar una invitación que ya se aceptó: para quitar el acceso se termina el
    vínculo."""
    SEGUNDO_FACTOR_REQUERIDO = "segundo_factor_requerido"
    """La acción pide haber verificado el segundo factor en los últimos 10 minutos en esta
    sesión (TB-CEM-3), o la persona todavía no tiene uno activo."""
    SEGUNDO_FACTOR_INVALIDO = "segundo_factor_invalido"
    """El código de la aplicación o de recuperación no sirve: incorrecto, ya usado o fuera de
    tiempo. Todas las causas responden igual."""
    SEGUNDO_FACTOR_YA_ACTIVO = "segundo_factor_ya_activo"
    """Ya hay una aplicación de autenticación confirmada: para cambiarla, primero se da de baja."""
    SEGUNDO_FACTOR_SIN_ALTA = "segundo_factor_sin_alta"
    """No hay un alta pendiente que confirmar, ni una aplicación activa con que verificar."""
    CREDENCIALES_INVALIDAS = "credenciales_invalidas"
    """El correo y la contraseña no sirven: no hay cuenta, no tiene contraseña o es incorrecta
    (§10.3). Todas las causas responden igual y en el mismo tiempo: la diferencia solo va a los
    registros."""
    ENTRADA_VENCIDA = "entrada_vencida"
    """El paso del segundo factor de la entrada con contraseña ya no sirve: venció (5 minutos),
    se agotaron sus intentos o se abrió en otro navegador. Hay que volver a escribir la
    contraseña."""
    CONTRASENA_ACTUAL_INVALIDA = "contrasena_actual_invalida"
    """Para cambiar la contraseña sin segundo factor activo hay que escribir la actual, y no
    coincide (o falta)."""
    CONTRASENA_DEBIL = "contrasena_debil"
    """La contraseña nueva es demasiado corta una vez normalizada o es igual al correo."""
    VINCULO_YA_CONFIRMADO = "vinculo_ya_confirmado"
    """El vínculo ya está confirmado: una confirmación no se repite ni se reemplaza en 2b-4."""
    VINCULO_FINALIZADO = "vinculo_finalizado"
    """El vínculo ya terminó: no se confirma."""
    MANDATO_YA_ASOCIADO = "mandato_ya_asociado"
    """El mandato ya tiene una cuenta: para cambiarla, primero se desasocia (A7)."""
    MANDATO_NO_VIGENTE = "mandato_no_vigente"
    """El mandato terminó o tiene una baja: no admite una cuenta (A7)."""
    MANDATO_NO_ASOCIABLE = "mandato_no_asociable"
    """Solo un administrador externo se asocia o desasocia por invitación (A7)."""
    MANDATO_SIN_PERSONA = "mandato_sin_persona"
    """El mandato no tiene una cuenta asociada: no hay qué desasociar."""
    ASOCIACION_INCOMPATIBLE_CON_CONSEJO = "asociacion_incompatible_con_consejo"
    """La persona tiene en ese consorcio un mandato de Consejo vigente o futuro que se
    superpone con el del administrador externo: el Consejo fiscaliza y no co-administra (P1-d)."""
    ASOCIACION_DE_SI_MISMO = "asociacion_de_si_mismo"
    """Quien emitió o aprobó la invitación de mandato no la acepta para sí."""
    DECISION_INVALIDA = "decision_invalida"
    """La decisión no existe en este consorcio, o la causa pide otro tipo (la remoción, una
    asamblea; C6)."""
    PERSONA_SIN_VINCULO = "persona_sin_vinculo"
    """La persona no tiene un vínculo confirmado, vigente o futuro, en este consorcio (2b-4c). El
    mismo código sale si la cuenta no existe o es de otro consorcio: no permite enumerarlas."""
    EXTERNO_POR_INVITACION = "externo_por_invitacion"
    """El administrador externo se da de alta sin cuenta; la cuenta se asocia por invitación con
    cuatro ojos (A7)."""
    MANDATO_SIN_CUENTA = "mandato_sin_cuenta"
    """Un miembro del Consejo o un administrador necesita su cuenta (A12)."""
    CUIT_INVALIDO = "cuit_invalido"
    """El CUIT falta (administrador externo, A11) o su dígito verificador no corresponde."""
    FACULTAD_FUERA_DE_PAQUETE = "facultad_fuera_de_paquete"
    """Alguna facultad pedida no está en el paquete del tipo de mandato, o la lista está vacía."""
    RESPALDO_INVALIDO = "respaldo_invalido"
    """El respaldo de la baja no corresponde a la causa (C6)."""
    ADMINISTRACION_OCUPADA = "administracion_ocupada"
    """Ya hay un mandato de administración en esas fechas en este consorcio (A14)."""
    MANDATO_SUPERPUESTO = "mandato_superpuesto"
    """La persona ya tiene un mandato de ese tipo en esas fechas."""
    CONSEJO_INCOMPATIBLE_CON_ADMINISTRACION = "consejo_incompatible_con_administracion"
    """La persona está asociada a un administrador externo vigente o futuro que se superpone:
    el Consejo fiscaliza y no co-administra (P1-d, R10)."""
    MANDATO_YA_DADO_DE_BAJA = "mandato_ya_dado_de_baja"
    """El mandato ya tiene una baja registrada (A10, irreversible)."""
    MANDATO_TERMINADO = "mandato_terminado"
    """El mandato ya terminó por su fecha: solo admite una baja por error de carga."""


class Error(BaseModel):
    codigo: CodigoError
    mensaje: str = Field(description="Texto genérico para la persona usuaria; sin trazas.")
    detalle: dict[str, object] | None = None
    id_correlacion: str = Field(description="Identificador para cruzar con los registros.")
