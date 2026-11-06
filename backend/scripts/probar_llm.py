"""Prueba mínima de la capa `llm`: una llamada corta con las variables `CEM_LLM_*` del proceso.

Muestra solo el texto de la respuesta y el modelo. Nunca la clave.

    python -m scripts.probar_llm
"""

from app.llm import Opciones, complete

MAX_TOKENS = 16
ESPERA_S = 30.0
PEDIDO = "Respondé solo con la palabra: listo"


def main() -> None:
    respuesta = complete(
        [{"role": "user", "content": PEDIDO}], Opciones(max_tokens=MAX_TOKENS, espera_s=ESPERA_S)
    )
    print(f"texto: {respuesta.texto}")
    print(f"modelo: {respuesta.modelo}")


if __name__ == "__main__":
    main()
