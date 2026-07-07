import React from "react";
import ReactDOM from "react-dom/client";
import { ThemeProvider } from "next-themes";
import { AuthProvider } from "./auth/AuthContext";
import { PerfLabProvider } from "./perflab/PerfLabProvider";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {/* class strategy → toggles `.dark` on <html>; defaults to dark (the app's
        historical single theme) so existing users see no change. The generated
        tokens switch every surface/text/status color per mode. */}
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} disableTransitionOnChange>
      <AuthProvider>
        <PerfLabProvider>
          <App />
        </PerfLabProvider>
      </AuthProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
