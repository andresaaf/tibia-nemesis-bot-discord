from typing import List, Optional, Tuple, Dict

# Global game worlds list (independent of data source).
# Extend as needed; providers can override list_worlds if they support discovery.
WORLDS: List[str] = [
    "Wadira",
    "Zephyra",
]

class CheckerSource:
    """Abstract base class for a boss prediction source.
    Implementations must provide methods to build world URLs, fetch HTML, and parse spawnables.
    """
    id: str = "base"
    display_name: str = "Base"

    def list_worlds(self) -> List[str]:
        """Optional: list known worlds to offer as choices. Can be static or fetched.
        Default returns global WORLDS.
        """
        return list(WORLDS)

    def world_url(self, world: str) -> str:
        raise NotImplementedError

    async def fetch_world_html(self, world: str) -> Optional[str]:
        raise NotImplementedError

    def parse_spawnables(self, html: str) -> List[Tuple[str, Optional[int]]]:
        """Return a list of (boss_name, percent_or_None) that could spawn.
        Must exclude explicit "No Chance" rows and include entries from a corresponding
        "without prediction" section (if present) with percent None.
        """
        raise NotImplementedError

    def parse_days_since_last_kill(self, html: str) -> Dict[str, int]:
        """Optional: Return mapping of boss_name -> days since last kill if available.
        Default returns empty mapping; sources can override when the information exists.
        Names should match the same text returned by parse_spawnables before canonicalization.
        """
        return {}
