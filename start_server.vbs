' Start the vocab book server silently (no console window)
Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = "D:\JProjects\vocabBook"
ws.Run "cmd /c python -m streamlit run app.py --server.port 8511 --server.headless true", 0, False
