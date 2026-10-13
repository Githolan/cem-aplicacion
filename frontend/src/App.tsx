import { EstadoServicio } from "./EstadoServicio";

export function App() {
  return (
    <>
      <header className="cabecera">
        <img src="/icono.svg" alt="" width="40" height="40" />
        <span className="marca">Consorcio en Mano</span>
      </header>
      <main id="contenido">
        <h1>Tu consorcio, en tu mano</h1>
        <p>
          Estamos construyendo la aplicación. Pronto vas a poder ver tus expensas, documentos y
          avisos desde acá.
        </p>
        <EstadoServicio />
      </main>
    </>
  );
}
