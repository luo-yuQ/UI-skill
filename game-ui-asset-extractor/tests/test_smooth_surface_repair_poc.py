from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


EXPERIMENTS_DIR = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS_DIR))

from smooth_surface_repair_poc import (  # noqa: E402
    _surface_image,
    fit_surface,
    split_samples,
)


def _coords(shape: tuple[int, int], mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    fit, validation = split_samples(~mask)
    assert len(fit) > 0
    assert len(validation) > 0
    return fit, validation


def test_constant_surface_reconstructs_constant_background_and_preserves_outside_mask() -> None:
    image = np.full((30, 40, 3), [30, 80, 150], dtype=np.uint8)
    mask = np.zeros((30, 40), dtype=bool)
    mask[10:20, 12:28] = True
    fit, validation = _coords(image.shape[:2], mask)

    repaired, result = _surface_image(image, mask, fit, validation, "constant")

    assert result["status"] == "ok"
    assert result["fit_mae"] < 1e-9
    assert result["validation_mae"] < 1e-9
    np.testing.assert_array_equal(repaired[~mask], image[~mask])
    np.testing.assert_array_equal(repaired[mask], image[mask])


def test_linear_surface_beats_constant_on_linear_gradient() -> None:
    ys, xs = np.indices((35, 45))
    image = np.stack([20 + 2 * xs + ys, 40 + xs, 80 + 2 * ys], axis=-1).clip(0, 255).astype(np.uint8)
    mask = np.zeros((35, 45), dtype=bool)
    mask[10:24, 14:31] = True
    fit, validation = _coords(image.shape[:2], mask)

    constant = fit_surface(image, fit, validation, "constant")
    linear = fit_surface(image, fit, validation, "linear")

    assert linear["validation_mae"] < constant["validation_mae"]


def test_quadratic_surface_is_at_least_as_good_as_linear() -> None:
    ys, xs = np.indices((40, 50))
    image = np.stack([
        80 + xs + 2 * ys + xs * xs / 20,
        100 + 2 * xs + ys + xs * ys / 30,
        120 + ys + ys * ys / 25,
    ], axis=-1).clip(0, 255).astype(np.uint8)
    mask = np.zeros((40, 50), dtype=bool)
    mask[12:28, 16:34] = True
    fit, validation = _coords(image.shape[:2], mask)

    linear = fit_surface(image, fit, validation, "linear")
    quadratic = fit_surface(image, fit, validation, "quadratic")

    assert quadratic["validation_mae"] <= linear["validation_mae"] + 0.1


def test_sample_split_is_deterministic_and_spatially_dispersed() -> None:
    candidate = np.ones((25, 31), dtype=bool)
    fit_a, validation_a = split_samples(candidate)
    fit_b, validation_b = split_samples(candidate)

    np.testing.assert_array_equal(fit_a, fit_b)
    np.testing.assert_array_equal(validation_a, validation_b)
    ratio = len(validation_a) / (len(fit_a) + len(validation_a))
    assert 0.15 <= ratio <= 0.25
    assert len(np.unique(validation_a[:, 0])) > 1
    assert len(np.unique(validation_a[:, 1])) > 1


def test_validation_samples_are_not_fit_samples() -> None:
    candidate = np.ones((20, 20), dtype=bool)
    fit, validation = split_samples(candidate)
    assert set(map(tuple, fit)).isdisjoint(set(map(tuple, validation)))


def test_validation_values_do_not_change_fitted_surface() -> None:
    image = np.full((24, 30, 3), [20, 60, 100], dtype=np.uint8)
    candidate = np.ones((24, 30), dtype=bool)
    fit, validation = split_samples(candidate)
    baseline = fit_surface(image, fit, validation, "constant")
    changed = image.copy()
    changed[validation[:, 0], validation[:, 1]] = [240, 240, 240]
    result = fit_surface(changed, fit, validation, "constant")
    np.testing.assert_allclose(result["fit_mae"], baseline["fit_mae"])
    assert result["validation_mae"] > baseline["validation_mae"]


def test_insufficient_samples_returns_status_instead_of_raising() -> None:
    image = np.zeros((5, 5, 3), dtype=np.uint8)
    coords = np.array([[1, 1], [1, 2]], dtype=np.int64)

    result = fit_surface(image, coords[:1], coords[1:], "quadratic")

    assert result == {"status": "insufficient_samples"}
