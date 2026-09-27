# Aaruu Music — Production Telegram Music Bot

A production-ready Telegram Music Bot powered by Telegram's **native Rich Message API**, featuring structured Rich Message blocks, interactive Rich Message buttons, per-chat isolated queues, SQLite persistence, and a resilient 24/7 background worker built for Railway.

---

## 🎵 Reference Rich Message Player UI

The in-chat player uses native Telegram Bot API Rich Messages (`sendRichMessage`, `editMessageRichText`, `InputRichMessage`, `InputRichBlock`) with controls embedded directly inside the same message:

```
Aaruu Music

Command requested by @username

[ LARGE ALBUM ARTWORK ]

Song Title
Artist / Channel Name
AUDIO · 3:06
REQUESTED BY @username

0:00 ━━━━━━━━━●━━━━━━ 3:06

[ ↩ Replay ] [ ⏸ Pause ] [ ≫ Skip ]
             [ ☷ Queue · 0 ]
```

* **No Mini App**
* **No Web App**
* **No External Website**
* **No Fake Image Buttons**
* **No Ordinary InlineKeyboardMarkup Fallbacks**

---

## 📁 Project Structure

```
.
├── main.py                   # 24/7 Long-polling worker process entry point
├── requirements.txt          # Production Python dependencies
├── Procfile                  # Railway worker definition (worker: python3 main.py)
├── railway.toml              # Railway deployment configuration
├── .env.example              # Documented environment variables template
├── .gitignore                # Production ignore rules (secrets, databases, caches)
├── README.md                 # Complete documentation and setup manual
│
├── bot/
│   ├── __init__.py           # Bot engine package
│   ├── api.py                # Direct Telegram Bot API HTTPS client (Rich Messages)
│   ├── handlers.py           # Update router and exception boundary
│   ├── callbacks.py          # Fast callback query handler & stale-session guard
│   ├── commands.py           # Complete command handlers (/start, /play, /queue, etc.)
│   ├── rich_player.py        # Native InputRichMessage player & queue builder
│   ├── rich_help.py          # Interactive guide with RichMessageButton navigation
│   └── permissions.py        # Centralized Owner, Sudo, and Chat Admin authorization
│
├── player/
│   ├── __init__.py           # Audio player package
│   ├── models.py             # Track and PlayerState dataclasses
│   ├── manager.py            # Per-chat concurrency locks and playback coordinator
│   ├── queue.py              # Per-chat FIFO TrackQueue with capacity limits
│   ├── extractor.py          # yt-dlp media metadata resolver with fallback
│   └── voice_chat.py         # Voice chat streaming interface (ASSISTANT_SESSION)
│
├── database/
│   ├── __init__.py           # Database package
│   └── db.py                 # SQLite persistent store for chats, settings & history
│
├── utils/
│   ├── __init__.py           # Utilities package
│   ├── formatting.py         # Progress line renderer (render_progress) and time format
│   ├── escaping.py           # HTML / Rich entity escaping and sanitization
│   └── logging.py            # Structured logging with automatic secret masking
│
└── tests/
    ├── __init__.py           # Test package
    ├── test_progress.py      # Unit tests for time formatting and progress line
    ├── test_queue.py         # Unit tests for queue operations and capacity limits
    ├── test_callbacks.py     # Unit tests for callback size, state transitions & session protection
    └── test_rich_message.py  # Unit tests for InputRichMessage block payload schemas
```

---

## 🤖 1. BotFather Setup

Follow these exact steps to register your bot with Telegram:

1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Send `/newbot`.
3. Enter the display name:
   ```
   Aaruu Music
   ```
4. Choose a unique bot username ending in `bot` (for example: `AaruuMusicBot` or `Aaruu_Music_Play_Bot`).
5. Copy the generated `BOT_TOKEN` (looks like `7123456789:ABCdefGHIjklMNOpqrSTUvwxYZ123456789`).
6. **NEVER expose or commit your `BOT_TOKEN` publicly.**
7. The bot **automatically registers its command menu** with Telegram on startup using `setMyCommands`. However, you can also register it manually by sending `/setcommands` to `@BotFather`:
   ```text
   start - Start Aaruu Music
   help - Help & command guide
   play - Play or queue a song
   pause - Pause playback
   resume - Resume playback
   replay - Replay current song
   skip - Skip current song
   queue - Show queue & up next
   shuffle - Shuffle queued tracks
   stop - Stop playback
   clear - Clear queue
   loop - Repeat mode (off|track|queue)
   seek - Jump to timestamp
   volume - Set volume (1-100)
   nowplaying - Show active player
   settings - Show chat playback settings
   ping - Check bot response latency & uptime
   stats - View bot usage statistics
   broadcast - Broadcast to DMs and Groups (Owner only)
   block - Block user permanently from bot (Owner only)
   unblock - Unblock user (Owner only)
   blocked - List all blocked users (Owner only)
   admincache - Reload chat administrators cache
   ```

---

## ⚙️ 2. Environment Variables & Where to Get Them

Configure these variables in your `.env` file (for local development) or under the **Variables** tab in your Railway deployment dashboard:

| Variable | Required | Description | Where to Get It / Kaise Milega |
|---|---|---|---|
| `BOT_TOKEN` | **Yes** | Telegram Bot API token | **Telegram @BotFather**:<br>1. Open Telegram and search for `@BotFather`.<br>2. Send `/newbot`.<br>3. Choose name `Aaruu Music` and a unique username (ending in `bot`, e.g. `AaruuMusic_bot`).<br>4. BotFather will generate an API Token like `7123456789:AAH...`. Copy and paste it here. |
| `OWNER_ID` | **Yes** | Numeric Telegram ID of the Bot Owner | **Telegram @userinfobot** or **@MissRose_bot**:<br>1. Open Telegram and search for `@userinfobot`.<br>2. Press Start. It will instantly reply with your numeric `Id:` (e.g. `1234567890`).<br>3. This gives you exclusive owner rights to `/block`, `/unblock`, and `/blocked`. |
| `SUDO_USERS` | No | Co-admin Telegram user IDs | Space or comma-separated user IDs (e.g., `123456789,987654321`) obtained the same way via `@userinfobot`. |
| `DATABASE_URL` | No | SQLite database path | Default is `sqlite:///aaruu_music.db`. Automatically created. No external database setup needed! |
| `LOG_LEVEL` | No | Log verbosity | Set to `INFO` (recommended) or `DEBUG`. |
| `ASSISTANT_SESSION`| No | MTProto user session for voice streaming | Optional. If left empty, bot operates fully in standalone native Rich Message player & queue mode. |
| `YTDLP_COOKIES` | No | Path to YouTube cookies.txt | Optional. Only needed if YouTube blocks search in specific data center IP ranges. |

---

## 🚀 3. Railway Deployment (Worker Process)

Aaruu Music is engineered as a **long-running background worker** process that polls Telegram continuously. It does not require incoming webhooks, open ports, or public domain names.

### Step-by-Step Railway Guide:

1. Log into your [Railway](https://railway.com) account.
2. Click **New Project** → **Deploy from GitHub repo** (or upload the project folder).
3. In your Railway service dashboard, navigate to **Settings**:
   - Ensure the service is configured to use the worker entry point:
     ```text
     worker: python3 main.py
     ```
   - (Railway automatically detects the `Procfile` containing `worker: python3 main.py`).
4. Navigate to **Variables** and add:
   - `BOT_TOKEN`: Your BotFather token.
   - `OWNER_ID`: Your Telegram numeric user ID.
   - `SUDO_USERS`: Any additional admin IDs (optional).
   - `LOG_LEVEL`: `INFO`
5. Click **Deploy**.
6. Railway will start the worker. Inspect the **Deploy Logs** to see:
   ```text
   Starting Aaruu Music...
   Telegram API: OK
   Database: OK
   Rich Messages: enabled
   Player manager: OK
   Polling started
   Aaruu Music is running.
   ```

---

## 🔄 4. 24/7 Always-On Worker & 10-Minute Keep-Alive Heartbeat

Aaruu Music is designed to stay online without human intervention:

* **10-Minute Keep-Alive Heartbeat**: A dedicated background task (`run_keep_alive_heartbeat` in `main.py`) pings Telegram every 10 minutes (600s), actively warming TCP/TLS connections and keeping free/hosted cloud containers from entering idle sleep mode.
* **No Sleep / No Idle Exit**: The long-polling worker never exits due to silence or absence of messages.
* **Automatic Network Reconnection**: On transient connection errors, DNS drops, or Telegram gateway errors (502/503/504), the bot applies exponential backoff (1s, 2s, 4s, up to 30s) and automatically re-establishes polling.
* **429 Rate-Limit Handling**: Automatically reads Telegram's `parameters.retry_after`, sleeps the requested duration, and resumes without message drops.
* **Railway Process Restarts**: If Railway restarts or migrates the worker container, `main.py` automatically initializes SQLite, registers commands, and reconnects to Telegram cleanly.

---

## 📢 5. Broadcast & Admin Commands Guide

### Broadcast Announcements (`/broadcast` or `/gcast`)
*(Owner & Sudo Only)*
* **Broadcast to All Chats (DMs & Groups)**:
  ```text
  /broadcast Hello everyone! New music features are now live on Aaruu Music.
  ```
* **Broadcast to Private DMs only**:
  ```text
  /broadcast -user Hello private users!
  ```
* **Broadcast to Groups only**:
  ```text
  /broadcast -group Hello groups!
  ```
* **Broadcast Rich Media / Photos / Audio**:
  Reply to any Telegram message (with photo, sticker, audio, or formatted text) with `/broadcast` (or `/broadcast -user` / `/broadcast -group`), and Aaruu Music will clone and forward the exact message with complete formatting and media to all registered chats.

### Ping & Diagnostic Commands
* `/ping`: Measures round-trip API latency and reports uptime.
* `/stats`: Shows registered user count, group count, and total plays.
* `/shuffle`: Shuffles all queued songs in random order (Admins only).
* `/admincache`: Manually invalidates cached admin permissions if chat admins change.

---

## 🧪 6. Local Testing & Validation

You can validate the codebase locally before deploying:

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Verify Code Compilation**:
   ```bash
   python3 -m compileall .
   ```

3. **Execute the Unit Test Suite**:
   ```bash
   python3 -m unittest discover -s tests -v
   ```
   All 23 tests cover:
   - `test_progress.py`: Time formatting, timestamp clamping, unicode visual bar calculation.
   - `test_queue.py`: FIFO ordering, capacity boundaries, undo operation, clearing.
   - `test_callbacks.py`: Telegram 64-byte callback limit, session staleness rejection, play/pause/resume state transitions.
   - `test_rich_message.py`: Validation of native `InputRichMessage`, heading, photo, and button block schemas.
   - `test_block.py`: Permanent owner blocking, unblocking, and memory cache preload.
   - `test_features.py`: Queue shuffling, chat registration & stats aggregation, and admin cache management.

4. **Run Locally**:
   ```bash
   python3 main.py
   ```

---

## 🛠 7. Troubleshooting

* **"This player is no longer active."**:
  Buttons embed a unique `session_id`. If a track finishes, is skipped, or is stopped, buttons on earlier messages are disabled to prevent stale manipulation. Send `/nowplaying` or `/play` to launch an active player.
* **"Only chat administrators can skip tracks"**:
  In group chats, destructive controls (`/skip`, `/stop`, `/clear`, `/volume`, `/seek`) are restricted to chat admins, bot owner (`OWNER_ID`), and sudo users (`SUDO_USERS`).
* **YouTube extraction timeout or blocked**:
  If YouTube blocks anonymous cloud IP requests, supply a Netscape cookies file via the `YTDLP_COOKIES` environment variable.
* **Voice Chat streaming**:
  Telegram Bot API does not provide voice-chat audio frames. To stream audio directly into Telegram Voice Chats (VC), set `ASSISTANT_SESSION`. If omitted, the bot runs fully in standalone Rich Message and Queue mode.
