' Disable "start with WorkBuddy" for the credit widget.
'
' Removes the logon entry and stops the hidden watcher, so nothing of this
' widget runs in the background anymore. The widget can still be started
' manually by double-clicking start_widget.vbs.
'
' This file intentionally contains NO logic: the work is in autostart_off.bat,
' which can actually be executed and tested.
' ASCII only on purpose: Chinese messages come from the Python side.
Option Explicit
Dim fso, sh, here
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run """" & here & "\autostart_off.bat""", 1, True
