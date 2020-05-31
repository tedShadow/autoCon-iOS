import socket
import fcntl
import os
import time
import traceback
import queue

from .controller import Controller, ControllerTypes
from ..bluez import BlueZ
from .protocol import ControllerProtocol
from .input import InputParser
from .utils import format_msg_controller, format_msg_switch


class ControllerServer():

    def __init__(self, controller_type, adapter_path="/org/bluez/hci0",
                 lock=None, colour_body=None, colour_buttons=None):

        self.controller_type = controller_type
        self.colour_body = colour_body
        self.colour_buttons = colour_buttons

        if lock:
            self.lock = lock

        self.reconnect_counter = 0

        # Intializing Bluetooth
        self.bt = BlueZ(adapter_path=adapter_path)

        self.controller = Controller(self.bt, self.controller_type)
        self.protocol = ControllerProtocol(
            self.controller_type,
            self.bt.address,
            colour_body=self.colour_body,
            colour_buttons=self.colour_buttons)

        self.input = InputParser(self.protocol)

    def run(self, reconnect_address=None, state=None, task_queue=None):
        """Runs the mainloop of the controller server.

        :param reconnect_address: The Bluetooth MAC address of a
        previously connected to Nintendo Switch, defaults to None
        :type reconnect_address: string, optional
        """

        if state:
            state["state"] = "initializing"

        try:
            # If we have a lock, prevent other controllers
            # from initializing at the same time and saturating
            # the DBus
            if self.lock:
                self.lock.acquire()
            try:
                self.controller.setup()

                if reconnect_address:
                    itr, ctrl = self.reconnect(reconnect_address, state=state)
                else:
                    itr, ctrl = self.connect(state=state)
            except Exception:
                if self.lock:
                    self.lock.release()

            self.switch_address = itr.getsockname()[0]

            if state:
                state["state"] = "connected"

            # Mainloop
            while True:
                # Attempt to get output from Switch
                try:
                    reply = itr.recv(50)
                    if len(reply) > 40:
                        print(format_msg_switch(reply))
                except BlockingIOError:
                    reply = None

                # Getting any inputs from the task queue
                if task_queue:
                    try:
                        msg = task_queue.get_nowait()
                        print(msg)
                        if msg:
                            self.input.buffer_macro(
                                msg["macro"], msg["macro_id"])
                    except queue.Empty:
                        pass

                self.protocol.process_commands(reply)
                self.input.set_protocol_input(state=state)
                msg = self.protocol.get_report()

                if reply:
                    print(format_msg_controller(msg))

                try:
                    itr.sendall(msg)
                except BlockingIOError:
                    continue
                except OSError as e:
                    # Attempt to reconnect to the Switch
                    if self.reconnect_counter < 2:
                        try:
                            print("Attempting to reconnect")
                            # Reinitialize the protocol
                            self.protocol = ControllerProtocol(
                                self.controller_type,
                                self.bt.address,
                                colour_body=self.colour_body,
                                colour_buttons=self.colour_buttons)
                            itr, ctrl = self.reconnect(self.switch_address,
                                                       state=state)
                        except OSError:
                            self.reconnect_counter += 1
                            print(e)
                            time.sleep(0.5)
                            continue
                    # If we can't reconnect, transition to attempting
                    # to connect to any Switch.
                    else:
                        print("Connecting")
                        # Reinitialize the protocol
                        self.protocol = ControllerProtocol(
                            self.controller_type,
                            self.bt.address,
                            colour_body=self.colour_body,
                            colour_buttons=self.colour_buttons)
                        itr, ctrl = self.connect(state=state)
                        self.switch_address = itr.getsockname()[0]

                # Respond at 120Hz for Pro Controller
                # or 60Hz for Joy-Cons
                if self.controller_type == ControllerTypes.PRO_CONTROLLER:
                    time.sleep(1/120)
                else:
                    time.sleep(1/60)

        except Exception as e:
            if state:
                state["state"] = "crashed"
                state["errors"] = traceback.format_exc()
            else:
                raise e

    def connect(self, state=None):
        """Configures as a specified controller, pairs with a Nintendo Switch,
        and creates/accepts sockets for communication with the Switch.
        """

        if state:
            state["state"] = "connecting"

        # Creating control and interrupt sockets
        s_ctrl = socket.socket(
            family=socket.AF_BLUETOOTH,
            type=socket.SOCK_SEQPACKET,
            proto=socket.BTPROTO_L2CAP)
        s_itr = socket.socket(
            family=socket.AF_BLUETOOTH,
            type=socket.SOCK_SEQPACKET,
            proto=socket.BTPROTO_L2CAP)

        # Setting up HID interrupt/control sockets
        try:
            s_ctrl.bind((self.bt.address, 17))
            s_itr.bind((self.bt.address, 19))
        except OSError:
            s_ctrl.bind((socket.BDADDR_ANY, 17))
            s_itr.bind((socket.BDADDR_ANY, 19))

        s_itr.listen(1)
        s_ctrl.listen(1)

        self.bt.set_discoverable(True)

        ctrl, ctrl_address = s_ctrl.accept()
        itr, itr_address = s_itr.accept()

        # Send an empty input report to the Switch to prompt a reply
        self.protocol.process_commands(None)
        msg = self.protocol.get_report()
        itr.sendall(msg)

        # Setting interrupt connection as non-blocking
        # In this case, non-blocking means it throws a "BlockingIOError"
        # for sending and receiving, instead of blocking
        fcntl.fcntl(itr, fcntl.F_SETFL, os.O_NONBLOCK)

        # Mainloop
        while True:
            # Attempt to get output from Switch
            try:
                reply = itr.recv(50)
                if len(reply) > 40:
                    print(format_msg_switch(reply))
            except BlockingIOError:
                reply = None

            self.protocol.process_commands(reply)
            msg = self.protocol.get_report()

            if reply:
                print(format_msg_controller(msg))

            try:
                itr.sendall(msg)
            except BlockingIOError:
                continue

            # Exit pairing loop on set player lights
            if reply and len(reply) > 45 and reply[11] == 0x30:
                break

            # Switch responds to packets slower during pairing
            # Pairing cycle responds optimally on a 15Hz loop
            time.sleep(1/15)

        return itr, ctrl

    def reconnect(self, reconnect_address, state=None):
        """Attempts to reconnect with a Switch at the given address.

        :param reconnect_address: The Bluetooth MAC address of the Switch
        :type reconnect_address: string
        """

        if state:
            state["state"] = "reconnecting"

        # Creating control and interrupt sockets
        ctrl = socket.socket(
            family=socket.AF_BLUETOOTH,
            type=socket.SOCK_SEQPACKET,
            proto=socket.BTPROTO_L2CAP)
        itr = socket.socket(
            family=socket.AF_BLUETOOTH,
            type=socket.SOCK_SEQPACKET,
            proto=socket.BTPROTO_L2CAP)

        # Setting up HID interrupt/control sockets
        ctrl.connect((reconnect_address, 17))
        itr.connect((reconnect_address, 19))

        fcntl.fcntl(itr, fcntl.F_SETFL, os.O_NONBLOCK)

        # Send an empty input report to the Switch to prompt a reply
        self.protocol.process_commands(None)
        msg = self.protocol.get_report()
        itr.sendall(msg)

        # Setting interrupt connection as non-blocking
        # In this case, non-blocking means it throws a "BlockingIOError"
        # for sending and receiving, instead of blocking
        fcntl.fcntl(itr, fcntl.F_SETFL, os.O_NONBLOCK)

        return itr, ctrl