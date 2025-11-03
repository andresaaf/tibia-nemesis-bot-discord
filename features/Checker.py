from IFeature import IFeature
import discord
from discord import app_commands
import asyncio
import logging
import hashlib
from typing import Dict, List, Set, Tuple, Union, Optional
from datetime import datetime, timezone, time as dtime, timedelta
from .Bosses import BOSSES
from .CheckerUpdater import CheckerUpdater, WORLDS

logger = logging.getLogger(__name__)

AREAS = {
    "Ab'Dendriel | Carlin | Kazordoon": [
        BOSSES['Foreman Kneebiter'],
        BOSSES['Rotworm Queen - Hellgate'],
        BOSSES['The Evil Eye'],
        BOSSES['Yeti'],
        BOSSES['Zarabustor'],
        BOSSES['Zevelon Duskbringer'],
    ],
    "Thais | Venore": [
        BOSSES['Dharalion'],
        BOSSES['General Murius'],
        BOSSES['Grorlam'],
        BOSSES['Rukor Zad'],
        BOSSES['The Frog Prince'],
        BOSSES['Warlord Ruzad'],
        BOSSES['Xenia'],
        BOSSES['Yaga the Crone'],
    ],
    "POI": [
        BOSSES['Albino Dragon - POI'],
        BOSSES['Countess Sorrow'],
        BOSSES['Dracola'],
        BOSSES['Massacre'],
        BOSSES['Mr. Punish'],
        BOSSES['The Handmaiden'],
        BOSSES['The Imperor'],
        BOSSES['The Plasmother'],
    ],
    "Ankrahmun | Darashia": [
        BOSSES['Albino Dragon - Ankrahmun'],
        BOSSES['Arachir the Ancient One'],
        BOSSES['Captain Jones'],
        BOSSES['Gravelord Oshuran'],
        BOSSES['Rotworm Queen - Darashia'],
        BOSSES['Tyrn - Darashia'],
        BOSSES['Tzumrah the Dazzler'],
        BOSSES['White Pale - Darashia'],
    ],
    "Edron": [
        BOSSES['Big Boss Trolliver'],
        BOSSES['Mahatheb'],
        BOSSES['Rotworm Queen - Edron'],
        BOSSES['Shlorg'],
        BOSSES['Sir Valorcrest'],
        BOSSES['The Big Bad One'],
        BOSSES['The Old Whopper'],
        BOSSES['White Pale - Edron'],
    ],
    "Zao": [
        BOSSES['Albino Dragon - Zao'],
        BOSSES['Dreadmaw - West'],
        BOSSES['Dreadmaw - East'],
        BOSSES['Fleabringer - North'],
        BOSSES['Fleabringer - South'],
        BOSSES['Fleabringer - Surface'],
        BOSSES['Hatebreeder'],
        BOSSES['The Voice of Ruin - Ghastly'],
        BOSSES['The Voice of Ruin - Middle'],
        BOSSES['Flamecaller Zazrak - Dojo'],
        BOSSES['Flamecaller Zazrak - Mountain'],
        BOSSES['Battlemaster Zunzu - West'],
        BOSSES['Battlemaster Zunzu - Middle'],
        BOSSES['Battlemaster Zunzu - East'],
    ],
    "Liberty Bay": [
        BOSSES['Albino Dragon - Liberty Bay'],
        BOSSES['Crustacea Gigantica'],
        BOSSES['Diblis the Fair'],
        BOSSES['Grandfather Tridian'],
        BOSSES['Rotworm Queen - Liberty Bay'],
        BOSSES['Tyrn - Liberty Bay'],
        BOSSES['Undead Cavebear'],
        BOSSES['White Pale - Liberty Bay'],
    ],
    "Port Hope": [
        BOSSES['Arthom the Hunter'],
        BOSSES['Hairman the Huge'],
        BOSSES['High Templar Cobrass'],
        BOSSES['Midnight Panther'],
        BOSSES['Smuggler Baron Silvertoe'],
        BOSSES['The Welter'],
    ],
    "Svargrond": [
        BOSSES['Barbaria'],
        BOSSES['Dire Penguin'],
        BOSSES['Hirintror - Nibelor'],
        BOSSES['Hirintror - Mines'],
        BOSSES['Man in the Cave'],
        BOSSES['Ocyakao'],
        BOSSES['Yakchal'],
        BOSSES['Zushuka'],
    ],
    "Others": [
        BOSSES['Bank Robbers (Board)'],
        BOSSES['Mawhawk'],
        BOSSES['Furyosa'],
        BOSSES['Omrafir'],
    ],
}

MAX_HISTORY = 15 # 25
MAX_CUSTOM_ID_LENGTH = 100  # Discord custom_id max length

def _now_unix() -> int:
    return int(datetime.now(timezone.utc).timestamp())

class Checker(IFeature):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cmd_registered = False
        self._ready = False
        self._locks: Dict[int, asyncio.Lock] = {}
        self._first_msg_id: Optional[int] = None
        # message_id -> { f"{area}|{boss_key}": last_clicked_unix_ts }
        self._active: Dict[int, Dict[str, int]] = {}
        # message_id -> area name
        self._message_area: Dict[int, str] = {}
        self._history: List[Tuple[Union[int, str], str, str, str]] = []
        self._history_lock: asyncio.Lock = asyncio.Lock()  # reduce contention for history updates/reads
        self._task: Optional[asyncio.Task] = None
        self._tick_task: Optional[asyncio.Task] = None
        self._checker_channel_id: Optional[int] = None
        # area name -> message id for reusing posts
        self._area_msg_ids: Dict[str, int] = {}
        # raids summary message id
        self._raids_msg_id: Optional[int] = None
        # Updater interface to determine which bosses can spawn today
        self._updater = CheckerUpdater(self.client)
        # Default button timing thresholds (in seconds)
        self._default_warn_sec = 15 * 60
        self._default_alert_sec = 30 * 60
        self._default_reset_sec = 60 * 60
        # Cached percent map per guild to avoid frequent provider calls during rapid clicks
        # guild_id -> (timestamp_unix, percent_map)
        self._percent_map_cache: Dict[int, Tuple[int, Dict[str, Optional[int]]]] = {}
        # Debounced update tasks per message and for the history embed
        self._debounce_tasks: Dict[int, asyncio.Task] = {}
        self._embed_update_task: Optional[asyncio.Task] = None
        # Debounce configuration (dynamic backoff)
        self._debounce_base_delay_sec: float = 0.75
        self._debounce_max_delay_sec: float = 20.0
        self._debounce_backoff_sec: float = self._debounce_base_delay_sec
        self._embed_debounce_base_delay_sec: float = 5.0
        self._embed_debounce_max_delay_sec: float = 20.0
        self._embed_debounce_backoff_sec: float = self._embed_debounce_base_delay_sec
        # Cache of message objects to avoid refetching
        self._messages: Dict[int, discord.Message] = {}
        # Last rendered style state per area message: msg_id -> { boss_key: (style_value, emoji) }
        self._last_style_state: Dict[int, Dict[str, Tuple[int, str]]] = {}
        # Stable short IDs for areas and boss keys to keep custom_id short
        self._area_id_map: Dict[str, str] = {name: hashlib.sha1(name.encode("utf-8")).hexdigest()[:8] for name in AREAS.keys()}
        self._area_id_rev: Dict[str, str] = {v: k for k, v in self._area_id_map.items()}
        self._boss_id_map: Dict[str, str] = {}
        self._boss_id_rev: Dict[str, str] = {}
        # Fury Gate city per guild (Carlin or Thais)
        self._furygate_city = {}

    async def on_ready(self):
        if self._ready:
            return
        self._ready = True

        if not self._cmd_registered:
            @app_commands.command(name="checker", description="Register which text channel the checker should use")
            @app_commands.describe(channel="Text channel to use for checker messages")
            async def checker(interaction: discord.Interaction, channel: discord.TextChannel):
                if interaction.guild is None:
                    await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
                    return
                member = interaction.user if isinstance(interaction.user, discord.Member) else interaction.guild.get_member(interaction.user.id)
                if member is None or not (member.guild_permissions.manage_guild or member.guild_permissions.manage_channels or member.guild_permissions.administrator):
                    await interaction.response.send_message("You need Manage Server/Channels or Administrator permission to set the checker channel.", ephemeral=True)
                    return
                try:
                    with self.client.db as db:
                        db.execute("CREATE TABLE IF NOT EXISTS checker_config (id INTEGER PRIMARY KEY, channel_id INTEGER)")
                        db.execute("INSERT OR REPLACE INTO checker_config (id, channel_id) VALUES (1, ?)", (channel.id,))

                    # apply immediately
                    await self._ensure_channel_messages_and_update()

                    if not self._task or self._task.done():
                        self._task = asyncio.create_task(self._background_loop())

                    await interaction.response.send_message(f"Checker channel set to {channel.mention}", ephemeral=True)
                except Exception:
                    logger.exception("Checker: failed to store channel in database")
                    try:
                        await interaction.response.send_message("Failed to register channel.", ephemeral=True)
                    except Exception:
                        pass

            try:
                self.client.tree.add_command(checker)
                # Add world registration command
                @app_commands.command(name="checkerworld", description="Set the Tibia game world for this server")
                @app_commands.choices(world=[app_commands.Choice(name=w, value=w) for w in WORLDS])
                @app_commands.default_permissions(manage_guild=True)
                @app_commands.checks.has_permissions(manage_guild=True)
                async def checkerworld(interaction: discord.Interaction, world: app_commands.Choice[str]):
                    if interaction.guild is None:
                        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
                        return
                    try:
                        await self._updater.set_world(interaction.guild.id, world.value)
                        # refresh cache for this guild and re-render if channel configured
                        await self._updater.update_cache_for_guild(interaction.guild.id)
                        await self._ensure_channel_messages_and_update()
                        await interaction.response.send_message(f"World set to {world.value}.", ephemeral=True)
                    except ValueError:
                        await interaction.response.send_message("Invalid world.", ephemeral=True)
                    except Exception:
                        logger.exception("Checker: failed to set world")
                        try:
                            await interaction.response.send_message("Failed to set world.", ephemeral=True)
                        except Exception:
                            pass

                self.client.tree.add_command(checkerworld)

                # Add manual refresh command for testing
                @app_commands.command(name="checkerrefresh", description="Force-refresh the checker cache for this server")
                @app_commands.default_permissions(manage_guild=True)
                @app_commands.checks.has_permissions(manage_guild=True)
                async def checkerrefresh(interaction: discord.Interaction):
                    if interaction.guild is None:
                        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
                        return
                    try:
                        guild_id = interaction.guild.id
                        # Refresh cache and re-render messages
                        await self._updater.update_cache_for_guild(guild_id)
                        await self._ensure_channel_messages_and_update()
                        allowed = self._updater.get_allowed_boss_names(guild_id)
                        world = self._updater.get_world(guild_id) or "(no world set)"
                        await interaction.response.send_message(
                            f"Refreshed checker cache for world {world}. Possible spawns: {len(allowed)}.",
                            ephemeral=True,
                        )
                    except Exception:
                        logger.exception("Checker: manual refresh failed")
                        try:
                            await interaction.response.send_message("Failed to refresh checker cache.", ephemeral=True)
                        except Exception:
                            pass

                self.client.tree.add_command(checkerrefresh)

                # Command to set Fury Gate city (Carlin or Thais). Affects Furyosa button label.
                @app_commands.command(name="furygate", description="Set the Fury Gate city for this server")
                @app_commands.choices(city=[
                    app_commands.Choice(name="Carlin", value="Carlin"),
                    app_commands.Choice(name="Ab'Dendriel", value="Ab'Dendriel"),
                    app_commands.Choice(name="Kazordoon", value="Kazordoon"),
                    app_commands.Choice(name="Thais", value="Thais"),
                    app_commands.Choice(name="Venore", value="Venore"),
                    app_commands.Choice(name="Edron", value="Edron"),
                    app_commands.Choice(name="Darashia", value="Darashia"),
                    app_commands.Choice(name="Ankrahmun", value="Ankrahmun"),
                    app_commands.Choice(name="Port Hope", value="Port Hope"),
                    app_commands.Choice(name="Liberty Bay", value="Liberty Bay"),
                ])
                async def furygate(interaction: discord.Interaction, city: app_commands.Choice[str]):
                    if interaction.guild is None:
                        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
                        return
                    try:
                        guild_id = interaction.guild.id
                        self._set_furygate_city(guild_id, city.value)
                        # Re-render checker messages to update the Furyosa label
                        await self._ensure_channel_messages_and_update()
                        user_name = getattr(interaction.user, "display_name", None) or getattr(interaction.user, "name", "User")
                        await interaction.response.send_message(f"{user_name} changed Fury Gate to {city.value}")
                    except Exception:
                        logger.exception("Checker: failed to set Fury Gate city")
                        try:
                            await interaction.response.send_message("Failed to set Fury Gate city.", ephemeral=True)
                        except Exception:
                            pass

                self.client.tree.add_command(furygate)
            except Exception:
                logger.exception("Failed to register checker command")
            self._cmd_registered = True

        # Start background updater only if a channel is configured.
        channel_id = None
        try:
            with self.client.db as db:
                db.execute("CREATE TABLE IF NOT EXISTS checker_config (id INTEGER PRIMARY KEY, channel_id INTEGER)")
                db.execute("SELECT channel_id FROM checker_config WHERE id=1")
                row = db.fetchone()
                if row and row[0]:
                    channel_id = row[0]
        except Exception:
            logger.exception("Checker: failed to read configured channel from db during startup")

        if channel_id:
            self._task = asyncio.create_task(self._background_loop())
            self._checker_channel_id = channel_id
            if not self._tick_task or self._tick_task.done():
                self._tick_task = asyncio.create_task(self._tick_loop())
        else:
            logger.info("Checker: no channel configured yet — waiting for /checker before activating updates")

    async def _background_loop(self):
        try:
            await self._ensure_channel_messages_and_update()
        except Exception:
            logger.exception("Checker initial update failed")

        # schedule daily 10:00 updates (API updates at 09:00, bot updates at 10:00)
        while True:
            now = datetime.now()
            target = datetime.combine(now.date(), dtime(hour=10, minute=0, second=0))
            if target <= now:
                target = target + timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            try:
                await asyncio.sleep(wait_seconds)
                await self._ensure_channel_messages_and_update()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Checker scheduled update failed")

    async def _ensure_channel_messages_and_update(self):
        # read configured channel id from DB
        channel_id = None
        try:
            with self.client.db as db:
                db.execute("CREATE TABLE IF NOT EXISTS checker_config (id INTEGER PRIMARY KEY, channel_id INTEGER)")
                db.execute("SELECT channel_id FROM checker_config WHERE id=1")
                row = db.fetchone()
                if row and row[0]:
                    channel_id = row[0]
        except Exception:
            logger.exception("Checker: failed to read configured channel from db")

        if not channel_id:
            logger.info("Checker: no channel configured, skipping ensure/update")
            return

        channel = self.client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.client.fetch_channel(channel_id)
            except Exception:
                logger.exception("Checker: failed to get channel %s", channel_id)
                return

        guild_id = getattr(channel, 'guild', None).id if getattr(channel, 'guild', None) else 0

        # Reset active state for bosses marked serversave=True (daily reset behavior) on any refresh/ensure
        try:
            self._prune_serversave_active()
        except Exception:
            logger.exception("Checker: failed to prune serversave active states")
        # Try to load persisted message IDs from DB to avoid scanning
        id_map = self._db_load_message_ids(guild_id)

        first_msg: Optional[discord.Message] = None
        existing_area_msgs: Dict[str, discord.Message] = {}
        existing_raids_msg: Optional[discord.Message] = None

        # Attempt to fetch messages by ID where present
        try:
            if 'FIRST' in id_map:
                try:
                    first_msg = await channel.fetch_message(id_map['FIRST'])
                    self._messages[first_msg.id] = first_msg
                except Exception:
                    first_msg = None
            for area_name in AREAS.keys():
                mid = id_map.get(area_name)
                if mid:
                    try:
                        m = await channel.fetch_message(mid)
                        existing_area_msgs[area_name] = m
                        self._messages[m.id] = m
                        self._message_area[m.id] = area_name
                        self._area_msg_ids[area_name] = m.id
                    except Exception:
                        pass
            if 'RAIDS' in id_map:
                try:
                    existing_raids_msg = await channel.fetch_message(id_map['RAIDS'])
                    self._messages[existing_raids_msg.id] = existing_raids_msg
                except Exception:
                    existing_raids_msg = None
        except Exception:
            logger.exception("Checker: error fetching messages by id")

        # Fallback to history scan if we couldn't find existing messages
        if first_msg is None or not existing_area_msgs:
            bot_msgs: List[discord.Message] = []
            try:
                async for msg in channel.history(limit=500):
                    if msg.author and msg.author.id == self.client.user.id:
                        bot_msgs.append(msg)
            except Exception:
                logger.exception("Checker: failed to scan channel history")

            # Find or create first message (embed titled "Boss Checks")
            for m in bot_msgs:
                emb = (m.embeds[0] if m.embeds else None)
                if emb and getattr(emb, "title", None) == "Boss Checks":
                    first_msg = m
                    self._db_save_message_id(guild_id, 'FIRST', m.id)
                    break

            # Map existing area and raids messages
            try:
                area_titles = set(AREAS.keys())
                for m in bot_msgs:
                    if first_msg and m.id == first_msg.id:
                        continue
                    content = (m.content or "").strip()
                    if content.startswith("**") and content.endswith("**"):
                        title = content.strip("*").strip()
                        if title in area_titles and title not in existing_area_msgs:
                            existing_area_msgs[title] = m
                            self._messages[m.id] = m
                            self._message_area[m.id] = title
                            self._area_msg_ids[title] = m.id
                            self._db_save_message_id(guild_id, title, m.id)
                            continue
                    if content.startswith("**Possible Raids**") and existing_raids_msg is None:
                        existing_raids_msg = m
                        self._messages[m.id] = m
                        self._db_save_message_id(guild_id, 'RAIDS', m.id)
            except Exception:
                logger.exception("Checker: failed to map existing area/raids messages")

        # Ensure first message exists
        try:
            if first_msg is None:
                sent = await channel.send(embed=self._build_first_embed())
                first_msg = sent
                self._db_save_message_id(guild_id, 'FIRST', sent.id)
            self._messages[first_msg.id] = first_msg
        except Exception:
            logger.exception("Checker: failed to create first message")
            return

        self._first_msg_id = first_msg.id

        # Start with empty active maps (no persistent button state)
        area_active_map: Dict[str, Dict[str, int]] = {area: {} for area in AREAS.keys()}

        # Ensure cache is up to date before rendering area messages (per guild)
        try:
            await self._updater.update_cache_for_guild(channel.guild.id)
        except Exception:
            logger.exception("Checker: updater.update_cache_for_guild failed; proceeding with previous cache")

        # Build spawnables percent map for labeling buttons
        percent_map: Dict[str, Optional[int]] = {}
        try:
            spawnables = await self._updater.get_spawnables_with_percentages(channel.guild.id)
            for name, pct in spawnables:
                percent_map[name] = pct
            # update cache for this guild
            self._percent_map_cache[channel.guild.id] = (_now_unix(), dict(percent_map))
        except Exception:
            logger.exception("Checker: failed to get spawnables; defaulting to empty map")

        # Update or create area messages (bold area name + buttons filtered by today's allowed bosses). Track active sets by message id.
        allowed: Set[str] = set(percent_map.keys())
        for area, bosses in AREAS.items():
            # Skip areas that have no configured bosses
            if not bosses:
                continue
            active = area_active_map.get(area, {})
            content = self._build_area_content(area, bosses, active)
            view = self._view_for_area(area, bosses, active, channel.guild.id, percent_map, allowed)
            existing = existing_area_msgs.get(area)
            if existing is not None:
                # Edit in place
                try:
                    await existing.edit(content=content, view=view)
                    self._active[existing.id] = dict(self._active.get(existing.id, {})) or {}
                    self._message_area[existing.id] = area
                    self._area_msg_ids[area] = existing.id
                    self._db_save_message_id(guild_id, area, existing.id)
                    # cache and record last style state
                    self._messages[existing.id] = existing
                    try:
                        style_state = self._compute_area_style_state(area, bosses, self._active.get(existing.id, {}), channel.guild.id, percent_map, allowed)
                        self._last_style_state[existing.id] = style_state
                    except Exception:
                        pass
                except Exception:
                    logger.exception("Checker: failed to edit area message for %s", area)
            else:
                try:
                    sent = await channel.send(content, view=view)
                    self._active[sent.id] = dict(active)
                    self._message_area[sent.id] = area
                    self._area_msg_ids[area] = sent.id
                    self._db_save_message_id(guild_id, area, sent.id)
                    # cache and record last style state
                    self._messages[sent.id] = sent
                    try:
                        style_state = self._compute_area_style_state(area, bosses, self._active.get(sent.id, {}), channel.guild.id, percent_map, allowed)
                        self._last_style_state[sent.id] = style_state
                    except Exception:
                        pass
                except Exception:
                    logger.exception("Checker: failed to send area message for %s", area)

        # Post a final message listing possible raids (no buttons)
        try:
            raids_text = await self._build_possible_raids_content(percent_map)
            if existing_raids_msg is not None:
                try:
                    await existing_raids_msg.edit(content=raids_text)
                    self._raids_msg_id = existing_raids_msg.id
                    self._messages[existing_raids_msg.id] = existing_raids_msg
                    self._db_save_message_id(guild_id, 'RAIDS', existing_raids_msg.id)
                except Exception:
                    logger.exception("Checker: failed to edit Possible Raids message")
            else:
                sent = await channel.send(raids_text)
                self._raids_msg_id = sent.id
                self._messages[sent.id] = sent
                self._db_save_message_id(guild_id, 'RAIDS', sent.id)
        except Exception:
            logger.exception("Checker: failed to send/edit Possible Raids message")

        # Update the first message embed to reflect current history
        try:
            first_msg = self._messages.get(self._first_msg_id)
            if first_msg is None:
                first_msg = await channel.fetch_message(self._first_msg_id)
                self._messages[self._first_msg_id] = first_msg
            await first_msg.edit(embed=self._build_first_embed())
        except Exception:
            logger.exception("Checker: failed to update first message %s", self._first_msg_id)

    def _prune_serversave_active(self) -> None:
        """Clear active markers for bosses that should reset on server save (09:00) or on manual refresh.
        Uses BOSSES[boss_key]['serversave'] == True.
        """
        if not self._active:
            return
        for msg_id, active_map in list(self._active.items()):
            changed = False
            for k in list(active_map.keys()):
                try:
                    _area, boss_key = k.split("|", 1)
                except Exception:
                    continue
                try:
                    meta = BOSSES.get(boss_key)
                    if isinstance(meta, dict) and bool(meta.get('serversave', False)):
                        del active_map[k]
                        changed = True
                except Exception:
                    continue
            if changed:
                self._active[msg_id] = active_map

    def _parse_history_from_embed(self, embed: discord.Embed) -> List[Tuple[Union[int, str], str, str, str]]:
        hist: List[Tuple[Union[int, str], str, str, str]] = []
        desc = embed.description or ""
        for line in desc.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(" - ", 3)
            if len(parts) == 4:
                ts_text, user, area, boss = parts
                try:
                    dt = datetime.strptime(ts_text, "%Y-%m-%d %H:%M:%S %Z")
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    ts = int(dt.timestamp())
                    hist.append((ts, user, area, boss))
                except Exception:
                    hist.append((ts_text, user, area, boss))
        return hist[:MAX_HISTORY]

    def _build_first_embed(self) -> discord.Embed:
        emb = discord.Embed(title="Boss Checks", description="", color=0x0066CC)
        legend_text = (
            "Click the button after checking a boss.\n\n"
            "Boss Check Legend:\n"
            ":white_check_mark: = up to 15 minutes (Zarabustor 7 minutes | POI 45 minutes)\n"
            ":grey_exclamation: = at least 15 minutes ago (Zarabustor 7 minutes | POI 45 minutes)\n"
            ":exclamation: = at least 30 minutes ago (Zarabustor 15 minutes | POI 90 minutes)\n"
            ":alarm_clock: = over 60 minutes ago (Zarabustor 30 minutes | POI 180 minutes)"
        )
        # Fast path when no history
        if not self._history:
            emb.description = f"{legend_text}\n\nNo checks yet."
            emb.set_footer(text=f"Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}")
            return emb
        # Non-blocking snapshot; writes are rare and protected by a lock
        emb.description = legend_text
        # Build a deduplicated list by boss key: keep only the latest event per boss
        try:
            hist_snapshot = list(self._history)
        except Exception:
            hist_snapshot = []
        seen_boss: Set[str] = set()
        uniques_newest_first: List[Tuple[Union[int, str], str, str, str]] = []
        for item in hist_snapshot:  # _history is newest-first
            try:
                _ts, _user, _area, _boss = item
            except Exception:
                continue
            if _boss in seen_boss:
                continue
            seen_boss.add(_boss)
            uniques_newest_first.append(item)
            if len(uniques_newest_first) >= MAX_HISTORY:
                break

        # Display oldest -> newest so the latest entry (per boss) is at the bottom
        rows = list(reversed(uniques_newest_first))

        boss_col: List[str] = []
        player_col: List[str] = []
        time_col: List[str] = []
        for ts_or_str, user, area, boss in rows:
            boss_col.append(boss)
            player_col.append(user)
            if isinstance(ts_or_str, int):
                time_col.append(f"<t:{ts_or_str}:R>")
            else:
                time_col.append(ts_or_str)

        def col_text(col: List[str]) -> str:
            return "\n".join(col) if col else "—"

        emb.add_field(name="BOSS", value=col_text(boss_col), inline=True)
        emb.add_field(name="PLAYER", value=col_text(player_col), inline=True)
        emb.add_field(name="TIME", value=col_text(time_col), inline=True)

        emb.set_footer(text=f"Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}")
        return emb

    def _build_area_content(self, area: str, bosses: List[str], active: Dict[str, int]) -> str:
        return f"**{area}**"

    async def _build_possible_raids_content(self, percent_map: Dict[str, Optional[int]]) -> str:
        # Collect bosses with raid=True and a numeric percentage available
        seen: Set[str] = set()
        entries: List[Tuple[int, str]] = []  # (pct, formatted_text)
        for key, data in BOSSES.items():
            try:
                if not isinstance(data, dict) or not data.get('raid', False):
                    continue
                base_name = str(data.get('role') or data.get('name') or key)
                if base_name in seen:
                    continue
                pct = percent_map.get(base_name)
                if isinstance(pct, int):
                    em_name = data.get('emoji')
                    em_obj = None
                    if em_name:
                        try:
                            em_obj = await self.client.get_app_emoji(em_name)
                        except Exception:
                            em_obj = None
                    em_text = f"{em_obj} " if em_obj else ""
                    # Only show the percentage when above 2%
                    text = f"{em_text}{base_name} [**{pct}%**]" if pct > 2 else f"{em_text}{base_name}"
                    entries.append((pct, text))
                    seen.add(base_name)
            except Exception:
                continue
        if not entries:
            return "**Possible Raids**\n"
        # Sort by percentage descending
        entries.sort(key=lambda x: x[0], reverse=True)
        parts = [text for _pct, text in entries]
        return "**Possible Raids**\n" + ", ".join(parts)

    def _boss_to_name(self, entry: Union[str, Dict, object]) -> str:
        try:
            if isinstance(entry, str):
                return entry
            if isinstance(entry, dict):
                # Prefer a shorter checker_name when provided; fallback to full name
                if entry.get('checker_name'):
                    return str(entry.get('checker_name'))
                if 'name' in entry:
                    return str(entry['name'])
        except Exception:
            pass
        return str(entry)

    def _boss_to_key(self, entry: Union[str, Dict, object]) -> str:
        """Return the BOSSES dict key for this entry when possible.
        - If entry is a string (already a key), return it.
        - If entry is a dict from BOSSES[key], find and return that key.
        - Fallback to display name if not found.
        """
        try:
            if isinstance(entry, str):
                return entry
            if isinstance(entry, dict):
                # Identity-based reverse lookup to avoid ambiguity on duplicate names
                for k, v in BOSSES.items():
                    if v is entry:
                        return k
                # Fallback: try matching by exact dict content (less reliable)
                for k, v in BOSSES.items():
                    if v == entry:
                        return k
                return str(entry.get('name') or entry)
        except Exception:
            pass
        return self._boss_to_name(entry)


    def _view_for_area(self, area: str, bosses: List[str], active: Dict[str, int], guild_id: int, percent_map: Dict[str, Optional[int]], allowed: Optional[Set[str]] = None) -> discord.ui.View:
        view = discord.ui.View(timeout=None)
        # Allowed based on spawnables map (bosses that could spawn). Value is percent or None.
        allowed = set(percent_map.keys()) if allowed is None else allowed
        now_ts = _now_unix()

        for b in bosses:
            # Determine if this is a persistent boss (always shown, no percent label)
            persistent = isinstance(b, dict) and bool(b.get('persist', False))
            name = self._boss_to_name(b)  # display name (may include location qualifiers)
            boss_key = self._boss_to_key(b)  # BOSSES mapping key for identification/history
            warn_s, alert_s, reset_s = self._thresholds_for_entry(b)
            # Base boss name used for matching against site data: prefer role if present, else name
            if isinstance(b, dict):
                base_name = str(b.get('role') or b.get('name') or name)
            else:
                base_name = name
            # If not persistent and not allowed by base boss name, skip
            if not persistent and base_name not in allowed:
                continue
            key = self._make_active_key(area, boss_key)
            last_ts = active.get(key)
            # Per-boss prune on reset
            if last_ts and (now_ts - last_ts) >= reset_s:
                try:
                    del active[key]
                except Exception:
                    pass
                last_ts = None
            style, emoji = self._style_and_emoji_for_ts(last_ts, now_ts, warn_s, alert_s, reset_s)
            cid = self._make_custom_id(area, boss_key)
            # Look up percentage by base boss name
            pct = percent_map.get(base_name)
            # Adjust display name for Furyosa: append current Fury Gate city
            label_name = name
            try:
                if boss_key == 'Furyosa':
                    city = self._get_furygate_city(guild_id)
                    if city:
                        label_name = f"{name} ({city})"
            except Exception:
                pass
            # Persistent bosses never show a percentage label; only show % when >2
            base_label = label_name if persistent else (f"{label_name} [{pct}%]" if (isinstance(pct, int) and pct > 2) else label_name)
            label_text = f"{emoji} {base_label}" if emoji else base_label
            view.add_item(discord.ui.Button(style=style, label=label_text, custom_id=cid))
        return view

    def _style_and_emoji_for_ts(self, ts: Optional[int], now_ts: Optional[int] = None, warn_s: Optional[int] = None, alert_s: Optional[int] = None, reset_s: Optional[int] = None) -> Tuple[discord.ButtonStyle, str]:
        if now_ts is None:
            now_ts = _now_unix()
        if not ts or ts <= 0:
            # Inactive
            return discord.ButtonStyle.danger, ":alarm_clock:"
        delta = now_ts - ts
        ws = warn_s or self._default_warn_sec
        as_ = alert_s or self._default_alert_sec
        rs = reset_s or self._default_reset_sec
        if delta < ws:
            return discord.ButtonStyle.success, ":white_check_mark:"
        if delta < as_:
            return discord.ButtonStyle.success, ":grey_exclamation:"
        if delta < rs:
            # No true orange style; use primary as closest alternative
            return discord.ButtonStyle.primary, ":exclamation:"
        # Expired -> inactive
        return discord.ButtonStyle.danger, ":alarm_clock:"

    def _thresholds_for_entry(self, entry: Union[str, Dict, object]) -> Tuple[int, int, int]:
        try:
            config = None
            if isinstance(entry, dict):
                config = entry.get('check_thresholds')
            if isinstance(config, dict):
                # Values in BOSSES are specified in minutes; convert to seconds when provided
                ws = int(config['warn']) * 60 if 'warn' in config else self._default_warn_sec
                als = int(config['alert']) * 60 if 'alert' in config else self._default_alert_sec
                rs = int(config['reset']) * 60 if 'reset' in config else self._default_reset_sec
                return ws, als, rs
        except Exception:
            pass
        return self._default_warn_sec, self._default_alert_sec, self._default_reset_sec

    def _thresholds_for_boss_key(self, boss_key: str) -> Tuple[int, int, int]:
        try:
            entry = BOSSES.get(boss_key)
            if isinstance(entry, dict):
                return self._thresholds_for_entry(entry)
        except Exception:
            pass
        return self._default_warn_sec, self._default_alert_sec, self._default_reset_sec

    async def on_button_click(self, interaction: discord.Interaction):
        data = getattr(interaction, "data", {}) or {}
        custom_id = data.get("custom_id") or getattr(interaction, "custom_id", None)
        if not custom_id or not custom_id.startswith("checker:"):
            return

        parsed = self._parse_custom_id(custom_id)
        if not parsed:
            return
        area, boss = parsed
        msg = getattr(interaction, "message", None)
        if msg is None:
            return

        # Acknowledge the interaction quickly to avoid client-side failure; we'll edit the message shortly (debounced)
        try:
            if not getattr(interaction.response, "is_done", lambda: False)():
                await interaction.response.defer()
        except Exception:
            pass

        # Per-message lock while mutating shared state
        lock = self._locks.get(msg.id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[msg.id] = lock

        async with lock:
            active = self._active.get(msg.id)
            if active is None or not isinstance(active, dict):
                active = {}
            key = self._make_active_key(area, boss)
            # Always record/refresh the check timestamp; do not toggle off
            active[key] = _now_unix()
            self._active[msg.id] = active

        # Schedule a debounced edit of the area message to reduce rate-limit hits
        try:
            guild_id = interaction.guild.id if interaction.guild else 0
            self._schedule_area_message_update(msg.channel, msg.id, area, guild_id)
        except Exception:
            logger.exception("Checker: failed to schedule debounced area update for %s", msg.id)

        # record check in history (newest first) using BOSSES key as before
        user_display = getattr(interaction.user, "display_name", None) or getattr(interaction.user, "name", str(interaction.user))
        ts = _now_unix()
        async with self._history_lock:
            self._history.insert(0, (ts, user_display, area, boss))
            if len(self._history) > MAX_HISTORY:
                self._history = self._history[:MAX_HISTORY]

        # Debounce the history embed update as well
        try:
            self._schedule_embed_update(msg.channel)
        except Exception:
            logger.exception("Checker: failed to schedule debounced history embed update")

    async def close(self):
        if self._task and not self._task.cancelled():
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
        if self._tick_task and not self._tick_task.cancelled():
            self._tick_task.cancel()
            try:
                await self._tick_task
            except Exception:
                pass
        # Cancel any pending debounced updates
        try:
            for t in list(self._debounce_tasks.values()):
                if t and not t.cancelled():
                    t.cancel()
                    try:
                        await t
                    except Exception:
                        pass
            self._debounce_tasks.clear()
        except Exception:
            pass
        if self._embed_update_task and not self._embed_update_task.cancelled():
            self._embed_update_task.cancel()
            try:
                await self._embed_update_task
            except Exception:
                pass
            self._embed_update_task = None

    async def _tick_loop(self):
        """Periodic updater to advance button states (15/30/60 min) and expire them after 60 minutes."""
        while True:
            try:
                await asyncio.sleep(60)
                # If no channel or no messages, skip
                if not self._checker_channel_id or not self._active:
                    continue
                channel = self.client.get_channel(self._checker_channel_id)
                if channel is None:
                    try:
                        channel = await self.client.fetch_channel(self._checker_channel_id)
                    except Exception:
                        continue
                # Do NOT refresh spawnable percentages every minute; use cached map
                guild_id = getattr(channel, 'guild', None).id if getattr(channel, 'guild', None) else 0
                pmap: Dict[str, Optional[int]] = self._get_cached_percent_map(guild_id) or {}
                allowed = set(pmap.keys())

                now_ts = _now_unix()
                for msg_id, active_map in list(self._active.items()):
                    # prune expired per-boss based on reset threshold
                    for k, ts in list(active_map.items()):
                        try:
                            # k format: "Area|BossKey"
                            _, boss_key = k.split("|", 1)
                        except Exception:
                            boss_key = None
                        if not ts:
                            continue
                        reset_s = self._thresholds_for_boss_key(boss_key)[2] if boss_key else self._default_reset_sec
                        if (now_ts - ts) >= reset_s:
                            try:
                                del active_map[k]
                            except Exception:
                                pass
                    area = self._message_area.get(msg_id)
                    if not area:
                        continue
                    bosses = AREAS.get(area, [])
                    lock = self._locks.get(msg_id)
                    if lock is None:
                        lock = asyncio.Lock()
                        self._locks[msg_id] = lock
                    # Compute current style state and compare with last
                    try:
                        new_state = self._compute_area_style_state(area, bosses, active_map, guild_id, pmap, allowed)
                        old_state = self._last_style_state.get(msg_id)
                        if old_state is not None and old_state == new_state:
                            continue  # No visual change needed
                    except Exception:
                        new_state = None

                    async with lock:
                        try:
                            # Avoid refetching: use cached message object when available
                            msg_obj = self._messages.get(msg_id)
                            if msg_obj is None:
                                msg_obj = await channel.fetch_message(msg_id)
                                self._messages[msg_id] = msg_obj
                            content = self._build_area_content(area, bosses, active_map)
                            view = self._view_for_area(area, bosses, active_map, guild_id, pmap, allowed)
                            await msg_obj.edit(content=content, view=view)
                            if new_state is not None:
                                self._last_style_state[msg_id] = new_state
                        except Exception:
                            # message might have been deleted; clean up
                            self._active.pop(msg_id, None)
                            self._message_area.pop(msg_id, None)
                            self._locks.pop(msg_id, None)
                            self._messages.pop(msg_id, None)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Checker: tick loop error")

    def _get_cached_percent_map(self, guild_id: int, ttl_sec: int = 60) -> Optional[Dict[str, Optional[int]]]:
        """Return the last known percent map for a guild.
        Note: spawnable percentages only change daily at 09:00, so we intentionally
        ignore TTL here and return the most recent cached map when available.
        """
        try:
            ts_map = self._percent_map_cache.get(guild_id)
            if not ts_map:
                return None
            _ts, pmap = ts_map
            return dict(pmap)
        except Exception:
            return None

    def _compute_area_style_state(self, area: str, bosses: List[str], active: Dict[str, int], guild_id: int, percent_map: Dict[str, Optional[int]], allowed: Optional[Set[str]] = None) -> Dict[str, Tuple[int, str]]:
        """Compute the style state (style.value, emoji) for all buttons that would be rendered for an area."""
        state: Dict[str, Tuple[int, str]] = {}
        allowed = set(percent_map.keys()) if allowed is None else allowed
        now_ts = _now_unix()
        for b in bosses:
            persistent = isinstance(b, dict) and bool(b.get('persist', False))
            name = self._boss_to_name(b)
            boss_key = self._boss_to_key(b)
            warn_s, alert_s, reset_s = self._thresholds_for_entry(b)
            if isinstance(b, dict):
                base_name = str(b.get('role') or b.get('name') or name)
            else:
                base_name = name
            if not persistent and base_name not in allowed:
                continue
            key = self._make_active_key(area, boss_key)
            last_ts = active.get(key)
            if last_ts and (now_ts - last_ts) >= reset_s:
                last_ts = None
            style, emoji = self._style_and_emoji_for_ts(last_ts, now_ts, warn_s, alert_s, reset_s)
            try:
                style_val = int(getattr(style, 'value', int(style)))  # discord.ButtonStyle has .value
            except Exception:
                style_val = 0
            state[boss_key] = (style_val, emoji)
        return state

    def _schedule_area_message_update(self, channel: discord.abc.Messageable, msg_id: int, area: str, guild_id: int, delay_sec: Optional[float] = None):
        # Only one debounce task per message id
        existing = self._debounce_tasks.get(msg_id)
        if existing and not existing.done():
            return

        async def _runner():
            try:
                use_delay = delay_sec if delay_sec is not None else float(self._debounce_backoff_sec)
                await asyncio.sleep(use_delay)
                # Double-check we still have state for this message
                active_map = self._active.get(msg_id, {})
                bosses = AREAS.get(area, [])
                # Use cached percent map if available; avoid network calls on rapid clicks
                pmap = self._get_cached_percent_map(guild_id) or {}
                allowed = set(pmap.keys())
                # Build prospective style state to decide if an edit is necessary
                try:
                    new_state = self._compute_area_style_state(area, bosses, active_map, guild_id, pmap, allowed)
                    old_state = self._last_style_state.get(msg_id)
                    if old_state is not None and old_state == new_state:
                        return  # Skip edit; no visual change
                except Exception:
                    new_state = None
                content = self._build_area_content(area, bosses, active_map)
                view = self._view_for_area(area, bosses, active_map, guild_id, pmap, allowed)
                # Acquire the same lock to avoid racing with tick loop
                lock = self._locks.get(msg_id)
                if lock is None:
                    lock = asyncio.Lock()
                    self._locks[msg_id] = lock
                async with lock:
                    try:
                        # Avoid refetching when possible
                        msg = self._messages.get(msg_id)
                        if msg is None:
                            msg = await channel.fetch_message(msg_id)
                            self._messages[msg_id] = msg
                        await msg.edit(content=content, view=view)
                        # Successful edit: gently reduce backoff toward base
                        try:
                            self._debounce_backoff_sec = max(
                                self._debounce_base_delay_sec,
                                self._debounce_backoff_sec * 0.8,
                            )
                        except Exception:
                            pass
                        if new_state is not None:
                            self._last_style_state[msg_id] = new_state
                    except Exception as e:
                        # If we're rate-limited, increase backoff (exponential up to max)
                        try:
                            status = getattr(e, 'status', None)
                            if isinstance(e, discord.HTTPException) and status == 429:
                                self._debounce_backoff_sec = min(
                                    self._debounce_max_delay_sec,
                                    max(self._debounce_backoff_sec, self._debounce_base_delay_sec) * 2.0,
                                )
                        except Exception:
                            pass
                        # message might be gone; clean up
                        self._active.pop(msg_id, None)
                        self._message_area.pop(msg_id, None)
                        self._locks.pop(msg_id, None)
            finally:
                # Clear task entry
                try:
                    self._debounce_tasks.pop(msg_id, None)
                except Exception:
                    pass

        task = asyncio.create_task(_runner())
        self._debounce_tasks[msg_id] = task

    def _schedule_embed_update(self, channel: discord.abc.Messageable, delay_sec: Optional[float] = None):
        if self._embed_update_task and not self._embed_update_task.done():
            return

        async def _runner():
            try:
                use_delay = delay_sec if delay_sec is not None else float(self._embed_debounce_backoff_sec)
                await asyncio.sleep(use_delay)
                if not self._first_msg_id:
                    return
                try:
                    first_msg = self._messages.get(self._first_msg_id)
                    if first_msg is None:
                        first_msg = await channel.fetch_message(self._first_msg_id)
                        self._messages[self._first_msg_id] = first_msg
                    await first_msg.edit(embed=self._build_first_embed())
                    # On success, gently reduce embed backoff
                    try:
                        self._embed_debounce_backoff_sec = max(
                            self._embed_debounce_base_delay_sec,
                            self._embed_debounce_backoff_sec * 0.8,
                        )
                    except Exception:
                        pass
                except Exception as e:
                    logger.exception("Checker: debounced history embed update failed")
                    # If rate limited, increase backoff
                    try:
                        status = getattr(e, 'status', None)
                        if isinstance(e, discord.HTTPException) and status == 429:
                            self._embed_debounce_backoff_sec = min(
                                self._embed_debounce_max_delay_sec,
                                max(self._embed_debounce_backoff_sec, self._embed_debounce_base_delay_sec) * 2.0,
                            )
                    except Exception:
                        pass
            finally:
                self._embed_update_task = None

        self._embed_update_task = asyncio.create_task(_runner())

    # --------------------- Helpers and DB persistence ---------------------
    def _make_active_key(self, area: str, boss_key: str) -> str:
        return f"{area}|{boss_key}"

    def _make_custom_id(self, area: str, boss_key: str) -> str:
        area_id = self._area_id_map.get(area, area)
        cid = f"checker:{area_id}:{boss_key}"
        if len(cid) <= MAX_CUSTOM_ID_LENGTH:
            return cid
        # Hash boss key when needed and remember mapping
        bid = self._boss_id_map.get(boss_key)
        if not bid:
            bid = hashlib.sha1(boss_key.encode("utf-8")).hexdigest()[:8]
            self._boss_id_map[boss_key] = bid
            self._boss_id_rev[bid] = boss_key
        cid = f"checker:{area_id}:{bid}"
        return cid[:MAX_CUSTOM_ID_LENGTH]

    def _parse_custom_id(self, cid: str) -> Optional[Tuple[str, str]]:
        try:
            if not cid or not cid.startswith("checker:"):
                return None
            _pfx, rest = cid.split(":", 1)
            parts = rest.split(":", 2)
            if len(parts) != 2:
                return None
            aid, bid_or_key = parts
            area = self._area_id_rev.get(aid, aid)
            # Prefer direct key; else map hashed id
            boss_key = bid_or_key if bid_or_key in BOSSES else self._boss_id_rev.get(bid_or_key, bid_or_key)
            return area, boss_key
        except Exception:
            return None

    def _db_init_message_table(self):
        try:
            with self.client.db as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS checker_messages (guild_id INTEGER, area TEXT, message_id INTEGER, PRIMARY KEY(guild_id, area))"
                )
        except Exception:
            logger.exception("Checker: failed to init checker_messages table")

    def _db_load_message_ids(self, guild_id: int) -> Dict[str, int]:
        self._db_init_message_table()
        mapping: Dict[str, int] = {}
        try:
            with self.client.db as db:
                db.execute("SELECT area, message_id FROM checker_messages WHERE guild_id=?", (guild_id,))
                rows = db.fetchall() or []
                for area, mid in rows:
                    if isinstance(area, str) and isinstance(mid, int):
                        mapping[area] = mid
        except Exception:
            logger.exception("Checker: failed to load message ids from db")
        return mapping

    def _db_save_message_id(self, guild_id: int, area: str, message_id: int):
        self._db_init_message_table()
        try:
            with self.client.db as db:
                db.execute(
                    "INSERT OR REPLACE INTO checker_messages (guild_id, area, message_id) VALUES (?, ?, ?)",
                    (guild_id, area, int(message_id)),
                )
        except Exception:
            logger.exception("Checker: failed to save message id for %s", area)

    # --------------------- Fury Gate persistence ---------------------
    def _db_init_furygate_table(self):
        try:
            with self.client.db as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS furygate (guild_id INTEGER PRIMARY KEY, city TEXT)"
                )
        except Exception:
            logger.exception("Checker: failed to init furygate table")

    def _db_load_furygate_city(self, guild_id: int) -> Optional[str]:
        self._db_init_furygate_table()
        try:
            with self.client.db as db:
                db.execute("SELECT city FROM furygate WHERE guild_id=?", (guild_id,))
                row = db.fetchone()
                if row and isinstance(row[0], str):
                    return row[0]
        except Exception:
            logger.exception("Checker: failed to load furygate city")
        return None

    def _db_save_furygate_city(self, guild_id: int, city: str) -> None:
        self._db_init_furygate_table()
        try:
            with self.client.db as db:
                db.execute(
                    "INSERT OR REPLACE INTO furygate (guild_id, city) VALUES (?, ?)",
                    (guild_id, city),
                )
        except Exception:
            logger.exception("Checker: failed to save furygate city")

    def _get_furygate_city(self, guild_id: int) -> str:
        # Default to 'Carlin' if not set
        try:
            if guild_id in self._furygate_city:
                return self._furygate_city[guild_id]
            city = self._db_load_furygate_city(guild_id)
            if not city:
                city = 'Carlin'
                self._db_save_furygate_city(guild_id, city)
            self._furygate_city[guild_id] = city
            return city
        except Exception:
            return 'Carlin'

    def _set_furygate_city(self, guild_id: int, city: str) -> None:
        self._furygate_city[guild_id] = city
        self._db_save_furygate_city(guild_id, city)
