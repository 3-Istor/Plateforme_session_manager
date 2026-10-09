import { useEffect, useId, useState } from "react";

export function validDuration(minutes: number) {
  return Number.isInteger(minutes) && minutes >= 15 && minutes <= 480;
}

function toMinutes(amount: string, unit: "minutes" | "hours") {
  if (!amount.trim()) return NaN;
  const minutes = Number(amount) * (unit === "hours" ? 60 : 1);
  // Decimal hours can produce floating-point noise (e.g. 1.1 h = 66 min).
  return Math.abs(minutes - Math.round(minutes)) < 1e-8 ? Math.round(minutes) : NaN;
}

export default function DurationPicker({ minutes, onChange }: {
  minutes: number; onChange: (minutes: number) => void;
}) {
  const id = useId();
  const [unit, setUnit] = useState<"minutes" | "hours">("minutes");
  const [amount, setAmount] = useState(Number.isFinite(minutes) ? String(minutes) : "");
  useEffect(() => {
    // Keep partially entered values intact; sync changes from the edit dates.
    if (Number.isFinite(minutes) && minutes !== toMinutes(amount, unit)) {
      setAmount(String(minutes / (unit === "hours" ? 60 : 1)));
    }
  }, [minutes, amount, unit]);
  return <div className="duration-picker">
    <span className="field-label">Durée de la session</span>
    <div className="duration-grid">{[15, 30, 60, 90, 120, 180, 240].map(value =>
      <button type="button" key={value} aria-pressed={minutes === value}
        className={minutes === value ? "active" : ""}
        onClick={() => { setAmount(String(value / (unit === "hours" ? 60 : 1))); onChange(value); }}>
        {value < 60 ? `${value} min` : `${value / 60} h`}
      </button>)}</div>
    <label className="field-label" htmlFor={`${id}-amount`}>Durée personnalisée</label>
    <div className="custom-duration">
      <input className="input" id={`${id}-amount`} type="number" step="any"
        min={unit === "hours" ? 0.25 : 15} max={unit === "hours" ? 8 : 480}
        value={amount} aria-invalid={!validDuration(minutes)} aria-describedby={`${id}-help`}
        onChange={event => { setAmount(event.target.value); onChange(toMinutes(event.target.value, unit)); }} />
      <select className="input" aria-label="Unité de la durée" value={unit}
        onChange={event => {
          const next = event.target.value as "minutes" | "hours";
          setUnit(next);
          setAmount(Number.isFinite(minutes) ? String(minutes / (next === "hours" ? 60 : 1)) : "");
        }}>
        <option value="minutes">Minutes</option><option value="hours">Heures</option>
      </select>
    </div>
    <p id={`${id}-help`} className="duration-help" role={validDuration(minutes) ? undefined : "alert"}>
      {validDuration(minutes) ? `${minutes} min · de 15 minutes à 8 heures, à la minute près.` : "Indiquez une durée de 15 à 480 minutes, sans fraction de minute."}
    </p>
  </div>;
}
