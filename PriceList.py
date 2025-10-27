from IFeature import IFeature
import discord
from discord import app_commands
import logging
from Bosses import BOSSES

logger = logging.getLogger(__name__)

# Discord message character limit
MAX_MESSAGE_LENGTH = 2000

class PriceList(IFeature):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cmd_registered = False
        # channel_id -> list of message_ids
        self._price_channels = {}
        self._init_db()
        
    def _init_db(self):
        """Initialize database tables and load existing data."""
        with self.client.db as cursor:
            # Create table for price list channels
            cursor.execute('''CREATE TABLE IF NOT EXISTS price_channels
                            (channel_id INTEGER PRIMARY KEY)''')
            
            # Load existing channels
            cursor.execute('SELECT channel_id FROM price_channels')
            for (channel_id,) in cursor.fetchall():
                self._price_channels[channel_id] = []
    
    def _format_price(self, price: int) -> str:
        """Format price with k/kk suffix."""
        if price >= 1000000:
            return f"{price // 1000000}kk"
        elif price >= 1000:
            return f"{price // 1000}k"
        else:
            return str(price)
    
    def _build_price_list_content(self) -> list[str]:
        """Build price list content, splitting into multiple messages if needed."""
        # Collect all bosses with prices
        boss_prices = []
        for boss_key, boss_data in BOSSES.items():
            if 'price' in boss_data and boss_data['price']:
                name = boss_data['name']
                price = boss_data['price']
                price_str = self._format_price(price)
                boss_prices.append((name, price_str))
        
        # Sort alphabetically by name
        boss_prices.sort(key=lambda x: x[0])
        
        # Build message content as a table
        messages = []
        header = "**Boss Price List**\n```\nBoss Name                        | Price\n" + "-" * 45 + "\n"
        current_lines = [header]
        current_length = len(header)
        
        for name, price in boss_prices:
            # Format as table row with fixed width columns
            line = f"{name:<32} | {price}\n"
            line_length = len(line)
            
            # Check if adding this line would exceed the limit
            if current_length + line_length + 3 > MAX_MESSAGE_LENGTH - 100:  # +3 for closing ```
                # Close the code block and start a new message
                current_lines.append("```")
                messages.append("".join(current_lines))
                current_lines = ["```\n" + line]
                current_length = len(current_lines[0])
            else:
                current_lines.append(line)
                current_length += line_length
        
        # Close the code block and add the last message
        if current_lines:
            current_lines.append("```")
            messages.append("".join(current_lines))
        
        return messages
    
    async def _update_price_list(self, channel: discord.TextChannel):
        """Update the price list in the given channel."""
        try:
            # Get new content
            new_messages = self._build_price_list_content()
            
            # Fetch existing messages from the channel
            existing_messages = []
            async for message in channel.history(limit=100):
                if message.author == self.client.user and message.content.startswith("**Boss Price List**"):
                    existing_messages.append(message)
            existing_messages.reverse()  # Oldest first
            
            # Update or create messages as needed
            message_ids = []
            
            # Update existing messages
            for i, content in enumerate(new_messages):
                if i < len(existing_messages):
                    # Update existing message if content changed
                    if existing_messages[i].content != content:
                        await existing_messages[i].edit(content=content)
                    message_ids.append(existing_messages[i].id)
                else:
                    # Create new message
                    msg = await channel.send(content)
                    message_ids.append(msg.id)
            
            # Delete extra messages if we now need fewer
            for i in range(len(new_messages), len(existing_messages)):
                try:
                    await existing_messages[i].delete()
                except:
                    pass
            
            # Update tracked message IDs
            self._price_channels[channel.id] = message_ids
            
            logger.info(f"Updated price list in channel {channel.id} with {len(message_ids)} message(s)")
            
        except Exception as e:
            logger.error(f"Failed to update price list in channel {channel.id}: {e}")
    
    async def on_ready(self):
        """Called when the bot is ready."""
        if not self._cmd_registered:
            @app_commands.command(name="setuppricelist", description="Setup price list in the current channel")
            async def setup_pricelist(interaction: discord.Interaction):
                if not interaction.guild or not interaction.channel:
                    await interaction.response.send_message("This command must be used in a server channel.", ephemeral=True)
                    return
                    
                # Check permissions
                member = interaction.guild.get_member(interaction.user.id)
                if not member or not member.guild_permissions.manage_channels:
                    await interaction.response.send_message("You need the Manage Channels permission to run this command.", ephemeral=True)
                    return
                    
                # Check if channel is already registered
                if interaction.channel.id in self._price_channels:
                    await interaction.response.send_message("This channel is already set up for price list. Use /removepricelist first if you want to reconfigure it.", ephemeral=True)
                    return
                    
                await interaction.response.send_message("Setting up price list...", ephemeral=True)
                
                # Build and send price list
                messages = self._build_price_list_content()
                message_ids = []
                
                for content in messages:
                    msg = await interaction.channel.send(content)
                    message_ids.append(msg.id)
                
                # Store in memory and database
                self._price_channels[interaction.channel.id] = message_ids
                
                with self.client.db as cursor:
                    cursor.execute('INSERT OR REPLACE INTO price_channels (channel_id) VALUES (?)', 
                                (interaction.channel.id,))
                
                logger.info(f"Price list set up in channel {interaction.channel.id} with {len(message_ids)} message(s)")
            
            @app_commands.command(name="removepricelist", description="Remove price list from the current channel")
            async def remove_pricelist(interaction: discord.Interaction):
                if not interaction.guild or not interaction.channel:
                    await interaction.response.send_message("This command must be used in a server channel.", ephemeral=True)
                    return
                    
                # Check permissions
                member = interaction.guild.get_member(interaction.user.id)
                if not member or not member.guild_permissions.manage_channels:
                    await interaction.response.send_message("You need the Manage Channels permission to run this command.", ephemeral=True)
                    return
                    
                channel_id = interaction.channel.id
                if channel_id not in self._price_channels:
                    await interaction.response.send_message("No price list is set up in this channel.", ephemeral=True)
                    return
                    
                # Delete messages
                try:
                    for msg_id in self._price_channels[channel_id]:
                        try:
                            msg = await interaction.channel.fetch_message(msg_id)
                            await msg.delete()
                        except:
                            pass
                    
                    # Clean up database and memory
                    with self.client.db as cursor:
                        cursor.execute('DELETE FROM price_channels WHERE channel_id = ?', (channel_id,))
                    
                    del self._price_channels[channel_id]
                    
                    await interaction.response.send_message("Price list removed from this channel.", ephemeral=True)
                    logger.info(f"Price list removed from channel {channel_id}")
                    
                except Exception as e:
                    await interaction.response.send_message(f"Error removing price list: {e}", ephemeral=True)
                    logger.error(f"Failed to remove price list from channel {channel_id}: {e}")
            
            self.client.tree.add_command(setup_pricelist)
            self.client.tree.add_command(remove_pricelist)
            self._cmd_registered = True
        
        # Update all registered channels on startup
        for channel_id in list(self._price_channels.keys()):
            try:
                channel = self.client.get_channel(channel_id)
                if channel:
                    await self._update_price_list(channel)
                else:
                    logger.warning(f"Price channel {channel_id} not found, removing from database")
                    with self.client.db as cursor:
                        cursor.execute('DELETE FROM price_channels WHERE channel_id = ?', (channel_id,))
                    del self._price_channels[channel_id]
            except Exception as e:
                logger.error(f"Failed to update price list for channel {channel_id}: {e}")
