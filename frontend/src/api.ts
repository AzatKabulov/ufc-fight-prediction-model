import type { AccuracyStats, AdminDataStatus, AdminJob, Event, EventReview, EventReviewSummary, Fight, IntelligenceExtractionPayload, IntelligenceExtractionResult, OddsSnapshot, PastEvent, Prediction, PredictionRun, RiskSignal, RiskSignalPayload } from "./types";
import { makeMockPrediction, mockEvents } from "./data/mock";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8010";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
    ...options,
  });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export async function getUpcomingEvents(): Promise<Event[]> {
  try {
    return await request<Event[]>("/events/upcoming");
  } catch {
    return mockEvents;
  }
}

export async function searchEvents(query: string): Promise<Event[]> {
  try {
    return await request<Event[]>(`/events/search?q=${encodeURIComponent(query)}`);
  } catch {
    return [];
  }
}

export async function getEvent(eventId: string): Promise<Event | undefined> {
  return request<Event>(`/events/${eventId}`);
}

export async function getFight(fightId: string): Promise<Fight | undefined> {
  return request<Fight>(`/fights/${fightId}`);
}

export async function analyzeFight(fightId: string): Promise<Prediction> {
  return request<Prediction>(`/fights/${fightId}/analyze`, { method: "POST" });
}

export async function refreshFight(fightId: string): Promise<PredictionRun> {
  return request<PredictionRun>(`/fights/${fightId}/refresh`, { method: "POST" });
}

export async function refreshFightIntelligence(fightId: string): Promise<PredictionRun> {
  return request<PredictionRun>(`/fights/${fightId}/intelligence/refresh`, { method: "POST" });
}

export async function refresh1winOdds(fightId: string): Promise<PredictionRun> {
  return request<PredictionRun>(`/fights/${fightId}/odds/refresh-1win`, { method: "POST" });
}

export async function refreshLiveOdds(fightId: string): Promise<PredictionRun> {
  try {
    return await request<PredictionRun>(`/fights/${fightId}/odds/refresh-theodds`, { method: "POST" });
  } catch {
    return {
      id: "odds-fail",
      run_type: "refresh_theodds",
      status: "failed",
      progress: 100,
      message: "Could not fetch odds. Add THE_ODDS_API_KEY to backend/.env",
      result: {},
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
  }
}

export async function getFightOdds(fightId: string): Promise<OddsSnapshot[]> {
  try {
    return await request<OddsSnapshot[]>(`/fights/${fightId}/odds`);
  } catch {
    return [];
  }
}

export async function analyzeCard(eventId: string): Promise<PredictionRun> {
  return request<PredictionRun>(`/cards/${eventId}/analyze`, { method: "POST" });
}

export async function syncUpcomingEvents(limit = 12): Promise<PredictionRun> {
  try {
    return await request<PredictionRun>(`/events/upcoming/refresh?limit=${limit}`, { method: "POST" });
  } catch {
    return {
      id: "local-upcoming-sync",
      run_type: "sync_upcoming_events",
      status: "failed",
      progress: 100,
      message: "Could not sync live upcoming UFC events. Check backend network access.",
      result: {},
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
  }
}

export async function getPrediction(predictionId: string): Promise<Prediction | undefined> {
  try {
    return await request<Prediction>(`/predictions/${predictionId}`);
  } catch {
    return undefined;
  }
}

export async function getPredictionHistory(): Promise<Prediction[]> {
  try {
    return await request<Prediction[]>("/predictions?limit=20");
  } catch {
    return [];
  }
}

export async function getCachedPrediction(fightId: string): Promise<Prediction | null> {
  try {
    return await request<Prediction>(`/fights/${fightId}/prediction`);
  } catch {
    return null;
  }
}

export async function getFightPredictions(fightId: string): Promise<Prediction[]> {
  try {
    return await request<Prediction[]>(`/fights/${fightId}/predictions`);
  } catch {
    return [];
  }
}

export async function getPastEvents(limit = 20): Promise<PastEvent[]> {
  try {
    return await request<PastEvent[]>(`/history/events?limit=${limit}`);
  } catch {
    return [];
  }
}

export async function getAccuracyStats(): Promise<AccuracyStats | null> {
  try {
    return await request<AccuracyStats>("/history/accuracy");
  } catch {
    return null;
  }
}

export async function createRiskSignal(payload: RiskSignalPayload): Promise<RiskSignal> {
  return request<RiskSignal>("/risk-signals", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function extractFightIntelligence(payload: IntelligenceExtractionPayload): Promise<IntelligenceExtractionResult> {
  return request<IntelligenceExtractionResult>("/intelligence/extract", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function scrapeHistorical(limit = 1): Promise<AdminJob> {
  return request<AdminJob>(`/admin/scrape-historical?limit=${limit}`, { method: "POST" });
}

export async function retrainModel(): Promise<AdminJob> {
  return request<AdminJob>("/admin/retrain-model", { method: "POST" });
}

export async function adminSyncUpcomingEvents(limit = 12): Promise<AdminJob> {
  return request<AdminJob>(`/admin/sync-upcoming-events?limit=${limit}`, { method: "POST" });
}

export async function getAdminDataStatus(): Promise<AdminDataStatus> {
  return request<AdminDataStatus>("/admin/data-status");
}

export async function recordAllResults(): Promise<AdminJob> {
  try {
    return await request<AdminJob>("/admin/record-results-all", { method: "POST" });
  } catch {
    return {
      run_id: "record-results-all",
      status: "failed",
      message: "Could not record results. Check that the backend is running.",
    };
  }
}

export async function buildEventReview(eventId: string): Promise<EventReview> {
  return request<EventReview>(`/admin/event-review/${eventId}`, { method: "POST" });
}

export async function getEventReview(eventId: string): Promise<EventReview | null> {
  try {
    return await request<EventReview>(`/admin/event-review/${eventId}`);
  } catch {
    return null;
  }
}

export async function listEventReviews(limit = 20): Promise<EventReviewSummary[]> {
  try {
    return await request<EventReviewSummary[]>(`/admin/event-reviews?limit=${limit}`);
  } catch {
    return [];
  }
}
