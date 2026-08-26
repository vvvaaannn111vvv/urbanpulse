import { useEffect, useState } from "react";

import { fetchHistory, fetchPrediction } from "../api";
import type { HistoryPoint, Prediction, StationSnapshot } from "../types";
import { TimeSeriesChart } from "./TimeSeriesChart";

const RANGES = [6, 24, 72, 168] as const;

interface Props {
  station: StationSnapshot;
  onClose: () => void;
}

/** Drill-down: recent history, the model's forecasts and the current split. */
export function StationPanel({ station, onClose }: Props) {
  const [hours, setHours] = useState<number>(24);
  const [points, setPoints] = useState<HistoryPoint[]>([]);
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [model, setModel] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    Promise.all([
      fetchHistory(station.station_id, hours, controller.signal),
      fetchPrediction(station.station_id, controller.signal).catch(() => null),
    ])
      .then(([history, prediction]) => {
        setPoints(history.points);
        setPredictions(prediction?.predictions ?? []);
        setModel(prediction?.model ?? "unavailable");
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setError(String(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [station.station_id, hours]);

  return (
    <aside className="panel">
      <header className="panel-head">
        <div>
          <h2>{station.name}</h2>
          <p className="muted">
            station {station.station_id} · capacity {station.capacity}
          </p>
        </div>
        <button type="button" onClick={onClose} aria-label="close station panel">
          ×
        </button>
      </header>

      <div className="stat-row">
        <div className="stat">
          <span className="stat-value">{station.num_bikes}</span>
          <span className="stat-label">bikes</span>
        </div>
        <div className="stat">
          <span className="stat-value">{station.num_docks}</span>
          <span className="stat-label">docks</span>
        </div>
        <div className="stat">
          <span className="stat-value">{Math.round(station.occupancy * 100)}%</span>
          <span className="stat-label">full</span>
        </div>
      </div>

      <div className="range-row">
        {RANGES.map((r) => (
          <button
            key={r}
            type="button"
            className={r === hours ? "chip active" : "chip"}
            onClick={() => setHours(r)}
          >
            {r}h
          </button>
        ))}
      </div>

      {error && <p className="error">{error}</p>}
      {loading && points.length === 0 ? (
        <p className="muted">loading history…</p>
      ) : (
        <TimeSeriesChart
          points={points}
          predictions={predictions}
          capacity={station.capacity}
        />
      )}

      <h3>
        Forecast <span className="muted">({model})</span>
      </h3>
      {predictions.length === 0 ? (
        <p className="muted">no forecast available for this station</p>
      ) : (
        <table className="forecast">
          <thead>
            <tr>
              <th>horizon</th>
              <th>bikes</th>
              <th>at</th>
            </tr>
          </thead>
          <tbody>
            {predictions.map((p) => (
              <tr key={p.horizon_min}>
                <td>+{p.horizon_min} min</td>
                <td>{p.predicted_bikes.toFixed(1)}</td>
                <td className="muted">
                  {new Date(p.target_ts).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </aside>
  );
}
