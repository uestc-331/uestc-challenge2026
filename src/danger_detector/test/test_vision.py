#!/usr/bin/env python3
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from danger_detector.vision import (  # noqa: E402
    LandmarkConfig,
    Observation3D,
    VisionConfig,
    cluster_landmarks,
    cluster_world_points,
    detections_from_rgbd,
    find_image_candidates,
    localize_yolo_sphere,
    red_mask,
)


def draw_disk(image, depth, center, radius_px, color, z_center=2.0, radius_m=0.15, fx=520.0):
    cx, cy = center
    yy, xx = np.mgrid[0 : image.shape[0], 0 : image.shape[1]]
    rr = (xx - cx) ** 2 + (yy - cy) ** 2
    disk = rr <= radius_px**2
    image[disk] = color
    normalized = np.sqrt(np.maximum(0.0, 1.0 - rr[disk] / float(radius_px**2)))
    depth[disk] = z_center - radius_m * normalized
    return disk


class VisionTest(unittest.TestCase):
    @staticmethod
    def observation(x, quality="strong", stamp=0.0):
        return Observation3D(
            position=np.asarray([x, 0.0, 0.0], dtype=np.float32),
            stamp=stamp,
            confidence=0.8,
            quality=quality,
            depth_metrics={},
        )

    def test_red_mask_ignores_green(self):
        rgb = np.zeros((80, 120, 3), dtype=np.uint8)
        rgb[:, :] = [70, 70, 70]
        rgb[20:45, 20:45] = [220, 20, 15]
        rgb[20:45, 60:85] = [15, 210, 25]

        mask = red_mask(rgb)

        self.assertGreater(mask[25:40, 25:40].mean(), 0.9)
        self.assertLess(mask[25:40, 65:80].mean(), 0.05)

    def test_shape_filter_rejects_red_square_and_accepts_round_blob(self):
        mask = np.zeros((120, 180), dtype=bool)
        yy, xx = np.mgrid[0:120, 0:180]
        mask[((xx - 50) ** 2 + (yy - 60) ** 2) <= 18**2] = True
        mask[35:75, 115:155] = True

        candidates = find_image_candidates(mask, VisionConfig(min_component_area=50))

        self.assertEqual(len(candidates), 1)
        self.assertLess(candidates[0].centroid_px[0], 80)

    def test_rgbd_detects_red_sphere(self):
        rgb = np.zeros((160, 220, 3), dtype=np.uint8)
        rgb[:, :] = [70, 70, 70]
        depth = np.full((160, 220), np.nan, dtype=np.float32)
        draw_disk(rgb, depth, (110, 80), 32, [230, 20, 15])
        intrinsics = (520.0, 520.0, 110.0, 80.0)

        detections = detections_from_rgbd(rgb, depth, intrinsics, VisionConfig(min_depth_points=30))

        self.assertEqual(len(detections), 1)
        self.assertAlmostEqual(float(detections[0].position_camera[2]), 2.0, delta=0.12)
        self.assertAlmostEqual(detections[0].radius_m, 0.15, delta=0.08)

    def test_cluster_world_points_requires_repeated_observations(self):
        config = VisionConfig(cluster_radius_m=0.5, min_cluster_observations=2)
        points = [
            [1.0, 1.0, 0.2],
            [1.1, 1.0, 0.22],
            [4.0, 4.0, 0.2],
        ]

        clusters = cluster_world_points(points, config)

        self.assertEqual(len(clusters), 1)
        self.assertTrue(np.allclose(clusters[0], [1.05, 1.0, 0.21], atol=0.02))

    def test_yolo_depth_localizer_finds_complete_sphere_center(self):
        rgb = np.full((160, 220, 3), 70, dtype=np.uint8)
        depth = np.full((160, 220), np.nan, dtype=np.float32)
        draw_disk(rgb, depth, (110, 80), 32, [230, 20, 15])

        result = localize_yolo_sphere(
            rgb, depth, [75, 45, 145, 115], (520.0, 520.0, 110.0, 80.0)
        )

        self.assertEqual(result.quality, "strong")
        np.testing.assert_allclose(result.position_camera, [0.0, 0.0, 2.0], atol=0.05)

    def test_yolo_depth_localizer_ignores_gray_center_occluder(self):
        rgb = np.full((160, 220, 3), 70, dtype=np.uint8)
        depth = np.full((160, 220), np.nan, dtype=np.float32)
        draw_disk(rgb, depth, (110, 80), 32, [230, 20, 15], z_center=2.5)
        rgb[65:96, 98:123] = [90, 90, 90]
        depth[65:96, 98:123] = 1.0

        result = localize_yolo_sphere(
            rgb, depth, [75, 45, 145, 115], (520.0, 520.0, 110.0, 80.0)
        )

        if result.position_camera is not None:
            self.assertGreater(float(result.position_camera[2]), 2.2)

    def test_yolo_depth_localizer_never_returns_door_depth(self):
        rgb = np.full((160, 220, 3), 70, dtype=np.uint8)
        depth = np.full((160, 220), np.nan, dtype=np.float32)
        draw_disk(rgb, depth, (110, 80), 32, [230, 20, 15], z_center=2.5)
        rgb[40:121, 104:117] = [95, 95, 95]
        depth[40:121, 104:117] = 1.2

        result = localize_yolo_sphere(
            rgb, depth, [75, 40, 145, 120], (520.0, 520.0, 110.0, 80.0)
        )

        if result.position_camera is not None:
            self.assertGreater(float(result.position_camera[2]), 2.2)

    def test_yolo_depth_localizer_rejects_red_plane(self):
        rgb = np.full((160, 220, 3), 70, dtype=np.uint8)
        depth = np.full((160, 220), np.nan, dtype=np.float32)
        rgb[55:106, 85:136] = [230, 20, 15]
        depth[55:106, 85:136] = 2.0

        result = localize_yolo_sphere(
            rgb, depth, [82, 52, 138, 108], (520.0, 520.0, 110.0, 80.0)
        )

        self.assertEqual(result.quality, "rejected")
        self.assertEqual(result.reason, "geometry_conflict")

    def test_edge_observations_are_weak(self):
        rgb = np.full((100, 140, 3), 70, dtype=np.uint8)
        depth = np.full((100, 140), np.nan, dtype=np.float32)
        draw_disk(rgb, depth, (5, 50), 24, [230, 20, 15])

        result = localize_yolo_sphere(
            rgb, depth, [0, 24, 31, 76], (520.0, 520.0, 70.0, 50.0)
        )

        self.assertIn(result.quality, {"weak", "rejected"})

    def test_landmark_clustering_is_deterministic(self):
        base = [
            self.observation(value, stamp=float(index))
            for index, value in enumerate([0.00, 0.03, -0.02, 0.31, 0.33, 0.29])
        ]
        config = LandmarkConfig(0.35, 5, 2)
        expected = cluster_landmarks(base, config)[0].center
        random = np.random.default_rng(20550755)
        for _ in range(100):
            shuffled = [base[index] for index in random.permutation(len(base))]
            result = cluster_landmarks(shuffled, config)
            self.assertEqual(len(result), 1)
            np.testing.assert_array_equal(result[0].center, expected)

    def test_landmark_clustering_merges_close_subclusters(self):
        observations = [self.observation(value) for value in [0.00, 0.02, 0.31, 0.32, 0.34]]
        result = cluster_landmarks(observations, LandmarkConfig(0.35, 5, 2))
        self.assertEqual(len(result), 1)

    def test_bridge_does_not_join_independent_landmarks(self):
        observations = [self.observation(value) for value in [0.00, 0.05, 0.40, 0.80, 0.85]]
        result = cluster_landmarks(observations, LandmarkConfig(0.5, 2, 2))
        self.assertEqual(len(result), 2)

    def test_landmarks_065m_apart_remain_separate(self):
        observations = [
            self.observation(value)
            for value in [0.00, 0.02, -0.02, 0.64, 0.65, 0.67]
        ]
        result = cluster_landmarks(observations, LandmarkConfig(0.35, 2, 2))
        self.assertEqual(len(result), 2)

    def test_weak_observations_cannot_form_landmark(self):
        weak_only = [self.observation(0.01 * index, quality="weak") for index in range(5)]
        self.assertEqual(cluster_landmarks(weak_only, LandmarkConfig(0.35, 5, 2)), [])

        with_strong = weak_only[:3] + [self.observation(0.0), self.observation(0.02)]
        result = cluster_landmarks(with_strong, LandmarkConfig(0.35, 5, 2))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].strong_support, 2)


if __name__ == "__main__":
    unittest.main()
