import { useEffect, useState } from "react";
import { AlertTriangle, CalendarDays, CheckCircle2, ChevronLeft, ExternalLink, LoaderCircle, LockKeyhole, RefreshCw, Save, ShieldCheck } from "lucide-react";
import { ApiError, api } from "./api";
import type { CalendarSelection } from "./types";
import "./calendar-settings.css";

export default function CalendarSettings({demo, onBack, onSaved}: {
  demo: boolean; onBack: () => void; onSaved: () => Promise<void>;
}) {
  const [data, setData] = useState<CalendarSelection | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [authorizing, setAuthorizing] = useState(false);
  const [needsAuthorization, setNeedsAuthorization] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (demo) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true); setError("");
    api.calendarSelection(controller.signal).then(result => {
      if (controller.signal.aborted) return;
      setData(result); setChosen(result.calendars.filter(value => value.selected).map(value => value.id));
      setNeedsAuthorization(result.authorization_required);
    }).catch(err => {
      if (controller.signal.aborted) return;
      setError((err as Error).message);
      if (err instanceof ApiError && err.status === 428) setNeedsAuthorization(true);
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [demo, refresh]);
  const dirty = Boolean(data && [...chosen].sort().join("\n") !== data.calendars.filter(value => value.selected).map(value => value.id).sort().join("\n"));
  const removedOnly = Boolean(data && chosen.every(id => data.calendars.some(value => value.id === id && value.selected)));
  const busy = loading || saving || authorizing;
  async function authorize() {
    setAuthorizing(true); setError("");
    try {
      const {authorization_url} = await api.calendarConnect(true);
      window.location.assign(authorization_url);
    } catch (err) { setError((err as Error).message); setAuthorizing(false); }
  }
  async function save() {
    setSaving(true); setError(""); setMessage("");
    try {
      await api.saveCalendarSelection(chosen);
      // Preserve the saved state even if Google is temporarily unavailable afterwards.
      setData(current => current && {...current, calendars: current.calendars
        .filter(value => value.available || chosen.includes(value.id))
        .map(value => ({...value, selected: value.primary || chosen.includes(value.id)}))});
      setMessage("Sélection enregistrée. Les prochains calculs de disponibilités utiliseront ces agendas.");
      await onSaved();
    } catch (err) {
      setError((err as Error).message);
      if (err instanceof ApiError && err.status === 428) setNeedsAuthorization(true);
    } finally { setSaving(false); }
  }
  return <section className="calendar-settings-page">
    <button className="btn btn-ghost" disabled={busy} onClick={onBack}><ChevronLeft size={16} />Retour au profil</button>
    <div className="page-title"><div><span className="eyebrow">DISPONIBILITÉS PERSONNELLES</span><h1>Mes agendas</h1><p>Choisissez les agendas dont les événements occupés doivent bloquer vos créneaux.</p></div></div>
    {demo ? <div className="panel"><h2>Mode démonstration</h2><p>La sélection des agendas Google est disponible après connexion avec Google sur le site.</p></div> : <>
      {error && <p role="alert" className="alert-error"><AlertTriangle size={17} />{error}</p>}
      {message && <p role="status" className="calendar-settings-success"><CheckCircle2 size={18} />{message}</p>}
      <div className="calendar-settings-layout">
        <div className="panel calendar-settings-choices">
          <div className="calendar-settings-head"><div><h2><CalendarDays size={20} />Agendas à vérifier</h2><p>Vous pouvez revenir ici à tout moment pour ajouter ou retirer un agenda.</p></div><button className="btn btn-ghost" aria-label="Actualiser la liste des agendas" disabled={busy} onClick={() => {
            if (dirty && !window.confirm("Actualiser la liste ? Vos choix non enregistrés seront perdus.")) return;
            setRefresh(value => value + 1);
          }}><RefreshCw size={17} /></button></div>
          {loading && <p role="status" className="calendar-settings-loading"><LoaderCircle className="spin" size={20} />Chargement de vos agendas…</p>}
          {!loading && needsAuthorization && <div className="calendar-settings-consent"><ShieldCheck size={24} /><div><h3>{data?.connected ? "Autoriser les agendas supplémentaires" : "Connecter vos agendas Google"}</h3><p>Google doit autoriser la lecture de la liste de vos agendas et de leurs disponibilités. Aucun accès au contenu des événements n'est demandé aux membres.</p><button className="btn btn-primary" disabled={busy} onClick={() => void authorize()}>{authorizing ? <LoaderCircle className="spin" size={16} /> : <ExternalLink size={16} />}Autoriser avec Google</button></div></div>}
          {!loading && data && <div className="calendar-settings-list">{data.calendars.map(value => <label key={value.id} className={`calendar-setting-choice ${chosen.includes(value.id) ? "selected" : ""}`}>
            <input type="checkbox" checked={chosen.includes(value.id)} disabled={busy || value.primary || (!chosen.includes(value.id) && (!value.available || chosen.length >= 50)) || (needsAuthorization && !value.selected)} onChange={event => {
              setMessage(""); setChosen(current => event.target.checked ? [...current, value.id] : current.filter(id => id !== value.id));
            }} /><span><strong>{value.name}</strong><small>{value.primary ? "Toujours vérifié · agenda principal" : !value.available ? needsAuthorization ? "Autorisation nécessaire · vous pouvez le retirer" : "Agenda inaccessible · retirez-le ou rétablissez son accès" : "Bloque vos disponibilités si ses événements sont marqués Occupé"}</small></span>{value.primary && <LockKeyhole size={16} aria-label="Agenda obligatoire" />}
          </label>)}</div>}
          {!loading && data && <div className="calendar-settings-save"><p>{Math.max(0, chosen.length - 1)} agenda{chosen.length > 2 ? "s" : ""} supplémentaire{chosen.length > 2 ? "s" : ""} sélectionné{chosen.length > 2 ? "s" : ""} · 49 maximum</p><button className="btn btn-primary" disabled={busy || !dirty || (needsAuthorization && !removedOnly)} onClick={() => void save()}>{saving ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}Enregistrer les agendas</button></div>}
        </div>
        <aside className="panel calendar-settings-info"><ShieldCheck size={26} /><h2>Vos événements restent privés</h2><p>Les noms de ces agendas ne sont visibles que par vous. Le planning de l'équipe affiche uniquement votre nom et vos périodes occupées.</p><h3>Agendas collectifs obligatoires</h3><p>Les agendas 3-ISTOR et des délégués SIGL restent vérifiés séparément pour toutes les sessions.</p><p>Pour ajouter un nouvel agenda, créez-le ou ajoutez-le dans Google Calendar, puis actualisez cette liste pour le sélectionner.</p><p>Un événement réglé sur « Disponible » dans Google ne bloque pas un créneau. Après avoir ajouté un événement ou changé cette sélection, actualisez le planning ouvert.</p><p>Retirer un agenda ici ne le supprime pas de Google et ne supprime aucun événement.</p></aside>
      </div>
    </>}
  </section>;
}
