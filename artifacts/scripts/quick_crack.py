import hashlib

target_hash = '18846efb090813788c3246ce05884e7155eee92186ec23569abc0c39b44b7032'
salt = '55077791'

words = [
    'password', 'user', 'admin', '1234', '12345', '123456', '12345678', 'admin123',
    'root', 'toor', 'guest', 'test', 'qwerty', '1111', '0000', 'Password', 'Admin'
]

for w in words:
    h = hashlib.sha256((w + salt).encode()).hexdigest()
    if h == target_hash:
        print(f"FOUND: {w}")
        exit(0)

print("Not found in small wordlist")
