' Silent launcher for TextWhisper. Runs pythonw.exe (no console window).
' Double-click to start, or right-click .lnk version to pin to taskbar.
Dim shell, fso, here, pyw, mainpy
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = here & "\venv\Scripts\pythonw.exe"
mainpy = here & "\main.py"

If Not fso.FileExists(pyw) Then
  MsgBox "TextWhisper venv not found." & vbCrLf & _
         "Run setup.bat first.", vbCritical, "TextWhisper"
  WScript.Quit 1
End If

shell.CurrentDirectory = here
' Run = 0 -> hide window. False -> don't wait for it to exit.
shell.Run """" & pyw & """ """ & mainpy & """", 0, False
