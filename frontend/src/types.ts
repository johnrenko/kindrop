export type ReadingDirection = "rtl" | "ltr";
export type SpreadMode = "split" | "rotate" | "both";
export type CropMode = "none" | "margins" | "margins_and_page_numbers";

export interface ConversionPreset {
  kindle_profile: string;
  reading_direction: ReadingDirection;
  spread_mode: SpreadMode;
  crop_mode: CropMode;
}

export interface SetupStatus {
  client_configured: boolean;
  google_connected: boolean;
  google_email: string | null;
  source_folder_configured: boolean;
  kindle_destination_configured: boolean;
  ready: boolean;
}

export interface Settings {
  google_email: string | null;
  source_folder_id: string | null;
  source_folder_name: string | null;
  kindle_email: string | null;
  preset: ConversionPreset;
}

export interface Scan {
  id: string;
  status: string;
  progress: number;
  discovered_count: number;
  processed_count: number;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface Candidate {
  id: string;
  status: string;
  resolved_title: string;
  title_override: string | null;
  metadata: {
    title?: string | null;
    series?: string | null;
    number?: string | null;
    author?: string | null;
    cover_url?: string | null;
  };
  cache_expires_at: string | null;
  error: string | null;
  drive_file_id: string;
  name: string;
  path: string;
  size: number;
  fingerprint: string;
}

export interface Delivery {
  id: string;
  status: string;
  filename: string;
  part_number: number;
  total_parts: number;
  gmail_message_id: string | null;
  error_code: string | null;
  error_detail: string | null;
  verification_url: string | null;
  sent_at: string | null;
}

export interface Job {
  id: string;
  batch_id: string;
  status: string;
  title: string;
  progress: number;
  error: string | null;
  created_at: string;
  completed_at: string | null;
  deliveries: Delivery[];
}

export interface MangaMatch {
  anilist_id: number;
  title: string;
  native_title: string | null;
  author: string | null;
  cover_url: string | null;
  format: string | null;
  year: number | null;
}

export interface DriveFolder {
  id: string;
  name: string;
}

export interface KindleProfile {
  id: string;
  name: string;
}

