"""Descripción legible del dispositivo de una sesión, derivada del agente (documento 13, §10.4).

La lista de sesiones (RF04) muestra «Chrome en Android» y no el agente crudo. El agente no
figura entre los datos que §16.1 manda redactar, pero junto con otros datos sirve de huella del
navegador: si alguien tuviera una sesión robada, el agente crudo de los otros dispositivos de la
persona le ayudaría a imitarlos. La descripción alcanza para que la persona reconozca cada uno.

Sin una biblioteca de análisis de agentes: se reconocen los navegadores y sistemas comunes por
marcas estables; lo demás sale como «desconocido». El orden importa, porque los agentes se
copian entre sí (Edge y Opera dicen «Chrome», Chrome dice «Safari», iPadOS a veces dice «Mac»).
"""

DESCONOCIDO = "Dispositivo desconocido"

NAVEGADORES: tuple[tuple[str, str], ...] = (
    ("Edg", "Edge"),
    ("OPR/", "Opera"),
    ("SamsungBrowser/", "Samsung Internet"),
    ("FxiOS/", "Firefox"),
    ("Firefox/", "Firefox"),
    ("CriOS/", "Chrome"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
"""(marca en el agente, nombre); gana la primera que aparece."""

SISTEMAS: tuple[tuple[str, str], ...] = (
    ("iPad", "iPad"),
    ("iPhone", "iPhone"),
    ("Android", "Android"),
    ("CrOS", "ChromeOS"),
    ("Windows", "Windows"),
    ("Macintosh", "Mac"),
    ("Linux", "Linux"),
)


def _primera(agente: str, marcas: tuple[tuple[str, str], ...]) -> str | None:
    return next((nombre for marca, nombre in marcas if marca in agente), None)


def describir(agente: str | None) -> str:
    """«Navegador en sistema», solo uno de los dos si falta el otro, o `DESCONOCIDO`."""
    if not agente:
        return DESCONOCIDO
    navegador, sistema = _primera(agente, NAVEGADORES), _primera(agente, SISTEMAS)
    if navegador and sistema:
        return f"{navegador} en {sistema}"
    return navegador or sistema or DESCONOCIDO
