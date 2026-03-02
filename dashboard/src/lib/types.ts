export interface Account {
  id: string;
  name: string;
  status: string;
  daily_limit: number;
  weekly_limit: number;
  timezone: string | null;
  paused_until: string | null;
  withdraw_threshold: number | null;
  avatar_path: string | null;
  created_at: string;
  updated_at: string;
}

export interface CampaignStats {
  daily: { date: string; sent: number; accepted: number; errors: number }[];
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
  csv_filename: string | null;
  total_leads: number;
  status_counts: Record<string, number> | null;
  created_at: string;
  updated_at: string;
}

export interface LeadList {
  id: string;
  name: string;
  csv_filename: string | null;
  total_leads: number;
  campaign_count: number;
  created_at: string;
  updated_at: string;
}

export interface LeadListDetail extends LeadList {
  campaigns: { id: string; name: string }[];
}

export interface Lead {
  id: string;
  campaign_id: string;
  lead_list_id: string | null;
  linkedin_url: string;
  first_name: string | null;
  last_name: string | null;
  company: string | null;
  title: string | null;
  extra_data: Record<string, unknown> | null;
  status: string;
  connection_requested_at: string | null;
  connection_accepted_at: string | null;
  followup_sent_at: string | null;
  error_message: string | null;
  retry_count: number;
  scheduled_at: string | null;
  created_at: string;
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
}

export interface DailyStat {
  date: string;
  connection_requests_sent: number;
  followup_messages_sent: number;
  connections_accepted: number;
  errors: number;
}

export interface ImportResponse {
  total_rows: number;
  imported: number;
  duplicates_skipped: number;
  no_url_skipped: number;
  errors: string[];
}
