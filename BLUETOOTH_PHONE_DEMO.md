# Bluetooth SIM call setup on this PC

**Current demo route: local SIP on this PC.** The user selected local SIP after
receiver keypad tones were absent from the Bluetooth path. See `LOCAL_SIP_DEMO.md`
and run `scripts/Run-LocalSipDemo.ps1`. The Bluetooth findings below remain historical;
`Run-PhoneDemo.ps1` refuses to run while the caller uses the local SIP route.

The RTX 4060 PC runs ASR, the language model and speech generation. A dedicated
`CollegePhoneGateway` WSL distribution runs BlueZ and Asterisk's mobile channel.
The phone supplies the SIM and places the cellular call. The configured receiver
and expected SIM number are in the private
`runtime/deployment/bluetooth/call-target.json`; the test uses fictional student
marks. A SIM number is not a caller-ID override: on a dual-SIM phone, choose the
intended SIM as its default for voice calls.

This is a real cellular call placed by the phone. Bluetooth carries call-control
commands and should carry the assistant's two-way voice audio to the phone; it
does not replace the cellular network connection to the receiving number.

## Verified so far

### Keypad delivery investigation — 9 October

The live receiver reported that pressing 1/2/3 repeated the menu. Auxiliary ARI
digit events were being discarded by the internal-channel filter; the event loop
now forwards those events to the registered parent channel before that filter.
This correction passes the affected software checks, but did **not** resolve the
live keypad issue.

44 affected checks passed, including early menu keys, auxiliary-channel event
forwarding, in-band tone detection, diagnostic duration limits and capture stopping
before identity. Empty menu timeouts are now labeled `keypad_timeout` in latency
reports rather than incorrectly reporting a received DTMF digit.

Diagnostic call `5da46c102b754bd9965a01d511776d7f` answered on the configured demo
receiver. Three menu captures contained incoming PCMU audio but no accepted digit
or ARI keypad event. A bounded, private Bluetooth capture before Asterisk's DSP
contained 81.849 seconds of inbound audio and no detectable dual-frequency keypad
tones, including after gain normalization. Its final spoken control phrase was
recognized as "Testing 1,2,3". Thus incoming speech reaches the PC; the observed
failure occurs before application tone detection, rather than the DSP stripping
the keys. This does not establish which phone/carrier component omits them.

Strict keypad selection is currently unverified and fails on this live path.
Software cannot reconstruct a missing button event from silence. A spoken-number
backup or a SIP connection delivering DTMF is needed if the phone/network cannot
supply keypad audio. Diagnostic audio is stored locally and menu capture is disabled
after investigation; ordinary identity/questions are not additionally captured.

### Multilingual keypad menu and open identity replies — latest update

The opening now plays an English introduction, then "Press 1 for English",
"हिंदी के लिए 2 दबाइए", and "ಕನ್ನಡಕ್ಕಾಗಿ 3 ಅನ್ನು ಒತ್ತಿ". Each instruction is
generated in its corresponding native voice and joined into one cached 8 kHz WAV.
The caller deployment has `LANGUAGE_MENU_KEYPAD_ONLY=true`: it accepts menu keys
1/2/3 during playback, stops the prompt when a choice arrives, and does not run
speech recognition for language selection. The answer timeout starts after the
prompt ends. No input leads to at most three menu attempts.

After selection, identity is requested in that language. The English prompt is
"Can you confirm your identity? Am I speaking with {parent}?"; Hindi and Kannada
use equivalent native prompts. The parent name comes from the demo/CSV record,
currently zulkharnain. Replies are not restricted to yes/no: registered names,
common self-confirmations and other clear natural confirmations can proceed.
The existing local model interprets otherwise unmatched named-parent replies;
uncertain/unrelated replies, different claimed names and negative answers do not
release marks. A model timeout or invalid classification asks for clarification.
This is conversational identity confirmation, not independent proof of identity.

92 affected checks passed. Eight no-call text cases using the deployed local model
included English/Hindi/Kannada self-confirmations, unrelated questions and wrong
identities. The native menu WAV and all three identity prompts were prepared through
the actual speech service. Subsequent live handset tests failed to deliver receiver
keypad choices; see the investigation above. Identity prompts could not be reached
through the keypad on this path.

Use the demo command below as before; **press 1, 2 or 3** instead of saying the
language. Confirm naturally or state the registered parent name, then ask a marks
question. Slightly slower speech and answers only on request remain enabled.

### Speaking pace and unsolicited results — after English receiver feedback

The caller now sends VITS `speaking_rate=0.95`. Native synthesis adjusts predicted
phoneme duration while keeping its decoder and 22050 Hz sample rate. The matched
sample check (same random seed for both generations) measured 3.843 seconds at
the original rate and 3.994 seconds at the new rate: about 4% longer, a slight
slowdown. Cache keys include speaking rate, so old faster speech is not reused.

After identity confirmation, the assistant introduces the student and asks what
the parent would like to know. It does not automatically read all marks. Switching
children without a marks question also asks for the next question. "Say that again"
repeats the last requested answer; a full summary requires an explicit marks or
summary request. Acknowledgements and punctuation-only recognizer outputs do not
trigger result generation. Identity and opt-out checks remain in place.

These changes were checked without dialing. To run another real demo to
+19995550123 from PowerShell with the existing gateway and Docker services running:

```powershell
cd 'C:\Users\mohda\Downloads\College-Parent-Caller-2026-10-08\Cursor-MajorProject'
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Run-PhoneDemo.ps1 -ExpectedNumber YOUR_AUTHORIZED_E164_NUMBER
```

The script checks that the fictional demo record points to the expected number
and the paired phone is connected and Free, prepares cached audio, then originates
one cellular call. Press 1 for English, confirm zulkharnain naturally, and ask a specific question.
It should wait for that question before sharing marks. New pace and conversation
behavior still need the receiver's live assessment.

### BlueZ profile integration — 2026-10-09 resumed session

The module now accepts BlueZ-managed RFCOMM connections through a root-only Unix
socket instead of directly connecting phones or publishing its own SDP record.
`scripts/phone_hfp_profile.py` registers Handsfree UUID 0x111e, version 1.5,
features 4, channel 1 with BlueZ ProfileManager. Both connection directions are
enabled. The profile process admits only the selected authenticated phone and
must remain running. Its disconnect handling closes the shared connection so
Asterisk can reconnect cleanly.

Verified with the installed module: BlueZ's controller UUID list now contains
Handsfree, the controller advertises its Audio service class, its generated SDP
record matches the profile, and BlueZ's NewConnection descriptor was accepted by
Asterisk. The iPhone was freshly paired with Pairable enabled; it is bonded and
its pairing record is saved. The temporary pairing agent restores the previous
Pairable state when its five-minute window ends.

`scripts/check_phone_hfp.py` passed real descriptor transfer and acknowledgement
checks, rejected failed handoffs, and verified native Asterisk rejects missing
and non-Bluetooth descriptors. The no-call recognition/playback/interruption
check passed again (synthetic interruption 0.769 s; native playback cancellation
0.029 s).

Live demo 5 established SCO voice traffic in both directions, unlike all four
previous calls. The user then changed the receiver to 19995550123. Demo 6 to that
number also established synchronous voice transport with status Success and
thousands of SCO packets in each direction. Incoming speech reached ASR, but
the observed language-menu transcripts were incomplete; a complete marks
conversation and receiver audio quality remain unverified. Results are in
`runtime/deployment/bluetooth/demo-call-5-result.json` and
`runtime/deployment/bluetooth/demo-call-6-result.json`.

The English language-menu matcher was subsequently corrected to accept the
observed recognition "ingles", including "a prefer ingles". All 29 conversation
tests passed, including rejecting ambiguous language choices and retaining the
parent identity check. The idle caller was restarted with that source change.
Demo 7 subsequently reached the English identity prompt, but confirmation failed.

### Language-menu correction after demo 6

The receiver reported that the call connected but neither keypad choices nor
spoken language names advanced the menu. The menu previously restricted Meta
decoding to Latin letters and started collecting speech after prompt playback.
It now permits Latin, Devanagari and Kannada scripts, tries the native Hindi and
Kannada models when no explicit language choice was decoded, and keeps the same
capture running during the language prompt. An early keypad choice or speech
onset stops the prompt. Ambiguous language choices still require clarification;
language selection does not verify the parent's identity.

`app/dtmf.py` supplies a conservative in-band tone fallback on the private incoming
audio tap. Both tone frequencies must dominate for at least 80 ms; held keys are
latched, clicks/single tones/noise are rejected, and only menu keys 1/2/3 are used.
It supplements ARI keypad events; it cannot recover a digit whose event and tone
are both absent from the received cellular audio.

78 affected regression tests passed. `python -m scripts.check_phone_menu` in the
caller container passed real synthesized full-sentence English, Hindi and Kannada
choices through the deployed GPU recognizers to the identity prompt, and those speech
samples produced no false keypad detections. An isolated synthesized Hindi word
was unreliable in the earlier check; full-sentence requests provide more context.
This is a no-call model check, not verification of a human Hindi/Kannada call.

Demo 7 to +19995550123 was placed with the deployed menu fixes; its result is in
`runtime/deployment/bluetooth/demo-call-7-result.json`. Live input progression and
the menu advanced to English, but the old unconstrained English recognizer decoded
short confirmations as Thai. The confirmation did not verify identity, and the call
ended. Its observed language fallback took 5.88 seconds, with about 7.00 seconds
from speech end to playback. This was not a successful complete conversation.

### General recognition and timing changes after demo 7

English now uses the existing verified local Whisper large-v3-turbo model with
explicit English decoding and int8/float16 CUDA inference. Hindi and Kannada keep
the native IndicConformer recognizers. Both native models stay resident and warm
before readiness, avoiding model loading during language selection. All three
models fit on this RTX 4060: observed total GPU usage was 5302 MiB of 8188 MiB.
No model was trained or fine-tuned.

The prompt asks for just "English, Hindi, or Kannada" and retains keypad choices.
Menu text normalizes repeated Latin vowels, so "eenglish" and "Hiindii" work
without requiring a sentence. This normalization is restricted to language
selection; ambiguous choices and parent identity checks remain enforced.
The endpoint waits 0.45 seconds of silence for a language choice, 0.65 seconds for
identity, and the configured longer pause for ordinary questions. The menu listens
during playback and stops playback on incoming speech or an accepted digit.

79 affected software checks passed. Synthetic Whisper evaluation recognized two
ordinary marks questions correctly in about 0.26 seconds of inference, but several
isolated synthetic words and "Yes." were inaccurate. The full menu check passes
three synthesized sentence choices but still fails that short synthetic confirmation.
These are diagnostic samples, not human accent validation or a production accuracy
claim. The results are in `runtime/deployment/bluetooth/english-whisper-evaluation.json`.

Demo 8 was placed through the connected phone to +19995550123 with these changes.
Live transcripts correctly included "English.", "Yes.", "Oh, what's the total?",
"What's the percentage?", and "Thank you.". The call advanced through identity,
played replies, and ended with zero remaining channels. Some short inputs decoded
as punctuation; their intended content and receiver audio quality are unconfirmed.
English selection, confirmation, total and percentage therefore have live evidence;
Hindi/Kannada, stretched pronunciations and varied human accents still need live
validation. `runtime/deployment/bluetooth/demo-call-8-result.json` records the results.
Measured PC speech-end-to-playback estimates were 1.006 s for English selection,
1.353 s for confirmation, 1.618 s for the total question and 1.650 s for percentage.
Incoming RTP loss was zero on these turns. These estimates exclude handset/network
playout delay and are not a guarantee for every call.
The previous compose configuration is preserved as
`runtime/deployment/bluetooth/compose.before-phone-recognition.json`.

After starting BlueZ and Asterisk, keep the profile bridge running:

```powershell
wsl -d CollegePhoneGateway -- python3 /mnt/c/Users/mohda/Downloads/College-Parent-Caller-2026-10-08/Cursor-MajorProject/scripts/phone_hfp_profile.py --phone A8:AB:B5:51:24:F9
```

In another terminal, verify `mobile show devices` reports simphone connected and
Free. With Docker services running, use the authenticated demo API:

```powershell
$demoCredentials = Get-Content runtime/deployment/credentials.json -Raw | ConvertFrom-Json
$demoHeaders = @{'X-Admin-Token' = $demoCredentials.admin_token}
$demoBody = '{"student_id":"demo-phone-001"}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8113/calls/prepare -Headers $demoHeaders -ContentType application/json -Body $demoBody
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8113/calls/originate -Headers $demoHeaders -ContentType application/json -Body $demoBody
```

Run those PowerShell commands from the project folder. They call the fictional
demo record's current parent_phone, now +19995550123. Originating makes a real
cellular call through the selected phone. Press keypad 1 for English, confirm
the zulkharnain demo identity, then ask about Mohammed Ayaan's marks.

Docker also failed to start because orphaned Windows socket entries could not be
opened. Its stopped, socket-only runtime directories were preserved as
`C:\Users\mohda\AppData\Local\Docker\run.stale-20261009` and
`C:\Users\mohda\AppData\Local\docker-secrets-engine.stale-20261009`; Docker
then recreated the endpoints and all five existing demo containers started.
Containers, volumes and settings were retained.

Profile API reference: [BlueZ ProfileManager](https://bluez.readthedocs.io/en/latest/profile-api/).

### Earlier setup and failed-call history

- Official signed usbipd-win 5.3.0 installed; only adapter `1-5`, USB
  `0489:e0f2`, was shared and attached to WSL.
- MediaTek MT7922 controller `00:41:0E:E7:8A:18` initializes successfully.
- Matching Microsoft 6.6.87.2 source provides the MediaTek USB Bluetooth driver.
  The driver build independently checks the compiled `module_layout` checksum
  `0xb08e71bf`, checks dependency versions, and retains the original BTF module
  layout. An early build lacked BTF tooling and was rejected; it was corrected
  before any phone pairing or call. No kernel validation was bypassed.
- Ubuntu's signed MediaTek firmware was decompressed and copied into the shared
  VM path used by the kernel. Its path is set with the documented
  [firmware search parameter](https://www.kernel.org/doc/html/v5.0/driver-api/firmware/fw_search_path.html).
- BlueZ runs with SDP compatibility enabled. Native Asterisk reads Ubuntu's
  `chan_mobile.conf`; its adapter voice setting is `0x0060` for 8 kHz telephone
  speech. The mobile module is loaded and awaiting a configured phone.
- The Docker caller reaches authenticated ARI at
  `http://host.docker.internal:8189/ari`. Local UDP ports 20100–20103 connect the
  incoming audio tap to the caller. Both audio delivery and real playback
  cancellation passed the no-call check. Synthetic interruption took 0.300 s;
  cancellation to PlaybackFinished took 0.019 s in that run.
- 23 route/setup/ARI regression tests passed. All 73 demo audio entries are ready.

The iPhone was paired and bonded. Its discovered Handsfree Audio Gateway
RFCOMM channel is 8, and `simphone` reached Connected=Yes, State=Free. The first
authorized SIM demo call was placed on 2026-10-09 at 00:04 IST; its mobile channel
reached Up and Asterisk completed the language prompt playback. The call then
ended after incoming audio capture failed and fallback recordings were empty.
The adapter reported zero SCO voice packets after the call, so actual handset
audio delivery has not been established. Asterisk playback completion alone does
not prove the receiver heard it. Phone call control works; Bluetooth voice
routing and a complete conversation remain pending. The synthetic check does
not establish real SIM-call latency. The previously deferred Hindi live call
remains pending.

The phone's `AT+CNUM` response confirmed the expected SIM number. The user
reported that the call stayed on the phone and did not check its Audio menu.
The next live diagnostic is to inspect **Audio > College Caller** on the source
iPhone. If that option is absent, profile advertisement/compatibility needs
investigation; if present, select it and check for real SCO packets and audible
assistant speech. An idle direct SCO probe was rejected by the phone; that
result does not establish what happens after selecting audio during a call.
The final phone call-list query returned no active calls.

A second explicitly requested demo also reached an Up mobile channel, then ended
after audio capture failed and recordings were empty. The SCO counter stayed at
zero throughout that attempt. Its private result is saved in
`runtime/deployment/bluetooth/demo-call-2-result.json`. The iPhone Audio menu
observation is still required to distinguish an unselected route from a missing
hands-free audio endpoint.

## Hands-free profile correction

The user confirmed College Caller was absent from the iPhone's call Audio menu.
The packaged driver advertised an incomplete legacy Headset record.
`scripts/build_phone_mobile.py` builds the exact installed Ubuntu source against
matching headers and verifies the packaged module's build-options checksum and
runtime dependencies. The correction publishes Handsfree UUID 0x111e, profile
version 1.5, features 0x0004 (matching its caller-ID AT+BRSF capability), a correct
service ID, and an authenticated RFCOMM listener on channel 1. Only a configured
phone on the same adapter may enter the HFP monitor; unrelated devices and
duplicate connections are rejected.

The corrected module compiled and loaded, and local SDP inspection verified the
profile, service ID and features. A separate bind attempt confirmed channel 1
was occupied. The exact patch is `asterisk/chan_mobile_hfp.patch`; metadata is in
`runtime/deployment/bluetooth/mobile-build.json`. The packaged module backup is
`/opt/college-phone-mobile/chan_mobile.packaged.so` in the native gateway.

A third requested call stayed connected to Bluetooth but still carried zero SCO
packets and produced empty recordings. The user heard the source phone owner's
microphone. Voice audio has not passed live verification.

The gateway now uses `Class = 0x000408` in `/etc/bluetooth/main.conf`, and BlueZ
was restarted to apply it. Its device class remains Audio/Video, Hands-free
during discoverability changes. Merely changing the live HCI class had reverted.
After the phone reported no active calls, its old PC pairing was cleared and a
five-minute pairing agent started. The user must forget College Caller on the
iPhone and pair again to refresh its cached profiles. The user completed fresh
pairing; BlueZ reported Paired=Yes and Connected=Yes, and the mobile channel was
connected and Free. The user also confirmed the iPhone's Bluetooth Headset
routing preference. A fourth call after these changes still had no College
Caller entry in the iPhone's Audio menu and carried no Bluetooth voice packets.
The user heard the source phone owner's microphone rather than the assistant.
A no-call ASR/playback/interruption regression check
passed after the gateway restart; that does not verify Bluetooth handset audio.

### Stopped checkpoint — 2026-10-09

Testing stopped at the user's request. The gateway reported zero active channels
and zero active calls at wrap-up. Dial control works, but live Bluetooth assistant
audio is unresolved; no further call should be made without a meaningful change
to the voice connection.

The next investigation is BlueZ ProfileManager registration of the Handsfree
profile and passing its RFCOMM connection to Asterisk. The current custom driver
publishes an SDP record and uses its own RFCOMM connection; BlueZ's controller
UUID list does not include the Handsfree profile. This is a suspected integration
gap, not a verified root cause. That proposed bridge has not been implemented.
The current module, its patch, packaged rollback backup, and diagnostic results
are preserved. Fresh pairing and the iPhone routing preference alone did not
resolve the missing audio destination.

For the desired automatic routing, on the source iPhone choose **Settings >
Accessibility > Touch > Call Audio Routing > Bluetooth Headset**, as documented
in [Apple's guide](https://support.apple.com/guide/iphone/iph29145acf1/ios).
This changes its preferred call-audio destination and still requires a working,
recognized hands-free connection.

To stage the module, run `scripts/build_phone_mobile.py` with native WSL Python.
With no active calls, its `--install` option preserves the packaged module and
installs the correction; then use `asterisk -rx 'core restart gracefully'`.
The matching source and headers are in `/opt/college-phone-mobile` and the
gateway's installed development package. To roll back while idle, restore the
saved packaged module to the `module_path` in the build metadata, restore
`/opt/college-phone-mobile/main.conf.before-hfp` to `/etc/bluetooth/main.conf`,
then restart BlueZ and Asterisk. No phone number is dialed by these operations.

## Finish pairing

On the SIM phone, open Bluetooth settings, select **College Caller**, and accept
the pairing confirmation. The SIM phone has been identified as Mohammed
Ayaan's iPhone. The initial requests timed out waiting for manual confirmation
on the PC. `scripts/phone_pairing_agent.py` provides a five-minute confirmation
handler restricted to that phone's Bluetooth address. It rejects other devices
and authorizes only headset/hands-free audio services. It does not make the PC
permanently discoverable.

In the dedicated gateway, install `python3-dbus python3-gi` once, then run:

```powershell
wsl -d CollegePhoneGateway -- python3 /mnt/c/Users/mohda/Downloads/College-Parent-Caller-2026-10-08/Cursor-MajorProject/scripts/phone_pairing_agent.py --phone PHONE_MAC
```

Use the actual phone address from `bluetoothctl devices`. During those five
minutes, select College Caller on the phone and accept its prompt. If a failed
College Caller entry is retained under My Devices, forget that entry before
retrying. The agent prints the current pairing code and confirms the selected
phone immediately using the [BlueZ Agent API](https://bluez.readthedocs.io/en/latest/agent-api/).

Once paired, query its Handsfree Audio Gateway service with `sdptool browse
PHONE_MAC` in `CollegePhoneGateway`, and use the returned RFCOMM channel rather
than guessing the port. Configure it with:

```powershell
wsl -d CollegePhoneGateway -- python3 /mnt/c/Users/mohda/Downloads/College-Parent-Caller-2026-10-08/Cursor-MajorProject/scripts/configure_phone_gateway.py --adapter 00:41:0E:E7:8A:18 --phone PHONE_MAC --rfcomm-port PORT
wsl -d CollegePhoneGateway -- asterisk -rx 'core restart gracefully'
wsl -d CollegePhoneGateway -- asterisk -rx 'mobile show devices'
```

Check that `simphone` is connected and free before originating a controlled demo.
The intended demo API record is `demo-phone-001`. Installation and synthetic
checks did not dial any number; the separately authorized live SIM demo did.

## After a WSL restart

The dedicated Linux filesystem retains pairing keys and build artifacts, but
USB attachment, loaded modules and the shared firmware directory need restoring.
Before attaching USB, run the checked bootstrap:

```powershell
wsl -d CollegePhoneGateway -- sh /mnt/c/Users/mohda/Downloads/College-Parent-Caller-2026-10-08/Cursor-MajorProject/scripts/phone_bluetooth_bootstrap.sh
& 'C:\Program Files\usbipd-win\usbipd.exe' attach --wsl CollegePhoneGateway --busid 1-5
```

Start BlueZ with `bluetoothd -C -E -n`, set `hciconfig hci0 voice 0x0060`, and
start Asterisk with `asterisk -f`. These commands run in the dedicated WSL
distribution. Restart the Docker caller if its ARI listener disconnected.
If WSL's kernel changes, rebuild matching modules before attachment; do not
force-load these drivers into another kernel.

To return the adapter to Windows after stopping the controlled phone call:

```powershell
& 'C:\Program Files\usbipd-win\usbipd.exe' detach --busid 1-5
```

The previous PC demo compose file is retained as
`runtime/deployment/compose.before-bluetooth.json`. Its caller environment must
also be restored in `caller-overrides.json` if reverting the active route.

References: [Asterisk mobile prerequisites](https://docs.asterisk.org/Configuration/Channel-Drivers/Mobile-Channel/Mobile-Channel-Requirements/),
[Microsoft USB passthrough](https://learn.microsoft.com/en-us/windows/wsl/connect-usb),
[Ubuntu mobile-channel package](https://packages.ubuntu.com/noble/comm/asterisk-mobile).
