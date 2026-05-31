from repo_bootstrap import bootstrap

bootstrap()

import json
import tools_legacy

res = tools_legacy.analyze_pcapng('last_capture.pcapng', filter_expression='http contains "login" or http contains "password" or http contains "xmlObj"')
print(json.dumps(res, indent=2))
