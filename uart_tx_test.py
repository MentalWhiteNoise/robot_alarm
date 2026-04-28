import board, busio, time

# Qualia side — transmits commands to S2 Mini
uart = busio.UART(board.TX, board.RX, baudrate=9600)

commands = [b"PLAY ALARM\n", b"PLAY WAKE\n", b"STOP\n", b"VOL 0.75\n"]
i = 0

while True:
    cmd = commands[i % len(commands)]
    uart.write(cmd)
    print("Sent:", cmd)
    i += 1
    time.sleep(2)
