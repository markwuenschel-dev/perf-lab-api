import React from "react";
import ReactDOM from "react-dom/client";
import { PerfLabProvider } from "./perflab/PerfLabProvider";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <PerfLabProvider>
      <App />
    </PerfLabProvider>
  </React.StrictMode>,
);
