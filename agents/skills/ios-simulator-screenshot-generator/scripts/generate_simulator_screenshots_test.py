#!/usr/bin/env python3
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Import functions from generate_simulator_screenshots.py
sys.path.insert(0, os.path.dirname(__file__))
import generate_simulator_screenshots as script


class GenerateSimulatorScreenshotsTest(unittest.TestCase):
    def test_get_booted_simulator_udid_found(self):
        sample_output = """
== Devices ==
-- iOS 17.0 --
    iPhone 15 Pro (12345678-ABCD-1234-ABCD-1234567890AB) (Shutdown)
    iPhone 17 Pro (DE79299C-BD72-4C01-BB33-D8E293155904) (Booted)
"""
        udid = script.get_booted_simulator_udid(simctl_output=sample_output)
        self.assertEqual(udid, "DE79299C-BD72-4C01-BB33-D8E293155904")

    def test_get_booted_simulator_udid_none_booted(self):
        sample_output = """
== Devices ==
-- iOS 17.0 --
    iPhone 15 Pro (12345678-ABCD-1234-ABCD-1234567890AB) (Shutdown)
"""
        udid = script.get_booted_simulator_udid(simctl_output=sample_output)
        self.assertIsNone(udid)

    @patch("subprocess.run")
    def test_run_cmd(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.stdout = "  output_string  \n"
        mock_run.return_value = mock_proc

        res = script.run_cmd(["echo", "hello"])
        self.assertEqual(res, "output_string")
        mock_run.assert_called_once_with(
            ["echo", "hello"], stdout=-1, stderr=-1, text=True
        )

    @patch("argparse.ArgumentParser.parse_args")
    def test_cli_flags_parsing(self, mock_parse_args):
        mock_args = MagicMock()
        mock_args.clean = True
        mock_args.yes = True
        mock_args.udid = "TEST-UDID"
        mock_parse_args.return_value = mock_args
        self.assertTrue(mock_args.clean)
        self.assertTrue(mock_args.yes)

    def test_cli_skip_install_and_features(self):
        test_args = [
            "--skip-install",
            "--enable-features",
            "TestFeature",
            "--launch-arg=--foo",
            "--launch-arg=bar",
        ]
        parser = script.argparse.ArgumentParser()
        parser.add_argument("--skip-install", action="store_true")
        parser.add_argument("--enable-features", type=str)
        parser.add_argument(
            "--launch-arg", action="append", dest="launch_args"
        )
        args = parser.parse_args(test_args)
        self.assertTrue(args.skip_install)
        self.assertEqual(args.enable_features, "TestFeature")
        self.assertEqual(args.launch_args, ["--foo", "bar"])


if __name__ == "__main__":
    unittest.main()
