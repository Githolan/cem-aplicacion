import { useEffect } from "react";
import { Link, Route, Routes } from "react-router";

import { Cuenta, RUTA_CUENTA } from "./Cuenta";
import { RUTA_ENTRAR, RUTA_INVITACION } from "./enlace";
import { Entrar } from "./Entrar";
import { EstadoServicio } from "./EstadoServicio";
import { EnlaceInstalar, Instalar } from "./Instalar";
import { Invitacion } from "./Invitacion";
import { Invitar, RUTA_INVITACIONES } from "./Invitar";
import { ModoLectura } from "./ModoLectura";
import { RUTA_SEGUNDO_FACTOR, SegundoFactor } from "./SegundoFactor";
import { useVersion } from "./version";

function Inicio() {
  return (
    <>
      <h1>Tu consorcio, en tu mano</h1>
      <p>
        Estamos construyendo la aplicación. Pronto vas a poder ver tus expensas, documentos y
        avisos desde acá.
      </p>
      <p>
        <Link to={RUTA_CUENTA}>Mi cuenta</Link>
      </p>
      <p>
        <Link to={RUTA_INVITACIONES}>Invitar a una persona</Link>
      </p>
      <EnlaceInstalar />
      <EstadoServicio />
    </>
  );
}

// Una ruta que esta versión no conoce puede ser de una versión más nueva (un enlace del correo,
// abierto con la aplicación vieja en caché). Si hay una versión nueva esperando, se aplica sola en
// la misma URL completa, en vez de dejar a la persona en "No encontramos". En las rutas conocidas
// la versión nueva sigue esperando el botón del aviso (documento 13, §14.4).
function NoEncontrada() {
  const { hayVersionNueva, actualizar } = useVersion();
  useEffect(() => {
    if (hayVersionNueva) actualizar();
  }, [hayVersionNueva, actualizar]);

  if (hayVersionNueva) {
    return (
      <>
        <h1>Actualizando la aplicación</h1>
        <p role="status" className="tarjeta">
          Cargando la versión nueva…
        </p>
      </>
    );
  }
  return (
    <>
      <h1>No encontramos esa página</h1>
      <p>
        <Link to="/">Volver al inicio</Link>
      </p>
    </>
  );
}

export function App() {
  return (
    <>
      <header className="cabecera">
        <img src="/icono.svg" alt="" width="40" height="40" />
        <span className="marca">Consorcio en Mano</span>
        <ModoLectura />
      </header>
      <main id="contenido">
        <Routes>
          <Route path="/" element={<Inicio />} />
          <Route path="/instalar" element={<Instalar />} />
          <Route path={RUTA_ENTRAR} element={<Entrar />} />
          <Route path={RUTA_INVITACION} element={<Invitacion />} />
          <Route path={RUTA_CUENTA} element={<Cuenta />} />
          <Route path={RUTA_INVITACIONES} element={<Invitar />} />
          <Route path={RUTA_SEGUNDO_FACTOR} element={<SegundoFactor />} />
          <Route path="*" element={<NoEncontrada />} />
        </Routes>
      </main>
    </>
  );
}
