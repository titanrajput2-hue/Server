# Telegram Bulk Key Generator Bot

Python Telegram bot with private-channel verification, join-request approval on Verify,
one key per user per rolling 24 hours, and admin bulk key import.

## Files
- `main.py` — bot logic
- `requirements.txt` — Python dependencies
- `Procfile` — Railway worker start command

## Required environment variables
- `BOT_TOKEN`: token from BotFather
- `CHANNEL_ID`: numeric private channel ID, normally like `-1001234567890`
- `CHANNEL_INVITE_URL`: private invite link configured to create join requests
- `ADMIN_IDS`: comma-separated numeric Telegram user IDs allowed to add keys and view stats
- `DATABASE_PATH`: set to `/data/bot.db` when a Railway Volume is mounted at `/data`

## Telegram setup
1. Add the bot as an administrator in the private channel.
2. Give it permission to invite users / manage join requests (Telegram may label this as
   “Invite Users via Link” or similar).
3. Create a private invite link that requires approval / creates join requests, and set it
   as `CHANNEL_INVITE_URL`.
4. Ensure the bot can receive `chat_join_request` updates. This bot uses long polling.
5. Find your numeric Telegram user ID and set it in `ADMIN_IDS`.

Important: a bot cannot discover every pending join request on demand. Telegram sends a
join-request update to the bot; this code records it and approves it only after the user
presses Verify. The user must actually submit a join request first.

## Bulk-add keys
1. Send a normal Telegram message containing one key per line.
2. Reply to that message with `/addkeys`.
3. Only IDs in `ADMIN_IDS` can add keys.

Example key-list message:
```text
KEY-AAA-111
KEY-BBB-222
KEY-CCC-333
```

## Railway deployment
1. Push this folder to a GitHub repository.
2. In Railway, create a project and deploy from that GitHub repository.
3. Set the environment variables above in the Railway service's Variables section.
4. Add a Railway Volume mounted at `/data` so the SQLite database survives redeploys/restarts.
5. Confirm the service uses the `worker` process from `Procfile`, or set Start Command to:
   `python main.py`
6. Deploy and inspect logs for `Bot is starting with long polling.`

## Important limitations
- Railway free access, trial credits, and pricing can change. Do not assume 24/7 service is free.
- Without a persistent volume or external database, local SQLite data may be lost on redeploy.
- This is a starter implementation. Test with a spare channel and test keys before production use.
