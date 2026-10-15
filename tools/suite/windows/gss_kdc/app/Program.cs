using System.Buffers.Binary;
using System.Collections.Concurrent;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;
using Kerberos.NET.Configuration;
using Kerberos.NET.Crypto;
using Kerberos.NET.Entities;
using Kerberos.NET.Entities.Pac;
using Kerberos.NET.Server;

// 固定测试身份只用于隔离夹具；认证与票据由锁定官方库处理。
sealed class Principal : IKerberosPrincipal
{
    public string PrincipalName { get; }
    public KerberosKey Key { get; }
    public Principal(string name)
    {
        PrincipalName = name;
        var raw = SHA256.HashData(Encoding.UTF8.GetBytes("PuTTY suite fixed test key v1 " + name));
        Key = new KerberosKey(key: raw,
            principal: new PrincipalName(PrincipalNameType.NT_PRINCIPAL, Realm.RealmName, name.Split('/')),
            etype: EncryptionType.AES256_CTS_HMAC_SHA1_96, kvno: 1);
    }
    public IEnumerable<PaDataType> SupportedPreAuthenticationTypes => new[] { PaDataType.PA_ENC_TIMESTAMP };
    public SupportedEncryptionTypes SupportedEncryptionTypes => SupportedEncryptionTypes.Aes256CtsHmacSha196;
    public PrincipalType Type => PrincipalName.Contains('/') ? PrincipalType.Service : PrincipalType.User;
    public DateTimeOffset? Expires => null;
    public KerberosKey RetrieveLongTermCredential() => Key;
    public KerberosKey RetrieveLongTermCredential(EncryptionType etype) =>
        etype == EncryptionType.AES256_CTS_HMAC_SHA1_96 ? Key : null;
    public PrivilegedAttributeCertificate GeneratePac() => null;
    public void Validate(X509Certificate2Collection certificates) => throw new NotSupportedException("禁止 PKINIT");
}

sealed class Principals : IPrincipalService
{
    internal readonly Dictionary<string, Principal> Entries = new(StringComparer.Ordinal)
    {
        ["suite"] = new("suite"),
        ["host/localhost"] = new("host/localhost"),
        ["krbtgt/SUITE.TEST"] = new("krbtgt/SUITE.TEST")
    };
    public IKerberosPrincipal Find(KrbPrincipalName principalName, string realm = null)
    {
        if (realm != null && realm != Realm.RealmName) return null;
        var name = string.Join("/", principalName.Name);
        var result = Entries.GetValueOrDefault(name);
        return result != null && principalName.Name.SequenceEqual(result.PrincipalName.Split('/')) ? result : null;
    }
    public Task<IKerberosPrincipal> FindAsync(KrbPrincipalName name, string realm = null) => Task.FromResult(Find(name, realm));
    public X509Certificate2 RetrieveKdcCertificate() => throw new NotSupportedException("禁止 PKINIT");
    public IExchangeKey RetrieveKeyCache(KeyAgreementAlgorithm algorithm) => null;
    public IExchangeKey CacheKey(IExchangeKey key) => throw new NotSupportedException("禁止 PKINIT");
}
sealed class Settings : IRealmSettings
{
    public TimeSpan MaximumSkew => TimeSpan.FromMinutes(2);
    public TimeSpan SessionLifetime => TimeSpan.FromMinutes(10);
    public TimeSpan MaximumRenewalWindow => TimeSpan.Zero;
    public KerberosCompatibilityFlags Compatibility => KerberosCompatibilityFlags.IsolateRealmsConsistently |
        KerberosCompatibilityFlags.EnableSpecCompliantCNameHandling;
}
sealed class Trusts : ITrustedRealmService
{
    public IRealmReferral ProposeTransit(KrbTgsReq request, PreAuthenticationContext context) => null;
}
sealed class Realm : IRealmService
{
    public const string RealmName = "SUITE.TEST";
    public string Name => RealmName;
    public IRealmSettings Settings { get; } = new Settings();
    public IPrincipalService Principals => Store;
    public Principals Store { get; } = new();
    public ITrustedRealmService TrustedRealms { get; } = new Trusts();
    public Krb5Config Configuration { get; }
    public Realm(Krb5Config config) { Configuration = config; }
    public DateTimeOffset Now() => DateTimeOffset.UtcNow;
}
sealed class BoundedKdc : IAsyncDisposable
{
    readonly TcpListener listener = new(IPAddress.Loopback, 0);
    readonly KdcServer server;
    readonly CancellationTokenSource cancellation = new(TimeSpan.FromSeconds(90));
    readonly ConcurrentDictionary<int, Task> workers = new();
    readonly ConcurrentDictionary<int, TcpClient> clients = new();
    readonly SemaphoreSlim slots = new(4);
    Task accepting;
    int accepted, completed, rejected, malformed, asRequests, tgsRequests;
    public int Port => ((IPEndPoint)listener.LocalEndpoint).Port;

    public BoundedKdc(KdcServerOptions options) { server = new KdcServer(options); }
    public void Start()
    {
        listener.ExclusiveAddressUse = true;
        listener.Start(8);
        accepting = Accept();
    }
    async Task Accept()
    {
        while (!cancellation.IsCancellationRequested)
        {
            TcpClient client;
            try { client = await listener.AcceptTcpClientAsync(cancellation.Token); }
            catch (OperationCanceledException) { break; }
            catch (SocketException) when (cancellation.IsCancellationRequested) { break; }
            int id = Interlocked.Increment(ref accepted);
            if (id > 64 || !slots.Wait(0))
            {
                Interlocked.Increment(ref rejected);
                client.Dispose();
                if (id >= 64) { listener.Stop(); break; }
                continue;
            }
            clients[id] = client;
            // 每个任务拥有唯一socket，任务表最多保留64项。
            workers[id] = Task.Run(async () =>
            {
                try { await Handle(client); }
                finally
                {
                    clients.TryRemove(id, out _);
                    client.Dispose();
                    slots.Release();
                }
            });
            if (id >= 64) { listener.Stop(); break; }
        }
    }
    static ReadOnlyMemory<byte> Error(KerberosErrorCode code)
    {
        var result = new KrbError { ErrorCode = code, Realm = Realm.RealmName,
            SName = new KrbPrincipalName { Type = PrincipalNameType.NT_SRV_INST,
                Name = new[] { "krbtgt", Realm.RealmName } } };
        result.StampServerTime();
        return result.EncodeApplication();
    }
    ReadOnlyMemory<byte> Policy(ReadOnlyMemory<byte> request)
    {
        // 使用正式解析器检查真实请求，绝不自行构造成功票据。
        KrbKdcReq body;
        if (request.Span[0] == 0x6a)
        {
            var parsed = KrbAsReq.DecodeApplication(request);
            if (parsed.Body.CName == null || !parsed.Body.CName.Name.SequenceEqual(new[] { "suite" }) ||
                parsed.Body.SName == null || !parsed.Body.SName.Name.SequenceEqual(new[] { "krbtgt", Realm.RealmName }))
                return Error(KerberosErrorCode.KDC_ERR_C_PRINCIPAL_UNKNOWN);
            body = parsed;
            Interlocked.Increment(ref asRequests);
        }
        else if (request.Span[0] == 0x6c)
        {
            var parsed = KrbTgsReq.DecodeApplication(request);
            body = parsed;
            Interlocked.Increment(ref tgsRequests);
            if (parsed.Body.SName == null || !parsed.Body.SName.Name.SequenceEqual(new[] { "host", "localhost" }))
                return Error(KerberosErrorCode.KDC_ERR_S_PRINCIPAL_UNKNOWN);
        }
        else return Error(KerberosErrorCode.KDC_ERR_BADOPTION);
        if (body.Body.Realm != Realm.RealmName)
            return Error(KerberosErrorCode.KDC_ERR_POLICY);
        var allowed = KdcOptions.Canonicalize | KdcOptions.RenewableOk | KdcOptions.Renewable;
        // 固定官方库给TGT设置Forwardable能力位，MIT据此在普通TGS请求中携带该位。
        // 允许这一个能力位不开放Forwarded、Proxy、Proxiable或附加票据。
        if (body is KrbTgsReq) allowed |= KdcOptions.Forwardable;
        if ((body.Body.KdcOptions & ~allowed) != 0 || body.Body.AdditionalTickets?.Length > 0)
            return Error(KerberosErrorCode.KDC_ERR_BADOPTION);
        return ReadOnlyMemory<byte>.Empty;
    }
    async Task Handle(TcpClient client)
    {
        using var requestDeadline = CancellationTokenSource.CreateLinkedTokenSource(cancellation.Token);
        requestDeadline.CancelAfter(TimeSpan.FromSeconds(8));
        try
        {
            var stream = client.GetStream();
            var header = new byte[4];
            await stream.ReadExactlyAsync(header, requestDeadline.Token);
            int length = BinaryPrimitives.ReadInt32BigEndian(header);
            if (length < 1 || length > 65536) { Interlocked.Increment(ref rejected); return; }
            var request = new byte[length];
            await stream.ReadExactlyAsync(request, requestDeadline.Token);
            var response = Policy(request);
            if (response.IsEmpty) response = await server.ProcessMessage(request);
            else Interlocked.Increment(ref rejected);
            if (response.Length < 1 || response.Length > 65536) throw new InvalidOperationException();
            BinaryPrimitives.WriteInt32BigEndian(header, response.Length);
            await stream.WriteAsync(header, requestDeadline.Token);
            await stream.WriteAsync(response, requestDeadline.Token);
            Interlocked.Increment(ref completed);
        }
        catch (Exception error) when (error is IOException || error is SocketException ||
            error is OperationCanceledException || error is ObjectDisposedException) { }
        catch (Exception) { Interlocked.Increment(ref malformed); }
    }
    public object Summary() => new { schema = 1, stopped = true, accepted, completed, rejected,
        malformed, as_requests = asRequests, tgs_requests = tgsRequests,
        active_connections = clients.Count, active_workers = workers.Values.Count(task => !task.IsCompleted) };
    public async ValueTask DisposeAsync()
    {
        cancellation.Cancel();
        listener.Stop();
        using var limit = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        await (accepting ?? Task.CompletedTask).WaitAsync(limit.Token);
        foreach (var client in clients.Values) client.Dispose();
        await Task.WhenAll(workers.Values).WaitAsync(limit.Token);
        cancellation.Dispose();
        slots.Dispose();
    }
}
static class Program
{
    static void WritePolicyProbes(string path)
    {
        // 仅生成无认证数据的拒绝向量；编码与解析均来自正式库，不生成测试成功票据。
        var probes = new Dictionary<string, string>();
        KrbTgsReq Request(KdcOptions option) => new()
        {
            Body = new KrbKdcReqBody
            {
                CName = new KrbPrincipalName { Type = PrincipalNameType.NT_PRINCIPAL, Name = new[] { "suite" } },
                Realm = Realm.RealmName,
                SName = new KrbPrincipalName { Type = PrincipalNameType.NT_SRV_HST, Name = new[] { "host", "localhost" } },
                KdcOptions = option,
                Till = DateTimeOffset.UtcNow.AddMinutes(5),
                Nonce = 42,
                EType = new[] { EncryptionType.AES256_CTS_HMAC_SHA1_96 }
            }
        };
        foreach (var option in new[] { KdcOptions.Forwarded, KdcOptions.Proxiable,
            KdcOptions.Proxy, KdcOptions.ConstrainedDelegation, KdcOptions.CNameInAdditionalTicket,
            KdcOptions.EncTktInSkey })
        {
            var request = Request(option);
            probes[option.ToString()] = Convert.ToBase64String(request.EncodeApplication().Span);
        }
        var crossRealm = Request(0);
        crossRealm.Body.Realm = "OTHER.TEST";
        probes["CrossRealm"] = Convert.ToBase64String(crossRealm.EncodeApplication().Span);
        var additional = Request(0);
        additional.Body.AdditionalTickets = new[] { new KrbTicket
        {
            Realm = Realm.RealmName,
            SName = additional.Body.SName,
            EncryptedPart = new KrbEncryptedData { EType = EncryptionType.AES256_CTS_HMAC_SHA1_96, Cipher = new byte[] { 0 } }
        } };
        probes["AdditionalTicket"] = Convert.ToBase64String(additional.EncodeApplication().Span);
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Write, FileShare.None);
        stream.SetLength(0);
        JsonSerializer.Serialize(stream, probes);
    }
    static void WriteKeytab(string path, KerberosKey key)
    {
        // 文件由Python以私有ACL预创建并核验，沿用原文件权限。
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Write, FileShare.None);
        stream.SetLength(0);
        using var writer = new BinaryWriter(stream);
        new KeyTable(key).Write(writer);
    }
    static async Task<bool> StopCommand()
    {
        var value = new char[5];
        for (int index = 0; index < value.Length; index++)
            if (await Console.In.ReadAsync(value.AsMemory(index, 1)) != 1) return false;
        return new string(value) == "stop\n";
    }
    static async Task<int> Main(string[] args)
    {
        try
        {
            if (args.Length != 1) return 1;
            var root = Path.GetFullPath(args[0]);
            if (!Directory.Exists(root)) return 1;
            var config = Krb5Config.Default();
            config.KdcDefaults.RegisterDefaultPkInitPreAuthHandler = false;
            config.KdcDefaults.ProxyEnabled = false;
            config.Defaults.AllowWeakCrypto = false;
            config.Defaults.DefaultRealm = Realm.RealmName;
            var realm = new Realm(config);
            WriteKeytab(Path.Combine(root, "client.keytab"), realm.Store.Entries["suite"].Key);
            WriteKeytab(Path.Combine(root, "server.keytab"), realm.Store.Entries["host/localhost"].Key);
            WritePolicyProbes(Path.Combine(root, "policy-probes.json"));
            var options = new KdcServerOptions { Configuration = config, DefaultRealm = Realm.RealmName,
                RealmLocator = name => name == Realm.RealmName ? realm : null, IsDebug = false };
            var listener = new BoundedKdc(options);
            bool stopped = false;
            try
            {
                listener.Start();
                Console.WriteLine(JsonSerializer.Serialize(new { schema = 1, ready = true,
                    realm = Realm.RealmName, client = "suite@SUITE.TEST", service = "host/localhost@SUITE.TEST",
                    kdc_port = listener.Port }));
                Console.Out.Flush();
                stopped = await StopCommand().WaitAsync(TimeSpan.FromSeconds(85));
            }
            finally { await listener.DisposeAsync(); }
            Console.WriteLine(JsonSerializer.Serialize(listener.Summary()));
            return stopped ? 0 : 1;
        }
        catch (Exception)
        {
            // 不把异常消息、私有路径或认证字节写到CI输出。
            Console.Error.WriteLine("隔离KDC宿主失败");
            return 1;
        }
    }
}
