' Start the widget right now, without a console window.
'
' This file intentionally contains NO logic: python discovery and every decision
' live in start_widget.bat, which can actually be executed and tested.
' (In some environments the Windows script host is blocked outright, so keeping
' this file trivially simple is the only way to be reasonably sure it works.)
Option Explicit
Dim fso, sh, here
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run """" & here & "\start_widget.bat""", 0, False
