import { AnimatePresence, motion } from "framer-motion";
import {
  Activity,
  BarChart3,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  Crosshair,
  Flame,
  Gauge,
  Radio,
  RefreshCw,
  Search,
  Settings,
  ShieldAlert,
  Swords,
  Target,
  Users,
  XCircle,
  Zap,
} from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link, NavLink, Route, Routes, useNavigate, useParams } from "react-router-dom";
import {
  analyzeCard,
  analyzeFight,
  buildEventReview,
  createRiskSignal,
  extractFightIntelligence,
  getAccuracyStats,
  getAdminDataStatus,
  getEventReview,
  getCachedPrediction,
  getEvent,
  getFight,
  getFightOdds,
  getFightPredictions,
  getPastEvents,
  getPrediction,
  getPredictionHistory,
  getUpcomingEvents,
  listEventReviews,
  recordAllResults,
  refreshFight,
  refresh1winOdds,
  refreshLiveOdds,
  refreshFightIntelligence,
  retrainModel,
  scrapeHistorical,
  searchEvents,
  syncUpcomingEvents,
  adminSyncUpcomingEvents,
} from "./api";
import { makeMockPrediction } from "./data/mock";
import type { AccuracyStats, AdminDataStatus, Event, EventReview, EventReviewSummary, Fight, Fighter, FightReview, OddsSnapshot, PastEvent, Prediction, PredictionRun } from "./types";

const pageMotion = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -6 },
  transition: { duration: 0.2 },
};

function App() {
  return (
    <div className="app-shell">
      <DesktopSidebar />
      <main className="screen">
        <AnimatePresence mode="wait">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/card/:eventId" element={<CardPage />} />
            <Route path="/fight/:fightId" element={<FightPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/admin" element={<AdminPage />} />{/* dev-only, not in nav */}
          </Routes>
        </AnimatePresence>
      </main>
      <BottomNav />
    </div>
  );
}

/* ── Dashboard / Events Home ── */
function Dashboard() {
  const [events, setEvents] = useState<Event[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncingEvents, setSyncingEvents] = useState(false);
  const [syncMessage, setSyncMessage] = useState("");

  useEffect(() => {
    getUpcomingEvents()
      .then(setEvents)
      .catch(() => setSyncMessage("Backend offline — showing cached data."))
      .finally(() => setLoading(false));
  }, []);

  const leadEvent = events[0];

  async function onSyncEvents() {
    setSyncingEvents(true);
    const run = await syncUpcomingEvents(12);
    setSyncMessage(run.message);
    setEvents(await getUpcomingEvents());
    setSyncingEvents(false);
  }

  return (
    <motion.section {...pageMotion} className="page dashboard-page">
      <div className="dashboard-brand">
        <BrandMark />
      </div>
      {loading ? (
        <div className="event-showcase-skeleton">
          <div className="skeleton-block" style={{ height: "180px", borderRadius: "12px" }} />
        </div>
      ) : leadEvent ? (
        <EventShowcase event={leadEvent} />
      ) : (
        <div className="empty-state">No upcoming events loaded. Press Sync UFC Events.</div>
      )}
      <div className="dashboard-actions">
        <button className="secondary-action" type="button" onClick={onSyncEvents} disabled={syncingEvents}>
          <RefreshCw size={16} className={syncingEvents ? "spin" : ""} />
          Sync UFC Events
        </button>
        <Link to="/search">Search Events</Link>
      </div>
      {syncMessage && (
        <div className="run-banner">
          <RefreshCw size={16} />
          <span>{syncMessage}</span>
        </div>
      )}
      <div className="section-header">
        <h2>Upcoming</h2>
        <Link to="/search">Search</Link>
      </div>
      <div className="event-stack">
        {events.map((event) => (
          <EventRow key={event.id} event={event} />
        ))}
      </div>
    </motion.section>
  );
}

/* ── Search ── */
function SearchPage() {
  const [query, setQuery] = useState("");
  const [events, setEvents] = useState<Event[]>([]);
  const [searching, setSearching] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    setSearching(true);
    getUpcomingEvents().then(setEvents).finally(() => setSearching(false));
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSearching(true);
    try {
      setEvents(!query.trim() ? await getUpcomingEvents() : await searchEvents(query));
    } finally {
      setSearching(false);
    }
  }

  return (
    <motion.section {...pageMotion} className="page">
      <TopBar kicker="Search" title="Cards & Fights" />
      <form className="search-box" onSubmit={onSubmit}>
        <Search size={16} />
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="UFC 315, Pereira, lightweight..." />
        <button type="submit">{searching ? <RefreshCw size={13} className="spin" /> : "Go"}</button>
      </form>
      <div className="event-stack">
        {searching && !events.length ? (
          <div className="empty-state"><RefreshCw size={18} className="spin" /> Loading events...</div>
        ) : !events.length ? (
          <div className="empty-state">No events found for "{query}"</div>
        ) : (
          events.map((event) => (
            <button key={event.id} className="event-row button-row" onClick={() => navigate(`/card/${event.id}`)}>
              <EventRowContent event={event} />
            </button>
          ))
        )}
      </div>
    </motion.section>
  );
}

/* ── Card Page ── */
function CardPage() {
  const { eventId = "" } = useParams();
  const [event, setEvent] = useState<Event>();
  const [run, setRun] = useState<PredictionRun>();
  const [cardPredictions, setCardPredictions] = useState<Record<string, Prediction>>({});
  const [busy, setBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  useEffect(() => {
    setRun(undefined);
    setCardPredictions({});
    setErrorMessage("");
    getEvent(eventId)
      .then(async (loadedEvent) => {
        setEvent(loadedEvent);
        // Auto-load cached predictions for all fights
        if (loadedEvent) {
          const cached = await Promise.all(
            loadedEvent.fights.map((f) => getCachedPrediction(f.id).then((p) => [f.id, p] as const))
          );
          const map: Record<string, Prediction> = {};
          for (const [id, pred] of cached) {
            if (pred) map[id] = pred;
          }
          setCardPredictions(map);
        }
      })
      .catch(() => setErrorMessage("Event not available."));
  }, [eventId]);

  async function onAnalyzeCard() {
    if (!event) return;
    setBusy(true);
    try {
      setErrorMessage("");
      const nextRun = await analyzeCard(eventId);
      setRun(nextRun);
      const updatedEvent = await getEvent(eventId);
      if (updatedEvent) setEvent(updatedEvent);
      const predictionIds = Array.isArray(nextRun.result.prediction_ids)
        ? nextRun.result.prediction_ids.filter((id): id is string => typeof id === "string")
        : [];
      const predictions = predictionIds.length
        ? (await Promise.all(predictionIds.map((id) => getPrediction(id)))).filter((p): p is Prediction => Boolean(p))
        : [];
      setCardPredictions(Object.fromEntries(predictions.map((p) => [p.fight_id, p])));
    } catch {
      setErrorMessage("Card analysis only works for real future UFCStats events.");
    } finally {
      setBusy(false);
    }
  }

  if (!event) return <LoadingPage label="Loading card" />;

  const fightGroups = groupFightsBySection(event.fights);
  const predictedCount = Object.keys(cardPredictions).length;
  const totalFights = event.fights.length;

  return (
    <motion.section {...pageMotion} className="page">
      <TopBar kicker={formatDate(event.event_date)} title={event.name} />
      <div className="card-actions">
        <button className="primary-action" onClick={onAnalyzeCard} disabled={busy}>
          {busy ? <RefreshCw size={16} className="spin" /> : <Zap size={16} />}
          Analyze Card
        </button>
        <span>{event.location}</span>
      </div>
      {predictedCount > 0 && (
        <div className="card-analysis-summary">
          <CheckCircle2 size={14} />
          <span>{predictedCount === totalFights ? "Full card analyzed" : `${predictedCount}/${totalFights} bouts analyzed`}</span>
        </div>
      )}
      {run && <RunBanner run={run} />}
      {errorMessage && (
        <div className="run-banner error-banner">
          <ShieldAlert size={16} />
          <span>{errorMessage}</span>
        </div>
      )}
      <div className="fight-list">
        {fightGroups.length ? (
          fightGroups.map(({ section, fights }) => (
            <div className="card-section" key={section}>
              <div className="card-section-title">{section}</div>
              {fights.map((fight) => (
                <FightRow key={fight.id} fight={fight} prediction={cardPredictions[fight.id]} />
              ))}
            </div>
          ))
        ) : (
          <div className="empty-state">Fight card is waiting for announced bouts.</div>
        )}
      </div>
    </motion.section>
  );
}

/* ── Fight / Matchup Page ── */
function FightPage() {
  const { fightId = "" } = useParams();
  const [fight, setFight] = useState<Fight>();
  const [prediction, setPrediction] = useState<Prediction>();
  const [predictionVersion, setPredictionVersion] = useState<number>(1);
  const [totalVersions, setTotalVersions] = useState<number>(0);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshingIntel, setRefreshingIntel] = useState(false);
  const [refreshingOdds, setRefreshingOdds] = useState(false);
  const [runMessage, setRunMessage] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [cacheLoaded, setCacheLoaded] = useState(false);
  const [odds, setOdds] = useState<OddsSnapshot[]>([]);

  useEffect(() => {
    setErrorMessage("");
    setCacheLoaded(false);
    getFight(fightId)
      .then(setFight)
      .catch(() => setErrorMessage("Fight not available as a future announced matchup."));

    // Auto-load cached prediction on page open
    getCachedPrediction(fightId).then((cached) => {
      if (cached) {
        setPrediction(cached);
        setPredictionVersion(cached.version ?? 1);
      }
      setCacheLoaded(true);
    });

    getFightPredictions(fightId).then((versions) => {
      setTotalVersions(versions.length);
    });
    getFightOdds(fightId).then(setOdds);
  }, [fightId]);

  async function onAnalyze() {
    setBusy(true);
    try {
      setErrorMessage("");
      const result = await analyzeFight(fightId);
      setPrediction(result);
      setPredictionVersion(result.version ?? 1);
      const updatedFight = await getFight(fightId);
      if (updatedFight) setFight(updatedFight);
      const versions = await getFightPredictions(fightId);
      setTotalVersions(versions.length);
    } catch {
      setErrorMessage("Analyze only works for real future booked fights.");
    } finally {
      setBusy(false);
    }
  }

  async function onRefresh() {
    setRefreshing(true);
    const run = await refreshFight(fightId);
    setRunMessage(run.message);
    const updatedFight = await getFight(fightId);
    if (updatedFight) setFight(updatedFight);
    setRefreshing(false);
  }

  async function onRefreshIntel() {
    setRefreshingIntel(true);
    const run = await refreshFightIntelligence(fightId);
    setRunMessage(run.message);
    setRefreshingIntel(false);
  }

  async function onRefreshOdds() {
    setRefreshingOdds(true);
    try {
      const run = await refreshLiveOdds(fightId);
      setRunMessage(run.message);
      setOdds(await getFightOdds(fightId));
    } catch {
      setRunMessage("Odds fetch failed. Add THE_ODDS_API_KEY to backend/.env — free key at the-odds-api.com");
    } finally {
      setRefreshingOdds(false);
    }
  }

  if (!fight) {
    return errorMessage ? (
      <section className="page loading-page">
        <ShieldAlert size={24} />
        <span>{errorMessage}</span>
        <Link to="/">Back to events</Link>
      </section>
    ) : (
      <LoadingPage label="Loading fight" />
    );
  }

  const activePrediction = prediction ?? makePendingPrediction(fight);
  const hasCachedPrediction = Boolean(prediction);
  const predictionAge = prediction ? formatPredictionAge(prediction.created_at) : null;

  return (
    <motion.section {...pageMotion} className="page fight-page">
      <div className="fight-hero">
        <div className="fight-topline">
          <span>{fight.weight_class}</span>
          <span>{fight.scheduled_rounds} rounds</span>
        </div>
        <div className="matchup-stage">
          <div className="fighter-silhouette left" />
          <FighterName fighter={fight.fighter_a} align="left" />
          <MatchupGauge prediction={activePrediction} />
          <FighterName fighter={fight.fighter_b} align="right" />
          <div className="fighter-silhouette right" />
        </div>
        <HeroStatPanel fight={fight} />
        <PredictionMiniCard prediction={activePrediction} />
        {hasCachedPrediction && predictionAge && (
          <div className="prediction-meta">
            <span className="prediction-age">Prediction from {predictionAge}</span>
            {totalVersions > 1 && (
              <span className="prediction-version">Run {totalVersions}× total</span>
            )}
          </div>
        )}
        <div className="hero-actions">
          <button className="secondary-action" onClick={onRefresh} disabled={refreshing} title="Pull the latest fighter stats and fight history from UFCStats">
            <RefreshCw size={15} className={refreshing ? "spin" : ""} />
            Update Stats
          </button>
          <button className="secondary-action" onClick={onRefreshIntel} disabled={refreshingIntel} title="Check for news, injuries and fight-week signals">
            <Radio size={15} className={refreshingIntel ? "spin" : ""} />
            Check News
          </button>
          <button className="secondary-action" onClick={onRefreshOdds} disabled={refreshingOdds} title="Pull live UFC odds via The Odds API (free key at the-odds-api.com)">
            <Gauge size={15} className={refreshingOdds ? "spin" : ""} />
            Live Odds
          </button>
          <button className="primary-action" onClick={onAnalyze} disabled={busy}>
            {busy ? <RefreshCw size={15} className="spin" /> : <Activity size={15} />}
            {hasCachedPrediction ? "Re-run Prediction" : "Get Prediction"}
          </button>
        </div>
      </div>
      {errorMessage && <div className="run-banner error-banner"><ShieldAlert size={15} /><span>{errorMessage}</span></div>}
      {runMessage && <div className="run-banner"><RefreshCw size={15} /><span>{runMessage}</span></div>}
      {activePrediction.data_quality === 0 && activePrediction.likely_method !== "pending" && (
        <div className="run-banner error-banner" style={{ background: "rgba(255,83,61,0.12)", borderColor: "var(--red)" }}>
          <ShieldAlert size={15} />
          <span>No fighter data available — this prediction is a coin flip and should not be used</span>
        </div>
      )}
      {activePrediction.data_quality > 0 && activePrediction.data_quality < 50 && (
        <div className="run-banner error-banner" style={{ background: "rgba(255,179,65,0.10)", borderColor: "var(--amber)", color: "var(--amber)" }}>
          <ShieldAlert size={15} />
          <span>Low data ({activePrediction.data_quality}/100) — stats are incomplete, treat this as a rough estimate only</span>
        </div>
      )}
      <FighterProfilePanel fight={fight} />
      <LiveOddsPanel fight={fight} odds={odds} prediction={activePrediction} />
      <FighterComparison fight={fight} />
      {activePrediction.style_profile_a && activePrediction.style_profile_b && (
        <StyleBreakdownPanel
          profileA={activePrediction.style_profile_a}
          profileB={activePrediction.style_profile_b}
          nameA={fight.fighter_a.name}
          nameB={fight.fighter_b.name}
          fightLocation={activePrediction.fight_location_estimate}
        />
      )}
      <FightIntelligencePanel prediction={activePrediction} />
      <RecentFormPanel fight={fight} />
      <InsightGrid prediction={activePrediction} />
      <RelevantTapePanel prediction={activePrediction} />
      <FeatureSnapshotPanel prediction={activePrediction} />
      <MethodPanel prediction={activePrediction} />
      <AddIntelPanel fightId={fightId} fight={fight} />
    </motion.section>
  );
}

/* ── Add Intel Panel (on fight page) ── */
function AddIntelPanel({ fightId, fight }: { fightId: string; fight: Fight }) {
  const [open, setOpen] = useState(false);
  const [fighterId, setFighterId] = useState(fight.fighter_a.id);
  const [signalType, setSignalType] = useState("bad_weight_cut");
  const [severity, setSeverity] = useState("medium");
  const [impactScore, setImpactScore] = useState("-0.04");
  const [summary, setSummary] = useState("");
  const [message, setMessage] = useState("");

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!summary.trim()) { setMessage("Add a summary first."); return; }
    await createRiskSignal({
      fighter_id: fighterId,
      fight_id: fightId,
      signal_type: signalType,
      severity,
      confidence: "medium",
      source: "personal observation",
      summary,
      impact_score: Number(impactScore),
    });
    setSummary("");
    setMessage("Saved. Re-analyze to apply it to the prediction.");
  }

  return (
    <section className="panel add-intel-panel">
      <button className="add-intel-toggle" type="button" onClick={() => setOpen((v) => !v)}>
        <ShieldAlert size={15} />
        <span>Add fight-week intel <small>— injuries, weight cuts, news</small></span>
        <ChevronRight size={14} className={open ? "rotated" : ""} />
      </button>
      {open && (
        <form className="add-intel-form" onSubmit={onSubmit}>
          <div className="add-intel-row">
            <label>
              Fighter
              <select value={fighterId} onChange={(e) => setFighterId(e.target.value)}>
                <option value={fight.fighter_a.id}>{fight.fighter_a.name}</option>
                <option value={fight.fighter_b.id}>{fight.fighter_b.name}</option>
              </select>
            </label>
            <label>
              Signal
              <select value={signalType} onChange={(e) => setSignalType(e.target.value)}>
                <option value="bad_weight_cut">Bad weight cut</option>
                <option value="confirmed_injury">Confirmed injury</option>
                <option value="illness">Illness</option>
                <option value="missed_weight">Missed weight</option>
                <option value="short_notice">Short notice</option>
                <option value="camp_change">Camp change</option>
                <option value="recent_ko_loss">Recent KO loss</option>
                <option value="odds_movement">Odds movement</option>
                <option value="analyst_pick">Analyst pick</option>
                <option value="public_sentiment">Fan sentiment</option>
              </select>
            </label>
            <label>
              Severity
              <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </label>
            <label>
              Impact
              <select value={impactScore} onChange={(e) => setImpactScore(e.target.value)}>
                <option value="0.04">Medium positive</option>
                <option value="0.02">Small positive</option>
                <option value="0">Informational</option>
                <option value="-0.02">Small negative</option>
                <option value="-0.04">Medium negative</option>
                <option value="-0.08">Major negative</option>
              </select>
            </label>
          </div>
          <textarea value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="e.g. Looked drained at weigh-ins, camp switch confirmed last week..." rows={2} />
          <div className="add-intel-actions">
            <button className="primary-action" type="submit">
              <ShieldAlert size={14} />
              Save Signal
            </button>
            {message && <span className="add-intel-message">{message}</span>}
          </div>
        </form>
      )}
    </section>
  );
}

/* ── Intel Feed Page ── */
function IntelPage() {
  const [events, setEvents] = useState<Event[]>([]);
  const [activeTab, setActiveTab] = useState<"fighter_a" | "fighter_b">("fighter_a");

  useEffect(() => { getUpcomingEvents().then(setEvents); }, []);

  const leadEvent = events[0];
  const leadFight = leadEvent?.fights[0];

  const fighterA = leadFight?.fighter_a;
  const fighterB = leadFight?.fighter_b;

  const mockIntelItems: Array<{
    source: string;
    time: string;
    text: string;
    severity: "HIGH" | "MEDIUM" | "LOW" | "GENERAL";
    icon: React.ReactNode;
  }> = activeTab === "fighter_a"
    ? [
        { source: "MMA Junkie", time: "2h ago", text: "Minor knee issue reported in final open workout", severity: "HIGH", icon: <ShieldAlert size={14} /> },
        { source: "ESPN", time: "5h ago", text: "Weight cut reportedly ahead of schedule", severity: "MEDIUM", icon: <Activity size={14} /> },
        { source: "Sherdog", time: "1d ago", text: "New striking coach added for camp", severity: "LOW", icon: <Swords size={14} /> },
        { source: "MMA Fighting", time: "1d ago", text: "Confident comments during media day", severity: "LOW", icon: <Users size={14} /> },
        { source: "Bloody Elbow", time: "2d ago", text: "Focused and locked in heading into fight week", severity: "GENERAL", icon: <Target size={14} /> },
      ]
    : [
        { source: "MMA Junkie", time: "4h ago", text: "Training camp looking sharp in final preparations", severity: "LOW", icon: <Activity size={14} /> },
        { source: "ESPN", time: "6h ago", text: "No injury concerns reported ahead of bout", severity: "GENERAL", icon: <ShieldAlert size={14} /> },
        { source: "RT Sport", time: "1d ago", text: "Fighter confident about gameplan execution", severity: "LOW", icon: <Users size={14} /> },
      ];

  return (
    <motion.section {...pageMotion} className="page">
      <TopBar kicker="Fight Week" title="Intel Feed" />
      {leadFight ? (
        <>
          <div className="intel-tabs">
            <button
              className={`intel-tab${activeTab === "fighter_a" ? " active" : ""}`}
              onClick={() => setActiveTab("fighter_a")}
            >
              {fighterA?.name?.split(" ").pop()?.toUpperCase() ?? "Fighter A"}
            </button>
            <button
              className={`intel-tab${activeTab === "fighter_b" ? " active" : ""}`}
              onClick={() => setActiveTab("fighter_b")}
            >
              {fighterB?.name?.split(" ").pop()?.toUpperCase() ?? "Fighter B"}
            </button>
          </div>
          <div className="intel-feed">
            {mockIntelItems.map((item, i) => (
              <div className="intel-card" key={i}>
                <div className={`intel-card-bar ${item.severity}`} />
                <div className="intel-card-content">
                  <div className="intel-card-source">
                    {item.icon}
                    <span>{item.source}</span>
                    &nbsp;·&nbsp;{item.time}
                  </div>
                  <div className="intel-card-text">{item.text}</div>
                </div>
                <span className={`intel-badge ${item.severity}`}>{item.severity}</span>
              </div>
            ))}
          </div>
        </>
      ) : (
        <div className="empty-state">Sync upcoming events to see fight-week intel.</div>
      )}
    </motion.section>
  );
}

/* ── History Page ── */
function HistoryPage() {
  const [accuracy, setAccuracy] = useState<AccuracyStats | null>(null);
  const [pastEvents, setPastEvents] = useState<PastEvent[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getAccuracyStats(), getPastEvents(20)])
      .then(([acc, events]) => {
        setAccuracy(acc);
        setPastEvents(events);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const hasRealData = pastEvents.length > 0;

  return (
    <motion.section {...pageMotion} className="page">
      <TopBar kicker="Model Log" title="History" />

      {/* Accuracy Dashboard */}
      <div className="history-accuracy-dashboard">
        {accuracy && accuracy.total_predictions > 0 ? (
          <>
            <div className="history-accuracy-hero">
              <strong>{Math.round(accuracy.winner_accuracy * 100)}%</strong>
              <span>Winner accuracy</span>
              <small>{accuracy.total_predictions} predictions scored</small>
            </div>
            <div className="history-accuracy-grid">
              <div className="history-acc-stat">
                <strong>{Math.round(accuracy.method_accuracy * 100)}%</strong>
                <span>Method</span>
              </div>
              <div className="history-acc-stat">
                <strong>{Math.round(accuracy.round_accuracy * 100)}%</strong>
                <span>Round bucket</span>
              </div>
              {Object.entries(accuracy.by_confidence).map(([conf, data]) => (
                <div className="history-acc-stat" key={conf}>
                  <strong>{data.total > 0 ? Math.round((data.correct / data.total) * 100) : 0}%</strong>
                  <span>{confidencePlainText(conf, 60)} ({data.total})</span>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="history-accuracy-empty">
            <Target size={32} />
            <span>Accuracy tracking starts from your first predicted fight.</span>
            <small>Use the Matchup page to predict upcoming fights — results will be scored here automatically after each event.</small>
          </div>
        )}
      </div>

      {/* Past Events Archive */}
      {loading ? (
        <LoadingPage label="Loading history" />
      ) : hasRealData ? (
        <div className="history-events-list">
          <div className="section-subheader">Past Events — tap to see your picks vs. the result</div>
          {pastEvents.map((event) => {
            const isOpen = expanded === event.id;
            const predictedCount = event.fights.filter((f) => f.prediction).length;
            const correctCount = event.fights.filter((f) => f.winner_correct === true).length;
            const scoredCount = event.fights.filter((f) => f.winner_correct !== null && f.winner_correct !== undefined).length;
            return (
              <div className="history-event-card" key={event.id}>
                <button
                  className="history-event-header"
                  onClick={() => setExpanded(isOpen ? null : event.id)}
                >
                  <div>
                    <strong>{event.name}</strong>
                    <span>{formatDate(event.event_date)} · {event.location}</span>
                  </div>
                  <div className="history-event-meta">
                    {scoredCount > 0 && (
                      <span className="accuracy-badge">
                        {correctCount}/{scoredCount} correct
                        {" "}({Math.round(correctCount / scoredCount * 100)}%)
                      </span>
                    )}
                    {predictedCount > 0 && scoredCount === 0 && (
                      <span className="predicted-badge">{predictedCount} predicted</span>
                    )}
                    <ChevronRight size={16} className={isOpen ? "rotated" : ""} />
                  </div>
                </button>
                {isOpen && (
                  <div className="history-event-fights">
                    {event.fights.map((fight) => {
                      const pred = fight.prediction;
                      const favoredIsA = pred ? pred.adjusted_probability_a >= 0.5 : null;
                      const predicted = pred ? (favoredIsA ? fight.fighter_a : fight.fighter_b) : null;
                      const predictedPct = pred
                        ? Math.round(Math.max(pred.adjusted_probability_a, 1 - pred.adjusted_probability_a) * 100)
                        : null;
                      const predictedMethod = pred?.likely_method;
                      return (
                        <div className="history-fight-row" key={fight.fight_id}>
                          <div className="history-fight-names">
                            <strong>{fight.fighter_a} vs {fight.fighter_b}</strong>
                            <small>{fight.weight_class}</small>
                          </div>
                          <div className="history-fight-detail">
                            {predicted ? (
                              <span>
                                Pick: <strong>{predicted.split(" ").pop()}</strong> {predictedPct}%
                                {predictedMethod && predictedMethod !== "pending" && ` · ${predictedMethod}`}
                              </span>
                            ) : (
                              <span className="no-pick">No prediction</span>
                            )}
                            {fight.actual_winner && (
                              <span>
                                Result: <strong>{fight.actual_winner.split(" ").pop()}</strong>
                                {fight.actual_method && ` · ${fight.actual_method}`}
                                {fight.actual_round && ` R${fight.actual_round}`}
                              </span>
                            )}
                            {pred && fight.method_correct !== null && fight.method_correct !== undefined && (
                              <small style={{ color: fight.method_correct ? "var(--green)" : "var(--muted)" }}>
                                Method: {fight.method_correct ? "✓" : "✗"}
                              </small>
                            )}
                          </div>
                          {fight.winner_correct !== null && fight.winner_correct !== undefined ? (
                            <div className={`outcome-icon ${fight.winner_correct ? "correct" : "wrong"}`}>
                              {fight.winner_correct ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
                            </div>
                          ) : (
                            <div className="outcome-icon pending">—</div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="history-empty-state">
          <Gauge size={36} />
          <strong>No past events yet</strong>
          <span>Go to Admin, import historical events, and run predictions to build your track record.</span>
        </div>
      )}
    </motion.section>
  );
}

/* ── Admin Page ── */
function AdminPage() {
  const [events, setEvents] = useState<Event[]>([]);
  const [fightId, setFightId] = useState("");
  const [fighterId, setFighterId] = useState("");
  const [signalType, setSignalType] = useState("bad_weight_cut");
  const [severity, setSeverity] = useState("medium");
  const [summary, setSummary] = useState("");
  const [impactScore, setImpactScore] = useState("-0.04");
  const [message, setMessage] = useState("");
  const [importing, setImporting] = useState(false);
  const [syncingUpcoming, setSyncingUpcoming] = useState(false);
  const [importMessage, setImportMessage] = useState("");
  const [training, setTraining] = useState(false);
  const [trainingMessage, setTrainingMessage] = useState("");
  const [recordingResults, setRecordingResults] = useState(false);
  const [recordMessage, setRecordMessage] = useState("");
  const [dataStatus, setDataStatus] = useState<AdminDataStatus>();
  const [intelText, setIntelText] = useState("");
  const [intelSource, setIntelSource] = useState("manual_text");
  const [intelFighterId, setIntelFighterId] = useState("");
  const [extractingIntel, setExtractingIntel] = useState(false);
  const [intelMessage, setIntelMessage] = useState("");
  const [reviewEventId, setReviewEventId] = useState("");
  const [buildingReview, setBuildingReview] = useState(false);
  const [currentReview, setCurrentReview] = useState<EventReview | null>(null);
  const [reviewSummaries, setReviewSummaries] = useState<EventReviewSummary[]>([]);
  const [reviewMessage, setReviewMessage] = useState("");

  useEffect(() => {
    getUpcomingEvents().then((loadedEvents) => {
      setEvents(loadedEvents);
      const firstFight = loadedEvents.flatMap((e) => e.fights)[0];
      if (firstFight) {
        setFightId(firstFight.id);
        setFighterId(firstFight.fighter_a.id);
      }
    });
    getAdminDataStatus().then(setDataStatus).catch(() => undefined);
    listEventReviews(20).then(setReviewSummaries).catch(() => undefined);
  }, []);

  const fights = events.flatMap((e) => e.fights);
  const selectedFight = fights.find((f) => f.id === fightId);

  useEffect(() => {
    if (!selectedFight) return;
    if (![selectedFight.fighter_a.id, selectedFight.fighter_b.id].includes(fighterId)) {
      setFighterId(selectedFight.fighter_a.id);
    }
    if (intelFighterId && ![selectedFight.fighter_a.id, selectedFight.fighter_b.id].includes(intelFighterId)) {
      setIntelFighterId("");
    }
  }, [fighterId, selectedFight]);

  async function onSubmitRisk(e: FormEvent) {
    e.preventDefault();
    if (!selectedFight || !fighterId || !summary.trim()) {
      setMessage("Choose a fight, fighter, and summary first.");
      return;
    }
    await createRiskSignal({
      fighter_id: fighterId,
      fight_id: selectedFight.id,
      signal_type: signalType,
      severity,
      confidence: "medium",
      source: "personal observation",
      summary,
      impact_score: Number(impactScore),
    });
    setSummary("");
    setMessage("Risk signal saved. Re-analyze the fight to apply it.");
  }

  async function onImportHistorical() {
    setImporting(true);
    try {
      const job = await scrapeHistorical(5);
      setImportMessage(job.message);
      setDataStatus(await getAdminDataStatus());
    } catch {
      setImportMessage("Historical import failed. Check that the backend is running.");
    } finally {
      setImporting(false);
    }
  }

  async function onSyncUpcoming() {
    setSyncingUpcoming(true);
    try {
      const job = await adminSyncUpcomingEvents(12);
      setImportMessage(job.message);
      setEvents(await getUpcomingEvents());
      setDataStatus(await getAdminDataStatus());
    } catch {
      setImportMessage("Upcoming event sync failed.");
    } finally {
      setSyncingUpcoming(false);
    }
  }

  async function onRetrainModel() {
    setTraining(true);
    try {
      const job = await retrainModel();
      setTrainingMessage(job.message);
      setDataStatus(await getAdminDataStatus());
    } catch {
      setTrainingMessage("Model training failed. Import more history first.");
    } finally {
      setTraining(false);
    }
  }

  async function onRecordResults() {
    setRecordingResults(true);
    setRecordMessage("");
    try {
      const job = await recordAllResults();
      setRecordMessage(job.message);
      setDataStatus(await getAdminDataStatus());
      // also refresh review summaries after recording
      setReviewSummaries(await listEventReviews(20));
    } catch {
      setRecordMessage("Result recording failed. Check that the backend is running.");
    } finally {
      setRecordingResults(false);
    }
  }

  async function onBuildReview() {
    if (!reviewEventId) {
      setReviewMessage("Select an event first.");
      return;
    }
    setBuildingReview(true);
    setReviewMessage("");
    setCurrentReview(null);
    try {
      const review = await buildEventReview(reviewEventId);
      setCurrentReview(review);
      setReviewSummaries(await listEventReviews(20));
      setReviewMessage(`Review built: ${review.winner_correct}/${review.fights_with_predictions} winners correct (${review.winner_accuracy != null ? Math.round(review.winner_accuracy * 100) + "%" : "N/A"})`);
    } catch {
      setReviewMessage("Could not build review. Make sure fight results are recorded first.");
    } finally {
      setBuildingReview(false);
    }
  }

  async function onLoadReview(eventId: string) {
    const review = await getEventReview(eventId);
    if (review) {
      setCurrentReview(review);
      setReviewEventId(eventId);
    }
  }

  async function onExtractIntel(e: FormEvent) {
    e.preventDefault();
    if (!selectedFight || intelText.trim().length < 20) {
      setIntelMessage("Paste at least a short paragraph of article or interview text.");
      return;
    }
    setExtractingIntel(true);
    try {
      const result = await extractFightIntelligence({
        fight_id: selectedFight.id,
        text: intelText,
        source: intelSource,
        fighter_id: intelFighterId || null,
        create_signals: true,
      });
      setIntelMessage(result.message);
      if (result.signals.length) {
        setIntelText("");
      }
    } catch {
      setIntelMessage("Could not extract intelligence. Check that the backend is running.");
    } finally {
      setExtractingIntel(false);
    }
  }

  return (
    <motion.section {...pageMotion} className="page">
      <TopBar kicker="Admin" title="Controls" />
      <div className="admin-grid">
        <AdminTile icon={<RefreshCw size={18} />} title="Import" text="Historical fights" />
        <AdminTile icon={<BarChart3 size={18} />} title="Train" text="Baseline model" />
        <AdminTile icon={<ShieldAlert size={18} />} title="Signals" text="Risk & injury" />
      </div>
      {dataStatus && (
        <div className="admin-status-grid">
          <Metric icon={<CalendarDays size={16} />} label="Events" value={`${dataStatus.completed_events}`} />
          <Metric icon={<Swords size={16} />} label="Fights" value={`${dataStatus.completed_fights}`} />
          <Metric icon={<Activity size={16} />} label="Examples" value={`${dataStatus.training_examples}/${dataStatus.required_examples}`} />
          <Metric icon={<ShieldAlert size={16} />} label="Missing Stats" value={`${dataStatus.fighters_missing_stats}`} />
          <Metric icon={<BarChart3 size={16} />} label="Models" value={`${dataStatus.model_versions}`} />
        </div>
      )}
      {dataStatus && dataStatus.fighter_missing_stats_ratio > 0.2 && (
        <div className="run-banner error-banner">
          <ShieldAlert size={15} />
          <span>{Math.round(dataStatus.fighter_missing_stats_ratio * 100)}% of fighters are missing stats. Run the alternate fighter stats scraper before trusting predictions.</span>
        </div>
      )}
      <div className="admin-action-band">
        <div>
          <strong>Upcoming Events</strong>
          <span>Sync announced UFC cards, fighters, dates, weight classes.</span>
        </div>
        <button className="secondary-action" type="button" onClick={onSyncUpcoming} disabled={syncingUpcoming}>
          <RefreshCw size={15} className={syncingUpcoming ? "spin" : ""} />
          Sync Events
        </button>
      </div>
      <div className="admin-action-band">
        <div>
          <strong>Historical Data</strong>
          <span>Import next uncached completed UFCStats events.</span>
        </div>
        <button className="secondary-action" type="button" onClick={onImportHistorical} disabled={importing}>
          <RefreshCw size={15} className={importing ? "spin" : ""} />
          Import 5 Events
        </button>
      </div>
      {importMessage && <div className="run-banner"><RefreshCw size={15} /><span>{importMessage}</span></div>}
      <div className="admin-action-band">
        <div>
          <strong>Winner Model</strong>
          <span>Train logistic regression from imported completed fights.</span>
        </div>
        <button className="secondary-action" type="button" onClick={onRetrainModel} disabled={training}>
          <BarChart3 size={15} className={training ? "spin" : ""} />
          Train Baseline
        </button>
      </div>
      {trainingMessage && <div className="run-banner"><BarChart3 size={15} /><span>{trainingMessage}</span></div>}
      <div className="admin-action-band">
        <div>
          <strong>Record Fight Results</strong>
          <span>After any UFC event — scores predictions vs actual results, populates History page.</span>
        </div>
        <button className="secondary-action" type="button" onClick={onRecordResults} disabled={recordingResults}>
          <CheckCircle2 size={15} className={recordingResults ? "spin" : ""} />
          {recordingResults ? "Recording…" : "Record Results"}
        </button>
      </div>
      {recordMessage && <div className="run-banner"><CheckCircle2 size={15} /><span>{recordMessage}</span></div>}

      {/* ── Post-Event Review ── */}
      <div className="admin-section-header">Post-Event Review</div>
      <div className="admin-action-band">
        <div>
          <strong>Build Event Review</strong>
          <span>Compare predictions vs actual results and save lessons learned.</span>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <select
            value={reviewEventId}
            onChange={(e) => setReviewEventId(e.target.value)}
            style={{ fontSize: 13, padding: "4px 8px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)" }}
          >
            <option value="">Select completed event…</option>
            {events.map((e) => (
              <option key={e.id} value={e.id}>{e.name}</option>
            ))}
          </select>
          <button className="secondary-action" type="button" onClick={onBuildReview} disabled={buildingReview || !reviewEventId}>
            <BarChart3 size={15} className={buildingReview ? "spin" : ""} />
            {buildingReview ? "Analyzing…" : "Build Review"}
          </button>
        </div>
      </div>
      {reviewMessage && <div className="run-banner"><BarChart3 size={15} /><span>{reviewMessage}</span></div>}

      {/* Past reviews list */}
      {reviewSummaries.length > 0 && !currentReview && (
        <div className="review-history-list">
          {reviewSummaries.map((s) => (
            <button key={s.id} className="review-history-item" onClick={() => onLoadReview(s.event_id)}>
              <span className="review-event-name">{s.event_name}</span>
              <span className="review-event-date">{s.event_date ?? ""}</span>
              <span className="review-accuracy">
                {s.winner_accuracy != null ? `${Math.round(s.winner_accuracy * 100)}% winner acc` : "No picks"}
              </span>
              {s.replacements_detected > 0 && (
                <span className="review-replacements">{s.replacements_detected} replacements missed</span>
              )}
            </button>
          ))}
        </div>
      )}

      {/* Active review detail */}
      {currentReview && (
        <div className="event-review-panel">
          <div className="event-review-header">
            <div>
              <h3>{currentReview.event_name}</h3>
              <span>{currentReview.event_date}</span>
            </div>
            <button className="icon-btn" onClick={() => setCurrentReview(null)}>✕</button>
          </div>

          {/* aggregate stats */}
          <div className="review-stats-row">
            <div className="review-stat">
              <span className="review-stat-value">{currentReview.fights_with_predictions}</span>
              <span className="review-stat-label">Fights graded</span>
            </div>
            <div className="review-stat">
              <span className="review-stat-value" style={{ color: "var(--green)" }}>
                {currentReview.winner_accuracy != null ? `${Math.round(currentReview.winner_accuracy * 100)}%` : "—"}
              </span>
              <span className="review-stat-label">Winner accuracy</span>
            </div>
            <div className="review-stat">
              <span className="review-stat-value">
                {currentReview.method_accuracy != null ? `${Math.round(currentReview.method_accuracy * 100)}%` : "—"}
              </span>
              <span className="review-stat-label">Method accuracy</span>
            </div>
            <div className="review-stat">
              <span className="review-stat-value" style={{ color: currentReview.replacements_detected > 0 ? "var(--amber)" : undefined }}>
                {currentReview.replacements_detected}
              </span>
              <span className="review-stat-label">Replacements missed</span>
            </div>
            <div className="review-stat">
              <span className="review-stat-value">{currentReview.no_contests}</span>
              <span className="review-stat-label">No contests</span>
            </div>
          </div>

          {/* mistake categories */}
          {Object.keys(currentReview.mistake_categories).length > 0 && (
            <div className="review-categories">
              {Object.entries(currentReview.mistake_categories).map(([cat, count]) => (
                <span key={cat} className={`review-cat-badge ${cat === "correct" ? "correct" : "mistake"}`}>
                  {cat.replace(/_/g, " ")}: {count}
                </span>
              ))}
            </div>
          )}

          {/* fight-by-fight rows */}
          <div className="fight-review-list">
            {currentReview.fight_reviews.map((fr) => (
              <FightReviewRow key={fr.fight_id} review={fr} />
            ))}
          </div>

          {/* lessons */}
          {currentReview.lessons.length > 0 && (
            <div className="review-lessons">
              <h4>Lessons</h4>
              {currentReview.lessons.map((lesson, i) => (
                <div key={i} className="review-lesson-item">
                  <span className="lesson-bullet">→</span>
                  <span>{lesson}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <form className="signal-form" onSubmit={onExtractIntel}>
        <label>
          Fight-week Intel
          <select value={fightId} onChange={(e) => setFightId(e.target.value)}>
            {fights.map((f) => (
              <option key={f.id} value={f.id}>{f.fighter_a.name} vs {f.fighter_b.name}</option>
            ))}
          </select>
        </label>
        <label>
          Attribute To
          <select value={intelFighterId} onChange={(e) => setIntelFighterId(e.target.value)}>
            <option value="">Auto-detect fighter</option>
            {selectedFight && (
              <>
                <option value={selectedFight.fighter_a.id}>{selectedFight.fighter_a.name}</option>
                <option value={selectedFight.fighter_b.id}>{selectedFight.fighter_b.name}</option>
              </>
            )}
          </select>
        </label>
        <label>
          Source
          <select value={intelSource} onChange={(e) => setIntelSource(e.target.value)}>
            <option value="manual_text">Manual text</option>
            <option value="ufc_official">UFC official</option>
            <option value="mma_news">MMA news</option>
            <option value="interview">Interview</option>
            <option value="reddit">Fan sentiment</option>
          </select>
        </label>
        <textarea
          value={intelText}
          onChange={(e) => setIntelText(e.target.value)}
          placeholder="Paste article, quote, weigh-in note, coach comment, odds note, or interview transcript..."
        />
        <button className="primary-action" type="submit" disabled={extractingIntel}>
          <Radio size={15} className={extractingIntel ? "spin" : ""} />
          Extract Intel
        </button>
        {intelMessage && <p className="form-message">{intelMessage}</p>}
      </form>
      <form className="signal-form" onSubmit={onSubmitRisk}>
        <label>
          Fight
          <select value={fightId} onChange={(e) => setFightId(e.target.value)}>
            {fights.map((f) => (
              <option key={f.id} value={f.id}>{f.fighter_a.name} vs {f.fighter_b.name}</option>
            ))}
          </select>
        </label>
        <label>
          Fighter
          <select value={fighterId} onChange={(e) => setFighterId(e.target.value)}>
            {selectedFight && (
              <>
                <option value={selectedFight.fighter_a.id}>{selectedFight.fighter_a.name}</option>
                <option value={selectedFight.fighter_b.id}>{selectedFight.fighter_b.name}</option>
              </>
            )}
          </select>
        </label>
        <label>
          Signal
          <select value={signalType} onChange={(e) => setSignalType(e.target.value)}>
            <option value="bad_weight_cut">Bad weight cut</option>
            <option value="confirmed_injury">Confirmed injury</option>
            <option value="illness">Illness</option>
            <option value="missed_weight">Missed weight</option>
            <option value="short_notice">Short notice</option>
            <option value="camp_change">Camp change</option>
            <option value="recent_ko_loss">Recent KO loss</option>
            <option value="odds_movement">Odds movement</option>
            <option value="analyst_pick">Analyst pick</option>
            <option value="public_sentiment">Fan sentiment</option>
          </select>
        </label>
        <label>
          Severity
          <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </label>
        <label>
          Impact
          <select value={impactScore} onChange={(e) => setImpactScore(e.target.value)}>
            <option value="0.04">Medium positive</option>
            <option value="0.02">Small positive</option>
            <option value="0">Informational</option>
            <option value="-0.02">Small negative</option>
            <option value="-0.04">Medium negative</option>
            <option value="-0.08">Major negative</option>
          </select>
        </label>
        <textarea value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="Looked drained at weigh-ins..." />
        <button className="primary-action" type="submit">
          <ShieldAlert size={15} />
          Save Signal
        </button>
        {message && <p className="form-message">{message}</p>}
      </form>
    </motion.section>
  );
}

/* ── Shared Components ── */

function TopBar({ kicker, title }: { kicker: string; title: string }) {
  return (
    <header className="top-bar">
      <BrandMark />
      <span>{kicker}</span>
      <h1>{title}</h1>
    </header>
  );
}

function BrandMark() {
  return (
    <div className="brand-mark">
      <strong>FIGHT<span>IQ</span></strong>
      <small>Data. Intel. Advantage.</small>
    </div>
  );
}

function EventShowcase({ event }: { event: Event }) {
  const title = formatEventHeroTitle(event.name);
  return (
    <section className="event-showcase">
      <Link to={`/card/${event.id}`} className="hero-fight">
        <div className="hero-matchup">
          <span>Upcoming Event</span>
          <strong className={title.compact ? "compact-event-title" : undefined}>{title.primary}</strong>
          {title.secondary && <b>{title.secondary}</b>}
          <EventCountdown date={event.event_date} />
        </div>
        <div className="hero-footer">
          <span>{event.location}</span>
          <span>{formatDate(event.event_date)}</span>
          <ChevronRight size={18} />
        </div>
      </Link>
      <div className="event-fight-card">
        <span>Fight Card</span>
        {event.fights.map((fight) => (
          <EventFightCardRow key={fight.id} fight={fight} />
        ))}
      </div>
    </section>
  );
}

function EventFightCardRow({ fight, prediction }: { fight: Fight; prediction?: Prediction }) {
  const hasPrediction = prediction && prediction.model_source !== "not_run";
  const favoredIsA = prediction ? prediction.adjusted_probability_a >= 0.5 : true;
  const favoredPct = hasPrediction
    ? Math.round(Math.max(prediction!.adjusted_probability_a, 1 - prediction!.adjusted_probability_a) * 100)
    : null;
  const favoredName = favoredIsA ? fight.fighter_a.name : fight.fighter_b.name;

  return (
    <Link to={`/fight/${fight.id}`} className="event-fight-row">
      <small>{shortWeightClass(fight.weight_class)}</small>
      <div>
        <strong>{fight.fighter_a.name}</strong>
        <em>vs</em>
        <strong>{fight.fighter_b.name}</strong>
        <span>{fight.headline ? "Title Fight" : fight.weight_class}</span>
      </div>
      <i className={favoredPct !== null ? "" : "pending-meter"}>
        <b style={{ width: favoredPct !== null ? `${favoredPct}%` : "0%" }} />
      </i>
      <strong>{favoredPct !== null ? `${favoredName.split(" ").pop()} ${favoredPct}%` : "—"}</strong>
      <ChevronRight size={16} />
    </Link>
  );
}

function EventCountdown({ date }: { date: string }) {
  const diffMs = Math.max(new Date(date).getTime() - Date.now(), 0);
  const days = Math.floor(diffMs / 86_400_000);
  const hours = Math.floor((diffMs % 86_400_000) / 3_600_000);
  const minutes = Math.floor((diffMs % 3_600_000) / 60_000);
  return (
    <div className="event-countdown">
      <span>{days}D</span>
      <span>{hours}H</span>
      <span>{minutes}M</span>
    </div>
  );
}

function formatEventHeroTitle(name: string) {
  const fightNight = name.match(/^UFC Fight Night:\s*(.+)$/i);
  if (fightNight) {
    return {
      primary: "UFC Fight Night",
      secondary: fightNight[1].replace(/\s+vs\.?\s+/i, " vs.\n"),
      compact: true,
    };
  }
  return { primary: name, secondary: "", compact: name.length > 14 };
}

function SectionHeader({ title, actionLabel, to }: { title: string; actionLabel?: string; to?: string }) {
  return (
    <div className="section-header">
      <h2>{title}</h2>
      {actionLabel && to && <Link to={to}>{actionLabel}</Link>}
    </div>
  );
}

function EventRow({ event }: { event: Event }) {
  return (
    <Link to={`/card/${event.id}`} className="event-row">
      <EventRowContent event={event} />
    </Link>
  );
}

function EventRowContent({ event }: { event: Event }) {
  return (
    <>
      <div>
        <span>{formatDate(event.event_date)}</span>
        <strong>{event.name}</strong>
        <small>{event.location}</small>
      </div>
      <div className="event-count">{event.fights.length}</div>
    </>
  );
}

function FightRow({ fight, prediction }: { fight: Fight; prediction?: Prediction }) {
  const aPct = prediction ? Math.round(prediction.adjusted_probability_a * 100) : undefined;
  const favoredName = prediction
    ? prediction.adjusted_probability_a >= 0.5 ? prediction.fighter_a : prediction.fighter_b
    : undefined;
  const favoredPct = prediction
    ? Math.round(Math.max(prediction.adjusted_probability_a, 1 - prediction.adjusted_probability_a) * 100)
    : undefined;
  const confidence = prediction?.confidence ?? "pending";
  const confidenceLabel = prediction ? confidencePlainText(confidence, aPct ?? 50) : "";

  return (
    <Link to={`/fight/${fight.id}`} className={`fight-row${prediction ? " with-prediction" : ""}`}>
      <div className="bout-label">
        {fight.headline ? "Main" : fight.weight_class.slice(0, 2)}
      </div>
      <div>
        <strong>{fight.fighter_a.name}</strong>
        <span>{fight.fighter_b.name}</span>
        {prediction && <small>Pick: {favoredName?.split(" ").pop()} by {prediction.likely_method}</small>}
      </div>
      {prediction ? (
        <div className="fight-prediction-meter">
          <span>{favoredPct}%</span>
          <i><b style={{ width: `${favoredPct}%` }} /></i>
          <small>{confidenceLabel}</small>
        </div>
      ) : (
        <ChevronRight size={18} />
      )}
    </Link>
  );
}

function FighterName({ fighter, align }: { fighter: Fighter; align: "left" | "right" }) {
  return (
    <div className={`fighter-name ${align}`}>
      <span>{fighter.record}</span>
      <strong>{fighter.name}</strong>
      <small>{fighter.stance}</small>
    </div>
  );
}

function MatchupGauge({ prediction }: { prediction: Prediction }) {
  const noData = prediction.data_quality === 0 && prediction.likely_method !== "pending";
  const aPct = Math.round(prediction.adjusted_probability_a * 100);
  const bPct = 100 - aPct;
  const angle = Math.max(0, Math.min(360, aPct * 3.6));

  if (noData) {
    return (
      <div className="win-gauge no-data-gauge">
        <div>
          <strong>No</strong>
          <span>Data</span>
        </div>
        <small>Cannot Predict</small>
      </div>
    );
  }

  return (
    <div className="win-gauge" style={{ "--gauge-angle": `${angle}deg` } as React.CSSProperties}>
      <div>
        <strong>{aPct}%</strong>
        <span>{bPct}%</span>
      </div>
      <small>Win Probability</small>
    </div>
  );
}

function PredictionMiniCard({ prediction }: { prediction: Prediction }) {
  const methodEntries = orderedMethodEntries(prediction.method_probabilities).slice(0, 3);
  const noPick = prediction.pick_grade === "no_pick";

  return (
    <section className="ai-prediction-card">
      <span>{noPick ? "AI Read" : "AI Prediction"}</span>
      <strong>{favoredPredictionText(prediction)}</strong>
      {noPick && prediction.no_pick_reason && <small>{prediction.no_pick_reason}</small>}
      <div className="prediction-pills">
        {methodEntries.map(([method, probability]) => (
          <div key={method}>
            <span>{method}</span>
            <strong>{Math.round(probability * 100)}%</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function makePendingPrediction(fight: Fight): Prediction {
  return {
    id: `pending-${fight.id}`,
    fight_id: fight.id,
    fighter_a: fight.fighter_a.name,
    fighter_b: fight.fighter_b.name,
    base_probability_a: 0.5,
    calibrated_probability_a: 0.5,
    adjusted_probability_a: 0.5,
    fightiq_probability_a: 0.5,
    market_probability_a: null,
    edge: null,
    expected_value: null,
    value_flag: false,
    pick_grade: "no_pick",
    no_pick_reason: "Analyze to generate a Fight IQ read",
    contextual_adjustment: 0,
    risk_adjustment: 0,
    total_adjustment: 0,
    confidence: "low",
    likely_method: "pending",
    data_quality: 0,
    main_factors: ["Analyze to generate model factors"],
    risk_signals: ["No prediction run yet"],
    method_probabilities: { "KO/TKO": 0.25, submission: 0.25, decision: 0.45, other: 0.05 },
    round_estimate: "pending",
    style_summary: "Press Analyze to generate the Fight IQ read.",
    written_analysis: "Press Analyze to generate the written fight breakdown.",
    key_signals: ["Analyze to generate key signals"],
    trust_warnings: ["No prediction run yet"],
    model_notes: ["Model source: not run"],
    intelligence_summary: "No fight-week intelligence collected yet",
    odds_summary: "No odds movement collected yet",
    relevant_fights: [],
    feature_version: "current-v1",
    feature_vector: {},
    model_source: "not_run",
    model_version_id: null,
    model_feature_version: null,
    model_feature_vector: {},
    version: 1,
    is_latest: true,
    created_at: new Date().toISOString(),
  };
}

function HeroStatPanel({ fight }: { fight: Fight }) {
  const noData = hasNoUsableFighterData(fight.fighter_a) && hasNoUsableFighterData(fight.fighter_b);
  const rows = [
    ["Striking Accuracy", "sig_str_acc"],
    ["Takedown Defense", "td_def"],
    ["Finish Rate", "finish_rate"],
    ["Reach", "reach_cm"],
    ["Strikes / Min", "strikes_landed_per_min"],
  ] as const;

  if (noData) {
    return (
      <div className="hero-stat-panel no-stat-panel">
        <strong>No data available</strong>
        <span>Update fighter stats before reading the matchup bars</span>
      </div>
    );
  }

  return (
    <div className="hero-stat-panel">
      {rows.map(([label, key]) => (
        <HeroStatRow
          key={key}
          label={label}
          left={valueFor(fight.fighter_a, key)}
          right={valueFor(fight.fighter_b, key)}
          statKey={key}
        />
      ))}
    </div>
  );
}

function HeroStatRow({ label, left, right, statKey }: { label: string; left?: number; right?: number; statKey: string }) {
  const leftValue = left ?? 0;
  const rightValue = right ?? 0;
  const total = Math.max(leftValue + rightValue, 0.01);
  const leftWidth = Math.max(8, Math.round((leftValue / total) * 100));
  const rightWidth = Math.max(8, Math.round((rightValue / total) * 100));

  return (
    <div className="hero-stat-row">
      <strong>{formatStat(left, statKey)}</strong>
      <i><b style={{ width: `${leftWidth}%` }} /></i>
      <span>{label}</span>
      <i><b style={{ width: `${rightWidth}%` }} /></i>
      <strong>{formatStat(right, statKey)}</strong>
    </div>
  );
}

function FighterProfilePanel({ fight }: { fight: Fight }) {
  const rows: Array<[string, string | undefined | null, string | undefined | null]> = [
    ["Record", fight.fighter_a.record, fight.fighter_b.record],
    ["Age", formatBioValue(fight.fighter_a.age), formatBioValue(fight.fighter_b.age)],
    ["Height", formatStat(fight.fighter_a.height_cm ?? undefined, "height_cm"), formatStat(fight.fighter_b.height_cm ?? undefined, "height_cm")],
    ["Reach", formatStat(fight.fighter_a.reach_cm ?? undefined, "reach_cm"), formatStat(fight.fighter_b.reach_cm ?? undefined, "reach_cm")],
    ["Weight", fight.fighter_a.weight_lbs ? `${fight.fighter_a.weight_lbs} lb` : "-", fight.fighter_b.weight_lbs ? `${fight.fighter_b.weight_lbs} lb` : "-"],
    ["Stance", fight.fighter_a.stance, fight.fighter_b.stance],
    ["Nationality", fight.fighter_a.nationality, fight.fighter_b.nationality],
  ];

  return (
    <section className="panel fighter-profile-panel">
      <div className="panel-title">
        <span>Official Profile</span>
        <strong>{fight.weight_class}</strong>
      </div>
      <div className="profile-table">
        {rows.map(([label, left, right]) => (
          <div className="profile-row" key={label ?? ""}>
            <strong>{left || "-"}</strong>
            <span>{label}</span>
            <strong>{right || "-"}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function LiveOddsPanel({ fight, odds, prediction }: { fight: Fight; odds: OddsSnapshot[]; prediction: Prediction }) {
  const latest = odds[0] ?? oddsFromPrediction(prediction);
  const source = latest?.sportsbook || latest?.source || "1win";
  const marketA = latest?.no_vig_probability_a ?? latest?.implied_probability_a ?? prediction.market_probability_a;
  const marketB = marketA === null || marketA === undefined ? null : 1 - marketA;

  return (
    <section className="panel live-odds-panel">
      <div className="panel-title">
        <span>Live Odds</span>
        <strong>{latest ? source : "1win ready"}</strong>
      </div>
      {latest ? (
        <>
          <div className="odds-board">
            <div>
              <span>{fight.fighter_a.name}</span>
              <strong>{formatAmericanOdds(latest.fighter_a_american_odds)}</strong>
              <small>{formatMarketProbability(marketA)}</small>
            </div>
            <div>
              <span>{fight.fighter_b.name}</span>
              <strong>{formatAmericanOdds(latest.fighter_b_american_odds)}</strong>
              <small>{formatMarketProbability(marketB)}</small>
            </div>
          </div>
          <div className="odds-meta-row">
            <span>{latest.snapshot_type} line</span>
            {prediction.edge !== null && prediction.edge !== undefined && <strong>Edge {formatSignedPercent(prediction.edge)}</strong>}
            {prediction.expected_value !== null && prediction.expected_value !== undefined && <strong>EV {formatSignedPercent(prediction.expected_value)}</strong>}
          </div>
        </>
      ) : (
        <div className="odds-empty-state">
          <span>No live odds stored yet</span>
          <strong>Use 1win Odds to fetch a snapshot, or add manual odds from admin/API.</strong>
        </div>
      )}
    </section>
  );
}

function FighterComparison({ fight }: { fight: Fight }) {
  const groups = [
    {
      title: "Striking",
      rows: [
        ["Strikes landed/min", "strikes_landed_per_min"],
        ["Strikes absorbed/min", "strikes_absorbed_per_min"],
        ["Strike accuracy", "sig_str_acc"],
        ["Strike defense", "sig_str_def"],
      ],
    },
    {
      title: "Grappling",
      rows: [
        ["Takedowns/15 min", "td_avg_per_15"],
        ["Takedown accuracy", "td_acc"],
        ["Takedown defense", "td_def"],
        ["Submissions/15 min", "sub_avg_per_15"],
      ],
    },
    {
      title: "Physical & Record",
      rows: [
        ["Height", "height_cm"],
        ["Reach", "reach_cm"],
        ["UFC fights", "raw_fight_count"],
        ["Finish rate", "finish_rate"],
      ],
    },
  ] as const;

  return (
    <section className="panel">
      <div className="panel-title">
        <span>Fighter Statistics</span>
        <strong>{fight.weight_class}</strong>
      </div>
      <div className="stat-groups">
        {groups.map((group) => (
          <div className="stat-group" key={group.title}>
            <h3>{group.title}</h3>
            <div className="stat-table">
              {group.rows.map(([label, key]) => (
                <div key={key} className="stat-row">
                  <span>{formatStat(valueFor(fight.fighter_a, key), key)}</span>
                  <small>{label}</small>
                  <span>{formatStat(valueFor(fight.fighter_b, key), key)}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function StyleBreakdownPanel({
  profileA,
  profileB,
  nameA,
  nameB,
  fightLocation,
}: {
  profileA: import("./types").StyleProfile;
  profileB: import("./types").StyleProfile;
  nameA: string;
  nameB: string;
  fightLocation?: string | null;
}) {
  function ChinDots({ score }: { score: number }) {
    const filled = Math.round((score / 10) * 5);
    const color = score >= 8 ? "var(--green)" : score >= 5.5 ? "var(--amber)" : score >= 3 ? "var(--orange)" : "var(--red)";
    return (
      <span className="chin-dots">
        {Array.from({ length: 5 }, (_, i) => (
          <span key={i} style={{ color: i < filled ? color : "var(--stroke)", fontSize: "0.85rem" }}>●</span>
        ))}
      </span>
    );
  }

  function FinishBar({ profile }: { profile: import("./types").StyleProfile }) {
    const total = profile.total_wins || 1;
    const koPct = Math.round((profile.ko_wins / total) * 100);
    const subPct = Math.round((profile.sub_wins / total) * 100);
    const decPct = Math.round((profile.decision_wins / total) * 100);
    return (
      <div className="finish-bar-row">
        {koPct > 0 && <span className="finish-chip ko-chip">KO {koPct}%</span>}
        {subPct > 0 && <span className="finish-chip sub-chip">Sub {subPct}%</span>}
        {decPct > 0 && <span className="finish-chip dec-chip">Dec {decPct}%</span>}
        {koPct === 0 && subPct === 0 && decPct === 0 && <span className="finish-chip dec-chip">Dec 100%</span>}
      </div>
    );
  }

  function FighterStyleColumn({ profile, name }: { profile: import("./types").StyleProfile; name: string }) {
    return (
      <div className="style-col">
        <span className="style-name-label">{name.split(" ")[0]}</span>
        <span className="style-badge">{profile.primary_style}</span>
        {profile.secondary_style && (
          <span className="style-secondary">{profile.secondary_style}</span>
        )}
        <div className="weapons-list">
          {profile.main_weapons.map((w) => (
            <span key={w} className="weapon-tag">{w}</span>
          ))}
        </div>
        <FinishBar profile={profile} />
        <div className="chin-row">
          <ChinDots score={profile.chin_score} />
          <span className="chin-label-text">{profile.chin_label} chin</span>
        </div>
      </div>
    );
  }

  const locationText = fightLocation ?? "Contested";
  const locationColor = locationText === "Standing" ? "var(--blue)" : locationText === "Ground" ? "var(--amber)" : "var(--muted)";

  return (
    <section className="panel style-breakdown-panel">
      <div className="panel-title">
        <span>Style Breakdown</span>
        <strong style={{ color: locationColor }}>
          {locationText === "Standing" ? "Feet" : locationText === "Ground" ? "Ground" : "Contested"} fight
        </strong>
      </div>
      <div className="style-breakdown-grid">
        <FighterStyleColumn profile={profileA} name={nameA} />
        <div className="style-divider">
          <span style={{ color: locationColor, fontSize: "0.65rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em" }}>
            {locationText === "Standing" ? "On the feet" : locationText === "Ground" ? "On the mat" : "Can go either way"}
          </span>
        </div>
        <FighterStyleColumn profile={profileB} name={nameB} />
      </div>
    </section>
  );
}

function FightIntelligencePanel({ prediction }: { prediction: Prediction }) {
  const methodLabel = {
    "KO/TKO": "Ends by KO/Stoppage",
    "submission": "Ends by Submission",
    "decision": "Goes to Decision",
    "pending": "Run prediction to see",
  }[prediction.likely_method] ?? prediction.likely_method;

  const roundLabel = {
    "early finish lean": "early rounds",
    "middle rounds finish lean": "mid-fight",
    "late finish lean": "late rounds",
    "decision likely": "",
  }[prediction.round_estimate] ?? prediction.round_estimate;

  const hasIntel = prediction.intelligence_summary &&
    !prediction.intelligence_summary.startsWith("No current") &&
    !prediction.intelligence_summary.toLowerCase().includes("reddit") &&
    !prediction.intelligence_summary.toLowerCase().includes("ranking");
  const hasOdds = prediction.odds_summary && !prediction.odds_summary.startsWith("No odds");

  const cleanSignals = prediction.key_signals
    .map((s) => s.startsWith("Context: ") ? s.slice("Context: ".length) : s)
    .filter(
      (s) =>
        !s.startsWith("Model factor:") &&
        !s.startsWith("learned edge:") &&
        !s.startsWith("Analyze to") &&
        !s.toLowerCase().includes("reddit") &&
        !s.toLowerCase().includes("ranking") &&
        !s.toLowerCase().includes("prime-window") &&
        s.trim().length > 0
    );

  return (
    <section className="panel intel-summary-panel">
      <div className="panel-title">
        <span>The Fight IQ Read</span>
        <strong className="method-tag">{methodLabel}{roundLabel ? ` · ${roundLabel}` : ""}</strong>
      </div>
      <p className="written-analysis">{prediction.written_analysis}</p>
      {cleanSignals.length > 0 && (
        <ul className="key-signals-list">
          {cleanSignals.map((signal) => (
            <li key={signal}>
              <span className="signal-bullet">→</span>
              {signal}
            </li>
          ))}
        </ul>
      )}
      {prediction.trust_warnings.length > 0 && (
        <div className="trust-warning-box">
          <strong>Trust Check</strong>
          <ul>
            {prediction.trust_warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
      {(hasIntel || hasOdds) && (
        <div className="intel-summary-grid">
          {hasIntel && (
            <div>
              <span>Fight week intel</span>
              <strong>{prediction.intelligence_summary}</strong>
            </div>
          )}
          {hasOdds && (
            <div>
              <span>Odds movement</span>
              <strong>{prediction.odds_summary}</strong>
            </div>
          )}
        </div>
      )}
      {prediction.model_notes.length > 0 && (
        <div className="model-note-strip">
          {prediction.model_notes.map((note) => <span key={note}>{note}</span>)}
        </div>
      )}
    </section>
  );
}

function RecentFormPanel({ fight }: { fight: Fight }) {
  return (
    <section className="panel">
      <div className="panel-title">
        <span>Recent Form</span>
        <strong>Last fights</strong>
      </div>
      <div className="recent-form-grid">
        <RecentFighterColumn fighter={fight.fighter_a} />
        <RecentFighterColumn fighter={fight.fighter_b} />
      </div>
    </section>
  );
}

function RecentFighterColumn({ fighter }: { fighter: Fighter }) {
  const fights = fighter.recent_fights?.slice(0, 5) ?? [];
  return (
    <div className="recent-column">
      <div className="recent-column-head">
        <strong>{fighter.name}</strong>
        <span>{fights.length ? `${fights.length} loaded` : "refresh needed"}</span>
      </div>
      {fights.length ? (
        fights.map((fight, index) => (
          <div className="recent-fight" key={`${fighter.id}-${fight.event}-${fight.opponent}-${index}`}>
            <div className={`result-pill ${fight.result.toLowerCase().startsWith("win") ? "win" : "loss"}`}>
              {fight.result.slice(0, 1).toUpperCase()}
            </div>
            <div>
              <strong>{fight.opponent ?? "Opponent TBA"}</strong>
              <span>{fight.method ?? "Method TBA"}{fight.round ? ` - R${fight.round}` : ""}</span>
              <small>{fight.event ?? "Event"}{fight.date ? ` - ${fight.date}` : ""}</small>
              <div className="mini-stat-line">
                <span>Sig {dashNumber(fight.sig_strikes_for)}-{dashNumber(fight.sig_strikes_against)}</span>
                <span>TD {dashNumber(fight.takedowns_for)}-{dashNumber(fight.takedowns_against)}</span>
                <span>KD {dashNumber(fight.knockdowns_for)}-{dashNumber(fight.knockdowns_against)}</span>
              </div>
            </div>
          </div>
        ))
      ) : (
        <div className="recent-empty">Press Refresh Data to load fight history.</div>
      )}
    </div>
  );
}

function InsightGrid({ prediction }: { prediction: Prediction }) {
  // Only show real fight-week signals — not model context flags like age/form adjustments
  const CONTEXT_PHRASES = ["prime-window", "reddit", "ranking", "public sentiment", "recent win trend", "multi-fight skid", "finish-loss durability"];
  const meaningfulRisks = prediction.risk_signals.filter(
    (s) =>
      !s.toLowerCase().startsWith("no ") &&
      !s.toLowerCase().includes("risk signal") &&
      !CONTEXT_PHRASES.some((p) => s.toLowerCase().includes(p))
  );

  return (
    <div className="insight-grid">
      <section className="panel span-2">
        <div className="panel-title">
          <span>How This Fight Plays Out</span>
        </div>
        <p className="style-summary-text">{prediction.style_summary}</p>
      </section>
      {meaningfulRisks.length > 0 && (
        <section className="panel span-2">
          <div className="panel-title">
            <span>Fight Week Flags</span>
            <ShieldAlert size={15} />
          </div>
          <ul className="plain-list">
            {meaningfulRisks.map((risk) => <li key={risk}>{risk}</li>)}
          </ul>
        </section>
      )}
    </div>
  );
}

function RelevantTapePanel({ prediction }: { prediction: Prediction }) {
  const FALLBACK_PHRASES = ["refresh fight history", "will improve after", "stronger context"];
  // Dedupe: keep at most one note per fighter+category combination
  const seen = new Set<string>();
  const items = prediction.relevant_fights
    .filter((note) => !FALLBACK_PHRASES.some((phrase) => note.toLowerCase().includes(phrase)))
    .map(parseTapeNote)
    .filter((item) => {
      // Extract fighter name from title (first word cluster before "was" / "has" / "been")
      const fighterKey = item.body.split(/\s+/).slice(0, 2).join(" ").toLowerCase();
      const key = `${fighterKey}|${item.category}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 4);
  if (!items.length) return null;

  return (
    <section className="panel tape-panel">
      <div className="panel-title">
        <span>Relevant Tape</span>
        <Activity size={15} />
      </div>
      <div className="tape-lead">
        <span>{items[0].category}</span>
        <strong>{items[0].title}</strong>
        <p>{items[0].body}</p>
      </div>
      {items.length > 1 && (
        <div className="tape-list">
          {items.slice(1).map((item) => (
            <div className={`tape-row ${item.tone}`} key={`${item.category}-${item.title}-${item.body}`}>
              <div className="tape-icon">{iconForTapeCategory(item.category)}</div>
              <div>
                <span>{item.category}</span>
                <strong>{item.title}</strong>
                <p>{item.body}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function FeatureSnapshotPanel({ prediction }: { prediction: Prediction }) {
  const entries = Object.entries(prediction.feature_vector ?? {})
    .sort(([, aValue], [, bValue]) => Math.abs(bValue) - Math.abs(aValue))
    .slice(0, 6);

  if (!entries.length) return null;

  return (
    <section className="panel">
      <div className="panel-title">
        <span>What the Model Sees</span>
        <strong className="muted-tag">Top stat edges</strong>
      </div>
      <div className="feature-grid">
        {entries.map(([key, value]) => (
          <div className="feature-chip" key={key}>
            <span>{formatFeatureLabel(key)}</span>
            <strong>{formatFeatureValue(value, key)}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function MethodPanel({ prediction }: { prediction: Prediction }) {
  const entries = orderedMethodEntries(prediction.method_probabilities);
  const methodFriendly: Record<string, string> = {
    "KO/TKO": "KO / Stoppage",
    "submission": "Submission",
    "decision": "Goes to judges",
    "other": "Other",
  };
  return (
    <section className="panel">
      <div className="panel-title">
        <span>How Does It End?</span>
        <strong className="muted-tag">Model probabilities</strong>
      </div>
      <div className="method-stack">
        {entries.map(([method, probability]) => (
          <div key={method} className="method-row">
            <span>{methodFriendly[method] ?? method}</span>
            <div><i style={{ width: `${probability * 100}%` }} /></div>
            <strong>{Math.round(probability * 100)}%</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="metric">
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function AdminTile({ icon, title, text }: { icon: React.ReactNode; title: string; text: string }) {
  return (
    <div className="admin-tile">
      {icon}
      <strong>{title}</strong>
      <span>{text}</span>
    </div>
  );
}

function FightReviewRow({ review }: { review: FightReview }) {
  const [open, setOpen] = useState(false);
  const cats = review.mistake_categories ?? [];
  const isCorrect = cats.includes("correct");
  const isNC = review.is_no_contest;
  const noData = cats.includes("no_data") || cats.includes("no_prediction");
  const isReplacement = cats.includes("rule7_replacement_missed");

  let statusColor = "var(--amber)";
  if (isCorrect) statusColor = "var(--green)";
  if (isNC || noData) statusColor = "var(--muted)";
  if (!isCorrect && !isNC && !noData) statusColor = "var(--red, #ef4444)";

  const dot = isNC ? "○" : isCorrect ? "✓" : "✗";

  return (
    <div className="fight-review-row" style={{ borderLeft: `3px solid ${statusColor}` }}>
      <button className="fight-review-header" onClick={() => setOpen((o) => !o)}>
        <span className="fight-review-dot" style={{ color: statusColor }}>{dot}</span>
        <span className="fight-review-matchup">{review.fighter_a} vs {review.fighter_b}</span>
        <span className="fight-review-result">
          {review.actual_winner
            ? `${review.actual_winner} by ${review.actual_method ?? "?"} R${review.actual_round ?? "?"}`
            : isNC ? "No Contest" : "No result"}
        </span>
        {isReplacement && <span className="review-cat-badge mistake" style={{ fontSize: 11 }}>replacement</span>}
        <ChevronRight size={14} style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform 0.15s" }} />
      </button>
      {open && (
        <div className="fight-review-detail">
          <div className="fight-review-grid">
            <div className="review-col">
              <strong>Predicted</strong>
              <span>{review.predicted_winner ?? "—"} {review.predicted_prob_pct ? `(${review.predicted_prob_pct})` : ""}</span>
              <span>Method: {review.predicted_method ?? "—"}</span>
              <span>Round: {review.predicted_round_bucket ?? "—"}</span>
              <span>Confidence: {review.confidence ?? "—"} | Quality: {review.data_quality ?? "—"}</span>
            </div>
            <div className="review-col">
              <strong>Actual</strong>
              <span>{review.actual_winner ?? "—"}</span>
              <span>Method: {review.actual_method ?? "—"}</span>
              <span>Round: {review.actual_round ?? "—"}</span>
              <span className={review.winner_correct ? "correct-text" : "wrong-text"}>
                Winner: {review.winner_correct === true ? "✓ Correct" : review.winner_correct === false ? "✗ Wrong" : "—"}
              </span>
              <span className={review.method_correct ? "correct-text" : "wrong-text"}>
                Method: {review.method_correct === true ? "✓ Correct" : review.method_correct === false ? "✗ Wrong" : "—"}
              </span>
            </div>
          </div>
          {review.missed_details && (
            <div className="review-missed"><strong>Missed:</strong> {review.missed_details}</div>
          )}
          {review.lesson && (
            <div className="review-lesson"><strong>Lesson:</strong> {review.lesson}</div>
          )}
          {cats.length > 0 && (
            <div className="review-categories" style={{ marginTop: 6 }}>
              {cats.map((c) => (
                <span key={c} className={`review-cat-badge ${c === "correct" ? "correct" : "mistake"}`}>{c.replace(/_/g, " ")}</span>
              ))}
            </div>
          )}
          {review.written_analysis && (
            <details style={{ marginTop: 8 }}>
              <summary style={{ cursor: "pointer", fontSize: 12, color: "var(--muted-text)" }}>Pre-fight notes</summary>
              <p style={{ fontSize: 12, marginTop: 4, color: "var(--muted-text)", lineHeight: 1.5 }}>{review.written_analysis}</p>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

function RunBanner({ run }: { run: PredictionRun }) {
  return (
    <div className="run-banner">
      <Zap size={15} />
      <span>{run.message}</span>
      <strong>{run.progress}%</strong>
    </div>
  );
}

function BottomNav() {
  return (
    <nav className="bottom-nav">
      {navLinks.map(({ to, label, icon: Icon }) => (
        <NavLink key={to} to={to} end={to === "/"}>
          <Icon size={20} />
          <span>{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

function DesktopSidebar() {
  return (
    <aside className="desktop-sidebar">
      <BrandMark />
      <nav>
        {navLinks.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} end={to === "/"}>
            <Icon size={16} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-tools">
        <BarChart3 size={16} />
        <Settings size={16} />
      </div>
    </aside>
  );
}

const navLinks = [
  { to: "/", label: "Events", icon: CalendarDays },
  { to: "/search", label: "Matchup", icon: Swords },
  { to: "/history", label: "History", icon: BarChart3 },
];

function LoadingPage({ label }: { label: string }) {
  return (
    <section className="page loading-page">
      <RefreshCw size={22} className="spin" />
      <span>{label}</span>
    </section>
  );
}

/* ── Utility functions ── */

function groupFightsBySection(fights: Fight[]) {
  const order = ["Main Card", "Prelims", "Early Prelims"];
  const grouped = fights.reduce<Record<string, Fight[]>>((acc, fight, index) => {
    const fallback = index < 5 ? "Main Card" : index < 9 ? "Prelims" : "Early Prelims";
    const section = fight.card_section ?? fallback;
    acc[section] = [...(acc[section] ?? []), fight];
    return acc;
  }, {});
  return order.filter((s) => grouped[s]?.length).map((s) => ({ section: s, fights: grouped[s] }));
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric" }).format(new Date(value));
}

function formatPredictionAge(createdAt: string): string {
  const diffMs = Date.now() - new Date(createdAt).getTime();
  const mins = Math.floor(diffMs / 60_000);
  const hours = Math.floor(diffMs / 3_600_000);
  const days = Math.floor(diffMs / 86_400_000);
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (hours < 24) return `${hours}h ago`;
  return `${days}d ago`;
}

function formatBioValue(value?: number | string | null) {
  if (value === undefined || value === null || value === "") return "-";
  return `${value}`;
}

function shortWeightClass(value: string) {
  const words = value.split(/\s+/).filter(Boolean);
  if (!words.length) return "UFC";
  return words.map((word) => word[0]?.toUpperCase()).join("").slice(0, 3);
}

function formatStat(value: number | undefined | string, key: string): string {
  if (value === undefined || value === null || value === "-") return "-";
  const n = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(n)) return String(value);
  if (key.includes("acc") || key.includes("def") || key.includes("rate")) return `${Math.round(n * 100)}%`;
  if (key.includes("_cm")) return `${Math.round(n)} cm`;
  if (key.includes("count")) return `${Math.round(n)}`;
  return n.toFixed(2);
}

function valueFor(fighter: Fighter, key: string) {
  if (key === "height_cm") return fighter.height_cm ?? undefined;
  if (key === "reach_cm") return fighter.reach_cm ?? undefined;
  return fighter.stats[key];
}

function hasNoUsableFighterData(fighter: Fighter) {
  return Object.keys(fighter.stats || {}).length === 0 && fighter.recent_fights.length === 0;
}

function dashNumber(value: number | null | undefined) { return value ?? "-"; }

function formatFeatureLabel(key: string) {
  const labels: Record<string, string> = {
    striking_output_diff: "Striking output",
    sig_str_defense_diff: "Strike defense",
    takedown_defense_diff: "TD defense",
    takedown_activity_diff: "Takedown vol.",
    recent_win_rate_diff: "Recent form",
    recent_finish_rate_diff: "Finish rate",
    recent_damage_absorbed_delta: "Damage taken",
    recent_sig_strike_diff_delta: "Strike margin",
    reach_cm_diff: "Reach edge",
    ufc_fight_count_diff: "UFC exp.",
    recent_knockdown_absorbed_delta: "KD absorbed",
    recent_knockdown_for_delta: "KD threat",
    height_cm_diff: "Height edge",
    sig_str_accuracy_diff: "Strike acc.",
    submission_activity_diff: "Sub threat",
    takedown_accuracy_diff: "TD accuracy",
  };
  return labels[key] ?? key.replaceAll("_", " ").replace("diff", "edge").replace("delta", "gap");
}

function formatFeatureValue(value: number, key: string) {
  if (key.includes("rate") || key.includes("accuracy") || key.includes("defense")) {
    return `${value > 0 ? "+" : ""}${Math.round(value * 100)}%`;
  }
  return `${value > 0 ? "+" : ""}${value.toFixed(Math.abs(value) >= 10 ? 0 : 2)}`;
}

function formatAmericanOdds(value?: number | null) {
  if (value === undefined || value === null) return "N/A";
  return value > 0 ? `+${Math.round(value)}` : `${Math.round(value)}`;
}

function formatMarketProbability(value?: number | null) {
  if (value === undefined || value === null) return "Market % N/A";
  return `${Math.round(value * 100)}% implied`;
}

function formatSignedPercent(value: number) {
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(1)}%`;
}

function oddsFromPrediction(prediction: Prediction): OddsSnapshot | undefined {
  const raw = prediction.odds_snapshot;
  if (!raw || !Object.keys(raw).length) return undefined;
  return raw as OddsSnapshot;
}

function formatModelSource(source?: string) {
  if (source === "not_run") return "Not run";
  return source?.startsWith("winner_") ? "Trained model" : "Heuristic";
}

function confidencePlainText(confidence: string, aPct: number): string {
  if (confidence === "high") return aPct >= 65 || aPct <= 35 ? "Strong Pick" : "Strong Pick";
  if (confidence === "medium") return "Slight Edge";
  return "Coin Flip";
}

function confidenceExplain(confidence: string, aPct: number): string {
  if (confidence === "high") {
    const pct = Math.max(aPct, 100 - aPct);
    return `The model sees a clear statistical edge here (${pct}%). Still MMA — anything can happen, but the data strongly favors one side.`;
  }
  if (confidence === "medium") {
    return "There's a measurable edge, but this fight is competitive. Small factors like fight-week news or style matchup could flip it.";
  }
  return "The data doesn't separate these fighters clearly. Treat this as a 50/50 and look for live signals before deciding.";
}

function favoredPredictionText(prediction: Prediction) {
  if (prediction.model_source === "not_run") return "Analyze to generate pick";
  if (prediction.pick_grade === "no_pick") return "No official pick";
  const favored = prediction.adjusted_probability_a >= 0.5 ? prediction.fighter_a : prediction.fighter_b;
  return `${favored} by ${prediction.likely_method}`;
}

function orderedMethodEntries(probabilities: Record<string, number>) {
  const order = ["KO/TKO", "submission", "decision", "other"];
  return Object.entries(probabilities).sort(([a], [b]) => order.indexOf(a) - order.indexOf(b));
}

function parseTapeNote(note: string) {
  const category = tapeCategory(note);
  const colonIdx = note.indexOf(":");
  const body = colonIdx > -1 ? note.slice(colonIdx + 1).trim() : note.trim();
  // Pull the first sentence of the body as a short title (up to first "--" or period)
  const shortTitle = body.split(/--|\.(?:\s)/)[0]?.trim().slice(0, 60) ?? "Tape note";
  return { title: shortTitle, body, category, tone: tapeTone(category) };
}

function tapeCategory(note: string) {
  const n = note.toLowerCase();
  if (n.includes("common opponent") || n.includes("both have fought")) return "Common Opponent";
  if (n.includes("grappling sample") || n.includes("taken down")) return "Grappling";
  if (n.includes("volume-striking") || n.includes("significant strikes against")) return "Striking";
  if (n.includes("durability")) return "Durability";
  if (n.includes("damage trend") || n.includes("absorbing more")) return "Damage";
  if (n.includes("finish") || n.includes("stoppage")) return "Finish Risk";
  if (n.includes("loss") || n.includes("dropped")) return "Recent Loss";
  return "Tape Note";
}

function tapeTone(category: string) {
  if (category === "Common Opponent") return "neutral";
  if (category === "Grappling") return "grapple";
  if (category === "Striking") return "strike";
  if (["Durability", "Damage", "Recent Loss"].includes(category)) return "danger";
  if (category === "Finish Risk") return "finish";
  return "neutral";
}

function iconForTapeCategory(category: string) {
  if (category === "Common Opponent") return <Users size={15} />;
  if (category === "Grappling") return <Swords size={15} />;
  if (category === "Striking") return <Crosshair size={15} />;
  if (["Durability", "Damage", "Recent Loss"].includes(category)) return <ShieldAlert size={15} />;
  if (category === "Finish Risk") return <Flame size={15} />;
  return <Activity size={15} />;
}

export default App;
