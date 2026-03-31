import importlib.machinery
import importlib.util
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Load the script with hyphens in name as a module
loader = importlib.machinery.SourceFileLoader(
    "gclient_new_workdir",
    "/usr/local/google/btrfs_mount/source/chromium_base/src/third_party/depot_tools/gclient-new-workdir.py",
)
spec = importlib.util.spec_from_loader(loader.name, loader)
gclient_new_workdir = importlib.util.module_from_spec(spec)
loader.exec_module(gclient_new_workdir)


class TestGclientNewWorkdir(unittest.TestCase):

    @patch("subprocess.run")
    @patch("subprocess.check_call")
    @patch("os.makedirs")
    @patch("sys.exit")
    def test_abort_on_btrfs_fail(self, mock_exit, mock_makedirs,
                                 mock_check_call, mock_run):
        # Setup mocks
        # try_btrfs_subvol_snapshot fails
        mock_check_call.side_effect = OSError("Failed")

        # stat -c %i returns 256 (is subvolume)
        mock_run_res = MagicMock()
        mock_run_res.returncode = 0
        mock_run_res.stdout = "256\n"
        mock_run.return_value = mock_run_res

        # Make mock_exit raise SystemExit to stop execution
        mock_exit.side_effect = SystemExit(1)

        # Mock parse_options
        mock_args = MagicMock()
        mock_args.repository = "repo"
        mock_args.new_workdir = "dest"
        mock_args.btrfs_use_sudo = False

        with patch.object(gclient_new_workdir,
                          "parse_options",
                          return_value=mock_args):
            try:
                gclient_new_workdir.main()
            except SystemExit:
                pass  # Expected

        # Assertions
        mock_exit.assert_called_with(1)
        mock_makedirs.assert_not_called()

    @patch("subprocess.run")
    @patch("subprocess.check_call")
    @patch("os.makedirs")
    @patch("sys.exit")
    def test_fallback_on_non_subvolume(self, mock_exit, mock_makedirs,
                                       mock_check_call, mock_run):
        # Setup mocks
        # try_btrfs_subvol_snapshot fails
        mock_check_call.side_effect = OSError("Failed")

        # stat -c %i returns something else
        mock_run_res = MagicMock()
        mock_run_res.returncode = 0
        mock_run_res.stdout = "123\n"
        mock_run.return_value = mock_run_res

        # Mock parse_options
        mock_args = MagicMock()
        mock_args.repository = "repo"
        mock_args.new_workdir = "dest"
        mock_args.btrfs_use_sudo = False

        with patch.object(gclient_new_workdir,
                          "parse_options",
                          return_value=mock_args):
            try:
                gclient_new_workdir.main()
            except SystemExit:
                pass

        # Assertions
        mock_exit.assert_not_called()
        # It should proceed to os.makedirs with resolved path
        mock_makedirs.assert_called_with(os.path.realpath("dest"))


if __name__ == "__main__":
    unittest.main()
