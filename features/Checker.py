from IFeature import IFeature
import discord
from discord import app_commands
import asyncio
import logging
import hashlib
from typing import Dict, List, Set, Tuple, Union, Optional
from datetime import datetime, timezone, time as dtime, timedelta
from zoneinfo import ZoneInfo
from .Bosses import BOSSES
from .CheckerUpdater import CheckerUpdater, WORLDS

logger = logging.getLogger(__name__)

# CET timezone for display
CET = ZoneInfo("Europe/Berlin")

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

MAX_HISTORY = 25
# Edit queue priorities: redraws a user is waiting for go before history and aging updates
EDIT_PRIO_CLICK = 0
EDIT_PRIO_NORMAL = 1
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
        # Cache last raids content to avoid unnecessary edits
        self._last_raids_content: Optional[str] = None
        # Updater interface to determine which bosses can spawn today
        self._updater = CheckerUpdater(self.client)
        # Default button timing thresholds (in seconds)
        self._default_warn_sec = 15 * 60
        self._default_alert_sec = 30 * 60
        self._default_reset_sec = 60 * 60
        # Cached percent map per guild to avoid frequent provider calls during rapid clicks
        # guild_id -> (timestamp_unix, percent_map)
        self._percent_map_cache: Dict[int, Tuple[int, Dict[str, Optional[int]]]] = {}
        # Paced edit queue for the checker channel. All channel edits share one Discord
        # rate limit, so a single worker sends them one at a time.
        # msg_id -> (priority, sequence, kind, attempts); lower priority goes first,
        # FIFO within a priority. One entry per message: newer requests replace older ones,
        # and the content is rendered when the edit is sent, so it is always current.
        self._edit_queue: Dict[int, Tuple[int, int, str, int]] = {}
        self._edit_seq: int = 0
        self._edit_event: asyncio.Event = asyncio.Event()
        self._edit_worker_task: Optional[asyncio.Task] = None
        self._edit_interval_sec: float = 1.2
        self._edit_max_attempts: int = 3
        # Latest Possible Raids content waiting to be sent by the edit worker
        self._pending_raids_content: Optional[str] = None
        # Guild owning the checker channel
        self._guild_id: int = 0
        # Cache of message objects to avoid refetching
        self._messages: Dict[int, discord.Message] = {}
        # Last rendered state per area message: msg_id -> { boss_key: (style_value, emoji, label) }
        self._last_style_state: Dict[int, Dict[str, Tuple[int, str, str]]] = {}
        # Stable short IDs for areas and boss keys to keep custom_id short
        self._area_id_map: Dict[str, str] = {name: hashlib.sha1(name.encode("utf-8")).hexdigest()[:8] for name in AREAS.keys()}
        self._area_id_rev: Dict[str, str] = {v: k for k, v in self._area_id_map.items()}
        self._boss_id_map: Dict[str, str] = {}
        self._boss_id_rev: Dict[str, str] = {}
        # Fury Gate city per guild (Carlin or Thais)
        self._furygate_city = {}
        # Killed bosses per guild (boss_key set) - hidden until next refresh
        self._killed_bosses: Dict[int, Set[str]] = {}

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
                # Setting up the messages takes longer than Discord's 3s reply window
                await interaction.response.defer(ephemeral=True, thinking=True)
                try:
                    with self.client.db as db:
                        db.execute("CREATE TABLE IF NOT EXISTS checker_config (id INTEGER PRIMARY KEY, channel_id INTEGER)")
                        db.execute("INSERT OR REPLACE INTO checker_config (id, channel_id) VALUES (1, ?)", (channel.id,))

                    # apply immediately
                    await self._ensure_channel_messages_and_update()

                    if not self._task or self._task.done():
                        self._task = asyncio.create_task(self._background_loop())

                    await interaction.followup.send(f"Checker channel set to {channel.mention}", ephemeral=True)
                except Exception:
                    logger.exception("Checker: failed to store channel in database")
                    try:
                        await interaction.followup.send("Failed to register channel.", ephemeral=True)
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
                    # Fetching spawn data and re-rendering takes longer than Discord's 3s reply window
                    await interaction.response.defer(ephemeral=True, thinking=True)
                    try:
                        await self._updater.set_world(interaction.guild.id, world.value)
                        # refresh cache for this guild and re-render if channel configured
                        await self._updater.update_cache_for_guild(interaction.guild.id)
                        await self._ensure_channel_messages_and_update()
                        await interaction.followup.send(f"World set to {world.value}.", ephemeral=True)
                    except ValueError:
                        await interaction.followup.send("Invalid world.", ephemeral=True)
                    except Exception:
                        logger.exception("Checker: failed to set world")
                        try:
                            await interaction.followup.send("Failed to set world.", ephemeral=True)
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
                    # Fetching spawn data and re-rendering takes longer than Discord's 3s reply window
                    await interaction.response.defer(ephemeral=True, thinking=True)
                    try:
                        guild_id = interaction.guild.id
                        # Refresh cache and re-render messages
                        await self._updater.update_cache_for_guild(guild_id)
                        await self._ensure_channel_messages_and_update()
                        allowed = self._updater.get_allowed_boss_names(guild_id)
                        world = self._updater.get_world(guild_id) or "(no world set)"
                        await interaction.followup.send(
                            f"Refreshed checker cache for world {world}. Possible spawns: {len(allowed)}.",
                            ephemeral=True,
                        )
                    except Exception:
                        logger.exception("Checker: manual refresh failed")
                        try:
                            await interaction.followup.send("Failed to refresh checker cache.", ephemeral=True)
                        except Exception:
                            pass

                self.client.tree.add_command(checkerrefresh)

                # Command to set Fury Gate city (Carlin or Thais). Affects Furyosa button label.
                @app_commands.command(name="furygate", description="Set the Fury Gate city for this server")
                @app_commands.choices(city=[
                    app_commands.Choice(name="None", value="None"),
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
                        # Handle "None" specially
                        if city.value == "None":
                            self._set_furygate_city(guild_id, None)
                            user_name = getattr(interaction.user, "display_name", None) or getattr(interaction.user, "name", "User")
                            await interaction.response.send_message(f"{user_name} set Fury Gate to None")
                        else:
                            self._set_furygate_city(guild_id, city.value)
                            user_name = getattr(interaction.user, "display_name", None) or getattr(interaction.user, "name", "User")
                            await interaction.response.send_message(f"{user_name} changed Fury Gate to {city.value}")
                        # Re-render checker messages to update the Furyosa label
                        await self._ensure_channel_messages_and_update()
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
            self._ensure_workers()
        else:
            logger.info("Checker: no channel configured yet — waiting for /checker before activating updates")

    async def _background_loop(self):
        try:
            await self._ensure_channel_messages_and_update()
        except Exception:
            logger.exception("Checker initial update failed")

        # schedule daily 10:45 updates (API updates at 10:30, bot updates at 10:45)
        while True:
            now = datetime.now()
            target = datetime.combine(now.date(), dtime(hour=10, minute=45, second=0))
            if target <= now:
                target = target + timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            try:
                await asyncio.sleep(wait_seconds)
                await self._ensure_channel_messages_and_update(clear_killed=True)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Checker scheduled update failed")

    async def _ensure_channel_messages_and_update(self, clear_killed: bool = False):
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
        # Also covers a channel set with /checker after startup
        self._checker_channel_id = channel_id
        self._guild_id = guild_id
        self._ensure_workers()

        # Reset active state for bosses marked serversave=True (daily reset behavior) on any refresh/ensure
        try:
            self._prune_serversave_active()
        except Exception:
            logger.exception("Checker: failed to prune serversave active states")
        
        # Clear killed bosses (from /boss skull button) only during scheduled daily refresh
        if clear_killed:
            try:
                self.clear_killed_bosses(guild_id)
            except Exception:
                logger.exception("Checker: failed to clear killed bosses")
        
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
                sent = await channel.send(embed=self._build_first_embed(guild_id))
                first_msg = sent
                self._db_save_message_id(guild_id, 'FIRST', sent.id)
            self._messages[first_msg.id] = first_msg
        except Exception:
            logger.exception("Checker: failed to create first message")
            return

        self._first_msg_id = first_msg.id

        # Load persisted button states from database
        area_active_map: Dict[str, Dict[str, int]] = {}
        try:
            # First load saved message IDs to know which messages exist
            saved_msg_ids = self._db_load_message_ids(guild_id)
            for area in AREAS.keys():
                area_active_map[area] = {}
                msg_id = saved_msg_ids.get(area)
                if msg_id:
                    # Load persisted timestamps for this area's message
                    active_data = self._db_load_active(guild_id, msg_id)
                    area_active_map[area] = active_data
                    # Also populate the in-memory _active dict
                    self._active[msg_id] = dict(active_data)
        except Exception:
            logger.exception("Checker: failed to load persisted active states; starting fresh")
            area_active_map = {area: {} for area in AREAS.keys()}

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
            existing = existing_area_msgs.get(area)
            if existing is not None:
                # Track the message and let the edit worker redraw it if anything changed
                self._active[existing.id] = dict(active)
                self._message_area[existing.id] = area
                self._area_msg_ids[area] = existing.id
                self._messages[existing.id] = existing
                self._db_save_message_id(guild_id, area, existing.id)
                self._queue_edit(existing.id, "area")
            else:
                # Create new message
                try:
                    content = self._build_area_content(area, bosses, active)
                    view = self._view_for_area(area, bosses, active, channel.guild.id, percent_map, allowed)
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
                self._raids_msg_id = existing_raids_msg.id
                self._messages[existing_raids_msg.id] = existing_raids_msg
                self._db_save_message_id(guild_id, 'RAIDS', existing_raids_msg.id)
                # Only edit if content changed
                if self._last_raids_content != raids_text:
                    self._pending_raids_content = raids_text
                    self._queue_edit(existing_raids_msg.id, "raids")
            else:
                sent = await channel.send(raids_text)
                self._last_raids_content = raids_text
                self._raids_msg_id = sent.id
                self._messages[sent.id] = sent
                self._db_save_message_id(guild_id, 'RAIDS', sent.id)
        except Exception:
            logger.exception("Checker: failed to send/edit Possible Raids message")

        # Update the first message embed to reflect current history
        self._queue_edit(self._first_msg_id, "first")

    def _prune_serversave_active(self) -> None:
        """Clear active markers for bosses that should reset on server save (09:00) or on manual refresh.
        Uses BOSSES[boss_key]['serversave'] == True.
        """
        if not self._active:
            return
        
        # Track which boss_keys are serversave bosses for database cleanup
        serversave_boss_keys: Set[str] = set()
        for boss_key, meta in BOSSES.items():
            if isinstance(meta, dict) and bool(meta.get('serversave', False)):
                serversave_boss_keys.add(boss_key)
        
        # Clear from in-memory state
        for msg_id, active_map in list(self._active.items()):
            changed = False
            for k in list(active_map.keys()):
                try:
                    _area, boss_key = k.split("|", 1)
                except Exception:
                    continue
                if boss_key in serversave_boss_keys:
                    del active_map[k]
                    changed = True
            if changed:
                self._active[msg_id] = active_map
        
        # Clear from database
        if serversave_boss_keys:
            try:
                self._db_init_active_table()
                with self.client.db as db:
                    # Delete all entries for serversave bosses
                    placeholders = ','.join('?' * len(serversave_boss_keys))
                    db.execute(
                        f"DELETE FROM checker_active WHERE boss_key IN ({placeholders})",
                        tuple(serversave_boss_keys)
                    )
            except Exception:
                logger.exception("Checker: failed to clear serversave bosses from database")

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

    def _build_first_embed(self, guild_id: Optional[int] = None) -> discord.Embed:
        # Get world name for title
        world = "Unknown"
        if guild_id:
            try:
                world = self._updater.get_world(guild_id) or "Unknown"
            except Exception:
                pass
        
        emb = discord.Embed(title=f"Boss Checks - {world}", description="", color=0x0066CC)
        legend_text = (
            "Click the button after checking a boss.\n\n"
            "Boss Check Legend:\n"
            ":white_check_mark: = up to 15 minutes (Zarabustor 7 minutes | POI 45 minutes)\n"
            ":grey_exclamation: = at least 15 minutes ago (Zarabustor 7 minutes | POI 45 minutes)\n"
            ":bangbang: = at least 30 minutes ago (Zarabustor 15 minutes | POI 90 minutes)\n"
            ":alarm_clock: = over 60 minutes ago (Zarabustor 30 minutes | POI 180 minutes)"
        )
        # Credit the site the spawn data comes from (requested by GuildStats)
        source = self._updater.get_source(guild_id) if guild_id else None
        if source:
            name, url = source
            legend_text += f"\n\nData based on [{name}]({url})" if url else f"\n\nData based on {name}"
        # Fast path when no history
        if not self._history:
            emb.description = f"{legend_text}\n\nNo checks yet."
            emb.set_footer(text=f"Updated: {datetime.now(CET).strftime('%Y-%m-%d %H:%M:%S %Z')}")
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

        emb.set_footer(text=f"Updated: {datetime.now(CET).strftime('%Y-%m-%d %H:%M:%S %Z')}")
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
        # Get killed bosses for this guild
        killed_bosses = self._killed_bosses.get(guild_id, set())

        for b in bosses:
            # Determine if this is a persistent boss (always shown, no percent label)
            persistent = isinstance(b, dict) and bool(b.get('persist', False))
            name = self._boss_to_name(b)  # display name (may include location qualifiers)
            boss_key = self._boss_to_key(b)  # BOSSES mapping key for identification/history
            
            # Skip if this boss has been marked as killed (but not if it's persistent)
            if not persistent and boss_key in killed_bosses:
                continue
            
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
            style, emoji_str = self._style_and_emoji_for_ts(last_ts, now_ts, warn_s, alert_s, reset_s)
            cid = self._make_custom_id(area, boss_key)
            base_label = self._button_label(name, boss_key, base_name, persistent, guild_id, percent_map)
            # Convert emoji string to PartialEmoji for button
            emoji_obj = discord.PartialEmoji.from_str(emoji_str) if emoji_str else None
            view.add_item(discord.ui.Button(style=style, label=base_label, emoji=emoji_obj, custom_id=cid))
        return view

    def _button_label(self, name: str, boss_key: str, base_name: str, persistent: bool, guild_id: int, percent_map: Dict[str, Optional[int]]) -> str:
        # Adjust display name for Furyosa: append current Fury Gate city if set
        label_name = name
        try:
            if boss_key == 'Furyosa':
                city = self._get_furygate_city(guild_id)
                if city:
                    label_name = f"{name} ({city})"
        except Exception:
            pass
        # Look up percentage by base boss name
        pct = percent_map.get(base_name)
        # Persistent bosses never show a percentage label; only show % when >2
        return label_name if persistent else (f"{label_name} [{pct}%]" if (isinstance(pct, int) and pct > 2) else label_name)

    def _style_and_emoji_for_ts(self, ts: Optional[int], now_ts: Optional[int] = None, warn_s: Optional[int] = None, alert_s: Optional[int] = None, reset_s: Optional[int] = None) -> Tuple[discord.ButtonStyle, str]:
        if now_ts is None:
            now_ts = _now_unix()
        if not ts or ts <= 0:
            # Inactive
            return discord.ButtonStyle.danger, "⏰"
        delta = now_ts - ts
        ws = warn_s or self._default_warn_sec
        as_ = alert_s or self._default_alert_sec
        rs = reset_s or self._default_reset_sec
        if delta < ws:
            return discord.ButtonStyle.success, "✅"
        if delta < as_:
            return discord.ButtonStyle.success, "❕"
        if delta < rs:
            # No true orange style; use primary as closest alternative
            return discord.ButtonStyle.primary, "‼️"
        # Expired -> inactive
        return discord.ButtonStyle.danger, "⏰"

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
                # Not tracked in memory; restore other bosses' timestamps from the database
                try:
                    active = self._db_load_active(interaction.guild.id if interaction.guild else 0, msg.id)
                except Exception:
                    active = {}
            # Make sure the tick loop keeps aging this message's buttons
            self._message_area[msg.id] = area
            self._area_msg_ids[area] = msg.id
            self._messages.setdefault(msg.id, msg)
            key = self._make_active_key(area, boss)
            # Always record/refresh the check timestamp; do not toggle off
            ts = _now_unix()
            active[key] = ts
            self._active[msg.id] = active
            
            # Persist timestamp to database
            guild_id = interaction.guild.id if interaction.guild else 0
            try:
                self._db_save_active(guild_id, msg.id, area, boss, ts)
            except Exception:
                logger.exception("Checker: failed to persist active timestamp for %s/%s", area, boss)

            # Redraw the clicked message right away through the interaction itself. This edit
            # does not count against the channel's message edit rate limit.
            redrawn = False
            try:
                pmap = self._get_cached_percent_map(guild_id) or {}
                allowed = set(pmap.keys())
                bosses = AREAS.get(area, [])
                new_state = self._compute_area_style_state(area, bosses, active, guild_id, pmap, allowed)
                view = self._view_for_area(area, bosses, active, guild_id, pmap, allowed)
                await interaction.edit_original_response(content=self._build_area_content(area, bosses, active), view=view)
                self._last_style_state[msg.id] = new_state
                redrawn = True
            except Exception as e:
                logger.warning("Checker: interaction redraw of %s (%s) failed, queueing a regular edit: %r", msg.id, area, e)
        if not redrawn:
            self._queue_edit(msg.id, "area", EDIT_PRIO_CLICK)

        # record check in history (newest first) using BOSSES key as before
        user_display = getattr(interaction.user, "display_name", None) or getattr(interaction.user, "name", str(interaction.user))
        ts = _now_unix()
        async with self._history_lock:
            self._history.insert(0, (ts, user_display, area, boss))
            if len(self._history) > MAX_HISTORY:
                self._history = self._history[:MAX_HISTORY]

        # The edit worker coalesces history embed updates from rapid clicks
        if self._first_msg_id:
            self._queue_edit(self._first_msg_id, "first")

    async def close(self):
        for task in (self._task, self._tick_task, self._edit_worker_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _tick_loop(self):
        """Periodic updater to advance button states (15/30/60 min) and expire them after 60 minutes."""
        while True:
            try:
                await asyncio.sleep(60)
                # If no channel or no messages, skip
                if not self._checker_channel_id or not self._active:
                    continue
                # Do NOT refresh spawnable percentages every minute; use cached map
                guild_id = self._guild_id
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
                    # Queue a redraw only when the buttons would look different
                    new_state = self._compute_area_style_state(area, AREAS.get(area, []), active_map, guild_id, pmap, allowed)
                    if self._last_style_state.get(msg_id) != new_state:
                        self._queue_edit(msg_id, "area")
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

    def _compute_area_style_state(self, area: str, bosses: List[str], active: Dict[str, int], guild_id: int, percent_map: Dict[str, Optional[int]], allowed: Optional[Set[str]] = None) -> Dict[str, Tuple[int, str, str]]:
        """Compute the state (style.value, emoji, label) for all buttons that would be rendered for an area."""
        state: Dict[str, Tuple[int, str, str]] = {}
        allowed = set(percent_map.keys()) if allowed is None else allowed
        now_ts = _now_unix()
        killed_bosses = self._killed_bosses.get(guild_id, set())
        
        for b in bosses:
            persistent = isinstance(b, dict) and bool(b.get('persist', False))
            name = self._boss_to_name(b)
            boss_key = self._boss_to_key(b)
            
            # Skip if this boss has been marked as killed (but not if it's persistent)
            if not persistent and boss_key in killed_bosses:
                continue
            
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
            label = self._button_label(name, boss_key, base_name, persistent, guild_id, percent_map)
            state[boss_key] = (style_val, emoji, label)
        return state

    # --------------------- Paced edit queue ---------------------
    def _ensure_workers(self) -> None:
        if not self._tick_task or self._tick_task.done():
            self._tick_task = asyncio.create_task(self._tick_loop())
        if not self._edit_worker_task or self._edit_worker_task.done():
            self._edit_worker_task = asyncio.create_task(self._edit_worker())

    def _queue_edit(self, msg_id: Optional[int], kind: str, priority: int = EDIT_PRIO_NORMAL, attempts: int = 0) -> None:
        """Queue an edit of a checker message ("area", "first" or "raids").
        A message has at most one queued edit; queueing it again keeps its place in line
        and takes the higher priority."""
        if not msg_id:
            return
        existing = self._edit_queue.get(msg_id)
        if existing is not None:
            prio, seq, _kind, old_attempts = existing
            self._edit_queue[msg_id] = (min(prio, priority), seq, kind, min(attempts, old_attempts))
        else:
            self._edit_seq += 1
            self._edit_queue[msg_id] = (priority, self._edit_seq, kind, attempts)
        self._edit_event.set()

    async def _edit_worker(self):
        """Send queued checker edits one at a time, paced to stay under the channel's rate limit."""
        while True:
            try:
                if not self._edit_queue:
                    self._edit_event.clear()
                    await self._edit_event.wait()
                    continue
                msg_id = min(self._edit_queue, key=lambda m: self._edit_queue[m][:2])
                _prio, _seq, kind, attempts = self._edit_queue.pop(msg_id)
                # Only pace after a request was actually sent
                if await self._run_edit(msg_id, kind, attempts):
                    await asyncio.sleep(self._edit_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Checker: edit worker error")
                await asyncio.sleep(self._edit_interval_sec)

    async def _run_edit(self, msg_id: int, kind: str, attempts: int) -> bool:
        """Render and send one queued edit. Returns True when a request was sent to Discord."""
        if not self._checker_channel_id:
            return False
        channel = self.client.get_channel(self._checker_channel_id)
        if channel is None:
            try:
                channel = await self.client.fetch_channel(self._checker_channel_id)
            except Exception:
                logger.warning("Checker: could not resolve checker channel for queued edit of %s", msg_id)
                return True
        lock = self._locks.get(msg_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[msg_id] = lock
        # Same lock as clicks, so a click redraw and a queued edit never overtake each other
        async with lock:
            new_state = None
            content = None
            try:
                if kind == "area":
                    area = self._message_area.get(msg_id)
                    if not area:
                        return False
                    active_map = self._active.get(msg_id, {})
                    bosses = AREAS.get(area, [])
                    pmap = self._get_cached_percent_map(self._guild_id) or {}
                    allowed = set(pmap.keys())
                    new_state = self._compute_area_style_state(area, bosses, active_map, self._guild_id, pmap, allowed)
                    if self._last_style_state.get(msg_id) == new_state:
                        return False  # Already showing this state
                    edit_kwargs = {
                        "content": self._build_area_content(area, bosses, active_map),
                        "view": self._view_for_area(area, bosses, active_map, self._guild_id, pmap, allowed),
                    }
                elif kind == "first":
                    edit_kwargs = {"embed": self._build_first_embed(self._guild_id)}
                elif kind == "raids":
                    content = self._pending_raids_content
                    if content is None or content == self._last_raids_content:
                        return False
                    edit_kwargs = {"content": content}
                else:
                    return False

                msg = self._messages.get(msg_id)
                if msg is None:
                    msg = await channel.fetch_message(msg_id)
                    self._messages[msg_id] = msg
                await msg.edit(**edit_kwargs)
                if kind == "area":
                    self._last_style_state[msg_id] = new_state
                elif kind == "raids":
                    self._last_raids_content = content
                return True
            except discord.NotFound:
                # Message was deleted; stop tracking it
                self._forget_message(msg_id)
                return True
            except Exception as e:
                if kind == "area":
                    # The tick loop queues it again within a minute since the state still differs
                    logger.warning("Checker: area edit of message %s failed, retrying on next tick: %r", msg_id, e)
                elif attempts + 1 < self._edit_max_attempts:
                    logger.warning("Checker: %s edit of message %s failed, will retry: %r", kind, msg_id, e)
                    self._queue_edit(msg_id, kind, attempts=attempts + 1)
                else:
                    logger.warning("Checker: %s edit of message %s failed %d times, giving up: %r", kind, msg_id, attempts + 1, e)
                return True

    # --------------------- Helpers and DB persistence ---------------------
    def _forget_message(self, msg_id: int) -> None:
        """Stop tracking an area message that no longer exists."""
        self._active.pop(msg_id, None)
        self._message_area.pop(msg_id, None)
        self._locks.pop(msg_id, None)
        self._messages.pop(msg_id, None)
        self._last_style_state.pop(msg_id, None)

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

    # --------------------- Killed boss tracking (for /boss command integration) ---------------------
    def mark_boss_killed(self, guild_id: int, role_name: str) -> None:
        """Mark all bosses with the given role as killed (hidden from checker) until next refresh.
        Called by BossAnnouncer when skull button is clicked.
        """
        if guild_id not in self._killed_bosses:
            self._killed_bosses[guild_id] = set()
        
        # Find all boss keys that have this role
        killed_keys: Set[str] = set()
        for boss_key, data in BOSSES.items():
            if isinstance(data, dict) and data.get('role') == role_name:
                self._killed_bosses[guild_id].add(boss_key)
                killed_keys.add(boss_key)
                logger.info("Checker: marked boss %s (role %s) as killed for guild %s", boss_key, role_name, guild_id)

        # Redraw only the area messages that show one of these bosses
        for area, bosses in AREAS.items():
            if any(self._boss_to_key(b) in killed_keys for b in bosses):
                self._queue_edit(self._area_msg_ids.get(area), "area", EDIT_PRIO_CLICK)
    
    def clear_killed_bosses(self, guild_id: int) -> None:
        """Clear all killed boss markers for a guild. Called during daily refresh."""
        if guild_id in self._killed_bosses:
            self._killed_bosses[guild_id].clear()
            logger.info("Checker: cleared killed bosses for guild %s", guild_id)

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

    def _db_save_furygate_city(self, guild_id: int, city: Optional[str]) -> None:
        self._db_init_furygate_table()
        try:
            with self.client.db as db:
                if city is None:
                    # Store NULL in database for None
                    db.execute(
                        "INSERT OR REPLACE INTO furygate (guild_id, city) VALUES (?, NULL)",
                        (guild_id,),
                    )
                else:
                    db.execute(
                        "INSERT OR REPLACE INTO furygate (guild_id, city) VALUES (?, ?)",
                        (guild_id, city),
                    )
        except Exception:
            logger.exception("Checker: failed to save furygate city")

    def _get_furygate_city(self, guild_id: int) -> Optional[str]:
        # Return None if not set or explicitly set to None (no default)
        try:
            if guild_id in self._furygate_city:
                return self._furygate_city[guild_id]
            city = self._db_load_furygate_city(guild_id)
            # Store in cache (can be None)
            self._furygate_city[guild_id] = city
            return city
        except Exception:
            return None

    def _set_furygate_city(self, guild_id: int, city: Optional[str]) -> None:
        self._furygate_city[guild_id] = city
        self._db_save_furygate_city(guild_id, city)

    # --------------------- Boss check timestamp persistence ---------------------
    def _db_init_active_table(self):
        """Create table to persist boss check timestamps across bot restarts."""
        try:
            with self.client.db as db:
                db.execute(
                    """CREATE TABLE IF NOT EXISTS checker_active 
                       (guild_id INTEGER, message_id INTEGER, area TEXT, boss_key TEXT, 
                        last_check_ts INTEGER, 
                        PRIMARY KEY(guild_id, message_id, area, boss_key))"""
                )
        except Exception:
            logger.exception("Checker: failed to init checker_active table")

    def _db_load_active(self, guild_id: int, message_id: int) -> Dict[str, int]:
        """Load boss check timestamps from database for a specific message."""
        self._db_init_active_table()
        active: Dict[str, int] = {}
        try:
            with self.client.db as db:
                db.execute(
                    "SELECT area, boss_key, last_check_ts FROM checker_active WHERE guild_id=? AND message_id=?",
                    (guild_id, message_id)
                )
                rows = db.fetchall() or []
                for area, boss_key, ts in rows:
                    if isinstance(area, str) and isinstance(boss_key, str) and isinstance(ts, int):
                        key = self._make_active_key(area, boss_key)
                        active[key] = ts
        except Exception:
            logger.exception("Checker: failed to load active timestamps from db for guild %s msg %s", guild_id, message_id)
        return active

    def _db_save_active(self, guild_id: int, message_id: int, area: str, boss_key: str, timestamp: int):
        """Save a boss check timestamp to database."""
        self._db_init_active_table()
        try:
            with self.client.db as db:
                db.execute(
                    """INSERT OR REPLACE INTO checker_active 
                       (guild_id, message_id, area, boss_key, last_check_ts) 
                       VALUES (?, ?, ?, ?, ?)""",
                    (guild_id, message_id, area, boss_key, timestamp)
                )
        except Exception:
            logger.exception("Checker: failed to save active timestamp for %s/%s", area, boss_key)


