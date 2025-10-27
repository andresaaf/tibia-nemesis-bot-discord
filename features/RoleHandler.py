from IFeature import IFeature
import discord
import logging
import re
from .Bosses import BOSSES

logger = logging.getLogger(__name__)

# Define sections for Nemesis bosses channel
# Note: When adding new bosses to a section, ensure that only new bosses are appended LAST to the existing list.
# When adding new sections, they have to be added at the END of the existing sections.
NEMESIS_SECTIONS = {
    "Bank Robbers": [
        BOSSES['Elvira Hammerthrust'],
        BOSSES['Jesse the Wicked'],
        BOSSES['Mornenion'],
        BOSSES['Robby the Reckless']
    ],
    "Hive Outpost": [
        BOSSES['The Manhunter'],
        BOSSES['The Mean Masher'],
        BOSSES['The Hungerer']
    ],
    "POI": [
        BOSSES['Countess Sorrow'],
        BOSSES['Dracola'],
        BOSSES['Massacre'],
        BOSSES['Mr. Punish'],
        BOSSES['The Handmaiden'],
        BOSSES['The Imperor'],
        BOSSES['The Plasmother'],
    ],
    "Vampire Lords": [
        BOSSES['Arachir the Ancient One'],
        BOSSES['Diblis the Fair'],
        BOSSES['Sir Valorcrest'],
        BOSSES['Zevelon Duskbringer']
    ],
    "Others A-D": [
        BOSSES['Arthom the Hunter'],
        BOSSES['Barbaria'],
        BOSSES['Battlemaster Zunzu'],
        BOSSES['Big Boss Trolliver'],
        BOSSES['Burster'],
        BOSSES['Captain Jones'],
        BOSSES['Dharalion'],
        BOSSES['Dreadful Disruptor'],
        BOSSES['Dreadmaw'],
    ],
    "E-G": [
        BOSSES['Flamecaller Zazrak'],
        BOSSES['Fleabringer'],
        BOSSES['Foreman Kneebiter'],
        BOSSES['Furyosa'],
        BOSSES['General Murius'],
        BOSSES['Grandfather Tridian'],
        BOSSES['Gravelord Oshuran'],
        BOSSES['Groam'],
        BOSSES['Grorlam'],
    ],
    "H-M": [
        BOSSES['Hairman the Huge'],
        BOSSES['Hatebreeder'],
        BOSSES['High Templar Cobrass'],
        BOSSES['Hirintor'],
        BOSSES['Mahatheb'],
        BOSSES['Man in the Cave'],
    ],
    "N-S": [
        BOSSES['Ocyakao'],
        BOSSES['Omrafir'],
        BOSSES['Oodok Witchmaster'],
        BOSSES['Rotworm Queen'],
        BOSSES['Rukor Zad'],
        BOSSES['Shlorg'],
        BOSSES['Smuggler Baron Silvertoe'],
    ],
    "T": [
        BOSSES['The Big Bad One'],
        BOSSES['The Evil Eye'],
        BOSSES['The Frog Prince'],
        BOSSES['The Old Whopper'],
        BOSSES['The Voice of Ruin'],
        BOSSES['The Welter'],
        BOSSES['Tyrn'],
        BOSSES['Tzumrah the Dazzler'],
    ],
    "U-Z": [
        BOSSES['Warlord Ruzad'],
        BOSSES['White Pale'],
        BOSSES['Xenia'],
        BOSSES['Yaga the Crone'],
        BOSSES['Yakchal'],
        BOSSES['Zarabustor'],
        BOSSES['Zushuka'],
    ],
}

# Define sections for Other bosses channel
# Note: When adding new bosses to a section, ensure that only new bosses are appended LAST to the existing list.
# When adding new sections, they have to be added at the END of the existing sections.
OTHER_SECTIONS = {
    "Raids": [
        BOSSES['Cublarc the Plunderer'],
        BOSSES['Feroxa'],
        BOSSES['Ferumbras'],
        BOSSES['Gaz\'haragoth'],
        BOSSES['Ghazbaran'],
        BOSSES['Grand Mother Foulscale'],
        BOSSES['Morgaroth'],
        BOSSES['Morshabaal'],
        BOSSES['Orshabaal'],
        BOSSES['Sir Leopold'],
        BOSSES['The Abomination'],
        BOSSES['The Blightfather'],
        BOSSES['The Pale Count'],
        BOSSES['Willi Wasp'],
        BOSSES['Zomba'],
        BOSSES['Zulazza the Corruptor'],
    ],
    "Bestiary": [
        BOSSES['Albino Dragon'],
        BOSSES['Dire Penguin'],
        BOSSES['Midnight Panther'],
        BOSSES['Troll Guard'],
        BOSSES['Undead Cavebear'],
        BOSSES['Wild Horse'],
    ],
    "Archfoe": [
        BOSSES['Mawhawk'],
    ]
}

class RoleHandler(IFeature):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cmd_registered = False
        # channel_id -> list of message_ids for each section
        self._tracked_channels = {}
        # message_id -> {emoji_id: role_id}
        self._role_messages = {}
        # channel_id -> channel type ('nemesis' or 'other')
        self._channel_types = {}
        # Cache for application emojis
        self._app_emojis = {}
        self._init_db()
        
    async def _get_emoji(self, emoji_name: str, guild: discord.Guild = None):
        """Get emoji by name from application emojis."""
        # Check cache first
        if emoji_name in self._app_emojis:
            return self._app_emojis[emoji_name]
        
        # Fetch application emojis if cache is empty
        if not self._app_emojis:
            try:
                app_emojis = await self.client.fetch_application_emojis()
                for emoji in app_emojis:
                    self._app_emojis[emoji.name] = emoji
            except Exception as e:
                logger.error(f"Failed to fetch application emojis: {e}")
                return None
        
        # Return from cache
        return self._app_emojis.get(emoji_name)
        
    def _init_db(self):
        """Initialize database tables and load existing data."""
        with self.client.db as cursor:
            # Create table for tracked channels
            cursor.execute('''CREATE TABLE IF NOT EXISTS role_channels
                            (channel_id INTEGER PRIMARY KEY,
                             channel_type TEXT NOT NULL)''')
            
            # Load existing data
            cursor.execute('SELECT channel_id, channel_type FROM role_channels')
            for channel_id, channel_type in cursor.fetchall():
                self._tracked_channels[channel_id] = []
                self._channel_types[channel_id] = channel_type
                
    async def _build_section_message(self, channel: discord.TextChannel, section_name: str, bosses: list) -> tuple[discord.Message, dict]:
        """Build and send a section message with reactions. Returns (message, emoji_role_map)."""
        # Build message content
        lines = [f"**{section_name}**"]
        for boss_data in bosses:
            if boss_data['emoji'] and boss_data['name']:
                emoji = await self._get_emoji(boss_data['emoji'], channel.guild)
                if emoji:
                    lines.append(f"{emoji} - {boss_data['name']}")
                else:
                    lines.append(f"{boss_data['emoji']} - {boss_data['name']}")
        
        msg = await channel.send("\n".join(lines))
        
        # Add reactions and build emoji->role mapping
        emoji_role_map = {}
        for boss_data in bosses:
            if boss_data['emoji'] and boss_data['role']:
                emoji = await self._get_emoji(boss_data['emoji'], channel.guild)
                if not emoji:
                    logger.warning(f"Emoji '{boss_data['emoji']}' not found in application emojis")
                    continue
                    
                # Get or create the role
                role = discord.utils.get(channel.guild.roles, name=boss_data['role'])
                if not role:
                    try:
                        role = await channel.guild.create_role(name=boss_data['role'])
                    except Exception as e:
                        logger.error(f"Failed to create role {boss_data['role']}: {e}")
                        continue
                        
                # Add reaction and track the mapping
                try:
                    await msg.add_reaction(emoji)
                    emoji_role_map[str(emoji)] = role.id
                except Exception as e:
                    logger.error(f"Failed to add reaction {emoji}: {e}")
        
        return msg, emoji_role_map
    
    async def _build_emoji_role_map(self, channel: discord.TextChannel, bosses: list) -> dict:
        """Build emoji->role mapping from boss data (for existing messages)."""
        emoji_role_map = {}
        for boss_data in bosses:
            if boss_data['emoji'] and boss_data['role']:
                emoji = await self._get_emoji(boss_data['emoji'], channel.guild)
                if emoji:
                    role = discord.utils.get(channel.guild.roles, name=boss_data['role'])
                    if role:
                        emoji_role_map[str(emoji)] = role.id
        return emoji_role_map
    
    async def _rebuild_channel_data(self, channel: discord.TextChannel):
        """Rebuild message and reaction mappings from channel messages."""
        try:
            # Get the channel type
            channel_type = self._channel_types.get(channel.id)
            if not channel_type:
                return
                
            # Get the appropriate sections
            sections = NEMESIS_SECTIONS if channel_type == "nemesis" else OTHER_SECTIONS
            
            # Fetch all messages from the channel (assuming it's a dedicated channel)
            messages = []
            async for message in channel.history(limit=100):
                if message.author == self.client.user:
                    messages.append(message)
            messages.reverse()  # Oldest first
            
            # Process messages and rebuild mappings
            section_idx = 0
            section_list = list(sections.items())
            message_ids = []
            
            for message in messages:
                if section_idx >= len(section_list):
                    break
                    
                section_name, bosses = section_list[section_idx]
                
                # Check if this message matches the expected section format
                if message.content.startswith(f"**{section_name}**"):
                    message_ids.append(message.id)
                    
                    # Check if section needs updating
                    await self._update_section_if_changed(message, section_name, bosses, channel.guild)
                    
                    # Rebuild emoji->role mapping from reactions and boss data
                    emoji_role_map = await self._build_emoji_role_map(channel, bosses)
                    if emoji_role_map:
                        self._role_messages[message.id] = emoji_role_map
                    
                    section_idx += 1
            
            # Check if there are new sections that need to be added
            if section_idx < len(section_list):
                logger.info(f"Found {len(section_list) - section_idx} new section(s) to add to channel {channel.id}")
                
                # Create messages for new sections
                for i in range(section_idx, len(section_list)):
                    section_name, bosses = section_list[i]
                    msg, emoji_role_map = await self._build_section_message(channel, section_name, bosses)
                    
                    message_ids.append(msg.id)
                    if emoji_role_map:
                        self._role_messages[msg.id] = emoji_role_map
                    
                    logger.info(f"Created new section '{section_name}' in channel {channel.id}")
            
            self._tracked_channels[channel.id] = message_ids
            
        except Exception as e:
            logger.error(f"Failed to rebuild channel data for {channel.id}: {e}")
            
    async def _update_section_if_changed(self, message: discord.Message, section_name: str, bosses: list, guild: discord.Guild):
        """Check if a section has changed and update it (append-only)."""
        try:
            # Build expected content
            expected_lines = [f"**{section_name}**"]
            expected_emojis = []
            
            for boss_data in bosses:
                if boss_data['emoji'] and boss_data['name']:
                    emoji_name = boss_data['emoji']
                    emoji = await self._get_emoji(emoji_name, guild)
                    
                    if emoji:
                        expected_lines.append(f"{emoji} - {boss_data['name']}")
                        expected_emojis.append(emoji)
                    else:
                        expected_lines.append(f"{emoji_name} - {boss_data['name']}")
            
            expected_content = "\n".join(expected_lines)
            
            # Check if content differs
            if message.content != expected_content:
                # Find what's missing (append-only approach)
                current_lines = message.content.split("\n")
                new_lines_to_add = []
                
                for line in expected_lines:
                    if line not in current_lines:
                        new_lines_to_add.append(line)
                
                if new_lines_to_add and new_lines_to_add[0] != f"**{section_name}**":
                    # Update message content
                    new_content = message.content + "\n" + "\n".join(new_lines_to_add)
                    await message.edit(content=new_content)
                    logger.info(f"Updated section '{section_name}' with {len(new_lines_to_add)} new boss(es)")
                    
                    # Add missing reactions
                    existing_reactions = {str(r.emoji) for r in message.reactions}
                    for emoji in expected_emojis:
                        if str(emoji) not in existing_reactions:
                            try:
                                await message.add_reaction(emoji)
                                logger.info(f"Added reaction {emoji} to section '{section_name}'")
                            except Exception as e:
                                logger.error(f"Failed to add reaction {emoji}: {e}")
                                
        except Exception as e:
            logger.error(f"Failed to update section '{section_name}': {e}")

    async def on_ready(self):
        logger.info("RoleHandler ready")
        
        if self._cmd_registered:
            return
            
        from discord import app_commands
        
        @app_commands.command(name="setuproles", description="Set up role reactions in the current channel")
        @app_commands.describe(channel_type="The type of channel to set up (nemesis or other)")
        @app_commands.choices(channel_type=[
            app_commands.Choice(name="Nemesis Bosses", value="nemesis"),
            app_commands.Choice(name="Other Bosses", value="other")
        ])
        async def setup_roles(interaction: discord.Interaction, channel_type: str):
            if not interaction.guild or not interaction.channel:
                await interaction.response.send_message("This command must be used in a server channel.", ephemeral=True)
                return
                
            # Check permissions
            member = interaction.guild.get_member(interaction.user.id)
            if not member or not member.guild_permissions.manage_roles:
                await interaction.response.send_message("You need the Manage Roles permission to run this command.", ephemeral=True)
                return
                
            # Check bot permissions
            me = interaction.guild.me
            if not me or not me.guild_permissions.manage_roles:
                await interaction.response.send_message("I need the Manage Roles permission to manage roles.", ephemeral=True)
                return
                
            # Check if channel is already being used
            if interaction.channel.id in self._tracked_channels:
                await interaction.response.send_message("This channel is already set up for role reactions. Use /removeroles first if you want to reconfigure it.", ephemeral=True)
                return
                
            await interaction.response.send_message("Setting up role reactions...", ephemeral=True)
            
            # Send description message
            desc = (f"**{channel_type.title()} Boss Roles**\n\n"
                   "React to messages below to receive role notifications for specific bosses.\n"
                   "Remove your reaction to stop receiving notifications.\n_ _")
            await interaction.channel.send(desc)
            
            # Track this channel
            channel_messages = []
            
            # Get sections based on channel type
            sections = NEMESIS_SECTIONS if channel_type == "nemesis" else OTHER_SECTIONS

            # Create a message for each section
            for section, bosses in sections.items():
                msg, emoji_role_map = await self._build_section_message(interaction.channel, section, bosses)
                channel_messages.append(msg.id)
                if emoji_role_map:
                    self._role_messages[msg.id] = emoji_role_map
                
            # Store in memory
            self._tracked_channels[interaction.channel.id] = channel_messages
            self._channel_types[interaction.channel.id] = channel_type
            
            # Store in database (only channel info)
            with self.client.db as cursor:
                cursor.execute('INSERT OR REPLACE INTO role_channels (channel_id, channel_type) VALUES (?, ?)', 
                            (interaction.channel.id, channel_type))
            
            # Sync current reactions
            await self._sync_all_reactions(interaction.guild, interaction.channel)
            
        @app_commands.command(name="removeroles", description="Remove role reaction setup from the current channel")
        async def remove_roles(interaction: discord.Interaction):
            if not interaction.guild or not interaction.channel:
                await interaction.response.send_message("This command must be used in a server channel.", ephemeral=True)
                return
                
            # Check permissions
            member = interaction.guild.get_member(interaction.user.id)
            if not member or not member.guild_permissions.manage_roles:
                await interaction.response.send_message("You need the Manage Roles permission to run this command.", ephemeral=True)
                return
                
            channel_id = interaction.channel.id
            if channel_id not in self._tracked_channels:
                await interaction.response.send_message("No role reactions are set up in this channel.", ephemeral=True)
                return
                
            # Remove messages
            try:
                # Delete messages and clean up memory
                for msg_id in self._tracked_channels[channel_id]:
                    try:
                        msg = await interaction.channel.fetch_message(msg_id)
                        await msg.delete()
                        if msg_id in self._role_messages:
                            del self._role_messages[msg_id]
                    except:
                        pass
                
                # Clean up database
                with self.client.db as cursor:
                    cursor.execute('DELETE FROM role_channels WHERE channel_id = ?', (channel_id,))
                
                del self._tracked_channels[channel_id]
                del self._channel_types[channel_id]
                await interaction.response.send_message("Role reaction setup has been removed.", ephemeral=True)
            except Exception as e:
                logger.error(f"Failed to remove role reactions: {e}")
                await interaction.response.send_message("Failed to remove role reactions.", ephemeral=True)
                
        # Register commands
        self.client.tree.add_command(setup_roles)
        self.client.tree.add_command(remove_roles)
        self._cmd_registered = True
        
        # Rebuild channel data and sync reactions on startup
        for guild in self.client.guilds:
            for channel_id in list(self._tracked_channels.keys()):
                channel = guild.get_channel(channel_id)
                if channel:
                    await self._rebuild_channel_data(channel)
                    await self._sync_all_reactions(guild, channel)

    async def _sync_all_reactions(self, guild: discord.Guild, channel: discord.TextChannel):
        """Sync all reactions in a channel with member roles."""
        for message_id in self._tracked_channels.get(channel.id, []):
            if message_id not in self._role_messages:
                continue
                
            try:
                message = await channel.fetch_message(message_id)
                emoji_role_map = self._role_messages[message_id]
                
                # Get all reactions and their users
                for reaction in message.reactions:
                    if str(reaction.emoji) in emoji_role_map:
                        role_id = emoji_role_map[str(reaction.emoji)]
                        role = guild.get_role(role_id)
                        if not role:
                            continue
                            
                        # Get users who reacted
                        async for user in reaction.users():
                            if not user.bot:
                                member = guild.get_member(user.id)
                                if member:
                                    if role not in member.roles:
                                        await member.add_roles(role, reason="Role reaction sync")
                                        
                        # Remove role from users who haven't reacted
                        for member in role.members:
                            if not member.bot:
                                has_reaction = False
                                async for reactor in reaction.users():
                                    if reactor.id == member.id:
                                        has_reaction = True
                                        break
                                if not has_reaction:
                                    await member.remove_roles(role, reason="Role reaction sync")
                                    
            except Exception as e:
                logger.error(f"Failed to sync reactions for message {message_id}: {e}")
                
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Handle reaction adds."""
        if payload.user_id == self.client.user.id:
            return
            
        if payload.message_id in self._role_messages:
            emoji = str(payload.emoji)
            if emoji in self._role_messages[payload.message_id]:
                role_id = self._role_messages[payload.message_id][emoji]
                guild = self.client.get_guild(payload.guild_id)
                if guild:
                    member = guild.get_member(payload.user_id)
                    role = guild.get_role(role_id)
                    if member and role and role not in member.roles:
                        try:
                            await member.add_roles(role, reason="Role reaction add")
                        except Exception as e:
                            logger.error(f"Failed to add role {role.id} to member {member.id}: {e}")
                            
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """Handle reaction removes."""
        if payload.user_id == self.client.user.id:
            return
            
        if payload.message_id in self._role_messages:
            emoji = str(payload.emoji)
            if emoji in self._role_messages[payload.message_id]:
                role_id = self._role_messages[payload.message_id][emoji]
                guild = self.client.get_guild(payload.guild_id)
                if guild:
                    member = guild.get_member(payload.user_id)
                    role = guild.get_role(role_id)
                    if member and role and role in member.roles:
                        try:
                            await member.remove_roles(role, reason="Role reaction remove")
                        except Exception as e:
                            logger.error(f"Failed to remove role {role.id} from member {member.id}: {e}")
