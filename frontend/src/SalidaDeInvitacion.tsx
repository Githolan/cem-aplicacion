import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { RUTA_INICIO } from "./enlace";
import { mensajeDeFallaConEnlace } from "./fallas";
import { olvidarEdificio } from "./permisos";
import { salirDeLaCuenta } from "./sesion";

// Las salidas de las páginas de invitación, para que ningún error termine sin un paso siguiente.

/** Sin sesión, el inicio lleva a /entrar: el mismo enlace sirve en los dos casos. */
export function EnlaceAlInicio() {
  return (
    <p>
      <Link to={RUTA_INICIO}>Ir al inicio</Link>
    </p>
  );
}

/** Para quien abrió la invitación con otra cuenta: al salir, la página sigue con el mismo token. */
export function SalirDeEstaCuenta({ alSalir }: { alSalir: () => void }) {
  const cliente = useQueryClient();
  const [enviando, setEnviando] = useState(false);
  const [falla, setFalla] = useState<string | null>(null);

  function salir() {
    setEnviando(true);
    setFalla(null);
    void salirDeLaCuenta(mensajeDeFallaConEnlace).then((mensaje) => {
      setEnviando(false);
      if (mensaje !== null) {
        setFalla(mensaje);
        return;
      }
      olvidarEdificio(cliente);
      alSalir();
    });
  }

  return (
    <>
      {falla !== null && <p className="aviso-error">{falla}</p>}
      <button type="button" className="secundario" disabled={enviando} onClick={salir}>
        Salir de esta cuenta
      </button>
    </>
  );
}
