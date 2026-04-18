import hashlib
import os
import logging
import signal
import threading

from common import middleware, message_protocol, fruit_item

ID = int(os.environ["ID"])
MOM_HOST = os.environ["MOM_HOST"]
INPUT_QUEUE = os.environ["INPUT_QUEUE"]
SUM_AMOUNT = int(os.environ["SUM_AMOUNT"])
SUM_PREFIX = os.environ["SUM_PREFIX"]
SUM_CONTROL_EXCHANGE = "SUM_CONTROL_EXCHANGE"
AGGREGATION_AMOUNT = int(os.environ["AGGREGATION_AMOUNT"])
AGGREGATION_PREFIX = os.environ["AGGREGATION_PREFIX"]

SUM_EOF_EXCHANGE = f"{SUM_PREFIX}_eof_exchange"

class SumFilter:
    def __init__(self):
        self.input_queue = middleware.MessageMiddlewareQueueRabbitMQ(
            MOM_HOST, INPUT_QUEUE
        )

        self.eof_input_exchange = middleware.MessageMiddlewareExchangeRabbitMQ(
            MOM_HOST, SUM_EOF_EXCHANGE, [f"{SUM_EOF_EXCHANGE}_{ID}"]
        )

        all_eof_routing_keys = [f"{SUM_EOF_EXCHANGE}_{i}" for i in range(SUM_AMOUNT)]
        self.eof_output_exchange = middleware.MessageMiddlewareExchangeRabbitMQ(
            MOM_HOST, SUM_EOF_EXCHANGE, all_eof_routing_keys
        )

        self.data_output_exchanges = []
        for i in range(AGGREGATION_AMOUNT):
            self.data_output_exchanges.append(
                middleware.MessageMiddlewareExchangeRabbitMQ(
                    MOM_HOST, AGGREGATION_PREFIX, [f"{AGGREGATION_PREFIX}_{i}"]
                )
            )

        self.amount_by_fruit = {}

        self.locks = {} 
        self.locks_lock = threading.Lock()
        self._stop_event = threading.Event()

        signal.signal(signal.SIGTERM, self._handle_sigterm)

    def _handle_sigterm(self, signum, frame):
        logging.info("Received SIGTERM")
        self.input_queue.stop_consuming()
        self.eof_input_exchange.stop_consuming()
        self._stop_event.set()

    def _get_lock(self, client_id):
        with self.locks_lock:
            if client_id not in self.locks:
                self.locks[client_id] = threading.Lock()
            return self.locks[client_id]

    def _get_aggregator_id(self, fruit):
        return int(hashlib.md5(fruit.encode()).hexdigest(), 16) % AGGREGATION_AMOUNT

    def _process_data(self, client_id, fruit, amount):
        with self._get_lock(client_id):
            logging.info(f"Process data")
            if client_id not in self.amount_by_fruit:
                self.amount_by_fruit[client_id] = {}
            client_state = self.amount_by_fruit[client_id]
            client_state[fruit] = client_state.get(
                fruit, fruit_item.FruitItem(fruit, 0)
            ) + fruit_item.FruitItem(fruit, int(amount))

    def _send_results_to_aggregator(self, client_id):
        logging.info(f"Sending results for client {client_id} to aggregator")
        with self._get_lock(client_id):
            client_state = self.amount_by_fruit.pop(client_id, {})

        for final_fruit_item in client_state.values():
            aggregator_id = self._get_aggregator_id(final_fruit_item.fruit)
            self.data_output_exchanges[aggregator_id].send(
                message_protocol.internal.serialize(
                    client_id, [final_fruit_item.fruit, final_fruit_item.amount]
                )
            )

        logging.info(f"Broadcasting EOF message")
        for exchange in self.data_output_exchanges:
            exchange.send(message_protocol.internal.serialize(client_id, []))

    def _process_eof_message(self, message, ack, nack):
        client_id, _ = message_protocol.internal.deserialize(message)
        logging.info(f"Got EOF from exchange for client {client_id}, sending results")
        self._send_results_to_aggregator(client_id)
        ack()

    def _process_data_message(self, message, ack, nack):
        client_id, payload = message_protocol.internal.deserialize(message)
        if len(payload) == 2:
            self._process_data(client_id, *payload)
        else:
            logging.info(f"Got EOF from data queue for client {client_id}, broadcasting")
            self.eof_output_exchange.send(
                message_protocol.internal.serialize(client_id, [])
            )
        ack()

    def start(self):
        eof_thread = threading.Thread(
            target=self.eof_input_exchange.start_consuming,
            args=(self._process_eof_message,),
            daemon=True
        )
        eof_thread.start()
        self.input_queue.start_consuming(self._process_data_message)

        self._stop_event.set()
        eof_thread.join(timeout=5)

def main():
    logging.basicConfig(level=logging.INFO)
    sum_filter = SumFilter()
    sum_filter.start()
    return 0


if __name__ == "__main__":
    main()
