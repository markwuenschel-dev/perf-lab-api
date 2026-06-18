import React from "react";
import ReactDOM from "react-dom/client";
import { AuthProvider } from "./auth/AuthContext";
import { PerfLabProvider } from "./perflab/PerfLabProvider";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthProvider>
      <PerfLabProvider>
        <App />
      </PerfLabProvider>
    </AuthProvider>
  </React.StrictMode>,
);
