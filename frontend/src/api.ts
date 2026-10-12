/** The only place that talks to FastAPI. No check logic lives in the client. */

import type {
  Health,
  PromptListResponse,
  ReviewListResponse,
  ReviewRequest,
  ReviewResponse,
  VouchReport,
} from "./types";

// The documented routes, at the root. In dev Vite proxies them to port 8741;
// in a built image FastAPI serves both the API and this bundle on one port.
const BASE = "";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError("Cannot reach the vouch API. Is it running on port 8741?", 0);
  }
  if (!response.ok) {
    throw new ApiError(await detailOf(response), response.status);
  }
  return (await response.json()) as T;
}

/** FastAPI puts the message in `detail`, which is a string or a validation array. */
async function detailOf(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          const field = Array.isArray(item?.loc) ? item.loc.at(-1) : undefined;
          return field ? `${field}: ${item.msg}` : item.msg;
        })
        .join("; ");
    }
  } catch {
    // Fall through to the status text.
  }
  return `${response.status} ${response.statusText}`;
}

export const api = {
  health: () => request<Health>("/health"),

  listPrompts: () => request<PromptListResponse>("/prompts"),

  createReview: (body: ReviewRequest) =>
    request<ReviewResponse>("/reviews", { method: "POST", body: JSON.stringify(body) }),

  listReviews: (limit = 50) => request<ReviewListResponse>(`/reviews?limit=${limit}`),

  getReview: (id: string) => request<VouchReport>(`/reviews/${encodeURIComponent(id)}`),
};
