"""World archive and statistics regression tests using synthetic data."""
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient
from scipy.stats import t

from app import global_map, main


def make_archive(tmp_path, monkeypatch, *, reverse=False, missing=None):
    monkeypatch.setattr(global_map, "GRID_SHAPE", (3, 4))
    folder = tmp_path / "global"
    folder.mkdir()
    x = np.arange(36, dtype=float)
    # Polar node series differ by construction. Retain distinct tests there.
    values = 10 + x[:, None] * np.linspace(0.001, 0.12, 12)
    values += np.sin(x[:, None] * 0.75 + np.arange(12)[None, :] * 0.2)
    if missing is not None:
        values[0, 1] = missing
    np.savez(folder / "annual.npz", temperature=values.reshape(36, 3, 4),
             years=global_map.YEARS, lat=[90, 0, -90] if reverse else [-90, 0, 90],
             lon=[-180, -90, 0, 90], version="synthetic test")
    monkeypatch.setenv("EARTHSCOPE_CACHE_DIR", str(tmp_path))
    return values


def test_world_hac_and_full_family_include_distinct_polar_series(tmp_path, monkeypatch):
    y = make_archive(tmp_path, monkeypatch)
    _, slopes, se, p, q, _, _, tested, _ = global_map.get_global("temperature")
    assert tested == y.shape[1]
    # Independent HAC reference via the full sandwich and a Bartlett weight matrix.
    x = np.column_stack((np.ones(36), np.arange(36, dtype=float)))
    coef = np.linalg.lstsq(x, y, rcond=None)[0]
    residual = y - x @ coef
    inverse = np.linalg.inv(x.T @ x)
    distance = np.abs(np.subtract.outer(np.arange(36), np.arange(36)))
    weights = np.maximum(0, 1 - distance / 4)
    ref = []
    for i in range(y.shape[1]):
        scores = x * residual[:, i, None]
        covariance = (36 / 34) * inverse @ scores.T @ weights @ scores @ inverse
        ref.append(math.sqrt(covariance[1, 1]))
    assert slopes == pytest.approx(coef[1])
    assert se == pytest.approx(ref)
    assert p == pytest.approx(2 * t.sf(np.abs(coef[1] / ref), 34))
    order = np.argsort(p)
    harmonic = sum(1 / i for i in range(1, tested + 1))
    expected = p[order] * tested * harmonic / np.arange(1, tested + 1)
    for i, j in enumerate(order):
        assert q[j] == pytest.approx(min(1, *expected[i:]))
    assert q[0] != q[3]


def test_world_endpoints_reject_reversed_archive_coordinates(tmp_path, monkeypatch):
    make_archive(tmp_path, monkeypatch, reverse=True)
    with TestClient(main.app) as client:
        for path in ("/api/maps/world", "/api/maps/world/cell?row=0&col=0"):
            response = client.get(path)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "GLOBAL_ARCHIVE_INVALID"
            assert str(tmp_path) not in response.text


@pytest.mark.parametrize("missing", [-999, float("nan")])
def test_world_archive_rejects_missing_observations(tmp_path, monkeypatch, missing):
    make_archive(tmp_path, monkeypatch, missing=missing)
    with pytest.raises(ValueError, match="missing|implausible"):
        global_map.get_global("temperature")
