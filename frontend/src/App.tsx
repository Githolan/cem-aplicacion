import { Route, Routes } from "react-router";
import { Inicio } from "./paginas/Inicio";
import { NoEncontrada } from "./paginas/NoEncontrada";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Inicio />} />
      <Route path="*" element={<NoEncontrada />} />
    </Routes>
  );
}
