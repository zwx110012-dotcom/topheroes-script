using System;using System.IO;using System.Linq;using Mono.Cecil;
class InspectNetworkSettings {
 static void Main(string[] a){
  using(var m=ModuleDefinition.ReadModule(a[0])){
   foreach(var t in m.Types.Where(t=>t.FullName.Contains("HTTP2Settings")||t.FullName.Contains("HostSettings")||t.FullName.Contains("HTTP2PluginSettings")||t.FullName=="BestHTTP.HTTPManager")){
    Console.WriteLine("TYPE "+t.FullName);
    foreach(var f in t.Fields)if(t.FullName!="BestHTTP.HTTPManager"||f.Name.ToLower().Contains("http2"))Console.WriteLine("FIELD "+f.FullName);
    foreach(var p in t.Properties)Console.WriteLine("PROPERTY "+p.FullName);
   }
   foreach(var t in m.Types)foreach(var method in t.Methods.Where(x=>x.HasBody))
    if(method.Body.Instructions.Any(i=>i.Operand is FieldReference&&((FieldReference)i.Operand).FullName.Contains("EnableConnect")))Console.WriteLine("USES_ENABLE "+method.FullName);
   foreach(var t in m.Types)foreach(var method in t.Methods.Where(x=>x.HasBody))
    if(method.Body.Instructions.Any(i=>i.Operand!=null && (i.Operand.ToString().Contains("ProtocolName::Http_2")||i.Operand.ToString().Contains("ProtocolName::Http_1_1")||(i.OpCode==Mono.Cecil.Cil.OpCodes.Ldstr && (string)i.Operand=="h2")))) {
     Console.WriteLine("ALPN_METHOD "+method.FullName);
     foreach(var i in method.Body.Instructions)Console.WriteLine(i.ToString());
    }
  }
 }
}
