"""Temporal experiment, interpretation and dashboard payload."""

import hashlib
import json
from datetime import date

import numpy as np

from .archive import ARCHIVE_SOURCE
from .audit import audit_datasets, diagnostics, promote
from .features import (
    LABELS,
    SEASONAL_LAST,
    SEASONAL_MEDIAN,
    TARGETS,
    UNITS,
    FeatureStore,
)
from .models import GRID, NAMES, Hybrid, bound, fit, predict, scores
from .sources import DAY, days
from .storage import fingerprint, journal
from .stress import scenario_sets

VERSION = "1.2.0"


def split(cfg, anchor):
    h = cfg["horizon"]
    start = date.fromisoformat(cfg["start"]) + 35 * DAY
    fit_end = anchor - (cfg["validation_days"] + cfg["test_days"]) * DAY
    val_end = anchor - cfg["test_days"] * DAY
    train = list(days(start, fit_end - h * DAY))[:: cfg["train_stride"]]
    val = list(days(fit_end, val_end - h * DAY))[::h]
    test = list(days(val_end, anchor - h * DAY))[::h]
    refit = list(days(start, val_end - h * DAY))[:: cfg["train_stride"]]
    if not (train and val and test):
        raise ValueError("Недостаточно истории для хронологического разбиения.")
    return (train, val, test, refit), (fit_end, val_end, anchor)


def datasets(fs, cfg, anchor):
    origins, ends = split(cfg, anchor)
    return [fs.dataset(o, cfg["horizon"], d) for o, d in zip(origins, (*ends, ends[1]))], ends


def subset(data, i):
    x, y, _, meta = data
    good = np.isfinite(y[:, i])
    return x[good], y[good, i], [m for m, keep in zip(meta, good) if keep]


def columns(fs, i, family):
    feature = SEASONAL_MEDIAN if family == "median" else SEASONAL_LAST
    return fs.names.index(f"{LABELS[TARGETS[i]]}: {feature}")


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def experiment(db, cfg, anchor, digest, i, family, params, metrics):
    spec = [VERSION, cfg, str(anchor), digest, TARGETS[i], family, params]
    eid = hashlib.sha256(canonical(spec).encode()).hexdigest()[:14]
    with db:
        db.execute(
            "INSERT OR IGNORE INTO experiments VALUES(?,?,?,?,?,?,?,?,?)",
            (
                eid,
                digest,
                TARGETS[i],
                family,
                canonical({"model": params, "config": cfg}),
                cfg["seed"],
                str(anchor),
                canonical(metrics),
                VERSION,
            ),
        )
    return dict(id=eid, family=family, label=NAMES[family], params=params, validation=metrics)


def importance(model, x, y, fs, t, seed, meta):
    con = np.abs(model.contrib(x))
    base = scores(y, predict(model, x, t))["mae"]
    rng = np.random.default_rng(seed)
    rows = []
    mid = len(x) // 2
    # Consecutive, equally sized collections of forecast origins.
    midpoint = sorted({r[0] for r in meta})
    cut = midpoint[len(midpoint) // 2]
    early = np.array([r[0] < cut for r in meta])
    if not early.any() or early.all():
        early = np.arange(len(x)) < mid
    for j, name in enumerate(fs.names):
        diffs = []
        for _ in range(3):
            shuffled = x.copy()
            shuffled[:, j] = rng.permutation(shuffled[:, j])
            diffs.append(scores(y, predict(model, shuffled, t))["mae"] - base)
        rows.append(
            dict(
                name=name,
                native=float(con[:, j].mean()),
                permutation=float(np.mean(diffs)),
                early=float(con[early, j].mean()),
                late=float(con[~early, j].mean()),
            )
        )
    j = fs.event_start + 4
    grid = np.unique(np.quantile(x[:, j], np.linspace(0, 0.98, 11)))
    if len(grid) < 2:
        grid = np.array([0.0, 1.0])
    curve = []
    for value in grid:
        changed = x.copy()
        changed[:, j] = value
        curve.append(dict(value=float(value), prediction=float(predict(model, changed, t).mean())))
    return sorted(rows, key=lambda r: -r["native"]), curve


def event_info(e):
    return {
        **{k: e[k] for k in ("title", "venue", "kind", "url", "basis")},
        "available_from": str(e["published"]),
    }


def validation_rank(candidate):
    validation = candidate["validation"]
    return validation["mae"], validation["rmse"]


def record_stress_scores(stress_sets, target, target_index, models):
    for scenario, info in stress_sets.values():
        features, actual, _ = subset(scenario, target_index)
        metrics = {}
        for family, model in models.items():
            prediction = predict(model, features, target)
            metrics[family] = dict(
                scores(actual, prediction),
                bias=float((prediction - actual).mean()),
            )
        info["targets"][target] = metrics


def fill_forecast(forecasts, feature_store, as_of, target, model, median_column):
    for source, hotels in forecasts.items():
        for hotel, rows in hotels.items():
            features = np.asarray(
                [
                    feature_store.row(hotel, as_of, date.fromisoformat(row["date"]), source)[0]
                    for row in rows
                ]
            )
            contributions = model.contrib(features)
            raw_values = model.intercept + contributions.sum(axis=1)
            values = bound(raw_values, target)
            for row, value, raw, effects, baseline in zip(
                rows, values, raw_values, contributions, features[:, median_column]
            ):
                row[target] = dict(
                    value=float(value),
                    baseline=float(baseline),
                    intercept=float(model.intercept),
                    clip=float(value - raw),
                    contributions=[float(effect) for effect in effects],
                )


def run(db, cfg, as_of, public=False, archive=False):
    anchor = date.fromisoformat(cfg["as_of"])
    if as_of < anchor:
        raise ValueError(
            "Дата запуска должна быть не раньше опорной даты эксперимента из config.json."
        )
    fs = FeatureStore(db, cfg, as_of)
    h = cfg["horizon"]
    sets, ends = datasets(fs, cfg, anchor)
    digest = fingerprint(db, anchor)
    actual_origins = list(days(fs.start + 35 * DAY, as_of - h * DAY))[:: cfg["train_stride"]]
    latest = fs.dataset(actual_origins, h, as_of)
    frozen_origins = list(days(fs.start + 35 * DAY, anchor - h * DAY))[:: cfg["train_stride"]]
    frozen = latest if as_of == anchor else fs.dataset(frozen_origins, h, anchor)
    stress_sets, stress_meta = scenario_sets(db, cfg, anchor)
    (audit, notices, audit_meta), (confirmation, confirm_notices, confirm_meta) = audit_datasets(
        db, cfg, anchor
    )
    archive_rows = db.execute(
        "SELECT slice,sha256 FROM raw WHERE source=? ORDER BY slice DESC LIMIT 1", (ARCHIVE_SOURCE,)
    ).fetchone()
    modes = (
        ["event_html"]
        + ([ARCHIVE_SOURCE] if archive_rows else [])
        + (["kudago_json"] if public else [])
    )
    if archive and not archive_rows:
        raise ValueError("Сначала загрузите архив публичной афиши.")
    source = "kudago_json" if public else (ARCHIVE_SOURCE if archive else "event_html")
    forecast_dates = list(days(as_of + DAY, as_of + h * DAY))
    output = dict(
        as_of=str(as_of),
        horizon=h,
        event_source=source,
        data_hash=digest,
        version=VERSION,
        hotels=list(fs.hotels.values()),
        features=fs.names,
        targets={},
        history={},
        forecasts={mode: {} for mode in modes},
        stress=stress_meta,
        audit=audit_meta,
        confirmation=confirm_meta,
        backtest={},
        journal=journal(db),
        splits=dict(
            start=cfg["start"],
            train_end=str(ends[0]),
            validation_end=str(ends[1]),
            test_end=str(ends[2]),
            test_origins=len(set(m[0] for m in sets[2][3])),
        ),
        counts={
            table: db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {field}<=?", (str(as_of),)
            ).fetchone()[0]
            for table, field in (("rooms", "day"), ("events", "published"), ("raw", "slice"))
        },
    )
    if archive_rows:
        catalog = {}
        for (src, d), entries in sorted(fs.events.items()):
            if src != ARCHIVE_SOURCE:
                continue
            for e in entries:
                k = e["id"].rsplit(":", 1)[0]
                row = catalog.setdefault(k, dict(event_info(e), start=str(d), end=str(d)))
                row["end"] = str(d)
        output["archive"] = dict(
            snapshot=archive_rows["slice"],
            sha256=archive_rows["sha256"],
            events=list(catalog.values()),
        )
    output["stress"]["scenarios"] = [s[1] for s in stress_sets.values()]
    output["counts"]["synthetic_events"] = db.execute(
        "SELECT COUNT(*) FROM events WHERE source='event_html' AND published<=?", (str(as_of),)
    ).fetchone()[0]
    for hotel in fs.hotels:
        output["history"][hotel] = [
            dict(date=str(d), **dict(zip(TARGETS, fs.facts[hotel].get(d, (None, None)))))
            for d in days(as_of - 89 * DAY, as_of)
        ]
        for mode in modes:
            output["forecasts"][mode][hotel] = [
                dict(
                    date=str(d), events=[event_info(e) for e in fs.available_events(as_of, d, mode)]
                )
                for d in forecast_dates
            ]
        output["backtest"][hotel] = {}
    output["forecast"] = output["forecasts"][source]
    for target_index, target in enumerate(TARGETS):
        training, validation, test, refit = [subset(dataset, target_index) for dataset in sets]
        train_features, train_actual, _ = training
        val_features, val_actual, _ = validation
        test_features, test_actual, test_dates = test
        refit_features, refit_actual, _ = refit
        frozen_features, frozen_actual, _ = subset(frozen, target_index)
        median_column = columns(fs, target_index, "median")

        candidates = []
        finalists = {}
        test_predictions = {}
        test_models = {}
        frozen_models = {}
        for family, configurations in GRID.items():
            baseline_column = columns(fs, target_index, family)
            family_runs = []
            for params in configurations:
                model = fit(
                    family, params, train_features, train_actual, baseline_column, fs.event_start
                )
                metrics = scores(val_actual, predict(model, val_features, target))
                family_runs.append(
                    experiment(db, cfg, anchor, digest, target_index, family, params, metrics)
                )
            candidates.extend(family_runs)
            finalist = min(family_runs, key=validation_rank)
            params = finalist["params"]
            test_model = fit(
                family, params, refit_features, refit_actual, baseline_column, fs.event_start
            )
            test_prediction = predict(test_model, test_features, target)
            finalists[family] = dict(finalist, test=scores(test_actual, test_prediction))
            test_predictions[family] = test_prediction
            test_models[family] = test_model
            frozen_models[family] = fit(
                family, params, frozen_features, frozen_actual, baseline_column, fs.event_start
            )

        audit_features, audit_actual, audit_dates = subset(audit, target_index)
        output["audit"]["targets"][target] = diagnostics(
            audit_actual,
            predict(frozen_models["robust"], audit_features, target),
            predict(frozen_models["ridge"], audit_features, target),
            audit_dates,
            notices,
        )

        # The component parameters come from validation. The day-seven cutoff
        # is fixed before the later confirmation period is scored.
        hybrid_params = dict(
            short=finalists["robust"]["params"], long=finalists["ridge"]["params"], split=7
        )
        validation_model = fit(
            "hybrid", hybrid_params, train_features, train_actual, median_column, fs.event_start
        )
        hybrid_run = experiment(
            db,
            cfg,
            anchor,
            digest,
            target_index,
            "hybrid",
            hybrid_params,
            scores(val_actual, predict(validation_model, val_features, target)),
        )
        candidates.append(hybrid_run)
        test_models["hybrid"] = Hybrid(test_models["robust"], test_models["ridge"])
        test_predictions["hybrid"] = predict(test_models["hybrid"], test_features, target)
        finalists["hybrid"] = dict(hybrid_run, test=scores(test_actual, test_predictions["hybrid"]))
        frozen_models["hybrid"] = Hybrid(frozen_models["robust"], frozen_models["ridge"])
        record_stress_scores(stress_sets, target, target_index, frozen_models)

        confirm_features, confirm_actual, confirm_dates = subset(confirmation, target_index)
        comparison = diagnostics(
            confirm_actual,
            predict(frozen_models["hybrid"], confirm_features, target),
            predict(frozen_models["ridge"], confirm_features, target),
            confirm_dates,
            confirm_notices,
        )
        comparison["promoted"] = promote(comparison)
        released_family = "hybrid" if comparison["promoted"] else "ridge"
        comparison["released_family"] = released_family
        output["confirmation"]["targets"][target] = comparison

        winner = finalists[released_family]
        selected_prediction = test_predictions[released_family]
        early = np.array([(day - origin).days <= 7 for origin, _, day in test_dates])
        by_horizon = {
            "1–7": scores(test_actual[early], selected_prediction[early]),
            "8–14": scores(test_actual[~early], selected_prediction[~early]),
        }
        importance_rows, partial_dependence = importance(
            test_models[released_family],
            test_features,
            test_actual,
            fs,
            target,
            cfg["seed"],
            test_dates,
        )
        # Isolate the effect of the event columns with the same model settings.
        event_start = fs.event_start
        released_column = columns(fs, target_index, released_family)
        ablation = fit(
            released_family,
            winner["params"],
            refit_features[:, :event_start],
            refit_actual,
            released_column,
            event_start,
        )
        without_events = scores(
            test_actual, predict(ablation, test_features[:, :event_start], target)
        )
        output["targets"][target] = dict(
            label=LABELS[target],
            unit=UNITS[target],
            winner=winner,
            baseline=finalists["median"],
            candidates=candidates,
            finalists=list(finalists.values()),
            by_horizon=by_horizon,
            validation_winner=min(candidates, key=validation_rank),
            importance=importance_rows,
            pdp=partial_dependence,
            without_events=without_events,
            samples=dict(
                train=len(train_actual), validation=len(val_actual), test=len(test_actual)
            ),
        )
        for hotel in fs.hotels:
            output["backtest"][hotel][target] = [
                dict(
                    date=str(day),
                    origin=str(origin),
                    fact=float(actual),
                    model=float(prediction),
                    baseline=float(median),
                )
                for (origin, test_hotel, day), actual, prediction, median in zip(
                    test_dates, test_actual, selected_prediction, test_predictions["median"]
                )
                if test_hotel == hotel
            ]
        live_model = frozen_models[released_family]
        if as_of != anchor:
            latest_features, latest_actual, _ = subset(latest, target_index)
            live_model = fit(
                released_family,
                winner["params"],
                latest_features,
                latest_actual,
                released_column,
                event_start,
            )
        fill_forecast(output["forecasts"], fs, as_of, target, live_model, median_column)
    return output


def replay(db, eid):
    r = db.execute("SELECT * FROM experiments WHERE id=?", (eid,)).fetchone()
    if r is None:
        raise ValueError("Неизвестный ID эксперимента. Сначала выполните build.")
    if r["version"] != VERSION:
        raise ValueError("Для воспроизведения нужна сохранённая версия кода.")
    spec = json.loads(r["params"])
    cfg, params = spec["config"], spec["model"]
    anchor = date.fromisoformat(r["cutoff"])
    if fingerprint(db, anchor) != r["data_hash"]:
        raise ValueError("Версия исходных данных не совпадает с журналом.")
    fs = FeatureStore(db, cfg, anchor)
    origins, ends = split(cfg, anchor)
    t, i = r["target"], TARGETS.index(r["target"])
    x, y, _ = subset(fs.dataset(origins[0], cfg["horizon"], ends[0]), i)
    vx, vy, _ = subset(fs.dataset(origins[1], cfg["horizon"], ends[1]), i)
    m = fit(r["family"], params, x, y, columns(fs, i, r["family"]), fs.event_start)
    actual, expected = scores(vy, predict(m, vx, t)), json.loads(r["metrics"])
    ok = all(abs(actual[k] - expected[k]) < 1e-7 for k in actual)
    return dict(id=eid, validation=actual, expected=expected, reproduced=ok)
