This run is a \*\*different failure mode\*\* than the earlier router `http\_get` case. The agent did not ignore PowerShell because `http\_get` was missing — it was \*\*pushed into a PCAP workflow\*\* by hard goal-matching rules before the model could plan a download.



\## What you asked for



A \*\*plan\*\* to:



1\. Download `http://192.168.1.1/` with \*\*PowerShell\*\* (`Invoke-WebRequest` / chunked reads)

2\. Split the body into manageable chunks

3\. \*\*Analyze login behavior\*\* in that HTML/JS (ZTE router UI)



That should surface tools like `http\_get`, `host\_exec`, `sequentialthinking` (one planning step), and maybe `grep\_file` on saved chunks — not packet capture.



\## What the agent did instead



`find\_file("last\_capture.pcapng")` → `analyze\_pcapng` → `read\_file` on `.pulse/pcap\_logs/verbose\_\*.txt`.



That path is the \*\*forensic PCAP pipeline\*\*, which happens to contain HTTP/login fields from \*\*old captures\*\*, not from a fresh PowerShell download of the router page.



\## Root cause (code-level, with your exact words)



\### 1. `ChatGoalRegistry` treats generic words as PCAP tasks



In `core/chat\_goals.py`, the PCAP template is registered with priority 10 and this pattern (lines 635–638):



```635:641:c:\\Users\\soyko\\Documents\\pwsh\_agent\\core\\chat\_goals.py

ChatGoalRegistry.register(

&#x20;   r"(\\.pcapng|\\.pcap\\b|...|"

&#x20;   r"\\b(?:decode|extract|parse|key\\s\*values?|look for|contents)\\b)",

&#x20;   \_build\_pcap\_goal,

&#x20;   priority=10,

)

```



Your prompt includes \*\*“download the contents”\*\* — the word \*\*`contents`\*\* alone matches that regex. So `match\_message()` selects `\_build\_pcap\_goal` even though you never mentioned pcap, tshark, or `last\_capture`.



\### 2. `\_build\_pcap\_goal` also keys off `login` + `contents`



```260:263:c:\\Users\\soyko\\Documents\\pwsh\_agent\\core\\chat\_goals.py

\_FOLLOWUP\_DECODE\_RE = re.compile(

&#x20;   r"\\b(decode|extract|parse|key\\s\*values?|look for|find|contents|login|expand|search|grep|filter)\\b",

```



\*\*“analyze … user login”\*\* hits `login`; \*\*“contents”\*\* hits again. That sets `is\_followup = True`, and the direct path still builds a PCAP goal (lines 350–377) with:



\- `required\_tools=\["analyze\_pcapng"]`

\- default `path\_hint = "last\_capture.pcapng"`



So `ChatGoalGuard` \*\*requires\*\* PCAP tools and nudges the model away from `host\_exec` / a download plan.



\### 3. `detect\_mission\_kind` is less guilty here, but `intent\_spec` may say `web\_auth`



`detect\_mission\_kind` in `core/task\_intent.py` only classifies as `pcap` when the text matches things like `analyze.\*packet` — your message does \*\*not\*\* contain “packet”, so mission kind is likely `dev` or `general`.



But `build\_fallback\_spec` can still pick \*\*`web\_auth`\*\* because of \*\*“login”\*\* + URL/IP (`\_AUTH\_RE`), which is closer than PCAP for intent — yet \*\*ChatGoals win at execution time\*\* because they hard-require `analyze\_pcapng`.



\### 4. Phase / salvage layers reinforce PCAP when “login” appears



In `core/llm\_utils.py`, parser recovery still biases toward `.pulse/pcap\_logs` when mission kind is `pcap` or search-intent regex matches (which includes `login`). Even with some fixes elsewhere, \*\*once the PCAP ChatGoal is active\*\*, the loop is locked in.



\### 5. Why it found login data anyway



The verbose log line you saw (`GET`, `SID=...`, `menuData`, `wlan\_homepage\_lua.lua`) is \*\*real traffic from a prior capture\*\* on `192.168.1.1`, not output of a new PowerShell download. The agent is \*\*reusing stale forensic artifacts\*\*, which can look “smart” but is not what you requested.



\## What was already fixed vs. what this run exposes



| Issue | Status |

|--------|--------|

| Missing `http\_get` / empty tool routing for “fetch URL” | Fixed (you saw good behavior on “fetch http://192.168.1.1/” earlier) |

| `host\_exec` Unicode crash on large HTML | Fixed (`utf-8` + `errors="replace"`) |

| Generic `report\_generate` dumping old findings | Fixed (session-scoped `report\_generate`) |

| End-of-turn stall (`append\_note` loops) | Partially fixed (`http\_get` early break) |

| \*\*“contents” / “login” → forced PCAP goal\*\* | \*\*Still open\*\* — this transcript |



\## What the agent \*should\* have done (conceptually)



A plan aligned with your prompt would look roughly like:



1\. \*\*Plan\*\* (one `sequentialthinking` or prose): steps only, no PCAP.

2\. \*\*`host\_exec` or `http\_get`\*\*:  

&#x20;  `Invoke-WebRequest -Uri 'http://192.168.1.1/' -UseBasicParsing -OutFile workspace/sessions/.../router\_root.html`  

&#x20;  (or `http\_get` + spill to artifact).

3\. \*\*Chunk\*\*: split by lines/size (e.g. 8–16 KB files or `read\_file` with `line\_start` / `line\_count`).

4\. \*\*Analyze login\*\*: grep for `login`, `password`, `SID`, `Set-Cookie`, `lua`, form fields, JS redirects — on the \*\*downloaded HTML\*\*, not `.pulse` logs.

5\. Optional: `try\_http\_login` only if you asked to \*\*test\*\* credentials, not for “how does login work” in the page source.



\## Targeted fixes (guidance only — Ask mode)



If you switch back to Agent mode, the high-leverage changes would be:



1\. \*\*Tighten PCAP registry regex\*\* — remove bare `contents`, `login`, `find` from the PCAP template; require explicit `pcap` / `packet` / `last\_capture` / `tshark`.

2\. \*\*Gate `\_FOLLOWUP\_DECODE\_RE`\*\* — only when `had\_pcap` in the \*\*current\*\* session or user mentions packets.

3\. \*\*Add a “fetch + analyze page” ChatGoal\*\* (priority \&lt; 10): requires `http\_get` or `host\_exec` with PowerShell, blocks `analyze\_pcapng` unless user says capture/sniff.

4\. \*\*Negative guard in `ChatGoalGuard`\*\*: if message contains `powershell` + `download`/`http`, block `analyze\_pcapng` and `find\_file('last\_capture.pcapng')`.



I can implement those in Agent mode if you want; in Ask mode I can only explain and point at the code paths above.



\## Quick check on your session



Session `20260602\_225632` is new, but \*\*`last\_capture.pcapng` and `.pulse/pcap\_logs/` persist globally\*\* in the repo — so the agent always has tempting forensic shortcuts even on a “fresh” session. That is by design for hash/PCAP missions, but it hurts generic “download and analyze” tasks until the goal regex is narrowed.

