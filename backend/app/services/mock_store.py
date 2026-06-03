from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from app.schemas import EventRead, FightRead, FighterRead, PredictionRead, PredictionRunRead, RiskSignalCreate, RiskSignalRead


def _now() -> datetime:
    return datetime.now(timezone.utc)


FIGHTERS: dict[str, FighterRead] = {
    "fighter-pereira": FighterRead(
        id="fighter-pereira",
        name="Alex Pereira",
        stance="Orthodox",
        height_cm=193,
        reach_cm=201,
        record="11-2-0",
        stats={
            "sig_str_acc": 0.51,
            "sig_str_def": 0.62,
            "td_acc": 0.70,
            "td_def": 0.71,
            "strikes_landed_per_min": 6.18,
            "strikes_absorbed_per_min": 3.75,
            "td_avg_per_15": 0.16,
            "sub_avg_per_15": 0.00,
            "raw_fight_count": 9,
            "finish_rate": 0.65,
        },
    ),
    "fighter-ankalaev": FighterRead(
        id="fighter-ankalaev",
        name="Magomed Ankalaev",
        stance="Southpaw",
        height_cm=191,
        reach_cm=191,
        record="20-1-1",
        stats={
            "sig_str_acc": 0.43,
            "sig_str_def": 0.60,
            "td_acc": 0.31,
            "td_def": 0.62,
            "strikes_landed_per_min": 4.12,
            "strikes_absorbed_per_min": 2.35,
            "td_avg_per_15": 0.99,
            "sub_avg_per_15": 0.00,
            "raw_fight_count": 13,
            "finish_rate": 0.28,
        },
    ),
    "fighter-royval": FighterRead(
        id="fighter-royval",
        name="Brandon Royval",
        stance="Orthodox",
        height_cm=175,
        reach_cm=173,
        record="17-7-0",
        stats={
            "sig_str_acc": 0.46,
            "sig_str_def": 0.53,
            "td_acc": 0.38,
            "td_def": 0.48,
            "strikes_landed_per_min": 3.85,
            "strikes_absorbed_per_min": 3.25,
            "finish_rate": 0.64,
        },
    ),
    "fighter-pantoja": FighterRead(
        id="fighter-pantoja",
        name="Alexandre Pantoja",
        stance="Orthodox",
        height_cm=165,
        reach_cm=173,
        record="29-5-0",
        stats={
            "sig_str_acc": 0.49,
            "sig_str_def": 0.52,
            "td_acc": 0.45,
            "td_def": 0.67,
            "strikes_landed_per_min": 4.41,
            "strikes_absorbed_per_min": 4.09,
            "finish_rate": 0.58,
        },
    ),
    "fighter-gaethje": FighterRead(
        id="fighter-gaethje",
        name="Justin Gaethje",
        stance="Orthodox",
        height_cm=180,
        reach_cm=178,
        record="27-5-0",
        stats={
            "sig_str_acc": 0.60,
            "sig_str_def": 0.53,
            "td_acc": 0.25,
            "td_def": 0.75,
            "strikes_landed_per_min": 7.35,
            "strikes_absorbed_per_min": 7.12,
            "finish_rate": 0.78,
        },
    ),
    "fighter-poirier": FighterRead(
        id="fighter-poirier",
        name="Dustin Poirier",
        stance="Southpaw",
        height_cm=175,
        reach_cm=183,
        record="30-9-0",
        stats={
            "sig_str_acc": 0.50,
            "sig_str_def": 0.53,
            "td_acc": 0.36,
            "td_def": 0.63,
            "strikes_landed_per_min": 5.49,
            "strikes_absorbed_per_min": 4.34,
            "finish_rate": 0.70,
        },
    ),
    "fighter-muhammad": FighterRead(
        id="fighter-muhammad",
        name="Belal Muhammad",
        stance="Orthodox",
        height_cm=180,
        reach_cm=183,
        record="24-3-0",
        stats={
            "sig_str_acc": 0.43,
            "sig_str_def": 0.58,
            "td_acc": 0.35,
            "td_def": 0.93,
            "strikes_landed_per_min": 4.55,
            "strikes_absorbed_per_min": 3.64,
            "finish_rate": 0.33,
        },
    ),
    "fighter-della": FighterRead(
        id="fighter-della",
        name="Jack Della Maddalena",
        stance="Switch",
        height_cm=180,
        reach_cm=185,
        record="17-2-0",
        stats={
            "sig_str_acc": 0.53,
            "sig_str_def": 0.66,
            "td_acc": 0.20,
            "td_def": 0.69,
            "strikes_landed_per_min": 6.84,
            "strikes_absorbed_per_min": 3.74,
            "finish_rate": 0.82,
        },
    ),
    "fighter-krylov": FighterRead(
        id="fighter-krylov",
        name="Nikita Krylov",
        stance="Orthodox",
        height_cm=191,
        reach_cm=196,
        record="30-9-0",
        stats={
            "sig_str_acc": 0.56,
            "sig_str_def": 0.47,
            "td_acc": 0.38,
            "td_def": 0.53,
            "strikes_landed_per_min": 4.34,
            "strikes_absorbed_per_min": 2.89,
            "finish_rate": 0.93,
        },
    ),
    "fighter-rakovic": FighterRead(
        id="fighter-rakovic",
        name="Aleksandar Rakovic",
        stance="Orthodox",
        height_cm=193,
        reach_cm=198,
        record="14-4-0",
        stats={
            "sig_str_acc": 0.52,
            "sig_str_def": 0.53,
            "td_acc": 0.23,
            "td_def": 0.90,
            "strikes_landed_per_min": 4.01,
            "strikes_absorbed_per_min": 2.40,
            "finish_rate": 0.71,
        },
    ),
}

FIGHTS: dict[str, FightRead] = {
    "fight-pereira-ankalaev": FightRead(
        id="fight-pereira-ankalaev",
        event_id="event-315",
        fighter_a=FIGHTERS["fighter-pereira"],
        fighter_b=FIGHTERS["fighter-ankalaev"],
        weight_class="Light Heavyweight",
        scheduled_rounds=5,
        headline=True,
    ),
    "fight-royval-pantoja": FightRead(
        id="fight-royval-pantoja",
        event_id="event-315",
        fighter_a=FIGHTERS["fighter-royval"],
        fighter_b=FIGHTERS["fighter-pantoja"],
        weight_class="Flyweight",
        scheduled_rounds=3,
    ),
    "fight-gaethje-poirier": FightRead(
        id="fight-gaethje-poirier",
        event_id="event-315",
        fighter_a=FIGHTERS["fighter-gaethje"],
        fighter_b=FIGHTERS["fighter-poirier"],
        weight_class="Lightweight",
        scheduled_rounds=3,
    ),
    "fight-muhammad-della": FightRead(
        id="fight-muhammad-della",
        event_id="event-315",
        fighter_a=FIGHTERS["fighter-muhammad"],
        fighter_b=FIGHTERS["fighter-della"],
        weight_class="Welterweight",
        scheduled_rounds=3,
    ),
    "fight-krylov-rakovic": FightRead(
        id="fight-krylov-rakovic",
        event_id="event-315",
        fighter_a=FIGHTERS["fighter-krylov"],
        fighter_b=FIGHTERS["fighter-rakovic"],
        weight_class="Middleweight",
        scheduled_rounds=3,
    ),
}

EVENTS: dict[str, EventRead] = {
    "event-315": EventRead(
        id="event-315",
        name="UFC 315",
        event_date=date.today() + timedelta(days=35),
        location="Bell Centre, Montreal, Canada",
        fights=[
            FIGHTS["fight-pereira-ankalaev"],
            FIGHTS["fight-royval-pantoja"],
            FIGHTS["fight-gaethje-poirier"],
            FIGHTS["fight-muhammad-della"],
            FIGHTS["fight-krylov-rakovic"],
        ],
    ),
    "event-330": EventRead(
        id="event-330",
        name="UFC 330",
        event_date=date.today() + timedelta(days=90),
        location="Philadelphia, PA",
        fights=[],
    ),
}

RUNS: dict[str, PredictionRunRead] = {}
PREDICTIONS: dict[str, PredictionRead] = {}
RISK_SIGNALS: dict[str, RiskSignalRead] = {}


def list_upcoming_events() -> list[EventRead]:
    return [deepcopy(event) for event in EVENTS.values()]


def search_events(query: str) -> list[EventRead]:
    normalized = query.lower().strip()
    return [
        deepcopy(event)
        for event in EVENTS.values()
        if normalized in event.name.lower() or normalized in event.location.lower()
    ]


def get_event(event_id: str) -> EventRead | None:
    event = EVENTS.get(event_id)
    return deepcopy(event) if event else None


def get_fight(fight_id: str) -> FightRead | None:
    fight = FIGHTS.get(fight_id)
    return deepcopy(fight) if fight else None


def create_run(run_type: str, message: str, result: dict | None = None) -> PredictionRunRead:
    run_id = str(uuid4())
    run = PredictionRunRead(
        id=run_id,
        run_type=run_type,
        status="completed",
        progress=100,
        message=message,
        result=result or {},
        created_at=_now(),
        updated_at=_now(),
    )
    RUNS[run_id] = run
    return run


def get_run(run_id: str) -> PredictionRunRead | None:
    return RUNS.get(run_id)


def save_prediction(prediction: PredictionRead) -> PredictionRead:
    PREDICTIONS[prediction.id] = prediction
    return prediction


def get_prediction(prediction_id: str) -> PredictionRead | None:
    return PREDICTIONS.get(prediction_id)


def add_risk_signal(payload: RiskSignalCreate) -> RiskSignalRead:
    signal = RiskSignalRead(id=str(uuid4()), created_at=_now(), **payload.model_dump())
    RISK_SIGNALS[signal.id] = signal
    return signal


def list_fight_risks(fight_id: str) -> list[RiskSignalRead]:
    return [signal for signal in RISK_SIGNALS.values() if signal.fight_id == fight_id]
