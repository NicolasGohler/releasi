export interface Account {
  id: string;
  name: string;
  status: string;
  daily_limit: number;
  weekly_limit: number;
  timezone: string | null;
  proxy_country: string | null;
  proxy_host: string | null;
  proxy_port: number | null;
  proxy_username: string | null;
  proxy_password_set: boolean;
  paused_until: string | null;
  withdraw_threshold: number | null;
  auto_withdraw_interval_days: number;
  auto_withdraw_last_run: string | null;
  pending_invitations_count: number | null;
  pending_requests: number | null;
  avatar_path: string | null;
  archived: boolean;
  work_start_hour: number | null;
  work_end_hour: number | null;
  created_at: string;
  updated_at: string;
}

export interface ProxyTestResult {
  ok: boolean;
  ip?: string | null;
  country?: string | null;
  latency_ms?: number | null;
  error?: string | null;
}

export interface CampaignStatDaily {
  date: string;
  sent: number;
  accepted: number;
  errors: number;
}

export interface CampaignStats {
  daily: CampaignStatDaily[];
  summary: {
    total_sent: number;
    total_accepted: number;
    acceptance_rate: number;
    avg_time_to_accept_hours: number | null;
  };
}

export interface Campaign {
  id: string;
  account_id: string;
  account_name: string | null;
  name: string;
  status: string;
  archived: boolean;
  connection_message_template: string | null;
  followup_message_template: string | null;
  followup_delay_hours: number;
  followup_enabled: boolean;
  followup_message_1: string | null;
  followup_message_2: string | null;
  followup_message_3: string | null;
  weekend_enabled: boolean;
  filter_no_photo: boolean;
  filter_min_connections: number | null;
  filter_exclude_open_to_work: boolean;
  csv_filename: string | null;
  total_leads: number;
  status_counts: Record<string, number> | null;
  assigned_lists: { id: string; name: string; total_leads: number; status_counts: Record<string, number> | null }[];
  account_status: string | null;
  account_paused_until: string | null;
  account_timezone: string | null;
  account_dispatch_mode: string | null;
  estimated_remaining_today: number | null;
  paused_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface LeadList {
  id: string;
  name: string;
  csv_filename: string | null;
  total_leads: number;
  campaign_count: number;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface LeadListDetail extends LeadList {
  campaigns: { id: string; name: string }[];
}

export interface Lead {
  id: string;
  campaign_id: string | null;
  lead_list_id: string | null;
  linkedin_url: string;
  first_name: string | null;
  last_name: string | null;
  company: string | null;
  title: string | null;
  email: string | null;
  phone: string | null;
  extra_data: Record<string, unknown> | null;
  status: string;
  connection_requested_at: string | null;
  connection_accepted_at: string | null;
  followup_sent_at: string | null;
  error_message: string | null;
  retry_count: number;
  scheduled_at: string | null;
  created_at: string;
  campaign_name: string | null;
  lead_list_name: string | null;
}

export interface LeadPage {
  items: Lead[];
  total: number;
  page: number;
  per_page: number;
}

export interface ActionLog {
  id: string;
  account_id: string;
  campaign_id: string | null;
  lead_id: string | null;
  action_type: string;
  status: string;
  details: Record<string, unknown> | null;
  created_at: string;
  lead_first_name: string | null;
  lead_last_name: string | null;
  lead_url: string | null;
}

export interface DailyStat {
  date: string;
  connection_requests_sent: number;
  followup_messages_sent: number;
  connections_accepted: number;
  errors: number;
}

export interface ScheduleSlot {
  lead_id: string;
  first_name: string | null;
  last_name: string | null;
  linkedin_url: string;
  campaign_name: string;
  scheduled_at: string | null;
  status: string; // "scheduled" or "sent"
}

export interface AccountHealth {
  last_action_at: string | null;
  days_since_last_activity: number | null;
  error_rate_7d: number;
  total_actions_7d: number;
  errors_7d: number;
  last_error_message: string | null;
}

export interface ScrapeStatus {
  status: "running" | "done" | "error" | "unknown";
  collected: number;
  error?: string | null;
}

export interface ImportResponse {
  total_rows: number;
  imported: number;
  duplicates_skipped: number;
  no_url_skipped: number;
  errors: string[];
}
