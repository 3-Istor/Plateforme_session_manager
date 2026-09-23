import { useEffect, useState } from "react";

// This remains the identity actually loaded in the current tab, not a value
// fetched from a newer server after an old tab wakes up.
const loadedEntry = (() => {
  const element = document.querySelector<HTMLScriptElement>('script[type="module"][src]');
  return element ? new URL(element.src, window.location.href).pathname : null;
})();

export default function UpdateNotice() {
  const [version, setVersion] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  useEffect(() => {
    if (!loadedEntry?.startsWith("/assets/")) return;
    let disposed = false;
    let active: AbortController | null = null;
    let candidate: string | null = null;
    async function check() {
      if (document.visibilityState === "hidden" || active) return;
      const controller = new AbortController();
      active = controller;
      const timeout = window.setTimeout(() => controller.abort(), 8000);
      try {
        const response = await fetch(`/api/version?check=${Date.now()}`, { cache: "no-store", signal: controller.signal });
        if (!response.ok) return;
        const value = await response.json();
        if (disposed) return;
        if (typeof value.entry !== "string" || !value.entry.startsWith("/assets/") || typeof value.version !== "string") return;
        if (value.entry === loadedEntry) { candidate = null; setVersion(null); return; }
        // Avoid announcing a single transient response during a rolling update.
        if (candidate === value.version) setVersion(value.version);
        candidate = value.version;
      } catch { /* Offline, old backend, or rollout errors must not disrupt forms. */ }
      finally { window.clearTimeout(timeout); active = null; }
    }
    void check();
    const timer = window.setInterval(() => void check(), 15000);
    const wake = () => { void check(); };
    window.addEventListener("focus", wake);
    document.addEventListener("visibilitychange", wake);
    return () => {
      disposed = true; active?.abort(); window.clearInterval(timer);
      window.removeEventListener("focus", wake);
      document.removeEventListener("visibilitychange", wake);
    };
  }, []);

  if (!version || version === dismissed) return null;
  return <aside className="update-notice" role="status" aria-live="polite">
    <div><strong>Une nouvelle version du site est disponible</strong>
      <p>Actualisez pour l’utiliser. Terminez d’abord vos saisies : les informations non envoyées seront perdues.</p></div>
    <button className="btn btn-primary" onClick={() => {
      if (!window.confirm("Appliquer la mise à jour ? Les informations non enregistrées dans cet onglet seront perdues.")) return;
      const url = new URL(window.location.href);
      url.searchParams.set("app_version", version);
      window.location.replace(url.toString());
    }}>Appliquer la mise à jour</button>
    <button className="btn btn-ghost" onClick={() => setDismissed(version)}>Plus tard</button>
  </aside>;
}
