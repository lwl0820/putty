/* 旧 IPC 的映射/SID 检查来自固定版 windows/pageant.c，传输不解析签名协议。 */
#include "putty.h"
#include "ssh.h"
#include "pageant.h"
#include "security-api.h"
#include "pa_windows.h"

#ifdef PA_WINDOWS_TEST_NAMESPACE
static char test_name[sizeof(PA_WINDOWS_TEST_NAMESPACE) + 33];
bool pa_windows_set_test_scope(const char *scope)
{
    if (test_name[0] || !scope || strlen(scope) != 32) return false;
    for (const char *p = scope; *p; ++p)
        if (!strchr("0123456789abcdef", *p)) return false;
    strcpy(test_name, PA_WINDOWS_TEST_NAMESPACE);
    strcat(test_name, "-"); strcat(test_name, scope);
    return true;
}
#define PA_IPC_CLASS (test_name[0] ? test_name : NULL)
#else
#define PA_IPC_CLASS "Pageant"
#endif

struct pa_windows_ipc {
    HANDLE stop, ready, request_ready, reply_ready, thread;
    HandleWait *wait;
    CRITICAL_SECTION lock;
    HWND window;
    pa_windows_request request;
    void *context;
    unsigned char *query, *reply;
    size_t query_size, reply_size;
};
const char *pa_windows_class_name(void) { return PA_IPC_CLASS; }
char *pa_windows_pipe_name(void)
{
#ifdef PA_WINDOWS_TEST_NAMESPACE
    if (!PA_IPC_CLASS) return NULL;
    return dupprintf("\\\\.\\pipe\\%s", PA_IPC_CLASS);
#else
    return agent_named_pipe_name();
#endif
}
void pa_windows_free(void *data) { sfree(data); }
void pa_windows_prepare(void)
{
    dll_hijacking_protection();
    enable_dit();
}
void *pa_windows_startup_lock(bool *exists, char **error)
{
    *error = NULL;
    *exists = false;
    if (!pa_windows_class_name()) { *error = dupstr("Pageant test namespace not initialised"); return NULL; }
    if (!got_advapi()) { *error = dupstr("Windows security APIs unavailable"); return NULL; }
#ifdef PA_WINDOWS_TEST_NAMESPACE
    char *name = dupprintf("Local\\%s-mutex", PA_IPC_CLASS);
#else
    char *name = agent_mutex_name();
#endif
    HANDLE mutex = lock_interprocess_mutex(name, error);
    sfree(name);
    if (!mutex) return NULL;
    char *pipe = pa_windows_pipe_name();
    WIN32_FIND_DATAA data;
    HANDLE found = FindFirstFileA(pipe, &data);
    sfree(pipe);
    *exists = found != INVALID_HANDLE_VALUE || FindWindowA(PA_IPC_CLASS, PA_IPC_CLASS) != NULL;
    if (found != INVALID_HANDLE_VALUE) FindClose(found);
    return mutex;
}
void pa_windows_startup_unlock(void *lock)
{ if (lock) unlock_interprocess_mutex((HANDLE)lock); }

/* 与旧版本一样同时接受用户 SID 和进程默认 owner SID。 */
static PSID default_sid(void)
{
    PSECURITY_DESCRIPTOR descriptor = NULL;
    PSID owner = NULL, copy = NULL;
    HANDLE process = OpenProcess(READ_CONTROL, FALSE, GetCurrentProcessId());
    if (process && p_GetSecurityInfo(process, SE_KERNEL_OBJECT, OWNER_SECURITY_INFORMATION,
                                    &owner, NULL, NULL, NULL, &descriptor) == ERROR_SUCCESS) {
        DWORD size = GetLengthSid(owner);
        copy = smalloc(size);
        if (!CopySid(size, copy, owner)) { sfree(copy); copy = NULL; }
    }
    if (descriptor) LocalFree(descriptor);
    if (process) CloseHandle(process);
    return copy;
}
static bool mapping_owned_by_user(HANDLE mapping)
{
    PSID user = get_user_sid(), legacy = default_sid(), owner = NULL;
    PSECURITY_DESCRIPTOR descriptor = NULL;
    bool valid = user && legacy && p_GetSecurityInfo(
        mapping, SE_KERNEL_OBJECT, OWNER_SECURITY_INFORMATION,
        &owner, NULL, NULL, NULL, &descriptor) == ERROR_SUCCESS &&
        (EqualSid(user, owner) || EqualSid(legacy, owner));
    /* get_user_sid 的缓存属于上游进程；legacy 是本次复制。 */
    sfree(legacy);
    if (descriptor) LocalFree(descriptor);
    return valid;
}
static bool answer_mapping(pa_windows_ipc *ipc, const char *name)
{
    HANDLE mapping = OpenFileMappingA(FILE_MAP_ALL_ACCESS, FALSE, name);
    if (!mapping) return false;
    unsigned char *view = NULL;
    bool success = false;
    if (!mapping_owned_by_user(mapping)) goto done;
    view = MapViewOfFile(mapping, FILE_MAP_WRITE, 0, 0, 0);
    if (!view) goto done;
    MEMORY_BASIC_INFORMATION info;
    if (VirtualQuery(view, &info, sizeof(info)) <
        offsetof(MEMORY_BASIC_INFORMATION, RegionSize) + sizeof(info.RegionSize) ||
        info.RegionSize < 5) goto done;
    size_t capacity = info.RegionSize - 4;
    unsigned length = GET_32BIT_MSB_FIRST(view);
    if (length > capacity || length > AGENT_MAX_MSGLEN - 4) {
        PUT_32BIT_MSB_FIRST(view, 1); view[4] = SSH_AGENT_FAILURE;
        success = true; goto done;
    }
    EnterCriticalSection(&ipc->lock);
    assert(!ipc->query && !ipc->reply);
    ipc->query = smalloc(length ? length : 1);
    memcpy(ipc->query, view + 4, length);
    ipc->query_size = length;
    LeaveCriticalSection(&ipc->lock);
    SetEvent(ipc->request_ready);
    HANDLE waits[2] = {ipc->stop, ipc->reply_ready};
    DWORD waited = WaitForMultipleObjects(2, waits, FALSE, INFINITE);
    EnterCriticalSection(&ipc->lock);
    if (waited == WAIT_OBJECT_0 + 1 && ipc->reply && ipc->reply_size <= capacity) {
        PUT_32BIT_MSB_FIRST(view, ipc->reply_size);
        memcpy(view + 4, ipc->reply, ipc->reply_size);
        success = true;
    } else {
        PUT_32BIT_MSB_FIRST(view, 1); view[4] = SSH_AGENT_FAILURE;
        success = waited == WAIT_OBJECT_0 || waited == WAIT_OBJECT_0 + 1;
    }
    sfree(ipc->reply); ipc->reply = NULL; ipc->reply_size = 0;
    LeaveCriticalSection(&ipc->lock);
  done:
    if (view) UnmapViewOfFile(view);
    CloseHandle(mapping);
    return success;
}
static LRESULT CALLBACK compatibility_window(HWND window, UINT message, WPARAM wp, LPARAM lp)
{
    if (message == WM_NCCREATE) {
        CREATESTRUCTA *created = (CREATESTRUCTA *)lp;
        SetWindowLongPtrA(window, GWLP_USERDATA, (LONG_PTR)created->lpCreateParams);
    }
    pa_windows_ipc *ipc = (pa_windows_ipc *)GetWindowLongPtrA(window, GWLP_USERDATA);
    if (message == WM_COPYDATA && ipc) {
        const COPYDATASTRUCT *copy = (const COPYDATASTRUCT *)lp;
        if (WaitForSingleObject(ipc->stop, 0) != WAIT_TIMEOUT || !copy ||
            copy->dwData != AGENT_COPYDATA_ID || !copy->lpData ||
            copy->cbData < 2 || copy->cbData > 4096) return 0;
        const char *name = copy->lpData;
        if (name[copy->cbData - 1] || memchr(name, 0, copy->cbData - 1)) return 0;
        return answer_mapping(ipc, name) ? 1 : 0;
    }
    return DefWindowProcA(window, message, wp, lp);
}
static DWORD WINAPI compatibility_thread(void *context)
{
    pa_windows_ipc *ipc = context;
    WNDCLASSA cls;
    memset(&cls, 0, sizeof(cls));
    cls.lpfnWndProc = compatibility_window;
    cls.hInstance = GetModuleHandleA(NULL);
    cls.lpszClassName = PA_IPC_CLASS;
    ATOM registered = RegisterClassA(&cls);
    if (registered) ipc->window = CreateWindowExA(0, PA_IPC_CLASS, PA_IPC_CLASS,
        WS_OVERLAPPED, 0, 0, 0, 0, NULL, NULL, cls.hInstance, ipc);
    SetEvent(ipc->ready);
    if (ipc->window) {
        /* 隐藏顶层窗口才能被旧 FindWindow 找到，不能改成 HWND_MESSAGE。 */
        while (MsgWaitForMultipleObjects(1, &ipc->stop, FALSE, INFINITE, QS_ALLINPUT) == WAIT_OBJECT_0 + 1) {
            MSG message;
            while (PeekMessageA(&message, NULL, 0, 0, PM_REMOVE)) {
                TranslateMessage(&message); DispatchMessageA(&message);
                if (WaitForSingleObject(ipc->stop, 0) != WAIT_TIMEOUT) break;
            }
        }
        DestroyWindow(ipc->window);
        ipc->window = NULL;
    }
    if (registered) UnregisterClassA(PA_IPC_CLASS, cls.hInstance);
    return 0;
}
static void got_request(void *context)
{
    pa_windows_ipc *ipc = context;
    EnterCriticalSection(&ipc->lock);
    unsigned char *data = ipc->query;
    size_t size = ipc->query_size;
    ipc->query = NULL; ipc->query_size = 0;
    LeaveCriticalSection(&ipc->lock);
    if (data) {
        ipc->request(ipc->context, data, size);
        smemclr(data, size); sfree(data);
    }
}
pa_windows_ipc *pa_windows_ipc_start(pa_windows_request request, void *context)
{
    if (!pa_windows_class_name() || !got_advapi()) return NULL;
    pa_windows_ipc *ipc = snew(pa_windows_ipc);
    memset(ipc, 0, sizeof(*ipc));
    InitializeCriticalSection(&ipc->lock);
    ipc->request = request; ipc->context = context;
    ipc->stop = CreateEventA(NULL, TRUE, FALSE, NULL);
    ipc->ready = CreateEventA(NULL, TRUE, FALSE, NULL);
    ipc->request_ready = CreateEventA(NULL, FALSE, FALSE, NULL);
    ipc->reply_ready = CreateEventA(NULL, FALSE, FALSE, NULL);
    if (!ipc->stop || !ipc->ready || !ipc->request_ready || !ipc->reply_ready) goto failure;
    ipc->thread = CreateThread(NULL, 0, compatibility_thread, ipc, 0, NULL);
    if (!ipc->thread) goto failure;
    WaitForSingleObject(ipc->ready, INFINITE);
    if (!ipc->window) goto failure;
    ipc->wait = add_handle_wait(ipc->request_ready, got_request, ipc);
    return ipc;
  failure:
    pa_windows_ipc_stop(ipc);
    return NULL;
}
void pa_windows_ipc_reply(pa_windows_ipc *ipc, const void *data, size_t size)
{
    if (WaitForSingleObject(ipc->stop, 0) != WAIT_TIMEOUT) return;
    static const unsigned char failure = SSH_AGENT_FAILURE;
    if (!size || size > AGENT_MAX_MSGLEN - 4) { data = &failure; size = 1; }
    EnterCriticalSection(&ipc->lock);
    assert(!ipc->reply);
    ipc->reply = smalloc(size); memcpy(ipc->reply, data, size); ipc->reply_size = size;
    LeaveCriticalSection(&ipc->lock);
    SetEvent(ipc->reply_ready);
}
void pa_windows_ipc_stop(pa_windows_ipc *ipc)
{
    if (!ipc) return;
    if (ipc->wait) delete_handle_wait(ipc->wait);
    if (ipc->stop) SetEvent(ipc->stop);
    /* 只在核心析构中等待 IPC 搬运线程；GUI Runtime::close 始终异步。
     * stop 同时解除 WndProc 的回复等待和消息等待，不依赖核心继续签名。 */
    if (ipc->thread) { WaitForSingleObject(ipc->thread, INFINITE); CloseHandle(ipc->thread); }
    if (ipc->stop) CloseHandle(ipc->stop);
    if (ipc->ready) CloseHandle(ipc->ready);
    if (ipc->request_ready) CloseHandle(ipc->request_ready);
    if (ipc->reply_ready) CloseHandle(ipc->reply_ready);
    if (ipc->query) { smemclr(ipc->query, ipc->query_size); sfree(ipc->query); }
    sfree(ipc->reply);
    DeleteCriticalSection(&ipc->lock);
    sfree(ipc);
}

/* 只有此 Pageant 宿主覆盖同步客户端入口，不改变共享 SSH 客户端。
 * 二次启动不能无限等候旧代理，否则核心再也无法处理异步 close。
 * 本地 PIPE_NOWAIT 不产生挂起 I/O，避免取消后释放 OVERLAPPED 的竞态。 */
#define PA_QUERY_TIMEOUT 8000
static bool query_failed;
bool pa_windows_query_failed(void) { return query_failed; }
static DWORD query_remaining(ULONGLONG started)
{
    ULONGLONG elapsed = GetTickCount64() - started;
    return elapsed < PA_QUERY_TIMEOUT ? (DWORD)(PA_QUERY_TIMEOUT - elapsed) : 0;
}
static bool query_pause(ULONGLONG started)
{
    DWORD remaining = query_remaining(started);
    if (!remaining) return false;
    Sleep(remaining < 10 ? remaining : 10);
    return query_remaining(started) != 0;
}
static HANDLE query_pipe(ULONGLONG started)
{
    char *name = pa_windows_pipe_name();
    if (!name) return INVALID_HANDLE_VALUE;
    HANDLE pipe = INVALID_HANDLE_VALUE;
    do {
        pipe = CreateFileA(name, GENERIC_READ | GENERIC_WRITE, 0, NULL,
                           OPEN_EXISTING, 0, NULL);
        if (pipe != INVALID_HANDLE_VALUE || GetLastError() != ERROR_PIPE_BUSY) break;
    } while (query_pause(started));
    sfree(name);
    if (pipe == INVALID_HANDLE_VALUE) return pipe;
    PSID owner = NULL, user = get_user_sid();
    PSECURITY_DESCRIPTOR descriptor = NULL;
    ULONG server_process = 0;
    bool valid = user && p_GetSecurityInfo(pipe, SE_KERNEL_OBJECT,
        OWNER_SECURITY_INFORMATION, &owner, NULL, NULL, NULL, &descriptor) == ERROR_SUCCESS &&
        EqualSid(user, owner) && GetNamedPipeServerProcessId(pipe, &server_process) && server_process;
    if (descriptor) LocalFree(descriptor);
    DWORD mode = PIPE_READMODE_BYTE | PIPE_NOWAIT;
    if (!valid || !query_remaining(started) ||
        !SetNamedPipeHandleState(pipe, &mode, NULL, NULL)) {
        CloseHandle(pipe); return INVALID_HANDLE_VALUE;
    }
    return pipe;
}
static bool query_transfer(HANDLE pipe, void *data, size_t size,
                           bool writing, ULONGLONG started)
{
    size_t done = 0;
    while (done < size && query_remaining(started)) {
        DWORD count = 0;
        BOOL success = writing ? WriteFile(pipe, (char *)data + done, (DWORD)(size - done), &count, NULL) :
            ReadFile(pipe, (char *)data + done, (DWORD)(size - done), &count, NULL);
        if (success && count) { done += count; continue; }
        if (!success) {
            if (GetLastError() != ERROR_NO_DATA) return false;
            /* 空的非阻塞管道允许重试；已断开的管道必须立即失败。 */
            if (!PeekNamedPipe(pipe, NULL, 0, NULL, NULL, NULL)) return false;
        }
        if (!query_pause(started)) break;
    }
    return done == size;
}
static bool query_named_pipe(strbuf *query, void **out, int *outlen, ULONGLONG started)
{
    HANDLE pipe = query_pipe(started);
    if (pipe == INVALID_HANDLE_VALUE) return false;
    unsigned char prefix[4], *response = NULL;
    bool success = false;
    if (!query_transfer(pipe, query->s, query->len, true, started) ||
        !query_transfer(pipe, prefix, sizeof(prefix), false, started)) goto done;
    unsigned length = GET_32BIT_MSB_FIRST(prefix);
    /* 原管道客户端上限针对正文；旧映射另外预留四字节前缀。 */
    if (!length || length > AGENT_MAX_MSGLEN) goto done;
    response = smalloc(length + 4);
    memcpy(response, prefix, 4);
    if (!query_transfer(pipe, response + 4, length, false, started)) goto done;
    *out = response; *outlen = (int)length + 4; response = NULL;
    success = true;
  done:
    if (response) { smemclr(response, GET_32BIT_MSB_FIRST(prefix) + 4); sfree(response); }
    CloseHandle(pipe);
    return success;
}
static void query_window(strbuf *query, void **out, int *outlen, ULONGLONG started)
{
    HWND window = FindWindowA(PA_IPC_CLASS, PA_IPC_CLASS);
    DWORD remaining = query_remaining(started);
    if (!window || !remaining) return;
    /* 同一个线程消息队列会忽略 SendMessageTimeout 的超时，不允许它重入核心。 */
    if (GetWindowThreadProcessId(window, NULL) == GetCurrentThreadId()) return;
    PSECURITY_DESCRIPTOR descriptor = NULL;
    PACL acl = NULL;
    char *error = NULL;
    if (!make_private_security_descriptor(FILE_MAP_ALL_ACCESS, &descriptor, &acl, &error)) {
        sfree(error); return;
    }
    SECURITY_ATTRIBUTES attributes = {sizeof(attributes), descriptor, FALSE};
    char *name = dupprintf("%sRequest%08lx-%016llx", PA_IPC_CLASS,
        (unsigned long)GetCurrentThreadId(), (unsigned long long)started);
    HANDLE mapping = CreateFileMappingA(INVALID_HANDLE_VALUE, &attributes,
        PAGE_READWRITE, 0, AGENT_MAX_MSGLEN, name);
    DWORD created_error = GetLastError();
    unsigned char *view = NULL;
    if (!mapping || created_error == ERROR_ALREADY_EXISTS) goto done;
    view = MapViewOfFile(mapping, FILE_MAP_WRITE, 0, 0, 0);
    if (!view) goto done;
    memcpy(view, query->s, query->len);
    COPYDATASTRUCT copy = {AGENT_COPYDATA_ID, (DWORD)strlen(name) + 1, name};
    DWORD_PTR result = 0;
    remaining = query_remaining(started);
    if (!remaining || !SendMessageTimeoutA(window, WM_COPYDATA, 0, (LPARAM)&copy,
        SMTO_BLOCK | SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT, remaining, &result) || !result) goto done;
    unsigned length = GET_32BIT_MSB_FIRST(view);
    if (!length || length > AGENT_MAX_MSGLEN - 4) goto done;
    *out = smalloc(length + 4); memcpy(*out, view, length + 4); *outlen = (int)length + 4;
  done:
    if (view) { smemclr(view, AGENT_MAX_MSGLEN); UnmapViewOfFile(view); }
    if (mapping) CloseHandle(mapping);
    sfree(name);
    LocalFree(descriptor); LocalFree(acl);
}
void agent_query_synchronous(strbuf *query, void **out, int *outlen)
{
    *out = NULL; *outlen = 0;
    if (query_failed) return;
    if (!pa_windows_class_name() || !got_advapi() || query->len < 5 ||
        query->len > AGENT_MAX_MSGLEN) return;
    ULONGLONG started = GetTickCount64();
    strbuf_finalise_agent_query(query);
    if (!query_named_pipe(query, out, outlen, started))
        query_window(query, out, outlen, started);
    /* 原枚举会连续尝试多个协议版本。失联后本次客户端进程快速失败，
     * 不让同一悬挂对端反复消耗预算；重新启动才会重新连接。 */
    if (!*out) query_failed = true;
}
