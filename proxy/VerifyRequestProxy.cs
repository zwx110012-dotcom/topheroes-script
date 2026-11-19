using System;
using System.IO;
using System.Reflection;
using System.Runtime.Serialization;

class VerifyRequestProxy {
    static void Main(string[] args) {
        AppDomain.CurrentDomain.AssemblyResolve+=(s,e)=>{
            string p=Path.Combine(Path.Combine(Environment.GetEnvironmentVariable("TOPHEROES_GAME_ROOT") ?? "", "TopHeroes_Steam_Data", "Managed"),new AssemblyName(e.Name).Name+".dll");
            return File.Exists(p)?Assembly.LoadFrom(p):null;
        };
        var asm=Assembly.LoadFrom(args[0]);
        var type=asm.GetType("BestHTTP.HTTPRequest",true);
        var proxy=type.GetProperty("Proxy",BindingFlags.Public|BindingFlags.Instance);
        var request=FormatterServices.GetUninitializedObject(type);
        Environment.SetEnvironmentVariable("TOPHEROES_PROXY",null);
        if(proxy.GetValue(request,null)!=null)throw new Exception("No-env null behavior changed");
        Environment.SetEnvironmentVariable("TOPHEROES_PROXY","http://127.0.0.1:10909");
        proxy.SetValue(request,null,null); // exact null assignment in new game downloader
        object fallback=proxy.GetValue(request,null);
        var address=fallback.GetType().GetProperty("Address");
        if(address.GetValue(fallback,null).ToString()!="http://127.0.0.1:10909/")throw new Exception("Null request did not use game proxy");
        Console.WriteLine("EXPLICIT_NULL_USES_PROCESS_PROXY_OK");
        object explicitProxy=Activator.CreateInstance(asm.GetType("BestHTTP.HTTPProxy"),new object[]{new Uri("http://127.0.0.1:19001")});
        proxy.SetValue(request,explicitProxy,null);
        if(!Object.ReferenceEquals(proxy.GetValue(request,null),explicitProxy))throw new Exception("Explicit non-null proxy overwritten");
        Console.WriteLine("EXPLICIT_NON_NULL_PRESERVED_OK");
        proxy.SetValue(request,null,null);
        Environment.SetEnvironmentVariable("TOPHEROES_PROXY","");
        if(proxy.GetValue(request,null)!=null)throw new Exception("Empty-env null behavior changed");
        Console.WriteLine("NO_ENV_OR_EMPTY_ENV_FALLBACK_OK");
        Environment.SetEnvironmentVariable("TOPHEROES_PROXY","http://127.0.0.1:10910");
        fallback=proxy.GetValue(request,null);
        if(address.GetValue(fallback,null).ToString()!="http://127.0.0.1:10910/")throw new Exception("Proxy port pinned");
        var manager=asm.GetType("BestHTTP.HTTPManager").GetProperty("Proxy",BindingFlags.Public|BindingFlags.Static);
        if(address.GetValue(manager.GetValue(null,null),null).ToString()!="http://127.0.0.1:10910/")throw new Exception("Global proxy regressed");
        Environment.SetEnvironmentVariable("TOPHEROES_PROXY",null);
        Console.WriteLine("DYNAMIC_PORT_AND_GLOBAL_PROXY_OK");
    }
}
