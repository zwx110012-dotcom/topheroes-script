using System;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
class VerifyTcpHook {
 static void Main(string[] argv){
  Environment.SetEnvironmentVariable("TOPHEROES_TCP_RELAY",argv[0]);
  using(var s=new Socket(AddressFamily.InterNetwork,SocketType.Stream,ProtocolType.Tcp))
  using(var args=new SocketAsyncEventArgs())
  using(var done=new ManualResetEvent(false)){
    args.RemoteEndPoint=new IPEndPoint(IPAddress.Parse("8.8.8.8"),3963);
    args.Completed+=(sender,eventArgs)=>done.Set();
    bool pending=TopHeroesLocalProxy.ConnectAsync(s,args,"test.topwargame.com",3963);
    if(pending&&!done.WaitOne(5000))throw new Exception("Native callback did not complete");
    if(args.SocketError!=SocketError.Success||args.ConnectSocket==null||!s.Connected)throw new Exception("Native socket result invalid");
    s.ReceiveTimeout=5000;s.Send(Encoding.ASCII.GetBytes("relay-test"));
    byte[] b=new byte[10];int offset=0;while(offset<b.Length)offset+=s.Receive(b,offset,b.Length-offset,SocketFlags.None);
    if(Encoding.ASCII.GetString(b)!="relay-test")throw new Exception("Payload changed");
    Console.WriteLine("TCP_NATIVE_CALLBACK_AND_PAYLOAD_OK");
  }
  Environment.SetEnvironmentVariable("TOPHEROES_TCP_RELAY",null);
  var listener=new TcpListener(IPAddress.Loopback,0);listener.Start();
  using(var s=new Socket(AddressFamily.InterNetwork,SocketType.Stream,ProtocolType.Tcp))
  using(var args=new SocketAsyncEventArgs())
  using(var done=new ManualResetEvent(false)){
    args.RemoteEndPoint=listener.LocalEndpoint;args.Completed+=(sender,eventArgs)=>done.Set();
    bool pending=TopHeroesLocalProxy.ConnectAsync(s,args,"original.example",123);
    using(var accepted=listener.AcceptSocket()){
      if(pending&&!done.WaitOne(5000))throw new Exception("Fallback callback failed");
      if(args.SocketError!=SocketError.Success||!s.Connected)throw new Exception("Fallback changed");
    }
    Console.WriteLine("TCP_NO_ENV_FALLBACK_OK");
  }
  listener.Stop();
 }
}
