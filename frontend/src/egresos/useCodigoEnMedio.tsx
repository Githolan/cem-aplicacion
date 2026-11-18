import { type ReactNode, useState } from "react";

import { PedirCodigo } from "./PedirCodigo";

// El segundo factor en medio de un acto hecho desde una ventana (informe de fricción, tanda 8): la
// ventana sigue «enviando» mientras la hoja del código está encima, y el acto sabe cómo terminó la
// confirmación. Al confirmar, el acto se repite con la misma `Idempotency-Key`; al cancelar, la
// ventana queda abierta con lo escrito.
//
//   const codigo = useCodigoEnMedio();
//   …dentro de `alEnviar`: if (codigoDe(error) === SEGUNDO_FACTOR_REQUERIDO) {
//     if (!(await codigo.pedir("registrar el pago"))) return "seguir";
//     …repetir el pedido con la misma clave…
//   }
//   …en la pantalla, después de la ventana: {codigo.hoja}

type Pendiente = { acto: string; fin: (confirmado: boolean) => void };

export function useCodigoEnMedio(): { pedir: (acto: string) => Promise<boolean>; hoja: ReactNode } {
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);

  const pedir = (acto: string) =>
    new Promise<boolean>((resolver) => {
      setPendiente({
        acto,
        fin: (confirmado) => {
          setPendiente(null);
          resolver(confirmado);
        },
      });
    });

  const hoja =
    pendiente === null ? null : (
      <PedirCodigo acto={pendiente.acto} alVerificar={() => pendiente.fin(true)} alCancelar={() => pendiente.fin(false)} />
    );

  return { pedir, hoja };
}
