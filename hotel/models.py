"""Ridge and gradient boosting with decision stumps, implemented using NumPy."""
import numpy as np

GRID = {"seasonal": [{}], "median": [{}], "ridge": [{"alpha": a} for a in (1., 20., 150.)],
        "boost": [{"trees": n, "rate": .14, "bins": 18} for n in (55, 120, 220)]}
NAMES = {"seasonal": "Последний одноимённый день", "median": "Сезонная медиана",
         "ridge": "Линейная регрессия Ridge", "boost": "Градиентный бустинг"}


def bound(p, target):
    return np.clip(p, 0., 100. if target == "occupancy" else np.inf)


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
        self.scale[self.scale < 1e-9] = 1.
        z = (x-self.mean)/self.scale
        self.intercept = float(y.mean())
        self.beta = np.linalg.solve(z.T@z + self.alpha*np.eye(z.shape[1]), z.T@(y-self.intercept))
        return self

    def contrib(self, x):
        return (x-self.mean)/self.scale*self.beta


class Boost:
    def __init__(self, trees, rate, bins):
        self.trees, self.rate, self.bins = trees, rate, bins

    def fit(self, x, y):
        residual = y-y.mean()
        self.intercept, self.stumps = float(y.mean()), []
        specs = []
        for j, v in enumerate(x.T):
            cuts = np.unique(np.quantile(v, np.linspace(0, 1, self.bins+1)[:-1]))
            cuts = cuts[cuts < v.max()]
            if len(cuts) == 0:
                continue
            idx = np.searchsorted(cuts, v, side="left")
            left = np.bincount(idx, minlength=len(cuts)+1).cumsum()[:-1]
            right = len(y)-left
            specs.append((j, cuts, idx, left, right, (left >= 8) & (right >= 8)))
        for _ in range(self.trees):
            best, total = None, residual.sum()
            for j, cuts, idx, left, right, valid in specs:
                ls = np.bincount(idx, weights=residual, minlength=len(cuts)+1).cumsum()[:-1]
                rs = total-ls
                gain = np.where(valid, ls*ls/np.maximum(1, left)+rs*rs/np.maximum(1, right), -1.)
                k = int(gain.argmax())
                if gain[k] > 1e-9 and (best is None or gain[k] > best[0]):
                    best = (gain[k], j, cuts[k], ls[k]/left[k], rs[k]/right[k], left[k]/len(y))
            if best is None:
                break
            _, j, cut, a, b, frac = best
            a, b = a*self.rate, b*self.rate
            avg = frac*a+(1-frac)*b
            self.stumps.append((j, cut, a-avg, b-avg))
            self.intercept += avg
            residual -= np.where(x[:, j] <= cut, a, b)
        return self

    def contrib(self, x):
        c = np.zeros_like(x)
        for j, cut, a, b in self.stumps:
            c[:, j] += np.where(x[:, j] <= cut, a, b)
        return c


def fit(family, params, x, y, baseline_col):
    model = Seasonal(baseline_col) if family in ("seasonal", "median") else (Ridge(**params) if family == "ridge" else Boost(**params))
    return model.fit(x, y)


def predict(model, x, target):
    return bound(model.intercept + model.contrib(x).sum(axis=1), target)


def scores(y, p):
    e = p-y
    return dict(mae=float(np.abs(e).mean()), rmse=float(np.sqrt(np.square(e).mean())), n=len(e))
