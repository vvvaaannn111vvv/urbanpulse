import type {
  HistoryResponse,
  ObservationSpan,
  PredictResponse,
  ReplayResponse,
  StationSnapshot,
} from "./types";

/**
 * In dev, Vite proxies /api and /ws to the FastAPI service (see vite.config.ts),
 * so the browser only ever talks to one origin. In a container build the two
 * VITE_* variables point straight at the API.
 */
const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

export const WS_URL: string =
  import.meta.env.VITE_WS_URL ??
  `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`;

async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { signal });
  if (!response.ok) {
    throw new Error(`${path} -> HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export const fetchStations = (signal?: AbortSignal) =>
  getJSON<StationSnapshot[]>("/stations", signal);

export const fetchSpan = (signal?: AbortSignal) => getJSON<ObservationSpan>("/meta/span", signal);

export const fetchHistory = (stationId: string, hours: number, signal?: AbortSignal) =>
  getJSON<HistoryResponse>(
    `/stations/${encodeURIComponent(stationId)}/history?hours=${hours}`,
    signal,
  );

export const fetchPrediction = (stationId: string, signal?: AbortSignal) =>
  getJSON<PredictResponse>(`/predict/${encodeURIComponent(stationId)}`, signal);

export const fetchReplay = (ts: Date, signal?: AbortSignal) =>
  getJSON<ReplayResponse>(`/replay?ts=${encodeURIComponent(ts.toISOString())}`, signal);
