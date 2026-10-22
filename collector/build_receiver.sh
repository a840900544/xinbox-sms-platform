#!/bin/sh
set -eu
umask 077
sdk_dir=${JEGO_SDK_DIR:-/opt/jego-research/sdk-linux}
target_dir=${JEGO_RECEIVER_DIR:-/opt/jego-research}
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
g++ -std=c++11 -fPIC -shared "$source_dir/linux_sms_receiver_health.cpp" \
  -I"$sdk_dir/include" -L"$sdk_dir/lib/linux/x86_64" -ljcsdk \
  -Wl,-soname,libjego_sms_receiver_health.so -o "$target_dir/libjego_sms_receiver_health.stage.so"
g++ -std=c++11 -pthread "$source_dir/linux_sms_daemon.cpp" \
  -I"$sdk_dir/include" -I"$sdk_dir/include/JCSDK" \
  -L"$sdk_dir/lib/linux/x86_64" -L"$target_dir" \
  -Wl,-rpath,"$sdk_dir/lib/linux/x86_64" -Wl,-rpath,"$target_dir" \
  -ljcsdk -l:libjego_sms_receiver_health.stage.so \
  -l:libX11.so.6 -l:libXext.so.6 -l:libXcomposite.so.1 \
  -l:libXfixes.so.3 -l:libXdamage.so.1 -l:libacl.so.1 \
  -o "$target_dir/linux_sms_daemon.new"
# Switch files atomically after compilation. Restart the service separately.
mv "$target_dir/libjego_sms_receiver_health.stage.so" "$target_dir/libjego_sms_receiver_health.so"
mv "$target_dir/linux_sms_daemon.new" "$target_dir/linux_sms_daemon"
cp "$source_dir/encrypted_sms_sink.py" "$target_dir/encrypted_sms_sink.py"
