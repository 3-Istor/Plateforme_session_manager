import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, Bell, CalendarDays, Check, CheckCircle2, ChevronLeft, ChevronRight,
  Clock3, Inbox, LayoutDashboard, ListFilter, LoaderCircle, LogOut, Menu, Plus, Save,
  ShieldCheck, Sparkles, Trophy, Users, Video, X, XCircle,
} from "lucide-react";
import ProfilePage from "./ProfilePage";
import { ApiError, SCHEDULE_TIMEZONE, api, getDemoUser, setDemoUser } from "./api";
import type { AppConfig, CalendarStatus, LatenessEntry, Member, Notification, SessionRequest, Slot, User } from "./types";

declare global {
  interface Window {
    google?: { accounts: { id: {
      initialize: (options: { client_id: string; callback: (result: { credential: string }) => void }) => void;
      renderButton: (element: HTMLElement, options: Record<string, string>) => void;
      disableAutoSelect: () => void;
    } } };
  }
}

type View = "dashboard" | "new" | "requests" | "lateness" | "profile";
const SESSION_TYPES = ["Team building", "Session de travail", "Rétro", "Dry run", "Soutenance", "Autre"] as const;
const scheduleDate = (date: Date) => {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: SCHEDULE_TIMEZONE, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date);
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
};
const addDateValue = (value: string, amount: number) => { const date = new Date(`${value}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + amount); return date.toISOString().slice(0, 10); };
const formatTime = (iso: string) => new Intl.DateTimeFormat("fr-FR", { timeZone: SCHEDULE_TIMEZONE, hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
const formatFrenchInputDate = (value: string) => { const [year, month, day] = value.split("-"); return year && month && day ? `${day}/${month}/${year}` : "JJ/MM/AAAA"; };
const formatDate = (iso: string, compact = false) => new Intl.DateTimeFormat("fr-FR", compact ? { timeZone: SCHEDULE_TIMEZONE, day: "numeric", month: "short" } : { timeZone: SCHEDULE_TIMEZONE, weekday: "long", day: "numeric", month: "long" }).format(new Date(iso));
const formatDateTime = (iso: string) => new Intl.DateTimeFormat("fr-FR", { timeZone: SCHEDULE_TIMEZONE, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));

function Avatar({ member, size = "md" }: { member: Member; size?: "sm" | "md" }) {
  return <span className={`avatar avatar-${size}`} style={{ background: member.color }} title={member.name}>{member.initials}</span>;
}

function StatusBadge({ status }: { status: SessionRequest["status"] }) {
  const labels = { pending: "En attente", approved: "Acceptée", declined: "Refusée" };
  return <span className={`status status-${status}`}><span />{labels[status]}</span>;
}

function GoogleLogin({ config, onLogin }: { config: AppConfig; onLogin: () => Promise<void> }) {
  const [loginError, setLoginError] = useState("");
  useEffect(() => {
    if (!config.google_client_id) return;
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.onload = () => {
      window.google?.accounts.id.initialize({ client_id: config.google_client_id!, callback: async ({ credential }) => {
        setLoginError("");
        try { await api.googleLogin(credential); await onLogin(); } catch (error) { setLoginError((error as Error).message); }
      } });
      const element = document.getElementById("google-signin");
      if (element) window.google?.accounts.id.renderButton(element, { theme: "outline", size: "large", shape: "pill", text: "continue_with" });
    };
    document.head.appendChild(script);
    return () => script.remove();
  }, [config.google_client_id, onLogin]);
  return <div className="login-screen"><div className="login-glow" /><div className="login-card">
    <img className="brand-logo brand-logo-login" src="/3istor-logo.png" alt="Logo 3istor SIGL" />
    <span className="eyebrow">ESPACE ÉQUIPE</span><h1>Connexion</h1><p>Plateforme de demande de sessions de l'équipe 3istor.</p>
    <div id="google-signin" className="google-signin" />{loginError && <p className="form-error">{loginError}</p>}
    {!config.google_client_id && <p className="form-error">Ajoutez GOOGLE_CLIENT_ID dans le fichier .env.</p>}
    <div className="login-note"><ShieldCheck size={16} /> Connexion sécurisée avec Google</div>
  </div></div>;
}

function RequestCard({ item, members, manager, onDecision }: {
  item: SessionRequest; members: Member[]; manager: boolean;
  onDecision: (item: SessionRequest, decision: "approved" | "declined") => void;
}) {
  const participants = item.participants.map(({ email }) => members.find((member) => member.email === email)).filter(Boolean) as Member[];
  const busyNames = (item.busy_participant_emails || []).map((email) => members.find((member) => member.email === email)?.name || email);
  return <article className={`request-card ${item.is_forced ? "request-forced" : ""}`}>
    <div className="request-date"><strong>{new Intl.DateTimeFormat("fr-FR", { timeZone: SCHEDULE_TIMEZONE, day: "numeric" }).format(new Date(item.start_at))}</strong><span>{new Intl.DateTimeFormat("fr-FR", { timeZone: SCHEDULE_TIMEZONE, month: "short" }).format(new Date(item.start_at))}</span></div>
    <div className="request-main"><div className="request-title-row"><div><span className="request-type">{item.session_type}</span><h3>{item.title}</h3></div><div className="request-badges"><StatusBadge status={item.status} />{item.is_forced && <span className="force-badge"><AlertTriangle size={12} /> Créneau forcé</span>}</div></div>
      <div className="request-meta"><span><Clock3 size={15} />{formatTime(item.start_at)}–{formatTime(item.end_at)}</span><span><Users size={15} />{participants.length} participants</span><span>Demandée par {item.requester_name}</span></div>
      {manager && item.is_forced && (busyNames.length > 0 || item.collective_calendar_busy) && <p className="forced-conflicts"><AlertTriangle size={14} /> Conflit{busyNames.length + Number(item.collective_calendar_busy) > 1 ? "s" : ""} au moment du choix : {[busyNames.length ? busyNames.join(", ") : "", item.collective_calendar_busy ? "agenda collectif" : ""].filter(Boolean).join(" · ")}</p>}
      <p className="agenda-preview">{item.agenda}</p><div className="card-footer"><div className="avatar-stack">{participants.slice(0, 5).map((member) => <Avatar key={member.email} member={member} size="sm" />)}</div>
      {manager && item.status === "pending" && <div className="decision-actions"><button className="btn btn-ghost danger" onClick={() => onDecision(item, "declined")}><X size={16} /> Refuser</button><button className="btn btn-dark small" onClick={() => onDecision(item, "approved")}><Check size={16} /> Accepter</button></div>}{item.manager_note && <span className="manager-note">Note : {item.manager_note}</span>}</div>
    </div>
  </article>;
}

function NewSession({ members, user, calendarConnected, connectedEmails, demoMode, onCreated, onCancel }: {
  members: Member[]; user: User; calendarConnected: boolean; connectedEmails: string[]; demoMode: boolean;
  onCreated: (item: SessionRequest) => void; onCancel: () => void;
}) {
  const minimumDay = addDateValue(scheduleDate(new Date()), 1);
  const [step, setStep] = useState(1);
  const [mode, setMode] = useState<"all" | "custom">("all");
  const [selected, setSelected] = useState<string[]>(members.map((member) => member.email));
  const [duration, setDuration] = useState(60);
  const [day, setDay] = useState(minimumDay);
  const [slots, setSlots] = useState<Slot[]>([]);
  const [slot, setSlot] = useState<Slot | null>(null);
  const [forceMode, setForceMode] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [details, setDetails] = useState({ title: "", session_type: "Session de travail", agenda: "" });
  const [customSessionType, setCustomSessionType] = useState("");
  const dateInputRef = useRef<HTMLInputElement>(null);
  const requestRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => { requestRef.current += 1; abortRef.current?.abort(); }, []);
  const week = useMemo(() => Array.from({ length: 7 }, (_, index) => addDateValue(day, index - 3)), [day]);
  const chosenMembers = members.filter((member) => selected.includes(member.email));
  const busyNamesFor = (item: Slot) => (item.busy_participant_emails || []).map((email) => members.find((member) => member.email === email)?.name || email);
  const toggleMember = (email: string) => { if (email !== user.email) setSelected((current) => current.includes(email) ? current.filter((item) => item !== email) : [...current, email]); };
  const openDatePicker = () => { const input = dateInputRef.current; if (!input) return; try { input.showPicker(); } catch { input.focus(); input.click(); } };

  const loadSlots = async (targetDay: string, forced: boolean, moveToSlots = false) => {
    const id = ++requestRef.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setDay(targetDay); setSlot(null); setSlots([]); setError(""); setLoading(true);
    try {
      const result = forced ? await api.forcedAvailability(targetDay, duration, selected, controller.signal) : await api.availability(targetDay, duration, selected, controller.signal);
      if (requestRef.current !== id) return;
      setSlots(forced ? result : result.filter((item) => !(item.busy_participant_emails?.length || item.collective_calendar_busy)));
      if (moveToSlots) setStep(2);
    } catch (err) {
      if (requestRef.current === id && (err as Error).name !== "AbortError") setError((err as Error).message);
    } finally { if (requestRef.current === id) setLoading(false); }
  };
  const findSlots = async (forced: boolean) => { if (forced && !user.is_manager) return; setForceMode(forced); await loadSlots(day, forced, true); };
  const changeDay = (nextDay: string) => { if (nextDay >= minimumDay) void loadSlots(nextDay, forceMode); };
  const changeWeek = (amount: number) => { const next = addDateValue(day, amount); changeDay(next < minimumDay ? minimumDay : next); };
  const submit = async () => {
    if (!slot) return;
    setError(""); setLoading(true);
    try {
      const sessionType = details.session_type === "Autre" ? customSessionType.trim() : details.session_type;
      const created = await api.createRequest({ ...details, session_type: sessionType, start_at: slot.start_at, end_at: slot.end_at, participant_emails: selected, force: forceMode, timezone: SCHEDULE_TIMEZONE });
      setStep(4); onCreated(created);
    } catch (err) {
      const message = (err as Error).message;
      if (!forceMode && err instanceof ApiError && err.status === 409) {
        setStep(2); setSlot(null);
        await loadSlots(day, false);
      }
      setError(message);
    } finally { setLoading(false); }
  };

  if (step === 4) return <div className="success-panel"><div className={`success-icon ${forceMode ? "forced" : ""}`}>{forceMode ? <AlertTriangle size={34} /> : <CheckCircle2 size={34} />}</div><span className="eyebrow">{forceMode ? "DEMANDE FORCÉE ENVOYÉE" : "DEMANDE ENVOYÉE"}</span><h2>C'est parti !</h2><p>{forceMode ? "Le créneau forcé a été enregistré avec ses conflits de disponibilité." : "Votre demande a bien été transmise au manager. Vous serez notifié dès qu'elle sera traitée."}</p><div className="success-summary"><CalendarDays size={20} /><div><strong>{formatDate(slot!.start_at)}</strong><span>{formatTime(slot!.start_at)}–{formatTime(slot!.end_at)} · {chosenMembers.length} participants</span></div></div><button className="btn btn-primary" onClick={onCancel}>Retour au tableau de bord</button></div>;

  const selectedBusyNames = slot ? busyNamesFor(slot) : [];
  const selectedHasConflict = Boolean(selectedBusyNames.length || slot?.collective_calendar_busy);
  return <section className="wizard-page">
    <div className="page-heading"><div><button className="back-link" onClick={step === 1 ? onCancel : () => setStep(step - 1)}><ChevronLeft size={17} /> Retour</button><h1>Nouvelle session</h1><p>Organisez un temps de travail avec votre équipe.</p></div><div className="steps">{[1, 2, 3].map((number) => <div key={number} className={number <= step ? "active" : ""}><span>{number < step ? <Check size={13} /> : number}</span><label>{["Équipe", "Créneau", "Détails"][number - 1]}</label></div>)}</div></div>
    {error && <div className="alert-error" role="alert"><XCircle size={18} />{error}</div>}
    {step === 1 && <div className="wizard-grid">
      <div className="panel"><div className="panel-head"><span className="icon-box"><Users size={20} /></span><div><h2>Qui participe ?</h2><p>Sélectionnez les membres concernés.</p></div></div><div className="segmented"><button className={mode === "all" ? "active" : ""} onClick={() => { setMode("all"); setSelected(members.map((member) => member.email)); }}><Users size={17} /> Toute l'équipe</button><button className={mode === "custom" ? "active" : ""} onClick={() => { setMode("custom"); setSelected([user.email]); }}><ListFilter size={17} /> Équipe réduite</button></div><div className="member-list">{members.map((member) => <button key={member.email} className={`member-row ${selected.includes(member.email) ? "selected" : ""}`} aria-pressed={selected.includes(member.email)} onClick={() => { setMode("custom"); toggleMember(member.email); }}><Avatar member={member} /><span><strong>{member.name}{member.email === user.email && <em>Vous</em>}</strong><small>{member.email} · {demoMode ? "Disponibilité simulée" : connectedEmails.includes(member.email) ? "Agenda connecté" : "Agenda à connecter"}</small></span><i>{selected.includes(member.email) && <Check size={15} />}</i></button>)}</div></div>
      <div className="panel schedule-settings"><div className="panel-head"><span className="icon-box coral"><Clock3 size={20} /></span><div><h2>Quand et combien de temps ?</h2><p>Nous chercherons les disponibilités communes.</p></div></div><label className="field-label" htmlFor="session-date-button">Date souhaitée</label><div className="french-date-input"><button id="session-date-button" type="button" onClick={openDatePicker} aria-label={`Choisir la date, actuellement ${formatFrenchInputDate(day)}`}><span>{formatFrenchInputDate(day)}</span><CalendarDays size={17} aria-hidden="true" /></button><input ref={dateInputRef} id="session-date" type="date" lang="fr-FR" min={minimumDay} value={day} onChange={(event) => setDay(event.target.value)} tabIndex={-1} aria-label="Date souhaitée" /></div><label className="field-label">Durée de la session</label><div className="duration-grid">{[30, 60, 90, 120, 180, 240].map((minutes) => <button key={minutes} className={duration === minutes ? "active" : ""} onClick={() => setDuration(minutes)}>{minutes < 60 ? `${minutes} min` : `${minutes / 60} h`}</button>)}</div><div className="availability-note"><span className={calendarConnected ? "live" : "local"}><span />{demoMode ? `Disponibilités simulées pour ${members.length} membre${members.length > 1 ? "s" : ""}` : `${connectedEmails.length}/${members.length} agendas Google connectés`}</span><p>Seules les périodes libre/occupé sont consultées, entre 08:00 et 21:00 (heure de Paris).</p></div><div className="availability-actions"><button className="btn btn-primary full" disabled={!selected.length || loading} onClick={() => void findSlots(false)}>{loading ? <LoaderCircle className="spin" size={18} /> : <Sparkles size={18} />} Trouver un créneau libre</button>{user.is_manager && <button className="btn btn-force full" disabled={!selected.length || loading} onClick={() => void findSlots(true)}><AlertTriangle size={17} /> Forcer un créneau</button>}</div>{user.is_manager && <p className="force-help">Le mode forcé affiche aussi les créneaux occupés et indique précisément les personnes concernées.</p>}</div>
    </div>}
    {step === 2 && <div className={`panel calendar-panel ${forceMode ? "force-mode" : ""}`}><div className="calendar-toolbar"><div><div className="calendar-title-line"><h2>Choisissez un créneau</h2>{forceMode && <span className="force-badge"><AlertTriangle size={12} /> Mode forcé</span>}</div><p>{duration} min · {chosenMembers.length} participants · {forceMode ? "créneaux libres et occupés" : "disponibilités communes uniquement"}</p></div><div className="avatar-stack">{chosenMembers.map((member) => <Avatar key={member.email} member={member} size="sm" />)}</div></div>{forceMode && <div className="force-mode-banner"><AlertTriangle size={17} /><span><strong>Vérifiez les conflits avant de continuer.</strong> Un créneau forcé reste identifiable dans la demande.</span></div>}<div className="week-strip"><button aria-label="Semaine précédente" disabled={loading || day <= minimumDay} onClick={() => changeWeek(-7)}><ChevronLeft /></button>{week.map((value) => { const date = new Date(`${value}T12:00:00Z`); return <button key={value} disabled={loading || value < minimumDay} className={value === day ? "active" : ""} aria-current={value === day ? "date" : undefined} onClick={() => changeDay(value)}><span>{new Intl.DateTimeFormat("fr-FR", { timeZone: "UTC", weekday: "short" }).format(date)}</span><strong>{date.getUTCDate()}</strong></button>; })}<button aria-label="Semaine suivante" disabled={loading} onClick={() => changeWeek(7)}><ChevronRight /></button></div><div className="timeline"><div className="timeline-head"><Clock3 size={16} /> {forceMode ? "Tous les créneaux" : "Créneaux disponibles"}<span>{slots.length} proposition{slots.length > 1 ? "s" : ""}</span></div>{loading ? <div className="empty-slots" aria-live="polite"><LoaderCircle className="spin" /><span>Vérification des agendas…</span></div> : slots.length ? <div className={`slots-grid ${forceMode ? "forced-grid" : ""}`}>{slots.map((item) => { const busyNames = busyNamesFor(item); const conflict = Boolean(busyNames.length || item.collective_calendar_busy); const text = [busyNames.length ? `Occupé : ${busyNames.join(", ")}` : "", item.collective_calendar_busy ? "Agenda collectif occupé" : ""].filter(Boolean).join(" · "); return <button key={item.start_at} className={`${slot?.start_at === item.start_at ? "selected" : ""} ${conflict ? "conflict" : "free"}`} aria-pressed={slot?.start_at === item.start_at} onClick={() => setSlot(item)}><span>{formatTime(item.start_at)}</span><small>à {formatTime(item.end_at)}</small>{forceMode && <em>{conflict ? text : "Tout le monde est libre"}</em>}{slot?.start_at === item.start_at && <Check size={16} />}</button>; })}</div> : <div className="empty-slots"><CalendarDays size={28} /><strong>Aucun créneau {forceMode ? "dans les horaires de travail" : "disponible"}</strong><span>Essayez une autre date ou une durée plus courte.</span></div>}</div><div className="wizard-footer"><button className="btn btn-ghost" onClick={() => setStep(1)}>Modifier l'équipe</button><button className={`btn ${forceMode && selectedHasConflict ? "btn-force" : "btn-primary"}`} disabled={!slot || loading} onClick={() => setStep(3)}>{forceMode && selectedHasConflict ? "Choisir malgré les conflits" : "Continuer"} <ChevronRight size={17} /></button></div></div>}
    {step === 3 && <div className="details-layout"><div className="panel details-form"><div className="panel-head"><span className="icon-box"><Video size={20} /></span><div><h2>Parlez-nous de la session</h2><p>Ces informations aideront le manager à décider.</p></div></div>{forceMode && <div className="force-summary-warning"><AlertTriangle size={19} /><div><strong>Créneau forcé</strong><p>{selectedHasConflict ? `${selectedBusyNames.length ? `${selectedBusyNames.join(", ")} ${selectedBusyNames.length > 1 ? "sont occupés" : "est occupé"}.` : ""}${slot?.collective_calendar_busy ? " L'agenda collectif est également occupé." : ""}` : "Aucun conflit n'est actuellement signalé sur ce créneau."}</p></div></div>}<label className="field-label" htmlFor="session-title">Titre de la session</label><input id="session-title" className="input" placeholder="Ex. Revue de la nouvelle identité" maxLength={160} value={details.title} onChange={(event) => setDetails({ ...details, title: event.target.value })} /><label className="field-label" htmlFor="session-type">Type de session</label><select id="session-type" className="input" value={details.session_type} onChange={(event) => setDetails({ ...details, session_type: event.target.value })}>{SESSION_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}</select>{details.session_type === "Autre" && <><label className="field-label" htmlFor="custom-session-type">Précisez le type de session</label><input id="custom-session-type" className="input" placeholder="Ex. Entretien technique" maxLength={60} value={customSessionType} onChange={(event) => setCustomSessionType(event.target.value)} autoFocus /></>}<label className="field-label" htmlFor="session-agenda">Ordre du jour</label><textarea id="session-agenda" className="input textarea" placeholder={'Décrivez les objectifs et les points à aborder…\n\n1. Contexte\n2. Décisions attendues'} maxLength={4000} value={details.agenda} onChange={(event) => setDetails({ ...details, agenda: event.target.value })} /><div className="char-count">{details.agenda.length} / 4000</div></div><aside className="summary-card"><span className="eyebrow">RÉCAPITULATIF</span><h3>{formatDate(slot!.start_at)}</h3><div className="summary-time"><Clock3 size={18} /><strong>{formatTime(slot!.start_at)}–{formatTime(slot!.end_at)}</strong><span>{duration} minutes</span></div><hr /><label>Participants ({chosenMembers.length})</label><div className="summary-members">{chosenMembers.map((member) => <div key={member.email}><Avatar member={member} size="sm" /><span>{member.name}</span></div>)}</div><div className={`approval-info ${forceMode ? "forced" : ""}`}>{forceMode ? <AlertTriangle size={18} /> : <ShieldCheck size={18} />}<p><strong>{forceMode ? "Forçage manager" : "Validation requise"}</strong><br />{forceMode ? "Les conflits seront conservés dans la demande." : "Le manager recevra votre demande."}</p></div><button className={`btn ${forceMode ? "btn-force" : "btn-primary"} full`} disabled={details.title.trim().length < 3 || details.agenda.trim().length < 10 || (details.session_type === "Autre" && customSessionType.trim().length < 2) || loading} onClick={() => void submit()}>{loading ? <LoaderCircle className="spin" size={18} /> : forceMode ? <AlertTriangle size={18} /> : <CheckCircle2 size={18} />} {forceMode ? "Envoyer la demande forcée" : "Envoyer la demande"}</button></aside></div>}
  </section>;
}

function Dashboard({ user, members, requests, onNew, onViewAll, onDecision }: {
  user: User; members: Member[]; requests: SessionRequest[]; onNew: () => void; onViewAll: () => void;
  onDecision: (item: SessionRequest, decision: "approved" | "declined") => void;
}) {
  const now = new Date();
  const [scheduleYear, scheduleMonth] = scheduleDate(now).split("-").map(Number);
  const quarter = Math.floor((scheduleMonth - 1) / 3) + 1;
  const quarterRequests = requests.filter((item) => {
    const [year, month] = scheduleDate(new Date(item.start_at)).split("-").map(Number);
    return year === scheduleYear && Math.floor((month - 1) / 3) + 1 === quarter;
  });
  const pending = requests.filter((item) => item.status === "pending");
  const upcoming = requests.filter((item) => item.status === "approved" && new Date(item.start_at) >= now);
  const hours = upcoming.reduce((total, item) => total + (new Date(item.end_at).getTime() - new Date(item.start_at).getTime()) / 3600000, 0);
  const plannedHours = Number.isInteger(hours) ? String(hours) : hours.toFixed(1).replace(".", ",");
  const displayed = user.is_manager ? pending : requests.filter((item) => new Date(item.end_at) >= now).sort((a, b) => +new Date(a.start_at) - +new Date(b.start_at));
  const today = new Intl.DateTimeFormat("fr-FR", { timeZone: SCHEDULE_TIMEZONE, weekday: "long", day: "numeric", month: "long" }).format(now).toUpperCase();
  return <section><div className="hero"><div><span className="eyebrow">{today}</span><h1>Bonjour {user.first_name || user.name.split(" ")[0]} <span>👋</span></h1><p>{user.is_manager && pending.length ? `${pending.length} demande${pending.length > 1 ? "s" : ""} attend${pending.length > 1 ? "ent" : ""} votre validation.` : "Prêt à organiser une nouvelle session de travail ?"}</p></div><button className="btn btn-light" onClick={onNew}><Plus size={18} /> Nouvelle session</button><div className="hero-orb one" /><div className="hero-orb two" /></div>
    <div className="stats-grid"><div className="stat-card"><span className="stat-icon indigo"><CalendarDays /></span><div className="stat-copy"><strong>{quarterRequests.length}</strong><span>Sessions ce trimestre</span></div><small>T{quarter} {scheduleYear}</small></div><div className="stat-card"><span className="stat-icon amber"><Clock3 /></span><div className="stat-copy"><strong>{pending.length}</strong><span>En attente</span></div><small>{user.is_manager ? "À traiter" : "En cours"}</small></div><div className="stat-card"><span className="stat-icon green"><CheckCircle2 /></span><div className="stat-copy"><strong>{upcoming.length}</strong><span>Sessions à venir</span></div><small>{plannedHours} h planifiée{hours > 1 ? "s" : ""}</small></div></div>
    <div className="section-title"><div><h2>{user.is_manager ? "Demandes à traiter" : "Mes prochaines sessions"}</h2><p>{user.is_manager ? "Les demandes qui attendent votre décision" : "Vos sessions et demandes à venir"}</p></div><button className="text-button" onClick={onViewAll}>Tout voir <ChevronRight size={16} /></button></div>
    <div className="request-list">{displayed.slice(0, 3).map((item) => <RequestCard key={item.id} item={item} members={members} manager={user.is_manager} onDecision={onDecision} />)}{displayed.length === 0 && <div className="empty-state"><Inbox size={32} /><h3>{user.is_manager ? "Aucune demande à traiter" : "Aucune session à venir"}</h3><p>{user.is_manager ? "Les nouvelles demandes apparaîtront ici." : "Votre prochaine session apparaîtra ici."}</p><button className="btn btn-primary" onClick={onNew}>Créer une session</button></div>}</div>
  </section>;
}

function LatenessPage({ entries, members, manager, onSave }: {
  entries: LatenessEntry[]; members: Member[]; manager: boolean; onSave: (email: string, points: number) => Promise<void>;
}) {
  const rows = useMemo(() => {
    const byEmail = new Map(entries.map((entry) => [entry.email, entry]));
    return members.map((member) => byEmail.get(member.email) || { email: member.email, name: member.name, points: 0 });
  }, [entries, members]);
  const ranking = useMemo(() => [...rows].sort((a, b) => b.points - a.points || a.name.localeCompare(b.name, "fr")), [rows]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [localError, setLocalError] = useState("");
  useEffect(() => setDrafts((current) => Object.fromEntries(rows.map((entry) => [entry.email, current[entry.email] ?? String(entry.points)]))), [rows]);
  const save = async (email: string) => {
    const rawPoints = drafts[email]?.trim();
    const points = Number(rawPoints);
    if (!rawPoints || !Number.isInteger(points) || points < 0 || points > 1_000_000) { setLocalError("Le nombre de retards doit être un entier positif ou nul (maximum 1 000 000)."); return; }
    setSaving(email); setSaved(null); setLocalError("");
    try { await onSave(email, points); setDrafts((current) => ({ ...current, [email]: String(points) })); setSaved(email); } catch (err) { setLocalError((err as Error).message); } finally { setSaving(null); }
  };
  return <section className="lateness-page"><div className="page-title"><div><span className="eyebrow">CLASSEMENT DE L'ÉQUIPE</span><h1>Nombre de retards</h1><p>{manager ? "Modifiez le nombre de retards de chaque membre. Le classement est visible par toute l'équipe." : "Consultez le classement des retards de l'équipe."}</p></div><span className="ranking-icon"><Trophy size={24} /></span></div>
    {localError && <div className="alert-error" role="alert"><XCircle size={18} />{localError}<button aria-label="Fermer" onClick={() => setLocalError("")}><X size={16} /></button></div>}
    <div className={`ranking-panel ${manager ? "manager" : "viewer"}`}><div className="ranking-head"><span>Classement</span><span>Membre</span><span>Nombre de retards</span>{manager && <span>Action</span>}</div><div className="ranking-list">{ranking.map((entry, index) => {
      const member = members.find((candidate) => candidate.email === entry.email) || { email: entry.email, name: entry.name, initials: entry.name.slice(0, 2).toUpperCase(), color: "#77768a" };
      return <div className={`ranking-row rank-${index + 1}`} key={entry.email}><div className="rank-number">{index < 3 ? <Trophy size={18} aria-label={`Rang ${index + 1}`} /> : <strong>{index + 1}</strong>}</div><div className="ranking-member"><Avatar member={member} /><span><strong>{entry.name}</strong><small>{entry.email}</small></span></div>{manager ? <div className="points-editor"><label className="sr-only" htmlFor={`lateness-${entry.email}`}>Nombre de retards de {entry.name}</label><input id={`lateness-${entry.email}`} type="number" min="0" max="1000000" step="1" inputMode="numeric" value={drafts[entry.email] ?? String(entry.points)} onChange={(event) => { setSaved(null); setDrafts((current) => ({ ...current, [entry.email]: event.target.value })); }} /><span>retard{Number(drafts[entry.email] ?? entry.points) > 1 ? "s" : ""}</span></div> : <strong className="points-readonly">{entry.points} <small>retard{entry.points > 1 ? "s" : ""}</small></strong>}{manager && <button className={`btn btn-ghost save-points ${saved === entry.email ? "saved" : ""}`} disabled={saving === entry.email || drafts[entry.email] === String(entry.points)} onClick={() => void save(entry.email)}>{saving === entry.email ? <LoaderCircle className="spin" size={15} /> : saved === entry.email ? <Check size={15} /> : <Save size={15} />} {saved === entry.email ? "Enregistré" : "Enregistrer"}</button>}</div>;
    })}</div>{!ranking.length && <div className="empty-state"><Users size={30} /><h3>Aucun membre</h3><p>Le classement apparaîtra dès que l'équipe sera configurée.</p></div>}</div>
  </section>;
}

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [requests, setRequests] = useState<SessionRequest[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [lateness, setLateness] = useState<LatenessEntry[]>([]);
  const [calendar, setCalendar] = useState<CalendarStatus | null>(null);
  const [view, setView] = useState<View>("dashboard");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [mobileMenu, setMobileMenu] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [decision, setDecision] = useState<{ item: SessionRequest; value: "approved" | "declined" } | null>(null);
  const [decisionLoading, setDecisionLoading] = useState(false);
  const [note, setNote] = useState("");
  const notificationsRef = useRef<HTMLDivElement>(null);
  const fullDataRequestRef = useRef(0);
  const liveRefreshRef = useRef(0);
  const explicitLoadRef = useRef(false);

  const loadData = useCallback(async () => {
    const requestId = ++fullDataRequestRef.current;
    explicitLoadRef.current = true;
    liveRefreshRef.current += 1;
    setLoading(true); setError("");
    try {
      const current = await api.me();
      const [team, items, calendarState, alerts, latenessRows] = await Promise.all([api.members(), api.requests(current.is_manager ? "all" : "mine"), api.calendarStatus(), api.notifications(), api.lateness()]);
      if (requestId !== fullDataRequestRef.current) return;
      setUser(current); setMembers(team); setRequests(items); setCalendar(calendarState); setNotifications(alerts); setLateness(latenessRows);
      const params = new URLSearchParams(window.location.search);
      const calendarError = params.get("calendar_error");
      if (calendarError) setError(calendarError);
      if (params.has("calendar") || calendarError) window.history.replaceState({}, "", window.location.pathname);
    } catch (err) {
      if (requestId === fullDataRequestRef.current) { setError((err as Error).message); setUser(null); }
    } finally {
      if (requestId === fullDataRequestRef.current) { explicitLoadRef.current = false; setLoading(false); }
    }
  }, []);
  useEffect(() => { api.config().then((value) => { setConfig(value); void loadData(); }).catch((err) => { setError(err.message); setLoading(false); }); }, [loadData]);
  useEffect(() => {
    if (!notificationsOpen) return;
    const close = (event: MouseEvent) => { if (!notificationsRef.current?.contains(event.target as Node)) setNotificationsOpen(false); };
    document.addEventListener("mousedown", close); return () => document.removeEventListener("mousedown", close);
  }, [notificationsOpen]);
  useEffect(() => {
    if (!user) return;
    let active = true;
    let refreshing = false;
    const refreshLiveData = async () => {
      if (refreshing || explicitLoadRef.current || document.visibilityState === "hidden") return;
      refreshing = true;
      const requestId = ++liveRefreshRef.current;
      try {
        const [alerts, items] = await Promise.all([
          api.notifications(),
          api.requests(user.is_manager ? "all" : "mine"),
        ]);
        if (active && !explicitLoadRef.current && requestId === liveRefreshRef.current) {
          setNotifications((current) => alerts.map((alert) => {
            const local = current.find((item) => item.id === alert.id);
            return local?.read_at && !alert.read_at ? { ...alert, read_at: local.read_at } : alert;
          }));
          setRequests(items);
        }
      } catch {
        // The main error banner is reserved for explicit user actions. A later
        // poll or focus event retries transient background failures.
      } finally { refreshing = false; }
    };
    const onVisibilityOrFocus = () => { if (document.visibilityState === "visible") void refreshLiveData(); };
    const interval = window.setInterval(() => void refreshLiveData(), 30_000);
    window.addEventListener("focus", onVisibilityOrFocus);
    document.addEventListener("visibilitychange", onVisibilityOrFocus);
    return () => {
      active = false;
      window.clearInterval(interval);
      window.removeEventListener("focus", onVisibilityOrFocus);
      document.removeEventListener("visibilitychange", onVisibilityOrFocus);
    };
  }, [user?.email, user?.is_manager]);

  const navigate = (next: View) => { setView(next); setMobileMenu(false); setNotificationsOpen(false); };
  const changeDemoUser = async (email: string) => { setDemoUser(email); navigate("dashboard"); await loadData(); };
  const logout = async () => { try { await api.logout(); } finally { window.google?.accounts.id.disableAutoSelect(); setUser(null); window.location.reload(); } };
  const connectCalendar = async () => { setError(""); try { const { authorization_url } = await api.calendarConnect(); window.location.assign(authorization_url); } catch (err) { setError((err as Error).message); } };
  const openDecision = (item: SessionRequest, value: "approved" | "declined") => { setNote(""); setDecision({ item, value }); };
  const closeDecision = () => { setDecision(null); setNote(""); };
  const handleDecision = async () => {
    if (!decision || decisionLoading) return;
    setDecisionLoading(true);
    try {
      await api.decide(decision.item.id, decision.value, note);
      closeDecision(); await loadData();
    } catch (err) {
      const message = (err as Error).message;
      closeDecision();
      await loadData();
      setError(message);
    } finally { setDecisionLoading(false); }
  };
  const markNotificationRead = async (notification: Notification) => {
    if (notification.read_at) return;
    liveRefreshRef.current += 1;
    const readAt = new Date().toISOString();
    setNotifications((current) => current.map((item) => item.id === notification.id ? { ...item, read_at: readAt } : item));
    try { await api.markNotificationRead(notification.id); } catch (err) { setNotifications((current) => current.map((item) => item.id === notification.id ? { ...item, read_at: null } : item)); setError((err as Error).message); }
  };
  const markAllRead = async () => {
    const unread = notifications.filter((item) => !item.read_at); if (!unread.length) return;
    liveRefreshRef.current += 1;
    const now = new Date().toISOString(); setNotifications((current) => current.map((item) => ({ ...item, read_at: item.read_at || now })));
    try { await Promise.all(unread.map((item) => api.markNotificationRead(item.id))); } catch (err) { setError((err as Error).message); const fresh = await api.notifications().catch(() => null); if (fresh) setNotifications(fresh); }
  };
  const saveLateness = async (email: string, points: number) => {
    const updated = await api.updateLateness(email, points);
    setLateness((current) => current.some((entry) => entry.email === email) ? current.map((entry) => entry.email === email ? (updated || { ...entry, points }) : entry) : [...current, updated || { email, name: members.find((member) => member.email === email)?.name || email, points }]);
  };
  const addCreatedRequest = (item: SessionRequest) => { liveRefreshRef.current += 1; setRequests((current) => [item, ...current.filter((request) => request.id !== item.id)]); };

  if (!config || loading && !user) return <div className="app-loader"><img className="brand-logo brand-logo-loader" src="/3istor-logo.png" alt="Logo 3istor SIGL" /><LoaderCircle className="spin" /></div>;
  if (config.auth_mode === "google" && !user) return <GoogleLogin config={config} onLogin={loadData} />;
  if (!user) return <div className="app-loader"><div className="error-card"><XCircle /><h2>Connexion impossible</h2><p>{error}</p><button className="btn btn-primary" onClick={() => void loadData()}>Réessayer</button></div></div>;
  const calendarReady = config.auth_mode === "demo" || Boolean(calendar?.connected && (!user.is_manager || calendar.can_create_events));
  const unreadCount = notifications.filter((item) => !item.read_at).length;
  const viewTitle = view === "profile" ? "Mon profil" : view === "dashboard" ? "Vue d'ensemble" : view === "new" ? "Nouvelle session" : view === "lateness" ? "Nombre de retards" : user.is_manager ? "Demandes" : "Mes sessions";

  return <div className="app-shell">
    <aside className={`sidebar ${mobileMenu ? "open" : ""}`}><div className="brand"><img className="brand-logo brand-logo-sidebar" src="/3istor-logo.png" alt="Logo 3istor SIGL" /><div><strong>3istor</strong><span>Sessions</span></div></div><nav><button className={view === "dashboard" ? "active" : ""} onClick={() => navigate("dashboard")}><LayoutDashboard /> Tableau de bord</button><button className={view === "new" ? "active" : ""} onClick={() => navigate("new")}><Plus /> Nouvelle session</button><button className={view === "requests" ? "active" : ""} onClick={() => navigate("requests")}><Inbox /> {user.is_manager ? "Demandes" : "Mes sessions"}{user.is_manager && requests.some((item) => item.status === "pending") && <b>{requests.filter((item) => item.status === "pending").length}</b>}</button><button className={view === "lateness" ? "active" : ""} onClick={() => navigate("lateness")}><Trophy /> Nombre de retards</button><button className={view === "profile" ? "active" : ""} onClick={() => navigate("profile")}><Users /> Mon profil</button></nav><div className="sidebar-bottom"><div className="secure-card"><ShieldCheck /><strong>Planning sécurisé</strong><span>Vos agendas restent privés. Seules les disponibilités sont lues.</span></div><div className="profile"><span className="avatar" style={{ background: "#4f46e5" }}>{user.name.slice(0, 2).toUpperCase()}</span><div><strong>{user.name}</strong><small>{user.is_manager ? "Manager" : "Membre"}</small></div>{config.auth_mode === "google" && <button className="logout-button" onClick={() => void logout()} title="Se déconnecter"><LogOut size={17} /></button>}</div>{config.auth_mode === "demo" && <select className="demo-select" value={getDemoUser() || user.email} onChange={(event) => void changeDemoUser(event.target.value)} aria-label="Identité de démonstration">{members.map((member) => <option key={member.email} value={member.email}>Voir comme {member.name}</option>)}</select>}</div></aside>
    {mobileMenu && <button className="sidebar-overlay" aria-label="Fermer le menu" onClick={() => setMobileMenu(false)} />}
    <main><header className="topbar"><button className="mobile-menu" aria-label="Ouvrir le menu" aria-expanded={mobileMenu} onClick={() => setMobileMenu(!mobileMenu)}><Menu /></button><div className="breadcrumb">Espace équipe <ChevronRight size={14} /> <strong>{viewTitle}</strong></div><div className="topbar-actions"><span className={`sync-pill ${calendarReady ? "connected" : ""}`}><span />{config.auth_mode === "demo" ? "Mode démonstration" : calendarReady ? "Google Calendar synchronisé" : "Agenda à connecter"}</span><div className="notifications" ref={notificationsRef}><button className="notification-button" aria-label={`${unreadCount} notification${unreadCount > 1 ? "s" : ""} non lue${unreadCount > 1 ? "s" : ""}`} aria-expanded={notificationsOpen} onClick={() => setNotificationsOpen((open) => !open)}><Bell size={18} />{unreadCount > 0 && <b>{unreadCount > 9 ? "9+" : unreadCount}</b>}</button>{notificationsOpen && <div className="notifications-panel"><div className="notifications-head"><div><strong>Notifications</strong><span>{unreadCount ? `${unreadCount} non lue${unreadCount > 1 ? "s" : ""}` : "Vous êtes à jour"}</span></div>{unreadCount > 0 && <button onClick={() => void markAllRead()}>Tout marquer comme lu</button>}</div><div className="notifications-list">{notifications.map((notification) => <button key={notification.id} className={notification.read_at ? "read" : "unread"} onClick={() => { void markNotificationRead(notification); if (notification.request_id) navigate("requests"); }}><span className="notification-dot" /><span><strong>{notification.title}</strong><p>{notification.message}</p><small>{formatDateTime(notification.created_at)}</small></span></button>)}{!notifications.length && <div className="notifications-empty"><Bell size={23} /><span>Aucune notification</span></div>}</div></div>}</div></div></header><div className="content">
      {error && <div className="alert-error" role="alert"><XCircle size={18} />{error}<button aria-label="Fermer" onClick={() => setError("")}><X size={16} /></button></div>}
      {!calendarReady && view !== "lateness" && <div className="calendar-connect-banner"><span className="icon-box"><CalendarDays size={20} /></span><div><strong>Connectez votre Google Calendar</strong><p>{user.is_manager ? "Nous lirons uniquement vos périodes occupées et vous autoriserez la création des sessions validées." : "La plateforme verra uniquement si vous êtes libre ou occupé, jamais le détail de vos événements."}</p></div><button className="btn btn-primary" onClick={() => void connectCalendar()}>Connecter mon agenda</button></div>}
      {view === "dashboard" && <Dashboard user={user} members={members} requests={requests} onNew={() => navigate("new")} onViewAll={() => navigate("requests")} onDecision={openDecision} />}
      {view === "new" && <NewSession members={members} user={user} calendarConnected={calendarReady} connectedEmails={config.auth_mode === "demo" ? [] : calendar?.connected_emails || []} demoMode={config.auth_mode === "demo"} onCreated={addCreatedRequest} onCancel={() => { navigate("dashboard"); void loadData(); }} />}
      {view === "requests" && <section><div className="page-title"><div><span className="eyebrow">{user.is_manager ? "ESPACE MANAGER" : "MON PLANNING"}</span><h1>{user.is_manager ? "Demandes de l'équipe" : "Mes sessions"}</h1><p>{user.is_manager ? "Validez les sessions et gardez la maîtrise du planning." : "Retrouvez vos demandes et leur état."}</p></div><button className="btn btn-primary" onClick={() => navigate("new")}><Plus size={18} /> Nouvelle session</button></div><div className="request-list">{requests.map((item) => <RequestCard key={item.id} item={item} members={members} manager={user.is_manager} onDecision={openDecision} />)}{!requests.length && <div className="empty-state"><Inbox size={32} /><h3>Aucune session</h3><p>Les demandes apparaîtront ici.</p></div>}</div></section>}
      {view === "profile" && <ProfilePage key={user.email} user={user} onSaved={loadData} />}
      {view === "lateness" && <LatenessPage entries={lateness} members={members} manager={user.is_manager} onSave={saveLateness} />}
    </div></main>
    {decision && <div className="modal-backdrop" onMouseDown={() => !decisionLoading && closeDecision()}><div className="modal" role="dialog" aria-modal="true" aria-labelledby="decision-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" aria-label="Fermer" disabled={decisionLoading} onClick={closeDecision}><X /></button><span className={`decision-icon ${decision.value}`}>{decision.value === "approved" ? <CheckCircle2 /> : <XCircle />}</span><h2 id="decision-title">{decision.value === "approved" ? "Accepter cette session ?" : "Refuser cette session ?"}</h2><p>« {decision.item.title} » · {formatDate(decision.item.start_at, true)} à {formatTime(decision.item.start_at)}</p>{decision.item.is_forced && <div className="decision-force-warning"><AlertTriangle size={17} /><span>Cette demande utilise un créneau forcé{decision.item.busy_participant_emails?.length ? ` malgré l'indisponibilité de ${decision.item.busy_participant_emails.map((email) => members.find((member) => member.email === email)?.name || email).join(", ")}` : ""}{decision.item.collective_calendar_busy ? `${decision.item.busy_participant_emails?.length ? "; l" : " malgré l"}'agenda collectif est occupé` : ""}.</span></div>}<label className="field-label" htmlFor="manager-note">Note au demandeur (facultatif)</label><textarea id="manager-note" className="input textarea small-area" disabled={decisionLoading} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Ajoutez un commentaire…" /><div className="modal-actions"><button className="btn btn-ghost" disabled={decisionLoading} onClick={closeDecision}>Annuler</button><button className={`btn ${decision.value === "approved" ? "btn-primary" : "btn-danger"}`} disabled={decisionLoading} onClick={() => void handleDecision()}>{decisionLoading && <LoaderCircle className="spin" size={16} />} Confirmer</button></div></div></div>}
  </div>;
}
