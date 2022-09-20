from websocket_server import WebsocketServer
import time
import threading
import nxbt
import json

nx = nxbt.Nxbt()
reconnect_address=nx.get_switch_addresses()
if len(reconnect_address) > 0:
    controller_idx = nx.create_controller(
    nxbt.PRO_CONTROLLER,
    reconnect_address=nx.get_switch_addresses())https://github.com/tedShadow/nxbt
else:
    controller_idx = nx.create_controller(nxbt.PRO_CONTROLLER)

# Called for every client connecting (after handshake)
def new_client(client, server):
    nx.wait_for_connection(controller_idx)
    print("New client connected and was given id %d" % client['id'])
	#server.send_message_to_all("Initialize controller complete")


# Called for every client disconnecting
def client_left(client, server):
	print("Client(%d) disconnected" % client['id'])


# Called when a client sends a message
def message_received(client, server, message):
    # handle incoming controller command
    #print("received message " + str(message))
    try:
        decodedJson = json.loads(message)
        inputTarget = decodedJson
        #print(inputTarget)
        nx.set_controller_input(controller_idx, inputTarget)
        # getting near


    except:
        print("fail to load json message")
        print(message)
    """
    message = json.loads(message)
    print(message)
    index = message[0]
    input_packet = message[1]
    nxbt.set_controller_input(index, input_packet)
    """


if __name__ == '__main__':
    reconnect_address=nx.get_switch_addresses()
    print(reconnect_address)
    PORT=9002
    server = WebsocketServer(host="0.0.0.0",port = PORT)
    server.set_fn_new_client(new_client)
    server.set_fn_client_left(client_left)
    server.set_fn_message_received(message_received)
    server.run_forever()
