import type { AppConfig, CalendarStatus, LatenessEntry, Member, Notification, SessionRequest, Slot, User } from "./types";

export const SCHEDULE_TIMEZONE = "Europe/Paris";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// Remove the legacy browser token left by versions prior to server-side sessions.
sessionStorage.removeItem("google_token");

export function setDemoUser(email: string) {
  localStorage.setItem("demo_user", email);
}

export function getDemoUser() {
  return localStorage.getItem("demo_user");
}

function clearDemoUser() {
  localStorage.removeItem("demo_user");
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  const demoUser = getDemoUser();
  if (demoUser) headers.set("X-Demo-User", demoUser);
  const response = await fetch(path, { ...options, headers, credentials: "include" });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const detail = data?.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item?.msg || String(item)).join(" · ")
      : typeof detail === "string"
        ? detail
        : "Une erreur inattendue est survenue.";
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export const api = {
  saveProfile: (first_name: string, last_name: string, request_manager: boolean) => request<User>("/api/profile", { method: "PATCH", body: JSON.stringify({ first_name, last_name, request_manager }) }),
  roleRequests: () => request<User[]>("/api/profile/role-requests"),
  decideRole: (email: string, approve: boolean) => request<User>(`/api/profile/role-requests/${encodeURIComponent(email)}`, { method: "PATCH", body: JSON.stringify({ approve }) }),
  config: () => request<AppConfig>("/api/config"),
  googleLogin: (credential: string) => request<User>("/api/auth/google", {
    method: "POST",
    body: JSON.stringify({ credential }),
  }),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  me: async () => {
    try {
      return await request<User>("/api/me");
    } catch (error) {
      if (error instanceof ApiError && error.status === 403 && getDemoUser()) {
        clearDemoUser();
        return request<User>("/api/me");
      }
      throw error;
    }
  },
  members: () => request<Member[]>("/api/members"),
  requests: (scope: "mine" | "all") => request<SessionRequest[]>(`/api/requests?scope=${scope}`),
  notifications: () => request<Notification[]>("/api/notifications"),
  calendarStatus: () => request<CalendarStatus>("/api/google/calendar/status"),
  calendarConnect: () => request<{ authorization_url: string }>("/api/google/calendar/connect"),
  calendarDisconnect: () => request<void>("/api/google/calendar/disconnect", { method: "POST" }),
  availability: (day: string, durationMinutes: number, participantEmails: string[], signal?: AbortSignal) =>
    request<Slot[]>("/api/availability", {
      method: "POST",
      signal,
      body: JSON.stringify({
        day,
        duration_minutes: durationMinutes,
        participant_emails: participantEmails,
        timezone: SCHEDULE_TIMEZONE,
      }),
    }),
  forcedAvailability: (day: string, durationMinutes: number, participantEmails: string[], signal?: AbortSignal) =>
    request<Slot[]>("/api/availability/force", {
      method: "POST",
      signal,
      body: JSON.stringify({
        day,
        duration_minutes: durationMinutes,
        participant_emails: participantEmails,
        timezone: SCHEDULE_TIMEZONE,
      }),
    }),
  createRequest: (payload: {
    title: string;
    project_name: string;
    no_project: boolean;
    session_type: string;
    agenda: string;
    start_at: string;
    end_at: string;
    participant_emails: string[];
    force: boolean;
    timezone: string;
  }) => request<SessionRequest>("/api/requests", { method: "POST", body: JSON.stringify(payload) }),
  modifyRequest: (id: number, payload: { title: string; project_name: string; no_project: boolean; session_type: string; agenda: string; start_at: string; end_at: string; participant_emails: string[]; force: boolean; timezone: string }) =>
    request<SessionRequest>(`/api/requests/${id}/modifications`, { method: "POST", body: JSON.stringify(payload) }),
  decide: (id: number, status: "approved" | "declined", manager_note?: string) =>
    request<SessionRequest>(`/api/requests/${id}/decision`, {
      method: "PATCH",
      body: JSON.stringify({ status, manager_note: manager_note || null }),
    }),
  markNotificationRead: (id: number) => request<void>(`/api/notifications/${id}/read`, { method: "PATCH" }),
  lateness: () => request<LatenessEntry[]>("/api/lateness"),
  updateLateness: (email: string, points: number) =>
    request<LatenessEntry>(`/api/lateness/${encodeURIComponent(email)}`, {
      method: "PATCH",
      body: JSON.stringify({ points }),
    }),
};
