// Bounded normal SDK login probe. Credentials are read from a protected file.
#include "JCClient.h"
#include "JCCall.h"
#include "JCMediaDevice.h"
#include <ctime>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <thread>
#include <queue>
#include <mutex>
#include <csignal>
static volatile std::sig_atomic_t stopping=0;
static void stop_handler(int){stopping=1;}
extern "C" int Mtc_CliDbSetTlsOnly(bool);
extern "C" int Mtc_CliDbSetRootDerData(const char*);
extern "C" int Mtc_CliOpen(const char*);
extern "C" int Mtc_ProfSaveProvision(void);
extern "C" void Mtc_CliClose(void);
extern "C" void* Mtc_CliGetClient(void);
extern "C" bool Mtc_CallDbGetDirectConnectEnable(void);
extern "C" int Mtc_CallDbSetDirectConnectEnable(bool);
extern "C" bool Mtc_CliDbGetForceLogin(void);
extern "C" int Mtc_CliSetPublicParam(const char*,const char*);
extern "C" int jego_query_own_missed_calls(void*);
extern "C" int jego_receiver_health(void*);
using PacketCallback = void (*)(const char*, const char*, const char*, const char*,
                                std::size_t, int, void*);
extern "C" int jego_attach_sms_receiver(void*, PacketCallback, void*);
extern "C" int jego_detach_sms_receiver();
extern "C" int jego_register_own_sip(void*, const char*, const char*);

static std::string hex(const char* value, std::size_t size) {
  const char* digits = "0123456789abcdef";
  std::string result; result.reserve(size*2);
  for (std::size_t i=0;i<size;++i) {
    const unsigned char c = value[i]; result += digits[c>>4]; result += digits[c&15];
  }
  return result;
}
static void receive_sms(const char* from, const char* time, const char* gateway,
                        const char* body, std::size_t size, int offline, void* context) {
  FILE* sink = static_cast<FILE*>(context);
  const std::string record = "{\"sender\":\"" + hex(from,std::strlen(from)) +
    "\",\"time\":\"" + hex(time,std::strlen(time)) +
    "\",\"gateway\":\"" + hex(gateway,std::strlen(gateway)) +
    "\",\"body\":\"" + hex(body,size) + "\",\"offline\":" + std::to_string(offline) + "}\n";
  const auto written = std::fwrite(record.data(),1,record.size(),sink);
  const int flushed = std::fflush(sink);
  std::printf("PROBE sms_received=1 bytes=%zu sink_write_ok=%d\n", size,
              written==record.size() && flushed==0);
  std::fflush(stdout);
  if(written!=record.size() || flushed!=0) std::_Exit(12);
}


class MediaObserver final : public JCMediaDeviceCallback {
public:
 void onCameraUpdate() override {}
 void onRenderReceived(JCMediaDeviceVideoCanvas*) override {}
 void onRenderStart(JCMediaDeviceVideoCanvas*) override {}
};
class CallObserver final : public JCCallCallback {
public:
 FILE* sink=nullptr;
 void record(JCCallItem* item,const char* event) {
  std::printf("PROBE call_callback=%s item_present=%d sink_present=%d direction=%d state=%d\n",
    event,item!=nullptr,sink!=nullptr,item?(int)item->getDirection():-1,item?(int)item->getState():-1);
  std::fflush(stdout);
  if (!sink || !item || item->getDirection()!=JCCallDirectionIn) return;
  const char* sender=item->getUserId(); const char* cid=item->getServerCallId();
  std::string value="{\"kind\":\"call\",\"event\":\""+std::string(event)+"\",\"sender\":\""+hex(sender?sender:"",sender?std::strlen(sender):0)+"\",\"call_id\":\""+hex(cid?cid:"",cid?std::strlen(cid):0)+"\",\"state\":"+std::to_string((int)item->getState())+",\"time\":"+std::to_string((long long)item->getCreateTime())+"}\n";
  bool ok=std::fwrite(value.data(),1,value.size(),sink)==value.size() && std::fflush(sink)==0;
  std::printf("PROBE call_event_received=1 sink_write_ok=%d\n",ok);std::fflush(stdout);
 }
 void onCallItemAdd(JCCallItem* i) override {record(i,"incoming");}
 void onCallItemRemove(JCCallItem* i,JCCallReason,const char*) override {record(i,"ended");}
 void onCallItemUpdate(JCCallItem* i,JCCallItemChangeParam) override {record(i,"updated");}
 void onMissedCallItem(JCCallItem* i) override {record(i,"missed");}
 void onMessageReceive(const char*,const char*,JCCallItem*) override {}
 void onDtmfReceived(JCCallItem*,JCCallDtmfValue) override {}
 void onEarlyMediaReceived(JCCallItem*) override {}
};
class Observer final : public JCClientCallback {
public:
  std::atomic<int> outcome{0};
  std::mutex mutex;
  std::queue<void*> events;
  void drain(JCClient* client) {
    std::queue<void*> pending;
    { std::lock_guard<std::mutex> guard(mutex); pending.swap(events); }
    while (!pending.empty()) { client->driveEvent(pending.front()); pending.pop(); }
  }
  void onLogin(bool ok, JCClientReason reason) override {
    std::printf("PROBE login_ok=%d reason=%d\n", ok, (int)reason);
    std::fflush(stdout); outcome.store(ok ? 1 : -1);
  }
  void onLogout(JCClientReason reason) override {
    std::printf("PROBE logout_reason=%d\n", (int)reason); outcome.store(-2);
  }
  void onClientStateChange(JCClientState state, JCClientState old) override {
    std::printf("PROBE state=%d old=%d\n", (int)state, (int)old);
  }
  void onOnlineMessageSendResult(int, bool) override {}
  void onOnlineMessageReceive(const char* from, const char* text) override {
    std::printf("PROBE sdk_online_message=1 sender_length=%zu body_length=%zu\n",
                from ? std::strlen(from) : 0, text ? std::strlen(text) : 0);
    std::fflush(stdout);
  }
  void onDriveEvent(void* event) override {
    std::lock_guard<std::mutex> guard(mutex); events.push(event);
  }
};

int main(int argc, char** argv) {
  if (argc < 2 || argc > 3) return 2;
  std::ifstream input(argv[1]);
  std::string account, token, key, server, root, sip_password;
  if (!std::getline(input, account) || !std::getline(input, token) ||
      !std::getline(input, key) || !std::getline(input, server) ||
      !std::getline(input, root)) return 3;
  std::getline(input, sip_password);
  for (auto* line : {&account, &token, &key, &server, &root})
    if (!line->empty() && line->back() == '\r') line->pop_back();
  std::signal(SIGTERM,stop_handler);
  std::signal(SIGINT,stop_handler);
  std::signal(SIGPIPE,SIG_IGN);
  Observer observer;
  JCClientCreateParam create;
  create.autoDriveEvent = false;
  create.sdkLogLevel = std::getenv("JEGO_PRIVATE_DEBUG") ? JCLogLevelDebug : JCLogLevelError;
  std::snprintf(create.sdkInfoDir,sizeof(create.sdkInfoDir),"%s",std::getenv("JEGO_INFO_DIR")?std::getenv("JEGO_INFO_DIR"):"/opt/jego-research/probe-info");
  std::snprintf(create.sdkLogDir,sizeof(create.sdkLogDir),"%s",std::getenv("JEGO_LOG_DIR")?std::getenv("JEGO_LOG_DIR"):"/opt/jego-research/probe-log");
  JCClient* client = createJCClient(key.c_str(), &observer, &create);
  if (!client || client->getState() == JCClientStateNotInit) {
    std::puts("PROBE initialization_failed"); return 4;
  }
  const int profile_result = Mtc_CliOpen(nullptr);
  std::printf("PROBE local_profile_open=%d\n", profile_result);
  if (profile_result != 0) { destroyJCClient(); return 7; }
  const int root_result = Mtc_CliDbSetRootDerData(root.c_str());
  const int tls_result = Mtc_CliDbSetTlsOnly(true);
  Mtc_CallDbSetDirectConnectEnable(false);
  std::printf("PROBE root_config=%d tls_config=%d\n", root_result, tls_result);
  if (root_result != 0 || tls_result != 0) { destroyJCClient(); return 5; }
  std::printf("PROBE save_profile=%d\n", Mtc_ProfSaveProvision());
  Mtc_CliClose();
  FILE* shared_sink = argc==3 ? popen("python3 /opt/jego-research/encrypted_sms_sink.py", "w") : nullptr;
  MediaObserver media_observer;
  CallObserver call_observer; call_observer.sink=shared_sink;
  bool media_created=false, calls_created=false;
  try {
    auto media=createJCMediaDevice(client,&media_observer); media_created=media!=nullptr;
    if(media) calls_created=createJCCall(client,media,&call_observer)!=nullptr;
  } catch(...) { }
  std::printf("PROBE call_observer_initialized=%d\n",calls_created); std::fflush(stdout);
  client->setServerAddress(server.c_str());
  JCClientLoginParam login;
  login.autoCreateAccount = false;
  login.stgEnable = false;
  // This SDK constructor leaves this field untouched. Match the app's
  // explicit endpoint mode instead of passing an indeterminate bool.
  login.directConnectEnable = false;
  login.timeout = 40;
  // Original JCClientImpl.login does not set a terminal type. Preserve the
  // vendor constructor's default instead of assigning a separate PC endpoint.
  login.terminalType[0] = '\0';
  std::puts("PROBE terminal_type=sdk_default");
  // Original Android app uses login and Mtc_CliDbSetForceLogin(true).
  // A normal login takes over an existing session of the same terminal type.
  std::printf("PROBE state_before=%d\n", (int)client->getState());
  const bool accepted = client->login(account.c_str(), token.c_str(), &login);
  std::puts("PROBE login_mode=normal");
  std::printf("PROBE request_accepted=%d\n", accepted);
  std::fflush(stdout);
  for (int i=0; observer.outcome.load()==0 && i<(accepted?450:20); ++i)
  {
    observer.drain(client);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  const int result = observer.outcome.load();
  std::printf("PROBE completed=%d\n", result);
  if (result==1) {
    // Original JCNewPush.init -> MtcEngine.openNewPush enables this property.
    // This is not registration of a fabricated vendor/APNs push token.
    std::printf("PROBE integrated_push_config_result=%d\n",
      Mtc_CliSetPublicParam("Notify.IntegratedPush","1"));
    std::printf("PROBE force_login_effective=%d\n",Mtc_CliDbGetForceLogin());
    std::printf("PROBE endpoint_config_result=%d\n", Mtc_CallDbSetDirectConnectEnable(false));
    std::printf("PROBE direct_connect_effective=%d\n", Mtc_CallDbGetDirectConnectEnable());
    std::fflush(stdout);
    if (argc==3) {
      const int seconds = std::stoi(argv[2]);
      if (seconds<0 || seconds>600) return 8;
      FILE* sink = shared_sink;
      const int attached = sink ? jego_attach_sms_receiver(Mtc_CliGetClient(), receive_sms, sink) : -1;
      std::printf("PROBE receiver_attached=%d listen_seconds=%d\n", attached, seconds);
      std::fflush(stdout);
      call_observer.sink=sink;
      if (attached==0) {
        if (!sip_password.empty() && sip_password.back()=='\r') sip_password.pop_back();
        if (!sip_password.empty()) {
          std::printf("PROBE sip_registration_requested=%d\n",
                      jego_register_own_sip(Mtc_CliGetClient(), account.c_str(), sip_password.c_str()));
          std::fflush(stdout);
        }
        long long unhealthy_ticks=0;
        for (long long i=0; !stopping && observer.outcome.load()==1 && (seconds==0 || i<seconds*10); ++i) {
          observer.drain(client);
          // Original Android MtcEngine.fetchCalls uses this native query.
          // Query once after startup; callback delivery remains asynchronous.
          if(i==100 && calls_created && std::getenv("JEGO_QUERY_CALL_DIAGNOSTIC")) {
            std::printf("PROBE missed_call_query_requested=%d\n",jego_query_own_missed_calls(Mtc_CliGetClient()));
            std::fflush(stdout);
          }
          if(i%600==0) {
            int health=jego_receiver_health(Mtc_CliGetClient());
            if(health==3 || health==5) unhealthy_ticks=0; else unhealthy_ticks+=600;
            const char* target=std::getenv("JEGO_HEALTH_FILE");
            std::string health_path=target?target:"/opt/jego-research/receiver-health.json";
            std::ofstream out(health_path+".tmp");
            out<<"{\"heartbeat\":"<<std::time(nullptr)<<",\"client_status\":"<<health<<",\"calls_initialized\":"<<(calls_created?"true":"false")<<"}";
            out.close(); std::rename((health_path+".tmp").c_str(),health_path.c_str());
            if(unhealthy_ticks>1800) {observer.outcome.store(-2);break;}
          }
          if(i>0 && i%12000==0 && !sip_password.empty()) {
            int rc=jego_register_own_sip(Mtc_CliGetClient(),account.c_str(),sip_password.c_str());
            std::printf("PROBE sip_renewal_requested=%d\n",rc);std::fflush(stdout);
          }
          std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        jego_detach_sms_receiver();
      }


    }
    client->logout();
    for (int i=0;i<10;++i) {
      observer.drain(client);
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
  }
  if(calls_created) destroyJCCall();
  if(media_created) destroyJCMediaDevice();
  call_observer.sink=nullptr;
  if(shared_sink) pclose(shared_sink);
  destroyJCClient();
  return result==1 && observer.outcome.load()!=-2 ? 0 : 6;
}

