from __future__ import annotations

import unittest

from atv_couch_wake.adb_control import input_uri, parse_current_input, parse_power_state, parse_tv_inputs

DUMPSYS_TV_INPUT = """
  inputMap: inputId -> TvInputState
    com.tcl.tvinput/.TvPassThroughService/HW17: info: \
TvInputInfo{id=com.tcl.tvinput/.TvPassThroughService/HW17, pkg=com.tcl.tvinput}; state: 1
    com.tcl.tvinput/.TvPassThroughService/HW18: info: \
TvInputInfo{id=com.tcl.tvinput/.TvPassThroughService/HW18, pkg=com.tcl.tvinput}; state: 0
    com.tcl.tvinput/.TunerInputService/HW2: info: \
TvInputInfo{id=com.tcl.tvinput/.TunerInputService/HW2, pkg=com.tcl.tvinput}; state: 0
    com.tcl.tvinput/.TvPassThroughService/HW15: info: \
TvInputInfo{id=com.tcl.tvinput/.TvPassThroughService/HW15, pkg=com.tcl.tvinput}; state: 0
    com.tcl.tvinput/.TvPassThroughService/HW16: info: \
TvInputInfo{id=com.tcl.tvinput/.TvPassThroughService/HW16, pkg=com.tcl.tvinput}; state: 1
      inputId: com.tcl.tvinput/.TvPassThroughService/HW15
"""


class ADBParsingTests(unittest.TestCase):
    def test_parses_tcl_passthrough_inputs_and_ignores_tuner(self) -> None:
        inputs = parse_tv_inputs(DUMPSYS_TV_INPUT)
        self.assertEqual([item.hardware_id for item in inputs], ["HW15", "HW16", "HW17", "HW18"])
        self.assertTrue(all("TunerInputService" not in item.input_id for item in inputs))

    def test_builds_encoded_passthrough_uri(self) -> None:
        value = input_uri("com.tcl.tvinput/.TvPassThroughService/HW15")
        self.assertEqual(
            value,
            "content://android.media.tv/passthrough/com.tcl.tvinput%2F.TvPassThroughService%2FHW15",
        )

    def test_parses_current_input(self) -> None:
        self.assertEqual(
            parse_current_input(DUMPSYS_TV_INPUT),
            "com.tcl.tvinput/.TvPassThroughService/HW15",
        )

    def test_parses_power_state(self) -> None:
        self.assertTrue(parse_power_state("mWakefulness=Awake"))
        self.assertFalse(parse_power_state("mWakefulness=Asleep"))
        self.assertIsNone(parse_power_state("no recognizable power state"))


if __name__ == "__main__":
    unittest.main()


class ADBControllerCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        import os
        import tempfile
        from pathlib import Path

        from atv_couch_wake.config import AppConfig

        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.log = root / "adb.log"
        self.adb = root / "adb"
        self.adb.write_text(
            """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_ADB_LOG"
case "$1" in
  connect)
    echo "connected to $2"
    exit 0
    ;;
  devices)
    printf 'List of devices attached\\n10.0.0.42:5555\\tdevice\\n'
    exit 0
    ;;
esac
case "$*" in
  *'shell input keyevent KEYCODE_WAKEUP'*) exit 0 ;;
  *'shell input keyevent KEYCODE_SLEEP'*) exit 0 ;;
  *'shell dumpsys power'*) echo 'mWakefulness=Awake'; exit 0 ;;
  *'shell dumpsys tv_input'*)
    echo 'TvInputInfo{id=com.tcl.tvinput/.TvPassThroughService/HW15, pkg=com.tcl.tvinput}'
    echo 'inputId: com.tcl.tvinput/.TvPassThroughService/HW15'
    exit 0
    ;;
  *'shell am start '*) echo 'Starting: Intent'; exit 0 ;;
  *'shell getprop ro.product.model'*) echo 'Fake TCL'; exit 0 ;;
esac
exit 0
""",
            encoding="utf-8",
        )
        self.adb.chmod(0o755)
        os.environ["FAKE_ADB_LOG"] = str(self.log)
        self.config = AppConfig()
        self.config.tv.host = "10.0.0.42"
        self.config.tv.adb_path = str(self.adb)
        self.config.tv.input_uri = input_uri("com.tcl.tvinput/.TvPassThroughService/HW15")
        self.config.behavior.wake_settle_seconds = 0.0
        self.config.behavior.input_settle_seconds = 0.0

    async def asyncTearDown(self) -> None:
        import os

        os.environ.pop("FAKE_ADB_LOG", None)
        self.tempdir.cleanup()

    async def test_wake_uses_discrete_adb_keyevent(self) -> None:
        from atv_couch_wake.adb_control import ADBController

        result = await ADBController(self.config).set_power(True)
        self.assertTrue(result.success)
        commands = self.log.read_text(encoding="utf-8")
        self.assertIn("shell input keyevent KEYCODE_WAKEUP", commands)
        self.assertNotIn("KEYCODE_POWER", commands)

    async def test_input_uses_tv_input_framework_uri(self) -> None:
        from atv_couch_wake.adb_control import ADBController

        await ADBController(self.config).select_input()
        commands = self.log.read_text(encoding="utf-8")
        self.assertIn("shell am start -a android.intent.action.VIEW -d", commands)
        self.assertIn("com.tcl.tvinput%2F.TvPassThroughService%2FHW15", commands)
