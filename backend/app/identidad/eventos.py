"""El correo con el enlace de acceso (documento 13, §10.3 y §12.2).

`POST /api/v1/auth/enlace` lo pide con `correo.solicitar()` (`app/plataforma/correo.py`), en
la misma transacción que registra el enlace y sin consorcio: o quedan los dos o ninguno.
`solicitar()` publica `correo.solicitado` en el outbox; el despachador (`cem-planificador`) lo
convierte en un trabajo `notificar.correo` y `cem-trabajos` lo envía por SMTP (la API no tiene
credenciales de correo, `TB-CEM-6`).

Carga: `para` (el correo normalizado), `asunto` y `texto` (texto plano con el enlace).

El texto lleva el token en claro, porque el correo lo tiene que llevar: en la base queda en
`plataforma.outbox` y `plataforma.jobs` hasta la purga (30 días). Vale 15 minutos y un solo
uso, y sin la cookie `nonce` del navegador que lo pidió no sirve.
"""

from sqlalchemy import Connection

from app.plataforma import correo

ASUNTO_ENLACE = "Tu enlace para entrar a Consorcio en Mano"
TEXTO_ENLACE = """Hola:

Para entrar a Consorcio en Mano abrí este enlace en el mismo navegador donde lo pediste:

{enlace}

El enlace vence en {minutos} minutos y sirve una sola vez.

Si no lo pediste, ignorá este correo: nadie puede entrar a tu cuenta sin abrirlo.
"""


def solicitar_enlace(conn: Connection, *, para: str, enlace: str, minutos: int) -> None:
    correo.solicitar(
        conn,
        para=para,
        asunto=ASUNTO_ENLACE,
        texto=TEXTO_ENLACE.format(enlace=enlace, minutos=minutos),
    )
