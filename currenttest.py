import board, busio, time

# S2 Mini side — receives commands from Qualia on GPIO 16
uart = busio.UART(board.IO17, board.IO16, baudrate=9600, timeout=0)

print("UART receiver ready, waiting for commands...")

buf = b""
while True:
    data = uart.read(64)
    if data:
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            print("Received:", line.decode().strip())
    time.sleep(0.05)
