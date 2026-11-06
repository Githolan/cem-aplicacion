// El nombre de cada facultad, un solo mapa para toda la aplicación: el código no se muestra. Están
// todas las de `nucleo.facultad`; `facultades.test.ts` lo compara con las políticas del contrato.

const NOMBRES: Readonly<Record<string, string>> = {
  "auditoria.ver": "Ver la auditoría del edificio",
  "coeficientes.aprobar": "Aprobar los coeficientes",
  "coeficientes.proponer": "Proponer los coeficientes",
  "contactos.gestionar": "Gestionar los contactos de emergencia",
  "decisiones.registrar": "Registrar decisiones",
  "decisiones.ver": "Ver las decisiones",
  "documentos.cargar": "Cargar documentos",
  "documentos.cargar_operativos": "Cargar actas, presupuestos y mantenimiento",
  "documentos.ver": "Ver los documentos del edificio",
  "egresos.aprobar_orden": "Revisar y decidir órdenes de pago",
  "egresos.ejecutar_pago": "Pagar las órdenes aprobadas",
  "egresos.preparar_orden": "Preparar órdenes de pago",
  "fiscalizacion.ver_panel": "Ver el panel de fiscalización",
  "fondo_reserva.autorizar": "Autorizar el uso del fondo de reserva",
  "fondo_reserva.solicitar": "Pedir el uso del fondo de reserva",
  "invitaciones.crear": "Invitar personas",
  "liquidacion.aprobar": "Aprobar la liquidación",
  "liquidacion.preparar": "Preparar la liquidación",
  "liquidacion.revisar": "Revisar la liquidación",
  "mandatos.registrar": "Dar de alta y de baja mandatos",
  "mandatos.ver": "Ver los mandatos",
  "padron.confirmar_vinculo": "Confirmar vínculos con las unidades",
  "padron.gestionar": "Gestionar el padrón",
  "padron.ver": "Ver el padrón del edificio",
  "solicitud_formal.firmar": "Firmar solicitudes formales",
  "solicitud_formal.preparar": "Preparar solicitudes formales",
};

/** Una facultad más nueva que esta versión de la aplicación tampoco muestra su código. */
const SIN_NOMBRE = "Otra facultad";

export const nombreDeFacultad = (codigo: string) => NOMBRES[codigo] ?? SIN_NOMBRE;

export const CODIGOS_CON_NOMBRE = Object.keys(NOMBRES);
