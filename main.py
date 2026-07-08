import socket
import threading
import sys

def receive_message(conn):
    """
    Runs upon seperate threads. Continiously listens 
    for the incoming data.
    """

    while True:
        try:
            data = conn.recv(1024)

            if not data:
                print("\n[Connection closed by peer]")
                break

            # decoding the bytes back to string
            message = data.decode('utf-8')

            # Printing the message and the rest of prompt on next line
            sys.stdout.write(f"\n[Peer]: {message}\n")
            sys.stdout.flush()

        except Exception as e:
            print(f"\n[Error receiving data]: {e}")
            break 

def start_server(port):
    """Binds to a port and waits for the peer to connect"""

    host = '0.0.0.0' # Listening to all avaliable interfaces
    server_socket = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM
    )

    # Allowing the ports to be reused immediately after reset.
    server_socket.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_REUSEADDR,
        1
    )

    server_socket.bind((host, port))
    server_socket.listen(1)

    print(f"Listening for connections on port {port}")
    conn, addr = server_socket.accept()
    print(f"Connected to {addr[0]} : {addr[1]}")


    return conn

def connect_to_peer(ip, port):
    """Actively connects to a listning port"""

    client_socket = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM
    )

    print(f"Connecting to {ip}:{port}")
    client_socket.connect((ip,port))    
    print("Connected...!")

    return client_socket

def main():
    print("--- Minimal P2P Chat ---")
    choice = input("Do you want to (1) Host or (2) Connect?")

    if choice == '1':
        port = int(input("Enter the port you want to listen on eg: 5000:- "))
        conn = start_server(port)

    elif choice == '2':
        pass



    

