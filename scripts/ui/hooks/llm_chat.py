# CONTRACT — Streaming Consumer (UI Glue Only)
# Glue only:
#  - Call services.llm_client.LLMClient.stream_generate(...)
#  - Incrementally append tokens into a single assistant turn in state_store.
#  - Trigger refresh_ui() to paint accumulating text.
# Forbidden:
#  - Provider imports
#  - Persona/memory logic
#  - Subprocess management

# Example usage (inside your UI hook):
# from scripts.services.llm_client import LLMClient
# _client = LLMClient()
# def ui_stream_reply(recent_turns):
#     buf = []
#     for chunk in _client.stream_generate(recent_turns):
#         buf.append(chunk)
#         state_store.update_assistant_text("".join(buf))
#         refresh_ui()
# # Call _client.stop() on cancel button.
