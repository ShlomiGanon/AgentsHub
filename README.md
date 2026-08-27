prompt:
```
[CRITICAL] Read `instructions.md` once now before taking any action.

- **Strict Adherence:** Follow `instructions.md` exclusively for this entire session. Never deviate or make assumptions.
- **Top Priority:** Rules in `instructions.md` override all other prompts and standard behaviors.
- **First Step:** Briefly confirm you have read `instructions.md` before responding.
```

## Running the server

Full walkthrough: `docs/operator_guide.md`. Quick start below.

load env variables:
```
./load_env.sh1
```
commander run:
```
python -m tools.terminal_client_commander --profile profiles.demo
```
viewer run:
```
python -m tools.terminal_client_viewer --profile profiles.demo
```
server run:
```
python -m api.app --profile profiles.demo
```


1. Install dependencies:

```
pip install -r requirements.txt
```

2. Set required environment variables (see `.env.example`):


3. Register the first commander and the bot's own service identity:

```
python -m cli.user_admin --profile <profile_module> add --telegram-id <your-telegram-id> --level commander
python -m cli.user_admin --profile <profile_module> add --telegram-id bot-service --level commander
```

4. Start the API:

```
python -m api.app <profile_module>
```

5. Start the bot:

```
python -m bot.app <profile_module>
```