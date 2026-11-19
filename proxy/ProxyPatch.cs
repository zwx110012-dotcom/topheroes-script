using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Security.Cryptography;
using Mono.Cecil;
using Mono.Cecil.Cil;

class ProxyPatch {
    static byte[] Decode(string path, int count) {
        byte[] b=File.ReadAllBytes(path); byte key=(byte)(b[0]^0x4d);
        for(int i=0;i<count;i++) b[i]^=key;
        if(b[0]!=0x4d||b[1]!=0x5a) throw new Exception("Invalid header");
        return b;
    }
    static string Hash(byte[] b) {using(var h=SHA256.Create())return BitConverter.ToString(h.ComputeHash(b)).Replace("-","");}
    static IEnumerable<TypeDefinition> Types(IEnumerable<TypeDefinition> ts) {foreach(var t in ts){yield return t;foreach(var x in Types(t.NestedTypes))yield return x;}}
    static string Body(MethodDefinition m) {
        if(!m.HasBody)return "";
        return string.Join("\n",m.Body.Instructions.Select(i=>i.OpCode+" "+(i.Operand==null?"":i.Operand.ToString())).ToArray());
    }
    static string StableBody(MethodDefinition m) {
        if(!m.HasBody)return "";
        var instructions=m.Body.Instructions;
        return string.Join("\n",instructions.Select(i=>i.OpCode+" "+(i.Operand is Instruction?"BR:"+instructions.IndexOf((Instruction)i.Operand):i.Operand is Instruction[]?"SW:"+string.Join(",",((Instruction[])i.Operand).Select(x=>instructions.IndexOf(x).ToString()).ToArray()):i.Operand==null?"":i.Operand.ToString())).ToArray());
    }
    static MethodDefinition CloneMethod(ModuleDefinition module, MethodDefinition src, TypeDefinition target){
        var dst=new MethodDefinition(src.Name,src.Attributes,module.ImportReference(src.ReturnType));
        foreach(var p in src.Parameters)dst.Parameters.Add(new ParameterDefinition(p.Name,p.Attributes,module.ImportReference(p.ParameterType)));
        target.Methods.Add(dst); dst.Body.InitLocals=src.Body.InitLocals; dst.Body.MaxStackSize=src.Body.MaxStackSize;
        foreach(var v in src.Body.Variables)dst.Body.Variables.Add(new VariableDefinition(module.ImportReference(v.VariableType)));
        var map=new Dictionary<Instruction,Instruction>();
        foreach(var i in src.Body.Instructions){var n=Instruction.Create(OpCodes.Nop);n.OpCode=i.OpCode;dst.Body.Instructions.Add(n);map[i]=n;}
        foreach(var i in src.Body.Instructions){object o=i.Operand;
            if(o is Instruction)o=map[(Instruction)o];
            else if(o is Instruction[])o=((Instruction[])o).Select(x=>map[x]).ToArray();
            else if(o is VariableDefinition)o=dst.Body.Variables[src.Body.Variables.IndexOf((VariableDefinition)o)];
            else if(o is ParameterDefinition)o=dst.Parameters[src.Parameters.IndexOf((ParameterDefinition)o)];
            else if(o is MethodReference)o=module.ImportReference((MethodReference)o);
            else if(o is FieldReference)o=module.ImportReference((FieldReference)o);
            else if(o is TypeReference)o=module.ImportReference((TypeReference)o);
            map[i].Operand=o;
        }
        foreach(var e in src.Body.ExceptionHandlers)dst.Body.ExceptionHandlers.Add(new ExceptionHandler(e.HandlerType){TryStart=map[e.TryStart],TryEnd=e.TryEnd==null?null:map[e.TryEnd],HandlerStart=map[e.HandlerStart],HandlerEnd=e.HandlerEnd==null?null:map[e.HandlerEnd],FilterStart=e.FilterStart==null?null:map[e.FilterStart],CatchType=e.CatchType==null?null:module.ImportReference(e.CatchType)});
        return dst;
    }
    static void Main(string[] args) {
        try { MainImpl(args); }
        catch(Exception e){Console.WriteLine("PATCH_ERROR_TYPE "+e.GetType().FullName);try{Console.WriteLine("PATCH_ERROR_MESSAGE "+e.Message);}catch{};Environment.ExitCode=1;}
    }
    static void MainImpl(string[] args) {
        string root=@"D:\steam\steamapps\common\Top Heroes\TopHeroes_Steam\TopHeroes_Steam_Data";
        string dir=Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location);
        string source=Path.Combine(root,@"StreamingAssets\Assemblies\ScriptThirdParty.mdl");
        if(args.Length>0&&args[0]=="patch-tcp"){
            string tcpSource=args.Length>1?args[1]:Path.Combine(root,@"StreamingAssets\Assemblies\ScriptProj.mdl");
            byte[] originalTcp=File.ReadAllBytes(tcpSource);
            File.WriteAllBytes(Path.Combine(dir,"ScriptProj.original.mdl"),originalTcp);
            var resolver=new DefaultAssemblyResolver();
            resolver.AddSearchDirectory(Path.Combine(dir,"decoded-deps"));
            resolver.AddSearchDirectory(Path.Combine(root,"Managed"));
            int headerCount=originalTcp[0]^0x4d;
            using(var module=ModuleDefinition.ReadModule(new MemoryStream(Decode(tcpSource,headerCount)),new ReaderParameters{AssemblyResolver=resolver}))
            using(var hook=ModuleDefinition.ReadModule(Path.Combine(dir,"TcpProxyHook.dll"))){
                var before=new Dictionary<string,string>();
                foreach(var t in Types(module.Types))for(int mi=0;mi<t.Methods.Count;mi++)before[t.FullName+"#"+mi+":"+t.Methods[mi].FullName]=StableBody(t.Methods[mi]);
                var target=module.GetType("Communication.SocketClient").Methods.Single(m=>m.Name=="TryConnect");
                var calls=target.Body.Instructions.Where(i=>i.Operand is MethodReference&&((MethodReference)i.Operand).FullName=="System.Boolean System.Net.Sockets.Socket::ConnectAsync(System.Net.Sockets.SocketAsyncEventArgs)").ToArray();
                if(calls.Length!=1)throw new Exception("Unexpected TCP connect method");
                var originalInstructions=target.Body.Instructions;
                int callIndex=originalInstructions.IndexOf(calls[0]);
                using(var previous=ModuleDefinition.ReadModule(Path.Combine(dir,"decoded-deps","ScriptProj.dll"))) {
                    var previousTarget=previous.GetType("Communication.SocketClient").Methods.Single(m=>m.Name=="TryConnect");
                    if(StableBody(target)!=StableBody(previousTarget))throw new Exception("TCP method changed; manual review required");
                }
                Console.WriteLine("TCP_SOURCE_CONNECT_CONTEXT\n"+string.Join("\n",originalInstructions.Skip(Math.Max(0,callIndex-9)).Take(10).Select(i=>i.ToString()).ToArray()));
                var newType=new TypeDefinition("TopHeroesTrial","LocalProxy",Mono.Cecil.TypeAttributes.Public|Mono.Cecil.TypeAttributes.Abstract|Mono.Cecil.TypeAttributes.Sealed,module.TypeSystem.Object);module.Types.Add(newType);
                var helper=CloneMethod(module,hook.GetType("TopHeroesLocalProxy").Methods.Single(m=>m.Name=="ConnectAsync"),newType);
                var pending=module.GetType("Communication.SocketClient").Fields.Single(f=>f.Name=="_pendingSockets");
                var wrapper=module.GetType("Communication.SocketWrapper");
                var host=wrapper.Fields.Single(f=>f.Name=="host");var port=wrapper.Fields.Single(f=>f.Name=="port");
                var il=target.Body.GetILProcessor();var call=calls[0];
                foreach(var field in new[]{host,port}){
                    foreach(var i in new[]{il.Create(OpCodes.Ldarg_0),il.Create(OpCodes.Ldfld,pending),il.Create(OpCodes.Ldloc_3),il.Create(OpCodes.Ldelem_Ref),il.Create(OpCodes.Ldfld,field)})il.InsertBefore(call,i);
                }
                call.OpCode=OpCodes.Call;call.Operand=helper;target.Body.MaxStackSize+=2;
                string outPath=Path.Combine(dir,"ScriptProj.proxy.dll");module.Write(outPath);
                using(var verify=ModuleDefinition.ReadModule(outPath)){
                    var keyed=Types(verify.Types).SelectMany(t=>t.Methods.Select((m,idx)=>new{method=m,key=t.FullName+"#"+idx+":"+m.FullName})).Where(x=>before.ContainsKey(x.key)).ToArray();
                    if(keyed.Length!=before.Count)throw new Exception("Original method inventory changed");
                    var changed=keyed.Where(x=>before[x.key]!=StableBody(x.method)).Select(x=>x.method).ToArray();
                    if(changed.Length!=1||changed[0].FullName!=target.FullName){
                        Console.WriteLine("TCP_CHANGED_COUNT "+changed.Length);
                        foreach(var bad in changed.Take(6)){
                            Console.WriteLine("DIFF_METHOD "+bad.FullName);
                            string[] oldLines=before[keyed.Single(x=>x.method==bad).key].Split('\n'),newLines=StableBody(bad).Split('\n');
                            for(int j=0;j<Math.Min(oldLines.Length,newLines.Length);j++)if(oldLines[j]!=newLines[j]){Console.WriteLine("OLD "+oldLines[j]);Console.WriteLine("NEW "+newLines[j]);break;}
                        }
                        throw new Exception("Unexpected TCP semantic changes");
                    }
                    Console.WriteLine("TCP_CHANGED_METHOD "+changed[0].FullName);
                    Console.WriteLine("TCP_OTHER_METHODS_UNCHANGED "+(before.Count-1));
                }
                byte[] patched=File.ReadAllBytes(outPath);byte key=(byte)(originalTcp[0]^0x4d);
                for(int i=0;i<headerCount;i++)patched[i]^=key;
                File.WriteAllBytes(Path.Combine(dir,"ScriptProj.proxy.mdl"),patched);
                Console.WriteLine("TCP_ORIGINAL_SHA256 "+Hash(originalTcp));Console.WriteLine("TCP_PATCHED_SHA256 "+Hash(patched));
            }
            return;
        }
        if(args.Length>0&&args[0]=="inspect"){
            using(var p=ModuleDefinition.ReadModule(new MemoryStream(Decode(Path.Combine(root,@"StreamingAssets\Assemblies\ScriptProj.mdl"),14)))){
                foreach(var t in Types(p.Types))foreach(var m in t.Methods)if(m.Name=="ConfigureBestHTTP"||(m.HasBody&&m.Body.Instructions.Any(i=>i.Operand is MethodReference&&((MethodReference)i.Operand).FullName.Contains("BestHTTP.HTTPManager::set_Proxy"))))Console.WriteLine(t.FullName+"::"+m.Name+"\n"+Body(m));
            }
            return;
        }
        if(args.Length>0&&args[0]=="inspect-tcp"){
            foreach(var f in new[]{"ScriptThirdParty.mdl","ScriptProj.mdl"}){
                using(var p=ModuleDefinition.ReadModule(new MemoryStream(Decode(Path.Combine(root,@"StreamingAssets\Assemblies\"+f), f=="ScriptProj.mdl"?14:20)))){
                    foreach(var t in Types(p.Types))foreach(var m in t.Methods)if(m.HasBody&&m.Body.Instructions.Any(i=>i.Operand is MethodReference&&((MethodReference)i.Operand).FullName.Contains("System.Net.Sockets.")&&(((MethodReference)i.Operand).Name.Contains("Connect")||((MethodReference)i.Operand).Name==".ctor"))){
                        Console.WriteLine("ASSEMBLY "+f+" METHOD "+m.FullName+"\n"+Body(m));
                    }
                }
            }
            return;
        }
        var original=File.ReadAllBytes(source);
        File.WriteAllBytes(Path.Combine(dir,"ScriptThirdParty.original.mdl"),original);
        byte[] plain=Decode(source,20);
        using(var module=ModuleDefinition.ReadModule(new MemoryStream(plain))){
            var manager=module.GetType("BestHTTP.HTTPManager");
            var getter=manager.Methods.Single(m=>m.Name=="get_Proxy");
            if(getter.Body.Instructions.Count!=2||getter.Body.Instructions[0].OpCode!=OpCodes.Ldsfld)throw new Exception("Unexpected getter; no patch");
            var before=new Dictionary<string,string>();
            foreach(var t in Types(module.Types))foreach(var m in t.Methods)before[m.FullName]=Body(m);
            var ctor=module.GetType("BestHTTP.HTTPProxy").Methods.Single(m=>m.IsConstructor&&m.Parameters.Count==1);
            var getEnv=module.ImportReference(typeof(Environment).GetMethod("GetEnvironmentVariable",new[]{typeof(string)}));
            var isEmpty=module.ImportReference(typeof(string).GetMethod("IsNullOrEmpty",new[]{typeof(string)}));
            var uriCtor=module.ImportReference(typeof(Uri).GetConstructor(new[]{typeof(string)}));
            var body=getter.Body;
            var first=body.Instructions[0];
            var il=body.GetILProcessor();
            var proxyUrl=new VariableDefinition(module.TypeSystem.String); body.Variables.Add(proxyUrl); body.InitLocals=true;
            var prefix=new[]{il.Create(OpCodes.Ldstr,"TOPHEROES_PROXY"),il.Create(OpCodes.Call,getEnv),il.Create(OpCodes.Stloc,proxyUrl),il.Create(OpCodes.Ldloc,proxyUrl),il.Create(OpCodes.Call,isEmpty),il.Create(OpCodes.Brtrue,first),il.Create(OpCodes.Ldloc,proxyUrl),il.Create(OpCodes.Newobj,uriCtor),il.Create(OpCodes.Newobj,ctor),il.Create(OpCodes.Ret)};
            foreach(var i in prefix)il.InsertBefore(first,i);
            body.MaxStackSize=2;
            string outPath=Path.Combine(dir,"ScriptThirdParty.proxy.dll"); module.Write(outPath);
            using(var verify=ModuleDefinition.ReadModule(outPath)){
                var changed=Types(verify.Types).SelectMany(t=>t.Methods).Where(m=>before[m.FullName]!=Body(m)).ToArray();
                if(changed.Length!=1||changed[0].FullName!=getter.FullName)throw new Exception("Unexpected semantic changes");
                Console.WriteLine("CHANGED_METHOD "+changed[0].FullName);
                Console.WriteLine(Body(changed[0]));
                Console.WriteLine("ALL_OTHER_METHODS_UNCHANGED "+(before.Count-1));
            }
            byte[] patched=File.ReadAllBytes(outPath); byte key=(byte)(original[0]^0x4d);
            for(int i=0;i<20;i++)patched[i]^=key;
            File.WriteAllBytes(Path.Combine(dir,"ScriptThirdParty.proxy.mdl"),patched);
            Console.WriteLine("ORIGINAL_SHA256 "+Hash(original));
            Console.WriteLine("PATCHED_SHA256 "+Hash(patched));
        }
    }
}
