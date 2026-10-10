import { useEffect, useRef, useState } from "react";
import { AlertTriangle, CalendarDays, ChevronLeft, LoaderCircle, RefreshCw, Save } from "lucide-react";
import { api } from "./api";
import DurationPicker, { validDuration } from "./DurationPicker";
import SessionCalendar from "./SessionCalendar";
import type { CalendarDayAvailability, Member, SessionRequest, Slot, User } from "./types";

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
  const [duration, setDuration] = useState((Date.parse(item.end_at) - Date.parse(item.start_at)) / 60000);
  const [emails, setEmails] = useState(item.participants.map(p => p.email));
  const [force, setForce] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [day, setDay] = useState(parisLocal(item.start_at).slice(0, 10));
  const [days, setDays] = useState<CalendarDayAvailability[]>([]);
  const [calendarLoading, setCalendarLoading] = useState(false);
  const [calendarError, setCalendarError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const detailsRef = useRef<HTMLDivElement>(null);
  const participantsRef = useRef<HTMLFieldSetElement>(null);
  const participantKey = [...emails].sort().join(",");
  const minimumDay = parisLocal(new Date().toISOString()).slice(0, 10);
  const formattedTitle = noProject ? title.trim() : `[${project.trim()}] ${title.trim()}`;
  const validTitle = title.trim().length >= 3 && formattedTitle.length <= 160 && (noProject || Boolean(project.trim() && !/[\[\]\r\n]/.test(project)));
  const selectedMembers = members.filter(member => emails.includes(member.email));
  const selectedSlot: Slot | null = start && end && validDuration(duration) ? {
    start_at: parisISO(start), end_at: parisISO(end),
    ...days.flatMap(value => value.slots).find(value => Date.parse(value.start_at) === Date.parse(parisISO(start)) && Date.parse(value.end_at) === Date.parse(parisISO(end))),
  } : null;

  useEffect(() => {
    const controller = new AbortController();
    setDays([]); setCalendarError("");
    if (!day || !validDuration(duration) || !participantKey) {
      setCalendarLoading(false);
      return () => controller.abort();
    }
    setCalendarLoading(true);
    api.modificationCalendar(item.id, day, duration, participantKey.split(","), controller.signal)
      .then(result => { if (!controller.signal.aborted) setDays(result.days); })
      .catch(err => { if (!controller.signal.aborted) setCalendarError((err as Error).message); })
      .finally(() => { if (!controller.signal.aborted) setCalendarLoading(false); });
    return () => controller.abort();
  }, [item.id, day, duration, participantKey, refresh]);

  function changeStart(value: string) {
    setStart(value);
    if (value) {
      setDay(value.slice(0, 10));
      if (validDuration(duration)) setEnd(parisLocal(new Date(Date.parse(parisISO(value)) + duration * 60000).toISOString()));
    }
  }
  function chooseSlot(slot: Slot, nextDay: string) {
    setStart(parisLocal(slot.start_at)); setEnd(parisLocal(slot.end_at)); setDay(nextDay);
  }
  function changeWeek(direction: number) {
    const value = new Date(`${day}T12:00:00Z`);
    value.setUTCDate(value.getUTCDate() + direction * 7);
    const nextDay = value.toISOString().slice(0, 10);
    setDay(nextDay < minimumDay ? minimumDay : nextDay);
  }
  function periodDuration(nextStart: string, nextEnd: string) {
    return nextStart && nextEnd ? (Date.parse(parisISO(nextEnd)) - Date.parse(parisISO(nextStart))) / 60000 : NaN;
  }
  function changeDuration(minutes: number) {
    setDuration(minutes);
    if (validDuration(minutes) && start) {
      setEnd(parisLocal(new Date(Date.parse(parisISO(start)) + minutes * 60000).toISOString()));
    }
  }
  async function save(event: React.FormEvent) {
    event.preventDefault(); setSaving(true); setError("");
    if (!validDuration(duration)) { setSaving(false); setError("La durée doit être comprise entre 15 minutes et 8 heures, à la minute près."); return; }
    try {
      await api.modifyRequest(item.id, { title, project_name: noProject ? "" : project,
        no_project: noProject, session_type: type, agenda, start_at: parisISO(start), end_at: parisISO(end),
        participant_emails: emails, force, timezone: "Europe/Paris" });
      onSaved();
    } catch (err) { setError((err as Error).message); }
    finally { setSaving(false); }
  }
  return <section className="edit-session-page">
    <button className="btn btn-ghost" disabled={saving} onClick={onClose}><ChevronLeft size={16} />Retour aux sessions</button>
    <div className="page-title"><div><span className="eyebrow">MODIFICATION DE SESSION</span><h1>{item.title}</h1><p>{user.is_manager ? "Vous pouvez proposer une modification pour cette session." : "Modifiez votre demande et soumettez-la au manager."}</p></div></div>
    <div className="edit-current-session"><CalendarDays size={21} /><div><strong>La session actuelle reste inchangée jusqu’à acceptation.</strong><p>{parisLocal(item.start_at).slice(0, 10).split("-").reverse().join("/")} · {parisLocal(item.start_at).slice(11)}–{parisLocal(item.end_at).slice(11)} · Une seule modification en attente.</p></div></div>
    {error && <p className="alert-error" role="alert">{error}</p>}
    <form onSubmit={event => void save(event)}>
      <fieldset disabled={saving} className="edit-session-fieldset">
      <div className="edit-session-layout">
      <div className="panel edit-session-details" ref={detailsRef} tabIndex={-1}>
        <h2>Informations de la session</h2>
        <label className="field-label">Nom du projet<input className="input" value={project} disabled={noProject} required={!noProject} maxLength={80} onChange={e => setProject(e.target.value)} /></label>
        <label><input type="checkbox" checked={noProject} onChange={e => setNoProject(e.target.checked)} /> Cette session ne concerne pas un projet précis</label>
        <label className="field-label">Titre<input className="input" required minLength={3} maxLength={160} value={title} onChange={e => setTitle(e.target.value)} /></label>
        <label className="field-label">Type<input className="input" required minLength={2} maxLength={60} value={type} onChange={e => setType(e.target.value)} /></label>
        <label className="field-label">Ordre du jour<textarea className="input textarea" required minLength={10} maxLength={4000} value={agenda} onChange={e => setAgenda(e.target.value)} /></label>
        <p className="edit-title-preview">{noProject ? "" : `[${project.trim() || "Nom du projet"}] `}<strong>{title.trim() || "Titre de la session"}</strong></p>
        {!validTitle && <p className="form-error">Indiquez un projet sans crochets ou cochez « pas de projet », et un titre d'au moins 3 caractères (160 caractères maximum au total).</p>}
      </div>
      <div className="panel edit-session-schedule">
        <h2>Horaire et participants</h2>
        <label className="field-label">Début · heure de Paris<input className="input" type="datetime-local" required step={900} min={`${minimumDay}T08:00`} value={start} onChange={e => changeStart(e.target.value)} /></label>
        <DurationPicker minutes={duration} onChange={changeDuration} />
        <label className="field-label">Fin · heure de Paris<input className="input" type="datetime-local" required step={60} value={end} onChange={e => { setEnd(e.target.value); setDuration(periodDuration(start, e.target.value)); }} /></label>
        <p>Les disponibilités seront vérifiées à l’envoi puis à l’acceptation. Un déplacement chevauchant l’événement actuel peut être signalé occupé par Google.</p>
        <fieldset className="edit-participants" ref={participantsRef} tabIndex={-1}><legend>Participants</legend>{members.map(member => <label className="field-label" key={member.email}><input type="checkbox" checked={emails.includes(member.email)} onChange={e => setEmails(e.target.checked ? [...emails, member.email] : emails.filter(email => email !== member.email))} /> <i style={{background: member.color}} />{member.name}</label>)}</fieldset>
        <label className="field-label"><input type="checkbox" checked={force} onChange={e => setForce(e.target.checked)} /> Forcer le créneau malgré les conflits (validation du manager requise)</label>
      </div>
      </div>
      <div className="edit-calendar-heading"><span className="eyebrow">CHOISIR LE NOUVEAU CRÉNEAU</span><h2>Le planning des participants</h2><p>Cliquez sur un créneau pour déplacer la session proposée. Les agendas collectifs sont également vérifiés.</p></div>
      {calendarError ? <div className="calendar-review-error" role="alert"><AlertTriangle size={24} /><h3>Impossible de vérifier les agendas</h3><p>{calendarError}</p><button type="button" className="btn btn-ghost" onClick={() => setRefresh(value => value + 1)}><RefreshCw size={16} />Réessayer</button></div>
        : !emails.length || !validDuration(duration) ? <p className="alert-error">Choisissez au moins un participant et une durée entre 15 minutes et 8 heures pour afficher le planning.</p>
        : <SessionCalendar days={days} day={day} minimumDay={minimumDay} duration={duration} members={selectedMembers}
          forced={force} slot={selectedSlot} loading={calendarLoading} previewTitle="Modification proposée" previewDay={start.slice(0, 10)}
          onDayChange={setDay} onWeekChange={changeWeek} onDurationChange={changeDuration} onForceChange={setForce}
          onSelect={chooseSlot} onRefresh={() => setRefresh(value => value + 1)}
          onBack={() => { participantsRef.current?.focus(); participantsRef.current?.scrollIntoView({behavior: "smooth", block: "center"}); }}
          onContinue={() => { detailsRef.current?.focus(); detailsRef.current?.scrollIntoView({behavior: "smooth", block: "start"}); }} />}
      <div className="edit-submit-bar"><p>Seule la proposition est envoyée. Le manager validera ou refusera la modification.</p><button className="btn btn-primary" disabled={saving || !emails.length || !validDuration(duration) || !validTitle}>{saving ? <LoaderCircle size={16} className="spin" /> : <Save size={16} />}{saving ? "Envoi…" : "Soumettre la modification au manager"}</button></div>
      </fieldset>
    </form>
  </section>;
}
