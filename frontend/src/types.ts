export type AppConfig = {
  auth_mode: "demo" | "google";
  google_client_id?: string;
  calendar_connected: boolean;
  working_hours: { start: string; end: string };
};

export type User = {
  email: string;
  name: string;
  avatar_url?: string;
  is_manager: boolean;
};

export type Member = {
  email: string;
  name: string;
  initials: string;
  color: string;
};

export type Slot = { start_at: string; end_at: string };
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
  manager_note?: string;
  created_at: string;
  participants: { email: string }[];
};

export type Notification = {
  id: number;
  title: string;
  message: string;
  request_id?: number;
  read_at?: string;
  created_at: string;
};

export type CalendarStatus = {
  connected: boolean;
  can_create_events: boolean;
  connected_emails: string[];
};
