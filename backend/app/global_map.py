"""Global NASA POWER trends from the locally prepared annual LST archive."""
from functools import lru_cache
import math
import os
from pathlib import Path
import threading

import numpy as np
from scipy import stats

GRID_SHAPE = (361, 576)
YEARS = tuple(range(1990, 2026))
PARAMETERS = {'temperature': ('T2M', '°C'), 'precipitation': ('PRECTOTCORR', 'mm')}
SOURCE = 'https://registry.opendata.aws/nasa-power/'
_lock = threading.Lock()


def archive_path():
    root = Path(__file__).resolve().parents[2]
    return Path(os.environ.get('EARTHSCOPE_CACHE_DIR', root / '.earthscope-cache')) / 'global' / 'annual.npz'


@lru_cache(maxsize=2)
def _calculate(path, stamp, variable):
    with np.load(path, allow_pickle=False) as archive:
        values = archive[variable]
        version = str(archive['version'])
        if values.shape != (len(YEARS), *GRID_SHAPE) or tuple(archive['years']) != YEARS:
            raise ValueError('Unexpected POWER annual archive dimensions or years')
    count = len(YEARS)
    x = np.arange(count, dtype='float64') - (count - 1) / 2
    sxx = np.dot(x, x)
    annual = values.reshape(count, -1).astype('float64')
    slopes = x @ annual / sxx
    residuals = annual - annual.mean(axis=0) - x[:, None] * slopes
    scores = x[:, None] * residuals
    variance = np.einsum('ij,ij->j', scores, scores)
    for lag in range(1, 4):
        variance += 2 * (1 - lag / 4) * np.einsum('ij,ij->j', scores[lag:], scores[:-lag])
    with np.errstate(invalid='ignore', divide='ignore'):
        standard_error = np.sqrt(variance * count / (count - 2)) / sxx
        p = np.where(standard_error > 0, 2 * stats.t.sf(np.abs(slopes / standard_error), count - 2), np.nan)
    # All meridians converge at each pole. Count one test per pole, then repeat
    # its adjusted value across the row for geographic display and lookup.
    p_family = p.copy()
    cols = GRID_SHAPE[1]
    p_family[1:cols] = np.nan
    p_family[-cols+1:] = np.nan
    order = np.flatnonzero(np.isfinite(p_family))
    order = order[np.argsort(p_family[order])]
    size = len(order)
    harmonic = math.fsum(1 / rank for rank in range(1, size + 1))
    adjusted = np.full_like(p, np.nan)
    if size:
        ranked = p_family[order] * size * harmonic / np.arange(1, size + 1)
        adjusted[order] = np.minimum(1, np.minimum.accumulate(ranked[::-1])[::-1])
    adjusted[:cols] = adjusted[0]
    adjusted[-cols:] = adjusted[-cols]
    flags = np.where(np.isfinite(adjusted), (adjusted <= .10).astype('float32'), np.nan)
    limit = max(.001, float(np.nanquantile(np.abs(slopes * 10), .98)))
    layer = np.stack([slopes * 10, flags]).astype('<f4').tobytes()
    return values, slopes, standard_error, p, adjusted, limit, layer, size, version


def get_global(variable):
    path = archive_path()
    with _lock:
        return _calculate(path, path.stat().st_mtime_ns, variable)


def cell_result(variable, row, col):
    values, slopes, standard_error, p, adjusted, _, _, _, version = get_global(variable)
    i = row * GRID_SHAPE[1] + col
    slope = slopes[i] * 10
    error = standard_error[i] * 10
    margin = stats.t.ppf(.975, len(YEARS) - 2) * error
    def number(x):
        return float(x) if np.isfinite(x) else None
    return {
        'latitude': -90 + row * .5, 'longitude': -180 + col * .625,
        'parameter': PARAMETERS[variable][0], 'unit': PARAMETERS[variable][1],
        'slope': number(slope), 'ci_low': number(slope - margin) if error > 0 else None,
        'ci_high': number(slope + margin) if error > 0 else None,
        'p': number(p[i]), 'q': number(adjusted[i]), 'version': version,
        'series': [{'year': year, 'value': number(value)} for year, value in zip(YEARS, values[:, row, col])],
    }
