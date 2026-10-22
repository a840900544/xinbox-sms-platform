// Research adapter inferred from the supplied APK. Not an end-to-end collector.
// Requires the vendor SDK under the user's applicable license.
#include "lemon/Client.h"
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <map>
#include <mutex>
#include <atomic>

// Callback strings are borrowed and valid only during the callback. Copy them
// before returning. The callback must not throw or recursively detach itself.
using PacketCallback = void (*)(const char* from, const char* time,
                               const char* gateway, const char* body,
                               std::size_t body_size, int offline, void* context);

namespace {
std::mutex lifecycle;
Client::Client* owner = nullptr;
Client::MessageReceiverPtr receiver;
bool receive_work_held = false;
bool previous_channel_enabled = false;
Common::ApplicationPtr observed_application;
Common::ServerCallListenerPtr protocol_observer;
std::atomic<unsigned long> rpc_seen{0}, voice_rpc_seen{0};
class VoiceProtocolObserver final : public Common::ServerCallListener {
 public:
  void onServerCallFinished(const Common::String&,const Common::String& command,
                           bool success,const Common::String&,int period) override {
    ++rpc_seen;
    // Do not emit OIDs, arbitrary method names, error text or message bodies.
    const char* cmd=command.c_str();
    const bool invited=std::strcmp(cmd,"onInvited")==0 ||
      std::strcmp(cmd,"onInvited.CallClientEx.CallEx")==0;
    if(invited || std::strstr(cmd,".CallClientEx.CallEx")) {
      ++voice_rpc_seen;
      const char* kind=invited?"invited":
        (std::strstr(cmd,"onReleased")==cmd?"released":"other_voice");
      std::printf("PROBE voice_rpc=%s success=%d elapsed_ms=%d\n",kind,success,period);
      std::fflush(stdout);
    } else {
      const char* kind=std::strcmp(cmd,"notifyCalls")==0 ||
        std::strcmp(cmd,"notifyCalls.ClientEx.Client")==0 ? "notifications" : "other";
      std::printf("PROBE inbound_rpc=%s success=%d method_length=%zu elapsed_ms=%d\n",
        kind,success,std::strlen(cmd),period);
      std::fflush(stdout);
    }
  }
};

class SmsReceiver final : public Client::MessageReceiver {
 public:
  SmsReceiver(PacketCallback callback, void* context)
      : callback_(callback), context_(context) {}

  void onOnlineMessage(const Common::String&,
                       const std::map<Common::String, Common::String>& params,
                       const Common::Stream& message) override {
    deliver(params, message, 0);
  }

  void onOfflineMessage(const Common::String&,
                        const std::map<Common::String, Common::String>& params,
                        const Common::Stream& message) override {
    deliver(params, message, 1);
  }

 private:
  void deliver(const std::map<Common::String, Common::String>& params,
               const Common::Stream& message, int offline) noexcept {
    try {
      std::printf("PROBE sip_request_received=1 offline=%d parameter_count=%zu\n",
                  offline, static_cast<std::size_t>(params.size()));
      std::fflush(stdout);
      const auto from = params.find(Common::String("Sip.Message.From"));
      const auto time = params.find(Common::String("Sip.Message.Time"));
      const auto gateway = params.find(Common::String("Sip.MessageGw.Oid"));
      // Ignore unrelated Sip.Request events. Do not invent missing sender/time.
      if (from == params.end() || time == params.end()) return;
      const Common::String body = message.toString();
      callback_(from->second.c_str(), time->second.c_str(),
                gateway == params.end() ? "" : gateway->second.c_str(),
                body.c_str(), static_cast<std::size_t>(body.size()),
                offline, context_);
    } catch (...) {
      // Do not propagate exceptions across a vendor callback boundary.
    }
  }
  PacketCallback callback_;
  void* context_;
};
}

// Pass a Client pointer obtained through the SDK's documented Mtc_CliGetClient,
// in an isolated client process. Never patch another application's process.
extern "C" int jego_attach_sms_receiver(void* client, PacketCallback callback,
                                        void* context) noexcept {
  std::lock_guard<std::mutex> guard(lifecycle);
  if (!client || !callback || owner) return 1;
  try {
    Client::Client* candidate = static_cast<Client::Client*>(client);
    Client::MessageReceiverPtr pending(new SmsReceiver(callback, context));
    if (!candidate->addMessageReceiver(Common::String("Sip.Request"), pending))
      return 2;
    receiver = pending;
    owner = candidate;
    previous_channel_enabled = candidate->getChannelEnable();
    std::printf("PROBE channel_before=%d work_mode_before=%d\n",
                previous_channel_enabled ? 1 : 0, candidate->isInWorkMode() ? 1 : 0);
    candidate->setClientBackground(false);
    candidate->setChannelEnable(true);
    candidate->setAliveEnable(true,60);
    candidate->setClientAlwaysTryAlive(true);
    candidate->workModeIncRef();
    receive_work_held = true;
    observed_application=candidate->getApplication();
    if(observed_application) {
      protocol_observer=Common::ServerCallListenerPtr(new VoiceProtocolObserver());
      observed_application->setServerCallListener(protocol_observer);
      std::puts("PROBE voice_rpc_observer_attached=1");
    }
    std::printf("PROBE channel_after=%d work_mode_after=%d\n",
                candidate->getChannelEnable() ? 1 : 0, candidate->isInWorkMode() ? 1 : 0);
    std::fflush(stdout);
    return 0;
  } catch (...) {
    return 3;
  }
}

// Stop SDK event dispatch and wait for callbacks to finish before detaching;
// keep the Client alive until detach returns.
extern "C" int jego_detach_sms_receiver() noexcept {
  std::lock_guard<std::mutex> guard(lifecycle);
  if (!owner) return 0;
  try {
    owner->removeMessageReceiver(Common::String("Sip.Request"));
    if(observed_application) observed_application->setServerCallListener(Common::ServerCallListenerPtr());
    observed_application=Common::ApplicationPtr();
    protocol_observer=Common::ServerCallListenerPtr();
    if (receive_work_held) { owner->workModeDecRef(); receive_work_held = false; }
    owner->setChannelEnable(previous_channel_enabled);
    owner = nullptr;
    receiver = Client::MessageReceiverPtr();
    return 0;
  } catch (...) {
    return 3;
  }
}

// Normal authenticated SIP registration derived from Mtc_SmgwUpdateStatus(4).
// Wire evidence: version short(1), short(0), own account, string/string map;
// method updateStatus.SipGatewayPub.SipGateway on #SipGateway.
class SipStatusResult final : public Common::AgentAsync {
public:
  void cmdResult(int result, const Common::IputStreamPtr& response,
                 const Common::ObjectPtr&) override {
    std::printf("PROBE sip_registration_rpc_result=%d\n", result);
    if (response) {
      std::printf("PROBE sip_registration_response_remaining=%d\n", response->remain());
      try {
        // Match the SDK's generated RPC result helpers. The returned stream
        // contains exception/final metadata; it is not a pair of version shorts.
        Common::ObjectAgent::processFirst(result, response);
        bool accepted = false;
        response->read(accepted);
        Common::ObjectAgent::processFinal(response);
        std::printf("PROBE sip_registration_return_flag=%d\n", accepted ? 1 : 0);
      } catch (...) { std::puts("PROBE sip_registration_response_parse_failed=1"); }
    }
    std::fflush(stdout);
  }
};

extern "C" int jego_register_own_sip(void* client, const char* account,
                                     const char* password) noexcept {
  if (!client || !account || !password || !*password) return 1;
  try {
    auto* sdk = static_cast<Client::Client*>(client);
    const Common::String sdk_account = sdk->getAccountId();
    std::printf("PROBE sdk_account_length=%zu supplied_account_length=%zu\n",
                static_cast<std::size_t>(sdk_account.size()), std::strlen(account));
    std::fflush(stdout);
    // The authenticated SDK account identifier is opaque and differs from the
    // SIP telephone number. The caller verifies ownership before creating this
    // client; do not reject a successful login by comparing these two namespaces.
    if (sdk_account.empty()) return 2;
    auto agent = sdk->createAgent(Common::String("#SipGateway"), true);
    if (!agent) return 3;
    agent->setRqstTimeout(20);
    Common::StrStrMap fields;
    fields["SipFlag"] = "4";
    fields["AuthnamePrefix"] = "CMIJEGOM";
    fields["ChannelId"] = "kcc";
    fields["DeleteIntCode"] = "1";
    fields["SipNumber"] = account;
    fields["SipPwd"] = password;
    auto out = Common::OputStream::create(Common::StreamData);
    out->write(static_cast<short>(1)); out->write(static_cast<short>(0));
    // APK 0x52be30 obtains Arc_AcGetAid (via 0x6af9a8 -> 0x65761c).
    // Its result is the first RPC argument; SipNumber separately holds the phone.
    out->write(sdk_account);
    std::puts("PROBE sip_registration_identity=authenticated_sdk_account");
    Common::__write_StrStrMap(out, fields);
    Common::AgentAsyncPtr callback(new SipStatusResult());
    agent->ex_async(callback, Common::String("updateStatus.SipGatewayPub.SipGateway"),
                    out, Common::CallParamsPtr(), Common::ObjectPtr());
    return 0;
  } catch (...) { return 4; }
}

extern "C" int jego_receiver_health(void* client) noexcept {
  if (!client) return -1;
  try {
    auto* sdk=static_cast<Client::Client*>(client);
    sdk->checkRequests();
    Client::ErrorReason reason;
    auto status=sdk->getClientStatus(reason);
    std::printf("PROBE heartbeat client_status=%d reason=%d channel=%d work=%d\n",(int)status,(int)reason,sdk->getChannelEnable(),sdk->isInWorkMode());
    std::printf("PROBE rpc_observed=%lu voice_rpc_observed=%lu\n",rpc_seen.load(),voice_rpc_seen.load());
    std::fflush(stdout);
    return (int)status;
  } catch (...) { return -1; }
}

// Same own-account query as Mtc_CallQueryMissed, with observable RPC outcome.
class MissedQueryResult final : public Common::AgentAsync {
public:
 void cmdResult(int result,const Common::IputStreamPtr& response,const Common::ObjectPtr&) override {
  std::printf("PROBE missed_call_query_rpc_result=%d\n",result);
  if(result==0 && response) try {
   Common::ObjectAgent::processFirst(result,response);
   bool accepted=false;response->read(accepted);
   Common::StrStrMap records;Common::__read_StrStrMap(response,records);
   Common::ObjectAgent::processFinal(response);
   // Values may contain callers. Do not log them or assume a record schema.
   std::printf("PROBE missed_call_query_accepted=%d map_entries=%zu\n",accepted,records.size());
  } catch(...) {std::puts("PROBE missed_call_query_parse_failed=1");}
  std::fflush(stdout);
 }
};
extern "C" int jego_query_own_missed_calls(void* client) noexcept {
 if(!client)return 1;
 try {
  auto* sdk=static_cast<Client::Client*>(client);
  auto agent=sdk->createAgent(Common::String("#CallServerEx"),true);
  if(!agent)return 2;
  agent->setRqstTimeout(20);
  auto out=Common::OputStream::create(Common::StreamData);
  out->write(static_cast<short>(1));out->write(static_cast<short>(0));
  out->write(static_cast<Common::Long>(0));
  Common::StrStrMap fields;fields["Retrieve.Command"]="getMissedCallsInJson";
  Common::AgentAsyncPtr callback(new MissedQueryResult());
  agent->ex_async(callback,Common::String("retrieve.CallServerEx.CallEx"),out,
    Common::CallParams::create(fields),Common::ObjectPtr());
  return 0;
 }catch(...){return 3;}
}
