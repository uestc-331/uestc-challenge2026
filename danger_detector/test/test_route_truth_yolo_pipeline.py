from argparse import Namespace
import importlib.util
from pathlib import Path
import unittest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "route_truth_yolo_pipeline.py"
SPEC = importlib.util.spec_from_file_location("route_truth_yolo_pipeline", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RouteTruthYoloPipelineTest(unittest.TestCase):
    def setUp(self):
        self.args = Namespace(
            goal_tolerance=0.35,
            turn_only_angle=0.75,
            max_vx=0.22,
            min_vx=0.05,
            max_wz=0.5,
            k_dist=0.35,
            k_yaw=0.9,
        )

    def test_fixed_route_is_a_closed_loop(self):
        route_file = SCRIPT_PATH.parents[3] / "config" / "fixed_floor0_route.json"
        waypoints = MODULE.load_route(route_file)
        self.assertGreater(len(waypoints), 10)
        self.assertEqual(waypoints[0], waypoints[-1])

    def test_route_default_avoids_rl_turn_deadzone(self):
        args = MODULE.build_parser().parse_args(["collect-route"])
        self.assertEqual(args.max_wz, 1.0)

    def test_route_command_turns_before_driving(self):
        linear_x, angular_z, reached, _ = MODULE.route_command(
            (0.0, 0.0, 3.141592653589793), (1.0, 0.0), self.args
        )
        self.assertEqual(linear_x, 0.0)
        self.assertEqual(abs(angular_z), self.args.max_wz)
        self.assertFalse(reached)

    def test_route_command_stops_inside_goal_tolerance(self):
        linear_x, angular_z, reached, _ = MODULE.route_command(
            (0.0, 0.0, 0.0), (0.1, 0.1), self.args
        )
        self.assertEqual((linear_x, angular_z), (0.0, 0.0))
        self.assertTrue(reached)

    def test_route_command_keeps_policy_minimum_speed(self):
        self.args.turn_only_angle = 0.2
        self.args.max_vx = 0.5
        self.args.min_vx = 0.5
        linear_x, _, reached, _ = MODULE.route_command(
            (0.0, 0.0, 0.1), (1.0, 0.0), self.args
        )
        self.assertEqual(linear_x, 0.5)
        self.assertFalse(reached)


if __name__ == "__main__":
    unittest.main()
