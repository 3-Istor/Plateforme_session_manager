import type { AppConfig, CalendarStatus, Member, Notification, SessionRequest, Slot, User } from "./types";

export function setDemoUser(email: string) {
  localStorage.setItem("demo_user", email);
}

export function getDemoUser() {
  return localStorage.getItem("demo_user") || "manager@3istor.fr";
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  headers.set("X-Demo-User", getDemoUser());
  const response = await fetch(path, { ...options, headers, credentials: "include" });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(data?.detail || "Une erreur inattendue est survenue.");
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export const api = {
  config: () => request<AppConfig>("/api/config"),
  googleLogin: (credential: string) => request<User>("/api/auth/google", {
    method: "POST",
    body: JSON.stringify({ credential }),
  }),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  me: () => request<User>("/api/me"),
  members: () => request<Member[]>("/api/members"),
  requests: (scope: "mine" | "all") => request<SessionRequest[]>(`/api/requests?scope=${scope}`),
  notifications: () => request<Notification[]>("/api/notifications"),
  calendarStatus: () => request<CalendarStatus>("/api/google/calendar/status"),
  calendarConnect: () => request<{ authorization_url: string }>("/api/google/calendar/connect"),
  calendarDisconnect: () => request<void>("/api/google/calendar/disconnect", { method: "POST" }),
  availability: (day: string, durationMinutes: number, participantEmails: string[]) =>
    request<Slot[]>("/api/availability", {
      method: "POST",
      body: JSON.stringify({
        day,
        duration_minutes: durationMinutes,
        participant_emails: participantEmails,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Paris",
      }),
    }),
  createRequest: (payload: {
    title: string;
    session_type: string;
    agenda: string;
    start_at: string;
    end_at: string;
    participant_emails: string[];
  }) => request<SessionRequest>("/api/requests", { method: "POST", body: JSON.stringify(payload) }),
  decide: (id: number, status: "approved" | "declined", manager_note?: string) =>
    request<SessionRequest>(`/api/requests/${id}/decision`, {
      method: "PATCH",
      body: JSON.stringify({ status, manager_note: manager_note || null }),
    }),
};
