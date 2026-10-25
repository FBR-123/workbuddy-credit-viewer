' Enable "start with WorkBuddy" for the credit widget.
'
' What gets installed is a WATCHER (no window), NOT the widget itself:
'   logon            -> only the hidden watcher waits, nothing is shown
'   WorkBuddy starts -> the widget appears
'   WorkBuddy exits  -> the widget process ends by itself
'
' This file intentionally contains NO logic: the work is in autostart_on.bat,
' which can actually be executed and tested.
' ASCII only on purpose: every Chinese message is rendered by the Python side.
Option Explicit
Dim fso, sh, here
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run """" & here & "\autostart_on.bat""", 1, True
