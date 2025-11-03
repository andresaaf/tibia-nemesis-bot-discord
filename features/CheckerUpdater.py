import logging
from typing import Set, Dict, Optional, Tuple, List
import asyncio
import aiohttp
from .Bosses import BOSSES

logger = logging.getLogger(__name__)

# Tibia game worlds list TODO: fetch from API if possible
WORLDS = [
    "Wadira",
    "Zephyra",
]

class CheckerUpdater:
    """
    Interface used by Checker to determine which bosses can spawn today and to refresh a cache.

    Uses the tibia-nemesis-api instead of direct HTML scraping.
    """
    def __init__(self, client):
        self.client = client
        # guild_id -> allowed boss names
        self._allowed_today: Dict[int, Set[str]] = {}
        # guild_id -> list of (boss_name, percent or None) for bosses that could spawn
        self._spawnables: Dict[int, List[Tuple[str, Optional[int]]]] = {}
        # API base URL from bot config
        self._api_base_url = client.api_base_url
        self._lock = asyncio.Lock()
        self._init_db()

    def _init_db(self) -> None:
        try:
            with self.client.db as db:
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS checker_worlds (
                        guild_id INTEGER PRIMARY KEY,
                        world TEXT NOT NULL
                    )
                    """
                )
        except Exception:
            logger.exception("CheckerUpdater: failed to init checker_worlds table")

    async def _fetch_spawnables_from_api(self, world: str) -> Tuple[List[Tuple[str, Optional[int]]], Dict[str, int]]:
        """
        Fetch spawn data from API GET /api/v1/spawnables?world={world}
        Returns (spawnables_list, days_map) where:
          - spawnables_list: [(name, percent), ...]
          - days_map: {name: days_since_kill, ...}
        """
        if not self._api_base_url:
            logger.error("CheckerUpdater: API_BASE_URL not configured")
            return [], {}
        
        url = f"{self._api_base_url}/api/v1/spawnables?world={world}"
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning("CheckerUpdater: API returned HTTP %s for %s", resp.status, url)
                        return [], {}
                    data = await resp.json()
                    
            # Parse JSON: [{"world": "...", "name": "...", "percent": int|null, "days_since_kill": int|null, "updated_at": "..."}]
            spawnables = []
            days_map = {}
            for entry in data:
                name = entry.get("name")
                percent = entry.get("percent")
                days = entry.get("days_since_kill")
                if name:
                    spawnables.append((name, percent))
                    if days is not None:
                        days_map[name] = days
            
            logger.info("CheckerUpdater: fetched %d spawnables from API for %s", len(spawnables), world)
            return spawnables, days_map
        except Exception:
            logger.exception("CheckerUpdater: failed to fetch from API")
            return [], {}

    async def update_cache_for_guild(self, guild_id: int) -> None:
        """Refresh internal cache for a specific guild by fetching spawn data from API.
        The API now handles inclusion_range filtering, so we just use the results directly.
        """
        try:
            world = self.get_world(guild_id)
            if not world:
                # No world configured => return no bosses
                logger.info("CheckerUpdater: no world set for guild %s; returning no bosses", guild_id)
                self._allowed_today[guild_id] = set()
                return
            
            # Fetch from API (already filtered by inclusion_range on API side)
            spawnables, days_map_raw = await self._fetch_spawnables_from_api(world)
            if not spawnables:
                logger.warning("CheckerUpdater: no spawnables returned from API for world %s", world)
                self._allowed_today[guild_id] = set()
                return

            # Canonicalize names and build allowed set
            allowed: Set[str] = set()
            canon_list: List[Tuple[str, Optional[int]]] = []

            for name, pct in spawnables:
                canon = self._canonicalize_name(name)
                if not canon:
                    continue
                allowed.add(canon)
                canon_list.append((canon, pct))

            self._allowed_today[guild_id] = allowed
            self._spawnables[guild_id] = canon_list
        except Exception:
            logger.exception("CheckerUpdater.update_cache_for_guild failed; leaving previous cache for guild %s", guild_id)

    def get_allowed_boss_names(self, guild_id: int) -> Set[str]:
        """Return the set of boss names that could spawn (percentage-present or without prediction), normalized to BOSSES['name']."""
        return set(self._allowed_today.get(guild_id, set()))

    def list_worlds(self) -> list[str]:
        return list(WORLDS)

    def get_world(self, guild_id: int) -> Optional[str]:
        try:
            with self.client.db as db:
                db.execute("SELECT world FROM checker_worlds WHERE guild_id=?", (guild_id,))
                row = db.fetchone()
                if row and row[0]:
                    return str(row[0])
        except Exception:
            logger.exception("CheckerUpdater: failed to read world for guild %s", guild_id)
        return None

    async def set_world(self, guild_id: int, world: str) -> None:
        if world not in WORLDS:
            raise ValueError("Invalid world")
        try:
            with self.client.db as db:
                db.execute("INSERT OR REPLACE INTO checker_worlds (guild_id, world) VALUES (?, ?)", (guild_id, world))
        except Exception:
            logger.exception("CheckerUpdater: failed to set world for guild %s", guild_id)
            raise

    # Source handles URL building and fetching

    # --- Parsing handled by source ---

    def _canonicalize_name(self, name: str) -> Optional[str]:
        # Map fetched names to BOSSES['name'] using case-insensitive comparison
        if not hasattr(self, "_canon_map"):
            canon: Dict[str, str] = {}
            for _k, data in BOSSES.items():
                n = str(data.get('name') or _k)
                canon[n.lower()] = n
            self._canon_map = canon
        return self._canon_map.get(name.lower())

    # --- Public helper to get spawnable bosses with percentages ---
    async def get_spawnables_with_percentages(self, guild_id: int) -> List[Tuple[str, Optional[int]]]:
        """Return list of (boss_name, percent) for bosses that could technically spawn on the guild's world.
        Names are canonicalized to BOSSES entries. If no world configured or fetch fails, returns empty list.
        Uses cached data when available; otherwise tries to fetch once.
        The API now handles inclusion_range filtering, so we just canonicalize the results.
        """
        # Serve from cache if we have it
        if guild_id in self._spawnables:
            return list(self._spawnables[guild_id])

        world = self.get_world(guild_id)
        if not world:
            return []
        
        # Fetch from API (already filtered by inclusion_range)
        raw_list, _ = await self._fetch_spawnables_from_api(world)
        if not raw_list:
            return []
        
        out: List[Tuple[str, Optional[int]]] = []
        for name, pct in raw_list:
            canon = self._canonicalize_name(name)
            if not canon:
                continue
            out.append((canon, pct))
        
        # Cache it
        self._spawnables[guild_id] = list(out)
        return out
