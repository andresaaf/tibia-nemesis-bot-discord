# Getting Started with Tibia Nemesis Discord Bot

This guide shows how to set up and run the Discord bot.

## Prerequisites

1. **Python 3.8+** installed
2. **Tibia Nemesis API** running (see [API setup](tibia-nemesis-api/GETTING_STARTED.md))
3. **Discord Bot Token** from [Discord Developer Portal](https://discord.com/developers/applications)

## Quick Start

### 1. Install Dependencies

```powershell
# Install Python dependencies
pip install discord.py aiohttp pyyaml
```

### 2. Configure Bot Token

Add your Discord bot token to `token.txt`:
```
your_discord_bot_token_here
```

### 3. Set API URL

```powershell
# Point to your API server
$env:API_BASE_URL="http://localhost:8080"
```

### 4. Run the Bot

```powershell
python main.py
```

## Bot Features

### Boss Checker
- `/checkerworld <world>` - Configure which Tibia world to track
- View spawnable bosses with real-time percentages
- Click buttons to mark bosses as checked
- Automatic daily refresh at 10:00 CET

### Boss Announcements
- `/boss <boss_name>` - Announce a boss spawn
- Sign up as "Coming" or "Ready"
- View recent boss checks (up to 4 latest)
- Mark boss as killed (creator/manage messages only)

### Fury Gate
- `/furygate <city>` - Set Carlin or Thais as active Fury Gate
- Persists across bot restarts

## Configuration

### Environment Variables

- `DISCORD_TOKEN` - Bot token (or use `token.txt`)
- `API_BASE_URL` - API server URL (default: `http://localhost:8080`)

Example:
```powershell
$env:DISCORD_TOKEN="your_token_here"
$env:API_BASE_URL="https://api.yourserver.com"
```

### Database

The bot uses SQLite (`gollux.db`) to store:
- Checker configuration per guild
- Checker message IDs for updates
- Fury Gate selections
- Boss check timestamps

## Discord Bot Permissions

Required bot permissions:
- Send Messages
- Embed Links
- Manage Messages (for checker updates)
- Use Slash Commands
- Add Reactions

## Integration with API

The bot fetches boss spawn data from the API:
- **API refreshes at 09:00 CET** - Scrapes spawn data from tibia-statistic.com
- **Bot refreshes at 10:00 CET** - Fetches filtered data from API
- API applies `inclusion_range` filtering based on days since last kill
- Bot displays only spawnable bosses with accurate percentages

## Troubleshooting

### Bot not connecting

1. Verify `token.txt` contains valid Discord token
2. Check bot has correct permissions in Discord server
3. Ensure bot is invited to your server

### No spawnable bosses shown

1. Check API is running: `curl http://localhost:8080/api/v1/status`
2. Verify `API_BASE_URL` environment variable is correct
3. Check bot logs for API connection errors
4. Ensure API has data: `curl "http://localhost:8080/api/v1/spawnables?world=Antica"`

### Commands not appearing

1. Wait a few minutes for Discord to sync slash commands
2. Check bot has "Use Slash Commands" permission
3. Restart bot to re-register commands

### Database locked errors

1. Close all bot instances
2. Delete `gollux.db` to reset (loses configuration)
3. Restart bot

## Development

### Project Structure

```
GolluxBot/
├── main.py              # Bot entry point
├── features/
│   ├── BossAnnouncer.py # Boss announcement command
│   ├── Checker.py       # Boss checker with buttons
│   ├── CheckerUpdater.py # API client for spawn data
│   ├── Bosses.py        # Boss metadata (emoji, roles, prices)
│   ├── RoleHandler.py   # Role ping management
│   └── IFeature.py      # Feature interface
├── Database.py          # SQLite wrapper
└── token.txt            # Discord token
```

### Adding New Features

1. Create new feature class in `features/`
2. Inherit from `IFeature`
3. Implement `on_load(bot)` to register commands
4. Register in `main.py`

### Modifying Boss Data

Edit `features/Bosses.py` to update:
- Boss display names
- Discord emojis
- Role mentions
- Service prices

**Note**: `inclusion_range` filtering is now handled by the API, not the bot.

## Next Steps

- Configure worlds with `/checkerworld`
- Test boss announcements with `/boss`
- Set up Fury Gate with `/furygate`
- Monitor daily refresh at 10:00 CET
