#!/usr/bin/env python3
"""Core RGB-D detection logic for red spherical danger sources.

The functions in this module intentionally avoid ROS imports so they can be
unit-tested outside a ROS Noetic workspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import pi
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

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


@dataclass(frozen=True)
class YoloDepthConfig:
    min_depth_m: float = 0.1
    max_depth_m: float = 12.0
    min_weak_depth_points: int = 8
    min_strong_depth_points: int = 18
    sphere_radius_m: float = 0.15
    sphere_radius_tolerance_m: float = 0.08
    max_sphere_rmse_m: float = 0.05
    max_plane_ratio: float = 0.70
    max_depth_mad_m: float = 0.08


@dataclass
class DepthObservation:
    position_camera: Optional[np.ndarray]
    quality: str
    reason: str
    surface_depth_m: Optional[float]
    mask_pixels: int
    valid_depth_pixels: int
    depth_mad_m: Optional[float]
    sphere_radius_m: Optional[float]
    sphere_rmse_m: Optional[float]
    plane_ratio: Optional[float]
    mask_roi: Optional[np.ndarray] = None

    def metrics(self) -> Dict[str, object]:
        return {
            "mask_pixels": self.mask_pixels,
            "valid_depth_pixels": self.valid_depth_pixels,
            "depth_mad_m": self.depth_mad_m,
            "sphere_radius_m": self.sphere_radius_m,
            "sphere_rmse_m": self.sphere_rmse_m,
            "plane_ratio": self.plane_ratio,
        }


@dataclass(frozen=True)
class Observation3D:
    position: np.ndarray
    stamp: float
    confidence: float
    quality: str
    depth_metrics: Dict[str, object]


@dataclass(frozen=True)
class LandmarkConfig:
    cluster_radius_m: float = 0.35
    min_total_observations: int = 5
    min_strong_observations: int = 2


@dataclass
class LandmarkCluster:
    center: np.ndarray
    total_support: int
    strong_support: int
    spread_m: float
    mean_confidence: float
    observations: List[Observation3D]


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


def _empty_depth_observation(reason: str, mask_pixels: int = 0, mask_roi=None) -> DepthObservation:
    return DepthObservation(
        position_camera=None,
        quality="rejected",
        reason=reason,
        surface_depth_m=None,
        mask_pixels=mask_pixels,
        valid_depth_pixels=0,
        depth_mad_m=None,
        sphere_radius_m=None,
        sphere_rmse_m=None,
        plane_ratio=None,
        mask_roi=mask_roi,
    )


def _ray_sphere_center(
    u: float,
    v: float,
    depth_z: float,
    intrinsics: Sequence[float],
    sphere_radius_m: float,
) -> np.ndarray:
    fx, fy, cx, cy = [float(value) for value in intrinsics]
    ray = np.array([(u - cx) / fx, (v - cy) / fy, 1.0], dtype=np.float64)
    surface = ray * float(depth_z)
    return (surface + sphere_radius_m * ray / np.linalg.norm(ray)).astype(np.float32)


def localize_yolo_sphere(
    rgb: np.ndarray,
    depth_m: np.ndarray,
    bbox: Sequence[int],
    intrinsics: Sequence[float],
    config: YoloDepthConfig = YoloDepthConfig(),
) -> DepthObservation:
    """Localize a YOLO red-sphere box using only red target pixels and RGB-D geometry."""
    image = np.asarray(rgb)
    depth = np.asarray(depth_m, dtype=np.float32)
    if image.ndim != 3 or depth.ndim != 2 or image.shape[:2] != depth.shape:
        raise ValueError("rgb and depth must have matching HxW dimensions")

    height, width = depth.shape
    bx0, by0, bx1, by1 = [int(round(value)) for value in bbox]
    x0 = max(0, min(width - 1, bx0))
    y0 = max(0, min(height - 1, by0))
    x1 = max(0, min(width - 1, bx1))
    y1 = max(0, min(height - 1, by1))
    if x1 <= x0 or y1 <= y0:
        return _empty_depth_observation("invalid_bbox")

    roi_rgb = image[y0 : y1 + 1, x0 : x1 + 1]
    components = connected_components(red_mask(roi_rgb))
    if not components:
        return _empty_depth_observation("no_red_component")

    component = max(components, key=lambda item: int(item.shape[0]))
    component_mask = np.zeros(roi_rgb.shape[:2], dtype=bool)
    component_mask[component[:, 0], component[:, 1]] = True
    mask_pixels = int(component_mask.sum())
    eroded_mask = erode_mask(component_mask, 1)
    erosion_insufficient = int(eroded_mask.sum()) < config.min_weak_depth_points
    selected_mask = component_mask if erosion_insufficient else eroded_mask

    roi_depth = depth[y0 : y1 + 1, x0 : x1 + 1]
    finite_mask = np.isfinite(roi_depth)
    valid_mask = np.zeros(roi_depth.shape, dtype=bool)
    valid_mask[finite_mask] = selected_mask[finite_mask] & (
        (roi_depth[finite_mask] >= config.min_depth_m)
        & (roi_depth[finite_mask] <= config.max_depth_m)
    )
    valid_depths = roi_depth[valid_mask]
    if valid_depths.size < config.min_weak_depth_points:
        result = _empty_depth_observation("insufficient_masked_depth", mask_pixels, component_mask)
        result.valid_depth_pixels = int(valid_depths.size)
        return result

    median_depth = float(np.median(valid_depths))
    depth_mad = float(np.median(np.abs(valid_depths - median_depth)))
    if depth_mad > config.max_depth_mad_m:
        return DepthObservation(
            position_camera=None,
            quality="rejected",
            reason="depth_multimodal",
            surface_depth_m=median_depth,
            mask_pixels=mask_pixels,
            valid_depth_pixels=int(valid_depths.size),
            depth_mad_m=depth_mad,
            sphere_radius_m=None,
            sphere_rmse_m=None,
            plane_ratio=None,
            mask_roi=component_mask,
        )

    inlier_window = max(0.03, 3.0 * 1.4826 * depth_mad)
    inlier_mask = np.zeros(roi_depth.shape, dtype=bool)
    inlier_mask[valid_mask] = (
        np.abs(roi_depth[valid_mask] - median_depth) <= inlier_window
    )
    ys, xs = np.nonzero(inlier_mask)
    if xs.size < config.min_weak_depth_points:
        result = _empty_depth_observation("insufficient_depth_inliers", mask_pixels, component_mask)
        result.valid_depth_pixels = int(xs.size)
        result.depth_mad_m = depth_mad
        result.surface_depth_m = median_depth
        return result

    z = roi_depth[ys, xs].astype(np.float32)
    u = (xs + x0).astype(np.float32)
    v = (ys + y0).astype(np.float32)
    fx, fy, cx, cy = [float(value) for value in intrinsics]
    points = np.stack(((u - cx) * z / fx, (v - cy) * z / fy, z), axis=1)
    center, radius, rmse = fit_sphere(points)
    planar = plane_ratio(points)
    surface_depth = float(np.median(z))
    edge_truncated = bx0 <= 0 or by0 <= 0 or bx1 >= width - 1 or by1 >= height - 1

    hard_geometry_conflict = (
        radius < 0.03
        or radius > 0.40
        or rmse > 0.10
        or planar > 0.90
    )
    if hard_geometry_conflict:
        return DepthObservation(
            position_camera=None,
            quality="rejected",
            reason="geometry_conflict",
            surface_depth_m=surface_depth,
            mask_pixels=mask_pixels,
            valid_depth_pixels=int(points.shape[0]),
            depth_mad_m=depth_mad,
            sphere_radius_m=radius,
            sphere_rmse_m=rmse,
            plane_ratio=planar,
            mask_roi=component_mask,
        )

    strong_geometry = (
        points.shape[0] >= config.min_strong_depth_points
        and abs(radius - config.sphere_radius_m) <= config.sphere_radius_tolerance_m
        and rmse <= config.max_sphere_rmse_m
        and planar <= config.max_plane_ratio
        and not edge_truncated
        and not erosion_insufficient
    )
    if strong_geometry:
        quality = "strong"
        reason = "ok"
        position = center
    else:
        quality = "weak"
        if edge_truncated:
            reason = "edge_truncated"
        elif erosion_insufficient or points.shape[0] < config.min_strong_depth_points:
            reason = "insufficient_strong_points"
        else:
            reason = "sphere_fit_uncertain"
        position = _ray_sphere_center(
            float(np.median(u)),
            float(np.median(v)),
            surface_depth,
            intrinsics,
            config.sphere_radius_m,
        )

    return DepthObservation(
        position_camera=position,
        quality=quality,
        reason=reason,
        surface_depth_m=surface_depth,
        mask_pixels=mask_pixels,
        valid_depth_pixels=int(points.shape[0]),
        depth_mad_m=depth_mad,
        sphere_radius_m=radius,
        sphere_rmse_m=rmse,
        plane_ratio=planar,
        mask_roi=component_mask,
    )


def _provisional_cluster_center(observations: Sequence[Observation3D]) -> np.ndarray:
    strong = [item.position for item in observations if item.quality == "strong"]
    values = strong or [item.position for item in observations]
    return np.asarray(values, dtype=np.float32).mean(axis=0)


def _robust_strong_center(
    observations: Sequence[Observation3D], config: LandmarkConfig
) -> Tuple[np.ndarray, float]:
    points = np.asarray(
        [item.position for item in observations if item.quality == "strong"], dtype=np.float32
    )
    median = np.median(points, axis=0)
    distances = np.linalg.norm(points - median, axis=1)
    radial_median = float(np.median(distances))
    radial_mad = float(np.median(np.abs(distances - radial_median)))
    limit = min(config.cluster_radius_m, max(0.05, radial_median + 3.0 * 1.4826 * radial_mad))
    inliers = points[distances <= limit]
    if inliers.shape[0] < config.min_strong_observations:
        inliers = points
    center = inliers.mean(axis=0)
    spread = float(np.sqrt(np.mean(np.sum((inliers - center) ** 2, axis=1))))
    return center.astype(np.float32), spread


def cluster_landmarks(
    observations: Iterable[Observation3D],
    config: LandmarkConfig = LandmarkConfig(),
) -> List[LandmarkCluster]:
    """Deterministically cluster observations and require strong geometric support."""
    items = list(observations)
    ordered = sorted(
        [item for item in items if item.quality == "strong"],
        key=lambda item: (
            float(item.stamp),
            float(item.position[0]),
            float(item.position[1]),
            float(item.position[2]),
            float(item.confidence),
        ),
    )
    weak = sorted(
        [item for item in items if item.quality == "weak"],
        key=lambda item: (
            float(item.stamp),
            float(item.position[0]),
            float(item.position[1]),
            float(item.position[2]),
            float(item.confidence),
        ),
    )
    provisional: List[List[Observation3D]] = []
    for observation in ordered:
        distances = [
            float(np.linalg.norm(observation.position - _provisional_cluster_center(cluster)))
            for cluster in provisional
        ]
        nearest = int(np.argmin(distances)) if distances else None
        if nearest is not None and distances[nearest] <= config.cluster_radius_m:
            provisional[nearest].append(observation)
        else:
            provisional.append([observation])

    while True:
        merge_candidates = []
        for first in range(len(provisional)):
            first_center = _provisional_cluster_center(provisional[first])
            for second in range(first + 1, len(provisional)):
                second_center = _provisional_cluster_center(provisional[second])
                distance = float(np.linalg.norm(first_center - second_center))
                if distance <= config.cluster_radius_m:
                    merge_candidates.append((distance, first, second))
        if not merge_candidates:
            break
        _, first, second = min(merge_candidates)
        provisional[first].extend(provisional[second])
        del provisional[second]

    for observation in weak:
        distances = [
            float(np.linalg.norm(observation.position - _provisional_cluster_center(cluster)))
            for cluster in provisional
        ]
        if not distances:
            continue
        nearest = int(np.argmin(distances))
        if distances[nearest] <= config.cluster_radius_m:
            provisional[nearest].append(observation)

    landmarks = []
    for cluster in provisional:
        strong_support = sum(item.quality == "strong" for item in cluster)
        if len(cluster) < config.min_total_observations:
            continue
        if strong_support < config.min_strong_observations:
            continue
        center, spread = _robust_strong_center(cluster, config)
        landmarks.append(
            LandmarkCluster(
                center=center,
                total_support=len(cluster),
                strong_support=strong_support,
                spread_m=spread,
                mean_confidence=float(np.mean([item.confidence for item in cluster])),
                observations=list(cluster),
            )
        )
    landmarks.sort(key=lambda item: tuple(float(value) for value in item.center))
    return landmarks


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
