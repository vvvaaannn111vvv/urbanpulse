import { useCallback, useEffect, useRef, useState } from "react";

import { WS_URL, fetchStations } from "../api";
import type { LiveMessage, StationSnapshot } from "../types";

export type ConnectionState = "connecting" | "live" | "offline";

/**
 * Keeps the station map in sync: one REST fetch for the initial state, then
 * incremental websocket updates. The socket reconnects with a capped backoff so
 * a restarted API heals the dashboard without a page reload.
 */
export function useLiveStations(): {
  stations: Map<string, StationSnapshot>;
  connection: ConnectionState;
  lastUpdate: Date | null;
  error: string | null;
} {
  const [stations, setStations] = useState<Map<string, StationSnapshot>>(new Map());
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const retryRef = useRef(0);

  const applyRows = useCallback((rows: StationSnapshot[]) => {
    setStations((previous) => {
      const next = new Map(previous);
      for (const row of rows) next.set(row.station_id, row);
      return next;
    });
    setLastUpdate(new Date());
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchStations(controller.signal)
      .then(applyRows)
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setError(String(cause));
      });
    return () => controller.abort();
  }, [applyRows]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let timer: number | undefined;
    let closed = false;

    const connect = () => {
      setConnection("connecting");
      socket = new WebSocket(WS_URL);

      socket.onopen = () => {
        retryRef.current = 0;
        setConnection("live");
        setError(null);
      };
      socket.onmessage = (event: MessageEvent<string>) => {
        try {
          const message = JSON.parse(event.data) as LiveMessage;
          applyRows(message.stations);
        } catch {
          setError("received a malformed live update");
        }
      };
      socket.onerror = () => setConnection("offline");
      socket.onclose = () => {
        setConnection("offline");
        if (closed) return;
        retryRef.current = Math.min(retryRef.current + 1, 6);
        timer = window.setTimeout(connect, 500 * 2 ** (retryRef.current - 1));
      };
    };

    connect();
    return () => {
      closed = true;
      if (timer) window.clearTimeout(timer);
      socket?.close();
    };
  }, [applyRows]);

  return { stations, connection, lastUpdate, error };
}
