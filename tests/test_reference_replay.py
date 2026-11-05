import sys
from pathlib import Path
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import reference_replay as replay


class ReferenceReplayTests(unittest.TestCase):
    def test_root_in_owner_bootstrap_session_required(self):
        replay.require_context(0,501,501)
        for uid,manager in [(0,0),(501,501)]:
            with self.assertRaises(ValueError):replay.require_context(uid,manager,501)

    def test_only_fixed_version_path_is_read(self):
        common=Mock();common.read_path.return_value=(1,b'IIIA_VERSION\n')
        camera=object()
        self.assertEqual(replay.read_version(common,camera),'IIIA_VERSION')
        common.read_path.assert_called_once_with(camera,'/version.txt')

    def test_reference_success_status_zero_is_accepted(self):
        common=Mock();common.read_path.return_value=(0,b'VERSION')
        self.assertEqual(replay.read_version(common,object()),'VERSION')

    def test_invalid_response_is_not_a_success(self):
        for response,data in [(5,b'error'),(1,b''),(1,b'x'*129)]:
            common=Mock();common.read_path.return_value=(response,data)
            with self.assertRaises(ValueError):replay.read_version(common,object())


if __name__=='__main__':unittest.main()
