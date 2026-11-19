using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;

public static class TopHeroesLocalProxy {
    public static bool ConnectAsync(Socket socket, SocketAsyncEventArgs args, string host, int port) {
        string relayPort = Environment.GetEnvironmentVariable("TOPHEROES_TCP_RELAY");
        if (String.IsNullOrEmpty(relayPort)) return socket.ConnectAsync(args);
        if (String.IsNullOrEmpty(host) || host.IndexOf('\n') >= 0 || host.IndexOf('\r') >= 0 || host.IndexOf('\t') >= 0) throw new IOException("Invalid relay target");
        using (TcpClient control = new TcpClient()) {
            control.ReceiveTimeout = 5000;
            control.SendTimeout = 5000;
            control.Connect(IPAddress.Loopback, Int32.Parse(relayPort));
            NetworkStream stream = control.GetStream();
            byte[] request = Encoding.UTF8.GetBytes(host + "\t" + port.ToString() + "\n");
            stream.Write(request, 0, request.Length);
            using (StreamReader reader = new StreamReader(stream, Encoding.UTF8)) {
                string response = reader.ReadLine();
                int localPort;
                if (!Int32.TryParse(response, out localPort) || localPort < 1024 || localPort > 65535) throw new IOException("Local game relay unavailable");
                args.RemoteEndPoint = new IPEndPoint(IPAddress.Loopback, localPort);
            }
        }
        return socket.ConnectAsync(args);
    }
}
