import React from "react";
import { createRoot } from "react-dom/client";
import EnterpriseConsole from "./ui/console.jsx";

const rootEl = document.getElementById("root");
if (rootEl) {
  const root = createRoot(rootEl);
  root.render(<EnterpriseConsole />);
}
