import { Link, Route, Routes } from "react-router";

import { EstadoServicio } from "./EstadoServicio";

function Inicio() {
  return (
    <>
      <h1>Tu consorcio, en tu mano</h1>
      <p>
        Estamos construyendo la aplicación. Pronto vas a poder ver tus expensas, documentos y
        avisos desde acá.
      </p>
      <EstadoServicio />
    </>
  );
}

function NoEncontrada() {
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
      </header>
      <main id="contenido">
        <Routes>
          <Route path="/" element={<Inicio />} />
          <Route path="*" element={<NoEncontrada />} />
        </Routes>
      </main>
    </>
  );
}
