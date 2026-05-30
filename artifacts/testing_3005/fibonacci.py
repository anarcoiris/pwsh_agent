# Fibonacci sequence generator
n = 100
def fibonacci(n):
    fib_sequence = [1, 1]
    for i in range(2, n):
        fib_sequence.append(fib_sequence[-1] + fib_sequence[-2])
    return fib_sequence

# Write the first 100 Fibonacci numbers to a file
with open('fibonacci.txt', 'w') as file:
    for num in fibonacci(n):
        file.write(str(num) + '\n')