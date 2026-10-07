@echo off
set D=C:\Users\sapfi\Desktop\VibeScripts\maxfast
set MR=C:\Program Files\Autodesk\3ds Max 2023
set B=%1
"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:library /platform:x64 /optimize+ /out:"%D%\MaxFast_b%B%.dll" /r:"%MR%\Autodesk.Max.dll" /r:"%MR%\ManagedServices.dll" /r:System.Windows.Forms.dll "%D%\MaxFast_b%B%.cs" > "%D%\build_log.txt" 2>&1
echo EXIT %errorlevel% >> "%D%\build_log.txt"
