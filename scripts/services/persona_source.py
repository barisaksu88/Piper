# CONTRACT — Persona (Single Source)
# - Load background.md + traits.ini into dict {background:str, traits:dict}.
# - No env or YAML unless legacy flag is enabled via config.

import configparser
from typing import Dict, Any

def load_persona(background_file: str, traits_file: str, legacy_yaml: bool = False) -> Dict[str, Any]:
    # BC01: ignore legacy unless explicitly enabled (still no envs)
    background = ""
    try:
        with open(background_file, "r", encoding="utf-8") as f:
            background = f.read()
    except FileNotFoundError:
        background = ""

    traits = {
        "sarcasm": 40, "professionalism": 30, "warmth": 60,
        "brevity": 40, "directness": 70, "humor": 35, "honorific": "sir",
    }
    cp = configparser.ConfigParser()
    try:
        with open(traits_file, "r", encoding="utf-8") as f:
            cp.read_file(f)
            if cp.has_section("traits"):
                for k, v in cp.items("traits"):
                    traits[k] = v if k == "honorific" else int(v)
    except FileNotFoundError:
        pass

    return {"background": background, "traits": traits}
