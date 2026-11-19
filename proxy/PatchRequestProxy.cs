using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Security.Cryptography;
using Mono.Cecil;
using Mono.Cecil.Cil;

class PatchRequestProxy {
    static byte[] Decode(string path) {
        byte[] b=File.ReadAllBytes(path); int key=b[0]^0x4d;
        if(key<=0 || key>64 || b.Length<key) throw new Exception("Unknown MDL encoding");
        for(int i=0;i<key;i++) b[i]^=(byte)key;
        if(b[0]!=0x4d || b[1]!=0x5a) throw new Exception("Invalid PE header");
        return b;
    }
    static IEnumerable<TypeDefinition> Types(IEnumerable<TypeDefinition> ts) {
        foreach(var t in ts){yield return t;foreach(var x in Types(t.NestedTypes))yield return x;}
    }
    static string Body(MethodDefinition m) {
        if(!m.HasBody)return "";
        var ins=m.Body.Instructions;
        return string.Join("\n",ins.Select(i=>i.OpCode+" "+
            (i.Operand is Instruction ? "BR:"+ins.IndexOf((Instruction)i.Operand) :
             i.Operand is Instruction[] ? "SW:"+string.Join(",",((Instruction[])i.Operand).Select(x=>ins.IndexOf(x).ToString()).ToArray()) :
             i.Operand==null ? "" : i.Operand.ToString())).ToArray());
    }
    static string Hash(byte[] b) {using(var h=SHA256.Create())return BitConverter.ToString(h.ComputeHash(b)).Replace("-","");}
    static void Main(string[] a) {
        try{MainImpl(a);}catch(Exception e){Console.WriteLine("PATCH_ERROR "+e.GetType().FullName+": "+e.Message);Environment.ExitCode=1;}
    }
    static void MainImpl(string[] a) {
        if(a.Length!=4)throw new Exception("Expected old patch, original, output DLL, output MDL");
        if(Hash(File.ReadAllBytes(a[0]))!="E2EF0322F3E423163D441F54B6DE973D2EADCA5EED337DE4825A8F74C3D98CAC")throw new Exception("Unexpected prior patch");
        if(Hash(File.ReadAllBytes(a[1]))!="9A2EB0048E7C4F3FF13454FC68432AE5D31B5851D2D354E262AB85AD43D95599")throw new Exception("Unexpected original");
        var resolver=new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(Path.Combine(Path.GetDirectoryName(a[3]),"decoded-deps"));
        resolver.AddSearchDirectory(Path.Combine(Environment.GetEnvironmentVariable("TOPHEROES_GAME_ROOT") ?? "", "TopHeroes_Steam_Data", "Managed"));
        using(var module=ModuleDefinition.ReadModule(new MemoryStream(Decode(a[0])),new ReaderParameters{AssemblyResolver=resolver}))
        using(var original=ModuleDefinition.ReadModule(new MemoryStream(Decode(a[1])))) {
            var before=Types(original.Types).SelectMany(t=>t.Methods).ToDictionary(m=>m.FullName,Body);
            var getter=module.GetType("BestHTTP.HTTPRequest").Methods.Single(m=>m.Name=="get_Proxy");
            var ins=getter.Body.Instructions;
            if(ins.Count!=3 || ins[0].OpCode!=OpCodes.Ldarg_0 || ins[1].OpCode!=OpCodes.Ldfld || ins[2].OpCode!=OpCodes.Ret)throw new Exception("Request proxy getter changed");
            var field=(FieldReference)ins[1].Operand;
            var ctor=module.GetType("BestHTTP.HTTPProxy").Methods.Single(m=>m.IsConstructor&&m.Parameters.Count==1&&m.Parameters[0].ParameterType.FullName=="System.Uri");
            var getEnv=module.ImportReference(typeof(Environment).GetMethod("GetEnvironmentVariable",new[]{typeof(string)}));
            var empty=module.ImportReference(typeof(string).GetMethod("IsNullOrEmpty",new[]{typeof(string)}));
            var uriCtor=module.ImportReference(typeof(Uri).GetConstructor(new[]{typeof(string)}));
            var env=new VariableDefinition(module.TypeSystem.String);getter.Body.Variables.Add(env);getter.Body.InitLocals=true;
            var il=getter.Body.GetILProcessor();var first=ins[0];
            var prefix=new[]{il.Create(OpCodes.Ldarg_0),il.Create(OpCodes.Ldfld,field),il.Create(OpCodes.Brtrue,first),
                il.Create(OpCodes.Ldstr,"TOPHEROES_PROXY"),il.Create(OpCodes.Call,getEnv),il.Create(OpCodes.Stloc,env),
                il.Create(OpCodes.Ldloc,env),il.Create(OpCodes.Call,empty),il.Create(OpCodes.Brtrue,first),
                il.Create(OpCodes.Ldloc,env),il.Create(OpCodes.Newobj,uriCtor),il.Create(OpCodes.Newobj,ctor),il.Create(OpCodes.Ret)};
            foreach(var i in prefix)il.InsertBefore(first,i);
            getter.Body.MaxStackSize=2;
            // Negotiate HTTPS over HTTP/1.1 only when this game's explicit
            // process proxy is enabled. Keep TLS/certificate handling intact.
            var connect=module.GetType("BestHTTP.Connections.TCPConnector").Methods.Single(m=>m.Name=="Connect"&&m.Parameters.Count==1);
            var h2=connect.Body.Instructions.Single(i=>i.OpCode==OpCodes.Ldstr&&(string)i.Operand=="h2");
            var listLoad=h2.Previous;var add=h2.Next;var nextLoad=add.Next;
            if(listLoad.OpCode!=OpCodes.Ldloc_S || add.OpCode!=OpCodes.Callvirt ||
               !(add.Operand is MethodReference) || ((MethodReference)add.Operand).Name!="Add" ||
               nextLoad.Next.OpCode!=OpCodes.Ldstr || (string)nextLoad.Next.Operand!="http/1.1")throw new Exception("ALPN structure changed");
            var cil=connect.Body.GetILProcessor();
            var guard=new[]{cil.Create(OpCodes.Ldstr,"TOPHEROES_PROXY"),cil.Create(OpCodes.Call,getEnv),
                cil.Create(OpCodes.Call,empty),cil.Create(OpCodes.Brfalse,nextLoad)};
            foreach(var i in guard)cil.InsertBefore(listLoad,i);
            // Insertion can push an existing short jump outside its byte range.
            var branches=typeof(OpCodes).GetFields().Where(f=>f.FieldType==typeof(OpCode))
                .Select(f=>(OpCode)f.GetValue(null)).ToDictionary(op=>op.Name,op=>op);
            foreach(var i in connect.Body.Instructions)
                if(i.OpCode.OperandType==OperandType.ShortInlineBrTarget)i.OpCode=branches[i.OpCode.Name.Substring(0,i.OpCode.Name.Length-2)];
            module.Write(a[2]);
            using(var verify=ModuleDefinition.ReadModule(a[2])) {
                var methods=Types(verify.Types).SelectMany(t=>t.Methods).ToArray();
                if(methods.Length!=before.Count || methods.Any(m=>!before.ContainsKey(m.FullName)))throw new Exception("Method inventory changed");
                var changed=methods.Where(m=>before[m.FullName]!=Body(m)).Select(m=>m.FullName).OrderBy(x=>x).ToArray();
                var allowed=new[]{"BestHTTP.Proxy BestHTTP.HTTPManager::get_Proxy()","BestHTTP.Proxy BestHTTP.HTTPRequest::get_Proxy()",
                    "System.Void BestHTTP.Connections.TCPConnector::Connect(BestHTTP.HTTPRequest)"};
                if(!changed.SequenceEqual(allowed))throw new Exception("Unexpected method changes: "+string.Join(";",changed));
                Console.WriteLine("CHANGED_METHODS "+string.Join(";",changed));
                Console.WriteLine("OTHER_METHODS_UNCHANGED "+(before.Count-3));
                var verifiedConnect=verify.GetType("BestHTTP.Connections.TCPConnector").Methods.Single(m=>m.Name=="Connect"&&m.Parameters.Count==1);
                CheckProtocols(verifiedConnect,null,"h2,http/1.1");
                CheckProtocols(verifiedConnect,"","h2,http/1.1");
                CheckProtocols(verifiedConnect,"http://127.0.0.1:10909","http/1.1");
                Console.WriteLine("ALPN_PROCESS_PROXY_H1_AND_ORIGINAL_FALLBACK_OK");
            }
            byte[] encoded=File.ReadAllBytes(a[2]);int key=File.ReadAllBytes(a[1])[0]^0x4d;
            for(int i=0;i<key;i++)encoded[i]^=(byte)key;
            File.WriteAllBytes(a[3],encoded);
            Console.WriteLine("PATCH_SHA256 "+Hash(encoded));
        }
    }
    static void CheckProtocols(MethodDefinition m,string env,string expected){
        var ins=m.Body.Instructions;
        int start=ins.IndexOf(ins.Single(i=>i.OpCode==OpCodes.Ldstr&&(string)i.Operand=="TOPHEROES_PROXY"));
        int end=ins.IndexOf(ins.Skip(start).First(i=>i.OpCode==OpCodes.Ldstr&&(string)i.Operand=="http/1.1"))+2;
        var stack=new Stack<object>();var protocols=new List<string>();int pc=start;
        while(pc<end){var i=ins[pc++];
            if(i.OpCode==OpCodes.Ldstr)stack.Push(i.Operand);
            else if(i.OpCode==OpCodes.Ldloc_S)stack.Push(protocols);
            else if(i.OpCode==OpCodes.Call){var mr=(MethodReference)i.Operand;
                if(mr.Name=="GetEnvironmentVariable"){stack.Pop();stack.Push(env);}
                else if(mr.Name=="IsNullOrEmpty")stack.Push(String.IsNullOrEmpty((string)stack.Pop()));
                else throw new Exception("Unexpected ALPN call");
            }else if(i.OpCode==OpCodes.Brfalse){if(!(bool)stack.Pop())pc=ins.IndexOf((Instruction)i.Operand);}
            else if(i.OpCode==OpCodes.Callvirt && ((MethodReference)i.Operand).Name=="Add"){
                string value=(string)stack.Pop();((List<string>)stack.Pop()).Add(value);
            }else throw new Exception("Unexpected ALPN instruction "+i);
        }
        if(stack.Count!=0||string.Join(",",protocols.ToArray())!=expected)throw new Exception("Wrong ALPN protocols");
    }
}
