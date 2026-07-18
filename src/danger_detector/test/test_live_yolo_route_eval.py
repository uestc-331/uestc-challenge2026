import importlib.util
from pathlib import Path
import sys
import unittest

import numpy as np


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
PACKAGE_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PACKAGE_SRC_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))
SCRIPT_PATH = SCRIPTS_DIR / "live_yolo_route_eval.py"
SPEC = importlib.util.spec_from_file_location("live_yolo_route_eval", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class LiveYoloRouteEvalTest(unittest.TestCase):
    def test_route_default_avoids_rl_turn_deadzone(self):
        args = MODULE.build_parser().parse_args(
            ["run", "--model", "model.pt", "--yolo-python", "python", "--truth-file", "truth.json"]
        )
        self.assertEqual(args.max_wz, 1.0)
        self.assertEqual(args.min_cluster_observations, 5)
        self.assertEqual(args.min_strong_observations, 2)

    def test_camera_world_transform_round_trip(self):
        pose = (1.0, 2.0, 0.5, 0.0, 0.0, 0.0, 1.0)
        offset = (0.28, 0.0, 0.043)
        camera_point = np.array([0.2, -0.1, 3.0], dtype=np.float32)
        world_point = MODULE.camera_to_world(camera_point, pose, offset)
        restored = MODULE.world_to_camera(world_point, pose, offset)
        np.testing.assert_allclose(restored, camera_point, atol=1e-6)

    def test_position_matching_is_one_to_one(self):
        truths = [[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]]
        detections = [[0.1, 0.0, 0.0], [0.2, 0.0, 0.0], [5.2, 0.0, 0.0]]
        matches = MODULE.greedy_position_matches(truths, detections, 1.0)
        self.assertEqual(len(matches), 2)
        self.assertEqual({match["truth_index"] for match in matches}, {0, 1})

    def test_clusters_require_repeated_observations(self):
        points = [[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [5.0, 0.0, 0.0]]
        centers = MODULE.cluster_points(points, radius_m=0.5, min_observations=2)
        self.assertEqual(len(centers), 1)
        np.testing.assert_allclose(centers[0], [0.05, 0.0, 0.0], atol=1e-6)

    def test_clusters_are_not_joined_by_a_bridge_observation(self):
        points = [
            [0.0, 0.0, 0.0],
            [0.8, 0.0, 0.0],
            [0.05, 0.0, 0.0],
            [0.85, 0.0, 0.0],
            [0.4, 0.0, 0.0],
        ]
        centers = MODULE.cluster_points(points, radius_m=0.5, min_observations=2)
        self.assertEqual(len(centers), 2)

    def test_detection_deduplication_keeps_two_distinct_balls(self):
        detections = [
            {"bbox": [10, 10, 40, 40], "confidence": 0.8},
            {"bbox": [11, 10, 40, 40], "confidence": 0.6},
            {"bbox": [60, 10, 90, 40], "confidence": 0.7},
        ]
        result = MODULE.deduplicate_detections(detections)
        self.assertEqual(len(result), 2)
        self.assertEqual([item["confidence"] for item in result], [0.8, 0.7])


if __name__ == "__main__":
    unittest.main()
