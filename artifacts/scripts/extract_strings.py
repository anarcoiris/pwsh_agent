import re

with open('last_capture.pcapng', 'rb') as f:
    data = f.read()

strings = re.findall(b'[ -~]{4,}', data)
lines = [s.decode() for s in strings]

for line in lines:
    if 'password' in line.lower() or 'login' in line.lower() or '55077791' in line:
        print(line)
