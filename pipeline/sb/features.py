"""Model features (SPEC 4.6, WP3c): consistency, change detector, opponent effect.

    feats = compute(run, run.objs[2], CONFIG)     # run: sb.model_runner.ModelRun
    feats.team_year[team]          -> {cons_sd, cons_n, change_flag, change_event, change_z,
                                       opp_effect, opp_effect_se, opp_n}
    feats.team_event[(team, ev)]   -> {change_k, change_mean_resid, change_z, change_flag}
    feats.sigma                    -> pooled SD of qual residuals this season (this model)
    diagnostics(run, feats)        -> calibration and caveat numbers for the WP report / Models tab

Residual of an updated team-match: `resid = attrib[0] - pre[0]` (total slot), where `pre` is the
team's unrounded pre-match vector (`run.pre_vec`, else upstream's 2-decimal `pre_epas`).
Only matches in `run.updated` count (the model really updated that team), and only quals.

Consistency   cons_sd = sample SD (ddof 1) of resid over the team's updated quals this season,
              cons_n = how many; cons_sd is None below `cons_min_n` (4).
Change        per team-event: k = min(3, updated quals there), needs k >= 2;
              change_mean_resid = mean(attr_epa of those k matches) - pre_epa entering the event
              (fixed baseline = pre vector at the team's first match there, as exported);
              sigma = pooled SD of all qual resid of the season for this model;
              change_z = change_mean_resid / (sigma / sqrt(k)); flag +1 / -1 beyond +-2, else 0.
              Team-year fields come from the team's latest event (processing order) with a flag.
Opponent      o_j = opposing alliance's actual no_foul - its predicted no_foul (alliance_pred
              slot 0), a whole-alliance quantity (not divided by 3).  opp_effect = mean o_j,
              opp_effect_se = SD / sqrt(n); all None below `opp_min_n` (12).  Experimental.

Ranges       match_sd = SD of a team's resid, shrunk toward sigma (k0 pseudo-matches);
              epa_se = c * match_sd / sqrt(min(n + n0, nmax)). Calibrated so that
              EPA +- 1.96 * epa_se held the team's next-6-match level ~95% of the time
              (2022-2024 fit; 2025 and 2026 held out). se_timeline gives epa_se after each update.

Nothing here changes a model or reads a match before it is played: it is a pure function of a
finished ModelRun, and every number is a summary of residuals the model already produced.
"""
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

CONFIG: Dict[str, Any] = {
    "z_threshold": 3.0,    # |change_z| above this flags a change (shown in "How this rating works")
    "k_max": 3,            # first matches at an event used by the detector
    "k_min": 2,            # fewer matches than this: no verdict (None)
    "cons_min_n": 4,       # consistency needs at least this many matches
    "opp_min_n": 12,       # opponent effect needs at least this many matches
    "opp_sig_mult": 2.0,   # the site shows opp_effect only when |opp_effect| > this * opp_effect_se
    # EPA ranges (calibrated on 2022-2024 quals; see pipeline/validate/ranges.md)
    "range_c": {"epa": 1.80, "fast": 1.71},  # scale so EPA +- 1.96*epa_se covers ~95%
    "range_k0": 8,         # match_sd shrinks toward the season's pooled SD with this many pseudo-matches
    "range_n0": 5,         # effective matches behind a rating = min(n + n0, nmax)
    "range_nmax": 6,       # EPA keeps adapting, so its memory is short
}


@dataclass
class SeasonFeatures:
    team_year: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    team_event: Dict[Tuple[int, str], Dict[str, Any]] = field(default_factory=dict)
    sigma: Optional[float] = None
    n_resid: int = 0
    config: Dict[str, Any] = field(default_factory=dict)
    # not exported: inputs for diagnostics()
    detail: Dict[str, Any] = field(default_factory=dict)


def _sd(xs: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    a = np.asarray(xs, dtype=float)
    return float(a.std(ddof=1))


def _pre_total(run: Any, m: Any, team: int) -> Optional[float]:
    pv = getattr(run, "pre_vec", None)
    if pv:
        v = pv.get((m.key, team))
        if v is not None:
            return float(v[0])
    d = (getattr(m, "pre_epas", None) or {}).get(str(team))
    if d is None:
        return None
    x = d.get("epa") if isinstance(d, dict) else getattr(d, "epa", None)
    return None if x is None else float(x)


def range_se(resids: List[float], sigma: Optional[float], cfg: Dict[str, Any], model_id: str) -> Tuple[Optional[float], Optional[float]]:
    """(match_sd, epa_se) from a team's qual residuals so far."""
    if not sigma or sigma <= 0:
        return None, None
    n = len(resids)
    k0 = float(cfg["range_k0"])
    ss = 0.0
    if n >= 2:
        a = np.asarray(resids, dtype=float)
        ss = float(((a - a.mean()) ** 2).sum())
    sig2 = (k0 * sigma * sigma + ss) / (k0 + max(n - 1, 0))
    c = float((cfg.get("range_c") or {}).get(model_id, 1.8))
    neff = min(n + float(cfg["range_n0"]), float(cfg["range_nmax"]))
    msd = math.sqrt(sig2)
    return msd, c * msd / math.sqrt(neff)


def _num(x: Any) -> Optional[float]:
    if x is None:
        return None
    x = float(x)
    return None if (math.isnan(x) or math.isinf(x)) else x


def compute(run: Any, events: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> SeasonFeatures:
    cfg = {**CONFIG, **(config or {})}
    matches = run.objs[4]
    order = sorted(matches.values(), key=lambda m: m.time)  # == calc.py / export.processing_order
    updated = run.updated
    attrib = run.attrib
    apred = run.alliance_pred
    k_max = int(cfg["k_max"])

    season_resid: Dict[int, List[float]] = {}
    ev_attr: Dict[Tuple[int, str], List[float]] = {}   # first k_max updated-qual attr totals
    ev_base: Dict[Tuple[int, str], float] = {}         # pre total at the team's first match there
    ev_first_seq: Dict[Tuple[int, str], int] = {}
    ev_prior_n: Dict[Tuple[int, str], int] = {}        # updated quals this season before the event
    team_events_seen: Dict[int, List[str]] = {}
    opp_o: Dict[int, List[float]] = {}
    opp_pred: Dict[int, List[float]] = {}
    opp_teams: Dict[int, List[int]] = {}
    all_resid: List[float] = []
    res_seq: Dict[int, List[Tuple[int, float]]] = {}
    pos_resid: Dict[int, List[float]] = {}             # resid of the 1st, 2nd, 3rd qual at an event

    for seq, m in enumerate(order):
        red, blue = list(m.get_red()), list(m.get_blue())
        ap = apred.get(m.key)
        for side, teams in ((0, red), (1, blue)):
            for t in teams:
                t = int(t)
                key = (t, m.event)
                if key not in ev_base:
                    p0 = _pre_total(run, m, t)
                    if p0 is not None:
                        ev_base[key] = p0
                        ev_first_seq[key] = seq
                        ev_prior_n[key] = len(season_resid.get(t, ()))
                        team_events_seen.setdefault(t, []).append(m.event)
                if m.elim or (m.key, t) not in updated:
                    continue
                at = attrib.get(m.key, {}).get(t)
                pre0 = _pre_total(run, m, t)
                if at is None or pre0 is None:
                    continue
                a0 = _num(at[0])
                if a0 is None:
                    continue
                r = a0 - pre0
                season_resid.setdefault(t, []).append(r)
                res_seq.setdefault(t, []).append((seq, r))
                all_resid.append(r)
                lst = ev_attr.setdefault(key, [])
                if len(lst) < k_max:
                    pos_resid.setdefault(len(lst), []).append(r)
                    lst.append(a0)
                # opponent effect: the OTHER alliance's actual vs predicted no_foul
                if ap is not None:
                    pred_opp = _num(ap[1 - side][0])
                    act_opp = _num(getattr(m, "blue_no_foul" if side == 0 else "red_no_foul", None))
                    if pred_opp is not None and act_opp is not None:
                        opp_o.setdefault(t, []).append(act_opp - pred_opp)
                        opp_pred.setdefault(t, []).append(pred_opp)
                        opp_teams.setdefault(t, []).extend(int(x) for x in (blue if side == 0 else red))

    sigma = _sd(all_resid)
    feats = SeasonFeatures(sigma=sigma, n_resid=len(all_resid), config=dict(cfg))
    mid = getattr(run, "model_id", "epa")
    se_timeline: Dict[int, List[Tuple[int, float]]] = {}
    for t, seqs in res_seq.items():
        rs: List[float] = []
        tl = []
        for sq, r in seqs:
            rs.append(r)
            tl.append((sq, range_se(rs, sigma, cfg, mid)[1]))
        se_timeline[t] = tl
    se_start = range_se([], sigma, cfg, mid)[1]
    zthr = float(cfg["z_threshold"])

    # ---- change detector, per team-event ----
    for key, attrs in ev_attr.items():
        k = len(attrs)
        base = ev_base.get(key)
        rec: Dict[str, Any] = {"change_k": k, "change_mean_resid": None, "change_z": None, "change_flag": None}
        if k >= int(cfg["k_min"]) and base is not None:
            mean_resid = float(np.mean(attrs)) - base
            rec["change_mean_resid"] = mean_resid
            if sigma and sigma > 0:
                z = mean_resid / (sigma / math.sqrt(k))
                rec["change_z"] = z
                rec["change_flag"] = 1 if z > zthr else (-1 if z < -zthr else 0)
        feats.team_event[key] = rec

    # ---- team-year ----
    by_team_events: Dict[int, List[Tuple[int, str]]] = {}
    for key in feats.team_event:
        by_team_events.setdefault(key[0], []).append(key)
    for t, rs in season_resid.items():
        n = len(rs)
        ty: Dict[str, Any] = {
            "cons_sd": _sd(rs) if n >= int(cfg["cons_min_n"]) else None, "cons_n": n,
            "change_flag": None, "change_event": None, "change_z": None,
            "opp_effect": None, "opp_effect_se": None, "opp_n": None,
        }
        ty["match_sd"], ty["epa_se"] = range_se(rs, sigma, cfg, mid)
        flagged = [k for k in by_team_events.get(t, []) if feats.team_event[k]["change_flag"] is not None]
        if flagged:
            last = max(flagged, key=lambda k: ev_first_seq.get(k, -1))
            ty["change_flag"] = feats.team_event[last]["change_flag"]
            ty["change_event"] = last[1]
            ty["change_z"] = feats.team_event[last]["change_z"]
        os_ = opp_o.get(t, [])
        if len(os_) >= int(cfg["opp_min_n"]):
            sd = _sd(os_)
            ty["opp_effect"] = float(np.mean(os_))
            ty["opp_effect_se"] = None if sd is None else sd / math.sqrt(len(os_))
            ty["opp_n"] = len(os_)
        feats.team_year[t] = ty

    feats.detail = {
        "season_resid": season_resid, "ev_prior_n": ev_prior_n, "ev_first_seq": ev_first_seq,
        "team_events_seen": team_events_seen, "ev_attr": ev_attr,
        "pos_resid": pos_resid, "se_timeline": se_timeline, "se_start": se_start, "opp_o": opp_o, "opp_pred": opp_pred, "opp_teams": opp_teams,
    }
    return feats


# =============================================================================================
# diagnostics: calibration of the detector, caveats for the opponent effect
# =============================================================================================
def _ranks(a: np.ndarray) -> np.ndarray:
    order = a.argsort(kind="mergesort")
    r = np.empty(len(a), dtype=float)
    r[order] = np.arange(len(a), dtype=float)
    # average ranks over exact ties
    _, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
    if len(cnt) != len(a):
        sums = np.zeros(len(cnt))
        np.add.at(sums, inv, r)
        r = (sums / cnt)[inv]
    return r


def _corr(x: List[float], y: List[float]) -> Dict[str, Optional[float]]:
    if len(x) < 3:
        return {"n": len(x), "pearson": None, "spearman": None}
    a, b = np.asarray(x, float), np.asarray(y, float)
    if a.std() == 0 or b.std() == 0:
        return {"n": len(x), "pearson": None, "spearman": None}
    return {"n": len(x), "pearson": round(float(np.corrcoef(a, b)[0, 1]), 4),
            "spearman": round(float(np.corrcoef(_ranks(a), _ranks(b))[0, 1]), 4)}


def _rate(zs: np.ndarray, thr: float) -> Dict[str, Any]:
    n = len(zs)
    if n == 0:
        return {"n": 0, "up": None, "down": None, "flagged": None}
    up, dn = float((zs > thr).mean()), float((zs < -thr).mean())
    return {"n": n, "up": round(up, 4), "down": round(dn, 4), "flagged": round(up + dn, 4)}


def diagnostics(run: Any, feats: SeasonFeatures, draws: int = 200, seed: int = 1218) -> Dict[str, Any]:
    """Numbers for the WP3c report and the site's caveats (pure function of `run` and `feats`)."""
    cfg, d = feats.config, feats.detail
    thr = float(cfg["z_threshold"])
    k_min = int(cfg["k_min"])
    rows = []
    for key, rec in feats.team_event.items():
        if rec["change_z"] is not None:
            rows.append((key, rec["change_z"], rec["change_k"]))
    z_all = np.array([r[1] for r in rows], dtype=float)
    first = np.array([d["ev_prior_n"].get(r[0], 0) == 0 for r in rows], dtype=bool)  # season's first event of the team
    settled = np.array([d["ev_prior_n"].get(r[0], 0) >= 9 for r in rows], dtype=bool)  # >= 9 earlier quals
    later = ~first

    def robust_sd(z):
        if len(z) < 10:
            return None
        return round(float(1.4826 * np.median(np.abs(z - np.median(z)))), 4)

    out: Dict[str, Any] = {
        "model": run.model_id, "year": int(run.year), "sigma": None if feats.sigma is None else round(feats.sigma, 4),
        "n_resid": feats.n_resid, "z_threshold": thr,
        "normal_null_flag_rate": round(float(2 * (1 - 0.5 * (1 + math.erf(thr / math.sqrt(2))))), 4),
        "team_events_with_verdict": len(rows),
        "flag_rate": {
            "all": _rate(z_all, thr), "first_event_of_season": _rate(z_all[first], thr),
            "later_events": _rate(z_all[later], thr), "later_events_settled_9plus_prior_quals": _rate(z_all[settled], thr),
        },
        "z_robust_sd": {"all": robust_sd(z_all), "first_event_of_season": robust_sd(z_all[first]),
                        "later_events": robust_sd(z_all[later])},
        "z_median": {"all": None if not len(z_all) else round(float(np.median(z_all)), 4),
                     "later_events": None if not later.any() else round(float(np.median(z_all[later])), 4)},
    }

    out["flag_rate_by_threshold"] = {str(th): _rate(z_all, th) for th in (2.0, 2.5, 3.0, 4.0)}
    out["flag_rate_by_threshold_later_events"] = {str(th): _rate(z_all[later], th) for th in (2.0, 2.5, 3.0, 4.0)}
    allr = np.concatenate([np.asarray(v, float) for v in d["season_resid"].values()]) if d["season_resid"] else np.array([])
    out["mean_resid"] = None if not len(allr) else round(float(allr.mean()), 3)
    # SD of resid by the match's position in the team's event (1 = first qual there): the detector's window
    # is noisier than the season pool because early ratings are less certain.
    out["resid_sd_by_event_position"] = {
        str(i + 1): (None if len(v) < 2 else round(float(np.std(v, ddof=1)), 3))
        for i, v in sorted(d["pos_resid"].items())}

    # Null for "stable" teams: a team's own season residuals (noise around a rating that tracks it
    # by construction), k at a time, no real change possible.  Same sigma and threshold.
    rng = np.random.default_rng(seed)
    null_flags, null_n = 0, 0
    sigma = feats.sigma
    if sigma:
        for key, _, k in rows:
            rs = d["season_resid"].get(key[0], [])
            if len(rs) < max(k, 6):
                continue
            a = np.asarray(rs, float)
            for _ in range(draws):
                pick = rng.choice(len(a), size=k, replace=False)
                z = a[pick].mean() / (sigma / math.sqrt(k))
                null_flags += abs(z) > thr
                null_n += 1
    out["stable_team_null_flag_rate"] = None if not null_n else round(null_flags / null_n, 4)
    out["stable_team_null_draws"] = null_n

    # Opponent effect vs schedule strength
    tys = {t: v for t, v in feats.team_year.items() if v["opp_effect"] is not None}
    final = getattr(run, "final_vec", None) or {}
    xs_eff, xs_pred, xs_hind, xs_own, ses, zs = [], [], [], [], [], []
    for t, v in tys.items():
        xs_eff.append(v["opp_effect"])
        xs_pred.append(float(np.mean(d["opp_pred"][t])))
        opp = d["opp_teams"].get(t, [])
        hv = [float(final[o][0]) for o in opp if o in final]
        xs_hind.append(float(np.mean(hv)) * 3 if hv else float("nan"))
        xs_own.append(float(final[t][0]) if t in final else float("nan"))
        if v["opp_effect_se"]:
            ses.append(v["opp_effect_se"])
            zs.append(v["opp_effect"] / v["opp_effect_se"])
    ok = [i for i, x in enumerate(xs_hind) if not math.isnan(x) and not math.isnan(xs_own[i])]
    eff = np.array(xs_eff, float)
    out["opponent_effect"] = {
        "teams_with_estimate": len(tys),
        "mean_across_teams": None if not len(eff) else round(float(eff.mean()), 3),
        "sd_across_teams": None if len(eff) < 2 else round(float(eff.std(ddof=1)), 3),
        "mean_se": None if not ses else round(float(np.mean(ses)), 3),
        "share_abs_gt_2se": None if not zs else round(float((np.abs(zs) > cfg["opp_sig_mult"]).mean()), 4),
        "normal_null_share": round(float(2 * (1 - 0.5 * (1 + math.erf(cfg["opp_sig_mult"] / math.sqrt(2))))), 4),
        "share_abs_gt_2se_after_removing_season_mean": None if not zs else round(float(
            (np.abs((eff[[i for i, v in enumerate(tys.values()) if v["opp_effect_se"]]] - eff.mean())
                    / np.array(ses)) > cfg["opp_sig_mult"]).mean()), 4),
        "corr_with_mean_predicted_opp_score": _corr(xs_eff, xs_pred),
        "corr_with_mean_opp_final_epa_sum": _corr([xs_eff[i] for i in ok], [xs_hind[i] for i in ok]),
        "corr_with_own_final_epa": _corr([xs_eff[i] for i in ok], [xs_own[i] for i in ok]),
    }
    if len(eff) > 2 and ses:
        sig_var = float(eff.var(ddof=1) - np.mean(np.square(ses)))
        out["opponent_effect"]["signal_sd_after_noise"] = None if sig_var <= 0 else round(math.sqrt(sig_var), 3)
    return out


def change_examples(run: Any, feats: SeasonFeatures, n: int = 4, min_prior_quals: int = 9) -> Dict[str, List[Dict[str, Any]]]:
    """Largest flags at events where the team already had `min_prior_quals` earlier quals this
    season (so the baseline is a settled rating, not a start-of-season prior), with the raw
    evidence: the k matches' alliance no_foul score vs the model's prediction, the baseline, the
    team's rating at the end of that event and its previous event's end-of-event rating."""
    d = feats.detail
    team_events = run.objs[3]
    matches = run.objs[4]
    order = sorted(matches.values(), key=lambda m: m.time)
    out: Dict[str, List[Dict[str, Any]]] = {"up": [], "down": []}
    cand = []
    for key, rec in feats.team_event.items():
        if rec["change_flag"] in (1, -1) and d["ev_prior_n"].get(key, 0) >= min_prior_quals:
            cand.append((abs(rec["change_z"]), key, rec))
    cand.sort(key=lambda c: -c[0])
    for _, key, rec in cand:
        side = "up" if rec["change_flag"] == 1 else "down"
        if len(out[side]) >= n:
            continue
        t, ev = key
        seen = d["team_events_seen"].get(t, [])
        i = seen.index(ev)
        te = team_events.get(f"{t}_{ev}")
        prev = team_events.get(f"{t}_{seen[i - 1]}") if i > 0 else None
        ms = []
        for m in order:
            if m.event != ev or m.elim or (m.key, t) not in run.updated:
                continue
            on_red = t in m.get_red()
            ap = run.alliance_pred.get(m.key)
            act = getattr(m, "red_no_foul" if on_red else "blue_no_foul", None)
            ms.append({"match": m.key.split("_", 1)[-1].upper(),
                       "alliance_no_foul": _num(act),
                       "predicted_no_foul": None if ap is None else round(float(ap[0 if on_red else 1][0]), 1),
                       "team_attr": round(float(run.attrib[m.key][t][0]), 1),
                       "team_pre": round(float(_pre_total(run, m, t)), 1)})
            if len(ms) >= feats.config["k_max"]:
                break
        base = feats.team_event[key]["change_mean_resid"]
        out[side].append({
            "team": t, "event": ev, "z": round(rec["change_z"], 2), "k": rec["change_k"],
            "baseline_pre_epa": None if te is None else round(float(_pre_total_event(run, order, ev, t)), 2),
            "mean_resid": round(base, 2),
            "end_of_event_epa": None if te is None else round(float(te.epa), 2),
            "previous_event": None if i == 0 else seen[i - 1],
            "previous_event_end_epa": None if prev is None else round(float(prev.epa), 2),
            "first_matches": ms,
        })
    return out


def _pre_total_event(run: Any, order: List[Any], ev: str, t: int) -> float:
    for m in order:
        if m.event == ev and t in list(m.get_red()) + list(m.get_blue()):
            p = _pre_total(run, m, t)
            if p is not None:
                return p
    return float("nan")
