import { useEffect, useRef } from "react";
import { LoaderCircle, Trash2, X } from "lucide-react";
import type { SessionRequest } from "./types";

export default function DeleteSessionDialog({item, loading, error, onClose, onConfirm}: {
  item: SessionRequest; loading: boolean; error: string; onClose: () => void; onConfirm: () => void;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    cancel.current?.focus();
    return () => previous?.focus();
  }, []);
  return <div className="modal-backdrop" onMouseDown={() => { if (!loading) onClose(); }}>
    <div ref={dialog} className="modal delete-session-modal" role="dialog" aria-modal="true" aria-labelledby="delete-session-title"
      aria-describedby="delete-session-description" onMouseDown={event => event.stopPropagation()} onKeyDown={event => {
        if (event.key === "Escape" && !loading) onClose();
        if (event.key === "Tab") {
          const buttons = [...(dialog.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") || [])];
          const first = buttons[0], last = buttons[buttons.length - 1];
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
      }}>
      <button className="modal-close" aria-label="Fermer" disabled={loading} onClick={onClose}><X /></button>
      <span className="decision-icon declined"><Trash2 /></span>
      <h2 id="delete-session-title">{item.modifies_request_id ? "Retirer cette modification ?" : "Supprimer cette session ?"}</h2>
      <p id="delete-session-description">« {item.title} »</p>
      <p className="delete-session-warning">{item.modifies_request_id
        ? "Cette demande de modification sera supprimée. La session d’origine restera inchangée."
        : "La session et ses demandes de modification seront supprimées définitivement. L’événement Google associé sera annulé et les participants seront informés."}</p>
      {error && <p className="alert-error" role="alert">{error}</p>}
      <div className="modal-actions"><button ref={cancel} className="btn btn-ghost" disabled={loading} onClick={onClose}>Annuler</button>
        <button className="btn btn-danger" disabled={loading} onClick={onConfirm}>{loading ? <LoaderCircle className="spin" size={16} /> : <Trash2 size={16} />}{loading ? "Suppression…" : "Supprimer définitivement"}</button>
      </div>
    </div>
  </div>;
}
