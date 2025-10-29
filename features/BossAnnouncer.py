from IFeature import IFeature
import discord
from discord import app_commands
import logging
import asyncio
from typing import Dict, Set
from .Bosses import BOSSES

logger = logging.getLogger(__name__)

class BossAnnouncer(IFeature):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cmd_registered = False
        # state: message_id -> {creator, role_id, coming:set, ready:set, killed:set, killed_enabled:bool}
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

    async def on_ready(self):
        if self._cmd_registered:
            return
        
        @app_commands.command(name="boss", description="Announce a boss and tag a role. Creates a message with signup buttons.")
        @app_commands.describe(role="Role to mention in the announcement", )
        async def boss(interaction: discord.Interaction, role: discord.Role):
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

            # initialize state and build initial embed via _build_embed for consistency
            state = {
                "creator": interaction.user.id,
                "role_id": role.id,
                "coming": set(),
                "ready": set(),
                "killed": set(),
                "killed_enabled": False,
            }
            embed = await self._build_embed(role, state)

            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label="Coming", custom_id="boss:coming"))
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.success, label="Ready", custom_id="boss:ready"))
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.danger, label="Remove me", custom_id="boss:remove"))
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label="💀", custom_id="boss:skull"))
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label="❌", custom_id="boss:close"))

            try:
                # Determine headline based on whether this boss is a raid
                is_raid = False
                if role and role.name:
                    for _k, _data in BOSSES.items():
                        if _data.get('role') == role.name:
                            is_raid = bool(_data.get('raid'))
                            break
                headline = "Raid!" if is_raid else "Boss spawn!"

                # If we're already in the registered channel, post as the interaction response
                redirected = not (channel and target_channel and channel.id == target_channel.id)
                if not redirected:
                    await interaction.response.send_message(
                        content=f"{role.mention} {headline} Click a button to sign up.",
                        embed=embed,
                        view=view,
                    )
                    msg = await interaction.original_response()
                else:
                    # Send the announcement to the target channel
                    msg = await target_channel.send(
                        content=f"{role.mention} {headline} Click a button to sign up.",
                        embed=embed,
                        view=view,
                    )
                    # Only in the redirect case, acknowledge with a link ephemerally
                    try:
                        await interaction.response.send_message(
                            f"Announcement created in {target_channel.mention}: {msg.jump_url}",
                            ephemeral=True,
                        )
                    except Exception:
                        # fallback if already responded or other issue
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
                    await interaction.response.send_message("Failed to create announcement. Check permissions.", ephemeral=True)
                except Exception:
                    try:
                        await interaction.followup.send("Failed to create announcement. Check permissions.", ephemeral=True)
                    except Exception:
                        pass
                return

            # store the initialized state keyed by message id
            self._state[msg.id] = state
            self._locks[msg.id] = asyncio.Lock()

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
        
        description = f"{role.mention} — signup below"
        if price_str:
            description += f"\n💰 Price: **{price_str}**"
        # If raid and extra message provided, append it
        if is_raid and raid_msg:
            description += f"\n\n{raid_msg}"
        
        # Use the boss name (role name) as the embed title instead of a generic one
        title_text = role.name if role and role.name else "Boss Announcement"
        embed = discord.Embed(title=title_text, description=description, color=0xAA0000)
        
        # Add boss emoji as thumbnail if available
        if emoji_name:
            emoji = await self.client.get_app_emoji(emoji_name)
            if emoji:
                embed.set_thumbnail(url=emoji.url)
        def list_names(ids: Set[int]):
            if not ids:
                return "—"
            names = []
            for uid in ids:
                member = role.guild.get_member(uid)
                if member:
                    names.append(member.display_name)
                else:
                    names.append(f"<@{uid}>")
            return ", ".join(names)
        
        # Show Coming and Ready side-by-side
        embed.add_field(name="Coming", value=list_names(state["coming"]), inline=True)
        embed.add_field(name="Ready", value=list_names(state["ready"]), inline=True)
        # Show Killed column only if enabled
        if state.get("killed_enabled"):
            embed.add_field(name="Killed 💀", value=list_names(state["killed"]), inline=True)

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
            # toggle behaviors
            if action == "coming":
                # If the user is currently in Ready, clicking Coming should move them back to Coming (remove Ready).
                # Otherwise toggle Coming on/off.
                if user_id in state["ready"]:
                    state["ready"].discard(user_id)
                    state["coming"].add(user_id)
                    state["killed"].discard(user_id)
                else:
                    if user_id in state["coming"]:
                        # toggle off
                        state["coming"].discard(user_id)
                    else:
                        state["coming"].add(user_id)
                        state["killed"].discard(user_id)
            elif action == "ready":
                # toggle membership in ready
                if user_id in state["ready"]:
                    state["ready"].discard(user_id)
                else:
                    state["ready"].add(user_id)
                    # ensure they're also in coming
                    state["coming"].add(user_id)
                    state["killed"].discard(user_id)
                # ensure Ready implies not counted as only "Coming" (remove from Coming if present)
                state["coming"].discard(user_id)
            elif action == "skull":
                # Enable the Killed column but do not add the clicking user automatically.
                state["killed_enabled"] = True
                # After enabling, the buttons will be replaced with a single "Killed" button
            elif action == "remove":
                # Remove the user from all signup lists (Coming, Ready, Killed)
                state["coming"].discard(user_id)
                state["ready"].discard(user_id)
                state["killed"].discard(user_id)
            elif action == "killed":
                # When Killed button is clicked, add the user to killed set and remove from others
                if user_id not in state["killed"]:
                    state["killed"].add(user_id)
                    state["coming"].discard(user_id)
                    state["ready"].discard(user_id)
                else:
                    # toggle off if already in killed
                    state["killed"].discard(user_id)
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
                embed = await self._build_embed(role if role else (interaction.guild and interaction.guild.default_role), state)
                # Recreate view depending on whether Killed column is enabled
                view = discord.ui.View(timeout=None)
                if state.get("killed_enabled"):
                    # Only the "Killed" button remains (per requirement)
                    view.add_item(discord.ui.Button(style=discord.ButtonStyle.danger, label="Killed 💀", custom_id="boss:killed"))
                    #view.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label="❌", custom_id="boss:close"))
                else:
                    view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label="Coming", custom_id="boss:coming"))
                    view.add_item(discord.ui.Button(style=discord.ButtonStyle.success, label="Ready", custom_id="boss:ready"))
                    # "Remove me" button (red) removes the user from Coming/Ready/Killed
                    view.add_item(discord.ui.Button(style=discord.ButtonStyle.danger, label="Remove me", custom_id="boss:remove"))
                    view.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label="💀", custom_id="boss:skull"))
                    view.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label="❌", custom_id="boss:close"))

                await msg.edit(embed=embed, view=view)
            except Exception:
                logger.exception("Failed to update announcement message %s", msg_id)
