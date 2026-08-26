// Typed API client: bearer token injection + one-shot refresh on 401 +
// uniform error envelope normalisation.

import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";
import type { ErrorEnvelope, TokenResponse } from "@/types/core";

export const api = axios.create({
  baseURL: "/api/v1",
  withCredentials: true, // refresh token cookie
});

let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (accessToken && config.headers) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

let refreshPromise: Promise<string> | null = null;

async function refreshAccessToken(): Promise<string> {
  refreshPromise ??= axios
    .post<TokenResponse>("/api/v1/auth/refresh", null, { withCredentials: true })
    .then((resp) => {
      setAccessToken(resp.data.access_token);
      return resp.data.access_token;
    })
    .finally(() => {
      refreshPromise = null;
    });
  return refreshPromise;
}

api.interceptors.response.use(
  (resp) => resp,
  async (error: AxiosError<ErrorEnvelope>) => {
    const original = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined;
    const isAuthCall = original?.url?.includes("/auth/");
    if (error.response?.status === 401 && original && !original._retried && !isAuthCall) {
      try {
        original._retried = true;
        await refreshAccessToken();
        return api.request(original);
      } catch {
        // fall through: session expired — surface the original error
        window.dispatchEvent(new CustomEvent("auth:expired"));
      }
    }
    const rawDetail = error.response?.data?.detail;
    // FastAPI request-validation failures send detail as an array of objects
    const detail = Array.isArray(rawDetail)
      ? rawDetail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join("; ")
      : rawDetail;
    const envelope: ErrorEnvelope = detail
      ? { ...error.response!.data, detail }
      : { detail: error.message, code: "ERR_NETWORK", field: null };
    return Promise.reject(envelope);
  },
);

/** Fetch a protected binary (e.g. invoice PDF) with auth and open it in a new tab. */
export async function openPdf(path: string): Promise<void> {
  const resp = await api.get<Blob>(path, { responseType: "blob" });
  const url = URL.createObjectURL(resp.data);
  window.open(url, "_blank");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** Fetch a protected binary with auth and save it under `filename`.
 *  A plain <a href> cannot be used for these: the access token lives in memory and
 *  rides an Authorization header, so a raw document navigation sends no
 *  credentials and the API answers 401. */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const resp = await api.get<Blob>(path, { responseType: "blob" });
  const url = URL.createObjectURL(resp.data);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** Fetch protected HTML (a document preview) with auth, as a string for an iframe srcdoc.
 *  The bearer token can't ride a raw iframe navigation, so we fetch then inject. */
export async function fetchPrintHtml(path: string): Promise<string> {
  const resp = await api.get(path, { responseType: "text", transformResponse: (d) => d });
  return resp.data as string;
}
