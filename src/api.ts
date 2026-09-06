import type { Dashboard, Meta, RangePreset, ReturnMethod } from "./types";

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok || data.error) {
    throw new Error(data.error || `Request failed: ${response.status}`);
  }
  return data as T;
}

export function fetchMeta(): Promise<Meta> {
  return getJson<Meta>("/api/meta");
}

export function fetchDashboard(params: {
  range: RangePreset;
  method: ReturnMethod;
  account: string;
  from?: string;
  to?: string;
}): Promise<Dashboard> {
  const query = new URLSearchParams({
    range: params.range,
    method: params.method,
    account: params.account,
  });
  if (params.from) query.set("from", params.from);
  if (params.to) query.set("to", params.to);
  return getJson<Dashboard>(`/api/dashboard?${query}`);
}
