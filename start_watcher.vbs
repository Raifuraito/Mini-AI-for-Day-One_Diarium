' start_watcher.vbs -- runs the journal watcher SILENTLY (no window) and
' restarts it automatically if it ever stops. Double-click to start it now, or
' put a shortcut to this file in your Startup folder (Win+R -> shell:startup)
' to run it hands-off at every login. (The setup wizard's "Start on login"
' button does that part for you automatically.)
Dim sh, fso, here
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
Do
    sh.Run "pythonw watcher.py", 0, True
    WScript.Sleep 5000
Loop
