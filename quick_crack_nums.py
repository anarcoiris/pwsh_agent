import hashlib

target_hash = '18846efb090813788c3246ce05884e7155eee92186ec23569abc0c39b44b7032'
salt = '55077791'

for i in range(1000000):
    w = str(i).zfill(6)
    h = hashlib.sha256((w + salt).encode()).hexdigest()
    if h == target_hash:
        print(f"FOUND 6-digit: {w}")
        exit(0)

for i in range(100000):
    w = str(i).zfill(5)
    h = hashlib.sha256((w + salt).encode()).hexdigest()
    if h == target_hash:
        print(f"FOUND 5-digit: {w}")
        exit(0)
        
print("Not a 5 or 6 digit number")
