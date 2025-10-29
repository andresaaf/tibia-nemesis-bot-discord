import discord
import logging
from pathlib import Path
import os
from Database import Database

from features import RoleHandler, BossAnnouncer, PriceList, Checker

logging.basicConfig(level=logging.INFO)

class GolluxBot(discord.Client):
    def __init__(self):
        intents = discord.Intents().default()
        intents.members = True
        intents.message_content = True
        super().__init__(intents=intents)
        Path("db").mkdir(parents=True, exist_ok=True)
        self.db = Database()
        self.tree = discord.app_commands.CommandTree(self)
        # Shared application emoji cache for all features
        self._app_emojis = {}

        self.features = [
            PriceList(self),
            Checker(self),
            #BossAnnouncer(self),
            #RoleHandler(self),
        ]

    async def _safe_call(self, method_name, *args, **kwargs):
        for feature in self.features:
            fn = getattr(feature, method_name, None)
            if callable(fn):
                try:
                    await fn(*args, **kwargs)
                except Exception:
                    logging.exception("Error in feature %s.%s", type(feature).__name__, method_name)

    async def on_ready(self):
        logging.info('Connected. Username: %s | ID: %s', self.user.name, self.user.id)
        # Get emoji cache
        await self.warm_app_emojis()

        # Run on_ready for all features
        await self._safe_call('on_ready')

        # Sync the command tree once after all features registered their commands
        try:
            await self.tree.sync()
            logging.info("Synced app command tree (global).")
        except Exception:
            try:
                for g in list(self.guilds):
                    await self.tree.sync(guild=g)
                logging.info("Synced app command tree (per-guild fallback).")
            except Exception:
                logging.exception("Failed to sync app command tree.")

        for feature in self.features:
            logging.info("Loaded feature %s.", type(feature).__name__)

    async def on_message(self, message):
        if message.author.bot:
            return
        await self._safe_call('on_message', message)

    async def on_message_edit(self, before, after):
        if before.author.bot:
            return
        await self._safe_call('on_message_edit', before, after)

    async def on_raw_reaction_add(self, payload):
        if payload.user_id == self.user.id:
            return
        await self._safe_call('on_raw_reaction_add', payload)

    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
        await self._safe_call('on_reaction_add', reaction, user)

    async def on_raw_reaction_remove(self, payload):
        if payload.user_id == self.user.id:
            return
        await self._safe_call('on_raw_reaction_remove', payload)

    async def on_reaction_remove(self, reaction, user):
        if user.bot:
            return
        await self._safe_call('on_reaction_remove', reaction, user)

    async def on_voice_state_update(self, member, before, after):
        await self._safe_call('on_voice_state_update', member, before, after)

    async def on_interaction(self, interaction: discord.Interaction):
        # ignore interactions from bots (including ourselves)
        user = getattr(interaction, "user", None) or getattr(interaction, "author", None)
        if user and getattr(user, "bot", False):
            return

        # Detect a button click by presence of custom_id in interaction data
        data = getattr(interaction, "data", {}) or {}
        if "custom_id" not in data:
            return

        logging.info("Button clicked by %s: %s", getattr(user, "id", None), data.get("custom_id"))

        # Try to defer to give features time to respond; ignore errors if already responded
        try:
            await interaction.response.defer()
        except Exception:
            pass

        await self._safe_call('on_button_click', interaction)

    async def get_app_emoji(self, emoji_name: str):
        """Return an application emoji by name, fetching once and caching for all features."""
        if not emoji_name:
            return None
        # Ensure cache is populated using the shared warmer
        if not self._app_emojis:
            await self.warm_app_emojis()
        return self._app_emojis.get(emoji_name)

    async def warm_app_emojis(self):
        """Fetch and cache application emojis once at startup for all features."""
        if self._app_emojis:
            return
        try:
            app_emojis = await self.fetch_application_emojis()
            for e in app_emojis:
                self._app_emojis[e.name] = e
            logging.info("Warmed application emoji cache with %d emojis", len(self._app_emojis))
        except Exception:
            logging.exception("Failed to warm application emoji cache")

if __name__ == '__main__':
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        try:
            with open('token.txt', 'r') as f:
                token = f.read().strip()
        except FileNotFoundError:
            logging.error("No token found in environment or token.txt")
            raise SystemExit(1)

    bot = GolluxBot()
    bot.run(token)
