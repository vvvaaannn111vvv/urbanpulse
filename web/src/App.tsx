import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchReplay, fetchSpan } from "./api";
import { MapView } from "./components/MapView";
import { ReplayBar } from "./components/ReplayBar";
import { StationPanel } from "./components/StationPanel";
import { useLiveStations } from "./hooks/useLiveStations";
import type { StationSnapshot } from "./types";

export default function App() {
  const { stations: live, connection, lastUpdate, error } = useLiveStations();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [span, setSpan] = useState<{ first: Date | null; last: Date | null }>({
    first: null,
    last: null,
  });
  const [replayTs, setReplayTs] = useState<Date | null>(null);
  const [replayRows, setReplayRows] = useState<StationSnapshot[] | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchSpan(controller.signal)
      .then((s) =>
        setSpan({
          first: s.first ? new Date(s.first) : null,
          last: s.last ? new Date(s.last) : null,
        }),
      )
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  // Replay mode swaps the live feed for a reconstructed past snapshot.
  useEffect(() => {
    if (!replayTs) {
      setReplayRows(null);
      return;
    }
    const controller = new AbortController();
    fetchReplay(replayTs, controller.signal)
      .then((r) => setReplayRows(r.stations))
      .catch(() => undefined);
    return () => controller.abort();
  }, [replayTs]);

  const liveRows = useMemo(() => [...live.values()], [live]);
  const rows = replayRows ?? liveRows;
  const selected = useMemo(
    () => rows.find((s) => s.station_id === selectedId) ?? null,
    [rows, selectedId],
  );

  const handleSelect = useCallback((id: string) => setSelectedId(id), []);
  const totalBikes = rows.reduce((sum, s) => sum + s.num_bikes, 0);
  const totalDocks = rows.reduce((sum, s) => sum + s.num_docks, 0);

  return (
    <div className="app">
      <header className="topbar">
        <h1>
          Urban<span>Pulse</span>
        </h1>
        <p className="muted">Ljubljana bike-share availability &amp; short-horizon forecasts</p>
        <div className="spacer" />
        <span className="metric">
          {rows.length} stations · {totalBikes} bikes · {totalDocks} free docks
        </span>
        <span className={`badge ${replayTs ? "" : connection}`}>
          {replayTs ? "REPLAY" : connection.toUpperCase()}
        </span>
        {lastUpdate && !replayTs && (
          <span className="muted">{lastUpdate.toLocaleTimeString()}</span>
        )}
      </header>

      {error && <p className="error banner">{error}</p>}

      <main className="content">
        <MapView stations={rows} selectedId={selectedId} onSelect={handleSelect} />
        {selected && <StationPanel station={selected} onClose={() => setSelectedId(null)} />}
      </main>

      <ReplayBar first={span.first} last={span.last} value={replayTs} onChange={setReplayTs} />
    </div>
  );
}
