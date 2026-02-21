from IFeature import IFeature
import discord
from discord import app_commands
import logging
from typing import Dict, Optional, List, Tuple
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class Highscore(IFeature):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cmd_registered = False
        self._highscore_channels: Dict[int, int] = {}  # guild_id -> channel_id
        self._init_db()

    def _init_db(self):
        """Initialize database tables for highscore tracking."""
        try:
            with self.client.db as cursor:
                # Table to store guild -> highscore channel mapping
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS highscore_config (
                        guild_id INTEGER PRIMARY KEY,
                        channel_id INTEGER
                    )
                    """
                )
                
                # Table to store individual kill records
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS highscore_kills (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER NOT NULL,
                        finder_id INTEGER NOT NULL,
                        finder_name TEXT,
                        boss_name TEXT NOT NULL,
                        money_earned INTEGER NOT NULL,
                        participants_count INTEGER NOT NULL,
                        timestamp INTEGER NOT NULL
                    )
                    """
                )
                
                # Table to store aggregated statistics per user
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS highscore_stats (
                        guild_id INTEGER,
                        user_id INTEGER,
                        bosses_found INTEGER DEFAULT 0,
                        total_money INTEGER DEFAULT 0,
                        PRIMARY KEY (guild_id, user_id)
                    )
                    """
                )
                
                # Load existing config
                cursor.execute('SELECT guild_id, channel_id FROM highscore_config')
                for (guild_id, channel_id) in cursor.fetchall():
                    self._highscore_channels[guild_id] = channel_id
        except Exception:
            logger.exception("Failed to init highscore tables")

    async def on_ready(self):
        if self._cmd_registered:
            return
        
        try:
            @app_commands.command(name="highscorechannel", description="Set the channel for highscore leaderboard")
            @app_commands.describe(channel="Text channel to use for highscore leaderboard")
            async def set_highscore_channel(interaction: discord.Interaction, channel: discord.TextChannel):
                if interaction.guild is None:
                    await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
                    return
                
                member = interaction.user if isinstance(interaction.user, discord.Member) else interaction.guild.get_member(interaction.user.id)
                if member is None or not (member.guild_permissions.manage_guild or member.guild_permissions.administrator):
                    await interaction.response.send_message("You need Manage Server or Administrator permission to set the highscore channel.", ephemeral=True)
                    return
                
                try:
                    with self.client.db as cursor:
                        cursor.execute(
                            "INSERT OR REPLACE INTO highscore_config (guild_id, channel_id) VALUES (?, ?)",
                            (interaction.guild.id, channel.id)
                        )
                    self._highscore_channels[interaction.guild.id] = channel.id
                    await interaction.response.send_message(f"Highscore channel set to {channel.mention}", ephemeral=True)
                except Exception:
                    logger.exception("Failed to set highscore channel")
                    await interaction.response.send_message("Failed to set highscore channel.", ephemeral=True)

            self.client.tree.add_command(set_highscore_channel)
            self._cmd_registered = True
            
            # Post initial leaderboards on startup for any configured guilds
            for guild_id in self._highscore_channels.keys():
                try:
                    await self._update_leaderboard(guild_id)
                except Exception:
                    logger.exception(f"Failed to post initial leaderboard for guild {guild_id}")
        except Exception:
            logger.exception("Failed to register highscore commands")

    async def record_kill(
        self, 
        guild_id: int, 
        finder_id: int, 
        finder_name: str, 
        boss_name: str, 
        money_earned: int,
        participants: list
    ) -> None:
        """Record a boss kill and update stats."""
        try:
            timestamp = int(datetime.now(timezone.utc).timestamp())
            participants_count = len(participants)
            
            with self.client.db as cursor:
                # Insert kill record
                cursor.execute(
                    """
                    INSERT INTO highscore_kills 
                    (guild_id, finder_id, finder_name, boss_name, money_earned, participants_count, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, finder_id, finder_name, boss_name, money_earned, participants_count, timestamp)
                )
                
                # Update or insert user stats
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO highscore_stats 
                    (guild_id, user_id, bosses_found, total_money)
                    VALUES (
                        ?,
                        ?,
                        COALESCE((SELECT bosses_found FROM highscore_stats WHERE guild_id = ? AND user_id = ?), 0) + 1,
                        COALESCE((SELECT total_money FROM highscore_stats WHERE guild_id = ? AND user_id = ?), 0) + ?
                    )
                    """,
                    (guild_id, finder_id, guild_id, finder_id, guild_id, finder_id, money_earned)
                )
            
            logger.info(
                f"Highscore: recorded kill for {finder_name} ({finder_id}) - {boss_name} - {money_earned}k from {participants_count} participants"
            )
            
            # Update the leaderboard in the designated channel
            await self._update_leaderboard(guild_id)
        except Exception:
            logger.exception(f"Failed to record kill for guild {guild_id}")

    async def _get_highscore_embed(self, guild_id: int) -> discord.Embed:
        """Get the highscore leaderboard as an embed."""
        embed = discord.Embed(
            title="🏆 Boss Finder Highscore",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        
        try:
            with self.client.db as cursor:
                # Get top 50 by bosses found, then by money (fits as many as possible in one Discord embed)
                cursor.execute(
                    """
                    SELECT user_id, bosses_found, total_money
                    FROM highscore_stats
                    WHERE guild_id = ?
                    ORDER BY bosses_found DESC, total_money DESC
                    LIMIT 50
                    """,
                    (guild_id,)
                )
                
                stats = cursor.fetchall()
                
                if not stats:
                    embed.description = "No kills recorded yet. Get out there and find some bosses!"
                    return embed
                
                description_lines = []
                for idx, (user_id, bosses_found, total_money) in enumerate(stats, 1):
                    # Format money as kk (millions) or k (thousands)
                    if total_money >= 1000000:
                        money_str = f"{total_money // 1000000}kk"
                    elif total_money >= 1000:
                        money_str = f"{total_money // 1000}k"
                    else:
                        money_str = str(total_money)
                    description_lines.append(
                        f"**#{idx}** - <@{user_id}>\n"
                        f"   Bosses: **{bosses_found}** | Money: **{money_str}**"
                    )
                
                embed.description = "\n".join(description_lines)
        except Exception:
            logger.exception(f"Failed to get highscore embed for guild {guild_id}")
            embed.description = "Error loading highscore data."
        
        return embed

    async def _update_leaderboard(self, guild_id: int) -> None:
        """Update the highscore leaderboard message in the designated channel."""
        channel_id = self._highscore_channels.get(guild_id)
        if not channel_id:
            logger.debug(f"No highscore channel configured for guild {guild_id}")
            return
        
        try:
            guild = self.client.get_guild(guild_id)
            if not guild:
                return
            
            channel = guild.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                logger.warning(f"Highscore channel {channel_id} not found or not a text channel for guild {guild_id}")
                return
            
            embed = await self._get_highscore_embed(guild_id)
            
            # Try to find and update the most recent message from the bot
            try:
                async for message in channel.history(limit=1):
                    if message.author == self.client.user:
                        # Update existing bot message
                        await message.edit(embed=embed)
                        logger.debug(f"Updated highscore message in channel {channel_id}")
                        return
                
                # No existing bot message found, create a new one
                message = await channel.send(embed=embed)
                logger.info(f"Created new highscore message {message.id} in channel {channel_id} for guild {guild_id}")
            except Exception:
                logger.exception(f"Failed to post/update highscore message in channel {channel_id}")
        except Exception:
            logger.exception(f"Failed to update leaderboard for guild {guild_id}")
