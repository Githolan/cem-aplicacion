"""Credenciales S3 separadas por función (H2c §3.1, repositorio CEM).

Cada clave tiene su prefijo de variables y solo la carga el proceso que la usa:

- `CEM_DOCUMENTOS_S3`: usuario `cem-documentos-acceso(-staging)`, lee y crea sin borrar; lo
  carga `cem-backend`.
- `CEM_DOCUMENTOS_ANALISIS_S3`: usuario `cem-documentos-analisis(-staging)`, lee y borra; lo
  carga `cem-trabajos`.

Por prefijo: `_ENDPOINT`, `_REGION`, `_CLAVE_ID` y `_CLAVE_SECRETA`. El bucket de documentos es
`CEM_DOCUMENTOS_BUCKET`. Falta una variable → error que nombra solo las que faltan.
"""

import os
from dataclasses import dataclass

import boto3
from botocore.config import Config as ConfigBotocore

DOCUMENTOS = "CEM_DOCUMENTOS_S3"
DOCUMENTOS_ANALISIS = "CEM_DOCUMENTOS_ANALISIS_S3"
VARIABLE_BUCKET_DOCUMENTOS = "CEM_DOCUMENTOS_BUCKET"
SUFIJOS = ("ENDPOINT", "REGION", "CLAVE_ID", "CLAVE_SECRETA")


class SinConfiguracion(RuntimeError):
    """Faltan variables de entorno del almacén; el mensaje nombra cuáles, nunca un valor."""


def _leer(nombres: list[str]) -> list[str]:
    faltan = [n for n in nombres if not os.environ.get(n)]
    if faltan:
        raise SinConfiguracion(f"faltan variables de entorno: {', '.join(faltan)}")
    return [os.environ[n] for n in nombres]


@dataclass(frozen=True)
class AlmacenDocumentos:
    bucket: str
    endpoint: str
    region: str
    clave_id: str
    clave_secreta: str

    @classmethod
    def desde_entorno(cls, prefijo: str) -> "AlmacenDocumentos":
        endpoint, region, clave_id, secreta = _leer([f"{prefijo}_{s}" for s in SUFIJOS])
        (bucket,) = _leer([VARIABLE_BUCKET_DOCUMENTOS])
        return cls(bucket, endpoint, region, clave_id, secreta)

    def cliente(self):
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            region_name=self.region,
            aws_access_key_id=self.clave_id,
            aws_secret_access_key=self.clave_secreta,
            config=ConfigBotocore(
                # La API compatible de OCI usa rutas (`/bucket/objeto`), no subdominios.
                s3={"addressing_style": "path"},
                # Las sumas CRC por defecto de boto3 ≥ 1.36 no son parte de la API compatible de
                # OCI; la integridad la da el SHA-256 que guarda la base.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )
