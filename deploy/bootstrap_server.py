"""Writes server secrets from a JSON file, then deletes the file. Prints no secret values."""

import json
import os
import time

os.umask(0o077)
source = ".bootstrap.json"
values = json.load(open(source))
with open(".env", "w") as file:
    file.write(f"DISCORD_BOT_TOKEN={values['token']}\nOWNER_ID={values['owner']}\nAGENT_MODEL=gpt-5.6-luna\n")
with open("data/codex-auth.json", "w") as file:
    json.dump({"access": values["access"], "refresh": "", "expires": values["exp"]}, file)
os.remove(source)
print("model token hours left", round((values["exp"] - time.time()) / 3600, 1))
