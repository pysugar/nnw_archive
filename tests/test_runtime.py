"""Verify checkout entrypoints and scheduled execution using disposable databases."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_nnw_archive import ROOT, create_source_db


class RuntimeTests(unittest.TestCase):
    def test_checkout_wrapper_preserves_cwd_and_interpreter(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nnw archive ") as directory:
            root = Path(directory)
            source = root / "source.sqlite3"
            create_source_db(source)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            env = dict(os.environ, NNW_ARCHIVE_PYTHON=sys.executable,
                       VIRTUAL_ENV="/missing/venv", UV_PROJECT="/missing/project",
                       UV_WORKING_DIR="/missing/cwd", UV_PYTHON="/missing/python")
            command = [str(ROOT / "bin/nnw_archive"), "sync", "--source", "source.sqlite3",
                       "--data-dir", str(root / "state"), "--json"]
            for expected_inserts in (1, 0):
                result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["articles_inserted"], expected_inserts)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_checkout_wrapper_reports_missing_interpreter(self) -> None:
        result = subprocess.run(
            [str(ROOT / "bin/nnw_archive"), "--help"], capture_output=True, text=True,
            env=dict(os.environ, NNW_ARCHIVE_PYTHON="/missing/nnw-python"),
        )
        self.assertEqual(result.returncode, 127)
        self.assertIn("Python interpreter is not executable", result.stderr)

    def test_generated_launchd_command_runs_without_checkout_wrapper(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nnw launchd ") as directory:
            root = Path(directory)
            source = root / "source.sqlite3"
            create_source_db(source)
            result = subprocess.run(
                [str(ROOT / "bin/nnw_archive"), "install-launchd", "--dry-run",
                 "--source", str(source), "--data-dir", str(root / "state")],
                env=dict(os.environ, NNW_ARCHIVE_PYTHON=sys.executable), capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plist = plistlib.loads(result.stdout)
            self.assertEqual(plist["ProgramArguments"][0], sys.executable)
            result = subprocess.run(
                plist["ProgramArguments"] + ["--json"], cwd=root,
                env={"PATH": "/usr/bin:/bin", "HOME": str(root)}, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["articles_inserted"], 1)
