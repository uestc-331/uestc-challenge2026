#!/usr/bin/env python3
"""Core RGB-D detection logic for red spherical danger sources.

The functions in this module intentionally avoid ROS imports so they can be
unit-tested outside a ROS Noetic workspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import pi
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np


@dataclass(frozen=True)
class VisionConfig:
    min_red_saturation: float = 0.38
    min_red_value: float = 0.18
    red_ratio: float = 1.35
    min_component_area: int = 40
    max_component_area_fraction: float = 0.25
    min_extent: float = 0.45
    max_extent: float = 0.88
    min_aspect_ratio: float = 0.55
    max_aspect_ratio: float = 1.8
    min_circularity: float = 0.45
    min_depth_points: int = 18
    sphere_radius_m: float = 0.15
    sphere_radius_tolerance_m: float = 0.13
    max_depth_m: float = 8.0
    min_depth_m: float = 0.05
    max_sphere_rmse_m: float = 0.08
    max_plane_ratio: float = 0.72
    cluster_radius_m: float = 0.75
    min_cluster_observations: int = 2


@dataclass
class ImageCandidate:
    bbox: Tuple[int, int, int, int]
    centroid_px: Tuple[float, float]
    area: int
    circularity: float
    extent: float
    aspect_ratio: float
    score: float


@dataclass
class Detection3D:
    position_camera: np.ndarray
    radius_m: float
    rmse_m: float
    observations: int
    image_candidate: ImageCandidate


def rgb_to_hsv01(rgb: np.ndarray) -> np.ndarray:
    """Convert uint8/float RGB image to HSV, all channels in [0, 1]."""
    arr = np.asarray(rgb, dtype=np.float32)
    if arr.max(initial=0.0) > 1.0:
        arr = arr / 255.0

    r = arr[..., 0]
    g = arr[..., 1]
    b = arr[..., 2]
    maxc = np.max(arr, axis=-1)
    minc = np.min(arr, axis=-1)
    delta = maxc - minc

    h = np.zeros_like(maxc)
    nonzero = delta > 1e-6

    mask = nonzero & (maxc == r)
    h[mask] = ((g[mask] - b[mask]) / delta[mask]) % 6.0

    mask = nonzero & (maxc == g)
    h[mask] = ((b[mask] - r[mask]) / delta[mask]) + 2.0

    mask = nonzero & (maxc == b)
    h[mask] = ((r[mask] - g[mask]) / delta[mask]) + 4.0
    h = h / 6.0

    s = np.zeros_like(maxc)
    valid_value = maxc > 1e-6
    s[valid_value] = delta[valid_value] / maxc[valid_value]

    return np.stack((h, s, maxc), axis=-1)


def red_mask(rgb: np.ndarray, config: VisionConfig = VisionConfig()) -> np.ndarray:
    """Return a high-recall mask for red objects in low-saturation rooms."""
    arr = np.asarray(rgb, dtype=np.float32)
    if arr.max(initial=0.0) > 1.0:
        arr = arr / 255.0

    hsv = rgb_to_hsv01(arr)
    hue = hsv[..., 0]
    sat = hsv[..., 1]
    val = hsv[..., 2]

    red_hue = (hue <= 12.0 / 360.0) | (hue >= 345.0 / 360.0)
    r = arr[..., 0]
    g = arr[..., 1]
    b = arr[..., 2]
    ratio_ok = (r > config.red_ratio * (g + 1e-6)) & (r > config.red_ratio * (b + 1e-6))
    mask = red_hue & ratio_ok & (sat >= config.min_red_saturation) & (val >= config.min_red_value)
    return close_mask(open_mask(mask, 1), 2)


def open_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    return dilate_mask(erode_mask(mask, radius), radius)


def close_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    return erode_mask(dilate_mask(mask, radius), radius)


def erode_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return mask.astype(bool)
    padded = np.pad(mask.astype(bool), radius, mode="constant", constant_values=False)
    out = np.ones(mask.shape, dtype=bool)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            out &= padded[radius + dy : radius + dy + mask.shape[0], radius + dx : radius + dx + mask.shape[1]]
    return out


def dilate_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return mask.astype(bool)
    padded = np.pad(mask.astype(bool), radius, mode="constant", constant_values=False)
    out = np.zeros(mask.shape, dtype=bool)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            out |= padded[radius + dy : radius + dy + mask.shape[0], radius + dx : radius + dx + mask.shape[1]]
    return out


def find_image_candidates(mask: np.ndarray, config: VisionConfig = VisionConfig()) -> List[ImageCandidate]:
    labels = connected_components(mask.astype(bool))
    candidates: List[ImageCandidate] = []
    image_area = mask.shape[0] * mask.shape[1]
    max_area = int(image_area * config.max_component_area_fraction)

    for component in labels:
        area = int(component.shape[0])
        if area < config.min_component_area or area > max_area:
            continue

        ys = component[:, 0]
        xs = component[:, 1]
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        width = x1 - x0 + 1
        height = y1 - y0 + 1
        aspect = width / float(height)
        if aspect < config.min_aspect_ratio or aspect > config.max_aspect_ratio:
            continue

        extent = area / float(width * height)
        if extent < config.min_extent or extent > config.max_extent:
            continue

        perimeter = estimate_perimeter(component, mask.shape)
        circularity = 4.0 * pi * area / max(perimeter * perimeter, 1.0)
        if circularity < config.min_circularity:
            continue

        centroid = (float(xs.mean()), float(ys.mean()))
        score = circularity * (1.0 - abs(1.0 - aspect)) * min(1.0, area / 500.0)
        candidates.append(
            ImageCandidate(
                bbox=(x0, y0, x1, y1),
                centroid_px=centroid,
                area=area,
                circularity=float(circularity),
                extent=float(extent),
                aspect_ratio=float(aspect),
                score=float(score),
            )
        )

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def connected_components(mask: np.ndarray) -> List[np.ndarray]:
    visited = np.zeros(mask.shape, dtype=bool)
    height, width = mask.shape
    components: List[np.ndarray] = []
    ys, xs = np.nonzero(mask)

    for start_y, start_x in zip(ys.tolist(), xs.tolist()):
        if visited[start_y, start_x]:
            continue
        stack = [(start_y, start_x)]
        visited[start_y, start_x] = True
        pixels = []
        while stack:
            y, x = stack.pop()
            pixels.append((y, x))
            for ny in (y - 1, y, y + 1):
                for nx in (x - 1, x, x + 1):
                    if ny == y and nx == x:
                        continue
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
        components.append(np.asarray(pixels, dtype=np.int32))
    return components


def estimate_perimeter(component: np.ndarray, shape: Tuple[int, int]) -> float:
    component_mask = np.zeros(shape, dtype=bool)
    component_mask[component[:, 0], component[:, 1]] = True
    ys = component[:, 0]
    xs = component[:, 1]
    perimeter = 0
    for y, x in zip(ys, xs):
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if ny < 0 or nx < 0 or ny >= shape[0] or nx >= shape[1] or not component_mask[ny, nx]:
                perimeter += 1
    return float(perimeter)


def candidate_points_from_depth(
    depth_m: np.ndarray,
    candidate: ImageCandidate,
    intrinsics: Sequence[float],
    config: VisionConfig = VisionConfig(),
) -> np.ndarray:
    fx, fy, cx, cy = [float(v) for v in intrinsics]
    x0, y0, x1, y1 = candidate.bbox
    y_grid, x_grid = np.mgrid[y0 : y1 + 1, x0 : x1 + 1]
    depth_roi = np.asarray(depth_m[y0 : y1 + 1, x0 : x1 + 1], dtype=np.float32)
    finite = np.isfinite(depth_roi)
    valid = np.zeros(depth_roi.shape, dtype=bool)
    valid[finite] = (depth_roi[finite] >= config.min_depth_m) & (depth_roi[finite] <= config.max_depth_m)
    if valid.sum() < config.min_depth_points:
        return np.empty((0, 3), dtype=np.float32)

    z = depth_roi[valid]
    u = x_grid[valid].astype(np.float32)
    v = y_grid[valid].astype(np.float32)
    x = (u - cx) * z / fx
    y = (v - cy) * z / fy
    return np.stack((x, y, z), axis=1)


def fit_sphere(points: np.ndarray) -> Tuple[np.ndarray, float, float]:
    """Least-squares sphere fit. Returns center, radius, radial RMSE."""
    pts = np.asarray(points, dtype=np.float64)
    a = np.column_stack((2.0 * pts, np.ones(pts.shape[0])))
    b = np.sum(pts * pts, axis=1)
    solution, *_ = np.linalg.lstsq(a, b, rcond=None)
    center = solution[:3]
    radius_sq = float(np.dot(center, center) + solution[3])
    radius = float(np.sqrt(max(radius_sq, 0.0)))
    residuals = np.linalg.norm(pts - center, axis=1) - radius
    rmse = float(np.sqrt(np.mean(residuals * residuals)))
    return center.astype(np.float32), radius, rmse


def plane_ratio(points: np.ndarray, tolerance_m: float = 0.025) -> float:
    pts = np.asarray(points, dtype=np.float64)
    if pts.shape[0] < 3:
        return 1.0
    centered = pts - pts.mean(axis=0)
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    normal = vh[-1]
    distances = np.abs(centered @ normal)
    return float((distances <= tolerance_m).sum() / float(pts.shape[0]))


def detections_from_rgbd(
    rgb: np.ndarray,
    depth_m: np.ndarray,
    intrinsics: Sequence[float],
    config: VisionConfig = VisionConfig(),
) -> List[Detection3D]:
    mask = red_mask(rgb, config)
    image_candidates = find_image_candidates(mask, config)
    detections: List[Detection3D] = []

    for candidate in image_candidates:
        points = candidate_points_from_depth(depth_m, candidate, intrinsics, config)
        if points.shape[0] < config.min_depth_points:
            continue

        center, radius, rmse = fit_sphere(points)
        radius_ok = abs(radius - config.sphere_radius_m) <= config.sphere_radius_tolerance_m
        rmse_ok = rmse <= config.max_sphere_rmse_m
        not_plane = plane_ratio(points) <= config.max_plane_ratio
        if radius_ok and rmse_ok and not_plane:
            detections.append(
                Detection3D(
                    position_camera=center,
                    radius_m=float(radius),
                    rmse_m=float(rmse),
                    observations=int(points.shape[0]),
                    image_candidate=candidate,
                )
            )
    return detections


def cluster_world_points(
    points: Iterable[Sequence[float]],
    config: VisionConfig = VisionConfig(),
) -> List[np.ndarray]:
    pts = np.asarray(list(points), dtype=np.float32)
    if pts.size == 0:
        return []
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError("points must be an iterable of 3D coordinates")

    remaining = set(range(pts.shape[0]))
    clusters: List[List[int]] = []
    while remaining:
        seed = remaining.pop()
        cluster = {seed}
        frontier = [seed]
        while frontier:
            idx = frontier.pop()
            nearby = [
                j
                for j in list(remaining)
                if float(np.linalg.norm(pts[idx] - pts[j])) <= config.cluster_radius_m
            ]
            for j in nearby:
                remaining.remove(j)
                cluster.add(j)
                frontier.append(j)
        clusters.append(sorted(cluster))

    centers = []
    for cluster in clusters:
        if len(cluster) < config.min_cluster_observations:
            continue
        centers.append(pts[cluster].mean(axis=0))
    centers.sort(key=lambda p: (float(p[0]), float(p[1]), float(p[2])))
    return centers


def normalize_depth_image(depth: np.ndarray) -> np.ndarray:
    arr = np.asarray(depth)
    if arr.dtype == np.uint16:
        return arr.astype(np.float32) / 1000.0
    return arr.astype(np.float32)
