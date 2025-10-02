Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Piper"
sh.Run """C:\Piper\venv\Scripts\pythonw.exe"" ""C:\Piper\mission_control\mission_control.py""", 0
