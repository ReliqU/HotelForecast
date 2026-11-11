"""Temporal experiment, interpretation and dashboard payload."""
from datetime import date
import hashlib
import json

import numpy as np

from .archive import ARCHIVE_SOURCE
from .audit import audit_datasets, diagnostics, promote
from .features import FeatureStore, LABELS, SEASONAL_LAST, SEASONAL_MEDIAN, TARGETS, UNITS
from .models import GRID, NAMES, Hybrid, bound, fit, predict, scores
from .sources import DAY, days
from .storage import fingerprint, journal
from .stress import scenario_sets

VERSION = "1.2.0"


def split(cfg, anchor):
    h = cfg["horizon"]
    start = date.fromisoformat(cfg["start"]) + 35*DAY
    fit_end = anchor - (cfg["validation_days"] + cfg["test_days"])*DAY
    val_end = anchor - cfg["test_days"]*DAY
    train = list(days(start, fit_end - h*DAY))[::cfg["train_stride"]]
    val = list(days(fit_end, val_end - h*DAY))[::h]
    test = list(days(val_end, anchor - h*DAY))[::h]
    refit = list(days(start, val_end - h*DAY))[::cfg["train_stride"]]
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
        db.execute("INSERT OR IGNORE INTO experiments VALUES(?,?,?,?,?,?,?,?,?)", (
            eid, digest, TARGETS[i], family, canonical({"model": params, "config": cfg}),
            cfg["seed"], str(anchor), canonical(metrics), VERSION))
    return dict(id=eid, family=family, label=NAMES[family], params=params, validation=metrics)


def importance(model, x, y, fs, t, seed, meta):
    con = np.abs(model.contrib(x))
    base = scores(y, predict(model, x, t))["mae"]
    rng = np.random.default_rng(seed)
    rows = []
    mid = len(x)//2
    # Consecutive, equally sized collections of forecast origins.
    midpoint = sorted({r[0] for r in meta})
    cut = midpoint[len(midpoint)//2]
    early = np.array([r[0] < cut for r in meta])
    if not early.any() or early.all():
        early = np.arange(len(x)) < mid
    for j, name in enumerate(fs.names):
        diffs = []
        for _ in range(3):
            shuffled = x.copy()
            shuffled[:, j] = rng.permutation(shuffled[:, j])
            diffs.append(scores(y, predict(model, shuffled, t))["mae"]-base)
        rows.append(dict(name=name, native=float(con[:, j].mean()), permutation=float(np.mean(diffs)),
                         early=float(con[early, j].mean()), late=float(con[~early, j].mean())))
    j = fs.event_start + 4
    grid = np.unique(np.quantile(x[:, j], np.linspace(0, .98, 11)))
    if len(grid) < 2:
        grid = np.array([0., 1.])
    curve = []
    for value in grid:
        changed = x.copy()
        changed[:, j] = value
        curve.append(dict(value=float(value), prediction=float(predict(model, changed, t).mean())))
    return sorted(rows, key=lambda r: -r["native"]), curve


def event_info(e):
    return {**{k: e[k] for k in ("title", "venue", "kind", "url", "basis")},
            "available_from": str(e["published"])}


def run(db, cfg, as_of, public=False, archive=False):
    anchor = date.fromisoformat(cfg["as_of"])
    if as_of < anchor:
        raise ValueError("Дата запуска должна быть не раньше опорной даты эксперимента из config.json.")
    fs = FeatureStore(db, cfg, as_of)
    h = cfg["horizon"]
    sets, ends = datasets(fs, cfg, anchor)
    digest = fingerprint(db, anchor)
    actual_origins = list(days(fs.start + 35*DAY, as_of-h*DAY))[::cfg["train_stride"]]
    latest = fs.dataset(actual_origins, h, as_of)
    frozen_origins = list(days(fs.start + 35*DAY, anchor-h*DAY))[::cfg["train_stride"]]
    frozen = latest if as_of == anchor else fs.dataset(frozen_origins,h,anchor)
    stress_sets, stress_meta = scenario_sets(db,cfg,anchor)
    (audit, notices, audit_meta),(confirmation, confirm_notices, confirm_meta) = audit_datasets(db,cfg,anchor)
    archive_rows = db.execute("SELECT slice,sha256 FROM raw WHERE source=? ORDER BY slice DESC LIMIT 1",
                              (ARCHIVE_SOURCE,)).fetchone()
    modes = ["event_html"] + ([ARCHIVE_SOURCE] if archive_rows else []) + (["kudago_json"] if public else [])
    if archive and not archive_rows:
        raise ValueError("Сначала загрузите архив публичной афиши.")
    source = "kudago_json" if public else (ARCHIVE_SOURCE if archive else "event_html")
    forecast_dates = list(days(as_of + DAY, as_of + h*DAY))
    output = dict(as_of=str(as_of), horizon=h, event_source=source, data_hash=digest, version=VERSION,
                  hotels=list(fs.hotels.values()), features=fs.names, targets={}, history={},
                  forecasts={mode:{} for mode in modes}, stress=stress_meta, audit=audit_meta,
                  confirmation=confirm_meta,
                  backtest={}, journal=journal(db),
                  splits=dict(start=cfg["start"], train_end=str(ends[0]), validation_end=str(ends[1]),
                              test_end=str(ends[2]), test_origins=len(set(m[0] for m in sets[2][3]))),
                  counts={table: db.execute(f"SELECT COUNT(*) FROM {table} WHERE {field}<=?", (str(as_of),)).fetchone()[0]
                          for table, field in (("rooms", "day"), ("events", "published"), ("raw", "slice"))})
    if archive_rows:
        catalog = {}
        for (src,d), entries in sorted(fs.events.items()):
            if src != ARCHIVE_SOURCE:
                continue
            for e in entries:
                k = e["id"].rsplit(":",1)[0]
                row = catalog.setdefault(k, dict(event_info(e), start=str(d), end=str(d)))
                row["end"] = str(d)
        output["archive"] = dict(snapshot=archive_rows["slice"],sha256=archive_rows["sha256"],
                                 events=list(catalog.values()))
    output["stress"]["scenarios"] = [s[1] for s in stress_sets.values()]
    output["counts"]["synthetic_events"] = db.execute(
        "SELECT COUNT(*) FROM events WHERE source='event_html' AND published<=?",(str(as_of),)).fetchone()[0]
    for hotel in fs.hotels:
        output["history"][hotel] = [
            dict(date=str(d), **dict(zip(TARGETS, fs.facts[hotel].get(d, (None, None)))))
            for d in days(as_of-89*DAY, as_of)]
        for mode in modes:
            output["forecasts"][mode][hotel] = [dict(date=str(d), events=[
                event_info(e) for e in fs.available_events(as_of,d,mode)]) for d in forecast_dates]
        output["backtest"][hotel] = {}
    output["forecast"] = output["forecasts"][source]
    for i, target in enumerate(TARGETS):
        (x, y, _), (vx, vy, _), (tx, ty, tm), (rx, ry, _) = [subset(d, i) for d in sets]
        medcol = columns(fs,i,"median")
        candidates = []
        for family, params_grid in GRID.items():
            for params in params_grid:
                model = fit(family, params, x, y, columns(fs,i,family),fs.event_start)
                validation = scores(vy, predict(model, vx, target))
                candidates.append(experiment(db, cfg, anchor, digest, i, family, params, validation))
        key = lambda r: (r["validation"]["mae"], r["validation"]["rmse"])
        final_rows, predictions, final_models, frozen_models = [], {}, {}, {}
        sx, sy, _ = subset(frozen,i)
        ax, ay, am = subset(audit,i)
        audit_predictions = {}
        for family in GRID:
            r = min((q for q in candidates if q["family"] == family), key=key).copy()
            col = columns(fs,i,family)
            m = fit(family, r["params"], rx, ry, col, fs.event_start)
            pp = predict(m, tx, target)
            r["test"] = scores(ty, pp)
            final_rows.append(r)
            predictions[family], final_models[family] = pp, m
            trained = fit(family,r["params"],sx,sy,col,fs.event_start)
            frozen_models[family] = trained
            if family in ("ridge","robust"):
                audit_predictions[family] = predict(trained,ax,target)
            for scenario, info in stress_sets.values():
                qx,qy,_ = subset(scenario,i)
                prediction = predict(trained,qx,target)
                info["targets"].setdefault(target,{})[family] = dict(
                    scores(qy,prediction), bias=float((prediction-qy).mean()))
        output["audit"]["targets"][target] = diagnostics(
            ay,audit_predictions["robust"],audit_predictions["ridge"],am,notices)

        # Each component is the finalist of its own family on the old validation.
        # The cutoff is fixed before opening the later confirmation period.
        original = next(r for r in final_rows if r["family"] == "ridge")
        robust = next(r for r in final_rows if r["family"] == "robust")
        params = dict(short=robust["params"],long=original["params"],split=7)
        mv = fit("hybrid",params,x,y,medcol,fs.event_start)
        row = experiment(db,cfg,anchor,digest,i,"hybrid",params,scores(vy,predict(mv,vx,target)))
        candidates.append(row)
        model = Hybrid(final_models["robust"],final_models["ridge"])
        final_models["hybrid"] = model
        predictions["hybrid"] = predict(model,tx,target)
        combined = dict(row,test=scores(ty,predictions["hybrid"]))
        final_rows.append(combined)
        combo = Hybrid(frozen_models["robust"],frozen_models["ridge"])
        frozen_models["hybrid"] = combo
        for scenario, info in stress_sets.values():
            qx,qy,_ = subset(scenario,i)
            p = predict(combo,qx,target)
            info["targets"][target]["hybrid"] = dict(scores(qy,p),bias=float((p-qy).mean()))
        cx,cy,cm = subset(confirmation,i)
        check = diagnostics(cy,predict(combo,cx,target),predict(frozen_models["ridge"],cx,target),cm,confirm_notices)
        check["promoted"] = promote(check)
        winner = combined if check["promoted"] else original
        check["released_family"] = winner["family"]
        output["confirmation"]["targets"][target] = check
        validation_winner = min(candidates, key=key)
        test_model = final_models[winner["family"]]
        pp = predictions[winner["family"]]
        chosen = next(r for r in final_rows if r["id"] == winner["id"])
        base = next(r for r in final_rows if r["family"] == "median")
        early = np.array([(d-o).days <= 7 for o, _, d in tm])
        segmented = {"1–7": scores(ty[early], pp[early]), "8–14": scores(ty[~early], pp[~early])}
        importance_rows, pdp = importance(test_model, tx, ty, fs, target, cfg["seed"], tm)
        # Same selected family and parameters; drop the event block, refit, compare.
        ablation = fit(winner["family"], winner["params"], rx[:, :fs.event_start], ry,
                       columns(fs,i,winner["family"]),fs.event_start)
        without = scores(ty, predict(ablation, tx[:, :fs.event_start], target))
        output["targets"][target] = dict(label=LABELS[target], unit=UNITS[target], winner=chosen,
            baseline=base, candidates=candidates, finalists=final_rows, by_horizon=segmented,
            validation_winner=validation_winner,
            importance=importance_rows, pdp=pdp, without_events=without,
            samples=dict(train=len(y), validation=len(vy), test=len(ty)))
        for hotel in fs.hotels:
            output["backtest"][hotel][target] = [
                dict(date=str(d), origin=str(o), fact=float(a), model=float(b), baseline=float(c))
                for (o, hh, d), a, b, c in zip(tm, ty, pp, predictions["median"]) if hh == hotel]
        lx, ly, _ = subset(latest, i)
        model = frozen_models[winner["family"]] if as_of == anchor else fit(
            winner["family"],winner["params"],lx,ly,columns(fs,i,winner["family"]),fs.event_start)
        for mode in modes:
            for hotel in fs.hotels:
                fx = np.asarray([fs.row(hotel,as_of,d,mode)[0] for d in forecast_dates])
                contributions = model.contrib(fx)
                raw = model.intercept + contributions.sum(axis=1)
                bounded = bound(raw, target)
                for row, pr, unbounded, cs, b in zip(output["forecasts"][mode][hotel], bounded, raw,
                                                     contributions, fx[:,medcol]):
                    row[target] = dict(value=float(pr), baseline=float(b), intercept=float(model.intercept),
                                       clip=float(pr-unbounded),
                                       contributions=[float(v) for v in cs])
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
    m = fit(r["family"], params, x, y, columns(fs,i,r["family"]),fs.event_start)
    actual, expected = scores(vy, predict(m, vx, t)), json.loads(r["metrics"])
    ok = all(abs(actual[k]-expected[k]) < 1e-7 for k in actual)
    return dict(id=eid, validation=actual, expected=expected, reproduced=ok)
