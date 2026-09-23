import { useState } from "react";
import { api } from "./api";
import type { Member, SessionRequest, User } from "./types";

function parisLocal(iso: string) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Paris", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(iso)).map(p => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

function parisISO(value: string) {
  // Session hours are 08:00–21:00: noon has the same Paris offset, even on DST days.
  const offset = new Intl.DateTimeFormat("en", { timeZone: "Europe/Paris", timeZoneName: "shortOffset" })
    .formatToParts(new Date(`${value.slice(0, 10)}T12:00:00Z`)).find(p => p.type === "timeZoneName")!.value;
  const hours = Number(offset.replace("GMT", ""));
  return `${value}:00${hours >= 0 ? "+" : "-"}${String(Math.abs(hours)).padStart(2, "0")}:00`;
}

export default function EditSession({ item, user, members, onClose, onSaved }: {
  item: SessionRequest; user: User; members: Member[]; onClose: () => void; onSaved: () => void;
}) {
  const match = item.title.match(/^\[([^\]]+)\] (.*)$/);
  const [title, setTitle] = useState(match ? match[2] : item.title);
  const [project, setProject] = useState(match ? match[1] : "");
  const [noProject, setNoProject] = useState(!match);
  const [type, setType] = useState(item.session_type);
  const [agenda, setAgenda] = useState(item.agenda);
  const [start, setStart] = useState(parisLocal(item.start_at));
  const [end, setEnd] = useState(parisLocal(item.end_at));
  const [emails, setEmails] = useState(item.participants.map(p => p.email));
  const [force, setForce] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  async function save(event: React.FormEvent) {
    event.preventDefault(); setSaving(true); setError("");
    try {
      await api.modifyRequest(item.id, { title, project_name: noProject ? "" : project,
        no_project: noProject, session_type: type, agenda, start_at: parisISO(start), end_at: parisISO(end),
        participant_emails: emails, force, timezone: "Europe/Paris" });
      onSaved();
    } catch (err) { setError((err as Error).message); }
    finally { setSaving(false); }
  }
  return <section className="panel">
    <button className="btn btn-ghost" disabled={saving} onClick={onClose}>← Retour aux sessions</button>
    <h1>Modifier la session #{item.id}</h1>
    <p>La session actuelle reste inchangée jusqu’à acceptation par un manager. Une seule modification peut être en attente.</p>
    {error && <p className="alert-error" role="alert">{error}</p>}
    <form onSubmit={event => void save(event)}>
      <fieldset disabled={saving} style={{ border: 0, padding: 0 }}>
        <label className="field-label">Nom du projet<input className="input" value={project} disabled={noProject} required={!noProject} maxLength={80} onChange={e => setProject(e.target.value)} /></label>
        <label><input type="checkbox" checked={noProject} onChange={e => setNoProject(e.target.checked)} /> Cette session ne concerne pas un projet précis</label>
        <label className="field-label">Titre<input className="input" required minLength={3} maxLength={160} value={title} onChange={e => setTitle(e.target.value)} /></label>
        <label className="field-label">Type<input className="input" required minLength={2} maxLength={60} value={type} onChange={e => setType(e.target.value)} /></label>
        <label className="field-label">Ordre du jour<textarea className="input textarea" required minLength={10} maxLength={4000} value={agenda} onChange={e => setAgenda(e.target.value)} /></label>
        <label className="field-label">Début · heure de Paris<input className="input" type="datetime-local" required step={1800} value={start} onChange={e => setStart(e.target.value)} /></label>
        <label className="field-label">Fin · heure de Paris<input className="input" type="datetime-local" required step={1800} value={end} onChange={e => setEnd(e.target.value)} /></label>
        <p>Les disponibilités seront vérifiées à l’envoi puis à l’acceptation. Un déplacement chevauchant l’événement actuel peut être signalé occupé par Google.</p>
        <fieldset><legend>Participants</legend>{members.map(member => <label className="field-label" key={member.email}><input type="checkbox" checked={emails.includes(member.email)} onChange={e => setEmails(e.target.checked ? [...emails, member.email] : emails.filter(email => email !== member.email))} /> {member.name}</label>)}</fieldset>
        {user.is_manager && <label className="field-label"><input type="checkbox" checked={force} onChange={e => setForce(e.target.checked)} /> Forcer le créneau malgré les conflits (ils seront affichés avant acceptation)</label>}
        <button className="btn btn-primary" disabled={saving || !emails.length}> {saving ? "Envoi…" : "Soumettre la modification au manager"}</button>
      </fieldset>
    </form>
  </section>;
}
