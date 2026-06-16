export interface Region {
  region_id: string;
  region_name: string;
  gap_score: number;
  claimed_icu_facilities: number;
  verified_icu_facilities: number;
  summary: string;
}

export interface Confidence {
  [key: string]: number | undefined;
}

export interface Evidence {
  [key: string]: string | undefined;
}

export interface ExtractedFields {
  specialties: string[];
  equipment: string[];
  bed_count: number | null;
  icu_beds: number;
  confidence: Confidence;
  evidence: Evidence;
}

export interface Facility {
  facility_id: string;
  name: string;
  region_id: string;
  raw_text: string;
  extracted: ExtractedFields;
  trust_score: number;
  trust_flags: string[];
}

export interface Annotation {
  id: number;
  region_id: string;
  facility_id: string | null;
  author: string | null;
  note: string;
  created_at: string;
}

export function gapScoreLabel(score: number): string {
  if (score >= 0.6) return 'Critical';
  if (score >= 0.3) return 'Moderate';
  return 'Low';
}

export function gapScoreColor(score: number): string {
  if (score >= 0.6) return 'text-red-600';
  if (score >= 0.3) return 'text-amber-600';
  return 'text-green-600';
}

export function gapScoreBg(score: number): string {
  if (score >= 0.6) return 'bg-red-50 border-red-200';
  if (score >= 0.3) return 'bg-amber-50 border-amber-200';
  return 'bg-green-50 border-green-200';
}

export function gapScoreBar(score: number): string {
  if (score >= 0.6) return 'bg-red-500';
  if (score >= 0.3) return 'bg-amber-500';
  return 'bg-green-500';
}

export function trustColor(score: number): string {
  if (score < 0.5) return 'text-red-600';
  if (score < 0.75) return 'text-amber-600';
  return 'text-green-600';
}

export function trustBg(score: number): string {
  if (score < 0.5) return 'bg-red-50 text-red-700 border-red-200';
  if (score < 0.75) return 'bg-amber-50 text-amber-700 border-amber-200';
  return 'bg-green-50 text-green-700 border-green-200';
}
