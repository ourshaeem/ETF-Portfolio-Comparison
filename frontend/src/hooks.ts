import { useEffect, useRef, useState } from "react";
import type { Row } from "./api";

/** Fetch on mount / when deps change. Keeps the previous data while reloading
 *  (charts dim instead of flashing a skeleton). */
export function useApi<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    fn()
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e) => alive && setError(String(e.message ?? e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading };
}

export interface LiveState {
  connected: boolean;
  source: string;
  ts: string | null;
  quotes: Row[];
  portfolios: Row[];
}

/** Subscribe to the server's live-quote WebSocket, reconnecting with backoff. */
export function useLiveQuotes(): LiveState {
  const [state, setState] = useState<LiveState>({ connected: false, source: "", ts: null, quotes: [], portfolios: [] });
  const retry = useRef(1000);
  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout>;
    let closed = false;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/quotes`);
      ws.onopen = () => {
        retry.current = 1000;
        setState((s) => ({ ...s, connected: true }));
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "quotes") {
          setState({ connected: true, source: msg.source, ts: msg.ts, quotes: msg.quotes, portfolios: msg.portfolios });
        }
      };
      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false }));
        if (!closed) timer = setTimeout(connect, (retry.current = Math.min(retry.current * 2, 30000)));
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, []);
  return state;
}
