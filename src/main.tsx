import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";

window.addEventListener("message", (event) => {
  if (event.data?.type === "host:set-theme") {
    document.documentElement.classList.toggle(
      "dark",
      event.data.theme === "dark",
    );
  }
});

// Pushing the theme in from the host can race this listener attaching, so
// also actively ask for it once we're definitely ready to receive the reply.
window.parent.postMessage({ type: "iframe:request-theme" }, "*");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
