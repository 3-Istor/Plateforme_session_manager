import { useEffect, useState } from "react";
import { api } from "./api";
import type { User } from "./types";

export default function ProfilePage({ user, onSaved }: { user: User; onSaved: () => Promise<void> }) {
  const [first, setFirst] = useState(user.first_name || "");
  const [last, setLast] = useState(user.last_name || "");
  const [requestManager, setRequestManager] = useState(false);
  const [requests, setRequests] = useState<User[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!user.is_manager) return;
    let active = true;
    api.roleRequests().then(rows => { if (active) setRequests(rows); }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [user]);
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setMessage("");
    try { await api.saveProfile(first, last, requestManager); await onSaved(); setRequestManager(false); setMessage("Profil enregistré."); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function decide(email: string, approve: boolean) {
    setBusy(true); setError("");
    try { await api.decideRole(email, approve); setRequests(await api.roleRequests()); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <section>
    <div className="page-title"><div><h1>Mon profil</h1><p>Votre identité et votre rôle dans l’équipe.</p></div></div>
    {error && <p role="alert" className="alert-error">{error}</p>}
    {message && <p role="status">{message}</p>}
    <form className="panel" onSubmit={save}>
      <p>{user.email}</p><p>Rôle actuel : <strong>{user.is_manager ? "Manager" : "Membre"}</strong></p>
      <label className="field-label" htmlFor="first-name">Prénom</label>
      <input className="input" id="first-name" autoComplete="given-name" required maxLength={50} value={first} onChange={e => setFirst(e.target.value)} />
      <label className="field-label" htmlFor="last-name">Nom</label>
      <input className="input" id="last-name" autoComplete="family-name" required maxLength={50} value={last} onChange={e => setLast(e.target.value)} />
      {!user.is_manager && (user.manager_status === "pending" ? <p>Demande de rôle manager en attente de validation.</p> : <p><label><input type="checkbox" checked={requestManager} onChange={e => setRequestManager(e.target.checked)} /> Demander le rôle manager (validation requise)</label>{user.manager_status === "declined" && <span> Votre précédente demande a été refusée.</span>}</p>)}
      <button className="btn btn-primary" disabled={busy || !first.trim() || !last.trim()}>Enregistrer</button>
    </form>
    {user.is_manager && <div className="panel"><h2>Demandes de rôle manager</h2>{requests.length === 0 && <p>Aucune demande en attente.</p>}{requests.map(person => <div key={person.email}><p><strong>{person.name}</strong> · {person.email}</p><button className="btn btn-primary" disabled={busy} onClick={() => void decide(person.email, true)}>Valider</button><button className="btn btn-ghost" disabled={busy} onClick={() => void decide(person.email, false)}>Refuser</button></div>)}</div>}
  </section>;
}
