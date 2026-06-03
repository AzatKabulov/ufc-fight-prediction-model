# FightIQ — Prediction Quality Rules

A living document. Every bug found in production gets a rule added here.
Purpose: prevent the same mistake from being shipped twice.

---

## RULE 1 — Never predict from empty data

**What happened:**  
Baraniewski vs Elekana showed 50/50 with all dashes in stats. Gaethje showed 0 fight history.
The model ran and produced a number anyway, showing it as a real prediction.

**Root cause:**  
`fighter.profile_stats` is NULL in the DB for fighters whose stats were never scraped.
`_fighter_to_schema()` returns empty `stats={}` and `recent_fights=[]` silently.
The prediction pipeline continues with zeroed-out features and presents a confident result.

**Rule:**  
- If EITHER fighter has `stats == {}` AND `recent_fights == []`, set `data_quality = 0`
- `data_quality = 0` must block the prediction from being stored or displayed as valid
- The UI must show "Insufficient data — cannot predict" instead of a probability number
- The 50/50 coin-flip output must never be surfaced as a real prediction

**Enforcement checkpoint:**  
Before `analyze_fight()` runs, check: `if not fight.fighter_a.stats and not fight.fighter_a.recent_fights` → raise or return a data-insufficient result, do not proceed.

---

## RULE 2 — Fighter style classification requires fight history

**What happened:**  
Topuria was classified as "BJJ Specialist" because his pre-UFC submission wins inflated his `sub_win_rate`.
In reality his UFC run is almost entirely KO finishes (Volkanovski, Holloway, etc).
Gaethje was classified as "Kickboxer" correctly but had 0 fight history loaded, so his chin problems, KO losses, and reckless style were completely invisible.

**Root cause:**  
- Style classification uses ALL-TIME sub_win_rate, not UFC-only or recent-weighted
- If `recent_fights` list is empty, `FighterIdentity` gets all zeros — no chin penalty, no KO loss flag
- The model treated Gaethje as a clean slate with no weaknesses

**Rule:**  
- Style classification must weight RECENT fights (last 8) more than career totals
- If `recent_fights` is empty, `chin_score` must default to a NEUTRAL value (5.0), not the clean-chin bonus (9.0)
- Never grant "Iron chin" label to a fighter with 0 fight history in the DB
- Log a warning in `main_factors` when style was derived from fewer than 3 fights

---

## RULE 3 — Market odds are the ground truth sanity check

**What happened:**  
Topuria vs Gaethje: our model said 53/47. The market had Topuria at ~75-80%.
A 25-point gap between our model and market odds is a signal the model is missing something major — not that the market is wrong.

**Rule:**  
- When `market_probability_a` is available and the gap vs `adjusted_probability_a` exceeds 20 percentage points, set `pick_grade = "no_pick"` and add a trust warning: "Large gap vs market odds — data may be incomplete"
- Never override market consensus with a strong directional pick when data quality is below 70
- The edge calculation (`adjusted - market`) must be shown as negative when the model is on the wrong side of market

---

## RULE 4 — Win streak and recency must have multiplied weight

**What happened:**  
Topuria has an 8-fight win streak with all finishes. `win_streak_diff = 8.0` was in the feature vector but the model only moved the needle ~3% from it.
Gaethje is on the skid of his career (multiple brutal KO losses recently). This should have been a major signal.

**Rule:**  
- `win_streak_diff >= 5` should always push probability by at least 5% toward the streaking fighter
- `recent_ko_loss_flag` must carry heavier weight — a fighter who was stopped cold in their last fight is NOT a coin flip vs a finisher
- `recent_win_rate_diff` and `decayed_win_rate_diff` should be among the top 3 features for fights where one fighter has a clean recent run and the other does not

---

## RULE 5 — Data completeness must be visible in the UI

**What happened:**  
The UI showed professional-looking stat bars for Baraniewski vs Elekana — all showing "-" — with a 50/50 prediction. A user would not know the prediction is meaningless.

**Rule:**  
- When `data_quality < 50`, show a red "LOW DATA" banner above the prediction panel
- When `data_quality == 0`, replace the probability donut with "No data" text — never show 50/50
- The stat comparison bars must show "No data available" as a section header when both fighters have empty stats, not just "-" dashes that look like zeroes
- `StyleBreakdownPanel` must not render at all when style profiles are empty/null (already implemented via the `&&` conditional guard)

---

## RULE 6 — Re-scrape flag when fighter stats are missing

**What happened:**  
44 out of 68 upcoming fights (65%) had at least one fighter with no stats.
This means most predictions are running on incomplete data.

**Root cause:**  
The UFC scraper is blocked by Cloudflare (JS bot challenge). Stats were never populated for fighters added after the scraper went down.

**Rule:**  
- After any event sync, run a data completeness audit: count fighters with `profile_stats = NULL`
- If more than 20% of fighters on a card have no stats, show an admin warning in `/admin/data-status`
- Add `fighters_missing_stats` count to `AdminDataStatusRead` schema
- Manual stat entry via admin UI should be available for main card fighters at minimum

---

## RULE 7 — Fighter replacements corrupt predictions (learned 2026-05-30)

**What happened:**
UFC Fight Night: Song vs Figueiredo had 4 replacement fighters we never detected:
- Carlston Harris replaced Muslim Salikhov (Jake Matthews fight)
- Jose Guruza replaced Jose Henrique (Ding Meng fight)
- Luis Gurule replaced Jesus Aguilar (Rei Tsuruya fight)
- Rodrigo Vera replaced Ramon Taveras (Zhu Kangjie fight)

We predicted against the wrong fighters for all 4. This directly caused wrong picks.

**Rule:**
- Sync the event card within 48h of fight night — replacements happen in the final week
- After sync, re-analyze any fight where fighter_a or fighter_b changed
- Add a `last_synced_at` timestamp to events — warn in UI if card is >48h stale before a fight
- If a fighter has <3 fights in DB → FORCE no_pick (they're likely a last-minute replacement we know nothing about)

---

## RULE 8 — Unknown fighters must suppress pick confidence

**What happened:**
Aoriqileng vs Cody Haddon: Aoriqileng had 1 fight in DB, we still picked Haddon at 77% confidence.
Aoriqileng won. We had no useful data on him — picking at 77% was false confidence.

**Rule:**
- If EITHER fighter has fewer than 3 career fights in DB → `pick_grade = "no_pick"`
- The 3-fight minimum is now enforced in `_pick_grade()` in analyzer.py
- Better to say "no pick" than to confidently pick against a fighter we know nothing about

---

---

## RULE 9 — Grappling identity must reflect current era, not career totals (learned UFC Macau 2026-05-30)

**What happened:**
Song Yadong vs Figueiredo: we classified Figueiredo as "BJJ Specialist who controls the ground phase."
Song Yadong submitted Figueiredo with a guillotine in Round 2.
Figueiredo had been badly stopped by Sandhagen recently — a shot, declining fighter.
Song Yadong's wrestling and clinch grappling had improved significantly in his recent UFC run, but our style profile only saw his striking numbers.

**Root cause:**
- Style classification leaned on lifetime sub_win_rate, not trajectory.
- We correctly identified Figueiredo as a grappler but failed to flag that his recent trajectory is declining and that Song Yadong's recent fight history shows improved clinch control.
- Written analysis said "Figueiredo controlling the ground phase" — this is the exact opposite of what happened.

**Rule:**
- When one fighter's style is grappling-heavy but their recent trajectory is "declining" or they have back-to-back stoppages, add a trust warning: "grappling identity may not reflect current form."
- When the favored fighter (by probability) has a significantly higher trajectory score, factor this asymmetry into the written analysis — don't just describe the style matchup, describe who is improving vs who is declining.
- Never state that a "BJJ Specialist controls the ground phase" without checking their recent grappling win rate specifically — not career totals.

---

## RULE 10 — High probability on a local/home crowd fighter requires market cross-check (learned UFC Macau 2026-05-30)

**What happened:**
Zhang Mingyang vs Alonzo Menifield: we gave Zhang 89% with quality=94 but confidence=low.
Menifield TKO'd Zhang in Round 1. Menifield's danger, aggression, and chin were ignored entirely.
The 89% figure was driven by Zhang's UFC stats in a home environment with a home crowd — a classic local fighter inflation pattern.

**Root cause:**
- Features built from UFCStats give home fighters a structural edge because they often have more fights, better records, and no bad-loss penalty from opponents who are harder to research.
- Menifield had legit KO power that our features scored low because his striking averages are moderate — but his explosive burst finish style is not captured by averages.
- confidence=low + quality=94 + probability=89% is an internal contradiction we did not flag.

**Rule:**
- When `confidence=low` but `adjusted_probability_a >= 0.80`, add a trust warning: "Very high probability with low confidence is a contradiction — do not treat as a strong pick."
- When an event is held in Asia/China with a local fighter on the card, apply a soft prior adjustment skeptical of the local fighter if market odds disagree by >10%.
- `quality=94` does not mean the prediction is reliable — it means the data is complete. A complete dataset with wrong features is still wrong.

---

## RULE 11 — KO artists must be predicted to finish, not go to decision (learned UFC Macau 2026-05-30)

**What happened:**
Sergei Pavlovich vs Tallison Teixeira: we predicted Pavlovich by Decision, mid-rounds.
Pavlovich KO'd Teixeira at 0:39 of Round 1 — one of the fastest HW finishes of the year.
Pavlovich has the highest early-KO rate in the HW division. Every single one of his wins is a KO, almost all in Round 1.

**Root cause:**
- Method model predicted Decision despite Pavlovich's entire identity being an early KO specialist.
- The style cap that prevents over-predicting finishes was working too conservatively here.
- `predicted_top_method=Decision` for Pavlovich is a fundamental model failure regardless of win probability being correct.

**Rule:**
- For any fighter with `ko_win_rate >= 0.85` and `ko_rate_rw >= 0.70`, the method prediction must be KO/TKO unless there is a strong grappling-pressure opponent who can prevent standup.
- Add a fighter archetype flag: "early_finisher" — any fighter with 3+ first-round KO wins in last 5 fights should have this tag, which forces the method prediction to KO/TKO early.
- If the model outputs Decision for an early_finisher, override to KO/TKO early and add a note: "KO finisher history overrides method model."

---

## Summary — Data quality gates (in order)

| Check | Action if fails |
|---|---|
| Both fighters have `stats == {}` | Block prediction, show "Insufficient data" |
| `data_quality < 50` | Show LOW DATA banner, set `pick_grade = "no_pick"` |
| `recent_fights == []` for favored fighter | Set chin_score = 5.0, add trust warning |
| Market gap > 20% and data_quality < 70 | Force `pick_grade = "no_pick"`, add trust warning |
| `win_streak_diff >= 5` | Guarantee at least 5% probability push toward streaking fighter |
| Style derived from < 3 fights | Add "limited fight history" to `trust_warnings` |
| Either fighter has < 3 fights in DB | Force `pick_grade = "no_pick"` — likely a replacement, unknown fighter |
| Card not synced within 48h of event | Re-sync and re-analyze all fights — replacements may have happened |
| `confidence=low` but `probability >= 0.80` | Add trust warning: internal contradiction — do not treat as strong pick |
| Fighter has `ko_win_rate >= 0.85` | Override method prediction to KO/TKO early unless grappling stopper |
| Favored fighter has declining trajectory | Add warning to written analysis — do not describe them as "controlling" their strong phase |
