# Recording the phone segment

What I need from you: one screen recording from your phone, portrait, 60 to 90 seconds.
Send it as a file (not a Telegram/WhatsApp compressed forward - use "send as file" or
AirDrop/USB). I will crop it into a phone frame and splice it into the demo section.

## Before you record (laptop)

Two terminals. The bot has to be running or the phone gets no answers.

Terminal 1 - the bot:
```bash
cd /home/prashant/KeeperHub/almanak-keeperhub && source .venv/bin/activate
set -a && . ./.env && set +a
export ALMANAK_BASE_SEPOLIA_RPC_URL=https://sepolia.base.org RPC_URL_BASE=https://sepolia.base.org ALMANAK_KEEPERHUB_CHAIN=base_sepolia
cd demos/metamorpho_base_sepolia && almanak-keeperhub bot -d . --chain base_sepolia
```
Leave it running. It prints when it is polling.

Terminal 2 - check the wallet has test USDC (the /tick step spends 5):
```bash
cd /home/prashant/KeeperHub/almanak-keeperhub && scripts/track_a.sh --status
```
If org USDC is under 6000000 (6 USDC), run `python scripts/redeem_all.py` first.

## On the phone

Open the chat with @KeeperHubAlmanakBot. Clear old messages so the screen starts clean
(long-press > delete, or just scroll to the bottom). Start the phone's screen recorder.
Then send these, one at a time, and WAIT for each reply to fully appear before the next.
Do not rush - the pauses are what make it readable.

1. `/status`
   Shows the wallet, chain, counts, keeper. Hold 4 seconds.

2. `/executions 3`
   The last three real executions with links. Hold 4 seconds.

3. `/verify 0x70b453be43f4b8c4d4` -- wait, use the FULL hash from the /executions
   reply: tap it, copy it, paste after /verify. This asks KeeperHub for its verdict and
   decodes the receipt to name who acted. Hold 5 seconds - this is the best reply.

4. `/simulate`
   A dry run of one strategy tick through KeeperHub. Nothing is broadcast. Takes 10-20s
   to answer - keep recording, do not tap anything. Hold 4 seconds on the reply.

5. `/tick`
   It asks for /confirm. Hold 2 seconds so the confirmation prompt is visible.

6. `/confirm`
   A REAL strategy tick: approve + deposit of 5 test USDC, broadcast through KeeperHub,
   from your phone. Takes 20-40 seconds. Keep recording the whole time. The reply lists
   the transaction hashes and "verified". Hold 6 seconds on it. This is the money shot.

7. (optional) `/demo duplicate`
   The idempotency demo: same intent sent twice, second one blocked. Hold 4 seconds.

Stop recording.

## What NOT to do

- Do not type while a reply is still loading.
- Do not switch apps mid-recording.
- Keep the phone in portrait. Keep notifications off (Do Not Disturb).
- If a command errors, just send it again - I can cut the failed one.

## Then

Send me the file and tell me which step the /confirm reply lands at (roughly, in seconds).
I will do the rest: phone frame, splice, re-time the narration around it, re-mix.
