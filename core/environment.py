# core/environment.py
import datetime
import json
import ssl  # FIX: Added import
from pathlib import Path
from typing import Optional

try:
    import psutil
except ImportError:
    psutil = None

try:
    import urllib.request
except ImportError:
    urllib = None

from core.environment_service import EnvironmentService
from core.operational_state_service import OperationalStateService
from memory.state_owner import SharedStateOwner
from memory.stores import EventStore, TaskStore

def get_time_str() -> str:
    now = datetime.datetime.now()
    return now.strftime("%A, %B %d, %Y at %I:%M %p")

def get_system_load() -> str:
    if not psutil:
        return "N/A (psutil missing)"
    
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory().percent
        return f"CPU: {cpu:.0f}%, RAM: {ram:.0f}%"
    except Exception:
        return "N/A"

def get_weather(city: str = "Istanbul") -> str:
    """Fetches weather from Open-Meteo (no API key, highly reliable)."""
    # Istanbul Coordinates
    lat = "41.0082"
    lon = "28.9784"
    
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&timezone=auto"
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'PiperCore/1.0'})
        
        # Using context for SSL fix
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        with urllib.request.urlopen(req, timeout=5, context=context) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            
            current = data.get("current_weather", {})
            temp = current.get("temperature")
            code = current.get("weathercode", 0)
            
            # Simple WMO code interpretation
            if code == 0: desc = "Clear sky"
            elif code in [1, 2, 3]: desc = "Partly cloudy"
            elif code in [45, 48]: desc = "Fog"
            elif code in [51, 53, 55, 56, 57]: desc = "Drizzle"
            elif code in [61, 63, 65]: desc = "Rain"
            elif code in [71, 73, 75, 77]: desc = "Snow"
            elif code in [80, 81, 82]: desc = "Showers"
            elif code in [95, 96, 99]: desc = "Thunderstorm"
            else: desc = "Unknown"
            
            return f"{temp}°C, {desc}"

    except Exception as e:
        print(f"[ENV] Weather Error (Open-Meteo): {e}")
        return "N/A"

def get_upcoming_events(data_dir: Path, *, event_store: Optional[EventStore] = None) -> str:
    """Checks events.json for upcoming dates."""
    owner = SharedStateOwner.for_data_dir(data_dir)
    store = event_store or owner.event_store
    now = datetime.datetime.now()
    upcoming = []
    for item in store.upcoming(now=now):
        try:
            event_date = datetime.datetime.strptime(item["date"], "%Y-%m-%d")
        except Exception:
            continue
        delta = event_date - now
        if 0 <= delta.days <= 7:
            if delta.days == 0:
                upcoming.append(f"{item['name']} (Today)")
            else:
                upcoming.append(f"{item['name']} (in {delta.days} days)")
    return ", ".join(upcoming)

def get_active_tasks(data_dir: Path, *, task_store: Optional[TaskStore] = None) -> str:
    """Loads pending tasks."""
    owner = SharedStateOwner.for_data_dir(data_dir)
    store = task_store or owner.task_store
    pending = store.pending_names()
    if not pending:
        return ""
    bullet_list = "\n".join([f"- {name}" for name in pending])
    return f"Pending Tasks:\n{bullet_list}"
        
def get_environment_block(
    data_dir: Path,
    include_personal: bool = True,
    *,
    event_store: Optional[EventStore] = None,
    task_store: Optional[TaskStore] = None,
) -> str:
    """Compatibility wrapper over EnvironmentService."""
    owner = SharedStateOwner.for_data_dir(data_dir)
    if event_store is not None or task_store is not None:
        owner = SharedStateOwner(
            data_dir=owner.data_dir,
            task_store=task_store or owner.task_store,
            event_store=event_store or owner.event_store,
            knowledge_store=owner.knowledge_store,
            world_model_store=owner.world_model_store,
            situational_state_store=owner.situational_state_store,
            intent_state_store=owner.intent_state_store,
        )
    blocks = [EnvironmentService(owner).render_block()]
    if include_personal:
        operational = OperationalStateService(owner).render_block()
        if operational:
            blocks.append(operational)
    return "\n\n".join(block for block in blocks if block)
