from __future__ import annotations

import datetime
import json
import ssl
import urllib.request
from dataclasses import dataclass, field

try:
    import psutil
except ImportError:
    psutil = None

from memory.state_owner import SharedStateOwner


@dataclass(frozen=True)
class EnvironmentSnapshot:
    time: str
    weather: str
    system_load: str


class EnvironmentService:
    def __init__(self, state_owner: SharedStateOwner, *, weather_city: str = "Istanbul") -> None:
        self.state_owner = state_owner
        self.weather_city = weather_city

    @staticmethod
    def _time_str() -> str:
        now = datetime.datetime.now()
        return now.strftime("%A, %B %d, %Y at %I:%M %p")

    @staticmethod
    def _system_load() -> str:
        if not psutil:
            return "N/A (psutil missing)"
        try:
            cpu = psutil.cpu_percent(interval=0.1)
            ram = psutil.virtual_memory().percent
            return f"CPU: {cpu:.0f}%, RAM: {ram:.0f}%"
        except Exception:
            return "N/A"

    def _weather(self) -> str:
        lat = "41.0082"
        lon = "28.9784"
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}&current_weather=true&timezone=auto"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PiperCore/1.0"})
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=5, context=context) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            current = data.get("current_weather", {})
            temp = current.get("temperature")
            code = current.get("weathercode", 0)
            if code == 0:
                desc = "Clear sky"
            elif code in [1, 2, 3]:
                desc = "Partly cloudy"
            elif code in [45, 48]:
                desc = "Fog"
            elif code in [51, 53, 55, 56, 57]:
                desc = "Drizzle"
            elif code in [61, 63, 65]:
                desc = "Rain"
            elif code in [71, 73, 75, 77]:
                desc = "Snow"
            elif code in [80, 81, 82]:
                desc = "Showers"
            elif code in [95, 96, 99]:
                desc = "Thunderstorm"
            else:
                desc = "Unknown"
            return f"{temp}°C, {desc}"
        except Exception as exc:
            print(f"[EnvironmentService] Weather Error (Open-Meteo): {exc}")
            return "N/A"

    def snapshot(self) -> EnvironmentSnapshot:
        return EnvironmentSnapshot(
            time=self._time_str(),
            weather=self._weather(),
            system_load=self._system_load(),
        )

    def render_block(self) -> str:
        snapshot = self.snapshot()
        # Plain labeled text — small local models (Qwen3.5-9B) fail to reliably
        # extract values from a JSON blob.  Labeled lines are read directly.
        lines = [
            "[ENVIRONMENT]",
            f"Today: {snapshot.time}",
            f"Weather: {snapshot.weather}",
            f"System load: {snapshot.system_load}",
        ]
        return "\n".join(lines)
