import type { ReactNode } from "react";
import { Link, useLocation } from "react-router";

import { RUTA_ENTRAR } from "./enlace";
import { ENTRAR_DE_NUEVO } from "./fallas";

// La salida de una acción que pide una entrada reciente. `renovar` hace que /entrar no devuelva al
// inicio a quien todavía tiene sesión, y `volver` lleva de regreso a esta pantalla al terminar.

export function EnlaceVolverAEntrar({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  return (
    <Link to={RUTA_ENTRAR} state={{ renovar: true, volver: pathname }}>
      {children}
    </Link>
  );
}

export function AvisoEntrarDeNuevo() {
  return (
    <>
      <p className="aviso-error">{ENTRAR_DE_NUEVO}</p>
      <p>
        <EnlaceVolverAEntrar>Volver a entrar</EnlaceVolverAEntrar>
      </p>
    </>
  );
}
