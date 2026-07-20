import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Inbox,
  LayoutDashboard,
  ListFilter,
  LoaderCircle,
  LogOut,
  Menu,
  Plus,
  ShieldCheck,
  Sparkles,
  Users,
  Video,
  X,
  XCircle,
} from "lucide-react";
import { api, clearGoogleToken, getDemoUser, setDemoUser, setGoogleToken } from "./api";
import type { AppConfig, CalendarStatus, Member, SessionRequest, Slot, User } from "./types";

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (options: { client_id: string; callback: (result: { credential: string }) => void }) => void;
          renderButton: (element: HTMLElement, options: Record<string, string>) => void;
          disableAutoSelect: () => void;
        };
      };
    };
  }
}

type View = "dashboard" | "new" | "requests";

const SESSION_TYPES = ["Team building", "Session de travail", "Rétro", "Dry run", "Soutenance", "Autre"] as const;

const pad = (value: number) => String(value).padStart(2, "0");
const localDate = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
const addDays = (date: Date, amount: number) => {
  const copy = new Date(date);
  copy.setDate(copy.getDate() + amount);
  return copy;
};
const formatTime = (iso: string) => new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
const formatFrenchInputDate = (value: string) => {
  const [year, month, day] = value.split("-");
  return year && month && day ? `${day}/${month}/${year}` : "JJ/MM/AAAA";
};
const formatDate = (iso: string, compact = false) =>
  new Intl.DateTimeFormat("fr-FR", compact
    ? { day: "numeric", month: "short" }
    : { weekday: "long", day: "numeric", month: "long" }).format(new Date(iso));

function Avatar({ member, size = "md" }: { member: Member; size?: "sm" | "md" }) {
  return <span className={`avatar avatar-${size}`} style={{ background: member.color }} title={member.name}>{member.initials}</span>;
}

function StatusBadge({ status }: { status: SessionRequest["status"] }) {
  const labels = { pending: "En attente", approved: "Acceptée", declined: "Refusée" };
  return <span className={`status status-${status}`}><span />{labels[status]}</span>;
}

function GoogleLogin({ config, onLogin }: { config: AppConfig; onLogin: () => void }) {
  useEffect(() => {
    if (!config.google_client_id) return;
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.onload = () => {
      window.google?.accounts.id.initialize({
        client_id: config.google_client_id!,
        callback: ({ credential }) => { setGoogleToken(credential); onLogin(); },
      });
      const target = document.getElementById("google-signin");
      if (target) window.google?.accounts.id.renderButton(target, { theme: "outline", size: "large", shape: "pill", text: "continue_with" });
    };
    document.head.appendChild(script);
    return () => script.remove();
  }, [config.google_client_id, onLogin]);
  return (
    <div className="login-screen">
      <div className="login-glow" />
      <div className="login-card">
        <img className="brand-logo brand-logo-login" src="/3istor-logo.png" alt="Logo 3istor SIGL" />
        <span className="eyebrow">ESPACE ÉQUIPE</span>
        <h1>Connexion</h1>
        <p>Plateforme de demande de sessions de l'équipe 3istor.</p>
        <div id="google-signin" className="google-signin" />
        {!config.google_client_id && <p className="form-error">Ajoutez GOOGLE_CLIENT_ID dans le fichier .env.</p>}
        <div className="login-note"><ShieldCheck size={16} /> Connexion sécurisée avec Google</div>
      </div>
    </div>
  );
}

function RequestCard({ item, members, manager, onDecision }: {
  item: SessionRequest;
  members: Member[];
  manager: boolean;
  onDecision: (item: SessionRequest, decision: "approved" | "declined") => void;
}) {
  const participants = item.participants.map((participant) => members.find((member) => member.email === participant.email)).filter(Boolean) as Member[];
  return (
    <article className="request-card">
      <div className="request-date"><strong>{new Date(item.start_at).getDate()}</strong><span>{new Intl.DateTimeFormat("fr-FR", { month: "short" }).format(new Date(item.start_at))}</span></div>
      <div className="request-main">
        <div className="request-title-row"><div><span className="request-type">{item.session_type}</span><h3>{item.title}</h3></div><StatusBadge status={item.status} /></div>
        <div className="request-meta"><span><Clock3 size={15} />{formatTime(item.start_at)}–{formatTime(item.end_at)}</span><span><Users size={15} />{participants.length} participants</span><span>Demandée par {item.requester_name}</span></div>
        <p className="agenda-preview">{item.agenda}</p>
        <div className="card-footer">
          <div className="avatar-stack">{participants.slice(0, 5).map((member) => <Avatar key={member.email} member={member} size="sm" />)}</div>
          {manager && item.status === "pending" && <div className="decision-actions"><button className="btn btn-ghost danger" onClick={() => onDecision(item, "declined")}><X size={16} /> Refuser</button><button className="btn btn-dark small" onClick={() => onDecision(item, "approved")}><Check size={16} /> Accepter</button></div>}
          {item.manager_note && <span className="manager-note">Note : {item.manager_note}</span>}
        </div>
      </div>
    </article>
  );
}

function NewSession({ members, user, calendarConnected, connectedEmails, onCreated, onCancel }: {
  members: Member[];
  user: User;
  calendarConnected: boolean;
  connectedEmails: string[];
  onCreated: (item: SessionRequest) => void;
  onCancel: () => void;
}) {
  const [step, setStep] = useState(1);
  const [mode, setMode] = useState<"all" | "custom">("all");
  const [selected, setSelected] = useState<string[]>(members.map((member) => member.email));
  const [duration, setDuration] = useState(60);
  const [day, setDay] = useState(localDate(addDays(new Date(), 1)));
  const [slots, setSlots] = useState<Slot[]>([]);
  const [slot, setSlot] = useState<Slot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [details, setDetails] = useState({ title: "", session_type: "Session de travail", agenda: "" });
  const [customSessionType, setCustomSessionType] = useState("");
  const dateInputRef = useRef<HTMLInputElement>(null);

  const week = useMemo(() => Array.from({ length: 7 }, (_, index) => addDays(new Date(`${day}T12:00:00`), index - 3)), [day]);
  const chosenMembers = members.filter((member) => selected.includes(member.email));

  const toggleMember = (email: string) => {
    if (email === user.email) return;
    setSelected((current) => current.includes(email) ? current.filter((item) => item !== email) : [...current, email]);
  };

  const openDatePicker = () => {
    const input = dateInputRef.current;
    if (!input) return;
    try {
      input.showPicker();
    } catch {
      input.focus();
      input.click();
    }
  };

  const findSlots = async () => {
    setError(""); setLoading(true);
    try {
      const result = await api.availability(day, duration, selected);
      setSlots(result); setSlot(null); setStep(2);
    } catch (err) { setError((err as Error).message); }
    finally { setLoading(false); }
  };

  const submit = async () => {
    if (!slot) return;
    setError(""); setLoading(true);
    try {
      const sessionType = details.session_type === "Autre" ? customSessionType.trim() : details.session_type;
      const created = await api.createRequest({ ...details, session_type: sessionType, start_at: slot.start_at, end_at: slot.end_at, participant_emails: selected });
      setStep(4); onCreated(created);
    } catch (err) { setError((err as Error).message); }
    finally { setLoading(false); }
  };

  if (step === 4) return (
    <div className="success-panel">
      <div className="success-icon"><CheckCircle2 size={34} /></div>
      <span className="eyebrow">DEMANDE ENVOYÉE</span>
      <h2>C'est parti !</h2>
      <p>Votre demande a bien été transmise au manager. Vous serez notifié dès qu'elle sera traitée.</p>
      <div className="success-summary"><CalendarDays size={20} /><div><strong>{formatDate(slot!.start_at)}</strong><span>{formatTime(slot!.start_at)}–{formatTime(slot!.end_at)} · {chosenMembers.length} participants</span></div></div>
      <button className="btn btn-primary" onClick={onCancel}>Retour au tableau de bord</button>
    </div>
  );

  return (
    <section className="wizard-page">
      <div className="page-heading"><div><button className="back-link" onClick={step === 1 ? onCancel : () => setStep(step - 1)}><ChevronLeft size={17} /> Retour</button><h1>Nouvelle session</h1><p>Organisez un temps de travail avec votre équipe.</p></div><div className="steps">{[1, 2, 3].map((number) => <div key={number} className={number <= step ? "active" : ""}><span>{number < step ? <Check size={13} /> : number}</span><label>{["Équipe", "Créneau", "Détails"][number - 1]}</label></div>)}</div></div>
      {error && <div className="alert-error"><XCircle size={18} />{error}</div>}

      {step === 1 && <div className="wizard-grid">
        <div className="panel"><div className="panel-head"><span className="icon-box"><Users size={20} /></span><div><h2>Qui participe ?</h2><p>Sélectionnez les membres concernés.</p></div></div>
          <div className="segmented"><button className={mode === "all" ? "active" : ""} onClick={() => { setMode("all"); setSelected(members.map((m) => m.email)); }}><Users size={17} /> Toute l'équipe</button><button className={mode === "custom" ? "active" : ""} onClick={() => { setMode("custom"); setSelected([user.email]); }}><ListFilter size={17} /> Équipe réduite</button></div>
          <div className="member-list">{members.map((member) => <button key={member.email} className={`member-row ${selected.includes(member.email) ? "selected" : ""}`} onClick={() => { setMode("custom"); toggleMember(member.email); }}><Avatar member={member} /><span><strong>{member.name}{member.email === user.email && <em>Vous</em>}</strong><small>{member.email} · {connectedEmails.includes(member.email) ? "Agenda connecté" : "Agenda à connecter"}</small></span><i>{selected.includes(member.email) && <Check size={15} />}</i></button>)}</div>
        </div>
        <div className="panel schedule-settings"><div className="panel-head"><span className="icon-box coral"><Clock3 size={20} /></span><div><h2>Quand et combien de temps ?</h2><p>Nous chercherons les disponibilités communes.</p></div></div>
          <label className="field-label" htmlFor="session-date-button">Date souhaitée</label><div className="french-date-input"><button id="session-date-button" type="button" onClick={openDatePicker} aria-label={`Choisir la date, actuellement ${formatFrenchInputDate(day)}`}><span>{formatFrenchInputDate(day)}</span><CalendarDays size={17} aria-hidden="true" /></button><input ref={dateInputRef} id="session-date" type="date" lang="fr-FR" min={localDate(addDays(new Date(), 1))} value={day} onChange={(event) => setDay(event.target.value)} tabIndex={-1} aria-label="Date souhaitée, au format jour mois année" /></div>
          <label className="field-label">Durée de la session</label><div className="duration-grid">{[30, 60, 90, 120, 180, 240].map((minutes) => <button key={minutes} className={duration === minutes ? "active" : ""} onClick={() => setDuration(minutes)}>{minutes < 60 ? `${minutes} min` : `${minutes / 60} h`}</button>)}</div>
          <div className="availability-note"><span className={calendarConnected ? "live" : "local"}><span />{connectedEmails.length}/{members.length} agendas Google connectés</span><p>Seules les périodes libre/occupé sont consultées, entre 08:00 et 21:00.</p></div>
          <button className="btn btn-primary full" disabled={selected.length === 0 || loading} onClick={findSlots}>{loading ? <LoaderCircle className="spin" size={18} /> : <Sparkles size={18} />} Trouver un créneau</button>
        </div>
      </div>}

      {step === 2 && <div className="panel calendar-panel">
        <div className="calendar-toolbar"><div><h2>Choisissez un créneau</h2><p>{duration} min · {chosenMembers.length} participants · disponibilités communes</p></div><div className="avatar-stack">{chosenMembers.map((member) => <Avatar key={member.email} member={member} size="sm" />)}</div></div>
        <div className="week-strip"><button onClick={() => setDay(localDate(addDays(new Date(`${day}T12:00:00`), -7)))}><ChevronLeft /></button>{week.map((date) => <button key={date.toISOString()} className={localDate(date) === day ? "active" : ""} onClick={async () => { const next = localDate(date); setDay(next); setLoading(true); try { setSlots(await api.availability(next, duration, selected)); setSlot(null); } catch (err) { setError((err as Error).message); } finally { setLoading(false); } }}><span>{new Intl.DateTimeFormat("fr-FR", { weekday: "short" }).format(date)}</span><strong>{date.getDate()}</strong></button>)}<button onClick={() => setDay(localDate(addDays(new Date(`${day}T12:00:00`), 7)))}><ChevronRight /></button></div>
        <div className="timeline"><div className="timeline-head"><Clock3 size={16} /> Créneaux disponibles <span>{slots.length} propositions</span></div>{loading ? <div className="empty-slots"><LoaderCircle className="spin" /></div> : slots.length ? <div className="slots-grid">{slots.map((item) => <button key={item.start_at} className={slot?.start_at === item.start_at ? "selected" : ""} onClick={() => setSlot(item)}><span>{formatTime(item.start_at)}</span><small>à {formatTime(item.end_at)}</small>{slot?.start_at === item.start_at && <Check size={16} />}</button>)}</div> : <div className="empty-slots"><CalendarDays size={28} /><strong>Aucun créneau disponible</strong><span>Essayez une autre date ou une durée plus courte.</span></div>}</div>
        <div className="wizard-footer"><button className="btn btn-ghost" onClick={() => setStep(1)}>Modifier l'équipe</button><button className="btn btn-primary" disabled={!slot} onClick={() => setStep(3)}>Continuer <ChevronRight size={17} /></button></div>
      </div>}

      {step === 3 && <div className="details-layout"><div className="panel details-form"><div className="panel-head"><span className="icon-box"><Video size={20} /></span><div><h2>Parlez-nous de la session</h2><p>Ces informations aideront le manager à décider.</p></div></div>
        <label className="field-label">Titre de la session</label><input className="input" placeholder="Ex. Revue de la nouvelle identité" maxLength={160} value={details.title} onChange={(e) => setDetails({ ...details, title: e.target.value })} />
        <label className="field-label">Type de session</label><select className="input" value={details.session_type} onChange={(e) => setDetails({ ...details, session_type: e.target.value })}>{SESSION_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}</select>
        {details.session_type === "Autre" && <><label className="field-label" htmlFor="custom-session-type">Précisez le type de session</label><input id="custom-session-type" className="input" placeholder="Ex. Entretien technique" maxLength={60} value={customSessionType} onChange={(e) => setCustomSessionType(e.target.value)} autoFocus /></>}
        <label className="field-label">Ordre du jour</label><textarea className="input textarea" placeholder={'Décrivez les objectifs et les points à aborder…\n\n1. Contexte\n2. Décisions attendues'} maxLength={4000} value={details.agenda} onChange={(e) => setDetails({ ...details, agenda: e.target.value })} /><div className="char-count">{details.agenda.length} / 4000</div>
      </div><aside className="summary-card"><span className="eyebrow">RÉCAPITULATIF</span><h3>{formatDate(slot!.start_at)}</h3><div className="summary-time"><Clock3 size={18} /><strong>{formatTime(slot!.start_at)}–{formatTime(slot!.end_at)}</strong><span>{duration} minutes</span></div><hr /><label>Participants ({chosenMembers.length})</label><div className="summary-members">{chosenMembers.map((member) => <div key={member.email}><Avatar member={member} size="sm" /><span>{member.name}</span></div>)}</div><div className="approval-info"><ShieldCheck size={18} /><p><strong>Validation requise</strong><br />Le manager recevra votre demande.</p></div><button className="btn btn-primary full" disabled={details.title.trim().length < 3 || details.agenda.trim().length < 10 || (details.session_type === "Autre" && customSessionType.trim().length < 2) || loading} onClick={submit}>{loading ? <LoaderCircle className="spin" size={18} /> : <CheckCircle2 size={18} />} Envoyer la demande</button></aside></div>}
    </section>
  );
}

function Dashboard({ user, members, requests, onNew, onViewAll, onDecision }: { user: User; members: Member[]; requests: SessionRequest[]; onNew: () => void; onViewAll: () => void; onDecision: (item: SessionRequest, decision: "approved" | "declined") => void }) {
  const pending = requests.filter((item) => item.status === "pending");
  const approved = requests.filter((item) => item.status === "approved");
  const hours = approved.reduce((total, item) => total + (new Date(item.end_at).getTime() - new Date(item.start_at).getTime()) / 3600000, 0);
  const today = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long" }).format(new Date()).toUpperCase();
  return <section><div className="hero"><div><span className="eyebrow">{today}</span><h1>Bonjour {user.name.split(" ")[0]} <span>👋</span></h1><p>{user.is_manager && pending.length ? `${pending.length} demande${pending.length > 1 ? "s" : ""} attend${pending.length > 1 ? "ent" : ""} votre validation.` : "Prêt à organiser une nouvelle session de travail ?"}</p></div><button className="btn btn-light" onClick={onNew}><Plus size={18} /> Nouvelle session</button><div className="hero-orb one" /><div className="hero-orb two" /></div>
    <div className="stats-grid"><div className="stat-card"><span className="stat-icon indigo"><CalendarDays /></span><div className="stat-copy"><strong>{requests.length}</strong><span>Sessions au total</span></div><small>Ce trimestre</small></div><div className="stat-card"><span className="stat-icon amber"><Clock3 /></span><div className="stat-copy"><strong>{pending.length}</strong><span>En attente</span></div><small>À traiter</small></div><div className="stat-card"><span className="stat-icon green"><CheckCircle2 /></span><div className="stat-copy"><strong>{approved.length}</strong><span>Sessions validées</span></div><small>{Math.round(hours)} h planifiées</small></div></div>
    <div className="section-title"><div><h2>{user.is_manager ? "Demandes à traiter" : "Mes prochaines sessions"}</h2><p>{user.is_manager ? "Les demandes récentes de votre équipe" : "Suivez vos demandes et sessions validées"}</p></div><button className="text-button" onClick={onViewAll}>Tout voir <ChevronRight size={16} /></button></div>
    <div className="request-list">{requests.slice(0, 3).map((item) => <RequestCard key={item.id} item={item} members={members} manager={user.is_manager} onDecision={onDecision} />)}{requests.length === 0 && <div className="empty-state"><Inbox size={32} /><h3>Aucune demande pour le moment</h3><p>Votre prochaine session apparaîtra ici.</p><button className="btn btn-primary" onClick={onNew}>Créer une session</button></div>}</div>
  </section>;
}

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [requests, setRequests] = useState<SessionRequest[]>([]);
  const [calendar, setCalendar] = useState<CalendarStatus | null>(null);
  const [view, setView] = useState<View>("dashboard");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [mobileMenu, setMobileMenu] = useState(false);
  const [decision, setDecision] = useState<{ item: SessionRequest; value: "approved" | "declined" } | null>(null);
  const [note, setNote] = useState("");

  const loadData = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const current = await api.me();
      const [team, items, calendarState] = await Promise.all([api.members(), api.requests(current.is_manager ? "all" : "mine"), api.calendarStatus()]);
      setUser(current); setMembers(team); setRequests(items); setCalendar(calendarState);
      const params = new URLSearchParams(window.location.search);
      const calendarError = params.get("calendar_error");
      if (calendarError) setError(calendarError);
      if (params.has("calendar") || calendarError) window.history.replaceState({}, "", window.location.pathname);
    } catch (err) { setError((err as Error).message); setUser(null); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { api.config().then((value) => { setConfig(value); if (value.auth_mode === "demo" || sessionStorage.getItem("google_token")) loadData(); else setLoading(false); }).catch((err) => { setError(err.message); setLoading(false); }); }, [loadData]);

  const changeDemoUser = async (email: string) => { setDemoUser(email); setView("dashboard"); await loadData(); };
  const logout = () => {
    window.google?.accounts.id.disableAutoSelect();
    clearGoogleToken();
    setUser(null);
    window.location.reload();
  };
  const connectCalendar = async () => {
    setError("");
    try {
      const { authorization_url } = await api.calendarConnect();
      window.location.assign(authorization_url);
    } catch (err) {
      setError((err as Error).message);
    }
  };
  const handleDecision = async () => {
    if (!decision) return;
    try { await api.decide(decision.item.id, decision.value, note); setDecision(null); setNote(""); await loadData(); }
    catch (err) { setError((err as Error).message); }
  };

  if (!config || loading && !user) return <div className="app-loader"><img className="brand-logo brand-logo-loader" src="/3istor-logo.png" alt="Logo 3istor SIGL" /><LoaderCircle className="spin" /></div>;
  if (config.auth_mode === "google" && !user) return <GoogleLogin config={config} onLogin={loadData} />;
  if (!user) return <div className="app-loader"><div className="error-card"><XCircle /><h2>Connexion impossible</h2><p>{error}</p><button className="btn btn-primary" onClick={loadData}>Réessayer</button></div></div>;

  const calendarReady = Boolean(calendar?.connected && (!user.is_manager || calendar.can_create_events));
  return <div className="app-shell">
    <aside className={`sidebar ${mobileMenu ? "open" : ""}`}><div className="brand"><img className="brand-logo brand-logo-sidebar" src="/3istor-logo.png" alt="Logo 3istor SIGL" /><div><strong>3istor</strong><span>Sessions</span></div></div><nav><button className={view === "dashboard" ? "active" : ""} onClick={() => { setView("dashboard"); setMobileMenu(false); }}><LayoutDashboard /> Tableau de bord</button><button className={view === "new" ? "active" : ""} onClick={() => { setView("new"); setMobileMenu(false); }}><Plus /> Nouvelle session</button><button className={view === "requests" ? "active" : ""} onClick={() => { setView("requests"); setMobileMenu(false); }}><Inbox /> {user.is_manager ? "Demandes" : "Mes sessions"}{user.is_manager && requests.filter((r) => r.status === "pending").length > 0 && <b>{requests.filter((r) => r.status === "pending").length}</b>}</button></nav><div className="sidebar-bottom"><div className="secure-card"><ShieldCheck /><strong>Planning sécurisé</strong><span>Vos agendas restent privés. Seules les disponibilités sont lues.</span></div><div className="profile"><span className="avatar" style={{ background: "#4f46e5" }}>{user.name.slice(0, 2).toUpperCase()}</span><div><strong>{user.name}</strong><small>{user.is_manager ? "Manager" : "Membre"}</small></div>{config.auth_mode === "google" && <button className="logout-button" onClick={logout} title="Se déconnecter"><LogOut size={17} /></button>}</div>{config.auth_mode === "demo" && <select className="demo-select" value={getDemoUser()} onChange={(e) => changeDemoUser(e.target.value)} aria-label="Identité de démonstration">{members.map((member) => <option key={member.email} value={member.email}>Voir comme {member.name}</option>)}</select>}</div></aside>
    <main><header className="topbar"><button className="mobile-menu" onClick={() => setMobileMenu(!mobileMenu)}><Menu /></button><div className="breadcrumb">Espace équipe <ChevronRight size={14} /> <strong>{view === "dashboard" ? "Vue d'ensemble" : view === "new" ? "Nouvelle session" : user.is_manager ? "Demandes" : "Mes sessions"}</strong></div><div className="topbar-actions"><span className={`sync-pill ${calendarReady ? "connected" : ""}`}><span />{calendarReady ? "Google Calendar synchronisé" : "Agenda à connecter"}</span></div></header><div className="content">
      {error && <div className="alert-error"><XCircle size={18} />{error}<button onClick={() => setError("")}><X size={16} /></button></div>}
      {!calendarReady && <div className="calendar-connect-banner"><span className="icon-box"><CalendarDays size={20} /></span><div><strong>Connectez votre Google Calendar</strong><p>{user.is_manager ? "Nous lirons uniquement vos périodes occupées et vous autoriserez la création des sessions validées." : "La plateforme verra uniquement si vous êtes libre ou occupé, jamais le détail de vos événements."}</p></div><button className="btn btn-primary" onClick={connectCalendar}>Connecter mon agenda</button></div>}
      {view === "dashboard" && <Dashboard user={user} members={members} requests={requests} onNew={() => setView("new")} onViewAll={() => setView("requests")} onDecision={(item, value) => setDecision({ item, value })} />}
      {view === "new" && <NewSession members={members} user={user} calendarConnected={calendarReady} connectedEmails={calendar?.connected_emails || []} onCreated={(item) => setRequests((current) => [item, ...current])} onCancel={() => { setView("dashboard"); loadData(); }} />}
      {view === "requests" && <section><div className="page-title"><div><span className="eyebrow">{user.is_manager ? "ESPACE MANAGER" : "MON PLANNING"}</span><h1>{user.is_manager ? "Demandes de l'équipe" : "Mes sessions"}</h1><p>{user.is_manager ? "Validez les sessions et gardez la maîtrise du planning." : "Retrouvez vos demandes et leur état."}</p></div><button className="btn btn-primary" onClick={() => setView("new")}><Plus size={18} /> Nouvelle session</button></div><div className="request-list">{requests.map((item) => <RequestCard key={item.id} item={item} members={members} manager={user.is_manager} onDecision={(request, value) => setDecision({ item: request, value })} />)}</div></section>}
    </div></main>
    {decision && <div className="modal-backdrop" onMouseDown={() => setDecision(null)}><div className="modal" onMouseDown={(e) => e.stopPropagation()}><button className="modal-close" onClick={() => setDecision(null)}><X /></button><span className={`decision-icon ${decision.value}`} >{decision.value === "approved" ? <CheckCircle2 /> : <XCircle />}</span><h2>{decision.value === "approved" ? "Accepter cette session ?" : "Refuser cette session ?"}</h2><p>« {decision.item.title} » · {formatDate(decision.item.start_at, true)} à {formatTime(decision.item.start_at)}</p><label className="field-label">Note au demandeur (facultatif)</label><textarea className="input textarea small-area" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Ajoutez un commentaire…" /><div className="modal-actions"><button className="btn btn-ghost" onClick={() => setDecision(null)}>Annuler</button><button className={`btn ${decision.value === "approved" ? "btn-primary" : "btn-danger"}`} onClick={handleDecision}>Confirmer</button></div></div></div>}
  </div>;
}
