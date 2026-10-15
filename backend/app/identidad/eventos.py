"""El correo con el enlace y el código de acceso (documento 13, §10.3 y §12.2).

`POST /api/v1/auth/enlace` lo pide con `correo.solicitar()` (`app/plataforma/correo.py`), en
la misma transacción que registra el enlace y su código y sin consorcio: o quedan los tres o
ninguno. `solicitar()` publica `correo.solicitado` en el outbox; el despachador
(`cem-planificador`) lo convierte en un trabajo `notificar.correo` y `cem-trabajos` lo envía por
SMTP (la API no tiene credenciales de correo, `TB-CEM-6`).

Carga: `para` (el correo normalizado), `asunto` y `texto` (texto plano con el enlace y el
código).

El texto lleva el token y el código en claro, porque el correo los tiene que llevar: en la base
quedan en `plataforma.outbox` y `plataforma.jobs` hasta la purga (30 días). El enlace vale 15
minutos y un solo uso, y sin la cookie `nonce` del navegador que lo pidió no sirve; el código
vale 10 minutos, cinco intentos, y muere con el enlace (usar uno gasta el otro).

El código va separado en dos grupos de tres para leerlo y dictarlo sin errores; la página lo
acepta con o sin el espacio.
"""

from sqlalchemy import Connection

from app.plataforma import correo

ASUNTO_ENLACE = "Tu enlace para entrar a Consorcio en Mano"
TEXTO_ENLACE = """Hola:

Para entrar a Consorcio en Mano abrí este enlace en el mismo navegador donde lo pediste:

{enlace}

El enlace vence en {minutos} minutos y sirve una sola vez.

Si lo leés en otro dispositivo o no podés abrir enlaces, en la página para entrar elegí
"Entrar con un código" y escribí tu correo y este código:

{codigo}

El código vence en {minutos_codigo} minutos. Si lo usás, el enlace deja de servir, y al revés.

Si no lo pediste, ignorá este correo: nadie puede entrar a tu cuenta sin el enlace o el código.
"""


def agrupar(codigo: str) -> str:
    """`042917` → `042 917`."""
    mitad = len(codigo) // 2
    return f"{codigo[:mitad]} {codigo[mitad:]}"


def solicitar_enlace(
    conn: Connection, *, para: str, enlace: str, minutos: int, codigo: str, minutos_codigo: int
) -> None:
    correo.solicitar(
        conn,
        para=para,
        asunto=ASUNTO_ENLACE,
        texto=TEXTO_ENLACE.format(
            enlace=enlace, minutos=minutos, codigo=agrupar(codigo), minutos_codigo=minutos_codigo
        ),
    )
