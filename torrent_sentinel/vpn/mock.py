from torrent_sentinel.vpn.base import BaseVPNAdapter
from torrent_sentinel.models import LocationProfile
from typing import List, Optional

class MockVPNAdapter(BaseVPNAdapter):
    def __init__(self):
        self.current_profile: Optional[LocationProfile] = None
        self.profiles = [
            LocationProfile(id="us-nyc", name="US New York", country="US", endpoint="1.2.3.4", config_file="us-nyc.conf"),
            LocationProfile(id="nl-ams", name="NL Amsterdam", country="NL", endpoint="5.6.7.8", config_file="nl-ams.conf"),
            LocationProfile(id="de-fra", name="DE Frankfurt", country="DE", endpoint="9.10.11.12", config_file="de-fra.conf")
        ]

    async def get_available_locations(self) -> List[LocationProfile]:
        return self.profiles

    async def rotate_to(self, profile: LocationProfile) -> bool:
        self.current_profile = profile
        return True

    async def get_current_profile(self) -> Optional[LocationProfile]:
        return self.current_profile
