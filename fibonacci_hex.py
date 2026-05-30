# Convert Fibonacci numbers from decimal to hexadecimal
with open('fibonacci.txt', 'r') as file:
    fibonacci_numbers = [int(line.strip()) for line in file]

hex_fibonacci_numbers = [format(num, 'x') for num in fibonacci_numbers]

# Write the hexadecimal Fibonacci numbers to a file
decoding_table = str.maketrans('0123456789abcdef', '0123456789ABCDEF')
hex_fibonacci_hex = [num.translate(decoding_table) for num in hex_fibonacci_numbers]

with open('fibonacci_hex.txt', 'w') as file:
    for num in hex_fibonacci_hex:
        file.write(num + '\n')