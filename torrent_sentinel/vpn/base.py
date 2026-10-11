from abc import ABC, abstractmethod
from typing import List, Optional
from torrent_sentinel.models import LocationProfile

class BaseVPNAdapter(ABC):
    @abstractmethod
    async def get_available_locations(self) -> List[LocationProfile]:
        """Returns a list of available VPN profiles."""
        pass

    @abstractmethod
    async def rotate_to(self, profile: LocationProfile) -> bool:
        """Swaps the active VPN configuration to the specified profile."""
        pass

    @abstractmethod
    async def get_current_profile(self) -> Optional[LocationProfile]:
        """Returns the currently active VPN profile."""
        pass

    @abstractmethod
    async def disconnect(self) -> bool:
        """Tears down active VPN connection to run direct/without VPN."""
        pass

    async def is_connected(self) -> bool:
        """Checks if a VPN tunnel profile is currently active."""
        prof = await self.get_current_profile()
        return prof is not None

