export interface StationSnapshot {
  station_id: string;
  name: string;
  lat: number;
  lon: number;
  capacity: number;
  num_bikes: number;
  num_docks: number;
  ts: string;
  occupancy: number;
}

export interface HistoryPoint {
  bucket: string;
  avg_bikes: number;
  min_bikes: number;
  max_bikes: number;
  avg_docks: number;
  samples: number;
}

export interface HistoryResponse {
  station_id: string;
  bucket_minutes: number;
  hours: number;
  points: HistoryPoint[];
}

export interface Prediction {
  horizon_min: number;
  predicted_bikes: number;
  target_ts: string;
}

export interface PredictResponse {
  station_id: string;
  as_of: string;
  current_bikes: number;
  capacity: number;
  model: string;
  predictions: Prediction[];
}

export interface ObservationSpan {
  first: string | null;
  last: string | null;
  count: number;
}

export interface ReplayResponse {
  ts: string;
  stations: StationSnapshot[];
}

export type LiveMessage =
  | { type: "snapshot"; ts: string; stations: StationSnapshot[] }
  | { type: "update"; ts: string; stations: StationSnapshot[] };
