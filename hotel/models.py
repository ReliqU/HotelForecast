"""Regularized linear models and gradient boosting, implemented using NumPy."""

import numpy as np

GRID = {
    "seasonal": [{}],
    "median": [{}],
    "ridge": [{"alpha": a} for a in (1.0, 20.0, 150.0)],
    "boost": [{"trees": n, "rate": 0.14, "bins": 18} for n in (55, 120, 220)],
    "robust": [
        {"alpha": 20.0, "cut": 0.6, "expanded": False},
        {"alpha": 75.0, "cut": 1.2, "expanded": False},
        {"alpha": 35.0, "cut": 0.6, "expanded": True},
        {"alpha": 100.0, "cut": 1.2, "expanded": True},
    ],
}
NAMES = {
    "seasonal": "Последний одноимённый день",
    "median": "Сезонная медиана",
    "ridge": "Линейная регрессия Ridge",
    "boost": "Градиентный бустинг",
    "robust": "Регрессия Хьюбера",
    "hybrid": "Хьюбер 1–7 / Ridge 8–14",
}


def bound(p, target):
    return np.clip(p, 0.0, 100.0 if target == "occupancy" else np.inf)


class Seasonal:
    def __init__(self, col):
        self.col = col

    def fit(self, x, y):
        self.intercept = float(np.mean(x[:, self.col]))
        return self

    def contrib(self, x):
        out = np.zeros_like(x)
        out[:, self.col] = x[:, self.col] - self.intercept
        return out


class Ridge:
    def __init__(self, alpha):
        self.alpha = alpha

    def fit(self, x, y):
        self.mean, self.scale = x.mean(axis=0), x.std(axis=0)
        self.scale[self.scale < 1e-9] = 1.0
        z = (x - self.mean) / self.scale
        self.intercept = float(y.mean())
        self.beta = np.linalg.solve(
            z.T @ z + self.alpha * np.eye(z.shape[1]), z.T @ (y - self.intercept)
        )
        return self

    def contrib(self, x):
        return (x - self.mean) / self.scale * self.beta


class Robust:
    """L2-regularized Huber regression with an optional small nonlinear basis."""

    def __init__(self, alpha, cut, expanded, event_start):
        self.alpha, self.cut, self.expanded = alpha, cut, expanded
        self.event_start = event_start

    def basis(self, x):
        width = x.shape[1]
        if not self.expanded:
            return x, [(i,) for i in range(width)]
        lead, sine, cosine = x[:, 0], x[:, 1], x[:, 2]
        extra = [2 * sine * cosine, cosine * cosine - sine * sine]
        owners = [(1, 2), (1, 2)]
        if self.event_start is not None and width == self.event_start + 5:
            j = self.event_start + 4
            near = x[:, j]
            extra.extend([near * near, near * lead, *(near * x[:, k] for k in range(j - 4, j))])
            owners.extend([(j,), (j, 0), *((j, k) for k in range(j - 4, j))])
        return np.column_stack((x, *extra)), [(i,) for i in range(width)] + owners

    def fit(self, x, y):
        z, self.owners = self.basis(x)
        self.mean, self.scale = z.mean(axis=0), z.std(axis=0)
        self.scale[self.scale < 1e-9] = 1.0
        design = np.column_stack((np.ones(len(z)), (z - self.mean) / self.scale))
        penalty = self.alpha * np.eye(design.shape[1])
        penalty[0, 0] = 0.0  # Do not shrink the intercept.

        def solve(weights):
            weighted = design.T * weights
            return np.linalg.solve(weighted @ design + penalty, weighted @ y)

        coef = solve(np.ones(len(y)))
        residual = y - design @ coef
        mad = np.median(np.abs(residual - np.median(residual)))
        self.delta = max(self.cut * 1.4826 * mad, 1e-6)
        for _ in range(25):
            residual = np.abs(y - design @ coef)
            new = solve(np.minimum(1.0, self.delta / np.maximum(residual, 1e-12)))
            if np.max(np.abs(new - coef)) <= 1e-7 * max(1.0, np.max(np.abs(coef))):
                coef = new
                break
            coef = new
        self.intercept, self.beta = float(coef[0]), coef[1:]
        return self

    def contrib(self, x):
        z, _ = self.basis(x)
        part = (z - self.mean) / self.scale * self.beta
        if not self.expanded:
            return part
        c = part[:, : x.shape[1]].copy()
        for j, owners in enumerate(self.owners[x.shape[1] :], x.shape[1]):
            for k in owners:
                c[:, k] += part[:, j] / len(owners)
        return c


class Hybrid:
    """Use Huber for the first week and the original Ridge for the second."""

    def __init__(self, short, long, split=7):
        self.short, self.long, self.split = short, long, split
        self.intercept = short.intercept

    def contrib(self, x):
        c = np.zeros_like(x)
        short = x[:, 0] <= self.split
        if short.any():
            c[short] = self.short.contrib(x[short])
        if (~short).any():
            c[~short] = self.long.contrib(x[~short])
            # Attribute the change of model intercept to the lead-time switch.
            c[~short, 0] += self.long.intercept - self.short.intercept
        return c


class Boost:
    def __init__(self, trees, rate, bins):
        self.trees, self.rate, self.bins = trees, rate, bins

    def fit(self, x, y):
        residual = y - y.mean()
        self.intercept, self.stumps = float(y.mean()), []
        specs = []
        for j, v in enumerate(x.T):
            cuts = np.unique(np.quantile(v, np.linspace(0, 1, self.bins + 1)[:-1]))
            cuts = cuts[cuts < v.max()]
            if len(cuts) == 0:
                continue
            idx = np.searchsorted(cuts, v, side="left")
            left = np.bincount(idx, minlength=len(cuts) + 1).cumsum()[:-1]
            right = len(y) - left
            specs.append((j, cuts, idx, left, right, (left >= 8) & (right >= 8)))
        for _ in range(self.trees):
            best, total = None, residual.sum()
            for j, cuts, idx, left, right, valid in specs:
                ls = np.bincount(idx, weights=residual, minlength=len(cuts) + 1).cumsum()[:-1]
                rs = total - ls
                gain = np.where(
                    valid, ls * ls / np.maximum(1, left) + rs * rs / np.maximum(1, right), -1.0
                )
                k = int(gain.argmax())
                if gain[k] > 1e-9 and (best is None or gain[k] > best[0]):
                    best = (
                        gain[k],
                        j,
                        cuts[k],
                        ls[k] / left[k],
                        rs[k] / right[k],
                        left[k] / len(y),
                    )
            if best is None:
                break
            _, j, cut, a, b, frac = best
            a, b = a * self.rate, b * self.rate
            avg = frac * a + (1 - frac) * b
            self.stumps.append((j, cut, a - avg, b - avg))
            self.intercept += avg
            residual -= np.where(x[:, j] <= cut, a, b)
        return self

    def contrib(self, x):
        c = np.zeros_like(x)
        for j, cut, a, b in self.stumps:
            c[:, j] += np.where(x[:, j] <= cut, a, b)
        return c


def fit(family, params, x, y, baseline_col, event_start=None):
    if family == "hybrid":
        return Hybrid(
            fit("robust", params["short"], x, y, baseline_col, event_start),
            fit("ridge", params["long"], x, y, baseline_col, event_start),
            params["split"],
        )
    if family in ("seasonal", "median"):
        model = Seasonal(baseline_col)
    elif family == "ridge":
        model = Ridge(**params)
    elif family == "robust":
        model = Robust(event_start=event_start, **params)
    elif family == "boost":
        model = Boost(**params)
    else:
        raise ValueError(f"Unknown model: {family}")
    return model.fit(x, y)


def predict(model, x, target):
    return bound(model.intercept + model.contrib(x).sum(axis=1), target)


def scores(y, p):
    e = p - y
    return dict(mae=float(np.abs(e).mean()), rmse=float(np.sqrt(np.square(e).mean())), n=len(e))
