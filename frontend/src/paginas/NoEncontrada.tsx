import { Link } from "react-router";

export function NoEncontrada() {
  return (
    <main className="pagina">
      <h1>No encontramos esa página</h1>
      <p>
        <Link to="/">Volver al inicio</Link>
      </p>
    </main>
  );
}
