using System;
using System.IO;
using System.Reflection;
class VerifyProxy {
 static void Main(string[] args){
   string dir=Path.GetDirectoryName(Path.GetFullPath(args[0]));
   AppDomain.CurrentDomain.AssemblyResolve+=(s,e)=>{string n=new AssemblyName(e.Name).Name;string p=Path.Combine(Path.Combine(Environment.GetEnvironmentVariable("TOPHEROES_GAME_ROOT") ?? "", "TopHeroes_Steam_Data", "Managed"),n+".dll");return File.Exists(p)?Assembly.LoadFrom(p):null;};
   var a=Assembly.LoadFrom(args[0]);var t=a.GetType("BestHTTP.HTTPManager",true);var prop=t.GetProperty("Proxy",BindingFlags.Public|BindingFlags.Static);
   Environment.SetEnvironmentVariable("TOPHEROES_PROXY",null);
   if(prop.GetValue(null,null)!=null)throw new Exception("Default behavior changed");
   Environment.SetEnvironmentVariable("TOPHEROES_PROXY","http://127.0.0.1:7897");
   object pxy=prop.GetValue(null,null);if(pxy.GetType().FullName!="BestHTTP.HTTPProxy")throw new Exception("Proxy type incorrect");
   object address=pxy.GetType().GetProperty("Address").GetValue(pxy,null);
   if(address.ToString()!="http://127.0.0.1:7897/")throw new Exception("Proxy endpoint incorrect");
   Console.WriteLine("PROXY_OBJECT_OK "+address);
   Environment.SetEnvironmentVariable("TOPHEROES_PROXY",null);
   if(prop.GetValue(null,null)!=null)throw new Exception("Fallback behavior changed");
   Console.WriteLine("NO_ENV_FALLBACK_OK");
 }
}
