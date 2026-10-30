# Apartment Screening Agent

Shortlists NoBroker listings against your preferences, calls the owners with a Vapi voice agent, and books viewings into slots you choose.

## How it works

1. **You** unlock owner contacts on NoBroker and add listings to a CSV (see `listings_sample.csv`).
2. `shortlist.py` filters and ranks them: BHK, floor range, target localities and rent ceiling from config.yaml. Power backup and mid-range floors score higher.
3. `caller.py` calls each shortlisted owner (10am–7pm IST only), checks availability and your lease terms (lease length, notice period, lock-in), then books one of your viewing slots.
4. Every call lands in `results.csv` with a summary, the answers, and any booked slot. Booked slots are removed so nothing gets double-booked.

The agent introduces itself as calling on your behalf, doesn't use a personal name, and answers truthfully if an owner asks whether it's an AI.

## Setup (one time, ~20 minutes)

```bash
pip install -r requirements.txt
```

1. **Vapi account**: sign up at vapi.ai, copy your private API key.
2. **Phone number**: Vapi's free numbers may not be able to dial India. The reliable path is a Twilio number with India enabled under *Voice → Geo Permissions*, imported into Vapi (*Phone Numbers → Import*). Copy the phone number ID Vapi shows.
3. Set your keys:
   ```bash
   export VAPI_API_KEY="..."
   export VAPI_PHONE_NUMBER_ID="..."
   ```
4. Edit `config.yaml`: rent ceiling, viewing slots, voice.
5. Create the agent:
   ```bash
   python caller.py setup
   ```
   Re-run `setup` any time you change `config.yaml` or `prompts.py`.

## Daily use

```bash
python shortlist.py listings.csv     # rank listings, writes shortlist.csv
python caller.py run --dry-run       # see who would be called
python caller.py run --limit 1       # first time: test with one call (try your own number)
python caller.py run                 # call everyone pending
```

Check `results.csv` for booked viewings. Statuses in `shortlist.csv`:
`pending` · `viewing_booked` · `callback` · `no_answer` · `not_a_fit` · `error`.
Set a row back to `pending` to retry it.

## Listing CSV columns

`listing_url, title, locality, bhk, floor, rent, power_backup, owner_name, owner_phone, notes`

`power_backup`: `yes` / `no` / `unknown`. Phone numbers can be 10-digit Indian mobiles; `+91` is added automatically.

## Before the first real call

Put your own number in a test row and call yourself once. Listen for tone and pacing, and tweak `prompts.py` if anything sounds off.
