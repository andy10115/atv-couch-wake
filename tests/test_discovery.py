from __future__ import annotations

import ipaddress
import unittest
from unittest.mock import AsyncMock, patch

from atv_couch_wake.discovery import DeviceCandidate, discover_all


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_mdns_and_subnet_results_are_combined(self) -> None:
        mdns = [DeviceCandidate("10.0.0.10", "Other TV", source="mdns")]
        scan = [DeviceCandidate("10.0.0.20", source="subnet-scan")]
        with (
            patch("atv_couch_wake.discovery.discover_mdns", AsyncMock(return_value=mdns)),
            patch(
                "atv_couch_wake.discovery.local_ipv4_networks",
                return_value=[ipaddress.ip_network("10.0.0.0/30")],
            ),
            patch("atv_couch_wake.discovery.scan_networks", AsyncMock(return_value=scan)),
        ):
            found = await discover_all(mdns_timeout=0.01, probe_timeout=0.01)
        self.assertEqual([item.host for item in found], ["10.0.0.10", "10.0.0.20"])


if __name__ == "__main__":
    unittest.main()
