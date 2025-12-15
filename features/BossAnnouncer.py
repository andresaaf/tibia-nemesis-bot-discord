from IFeature import IFeature
import discord
from discord import app_commands
import logging
import asyncio
from typing import Dict, Set, List, Tuple, Union, Optional
from .Bosses import BOSSES
from .Checker import Checker

logger = logging.getLogger(__name__)

class BossAnnouncer(IFeature):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cmd_registered = False
    # state: message_id -> {creator, role_id, coming:list[int], ready:list[int], killed:list[int], killed_enabled:bool}
        self._state: Dict[int, Dict] = {}
        self._locks: Dict[int, asyncio.Lock] = {}
        # channels where /boss is allowed
        self._boss_channels: set[int] = set()
        # valid boss role names derived from BOSSES
        self._boss_role_names = {data['role'] for data in BOSSES.values() if data.get('role')}
        self._init_db()

    def _init_db(self):
        """Initialize database table and load registered boss channels."""
        try:
            with self.client.db as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS boss_channels (
                        channel_id INTEGER PRIMARY KEY
                    )
                    """
                )
                cursor.execute('SELECT channel_id FROM boss_channels')
                for (channel_id,) in cursor.fetchall():
                    self._boss_channels.add(channel_id)
        except Exception:
            logger.exception("Failed to init boss_channels table")

    async def on_message(self, message: discord.Message):
        """Listen for role mentions in boss channels and create announcements automatically."""
        # Ignore bot messages
        if message.author.bot:
            return
        
        # Only process messages in registered boss channels
        if message.channel.id not in self._boss_channels:
            return
        
        # Check if message has exactly one role mention
        if len(message.role_mentions) == 0:
            # No role mentions - delete the message
            try:
                await message.delete()
            except Exception:
                logger.debug("Could not delete message without role mentions (may lack permissions)")
            return
        
        if len(message.role_mentions) > 1:
            # Multiple role mentions - delete the message
            try:
                await message.delete()
            except Exception:
                logger.debug("Could not delete message with multiple role mentions (may lack permissions)")
            return
        
        # Get the single role mention
        role = message.role_mentions[0]
        
        # Check if the role is a configured boss role
        if role.name not in self._boss_role_names:
            # Not a valid boss role - delete the message
            try:
                await message.delete()
            except Exception:
                logger.debug(f"Could not delete message with invalid boss role {role.name} (may lack permissions)")
            return
        
        # Extract the message text (remove role mention to get the custom message)
        custom_message = message.content.replace(f"<@&{role.id}>", "").strip()
        
        # If the message is empty after removing mention, set to None
        if not custom_message:
            custom_message = None
        
        # Create the boss announcement
        try:
            await self._create_boss_announcement(
                channel=message.channel,
                role=role,
                creator=message.author,
                custom_message=custom_message,
                original_message=message
            )
        except Exception:
            logger.exception(f"Failed to create boss announcement from message for role {role.name}")

    async def _create_boss_announcement(
        self, 
        channel: discord.TextChannel, 
        role: discord.Role, 
        creator: discord.User | discord.Member,
        custom_message: Optional[str] = None,
        original_message: Optional[discord.Message] = None
    ) -> Optional[discord.Message]:
        """Helper method to create a boss announcement. Used by both /boss command and message listener."""
        # Validate that the role corresponds to a configured boss
        if not role or role.name not in self._boss_role_names:
            return None

        # initialize state
        state = {
            "creator": creator.id,
            "role_id": role.id,
            "coming": [],
            "ready": [],
            "killed": [],
            "killed_enabled": False,
        }
        
        # Snapshot latest checks at creation time only
        try:
            state["latest_checks_lines"] = await self._recent_checks_lines_for_role(role_name=role.name, limit=4)
        except Exception:
            state["latest_checks_lines"] = []

        # build initial embed
        embed = await self._build_embed(role, state)

        # Determine headline based on whether this boss is a raid
        is_raid = False
        if role and role.name:
            for _k, _data in BOSSES.items():
                if _data.get('role') == role.name:
                    is_raid = bool(_data.get('raid'))
                    break

        # Create view with buttons
        view = discord.ui.View(timeout=None)
        # For raids, skip Coming/Ready/Remove me buttons
        if not is_raid:
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label="Coming", custom_id="boss:coming"))
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.success, label="Ready", custom_id="boss:ready"))
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.danger, label="Remove me", custom_id="boss:remove"))
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label="💀", custom_id="boss:skull"))
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label="❌", custom_id="boss:close"))

        # Build content
        headline = "Raid!" if is_raid else "Boss spawn!"
        content = f"{role.mention} {headline}"
        if custom_message:
            content += f"\n{custom_message}"

        # Send the announcement
        try:
            msg = await channel.send(content=content, embed=embed, view=view)
            
            # Store the initialized state keyed by message id
            self._state[msg.id] = state
            self._locks[msg.id] = asyncio.Lock()
            
            return msg
        except Exception:
            logger.exception("Failed to send boss announcement")
            return None

    async def on_ready(self):
        if self._cmd_registered:
            return
        
        @app_commands.command(name="boss", description="Announce a boss and tag a role. Creates a message with signup buttons.")
        @app_commands.describe(role="Role to mention in the announcement", message="Optional message to include under the headline")
        async def boss(interaction: discord.Interaction, role: discord.Role, message: Optional[str] = None):
            if interaction.guild is None:
                await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
                return

            # Determine target channel: if current isn't registered, try to find a registered one in this guild
            channel = interaction.channel
            target_channel: discord.TextChannel | None = None
            if channel and channel.id in self._boss_channels:
                target_channel = channel  # current channel is registered
            else:
                # find a registered channel belonging to this guild
                for ch_id in list(self._boss_channels):
                    ch = self.client.get_channel(ch_id)
                    if isinstance(ch, discord.TextChannel) and ch.guild.id == interaction.guild.id:
                        target_channel = ch
                        break

            if target_channel is None:
                await interaction.response.send_message(
                    "No boss announcement channel is registered for this server."
                    " Ask an admin to run /setupbosschannel in the desired channel.",
                    ephemeral=True,
                )
                return

            # Validate that the selected role corresponds to a configured boss
            if not role or role.name not in self._boss_role_names:
                await interaction.response.send_message(
                    "That role is not a configured boss role. Please choose a role that matches a boss in the list.",
                    ephemeral=True,
                )
                return

            # If we're already in the registered channel, post as the interaction response
            redirected = not (channel and target_channel and channel.id == target_channel.id)
            
            if not redirected:
                # Defer the response since we're posting in the same channel
                await interaction.response.defer(ephemeral=True)
            
            # Create the announcement using the helper method
            try:
                msg = await self._create_boss_announcement(
                    channel=target_channel,
                    role=role,
                    creator=interaction.user,
                    custom_message=message,
                    original_message=None  # Don't delete slash command trigger
                )
                
                if msg is None:
                    raise Exception("Failed to create announcement")
                
                # Respond appropriately based on whether we redirected
                if not redirected:
                    # We already deferred with ephemeral=True, so just delete it silently
                    try:
                        await interaction.delete_original_response()
                    except Exception:
                        pass
                else:
                    # Send ephemeral confirmation with link
                    try:
                        await interaction.response.send_message(
                            f"Announcement created in {target_channel.mention}: {msg.jump_url}",
                            ephemeral=True,
                        )
                    except Exception:
                        try:
                            await interaction.followup.send(
                                f"Announcement created in {target_channel.mention}: {msg.jump_url}",
                                ephemeral=True,
                            )
                        except Exception:
                            pass
            except Exception:
                logger.exception("Failed to send boss announcement")
                try:
                    if not redirected:
                        await interaction.followup.send("Failed to create announcement. Check permissions.", ephemeral=True)
                    else:
                        await interaction.response.send_message("Failed to create announcement. Check permissions.", ephemeral=True)
                except Exception:
                    try:
                        await interaction.followup.send("Failed to create announcement. Check permissions.", ephemeral=True)
                    except Exception:
                        pass
                return

        try:
            # Register channel management commands and /boss
            @app_commands.command(name="setupbosschannel", description="Register this channel for /boss announcements")
            @app_commands.default_permissions(manage_channels=True)
            @app_commands.checks.has_permissions(manage_channels=True)
            async def setup_boss_channel(interaction: discord.Interaction):
                if not interaction.guild or not interaction.channel:
                    await interaction.response.send_message("This command must be used in a server channel.", ephemeral=True)
                    return

                channel_id = interaction.channel.id
                if channel_id in self._boss_channels:
                    await interaction.response.send_message("This channel is already registered for boss announcements.", ephemeral=True)
                    return

                try:
                    with self.client.db as cursor:
                        cursor.execute('INSERT OR REPLACE INTO boss_channels (channel_id) VALUES (?)', (channel_id,))
                    self._boss_channels.add(channel_id)
                    await interaction.response.send_message("Channel registered for boss announcements.", ephemeral=True)
                except Exception:
                    logger.exception("Failed to register boss channel")
                    await interaction.response.send_message("Failed to register this channel.", ephemeral=True)

            @app_commands.command(name="removebosschannel", description="Unregister this channel from /boss announcements")
            @app_commands.default_permissions(manage_channels=True)
            @app_commands.checks.has_permissions(manage_channels=True)
            async def remove_boss_channel(interaction: discord.Interaction):
                if not interaction.guild or not interaction.channel:
                    await interaction.response.send_message("This command must be used in a server channel.", ephemeral=True)
                    return

                channel_id = interaction.channel.id
                if channel_id not in self._boss_channels:
                    await interaction.response.send_message("This channel is not registered for boss announcements.", ephemeral=True)
                    return

                try:
                    with self.client.db as cursor:
                        cursor.execute('DELETE FROM boss_channels WHERE channel_id = ?', (channel_id,))
                    self._boss_channels.discard(channel_id)
                    await interaction.response.send_message("Channel unregistered from boss announcements.", ephemeral=True)
                except Exception:
                    logger.exception("Failed to unregister boss channel")
                    await interaction.response.send_message("Failed to unregister this channel.", ephemeral=True)

            self.client.tree.add_command(boss)
            self.client.tree.add_command(setup_boss_channel)
            self.client.tree.add_command(remove_boss_channel)
            self._cmd_registered = True
        except Exception:
            logger.exception("Failed to register /boss command.")

    async def _build_embed(self, role: discord.Role, state: Dict) -> discord.Embed:
        # Find boss price and emoji from BOSSES dictionary by matching role name
        price_str = None
        emoji_name = None
        is_raid = False
        raid_msg = None
        if role and role.name:
            for boss_key, boss_data in BOSSES.items():
                if boss_data.get('role') == role.name:
                    # Get price if available
                    if 'price' in boss_data:
                        price = boss_data['price']
                        if price >= 1000000:
                            price_str = f"{price // 1000000}kk"
                        elif price >= 1000:
                            price_str = f"{price // 1000}k"
                        else:
                            price_str = str(price)
                    # Get emoji name if available
                    if 'emoji' in boss_data:
                        emoji_name = boss_data['emoji']
                    # Raid info
                    is_raid = bool(boss_data.get('raid'))
                    raid_msg = boss_data.get('raid_msg')
                    break
        
        # Build a cleaner description (no @role — signup below)
        description_parts: List[str] = []
        if price_str:
            description_parts.append(f"💰 Price: **{price_str}**")
        # If raid and extra message provided, append it
        if is_raid and raid_msg:
            description_parts.append(raid_msg)
        description = "\n\n".join(description_parts)
        
        # Use the boss name (role name) as the embed title instead of a generic one
        title_text = role.name if role and role.name else "Boss Announcement"
        embed = discord.Embed(title=title_text, description=description, color=0xAA0000)
        
        # Add boss emoji as thumbnail if available
        if emoji_name:
            emoji = await self.client.get_app_emoji(emoji_name)
            if emoji:
                embed.set_thumbnail(url=emoji.url)
        def list_names(ids):
            if not ids:
                return "—"
            # Preserve insertion order; accept list or set from older messages
            try:
                ordered_ids = list(ids)
            except Exception:
                ordered_ids = [uid for uid in ids]
            names = []
            for uid in ordered_ids:
                member = role.guild.get_member(uid)
                if member:
                    names.append(member.display_name)
                else:
                    names.append(f"<@{uid}>")
            # One name per line as requested
            return "\n".join(names)
        
        # Latest checks first (snapshot at creation time)
        try:
            recent_lines = list(state.get("latest_checks_lines") or [])
            if recent_lines:
                embed.add_field(name="Latest checks", value="\n".join(recent_lines), inline=False)
        except Exception:
            pass

        # For raids, skip Coming and Ready columns
        if not is_raid:
            # Show Coming and Ready side-by-side
            embed.add_field(name="Coming", value=list_names(state["coming"]), inline=True)
            embed.add_field(name="Ready", value=list_names(state["ready"]), inline=True)
        
        # Show Killed column only if enabled
        if state.get("killed_enabled"):
            embed.add_field(name="Got Kill", value=list_names(state["killed"]), inline=True)

        # Footer: "Found by <user>"
        creator_id = state.get("creator")
        guild = role.guild if role else None
        if creator_id:
            creator_name = None
            if guild:
                member = guild.get_member(creator_id)
                if member:
                    creator_name = member.display_name
            if not creator_name:
                creator_name = f"<@{creator_id}>"
            embed.set_footer(text=f"\n──────────────────────────\nFound by {creator_name}")

        return embed

    async def _recent_checks_lines_for_role(self, role_name: str, limit: int = 4) -> List[str]:
        """Return up to `limit` recent check lines for any BOSSES entries that map to the given role name.
        Output format per line: "• <boss_key> — <relative time> — by <user>" (area included when available).
        """
        if not role_name:
            return []
        # Gather BOSSES keys that share this role
        matching_keys: Set[str] = set()
        for key, data in BOSSES.items():
            try:
                if isinstance(data, dict) and data.get('role') == role_name:
                    matching_keys.add(key)
            except Exception:
                continue

        if not matching_keys:
            return []

        # Find the Checker feature instance to read its in-memory history
        checker: Optional[Checker] = None
        try:
            for feat in getattr(self.client, 'features', []) or []:
                if isinstance(feat, Checker):
                    checker = feat
                    break
        except Exception:
            checker = None

        if not checker:
            return []

        # Snapshot recent history (already newest-first). We'll pick those matching our keys.
        recent: List[Tuple[Union[int, str], str, str, str]] = []
        try:
            # Prefer to read under the lock if available
            lock = getattr(checker, '_history_lock', None)
            if isinstance(lock, asyncio.Lock):
                async with lock:
                    recent = list(getattr(checker, '_history', []) or [])
            else:
                recent = list(getattr(checker, '_history', []) or [])
        except Exception:
            recent = []

        if not recent:
            return []

        lines: List[str] = []
        for ts_or_str, user, area, boss_key in recent:
            if boss_key not in matching_keys:
                continue
            # Time formatting: use Discord relative time if int
            if isinstance(ts_or_str, int):
                tpart = f"<t:{ts_or_str}:R>"
            else:
                tpart = str(ts_or_str)
            # Line format: timestamp - user (no bullets, no area/boss)
            lines.append(f"{tpart} - {user}")
            if len(lines) >= limit:
                break
        # Display oldest -> newest so the latest check is at the bottom
        return list(reversed(lines))

    async def on_button_click(self, interaction: discord.Interaction):
        # Only handle our boss buttons
        data = getattr(interaction, "data", {}) or {}
        custom_id = data.get("custom_id") or getattr(interaction, "custom_id", None)
        if not custom_id or not custom_id.startswith("boss:"):
            return

        action = custom_id.split(":", 1)[1]
        msg = getattr(interaction, "message", None)
        if msg is None:
            try:
                await interaction.response.send_message("Could not resolve the announcement message.", ephemeral=True)
            except Exception:
                pass
            return

        msg_id = msg.id
        state = self._state.get(msg_id)
        if state is None:
            try:
                await interaction.response.send_message("This announcement is no longer tracked (bot restart?).", ephemeral=True)
            except Exception:
                pass
            return

        lock = self._locks.get(msg_id) or asyncio.Lock()
        async with lock:
            user_id = interaction.user.id
            # Coerce legacy sets into lists to preserve ordering going forward
            try:
                if not isinstance(state["coming"], list):
                    state["coming"] = list(state["coming"]) if state.get("coming") else []
                if not isinstance(state["ready"], list):
                    state["ready"] = list(state["ready"]) if state.get("ready") else []
                if not isinstance(state["killed"], list):
                    state["killed"] = list(state["killed"]) if state.get("killed") else []
            except Exception:
                pass

            def _remove(lst: List[int], uid: int):
                try:
                    while uid in lst:
                        lst.remove(uid)
                except Exception:
                    pass

            def _append_unique(lst: List[int], uid: int):
                try:
                    if uid in lst:
                        lst.remove(uid)
                except Exception:
                    pass
                lst.append(uid)
            # toggle behaviors
            if action == "coming":
                # Move from Ready to Coming if present; else toggle Coming membership
                if user_id in state["ready"]:
                    _remove(state["ready"], user_id)
                    _remove(state["killed"], user_id)
                    _append_unique(state["coming"], user_id)
                else:
                    if user_id in state["coming"]:
                        _remove(state["coming"], user_id)
                    else:
                        _remove(state["killed"], user_id)
                        _append_unique(state["coming"], user_id)
            elif action == "ready":
                # Toggle Ready; Ready implies not in Coming; also remove from Killed
                if user_id in state["ready"]:
                    _remove(state["ready"], user_id)
                else:
                    _append_unique(state["ready"], user_id)
                _remove(state["coming"], user_id)
                _remove(state["killed"], user_id)
            elif action == "skull":
                # Only creator or moderators (manage_messages) can enable killed mode
                guild = interaction.guild
                allowed = False
                if guild:
                    member = guild.get_member(interaction.user.id) if isinstance(interaction.user, discord.User) else interaction.user
                    if member and member.guild_permissions.manage_messages:
                        allowed = True
                if interaction.user.id == state.get("creator"):
                    allowed = True

                if not allowed:
                    try:
                        await interaction.response.send_message("Only the creator or users with Manage Messages can enable killed mode.", ephemeral=True)
                    except Exception:
                        pass
                    return

                # Enable the Killed column but do not add the clicking user automatically.
                state["killed_enabled"] = True
                
                # Mark this boss as killed in the Checker so it's hidden until next refresh
                try:
                    if guild:
                        role = guild.get_role(state.get("role_id"))
                        if role:
                            # Find the Checker feature instance
                            checker: Optional[Checker] = None
                            for feat in getattr(self.client, 'features', []) or []:
                                if isinstance(feat, Checker):
                                    checker = feat
                                    break
                            if checker:
                                checker.mark_boss_killed(guild.id, role.name)
                                logger.info("BossAnnouncer: marked boss role %s as killed in Checker for guild %s", role.name, guild.id)
                                # Trigger a refresh of checker messages to immediately hide the boss
                                try:
                                    await checker._ensure_channel_messages_and_update()
                                except Exception:
                                    logger.exception("BossAnnouncer: failed to refresh checker messages after marking boss killed")
                except Exception:
                    logger.exception("BossAnnouncer: failed to mark boss as killed in Checker")
                
                # After enabling, the buttons will be replaced with a single "Killed" button
            elif action == "remove":
                # Remove the user from all signup lists (Coming, Ready, Killed)
                _remove(state["coming"], user_id)
                _remove(state["ready"], user_id)
                _remove(state["killed"], user_id)
            elif action == "killed":
                # When Killed button is clicked, add the user to killed set and remove from others
                if user_id not in state["killed"]:
                    _append_unique(state["killed"], user_id)
                    _remove(state["coming"], user_id)
                    _remove(state["ready"], user_id)
                else:
                    # toggle off if already in killed
                    _remove(state["killed"], user_id)
            elif action == "close":
                # only creator or moderators (manage_messages) can close
                guild = interaction.guild
                allowed = False
                if guild:
                    member = guild.get_member(interaction.user.id) if isinstance(interaction.user, discord.User) else interaction.user
                    if member and member.guild_permissions.manage_messages:
                        allowed = True
                if interaction.user.id == state.get("creator"):
                    allowed = True

                if not allowed:
                    try:
                        await interaction.response.send_message("Only the creator or users with Manage Messages can close this announcement.", ephemeral=True)
                    except Exception:
                        pass
                    return

                try:
                    await msg.delete()
                except Exception:
                    try:
                        await interaction.response.send_message("Failed to delete announcement.", ephemeral=True)
                    except Exception:
                        pass
                # cleanup state
                self._state.pop(msg_id, None)
                self._locks.pop(msg_id, None)
                return
            else:
                # unknown action
                return

            # edit the announcement message to reflect updated lists
            try:
                guild = interaction.guild
                role = None
                if guild:
                    role = guild.get_role(state["role_id"])
                
                # Determine if this is a raid boss
                is_raid = False
                if role and role.name:
                    for _k, _data in BOSSES.items():
                        if _data.get('role') == role.name:
                            is_raid = bool(_data.get('raid'))
                            break
                
                embed = await self._build_embed(role if role else (interaction.guild and interaction.guild.default_role), state)
                # Recreate view depending on whether Killed column is enabled
                view = discord.ui.View(timeout=None)
                if state.get("killed_enabled"):
                    # Only the "Killed" button remains (per requirement)
                    view.add_item(discord.ui.Button(style=discord.ButtonStyle.danger, label="Got Kill", custom_id="boss:killed"))
                    #view.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label="❌", custom_id="boss:close"))
                else:
                    # For raids, skip Coming/Ready/Remove me buttons
                    if not is_raid:
                        view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label="Coming", custom_id="boss:coming"))
                        view.add_item(discord.ui.Button(style=discord.ButtonStyle.success, label="Ready", custom_id="boss:ready"))
                        # "Remove me" button (red) removes the user from Coming/Ready/Killed
                        view.add_item(discord.ui.Button(style=discord.ButtonStyle.danger, label="Remove me", custom_id="boss:remove"))
                    view.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label="💀", custom_id="boss:skull"))
                    view.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label="❌", custom_id="boss:close"))

                await msg.edit(embed=embed, view=view)
            except Exception:
                logger.exception("Failed to update announcement message %s", msg_id)
