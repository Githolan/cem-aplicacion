import { forwardRef, type InputHTMLAttributes, useState } from "react";

// Campo de contraseña con un botón para verla mientras se escribe (pedido del propietario,
// 17-10-2026): con letra grande o en el teléfono es fácil equivocarse sin ver lo que se escribe.
// La etiqueta va afuera, con `htmlFor` al `id` del campo, como en el resto de los formularios.
// El botón dice lo que va a hacer ("Mostrar" u "Ocultar"), y su nombre accesible contiene ese
// mismo texto (WCAG 2.5.3); por eso no lleva además `aria-pressed`, que lo contradiría. El nombre
// no repite "contraseña": la etiqueta del campo ya lo dice y así no se confunden al buscarlos.

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & { id: string };

export const CampoContrasena = forwardRef<HTMLInputElement, Props>(function CampoContrasena(
  props,
  ref,
) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="campo-contrasena">
      <input {...props} ref={ref} type={visible ? "text" : "password"} />
      <button
        type="button"
        className="boton-ver"
        aria-controls={props.id}
        aria-label={visible ? "Ocultar lo escrito" : "Mostrar lo escrito"}
        onClick={() => setVisible((v) => !v)}
      >
        {visible ? "Ocultar" : "Mostrar"}
      </button>
    </div>
  );
});
