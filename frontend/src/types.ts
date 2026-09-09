export type AppConfig = {
  auth_mode: "demo" | "google";
  google_client_id?: string | null;
  calendar_connected: boolean;
  working_hours: { start: string; end: string };
};

export type User = {
  email: string;
  name: string;
  avatar_url?: string | null;
  is_manager: boolean;
  first_name: string;
  last_name: string;
  manager_status: string;
};

export type Member = {
  email: string;
  name: string;
  initials: string;
  color: string;
};

export type Slot = {
  start_at: string;
  end_at: string;
  busy_participant_emails?: string[];
  collective_calendar_busy?: boolean;
};
export type RequestStatus = "pending" | "approved" | "declined";

export type SessionRequest = {
  id: number;
  requester_email: string;
  requester_name: string;
  title: string;
  session_type: string;
  agenda: string;
  start_at: string;
  end_at: string;
  status: RequestStatus;
  manager_note?: string | null;
  created_at: string;
  participants: { email: string }[];
  is_forced: boolean;
  busy_participant_emails: string[];
  collective_calendar_busy: boolean;
};

export type Notification = {
  id: number;
  title: string;
  message: string;
  request_id?: number | null;
  read_at?: string | null;
  created_at: string;
};

export type LatenessEntry = {
  email: string;
  name: string;
  points: number;
  updated_at?: string | null;
};

export type CalendarStatus = {
  connected: boolean;
  can_create_events: boolean;
  connected_emails: string[];
};
