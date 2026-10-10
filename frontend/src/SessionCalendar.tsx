import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { AlertTriangle, CalendarDays, Check, CheckCircle2, ChevronLeft, ChevronRight, Clock3, LoaderCircle, RefreshCw, ShieldCheck, Users } from "lucide-react";
import type { CalendarDayAvailability, Member, Slot } from "./types";
import "./session-calendar.css";

const timezone = "Europe/Paris";
const hourHeight = 64;
const openingMinute = 8 * 60;
const hours = Array.from({length: 14}, (_, index) => index + 8);
const quarters = Array.from({length: 52}, (_, index) => index);
const time = (iso: string) => new Intl.DateTimeFormat("fr-FR", {timeZone: timezone, hour: "2-digit", minute: "2-digit"}).format(new Date(iso));
const dayDate = (day: string) => new Date(`${day}T12:00:00Z`);
const dateLabel = (day: string) => new Intl.DateTimeFormat("fr-FR", {timeZone: "UTC", weekday: "long", day: "numeric", month: "long"}).format(dayDate(day));
function minute(iso: string) {
  const [hour, minutes] = time(iso).split(":").map(Number);
  return hour * 60 + minutes;
}
export const slotHasConflict = (slot: Slot) => Boolean(slot.busy_participant_emails?.length || slot.collective_calendar_busy);
const position = (slot: Slot): CSSProperties => ({
  top: (minute(slot.start_at) - openingMinute) / 60 * hourHeight,
  height: (minute(slot.end_at) - minute(slot.start_at)) / 60 * hourHeight,
});

type Props = {
  days: CalendarDayAvailability[]; day: string; minimumDay: string; duration: number;
  members: Member[]; forced: boolean; slot: Slot | null; loading: boolean;
  onDayChange: (day: string) => void; onWeekChange: (direction: number) => void;
  onDurationChange: (duration: number) => void; onForceChange: (forced: boolean) => void;
  onSelect: (slot: Slot, day: string) => void; onRefresh: () => void;
  onBack: () => void; onContinue: () => void;
  readOnly?: boolean; previewTitle?: string; previewDay?: string; reviewSummary?: ReactNode;
};

export default function SessionCalendar({ days, day, minimumDay, duration, members, forced, slot, loading,
  onDayChange, onWeekChange, onDurationChange, onForceChange, onSelect, onRefresh, onBack, onContinue,
  readOnly = false, previewTitle = "Votre session", previewDay, reviewSummary }: Props) {
  const [view, setView] = useState<"week" | "day">("week");
  const previewRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const dateInputRef = useRef<HTMLInputElement>(null);
  const chosenDay = days.find(value => value.day === day);
  const displayedDays = view === "day" ? days.filter(value => value.day === day) : days;
  const options = chosenDay?.slots.filter(value => forced || !slotHasConflict(value)) || [];
  const selectedConflict = slot ? slotHasConflict(slot) : false;
  const selectedKey = slot ? `${slot.start_at}/${slot.end_at}` : "";
  const memberName = (email: string) => members.find(value => value.email === email)?.name || email;
  const busyLabel = (value: Slot) => [
    value.busy_participant_emails?.length ? `Occupé${value.busy_participant_emails.length > 1 ? "s" : ""} · ${value.busy_participant_emails.map(memberName).join(", ")}` : "",
    value.collective_calendar_busy ? "Agenda collectif" : "",
  ].filter(Boolean).join(" · ");
  const rangeLabel = days.length ? new Intl.DateTimeFormat("fr-FR", {timeZone: "UTC", day: "numeric", month: "long", year: "numeric"})
    .formatRange(dayDate(days[0].day), dayDate(days[days.length - 1].day)) : dateLabel(day);
  const durations = [...new Set([15, 30, 45, 60, 90, 120, 180, 240, 480, duration])].sort((a, b) => a - b);

  useEffect(() => {
    const preview = previewRef.current;
    const scroller = scrollRef.current;
    if (!preview || !scroller) return;
    // Reveal the selection without unexpectedly scrolling the whole page.
    const top = Number.parseFloat(preview.style.top);
    const height = Number.parseFloat(preview.style.height);
    if (top < scroller.scrollTop + 54 || top + height > scroller.scrollTop + scroller.clientHeight - 12) {
      scroller.scrollTop = Math.max(0, top - 100);
    }
    const bounds = preview.getBoundingClientRect();
    const viewport = scroller.getBoundingClientRect();
    if (bounds.left < viewport.left + 52) scroller.scrollLeft -= viewport.left + 52 - bounds.left;
    else if (bounds.right > viewport.right) scroller.scrollLeft += bounds.right - viewport.right;
  }, [selectedKey, view, loading, previewDay, days[0]?.day]);

  return <section className={`session-calendar ${forced ? "is-forced" : ""} ${readOnly ? "calendar-review" : ""}`} aria-busy={loading}>
    <div className="session-calendar-heading">
      <span className="calendar-zone"><Clock3 size={14} /> Heure de Paris · 08:00–21:00</span>
    </div>
    <div className="session-calendar-toolbar">
      <div className="calendar-date-controls">
        <button type="button" aria-label="Semaine précédente" disabled={loading || !days.length || days[0].day <= minimumDay} onClick={() => onWeekChange(-1)}><ChevronLeft size={17} /></button>
        <strong>{rangeLabel}</strong>
        <button type="button" aria-label="Semaine suivante" disabled={loading} onClick={() => onWeekChange(1)}><ChevronRight size={17} /></button>
      </div>
      <label className="calendar-date-picker" onClick={() => { if (loading) return; try { dateInputRef.current?.showPicker(); } catch { dateInputRef.current?.focus(); } }}><span className="sr-only">Choisir la date</span><CalendarDays size={15} /><span aria-hidden="true">{day.split("-").reverse().join("/")}</span><input ref={dateInputRef} aria-label="Choisir la date" type="date" lang="fr-FR" min={minimumDay} value={day} disabled={loading} onChange={event => { if (event.target.value >= minimumDay) onDayChange(event.target.value); }} /></label>
      {!readOnly && <label className="calendar-duration"><Clock3 size={15} /><select aria-label="Durée de la session" value={duration} disabled={loading} onChange={event => onDurationChange(Number(event.target.value))}>
        {durations.map(value => <option key={value} value={value}>{value} min</option>)}
      </select></label>}
      <button className="calendar-refresh" type="button" aria-label="Actualiser les disponibilités" disabled={loading} onClick={onRefresh}><RefreshCw size={16} /></button>
      <div className="calendar-view-controls" aria-label="Vue du calendrier"><button type="button" aria-pressed={view === "week"} className={view === "week" ? "active" : ""} onClick={() => setView("week")}>Semaine</button><button type="button" aria-pressed={view === "day"} className={view === "day" ? "active" : ""} onClick={() => setView("day")}>Jour</button></div>
    </div>
    <div className="calendar-filter-row">
      <div className="calendar-members">{members.map(member => <span key={member.email} title={member.name}><i style={{background: member.color}} />{member.name}</span>)}</div>
      {!readOnly && <div className="calendar-mode-controls"><button type="button" aria-pressed={!forced} className={!forced ? "active" : ""} disabled={loading} onClick={() => onForceChange(false)}><ShieldCheck size={15} /> Créneaux libres</button><button type="button" aria-pressed={forced} className={forced ? "force active" : "force"} disabled={loading} onClick={() => onForceChange(true)}><AlertTriangle size={15} /> Forcer un créneau</button></div>}
    </div>
    {forced && !readOnly && <p className="calendar-force-note"><AlertTriangle size={16} /> Les créneaux occupés deviennent sélectionnables. Le manager devra valider les conflits.</p>}
    <div className="calendar-mobile-days" aria-label="Jour à afficher">{days.map(value => <button key={value.day} type="button" disabled={loading || value.day < minimumDay} aria-pressed={value.day === day} className={value.day === day ? "active" : ""} onClick={() => onDayChange(value.day)}>
      <span>{new Intl.DateTimeFormat("fr-FR", {timeZone: "UTC", weekday: "short"}).format(dayDate(value.day))}</span><strong>{dayDate(value.day).getUTCDate()}</strong>
    </button>)}</div>
    {loading ? <div className="calendar-loading" role="status"><LoaderCircle className="spin" size={26} /><strong>Vérification des agendas…</strong><span>Chargement des disponibilités de la semaine.</span></div> : <div className="calendar-booking-layout">
      <div className="calendar-scroll" ref={scrollRef} tabIndex={0} role="region" aria-label="Calendrier des disponibilités, défilement horizontal et vertical possible">
        <div className={`calendar-time-grid view-${view}`} style={{"--calendar-columns": displayedDays.length} as CSSProperties}>
          <div className="calendar-hours-head">Paris</div>
          {displayedDays.map(value => <button key={`head-${value.day}`} type="button" disabled={value.day < minimumDay} className={`calendar-day-head ${value.day === day ? "active" : ""}`} aria-pressed={value.day === day} onClick={() => onDayChange(value.day)}>
            <span>{new Intl.DateTimeFormat("fr-FR", {timeZone: "UTC", weekday: "short"}).format(dayDate(value.day))}</span><strong>{dayDate(value.day).getUTCDate()}</strong>
          </button>)}
          <div className="calendar-hours" style={{height: 13 * hourHeight}}>{hours.map(hour => <span key={hour} style={{top: (hour - 8) * hourHeight}}>{String(hour).padStart(2, "0")}:00</span>)}</div>
          {displayedDays.map(value => {
            const canChooseDay = value.day >= minimumDay;
            const starts = new Map(value.slots.map(value => [minute(value.start_at), value]));
            return <div key={value.day} className={`calendar-day-column ${value.day === day ? "active" : ""} ${canChooseDay ? "" : "past"}`} style={{height: 13 * hourHeight}}>
              {quarters.map(index => {
                const candidate = starts.get(openingMinute + index * 15);
                const conflict = candidate ? slotHasConflict(candidate) : false;
                if (!candidate || !canChooseDay || (!forced && conflict)) return null;
                if (readOnly) return !conflict ? <div key={candidate.start_at} className="calendar-time-choice free" style={{top: index * hourHeight / 4, height: hourHeight / 4}} aria-hidden="true" /> : null;
                return <button type="button" key={candidate.start_at} style={{top: index * hourHeight / 4, height: hourHeight / 4}}
                  className={`calendar-time-choice ${conflict ? "conflict" : "free"}`}
                  aria-label={`Choisir ${dateLabel(value.day)}, ${time(candidate.start_at)}–${time(candidate.end_at)}${conflict ? `, ${busyLabel(candidate)}` : ", tout le monde est libre"}`}
                  title={`${time(candidate.start_at)}–${time(candidate.end_at)} · ${conflict ? busyLabel(candidate) : "Tout le monde est libre"}`}
                  onClick={() => onSelect(candidate, value.day)} />;
              })}
              {value.busy.map((busy, index) => {
                const color = busy.busy_participant_emails?.length === 1 && !busy.collective_calendar_busy
                  ? members.find(member => member.email === busy.busy_participant_emails![0])?.color || "#9380c9" : "#9380c9";
                return <div key={`${busy.start_at}-${index}`} className={`calendar-busy-block ${minute(busy.end_at) - minute(busy.start_at) < 30 ? "short" : ""}`} style={{...position(busy), "--busy-color": color} as CSSProperties}
                  role="note" tabIndex={forced ? undefined : 0} aria-label={`${time(busy.start_at)}–${time(busy.end_at)} · ${busyLabel(busy)}`}
                  title={`${time(busy.start_at)}–${time(busy.end_at)} · ${busyLabel(busy)}`}>
                  <small>{time(busy.start_at)}–{time(busy.end_at)}</small><strong>{busyLabel(busy)}</strong>
                </div>;
              })}
              {slot && value.day === (previewDay || day) && <div ref={previewRef} className={`calendar-session-preview ${selectedConflict ? "conflict" : ""} ${duration < 45 ? "short" : ""}`} style={position(slot)} title={`${previewTitle} · ${time(slot.start_at)}–${time(slot.end_at)}`}>
                <strong>{previewTitle}</strong><span>{time(slot.start_at)}–{time(slot.end_at)}</span><small>{selectedConflict ? <AlertTriangle size={12} /> : <CheckCircle2 size={12} />}{readOnly ? "Position de la session" : selectedConflict ? "Conflits à valider" : "Tout le monde est libre"}</small>
              </div>}
            </div>;
          })}
        </div>
      </div>
      {readOnly ? <aside className="calendar-slot-panel calendar-review-summary">{reviewSummary}</aside> : <aside className="calendar-slot-panel">
        <h3>{forced ? "Tous les créneaux" : "Créneaux libres"}</h3><p>{dateLabel(day)} · {duration} min</p>
        <div className="calendar-slot-options" role="group" aria-label="Créneaux proposés">
          {options.map(option => <button type="button" key={option.start_at} aria-pressed={slot?.start_at === option.start_at} className={`${slot?.start_at === option.start_at ? "selected" : ""} ${slotHasConflict(option) ? "conflict" : ""}`} onClick={() => onSelect(option, day)}>
            <span><strong>{time(option.start_at)}–{time(option.end_at)}</strong>{forced && <small>{slotHasConflict(option) ? busyLabel(option) : "Tout le monde est libre"}</small>}</span><i>{slot?.start_at === option.start_at && <Check size={12} />}</i>
          </button>)}
          {!options.length && <div className="calendar-no-slots"><CalendarDays size={24} /><strong>Aucun créneau {forced ? "dans les horaires de travail" : "libre"}</strong><span>Essayez une autre date ou une durée plus courte.</span></div>}
        </div>
        <div className="calendar-selection-summary" aria-live="polite">
          <strong>Créneau sélectionné</strong>
          {slot ? <div className={selectedConflict ? "has-conflict" : ""}><p><CalendarDays size={16} /><b>{time(slot.start_at)}–{time(slot.end_at)}</b></p><p className="selected-slot-date">{new Intl.DateTimeFormat("fr-FR", {timeZone: timezone, day: "numeric", month: "long"}).format(new Date(slot.start_at))}</p><p><Users size={16} />{members.length} participants</p><p>{selectedConflict ? <AlertTriangle size={16} /> : <CheckCircle2 size={16} />}{selectedConflict ? busyLabel(slot) : "Aucun conflit"}</p></div> : <p className="calendar-pick-hint">Cliquez sur un horaire libre dans le calendrier ou dans la liste.</p>}
        </div>
        <button type="button" className={`btn ${selectedConflict ? "btn-force" : "btn-primary"} full`} disabled={!slot} onClick={onContinue}>{selectedConflict ? "Continuer malgré les conflits" : "Continuer"}<ChevronRight size={16} /></button>
      </aside>}
    </div>}
    <div className="calendar-legend"><span><i className="busy" />Période occupée</span><span><i className="free" />Tout le monde disponible</span><span><i className="preview" />{readOnly ? "Session examinée" : "Votre session"}</span><p><ShieldCheck size={13} />Seules les périodes occupées sont affichées, jamais les détails des événements.</p></div>
    {!readOnly && <div className="calendar-bottom"><button type="button" className="btn btn-ghost" disabled={loading} onClick={onBack}><ChevronLeft size={15} />Modifier l’équipe ou la durée personnalisée</button></div>}
  </section>;
}
