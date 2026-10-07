-- maxfast.ms : loader + helpers for MaxFast (.NET) - safe to fileIn many times.
global MF_NS, MF_Info, MF_Geo, MF_Rays, MF_Grass, MF_Jobs
global mfEnsure, mfInts, mfFloats, mfStrings
global MF_DIR = @"C:\Users\sapfi\Desktop\VibeScripts\maxfast\"
global MF_BUILD = "MaxFast_b1"
fn mfEnsure = (
  if MF_NS != MF_BUILD or MF_Info == undefined do (
    dotNet.loadAssembly (MF_DIR + MF_BUILD + ".dll")
    MF_Info = dotNetClass (MF_BUILD + ".Info")
    MF_Geo = dotNetClass (MF_BUILD + ".Geo")
    MF_Rays = dotNetClass (MF_BUILD + ".Rays")
    MF_Grass = dotNetClass (MF_BUILD + ".Grass")
    MF_Jobs = dotNetClass (MF_BUILD + ".Jobs")
    MF_NS = MF_BUILD
  )
  MF_Info.Version()
)
fn mfInts arr = dotNet.ValueToDotNetObject (for a in arr collect (a as integer)) (dotNetClass "System.Int32[]")
fn mfFloats arr = dotNet.ValueToDotNetObject (for a in arr collect (a as float)) (dotNetClass "System.Single[]")
fn mfStrings arr = dotNet.ValueToDotNetObject (for a in arr collect (a as string)) (dotNetClass "System.String[]")
