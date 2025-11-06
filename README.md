# Tibia Nemesis Discord Bot

We Vibin'
A Discord bot for tracking and announcing Tibia boss spawns with real-time spawn predictions. The bot integrates with a Go REST API that scrapes spawn data from tibia-statistic.com and applies intelligent filtering based on boss spawn patterns.

## Features

### 🎯 Boss Checker
- **Real-time spawn predictions** with percentage chances
- **Interactive button interface** to mark bosses as checked
- **Color-coded status indicators**:
  - ✅ Green (0-15 min) - Recently checked
  - ❕ Green (15-30 min) - Some time passed
  - ‼️ Blue (30-60 min) - Needs attention
  - ⏰ Red (60+ min) - Long overdue
- **Auto-refresh daily** at 10:00 CET (1 hour after API refresh)
- **Boss filtering** by spawn probability using inclusion_range logic
- **Multi-world support** - Configure different Tibia worlds per server

### 📢 Boss Announcements
- **Role mentions** for boss spawns
- **Coming/Ready signup buttons** with user lists
- **Recent check history** showing last 4 checks
- **Raid detection** with special formatting
- **Kill confirmation** button (creator/manage messages only)
- **Channel management** - Configure which channels allow announcements

### 💰 Price List
- Boss service pricing information
- Role-based pricing display

### 🔥 Fury Gate Tracker
- Track which Fury Gate city is active
- Supports 10 different cities
- Persists across bot restarts
- Updates Furyosa button label automatically

## Architecture

The bot follows a feature-based architecture with separation of concerns:

```
GolluxBot/
├── main.py                 # Bot entry point
├── features/
│   ├── Checker.py          # Boss checker with interactive UI
│   ├── CheckerUpdater.py   # API client for spawn data
│   ├── BossAnnouncer.py    # Boss announcement system
│   ├── Bosses.py           # Boss metadata (emoji, roles, prices)
│   ├── RoleHandler.py      # Role management
│   └── PriceList.py        # Service pricing
├── Database.py             # SQLite wrapper
└── tibia-nemesis-api/      # Go REST API (separate repo)
```

## Prerequisites

- **Python 3.8+**
- **Go 1.21+** (for API server)
- **Discord Bot Token** from [Discord Developer Portal](https://discord.com/developers/applications)
- **Tibia Nemesis API** running (see [tibia-nemesis-api/GETTING_STARTED.md](tibia-nemesis-api/GETTING_STARTED.md))

## Quick Start

### 1. Set Up the Go API

The bot requires the Tibia Nemesis API to be running. See [tibia-nemesis-api/GETTING_STARTED.md](tibia-nemesis-api/GETTING_STARTED.md) for detailed setup instructions.

### 2. Install Bot Dependencies

```powershell
pip install discord.py aiohttp pyyaml
```

### 3. Configure Discord Token

Create a `token.txt` file in the project root:
```
your_discord_bot_token_here
```

Or set environment variable:
```powershell
$env:DISCORD_TOKEN="your_token_here"
```

### 4. Set API URL (Optional)

```powershell
$env:API_BASE_URL="http://localhost:8080"
```

Default is `http://localhost:8080` if not set.

### 5. Run the Bot

```powershell
python main.py
```

## Discord Bot Setup

### Required Permissions

- Send Messages
- Embed Links
- Manage Messages (for checker updates)
- Use Slash Commands
- Add Reactions

### Bot Intents

The bot requires the following intents:
- `members` - For member information
- `message_content` - For message handling

## Configuration

### Per-Server Setup

1. **Set Boss Announcement Channel**
   ```
   /setupbosschannel
   ```
   Run in the channel where you want boss announcements.

2. **Configure Tibia World**
   ```
   /checkerworld <world>
   ```
   Choose from available worlds (Kalanta, etc.)

3. **Set Up Checker**
   ```
   /checker <channel>
   ```
   Register the channel for the interactive boss checker.

4. **Set Fury Gate City**
   ```
   /furygate <city>
   ```
   Select which Fury Gate city is active.

## Commands

| Command | Description | Permissions |
|---------|-------------|-------------|
| `/boss <role> [message]` | Announce a boss spawn | None |
| `/checkerworld <world>` | Set the Tibia world | Manage Server |
| `/checker <channel>` | Set checker channel | Manage Server/Channels |
| `/checkerrefresh` | Force refresh checker cache | Manage Server |
| `/setupbosschannel` | Register channel for announcements | Manage Channels |
| `/removebosschannel` | Unregister channel | Manage Channels |
| `/furygate <city>` | Set active Fury Gate city | None |

## Data Flow

```
tibia-statistic.com
        ↓
   Go API Server
   (see tibia-nemesis-api/README.md)
        ↓
   Discord Bot
   ├── Fetches filtered data at 10:00 CET
   ├── Updates checker UI
   └── Shows only spawnable bosses
```

## Database Schema

The bot uses SQLite with the following tables:

- `checker_config` - Channel configuration per guild
- `checker_worlds` - World selection per guild
- `checker_messages` - Message IDs for updates
- `boss_channels` - Registered announcement channels
- `furygate` - Fury Gate city per guild

## Boss Metadata

Boss configuration in `features/Bosses.py`:

```python
'Furyosa': {
    'name': 'Furyosa',
    'emoji': 'furyosa',
    'role': 'Furyosa',
    'price': 600000,
    'raid': False,
    'persist': False,
    'serversave': False,
}
```

**Fields:**
- `name` - Display name
- `emoji` - App emoji identifier
- `role` - Discord role name to mention
- `price` - Service price in gold
- `raid` - Whether this is a raid boss
- `persist` - Always show (ignores spawn predictions)
- `serversave` - Reset button state at server save
- `check_thresholds` - Custom timing for button colors (warn/alert/reset in minutes)

## How It Works

### Adding a New Boss

1. Add to `features/Bosses.py`:
   ```python
   'New Boss': {
       'name': 'New Boss',
       'emoji': 'newboss',
       'role': 'New Boss',
       'price': 200000,
   }
   ```

2. Add to appropriate area in `features/Checker.py` AREAS dict

## How It Works

The bot fetches pre-filtered boss spawn data from the Go API. The API handles:
- Web scraping from tibia-statistic.com
- Extracting spawn percentages and days since last kill
- Applying inclusion_range filtering based on spawn patterns
- Daily refresh at 09:00 CET

The bot handles:
- Displaying spawn data in Discord
- Interactive button interface for marking checks
- Boss announcements and signups
- Per-server configuration

For API details, see [tibia-nemesis-api/README.md](tibia-nemesis-api/README.md).

### Adding a New Feature

1. Create `features/YourFeature.py` extending `IFeature`
2. Implement `on_ready()` to register commands
3. Register in `main.py`:
   ```python
   self.features = [
       YourFeature(self),
       # ... other features
   ]
   ```

## Troubleshooting

### Bot shows no spawnable bosses

1. Check API is running: `curl http://localhost:8080/api/v1/status`
2. Verify world is configured: `/checkerworld`
3. Force refresh: `/checkerrefresh`
4. Check bot logs for API connection errors
5. Verify `API_BASE_URL` environment variable is correct

### Commands not appearing

1. Wait a few minutes for Discord to sync
2. Check bot has "Use Slash Commands" permission
3. Restart bot to re-register commands

### API Issues

See [tibia-nemesis-api/GETTING_STARTED.md](tibia-nemesis-api/GETTING_STARTED.md) for API troubleshooting.

## Performance Notes

- **Debounced updates**: Button clicks are debounced to prevent rate limits
- **Message caching**: Messages are cached to avoid repeated API calls
- **Smart refresh**: Only updates when visual state changes
- **Cached spawn data**: Percentage map cached per guild (60s TTL)

## License

MIT License - See LICENSE file for details

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Test your changes
4. Submit a pull request

## Support

For issues and questions:
- GitHub Issues: [Create an issue](https://github.com/andresaaf/tibia-nemesis-bot-discord/issues)
- Discord: Join our community server (link TBD)

## Credits

- Boss spawn data from [tibia-statistic.com](https://www.tibia-statistic.com/)
- Built with [discord.py](https://github.com/Rapptz/discord.py)
- API built with Go and [chi router](https://github.com/go-chi/chi)
