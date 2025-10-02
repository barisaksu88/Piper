# entries/app_wake_entry.py
# Ensure project root in path
from core.startup import print_banner
from core.state_defs import CoreApp
from core.flags import read

def main():
    # Print unified startup banner
    print_banner(file_label=__file__)

    app = CoreApp()
    app.start()

    try:
        while True:
            user_input = input("\nYou: ").strip()
            if user_input.lower() in ("exit", "quit"):
                # Graceful shutdown
                print("[CORE] sandbox=exit")
                app.stop()
                break
            elif user_input:
                # Forward input to app if transitions are active
                flags = read()
                if "PIPER_CORE_FORWARD_INPUT" in flags.active_keys():
                    print(f"[CORE] fwd_input text='{user_input}' -> state={app.state.name}")
    except KeyboardInterrupt:
        print("\n[CORE] interrupted, shutting down")
        app.stop()

if __name__ == "__main__":
    main()