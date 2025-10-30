import logging
from typing import Set, Dict, Optional, Tuple, List
from .Bosses import BOSSES
from .checker_sources.base import WORLDS as GAME_WORLDS
from .checker_sources.tibia_statistic import TibiaStatisticSource

logger = logging.getLogger(__name__)

WORLDS = GAME_WORLDS

class CheckerUpdater:
    """
    Interface used by Checker to determine which bosses can spawn today and to refresh a cache.

    Replace the stubbed logic in update_cache/get_allowed_boss_names with real website parsing later.
    """
    def __init__(self, client):
        self.client = client
        # guild_id -> allowed boss names
        self._allowed_today: Dict[int, Set[str]] = {}
        # guild_id -> list of (boss_name, percent or None) for bosses that could spawn
        self._spawnables: Dict[int, List[Tuple[str, Optional[int]]]] = {}
        # Single active source (tibia-statistic.com)
        self._source = TibiaStatisticSource()
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

    async def update_cache_for_guild(self, guild_id: int) -> None:
        """Refresh internal cache for a specific guild by fetching and parsing the world page.
        Allowed set now reflects 'possible spawns' = bosses with a numeric percentage OR in the 'without predictions' table.
        Explicit 'No Chance' bosses are excluded. If no world is configured or fetch/parsing fails, store an empty allowed set.
        """
        try:
            world = self.get_world(guild_id)
            if not world:
                # No world configured => return no bosses
                logger.info("CheckerUpdater: no world set for guild %s; returning no bosses", guild_id)
                self._allowed_today[guild_id] = set()
                return
            else:
                url = self._source.world_url(world)
                html = await self._source.fetch_world_html(world)
                if html is None:
                    logger.warning("CheckerUpdater: failed to fetch %s", url)
                    self._allowed_today[guild_id] = set()
                    return
                logger.info("CheckerUpdater: fetched %s (%d bytes)", url, len(html))

            # Populate spawnables (known percentage + without predictions), excluding 'No Chance'
            spawnables = self._source.parse_spawnables(html)
            # Optionally get days since last kill for sanity overrides
            days_map_raw = {}
            try:
                days_map_raw = self._source.parse_days_since_last_kill(html) or {}
            except Exception:
                logger.exception("CheckerUpdater: source.parse_days_since_last_kill failed")
            # Allowed names derive from spawnables
            allowed: Set[str] = set()
            # Canonicalize and drop unknowns
            canon_list: List[Tuple[str, Optional[int]]] = []
            # Canonicalize days map
            canon_days: Dict[str, int] = {}
            for n_raw, d in days_map_raw.items():
                cn = self._canonicalize_name(n_raw)
                if cn is not None:
                    canon_days[cn] = d

            for name, pct in spawnables:
                canon = self._canonicalize_name(name)
                if not canon:
                    continue
                # Enforce per-boss unknown range: never show before min_days
                try:
                    meta = BOSSES.get(canon, {})
                    min_days, max_days = self._get_unknown_range(meta)
                    dval = canon_days.get(canon)
                    if dval is not None and min_days is not None and dval < min_days:
                        continue  # skip below-min entries
                except Exception:
                    pass
                allowed.add(canon)
                canon_list.append((canon, pct))

            # Range sanity overrides: always include when days >= max_days
            for canon_name, days in canon_days.items():
                if canon_name in allowed:
                    continue
                try:
                    meta = BOSSES.get(canon_name, {})
                    _min_days, max_days = self._get_unknown_range(meta)
                except Exception:
                    max_days = None
                if max_days is not None and days >= max_days:
                    allowed.add(canon_name)
                    canon_list.append((canon_name, None))
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

    def _get_unknown_range(self, meta: dict) -> Tuple[Optional[int], Optional[int]]:
        """Parse per-boss inclusion range controlling days since last kill visibility.
        Only supports 'inclusion_range' as a list/tuple [min_days, max_days].
        Returns (min_days, max_days). If not configured or invalid, returns (None, None).
        """
        if not isinstance(meta, dict):
            return None, None
        val = meta.get('inclusion_range')
        if val is None:
            return None, None
        try:
            if isinstance(val, (list, tuple)) and len(val) == 2:
                a, b = int(val[0]), int(val[1])
                if a > b:
                    a, b = b, a
                return a, b
        except Exception:
            return None, None
        return None, None

    # --- Public helper to get spawnable bosses with percentages ---
    async def get_spawnables_with_percentages(self, guild_id: int) -> List[Tuple[str, Optional[int]]]:
        """Return list of (boss_name, percent) for bosses that could technically spawn on the guild's world.
        Includes bosses with known percentage and bosses without prediction (percent=None). Excludes 'No Chance'.
        Names are canonicalized to BOSSES entries. If no world configured or fetch fails, returns empty list.
        Uses cached data when available; otherwise tries to fetch once.
        """
        # Serve from cache if we have it (and it's recent enough per your scheduler)
        if guild_id in self._spawnables:
            return list(self._spawnables[guild_id])

        world = self.get_world(guild_id)
        if not world:
            return []
        html = await self._source.fetch_world_html(world)
        if not html:
            return []
        raw_list = self._source.parse_spawnables(html)
        days_map_raw = {}
        try:
            days_map_raw = self._source.parse_days_since_last_kill(html) or {}
        except Exception:
            logger.exception("CheckerUpdater: parse_days_since_last_kill failed in get_spawnables")
        out: List[Tuple[str, Optional[int]]] = []
        canon_days: Dict[str, int] = {}
        for n_raw, d in days_map_raw.items():
            cn = self._canonicalize_name(n_raw)
            if cn is not None:
                canon_days[cn] = d
        for name, pct in raw_list:
            canon = self._canonicalize_name(name)
            if not canon:
                continue
            # Enforce never show before min_days
            try:
                meta = BOSSES.get(canon, {})
                min_days, max_days = self._get_unknown_range(meta)
                dval = canon_days.get(canon)
                if dval is not None and min_days is not None and dval < min_days:
                    continue
            except Exception:
                pass
            out.append((canon, pct))
        # Apply range sanity on cache-miss path as well, only if per-boss threshold is set
        current_allowed = {n for (n, _p) in out}
        for canon_name, days in canon_days.items():
            if canon_name in current_allowed:
                continue
            try:
                meta = BOSSES.get(canon_name, {})
                _min_days, max_days = self._get_unknown_range(meta)
            except Exception:
                max_days = None
            if max_days is not None and days >= max_days:
                out.append((canon_name, None))
        # Cache it
        self._spawnables[guild_id] = list(out)
        return out

    # Parsing is delegated to the source implementation
