import { useEffect, useState } from "react";
import { AlertTriangle, CalendarDays, Check, ChevronLeft, Clock3, RefreshCw, Users, X } from "lucide-react";
import { api, SCHEDULE_TIMEZONE } from "./api";
import SessionCalendar from "./SessionCalendar";
import type { CalendarDayAvailability, Member, SessionRequest } from "./types";

const dateValue = (iso: string) => {
  const parts = new Intl.DateTimeFormat("en-CA", {timeZone: SCHEDULE_TIMEZONE, year: "numeric", month: "2-digit", day: "2-digit"}).formatToParts(new Date(iso));
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
};
const time = (iso: string) => new Intl.DateTimeFormat("fr-FR", {timeZone: SCHEDULE_TIMEZONE, hour: "2-digit", minute: "2-digit"}).format(new Date(iso));
const noop = () => {};

export default function RequestCalendar({item, members, onClose, onDecision}: {
  item: SessionRequest; members: Member[]; onClose: () => void;
  onDecision: (item: SessionRequest, value: "approved" | "declined") => void;
}) {
  const sessionDay = dateValue(item.start_at);
  const [day, setDay] = useState(sessionDay);
  const [days, setDays] = useState<CalendarDayAvailability[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const participants = item.participants.map(({email}) => members.find(member => member.email === email) || {
    email, name: email, initials: email.slice(0, 2).toUpperCase(), color: "#9380c9",
  });
  const duration = Math.round((+new Date(item.end_at) - +new Date(item.start_at)) / 60000);
  const participantKey = item.participants.map(participant => participant.email).sort().join(",");
  const busy = days.flatMap(value => value.busy).filter(value => +new Date(value.start_at) < +new Date(item.end_at) && +new Date(value.end_at) > +new Date(item.start_at));
  const busyEmails = [...new Set(busy.flatMap(value => value.busy_participant_emails || []))];
  const collectiveBusy = busy.some(value => value.collective_calendar_busy);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setDays([]);
    api.requestCalendar(item.id, day, controller.signal).then(result => {
      if (!controller.signal.aborted) setDays(result.days);
    }).catch(err => {
      if (!controller.signal.aborted) setError((err as Error).message);
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [item.id, item.start_at, item.end_at, item.status, participantKey, day, refresh]);

  const changeWeek = (direction: number) => {
    const next = new Date(`${day}T12:00:00Z`);
    next.setUTCDate(next.getUTCDate() + direction * 7);
    setDay(next.toISOString().slice(0, 10));
  };
  const dateLabel = new Intl.DateTimeFormat("fr-FR", {timeZone: SCHEDULE_TIMEZONE, weekday: "long", day: "numeric", month: "long"}).format(new Date(item.start_at));
  return <section className="request-calendar-review">
    <button className="btn btn-ghost" onClick={onClose}><ChevronLeft size={16} />Retour aux demandes</button>
    <div className="page-title"><div><span className="eyebrow">{item.modifies_request_id ? "MODIFICATION À EXAMINER" : "APERÇU DU PLANNING"}</span><h1>{item.title}</h1><p>La session apparaît dans le calendrier des participants, avec leurs périodes occupées et celles des agendas collectifs.</p></div><span className={`status status-${item.status}`}><span />{item.status === "pending" ? "En attente" : item.status === "approved" ? "Acceptée" : "Refusée"}</span></div>
    {error ? <div className="calendar-review-error" role="alert"><AlertTriangle size={24} /><h2>Impossible de vérifier les agendas</h2><p>{error}</p><button className="btn btn-primary" onClick={() => setRefresh(value => value + 1)}><RefreshCw size={16} />Réessayer</button></div> : <SessionCalendar
      days={days} day={day} minimumDay="" duration={duration} members={participants}
      forced={false} slot={{start_at: item.start_at, end_at: item.end_at, busy_participant_emails: busyEmails, collective_calendar_busy: collectiveBusy}} loading={loading}
      onDayChange={setDay} onWeekChange={changeWeek} onRefresh={() => setRefresh(value => value + 1)}
      onDurationChange={noop} onForceChange={noop} onSelect={noop} onBack={onClose} onContinue={noop}
      readOnly previewTitle={item.title} previewDay={sessionDay}
      reviewSummary={<><span className="eyebrow">{item.modifies_request_id ? "NOUVEAU CRÉNEAU PROPOSÉ" : "SESSION EXAMINÉE"}</span><h3>{item.title}</h3><p className="review-session-date"><CalendarDays size={16} />{dateLabel}</p><p><Clock3 size={16} />{time(item.start_at)}–{time(item.end_at)} · {duration} min</p><p><Users size={16} />Demandée par {item.requester_name}</p><div className="review-participants">{participants.map(member => <span key={member.email}><i style={{background: member.color}} />{member.name}</span>)}</div>
        {item.is_forced && <p className="review-force-note"><AlertTriangle size={16} />Créneau forcé : vérifiez les conflits avant de valider.</p>}
        {item.status === "pending" && (busyEmails.length > 0 || collectiveBusy) && <p className="review-force-note"><AlertTriangle size={16} />Occupé sur ce créneau : {[busyEmails.map(email => members.find(member => member.email === email)?.name || email).join(", "), collectiveBusy ? "agenda collectif" : ""].filter(Boolean).join(" · ")}</p>}
        {item.previous_session && <div className="review-previous"><strong>Avant modification</strong><p>{item.previous_session.title}</p><p>{dateValue(item.previous_session.start_at).split("-").reverse().join("/")} · {time(item.previous_session.start_at)}–{time(item.previous_session.end_at)}</p></div>}
        <p className="review-agenda">{item.agenda}</p><button className="btn btn-ghost full" onClick={() => { setDay(sessionDay); setRefresh(value => value + 1); }}><CalendarDays size={15} />Revenir à la session</button>
        {item.status === "approved" && <p className="review-help">L'événement déjà enregistré dans Google peut également apparaître parmi les périodes occupées.</p>}
        {item.status === "pending" && <div className="review-decision-actions"><button className="btn btn-primary full" onClick={() => onDecision(item, "approved")}><Check size={16} />{item.modifies_request_id ? "Accepter la modification" : "Accepter la session"}</button><button className="btn btn-ghost danger full" onClick={() => onDecision(item, "declined")}><X size={16} />Refuser</button></div>}
        <p className="review-help">Les agendas sont revérifiés lors de l'acceptation. Cet aperçu ne modifie pas la réservation.</p>
      </>}
    />}
  </section>;
}
