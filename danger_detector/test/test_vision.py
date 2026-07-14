#!/usr/bin/env python3
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from danger_detector.vision import (  # noqa: E402
    VisionConfig,
    cluster_world_points,
    detections_from_rgbd,
    find_image_candidates,
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


if __name__ == "__main__":
    unittest.main()
